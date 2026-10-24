"""Sweeps for the api process (invariant I25): retention, orphans, expired sessions and input
links, and the daily ledger check. Plain functions; the api's runners (`api/runners.py`) call `run_housekeeping`
every 15 minutes and the ledger check once a day. Every delete loops in bounded batches, each in its own short
transaction, and every object-store call happens outside a transaction.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg_pool import AsyncConnectionPool

from relay_core import storage
from relay_core.db import tx
from relay_core.jobs.shares import expire_input_links
from relay_core.storage import ObjectStore

log = logging.getLogger("relay.sweeps")

BATCH = 5000
MEDIA_BATCH = 200
SWEEP_STATEMENT_TIMEOUT = "60s"          # the sweeps raise the 5 s default for their own transactions
SWEEP_HOLD_S = 70.0                      # ... and `tx` holds a sweep transaction a little longer than that

# Per wallet (key, scope): used_total = sum(charge) - sum(refund) + sum(adjust); used_day is the same sum restricted
# to the wallet's day_anchor, where a refund counts only when it was made on the day of the charge it undoes.
_LEDGER_VS_COUNTERS = """
SELECT w.key_id, w.scope, w.used_total, w.used_day,
       COALESCE(sum(CASE l.entry WHEN 'charge' THEN l.amount WHEN 'refund' THEN -l.amount ELSE l.amount END), 0),
       COALESCE(sum(CASE WHEN l.billing_day = w.day_anchor THEN
                     CASE l.entry WHEN 'charge' THEN l.amount
                                  WHEN 'refund' THEN CASE WHEN c.billing_day = l.billing_day THEN -l.amount ELSE 0 END
                                  ELSE l.amount END
                   ELSE 0 END), 0)
  FROM key_wallets w
  LEFT JOIN ledger l ON l.key_id = w.key_id AND l.scope = w.scope
  LEFT JOIN ledger c ON c.id = l.refund_of
 GROUP BY w.key_id, w.scope, w.used_total, w.used_day, w.day_anchor
"""


ALERT_EVERY_H = 20
_STORE_DRIFT = """
INSERT INTO billing_alerts (alert, key_id, scope, amount, used_day, used_total, detail)
SELECT 'drift', %(k)s, %(scope)s, %(amt)s, %(day)s, %(total)s, %(detail)s
 WHERE NOT EXISTS (SELECT 1 FROM billing_alerts WHERE alert = 'drift' AND key_id = %(k)s AND scope = %(scope)s
                   AND detail = %(detail)s
                   AND at > clock_timestamp() - make_interval(hours => %(h)s))
