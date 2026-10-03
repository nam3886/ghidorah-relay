"""Result application (contracts 7.1, 5.3; invariants I1, I2, I7, I9, I23, I27).

Each function is one transaction on a fresh pooled connection. Submit results are fenced by the job lease ID with
`clock_timestamp()` under `FOR SHARE` (`leases.fence`), so an expired holder cannot write; a failed fence raises
`LeaseLost` and the caller drops the result (the reaper takes over). The caller runs these OUTSIDE the submit
timeout and shields them from cancellation: a result that was received is applied or lost to the reaper, never
half-applied.

The account is charged only when the order is proven taken, and a `Balance(reported)` effect of the same result is
applied after that charge so it wins. Facts are stored only when taken. A job that leaves `submitting` releases its
lease in the same transaction.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from relay_core import failpoints, leases, pool, storage
from relay_core.db import lock_account, tx
from relay_core.errors import rule
from relay_core.jobs.events import add_event
from relay_core.jobs.fail import fail_in_tx
from relay_core.jobs.recheck import StoredOutput
from relay_core.leases import LeaseLost
from relay_core.providers import (
    Accepted,
    Ambiguous,
    Caps,
    Completed,
    Done,
    Fail,
    Failed,
    Found,
    NotSent,
    NotTaken,
    Output,
    Pending,
    PollResult,
    Reconciled,
    Retry,
    StillUnknown,
    SubmitOutcome,
    SubmitResult,
)
from relay_core.redact import redact, redact_log

log = logging.getLogger(__name__)

FIRST_POLL_S = 30            # a job that was accepted is first polled this long after
UNKNOWN_FIRST_S = 60         # the first reconcile of an unknown job; doubles up to UNKNOWN_MAX_S
UNKNOWN_MAX_S = 1800
NOT_SENT_MARGIN_S = 60       # I27: the proving read starts later than sent_at + submit_timeout_s + this
REQUEUE_PAUSE_S = 30         # a job requeued after Retry or NotTaken is not claimed again for this long
NOTE_LIMIT = 200


@dataclass(frozen=True)
class _Raw:
    """A SQL expression (with its parameters) for one column of an UPDATE."""
    sql: str
    params: tuple = ()


_NOW = _Raw("clock_timestamp()")
_SENT_OR_NOW = _Raw("COALESCE(sent_at, clock_timestamp())")


def _in(seconds: float) -> _Raw:
    return _Raw("clock_timestamp() + make_interval(secs => %s)", (seconds,))


def _state(patch: Mapping[str, Any]) -> Jsonb:
    """The one way a job's `provider_state` patch reaches the database: through the redactor (I24)."""
    return Jsonb(redact(dict(patch)))


def _merge(patch: Mapping[str, Any]) -> _Raw:
    return _Raw("provider_state || %s::jsonb", (_state(patch),))


def _merge_outputs(outputs: tuple[Output, ...]) -> _Raw:
    """Outputs keep their refs as given (a ref may be the signed URL the fetcher needs); their `meta` is redacted."""
    return _Raw("provider_state || %s::jsonb", (Jsonb(_outputs(outputs)),))


def _reset(patch: Mapping[str, Any]) -> dict:
    """What a requeue writes: the previous attempt's reference and state are dropped (they belong to the account
    that attempt ran on) and only the requeue's own patch survives. The core keeps no key of its own on a job that
    has not been taken, so nothing else is kept."""
    return dict(external_ref=None, provider_state=_Raw("%s::jsonb", (_state(patch),)))


def _dropped(job: "_Job") -> str:
    return " (dropped reference %s)" % job.external_ref if job.external_ref else ""


async def _set(conn: AsyncConnection, job_id, expect: str, **cols: Any) -> None:
    """UPDATE jobs SET <cols> WHERE id AND status = expect. Column names come from code, values are parameters."""
    parts, params = ["updated_at = clock_timestamp()"], []
    for col, val in cols.items():
        if isinstance(val, _Raw):
            parts.append("%s = %s" % (col, val.sql))
            params.extend(val.params)
        else:
            parts.append("%s = %%s" % col)
            params.append(val)
    cur = await conn.execute("UPDATE jobs SET %s WHERE id = %%s AND status = %%s" % ", ".join(parts),
                             (*params, job_id, expect))
    if cur.rowcount != 1:
        raise RuntimeError("job %s is not %s" % (job_id, expect))


