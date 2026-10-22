"""Leases: who may act on an account right now (invariants I2, I14).

A lease row is the fencing token: its `id` is monotonic and every irreversible write is checked against it with
`clock_timestamp()` under `FOR SHARE` (a stale `now()` frozen at transaction start would let an expired holder
through). `owner` names one worker BOOT (`<worker id>/<short uuid>`): a restarted process never inherits a dead one's
leases, and two workers of one provider never touch each other's. The heartbeat extends only the leases its keeper
holds, so a lease no live task holds expires within its TTL. Every lease has a `hard_until`: the heartbeat never
extends past it, so a hung holder cannot keep an account forever.

Timing is passed in, never read from module constants inside SQL, so tests inject short values.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from relay_core import failpoints
from relay_core.db import lock_account, tx

log = logging.getLogger(__name__)

HEARTBEAT_S = 15.0
LEASE_TTL_S = 60.0
# A job lease's hard bound is the submit timeout plus this: a result that arrives just before the timeout can still
# be applied while the keeper keeps the lease alive.
APPLY_MARGIN_S = 30
CHECK_HARD_S = 180
RECONCILE_HARD_S = 300
# Hard bound per command kind that runs under an account lease. A kind without an entry cannot take a lease.
COMMAND_HARD_S: dict[str, int] = {
    "recover": 600, "list_conversations": 300, "refresh_catalog": 120, "titles": 120,
    "check": CHECK_HARD_S, "proxy_recheck": 120,
}
PURPOSES = ("job", "check", "command", "reconcile")


class LeaseLost(Exception):
    """The lease is gone or expired: the holder must stop and must not write."""

    def __init__(self, lease_id: int):
        super().__init__("lease %s is lost" % lease_id)
        self.lease_id = lease_id


def command_hard_s(kind: str) -> int:
    try:
        return COMMAND_HARD_S[kind]
    except KeyError:
        raise ValueError("command kind %r has no lease bound" % kind) from None


async def acquire(conn: AsyncConnection, *, account_id, purpose: str, owner: str, hard_s: float,
                  ttl_s: float = LEASE_TTL_S, job_id=None, pool: str | None = None,
                  max_live: int | None = None, skip_locked: bool = False) -> int | None:
    """Insert a lease and return its id, or None when the account already has `max_live` live leases (any
    purpose), is gone, or (`skip_locked`, for a claim that must never wait) is locked by another transaction. Run
    inside the caller's transaction: the account row is locked `FOR NO KEY UPDATE` first, so two callers cannot
    both pass the count (that lock does not conflict with the key-share that inserting a lease row takes). `hard_s`
    is required: every purpose has a bound. `until = LEAST(now + ttl, hard_until)`."""
    if purpose not in PURPOSES:
        raise ValueError("unknown lease purpose %r" % purpose)
    if hard_s <= 0 or ttl_s <= 0:
        raise ValueError("hard_s and ttl_s must be positive")
    if not await lock_account(conn, account_id, skip_locked=skip_locked):
        return None
    if max_live is not None:
        live = (await (await conn.execute(
            "SELECT count(*) FROM leases WHERE account_id = %s AND until > clock_timestamp()",
            (account_id,))).fetchone())[0]
        await failpoints.hit("lease.acquire.counted")
        if live >= max_live:
            return None
    row = await (await conn.execute(
        "INSERT INTO leases (account_id, job_id, purpose, pool, owner, until, hard_until) "
        "SELECT %(a)s, %(j)s::uuid, %(p)s, %(pool)s::text, %(o)s, "
        "LEAST(t.n + make_interval(secs => %(ttl)s), t.n + make_interval(secs => %(hard)s)), "
        "t.n + make_interval(secs => %(hard)s) FROM (SELECT clock_timestamp() AS n) t RETURNING id",
        {"a": account_id, "j": job_id, "p": purpose, "pool": pool, "o": owner, "ttl": ttl_s,
         "hard": hard_s})).fetchone()
    return row[0]


async def live_count(conn: AsyncConnection, account_id) -> int:
    """Live leases on the account (slot counting uses these, never expired rows)."""
    return (await (await conn.execute("SELECT count(*) FROM leases WHERE account_id = %s AND until > clock_timestamp()",
                                      (account_id,))).fetchone())[0]


async def release(conn: AsyncConnection, lease_id: int) -> bool:
    """Delete one lease by id. False when it was already gone."""
    return (await conn.execute("DELETE FROM leases WHERE id = %s", (lease_id,))).rowcount > 0


@dataclass(frozen=True)
class ExpiredLease:
    id: int
    account_id: object
    job_id: object
    purpose: str


async def reap_expired(conn: AsyncConnection, purposes: Iterable[str] = ("check", "command", "reconcile")
                       ) -> list[ExpiredLease]:
    """Delete expired leases of the given purposes and return them (a command's owner fails the command whose
    `lease_id` appears here). Job leases are not reaped here: the reaper takes them together with their job's
    transition, in one transaction."""
    rows = await (await conn.execute(
        "DELETE FROM leases WHERE until <= clock_timestamp() AND purpose = ANY(%s) "
        "RETURNING id, account_id, job_id, purpose", (list(purposes),))).fetchall()
    return [ExpiredLease(*r) for r in rows]


FENCE_SQL = "SELECT 1 FROM leases WHERE id = %s AND until > clock_timestamp() FOR SHARE"
HEARTBEAT_SQL = (
    "UPDATE leases SET until = LEAST(clock_timestamp() + make_interval(secs => %s), "
    "COALESCE(hard_until, 'infinity')), heartbeat_at = clock_timestamp() "
    "WHERE id IN (SELECT id FROM leases "
    "WHERE owner = %s AND id = ANY(%s) AND until > clock_timestamp() FOR UPDATE SKIP LOCKED) RETURNING id")


async def fence(conn: AsyncConnection, lease_id: int) -> None:
    """Raise LeaseLost unless the lease is live now. Call inside the transaction that does the fenced write: the
    `FOR SHARE` row lock keeps the heartbeat and the reaper from touching the lease until that transaction ends."""
    if await (await conn.execute(FENCE_SQL, (lease_id,))).fetchone() is None:
        raise LeaseLost(lease_id)


async def fence_once(pool: AsyncConnectionPool, lease_id: int) -> None:
    """RunCtx.fence(): the check alone, in one short transaction."""
    async with tx(pool) as conn:
        await fence(conn, lease_id)


async def heartbeat(pool: AsyncConnectionPool, owner: str, held: Iterable[int], *,
                    ttl_s: float = LEASE_TTL_S) -> set[int]:
    """One statement for the leases `held` by this keeper: extend the live ones of `owner` (never past
    `hard_until`), return their ids. An expired lease is not revived; a lease that is not held, or belongs to
    another owner, is not touched. A row a fenced write holds is skipped, not waited for (one slow write must not
    void the beat of every other lease): it is missing from the result and retried on the next beat."""
    held = list(held)
    if not held:
        return set()
    async with pool.connection() as conn:
        rows = await (await conn.execute(HEARTBEAT_SQL, (ttl_s, owner, held))).fetchall()
    return {r[0] for r in rows}


class LeaseKeeper:
    """Keeps this worker's leases alive and cancels the task of any lease it could not extend.

    A task holds a lease with `async with keeper.holding(lease_id) as settle:`. When a beat finds the lease missing
    from the result (expired, reaped, past its hard bound) the holding task is cancelled and the `async with` raises
    LeaseLost instead of CancelledError. The holder calls `settle()` right before its final fenced write: from then
    on a MISSING row is no verdict (that write's own commit deletes the lease, and the fence alone decides whether
    it may happen), so a beat that lands after the commit cancels nothing. A row that is still there but expired is
    still lost. The beat keeps extending a settled lease whose row exists.
    """

    def __init__(self, pool: AsyncConnectionPool, owner: str, *, ttl_s: float = LEASE_TTL_S,
                 heartbeat_s: float = HEARTBEAT_S):
        self._pool, self.owner, self.ttl_s, self.heartbeat_s = pool, owner, ttl_s, heartbeat_s
        self._held: dict[int, asyncio.Task] = {}
        self._lost: set[int] = set()
        self._settling: set[int] = set()

    @contextlib.asynccontextmanager
    async def holding(self, lease_id: int) -> AsyncIterator[Callable[[], None]]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("holding() must run inside a task")
        self._held[lease_id] = task
        try:
            yield lambda: self._settling.add(lease_id)
        except asyncio.CancelledError:
            if lease_id in self._lost:
                task.uncancel()
                raise LeaseLost(lease_id) from None
            raise
        finally:
            self._held.pop(lease_id, None)
            self._lost.discard(lease_id)
            self._settling.discard(lease_id)

    async def beat(self) -> set[int]:
        """One heartbeat. Returns the ids that were lost on this beat; their tasks are cancelled."""
        held = set(self._held)          # a lease registered while the statement runs is judged on the next beat
        alive = await heartbeat(self._pool, self.owner, held, ttl_s=self.ttl_s)
        gone = {i for i in held if i not in alive}
        if gone:
            rows = await self._rows(gone)
            gone -= {i for i, live in rows.items() if live}     # skipped (locked by a fenced write): next beat
            # a settled lease whose row is gone was consumed by its own write
            gone -= (gone & self._settling) - rows.keys()
        lost = {i for i in gone if i in self._held}
        for lease_id in lost:
            log.error("lease %s lost: cancelling its task", lease_id)
            self._lost.add(lease_id)
            self._held[lease_id].cancel()
        return lost

    async def _rows(self, ids: Iterable[int]) -> dict[int, bool]:
        """The rows that still exist among `ids`, each with whether it is live now."""
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id, until > clock_timestamp() FROM leases WHERE id = ANY(%s)", (list(ids),))).fetchall()
        return {r[0]: r[1] for r in rows}

    async def run(self, stop: asyncio.Event) -> None:
        """The heartbeat loop. A failed beat is logged and retried on the next one; the leases then expire by
        themselves if the database stays unreachable, which is the safe direction."""
        while not stop.is_set():
            try:
                await self.beat()
            except Exception as e:
                log.error("lease heartbeat failed: %s", e)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self.heartbeat_s)
