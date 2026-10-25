"""The claim (invariants I6, I9): which queued job runs on which account, in ONE transaction.

The heads of every pool (the oldest queued job of each `account_cost` in each pool) are merged and walked oldest
first by `(created_at, id)`, so a video backlog never starves an older image job. A head looks for an
account that is available, checked, not cooling, not resting in the pool, has a free slot, and can afford the head
(`left_n - reserved >= account_cost`, unknown allows). Affordability is decided before any browser token is taken, so
a head that no account can pay for never stalls the cheaper jobs behind it. A candidate's account row is locked first,
`SKIP LOCKED` (one another transaction holds is skipped), then its pool row, so a claim never waits for anyone.
The oldest queued job the winner can afford is claimed with `FOR UPDATE SKIP LOCKED` and its lease is inserted in the
same transaction. The job lease's hard bound is `submit_timeout_s + APPLY_MARGIN_S`: the engine's timeout cuts the
submit, and the margin keeps the lease alive for the fenced apply of a result that arrived just before it.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from relay_core import leases, pool
from relay_core.budget import Token
from relay_core.db import lock_account, tx
from relay_core.jobs.apply import RECONCILE_CLAIM_STOP_S, RENDER_BACKSTOP_S, StartupGrace
from relay_core.jobs.create import Settings
from relay_core.providers import Caps
from relay_core.settings import SettingsError
from relay_core.txguard import require_tx

log = logging.getLogger(__name__)

_HEADS = """
SELECT id, account_cost, tried, created_at FROM (
    SELECT DISTINCT ON (account_cost) id, account_cost, tried, created_at FROM jobs
     WHERE provider = %(provider)s AND pool IS NOT DISTINCT FROM %(pool)s AND status = 'queued'
       AND created_at > clock_timestamp() - make_interval(secs => %(deadline)s)
       AND (next_poll_at IS NULL OR next_poll_at <= clock_timestamp())
     ORDER BY account_cost, created_at, id) h
 ORDER BY created_at, id
"""

# `next_poll_at` on a queued job is its not-before time: a requeue after Retry or NotTaken pauses the job (apply.py).

# Never-checked accounts are not claimable; a pool at rest, a cooling account, a full account, an account claimed less
# than the send gap ago and (browser only) an account with an unknown job due for a browser reconcile are skipped.
# Untried-for-this-head first, then LRU.
_CANDIDATES = """
SELECT a.id FROM accounts a
 WHERE a.provider = %(provider)s AND a.state = 'available' AND a.last_checked_at IS NOT NULL
   AND (a.cooldown_until IS NULL OR a.cooldown_until <= clock_timestamp())
   AND (a.last_used_at IS NULL OR a.last_used_at <= clock_timestamp() - make_interval(secs => %(gap)s))
   AND (%(pool)s::text IS NULL OR NOT EXISTS (
        SELECT 1 FROM account_pools c
         WHERE c.account_id = a.id AND c.pool = %(pool)s AND c.rest_until > clock_timestamp()))
   AND (%(slots)s::int IS NULL OR
        (SELECT count(*) FROM leases l WHERE l.account_id = a.id AND l.until > clock_timestamp()) < %(slots)s)
   AND (NOT %(browser)s OR NOT EXISTS (
        SELECT 1 FROM jobs u WHERE u.account_id = a.id AND u.status = 'unknown'
           AND (u.next_poll_at IS NULL OR u.next_poll_at <= clock_timestamp())))
 ORDER BY a.id = ANY(%(tried)s::uuid[]), a.last_used_at NULLS FIRST, a.id
"""

_PICK = """
SELECT id, account_cost, submit_timeout_s, tried FROM jobs
 WHERE provider = %(provider)s AND pool IS NOT DISTINCT FROM %(pool)s AND status = 'queued'
   AND created_at > clock_timestamp() - make_interval(secs => %(deadline)s)
   AND (next_poll_at IS NULL OR next_poll_at <= clock_timestamp())
   AND (%(afford)s::int IS NULL OR account_cost <= %(afford)s)
 ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