@dataclass(frozen=True)
class _Job:
    id: Any
    status: str
    sent_at: datetime | None
    attempts: int
    account_id: Any
    pool: str | None
    account_cost: int
    submit_timeout_s: int
    poll_token: Any
    taken_at: datetime | None
    external_ref: str | None
    provider_state: Mapping[str, Any]


_COLS = ("id, status, sent_at, attempts, account_id, pool, account_cost, submit_timeout_s, poll_token, taken_at, "
         "external_ref, provider_state")


async def _lock_job(conn: AsyncConnection, job_id) -> _Job:
    row = await (await conn.execute("SELECT %s FROM jobs WHERE id = %%s FOR UPDATE" % _COLS, (job_id,))).fetchone()
    if row is None:
        raise LookupError("no job %s" % job_id)
    return _Job(*row)


async def _fenced_job(conn: AsyncConnection, claim, *, expired_ok: bool = False) -> _Job:
    """The claimed job, locked, once the lease fence passes; LeaseLost (nothing written) otherwise.
    `expired_ok` (interruptions only) accepts a lease that has expired but is still there: nobody else can have
    touched the job, and the reaper that would take it makes the same transition. A deleted lease is LeaseLost."""
    if expired_ok:
        held = await conn.execute("SELECT 1 FROM leases WHERE id = %s FOR UPDATE", (claim.lease_id,))
        if await held.fetchone() is None:
            raise LeaseLost(claim.lease_id)
    else:
        await leases.fence(conn, claim.lease_id)
    job = await _lock_job(conn, claim.job_id)
    if job.status != "submitting" or job.account_id != claim.account_id:
        raise LeaseLost(claim.lease_id)
    return job


def _outputs(outputs: tuple[Output, ...]) -> dict:
    """What a job keeps of its outputs until the fetcher stores them: refs and meta in `provider_state.outputs`."""
    return {"outputs": [{"ref": o.ref, "mime": o.mime, "idx": o.idx, "upstream_id": o.upstream_id,
                         "meta": redact(dict(o.meta))} for o in outputs]}


async def _charge_taken(conn: AsyncConnection, job: _Job, pools: Mapping[str, pool.PoolConfig]) -> None:
    """The order is proven taken: the account's pool is charged `account_cost` (once per job and pool, I7)."""
    if job.pool is not None:
        await pool.charge_account(conn, job.account_id, job.id, pools[job.pool], job.account_cost)


async def _effects(conn: AsyncConnection, job: _Job, effects, caps: Caps, pools) -> None:
    if effects and job.account_id is not None:
        await pool.apply_effects(conn, job.account_id, effects, caps, pools)


async def _lock_effect_account(conn: AsyncConnection, job: _Job, effects) -> None:
    """A result with account effects locks the job's account right after the job: a failure in the same transaction
    refunds the key wallet, and the wallet is the last row of the lock order, so the effects' account lock cannot
    come after it."""
    if effects and job.account_id is not None:
        await lock_account(conn, job.account_id)


async def _requeue(conn: AsyncConnection, job: _Job, code: str, note: str, caps: Caps, pools, *,
                   expect: str = "submitting", patch: Mapping[str, Any] | None = None) -> str:
    """Back to `queued` with `sent_at` cleared (I9: only NotTaken, Retry before the send and NotSent get here), paused
    for REQUEUE_PAUSE_S so it does not hit a failing upstream on the next tick, with the previous attempt's reference
    dropped (and named in the event). The attempt counts when the code's rule says so; at `caps.max_attempts` the
    job fails with that code."""
    counts = rule(code).counts_attempt
    attempts = job.attempts + (1 if counts else 0)
    if counts and attempts >= caps.max_attempts:
        await _set(conn, job.id, expect, attempts=attempts)
        await fail_in_tx(conn, job.id, (expect,), code, note, pools)
        return "failed"
    await _set(conn, job.id, expect, status="queued", sent_at=None, attempts=attempts, account_id=None,
               next_poll_at=_in(REQUEUE_PAUSE_S), **_reset(patch or {}))
    await add_event(conn, job.id, "system", "requeued", "requeued: %s%s%s" % (
        code, " (%s)" % note if note else "", _dropped(job)))
    return "queued"