"""


@dataclass(frozen=True)
class Drift:
    """A wallet counter that disagrees with the ledger. `counter` is `used_total` or `used_day`."""

    key_id: uuid.UUID
    scope: str
    counter: str
    stored: int
    ledger: int


async def ledger_check(db: AsyncConnectionPool) -> list[Drift]:
    """I25: every wallet's counters against its ledger. The discrepancies come back as data, are logged, and are
    stored as `billing_alerts` rows (`alert='drift'`) for the admin alerts, at most one per distinct
    drift per ALERT_EVERY_H hours so the daily check does not pile up rows. Nothing is corrected: a drift needs a
    person."""
    async with tx(db, hold_s=SWEEP_HOLD_S) as conn:
        await conn.execute("SET LOCAL statement_timeout = '%s'" % SWEEP_STATEMENT_TIMEOUT)
        rows = await (await conn.execute(_LEDGER_VS_COUNTERS)).fetchall()
    found = []
    for key_id, scope, used_total, used_day, ledger_total, ledger_day in rows:
        if used_total != ledger_total:
            found.append((Drift(key_id, scope, "used_total", used_total, ledger_total), used_day, used_total))
        if used_day != ledger_day:
            found.append((Drift(key_id, scope, "used_day", used_day, ledger_day), used_day, used_total))
    for d, _, _ in found:
        log.error("ledger drift: wallet %s %s %s is %d, the ledger says %d",
                  d.key_id, d.scope, d.counter, d.stored, d.ledger)
    if found:
        async with tx(db) as conn:
            for d, used_day, used_total in found:
                await conn.execute(_STORE_DRIFT, {
                    "k": d.key_id, "scope": d.scope, "amt": d.stored - d.ledger, "day": used_day,
                    "total": used_total, "h": ALERT_EVERY_H,
                    "detail": "%s is %d, the ledger says %d" % (d.counter, d.stored, d.ledger)})
    return [d for d, _, _ in found]


async def _purge(db: AsyncConnectionPool, table: str, where: str, params: tuple = ()) -> int:
    """Delete the rows of `table` matching `where` in batches of BATCH; `table` and `where` are fixed code, never
    input."""
    total = 0
    while True:
        async with tx(db, hold_s=SWEEP_HOLD_S) as conn:
            await conn.execute("SET LOCAL statement_timeout = '%s'" % SWEEP_STATEMENT_TIMEOUT)
            n = (await conn.execute(
                "DELETE FROM %s WHERE ctid IN (SELECT ctid FROM %s WHERE %s LIMIT %%s)" % (table, table, where),
                (*params, BATCH))).rowcount
        total += n
        if n < BATCH:
            return total


@dataclass(frozen=True)
class Retained:
    request_logs: int
    job_events: int
    debug_media: int


async def sweep_retention(db: AsyncConnectionPool, store: ObjectStore, *, retention_days: int) -> Retained:
    """Request logs, job events and debug media (object and row) older than `retention_days`. Outputs and inputs
    are never touched here. A debug object that cannot be removed keeps its row, so the next pass retries it."""
    old = "make_interval(days => %s)"
    logs = await _purge(db, "request_logs", "at < clock_timestamp() - " + old, (retention_days,))
    events = await _purge(db, "job_events", "at < clock_timestamp() - " + old, (retention_days,))
    media = 0
    while True:
        async with tx(db) as conn:
            rows = await (await conn.execute(
                "SELECT job_id, idx, object_key FROM job_media WHERE role = 'debug' "
                "AND created_at < clock_timestamp() - " + old + " LIMIT %s", (retention_days, MEDIA_BATCH))
            ).fetchall()
        if not rows:
            return Retained(logs, events, media)
        for _, _, key in rows:
            await store.remove(key)
        async with tx(db) as conn:
            for job_id, idx, _ in rows:
                await conn.execute("DELETE FROM job_media WHERE job_id = %s AND role = 'debug' AND idx = %s",
                                   (job_id, idx))
        media += len(rows)


async def sweep_orphans(db: AsyncConnectionPool, store: ObjectStore, *, min_age_h: int, enabled: bool,
                        now: datetime | None = None) -> list[str]:
    """Remove stored objects that no job owns and that are older than `min_age_h` hours (a file uploaded a moment
    before its job commits looks like an orphan). `enabled=False` is the restore switch: nothing is listed or
    removed. Lists the whole bucket in memory (accepted for now)."""
    async def owned_jobs(ids: set[str]) -> set[str]:
        """Ids that own objects: jobs, and accounts (the screenshot of an account is `<account_id>/debug/0.png`)."""
        found = []
        for i in ids:
            try:
                found.append(uuid.UUID(i))
            except ValueError:
                pass                                    # not a job id, so no job owns it
        async with tx(db) as conn:
            rows = await (await conn.execute("SELECT id FROM jobs WHERE id = ANY(%(f)s) UNION "
                                             "SELECT id FROM accounts WHERE id = ANY(%(f)s)", {"f": found})).fetchall()
        return {str(r[0]) for r in rows}

    return await storage.sweep_orphans(store, owned_jobs, min_age=timedelta(hours=min_age_h),
                                       now=now or datetime.now(UTC), enabled=enabled)


async def expire_admin_sessions(db: AsyncConnectionPool) -> int:
    return await _purge(db, "admin_sessions", "expires_at <= clock_timestamp()")


async def run_housekeeping(db: AsyncConnectionPool, store: ObjectStore, *, retention_days: int, orphan_age_h: int,
                           orphans_enabled: bool) -> dict:
    """One housekeeping pass: retention, orphans, expired sessions and input links. The ledger check is separate
    (daily). A failing step is logged and does not stop the others."""
    steps = {
        "retention": lambda: sweep_retention(db, store, retention_days=retention_days),
        "orphans": lambda: sweep_orphans(db, store, min_age_h=orphan_age_h, enabled=orphans_enabled),
        "sessions": lambda: expire_admin_sessions(db),
        "input_links": lambda: expire_input_links(db),
    }
    done = {}
    for name, step in steps.items():
        try:
            done[name] = await step()
        except Exception:
            log.exception("housekeeping step %s failed", name)
    return done