"""


@dataclass(frozen=True)
class Claim:
    """A job in `submitting` under `lease_id`. A browser `token` is released by the caller after the submit."""
    job_id: Any
    lease_id: int
    account_id: Any
    provider: str
    pool: str | None
    account_cost: int
    submit_timeout_s: int
    owner: str
    token: Token | None = None


class _NoToken:
    """Sentinel: no browser slot is free, so nothing is claimed this tick."""


NO_TOKEN = _NoToken()


async def _claim_oldest(conn: AsyncConnection, *, provider: str, caps: Caps, cfgs: dict[str, pool.PoolConfig],
                        deadline_s: float, owner: str, ttl_s: float, gap_s: float,
                        take_token: Callable[[], Token | None] | None) -> Claim | _NoToken | None:
    require_tx(conn)
    heads = []
    for name, cfg in (*cfgs.items(), (None, None)):
        scope = {"provider": provider, "pool": name, "deadline": deadline_s}
        for head_id, cost, head_tried, created_at in await (await conn.execute(_HEADS, scope)).fetchall():
            heads.append((created_at, head_id, name, cfg, cost, head_tried, scope))
    heads.sort(key=lambda h: (h[0], h[1]))
    for _created, _head_id, name, cfg, cost, head_tried, scope in heads:
        candidates = await (await conn.execute(_CANDIDATES, {
            **scope, "gap": gap_s, "slots": caps.account_slots, "browser": caps.execution == "browser",
            "tried": list(head_tried)})).fetchall()
        for (account_id,) in candidates:
            afford = None
            if cfg is None:
                if not await lock_account(conn, account_id, skip_locked=True):
                    continue                        # another transaction holds it: this claim never waits
            else:
                cap = await pool.lock_capacity(conn, account_id, cfg)
                if cap is None:
                    continue
                held = await pool.reserved(conn, account_id, name)
                if not pool.can_afford(cap.left_n, held, cost):
                    continue
                afford = None if cap.left_n is None else cap.left_n - held
            token = None
            if take_token is not None:
                token = take_token()
                if token is None:
                    return NO_TOKEN
            try:
                claim = await _take(conn, account_id, scope, afford, caps, owner, ttl_s, token)
            except BaseException:
                if token is not None:
                    token.release()
                raise
            if claim is not None:
                return claim
            if token is not None:
                token.release()
    return None


async def _take(conn, account_id, scope, afford, caps, owner, ttl_s, token) -> Claim | None:
    job = await (await conn.execute(_PICK, {**scope, "afford": afford})).fetchone()
    if job is None:
        return None
    job_id, cost, timeout_s, tried = job
    lease_id = await leases.acquire(conn, account_id=account_id, purpose="job", owner=owner,
                                    hard_s=timeout_s + leases.APPLY_MARGIN_S, ttl_s=ttl_s, job_id=job_id,
                                    pool=scope["pool"], max_live=caps.account_slots, skip_locked=True)
    if lease_id is None:
        return None
    await conn.execute(
        "UPDATE jobs SET status = 'submitting', account_id = %s, claims = claims + 1, next_poll_at = NULL, "
        "tried = CASE WHEN %s = ANY(tried) THEN tried ELSE tried || %s::uuid END, "
        "updated_at = clock_timestamp() WHERE id = %s", (account_id, account_id, account_id, job_id))
    await conn.execute("UPDATE accounts SET last_used_at = clock_timestamp() WHERE id = %s", (account_id,))
    return Claim(job_id, lease_id, account_id, scope["provider"], scope["pool"], cost, timeout_s, owner, token)


def send_gap_s(settings: Settings, provider: str) -> float:
    """`<provider>.send_gap_s`, or 0 when the provider declares no such setting."""
    try:
        return settings.get(provider + ".send_gap_s")
    except (KeyError, SettingsError):
        return 0


async def claim_one(db: AsyncConnectionPool, *, provider: str, caps: Caps, settings: Settings, owner: str,
                    take_token: Callable[[], Token | None] | None = None,
                    ttl_s: float = leases.LEASE_TTL_S) -> Claim | None:
    """Claim at most one job for `provider`; None when nothing can run now.

    `settings` supplies `<provider>.claims_enabled` (drain mode, I9: a drained provider claims nothing),
    `unsent_deadline_s`, the pool windows (`<provider>.pools`) and, when the provider declares it,
    `<provider>.send_gap_s`: an account claimed (its `last_used_at`) less than that many seconds ago is not claimed
    again, so an upstream that puts a bot checkpoint in front of a busy account sees its sends spaced.
    `take_token` is the browser semaphore's
    non-blocking acquire for a
    submit (None for an HTTP provider); a returned token is released here when no claim results and by the caller
    after the submit otherwise."""
    if not settings.get(provider + ".claims_enabled"):
        return None
    cfgs = pool.pool_configs(provider, caps, settings.get)
    deadline_s = settings.get("unsent_deadline_s")
    gap_s = send_gap_s(settings, provider)
    claim: Claim | None = None
    try:
        async with tx(db) as conn:
            found = await _claim_oldest(conn, provider=provider, caps=caps, cfgs=cfgs, deadline_s=deadline_s,
                                        owner=owner, ttl_s=ttl_s, gap_s=gap_s, take_token=take_token)
            if isinstance(found, Claim):
                claim = found
    except BaseException:
        if claim is not None and claim.token is not None:
            claim.token.release()
        raise
    return claim


# ---- poll and fetch claims (visibility timeout) and reconcile claims ----------------------------------------------
@dataclass(frozen=True)
class Visible:
    """A job claimed for a poll or a fetch: only the holder of `token` may apply its result (I23)."""
    job_id: Any
    token: Any
    account_id: Any
    claimed_at: datetime       # the database's clock at the claim: before the read starts (see `apply_poll`)


_VISIBLE = """
UPDATE jobs SET poll_token = gen_random_uuid(), updated_at = clock_timestamp(),
       next_poll_at = clock_timestamp() + make_interval(secs => %(vis)s)
 WHERE id IN (SELECT id FROM jobs WHERE provider = %(provider)s AND status = %(status)s
                 AND next_poll_at <= clock_timestamp()
               ORDER BY next_poll_at, id LIMIT %(n)s FOR UPDATE SKIP LOCKED)
   AND status = %(status)s AND next_poll_at <= clock_timestamp()