async def _unknown(conn: AsyncConnection, job: _Job, note: str) -> str:
    await _set(conn, job.id, "submitting", status="unknown", sent_at=_SENT_OR_NOW, next_poll_at=_in(UNKNOWN_FIRST_S))
    await add_event(conn, job.id, "system", "unknown", "outcome unknown: %s" % (note or "no answer"))
    return "unknown"


# ---- before and during the send --------------------------------------------------------------------------------
async def mark_sent(db: AsyncConnectionPool, claim) -> None:
    """RunCtx.mark_sent: fence, then `sent_at = now` in the same transaction, immediately before the first
    irreversible action. Raises LeaseLost, also when the job is no longer `submitting` on this claim's account. A job
    that was already marked keeps its first `sent_at`."""
    async with tx(db) as conn:
        await leases.fence(conn, claim.lease_id)
        cur = await conn.execute(
            "UPDATE jobs SET sent_at = COALESCE(sent_at, clock_timestamp()), updated_at = clock_timestamp() "
            "WHERE id = %s AND status = 'submitting' AND account_id = %s", (claim.job_id, claim.account_id))
        if cur.rowcount != 1:       # the job left this claim (failed, moved) while its lease row still exists
            raise LeaseLost(claim.lease_id)


async def report_ref(db: AsyncConnectionPool, claim, ref: str, state: Mapping[str, Any]) -> None:
    """RunCtx.report_ref: fenced; keeps the upstream reference and state as soon as they are known."""
    async with tx(db) as conn:
        await leases.fence(conn, claim.lease_id)
        await _set(conn, claim.job_id, "submitting", external_ref=ref, provider_state=_merge(state))


# ---- the submit result -----------------------------------------------------------------------------------------
async def _submit_outcome(conn: AsyncConnection, job: _Job, outcome: SubmitOutcome, caps: Caps, pools) -> str:
    match outcome:
        case Accepted():
            cols = dict(status="waiting", external_ref=outcome.ref, sent_at=_SENT_OR_NOW, accepted_at=_NOW,
                        render_since=_SENT_OR_NOW, next_poll_at=_in(FIRST_POLL_S),
                        provider_state=_merge(outcome.provider_state))
            if outcome.taken:
                await _charge_taken(conn, job, pools)
                cols.update(taken_at=_NOW, upstream_price=outcome.facts.upstream_price,
                            upstream_model=redact_log(outcome.facts.upstream_model))
            await _set(conn, job.id, "submitting", **cols)
            await add_event(conn, job.id, "system", "accepted", "accepted by upstream")
            return "waiting"
        case Completed():
            await _charge_taken(conn, job, pools)
            await _set(conn, job.id, "submitting", status="fetching", sent_at=_SENT_OR_NOW, accepted_at=_NOW,
                       taken_at=_NOW, render_since=_SENT_OR_NOW, next_poll_at=_NOW,
                       upstream_price=outcome.facts.upstream_price,
                       upstream_model=redact_log(outcome.facts.upstream_model),
                       provider_state=_merge_outputs(outcome.outputs))
            await add_event(conn, job.id, "system", "completed", "%d output(s) ready to fetch" % len(outcome.outputs))
            return "fetching"
        case NotTaken():
            return await _requeue(conn, job, outcome.code, "upstream did not take the order", caps, pools,
                                  patch=outcome.state_patch)
        case Retry() if job.sent_at is None:
            return await _requeue(conn, job, outcome.code, "retry before send", caps, pools)
        case Retry():       # after mark_sent a retry could be a second order: it is ambiguous
            return await _unknown(conn, job, "retry after send: %s" % outcome.code)
        case Fail():
            await _set(conn, job.id, "submitting", provider_state=_merge(outcome.state_patch))
            await fail_in_tx(conn, job.id, ("submitting",), outcome.code, outcome.detail, pools)
            return "failed"
        case Ambiguous():
            return await _unknown(conn, job, outcome.note)
    raise TypeError("unknown submit outcome %r" % (outcome,))


