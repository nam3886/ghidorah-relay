"""Re-check, Recover and Regenerate (contracts 7.1; D14, D15; invariants I8, I10, I13, I17, I23).

The only ways a terminal job changes (I13). Each is one transaction.

Re-check polls again for a result that was already paid for: no new order. It restarts the render clock (D15), takes a
fresh poll token (a poll result still in flight is dropped, I23) and clears the callback fields. After a refund the KEY
is charged again (a new ledger row, which a later failure can refund, I10); the account pool never is. A refused
re-charge (key disabled, limit reached) does not block the revival: a paid result must be reachable.

Recover attaches outputs that were stored from the upstream account, idempotently by `upstream_id`, and ends the job
`done`; a job that was already `done` keeps its price (I8). Regenerate makes a NEW job, with a new marker, charged to
the same key through the ordinary creation gate.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from relay_core import billing, storage
from relay_core.db import tx
from relay_core.jobs.create import Created, InputMedia, NewJob, Settings, body_hash, create_job
from relay_core.jobs.events import add_event
from relay_core.providers import Plan

log = logging.getLogger(__name__)

# What a revived job forgets of its terminal event: the error, the finish time and the webhook state.
_CLEAR_CALLBACK = ("callback_status = NULL, callback_attempts = 0, callback_next_at = NULL, callback_for = NULL")
_NET = ("SELECT COALESCE(sum(CASE entry WHEN 'charge' THEN amount ELSE -amount END), 0) FROM ledger "
        "WHERE job_id = %s AND entry IN ('charge', 'refund')")


class JobRefused(Exception):
    """The job is not in a state this action may act on. `code` is an error-registry code (HTTP 409 or 404)."""

    def __init__(self, code: str, text: str):
        super().__init__(text)
        self.code = code


class Skipped(Exception):
    """A bulk Re-check left this job alone because its provider flagged it (`provider_state.no_recheck`)."""


async def _recharge(conn: AsyncConnection, job_id, key_id, provider: str, kind: str, price: int) -> int:
    """After a refund the key pays again: a new charge row for the price. Nothing is charged when the job was not
    refunded (its net charge is still the price) or is free. A refusal is logged and the job is revived anyway."""
    if price <= 0:
        return 0
    net = (await (await conn.execute(_NET, (job_id,))).fetchone())[0]
    if net >= price:
        return 0
    try:
        await billing.charge(conn, key_id, provider, kind, price, job_id)
    except (billing.QuotaExceeded, billing.KeyDisabled) as e:     # a missing wallet is a bug: it propagates (§4.1)
        log.warning("job %s revived without a new charge: %s", job_id, e)
        return 0
    return price


async def _locked(conn: AsyncConnection, job_id) -> tuple:
    row = await (await conn.execute(
        "SELECT status, external_ref, key_id, provider, wallet_kind, price, provider_state FROM jobs "
        "WHERE id = %s FOR UPDATE", (job_id,))).fetchone()
    if row is None:
        raise JobRefused("resource.not_found", "no job %s" % job_id)
    return row


def _note(recharged: int) -> str:
    return " - %d refunded points charged again" % recharged if recharged else ""


# ---- Re-check ----------------------------------------------------------------------------------------------------
async def recheck_job(db: AsyncConnectionPool, job_id, *, bulk: bool = False) -> int:
    """Send a `failed`, `waiting` or `unknown` job that has an upstream reference back to `waiting` for another
    poll. Returns the points charged again (0 when none). Raises JobRefused: `job.already_finished` (done),
    `job.busy` (submitting or fetching: a run is working on it), `job.not_submitted` (no reference, or still
    queued). `bulk=True` raises Skipped for a job its provider flagged `no_recheck`."""
    async with tx(db) as conn:
        status, ref, key_id, provider, kind, price, state = await _locked(conn, job_id)
        if status == "done":
            raise JobRefused("job.already_finished", "the job already finished")
        if status in ("submitting", "fetching"):
            raise JobRefused("job.busy", "the job is being worked on")
        if status == "queued" or ref is None:
            raise JobRefused("job.not_submitted", "the job was never sent upstream")
        if bulk and state.get("no_recheck") is True:
            raise Skipped("the provider flagged this job as not re-checkable")
        recharged = await _recharge(conn, job_id, key_id, provider, kind, price)
        await conn.execute(
            "UPDATE jobs SET status = 'waiting', render_since = clock_timestamp(), error = NULL, error_code = NULL, "
            "finished_at = NULL, poll_token = gen_random_uuid(), next_poll_at = clock_timestamp(), "
            + _CLEAR_CALLBACK + ", updated_at = clock_timestamp() WHERE id = %s", (job_id,))
        await add_event(conn, job_id, "system", "recheck", "manual re-check: back to waiting, render clock reset%s"
                        % _note(recharged))
        return recharged


@dataclass(frozen=True)
class BulkRecheck:
    rechecked: tuple[str, ...]
    skipped: dict[str, str]          # job id -> why it was left alone


async def recheck_bulk(db: AsyncConnectionPool, job_ids: Iterable) -> BulkRecheck:
    """Re-check many jobs, each in its own transaction: one that cannot be re-checked does not stop the rest."""
    done: list[str] = []
    skipped: dict[str, str] = {}
    for job_id in job_ids:
        try:
            await recheck_job(db, job_id, bulk=True)
            done.append(str(job_id))
        except JobRefused as e:
            skipped[str(job_id)] = e.code
        except Skipped as e:
            skipped[str(job_id)] = str(e)
        except Exception:
            log.exception("bulk re-check of job %s failed", job_id)
            skipped[str(job_id)] = "internal"
    return BulkRecheck(tuple(done), skipped)


# ---- Recover -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class StoredOutput:
    """An output the command already stored in the object store (foreign I/O never runs inside a transaction)."""
    object_key: str
    mime: str
    size: int
    upstream_id: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None


@dataclass(frozen=True)
class Recovered:
    status: str
    attached: int            # outputs that were new (an upstream id already on the job is skipped)
    recharged: int


async def recover_job(db: AsyncConnectionPool, job_id, outputs: Sequence[StoredOutput], *, account_id=None,
                      note: str = "") -> Recovered:
    """`recover_in_tx` in a transaction of its own (admin and API callers)."""
    async with tx(db) as conn:
        return await recover_in_tx(conn, job_id, outputs, account_id=account_id, note=note)


async def recover_in_tx(conn: AsyncConnection, job_id, outputs: Sequence[StoredOutput], *, account_id=None,
                        note: str = "") -> Recovered:
    """Inside the caller's transaction (the command runner's, fenced by its lease): attach `outputs` to a
    terminal job and end it `done`. The job must be `done` or `failed` (`job.busy` otherwise). An `upstream_id`
    the job already has is skipped, so a repeated command adds nothing. A job that was already `done` is not
    charged again (I8); a refunded one is charged again (I10). `account_id` records which account the outputs came
    from. A job with a webhook gets a fresh pending delivery: the latest terminal event is the one delivered (I16)."""
    if not outputs or any(not o.upstream_id for o in outputs):
        raise ValueError("recovered outputs need an upstream id each")
    status, _ref, key_id, provider, kind, price, _state = await _locked(conn, job_id)
    if status not in ("done", "failed"):
        raise JobRefused("job.busy", "only a finished job can be recovered")
    have = {r[0] for r in await (await conn.execute(
        "SELECT upstream_id FROM job_media WHERE job_id = %s AND role = 'output'", (job_id,))).fetchall()}
    nxt = (await (await conn.execute("SELECT COALESCE(max(idx) + 1, 0) FROM job_media WHERE job_id = %s "
                                     "AND role = 'output'", (job_id,))).fetchone())[0]
    attached = 0
    for out in outputs:
        if out.upstream_id in have:
            continue
        have.add(out.upstream_id)
        await conn.execute(
            "INSERT INTO job_media (job_id, role, idx, object_key, mime, size, width, height, duration_s, "
            "upstream_id, share_token) VALUES (%s, 'output', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (job_id, nxt + attached, out.object_key, out.mime, out.size, out.width, out.height, out.duration_s,
             out.upstream_id, storage.new_share_token()))
        attached += 1
    recharged = await _recharge(conn, job_id, key_id, provider, kind, price) if status == "failed" else 0
    await conn.execute(
        "UPDATE jobs SET status = 'done', error = NULL, error_code = NULL, "
        "finished_at = COALESCE(finished_at, clock_timestamp()), "
        "account_id = COALESCE(%s, account_id), poll_token = NULL, next_poll_at = NULL, "
        "callback_status = CASE WHEN callback_url IS NULL THEN NULL ELSE 'pending' END, callback_attempts = 0, "
        "callback_next_at = CASE WHEN callback_url IS NULL THEN NULL ELSE clock_timestamp() END, "
        "callback_for = CASE WHEN callback_url IS NULL THEN NULL ELSE clock_timestamp() END, "
        "updated_at = clock_timestamp() WHERE id = %s", (account_id, job_id))
    await add_event(conn, job_id, "system", "recovered", "recovered %d output(s)%s%s" % (
        attached, " - %s" % note if note else "", _note(recharged)))
    return Recovered("done", attached, recharged)


# ---- Regenerate --------------------------------------------------------------------------------------------------
CopyInputs = Callable[[Any, list[InputMedia]], Awaitable[tuple[InputMedia, ...]]]

_SOURCE = "SELECT provider, key_id, prompt, title, request FROM jobs WHERE id = %s"


async def regenerate_job(db: AsyncConnectionPool, job_id, settings: Settings, *, plan: Plan,
                         copy_inputs: CopyInputs | None = None, idem_key: str | None = None) -> Created:
    """A NEW job with the old job's prompt, title and key and a NEW marker (derived from its own id), charged to the
    key through the ordinary creation gate (cap, wallet). The `plan` is the caller's: it ran `spec.plan()` and the
    key's allow-list for today's price, catalog and timeout; nothing of the old job's plan is reused. Input objects
    are copied for it by `copy_inputs(new_job_id, old_inputs)`, which runs before the creation transaction (it does
    object-store I/O). The old job is untouched but for an event that points at the new one.

    With `idem_key` the creation is idempotent per key (the key's jobs.idem_key): the same key and source job again
    returns the job made the first time, `replayed`, and writes no second event."""
    async with tx(db) as conn:
        row = await (await conn.execute(_SOURCE, (job_id,))).fetchone()
        if row is None:
            raise JobRefused("resource.not_found", "no job %s" % job_id)
        old = [InputMedia(*r) for r in await (await conn.execute(
            "SELECT idx, object_key, mime, size, width, height FROM job_media WHERE job_id = %s AND role = 'input' "
            "ORDER BY idx", (job_id,))).fetchall()]
    provider, key_id, prompt, title, request = row
    new = NewJob(provider, key_id, plan, prompt, title, request=request)   # kept, so the new job can be regenerated
    if idem_key is not None:
        new = replace(new, idem_key=idem_key, idem_body_hash=body_hash({"regenerate": str(job_id)}))
    if old:
        if copy_inputs is None:
            raise ValueError("the job has input media: copy_inputs is required")
        new = replace(new, inputs=await copy_inputs(new.job_id, old))
    created = await create_job(db, new, settings)
    if not created.replayed:
        try:
            async with tx(db) as conn:
                await add_event(conn, job_id, "system", "regenerated", "regenerated as job %s" % created.job_id)
        except Exception:      # the new job is committed and charged: its id must reach the caller
            log.exception("the regenerated event of job %s was not written (new job %s)", job_id, created.job_id)
    return created