RETURNING id, poll_token, account_id, clock_timestamp()
"""


async def claim_visible(db: AsyncConnectionPool, *, provider: str, status: str, visibility_s: float,
                        limit: int) -> list[Visible]:
    """Claim up to `limit` due jobs of `status` (`waiting` for the poller, `fetching` for the fetcher): a fresh
    `poll_token` and `next_poll_at = now + visibility_s`, in one statement under `SKIP LOCKED`, so two loops never
    take the same job. No account lease. A claim that is never applied becomes due again after the visibility
    timeout and is claimed with a new token."""
    if status not in ("waiting", "fetching"):
        raise ValueError("only waiting and fetching jobs are claimed by visibility")
    async with tx(db) as conn:
        rows = await (await conn.execute(_VISIBLE, {"provider": provider, "status": status, "vis": visibility_s,
                                                    "n": limit})).fetchall()
    return [Visible(*r) for r in rows]


_RECONCILE_DUE = """
SELECT j.id, j.account_id FROM jobs j
 WHERE j.provider = %(provider)s AND j.status = 'unknown' AND j.after_sent = 'reconcile'
   AND j.account_id IS NOT NULL
   AND (j.next_poll_at IS NULL OR j.next_poll_at <= clock_timestamp())
   AND (COALESCE(j.render_since, j.sent_at) + make_interval(secs => %(deadline)s) > clock_timestamp()
        OR ((j.late_seen_at IS NULL OR %(grace)s
             OR j.late_seen_at + make_interval(secs => %(backstop)s) > clock_timestamp()
             OR j.late_seen_at <= COALESCE(j.render_since, j.sent_at) + make_interval(secs => %(deadline)s))
            AND (j.answered_at IS NULL
                 OR j.answered_at <= COALESCE(j.render_since, j.sent_at) + make_interval(secs => %(deadline)s))))
 ORDER BY j.next_poll_at NULLS FIRST, j.id LIMIT %(n)s FOR UPDATE OF j SKIP LOCKED
"""


@dataclass(frozen=True)
class ReconcileClaim:
    """An `unknown` job under a `reconcile` lease. `read_started_at` is the database's clock at the claim, so the
    NotSent timing rule (I27) never compares two machines' clocks."""
    job_id: Any
    lease_id: int
    account_id: Any
    owner: str
    read_started_at: datetime
    hard_s: float
    token: Token | None = None


async def claim_reconcile(db: AsyncConnectionPool, *, provider: str, caps: Caps, deadline_s: float, owner: str,
                          hard_s: float = leases.RECONCILE_HARD_S, ttl_s: float = leases.LEASE_TTL_S,
                          token: Token | None = None, grace: StartupGrace | None = None) -> ReconcileClaim | None:
    """Claim the next due `unknown` job under a `reconcile` lease with a hard bound. A job past its render deadline is
    still claimed until a read that started after the deadline has been answered (the final read the reaper waits for),
    and never in the last `RECONCILE_CLAIM_STOP_S` before the backstop (which counts from the reaper's `late_seen_at`;
    the startup `grace` lifts it). `next_poll_at` moves past the bound
    so no second reconciler takes it; applying the result sets the real next time. Works on any account state (reads
    only). The caller took `token` from the reconcile share for a browser provider; it is released by the caller when
    the result is None."""
    async with tx(db) as conn:
        due = await (await conn.execute(_RECONCILE_DUE, {"provider": provider, "deadline": deadline_s,
                                                         "backstop": RENDER_BACKSTOP_S - RECONCILE_CLAIM_STOP_S,
                                                         "grace": bool(grace), "n": 8})).fetchall()
        for job_id, account_id in due:
            lease_id = await leases.acquire(conn, account_id=account_id, purpose="reconcile", owner=owner,
                                            hard_s=hard_s, ttl_s=ttl_s, job_id=job_id,
                                            max_live=caps.account_slots, skip_locked=True)
            if lease_id is None:
                continue
            started = (await (await conn.execute(
                "UPDATE jobs SET next_poll_at = clock_timestamp() + make_interval(secs => %s), "
                "updated_at = clock_timestamp() WHERE id = %s AND status = 'unknown' "
                "RETURNING clock_timestamp()", (hard_s + 1, job_id))).fetchone())[0]
            return ReconcileClaim(job_id, lease_id, account_id, owner, started, hard_s, token)
    return None