async def _decorate(conn: AsyncConnection, job_id, result) -> None:
    """Warnings and the upstream title ride along with whatever transition happened."""
    if result.warnings:
        await conn.execute("UPDATE jobs SET warnings = warnings || %s::jsonb WHERE id = %s",
                           (Jsonb([{"code": w.code, "text": redact_log(w.text)} for w in result.warnings]), job_id))
    # imported here: at module level it is a cycle (titles -> checks -> runctx -> store -> apply)
    from relay_core.jobs.titles import clean_title
    title = clean_title(result.title)
    if title is not None:
        await conn.execute("UPDATE jobs SET title = COALESCE(NULLIF(title, ''), %s) WHERE id = %s", (title, job_id))


async def apply_submit(db: AsyncConnectionPool, claim, result: SubmitResult, *, caps: Caps,
                       pools: Mapping[str, pool.PoolConfig]) -> str:
    """Apply what a submit returned, in one transaction fenced by the lease id. Returns the job's new status.
    Raises LeaseLost (nothing written) when the lease is no longer live."""
    async with tx(db) as conn:
        job = await _fenced_job(conn, claim)
        await _lock_effect_account(conn, job, result.account)
        status = await _submit_outcome(conn, job, result.job, caps, pools)
        await _effects(conn, job, result.account, caps, pools)
        await _decorate(conn, job.id, result)
        await leases.release(conn, claim.lease_id)
        return status


async def apply_interrupted(db: AsyncConnectionPool, claim, *, caps: Caps, pools, expired_ok: bool = False,
                            pause: bool = True) -> str:
    """A cancelled or timed-out submit: `unknown` if the job was sent, else back to `queued` with no attempt.
    A timed-out submit's lease has usually run past its `hard_until` by then: the engine passes `expired_ok`.
    An unsent job waits REQUEUE_PAUSE_S like one requeued after Retry or NotTaken, so a submit that keeps failing
    before the send is not claimed again on every tick; `pause=False` for a stop, where the wait would only delay."""
    async with tx(db) as conn:
        job = await _fenced_job(conn, claim, expired_ok=expired_ok)
        if job.sent_at is None:
            await _set(conn, job.id, "submitting", status="queued", account_id=None,
                       next_poll_at=_in(REQUEUE_PAUSE_S) if pause else None, **_reset({}))
            await add_event(conn, job.id, "system", "requeued", "interrupted before the send" + _dropped(job))
            status = "queued"
        else:
            status = await _unknown(conn, job, "interrupted after the send")
        await leases.release(conn, claim.lease_id)
        return status


# ---- polling (a job that was accepted and waits) ---------------------------------------------------------------
async def apply_poll(db: AsyncConnectionPool, job_id, poll_token, result: PollResult, *, caps: Caps,
                     pools: Mapping[str, pool.PoolConfig]) -> str | None:
    """Apply a poll result only while `poll_token` is still the job's token (I23: two pollers, or a Re-check that
    took a fresh token, cannot both apply). Returns the new status, or None when the result was dropped.
    A `Done` charges the account when the order was not proven taken before."""
    async with tx(db) as conn:
        job = await _lock_job(conn, job_id)
        if job.status != "waiting" or job.poll_token is None or job.poll_token != poll_token:
            return None
        await _lock_effect_account(conn, job, result.effects)
        out = result.outcome
        match out:
            case Pending():
                note = redact_log(out.note or "")[:NOTE_LIMIT]
                patch = dict(out.state_patch)
                if note and note != job.provider_state.get("pending_note"):    # a line only when the reason changes
                    await add_event(conn, job_id, "system", "pending", note)
                    patch["pending_note"] = note
                await _set(conn, job_id, "waiting", poll_token=None, next_poll_at=_in(out.next_in_s),
                           provider_state=_merge(patch))
                status = "waiting"
            case Done():
                await _charge_taken(conn, job, pools)
                await _set(conn, job_id, "waiting", status="fetching", poll_token=None, next_poll_at=_NOW,
                           taken_at=_Raw("COALESCE(taken_at, clock_timestamp())"),
                           upstream_price=out.facts.upstream_price, upstream_model=redact_log(out.facts.upstream_model),
                           provider_state=_merge_outputs(out.outputs))
                await add_event(conn, job_id, "system", "completed", "%d output(s) ready to fetch" % len(out.outputs))
                status = "fetching"
            case Failed():
                cols = dict(provider_state=_merge(out.state_patch))
                if out.taken:                           # proven taken: the account is charged here and the failure
                    await _charge_taken(conn, job, pools)   # below refunds it, both in this transaction (I7, D14)
                    cols["taken_at"] = _Raw("COALESCE(taken_at, clock_timestamp())")
                await _set(conn, job_id, "waiting", **cols)
                await fail_in_tx(conn, job_id, ("waiting",), out.code, out.detail, pools)
                status = "failed"
            case _:
                raise TypeError("unknown poll outcome %r" % (out,))
        await _effects(conn, job, result.effects, caps, pools)
        await _decorate(conn, job_id, result)
        return status


