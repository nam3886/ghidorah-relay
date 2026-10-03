"""Operator actions (contracts 7.7): drain mode and the restore mark. Store functions only; the CLI wires them later."""

from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from relay_core import failpoints
from relay_core.db import tx
from relay_core.jobs.events import add_event
from relay_core.jobs.reaper import OWNER_GONE_S
from relay_core.settings import Settings

# A job that is not terminal. `queued` jobs that were never claimed cannot have been sent and are left alone.
_OPEN = """
 WHERE status IN ('queued', 'submitting', 'unknown', 'waiting', 'fetching')
   AND created_at < %(t)s + make_interval(secs => (SELECT COALESCE(max(submit_timeout_s), 0) FROM jobs))
   AND NOT (status = 'queued' AND claims = 0 AND sent_at IS NULL)
"""
_OPEN_IDS = "SELECT id FROM jobs" + _OPEN + " ORDER BY id"
_MARK = """
UPDATE jobs SET status = 'unknown', sent_at = COALESCE(sent_at, %(t)s), poll_token = NULL,
       next_poll_at = clock_timestamp(), updated_at = clock_timestamp()
""" + _OPEN + " AND id = ANY(%(ids)s) RETURNING id"
_LIVE_WORKERS = ("SELECT worker_id FROM worker_heartbeats "
                 "WHERE at > clock_timestamp() - make_interval(secs => %s) ORDER BY worker_id")


class WorkersRunning(RuntimeError):
    """`restore_mark` found a worker whose heartbeat is fresh: it must run with every worker stopped."""


async def drain(settings: Settings, provider: str, *, by: str = "cli") -> bool:
    """Drain mode: the provider claims no new work, open jobs still finish, fail, refund and notify."""
    return await settings.set(provider + ".claims_enabled", False, by=by)


async def resume(settings: Settings, provider: str, *, by: str = "cli") -> bool:
    return await settings.set(provider + ".claims_enabled", True, by=by)


async def restore_mark(db: AsyncConnectionPool, dump_time: datetime) -> int:
    """After a restore from a dump taken at `dump_time`: any job that may have been sent after the dump becomes
    `unknown`, never `queued` (I1); reconcile then proves each one. `sent_at` is `dump_time` where it was empty.
    Queued commands fail; capacity that is not a reported balance becomes unknown and every
    account is due for a check. Returns how many jobs were marked.

    It writes whole tables, so it refuses (`WorkersRunning`) while any worker's heartbeat is fresh: the runbook runs
    it before the workers start. The heartbeat table is locked first (SHARE), so a booting worker's claim loop
    waits at its first heartbeat write; its other loops do not, so the runbook starts NO worker until this has
    returned. Rows are then locked in the global order: leases, jobs, commands, accounts, capacity rows."""
    if dump_time.tzinfo is None:
        raise ValueError("the dump time needs a timezone")
    async with tx(db) as conn:
        await conn.execute("LOCK TABLE worker_heartbeats IN SHARE MODE")
        live = [r[0] for r in await (await conn.execute(_LIVE_WORKERS, (OWNER_GONE_S,))).fetchall()]
        if live:
            raise WorkersRunning("workers are running (%s): stop them before the restore mark" % ", ".join(live))
        await failpoints.hit("ops.restore.checked")
        ids = [r[0] for r in await (await conn.execute(_OPEN_IDS, {"t": dump_time})).fetchall()]
        await conn.execute("DELETE FROM leases WHERE job_id = ANY(%s)", (ids,))
        marked = [r[0] for r in await (await conn.execute(_MARK, {"t": dump_time, "ids": ids})).fetchall()]
        for job_id in marked:
            await add_event(conn, job_id, "system", "restore", "marked unknown after a restore: it may have been "
                            "sent after the dump")
        await conn.execute("UPDATE commands SET status = 'failed', finished_at = clock_timestamp(), "
                           "result = '{\"error\": \"restore\"}' WHERE status = 'queued'")
        # every account is checked next so the checks report the real balances; the account rows are locked before
        # the capacity rows
        await conn.execute("UPDATE accounts SET check_due_at = clock_timestamp()")
        # left_n is rebuilt from reported balances: a counted or reset value of the dump may be stale, so it becomes
        # unknown (allow)
        await conn.execute("UPDATE account_capacity SET left_n = NULL WHERE source <> 'reported'")
    return len(marked)