# ---- reconciling an unknown job --------------------------------------------------------------------------------
async def _still_unknown(conn: AsyncConnection, job_id, text: str) -> None:
    """The reconcile interval grows 60 s, 120 s, ... up to 30 min; the count is the reconcile events so far."""
    tried = (await (await conn.execute("SELECT count(*) FROM job_events WHERE job_id = %s AND stage = 'reconcile'",
                                       (job_id,))).fetchone())[0]
    await _set(conn, job_id, "unknown", next_poll_at=_in(min(UNKNOWN_MAX_S, UNKNOWN_FIRST_S * 2 ** tried)))
    await add_event(conn, job_id, "system", "reconcile", text)


async def apply_reconciled(db: AsyncConnectionPool, job_id, result: Reconciled, *, read_started_at: datetime,
                           caps: Caps, pools: Mapping[str, pool.PoolConfig], lease_id: int | None = None,
                           release_lease: bool = False) -> str:
    """Apply what a reconcile read found for an `unknown` job; returns the job's status afterwards.

    `NotSent` requeues the job only when BOTH hold (I27): the proving read started later than
    `sent_at + submit_timeout_s + 60 s`, and the result's own `covers_send_window` proof (the provider's assertion
    that the read covers the send window; a capped or truncated listing never does). Otherwise it is StillUnknown.
    A `Found` moves the job to `waiting`; the account is charged only when the order is proven taken.
    `lease_id` (the reconcile lease) is fenced in the same transaction when given, and deleted in it too when
    `release_lease` is set."""
    async with tx(db) as conn:
        if lease_id is not None and isinstance(result, StillUnknown):
            # A failed or timed-out read only reschedules the next try: it is recorded even when the lease has just
            # expired, as long as its row is still there (the reaper has not taken the job). A read that ran out of
            # time near its hard bound would otherwise leave no trace and hold the account until the reaper came.
            if await (await conn.execute("SELECT 1 FROM leases WHERE id = %s FOR UPDATE", (lease_id,))).fetchone() \
                    is None:
                raise LeaseLost(lease_id)
        elif lease_id is not None:
            await leases.fence(conn, lease_id)
        status = await _reconciled(conn, job_id, result, read_started_at, pools)
        if release_lease and lease_id is not None:
            await leases.release(conn, lease_id)
        return status


async def _reconciled(conn: AsyncConnection, job_id, result: Reconciled, read_started_at: datetime,
                      pools: Mapping[str, pool.PoolConfig]) -> str:
    job = await _lock_job(conn, job_id)
    if job.status != "unknown":
        return job.status
    match result:
        case Found():
            if result.taken:
                await _charge_taken(conn, job, pools)
            cols = dict(status="waiting", external_ref=result.ref, accepted_at=_Raw(
                "COALESCE(accepted_at, clock_timestamp())"), next_poll_at=_in(FIRST_POLL_S),
                provider_state=_merge(result.provider_state))
            if result.taken:
                cols["taken_at"] = _Raw("COALESCE(taken_at, clock_timestamp())")
            await _set(conn, job_id, "unknown", **cols)
            await add_event(conn, job_id, "system", "reconcile", "found upstream: the order exists")
            return "waiting"
        case NotSent():
            proven_late = job.sent_at is not None and read_started_at > job.sent_at + timedelta(
                seconds=job.submit_timeout_s + NOT_SENT_MARGIN_S)
            proven_covered = result.covers_send_window
            if proven_late and proven_covered:
                await _set(conn, job_id, "unknown", status="queued", sent_at=None, attempts=job.attempts + 1,
                           account_id=None, next_poll_at=None, **_reset({}))
                await add_event(conn, job_id, "system", "requeued",
                                "proven not sent: sent_at cleared, re-queued" + _dropped(job))
                return "queued"
            await _still_unknown(conn, job_id, "not-sent claim refused: %s" % (
                "read too early for the send window" if not proven_late else "listing does not cover the send"))
            return "unknown"
        case StillUnknown():
            await _still_unknown(conn, job_id, "still unknown")
            return "unknown"
    raise TypeError("unknown reconcile result %r" % (result,))


# ---- fetching (a job whose outputs are ready upstream) -----------------------------------------------------------
_DONE_CALLBACK = {k: _Raw("CASE WHEN callback_url IS NULL THEN %s ELSE %s END" % (keep, new)) for k, keep, new in (
    ("callback_status", "callback_status", "'pending'"), ("callback_attempts", "callback_attempts", "0"),
    ("callback_next_at", "callback_next_at", "clock_timestamp()"), ("callback_for", "callback_for",
                                                                     "clock_timestamp()"))}


def _not_the_fetcher(job: _Job, token) -> bool:
    """The job is not `fetching`, or `token` is not the one the fetcher claimed it with."""
    return job.status != "fetching" or job.poll_token is None or job.poll_token != token


async def apply_fetched(db: AsyncConnectionPool, job_id, poll_token, media: Sequence[StoredOutput]) -> bool:
    """Every output is in the object store: one transaction writes the media rows and moves the job to `done`
    (I11), only while `poll_token` is still the fetcher's. False: the result was dropped (a Re-check, another
    fetcher, or the job is no longer `fetching`); the objects stay under their final keys and are overwritten by
    the next attempt. Refuses an empty output list."""
    if not media:
        raise ValueError("a job cannot be done without outputs")
    async with tx(db) as conn:
        job = await _lock_job(conn, job_id)
        if _not_the_fetcher(job, poll_token):
            return False
        for i, m in enumerate(media):
            await conn.execute(
                "INSERT INTO job_media (job_id, role, idx, object_key, mime, size, width, height, duration_s, "
                "upstream_id, share_token) VALUES (%s, 'output', %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (job_id, i, m.object_key, m.mime, m.size, m.width, m.height, m.duration_s, m.upstream_id,
                 storage.new_share_token()))
        await failpoints.hit("fetch.rows_written")
        await _set(conn, job_id, "fetching", status="done", poll_token=None, next_poll_at=None, finished_at=_NOW,
                   error=None, error_code=None, **_DONE_CALLBACK)
        await add_event(conn, job_id, "system", "done", "%d output(s) stored" % len(media))
        return True


async def apply_fetch_failed(db: AsyncConnectionPool, job_id, poll_token, *, code: str, detail: str,
                             retry_in_s: float, deadline_s: float,
                             pools: Mapping[str, pool.PoolConfig]) -> str | None:
    """A fetch that did not work. A terminal code (an unreadable file) or a render deadline already passed fails
    the job with its refund in this transaction; anything else keeps it `fetching` and tries again in
    `retry_in_s`. None: the token was not the job's, nothing changed."""
    async with tx(db) as conn:
        job = await _lock_job(conn, job_id)
        if _not_the_fetcher(job, poll_token):
            return None
        late = (await (await conn.execute(
            "SELECT COALESCE(render_since, sent_at, created_at) + make_interval(secs => %s) <= clock_timestamp() "
            "FROM jobs WHERE id = %s", (deadline_s, job_id))).fetchone())[0]
        if rule(code).terminal or late:
            await fail_in_tx(conn, job_id, ("fetching",), code, detail, pools)
            return "failed"
        await _set(conn, job_id, "fetching", poll_token=None, next_poll_at=_in(retry_in_s))
        await add_event(conn, job_id, "system", "fetch", "fetch failed, trying again: %s - %s" % (code, detail))
        return "fetching"
