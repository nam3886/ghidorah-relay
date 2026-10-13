"""The reaper (contracts 7.1 "Reaper"; invariants I1, I9, I14, I17, I21).

Every `interval_s` (30 s), for one provider, each transition is ONE transaction that locks what it changes,
re-checks its condition under the lock and compare-and-sets the status, so two reapers (or the reaper and a live
worker) never both act:

- a `submitting` job whose lease expired: `queued` if it was never sent, `unknown` if it was sent. The requeue counts
  an attempt only when the lease's owner is gone (its worker's `worker_heartbeats` row is missing, older than
  `owner_gone_s`, or started after this lease's last heartbeat: the worker restarted); at `Caps.max_attempts`
  the job then fails `queue.never_started` with the refund instead. A live owner whose lease merely expired
  (a stalled keeper) is requeued without counting, so it can still record its own zero-attempt interruption;
  never `queued` after the send (I1). The lease row is deleted in the same transaction. A lease still locked by a
  fenced write in flight is skipped (`SKIP LOCKED`): that write is alive and ends the job itself.
- the other expired leases (check, command, reconcile) are deleted; a `running` command whose lease expired is
  `failed` in the same transaction (I14).
- a `running` command with no lease (delete, wake, check all, check) whose `started_at` is older than its bound
  (`leases.COMMAND_HARD_S`, else `COMMAND_STALL_S`, plus a margin) has stalled: it is failed (I14, contracts 4.4).
- a `queued` job past `unsent_deadline_s` from its creation fails `queue.never_started`.
- a `waiting` or `unknown` job past `<provider>.render_deadline_s`, counted from `render_since` (from the send
  when it has none), so a Re-check that restarts the clock saves it (D15, I17), fails with the spec's
  `deadline_cause` code, but only once a final read can no longer save it: a read that started after the deadline and
  that the engine answered (`jobs.answered_at`), or `RENDER_BACKSTOP_S` after a running worker first saw the job late
  (`jobs.late_seen_at`, stamped by this pass; not within `STARTUP_GRACE_S` of the worker starting, so the reads of a
  restarted stack come first), and never while a read of it is in flight. A job no read will ever reach
  (no poll or no reconcile for it) fails at the deadline as before.
- a `waiting` or `fetching` job nobody can see (`next_poll_at` NULL) is made visible again.
- every `drift_s` (300 s) the pool counters of the provider's accounts are compared with their charges and corrected
  (`pool.check_drift`, contracts 7.2).

Failing always goes through `fail_in_tx`: status, error and refunds in one transaction (I21). The settings are read
live on every pass.
"""

import asyncio
import contextlib
import logging
import time
from collections import Counter
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from relay_core import alerts, failpoints, leases, pool
from relay_core.db import tx
from relay_core.errors import RULES
from relay_core.jobs.apply import RENDER_BACKSTOP_S, UNKNOWN_FIRST_S, StartupGrace
from relay_core.jobs.events import add_event
from relay_core.jobs.fail import fail_in_tx
from relay_core.providers import ProviderSpec

log = logging.getLogger(__name__)

INTERVAL_S = 30.0
DRIFT_S = 300.0
DEADLINE_CODE = "render.deadline"
NEVER_STARTED = "queue.never_started"
OWNER_GONE_S = 45.0          # 3 x the worker heartbeat (engine.BEAT_S); `Worker` passes 3 x its own beat
COMMAND_STALL_S = 300       # bound of a command that takes no lease and has no entry in leases.COMMAND_HARD_S
COMMAND_STALL_MARGIN_S = 60  # room for the command to record its own outcome before the reaper decides it stalled

# Where the render clock starts: the send, or the last Re-check (D15). Never the creation of the job.
RENDER_FROM = "COALESCE(render_since, sent_at)"
UNSENT_FROM = "created_at"

_SUBMIT_LEASES = """
SELECT l.id, l.job_id FROM leases l JOIN jobs j ON j.id = l.job_id
 WHERE l.purpose = 'job' AND l.until <= clock_timestamp() AND j.provider = %s ORDER BY l.id
"""

# Lock the expired lease first (the order the result application also takes: lease, then job), skipping one a
# fenced write holds, and say whether its owner is gone; then ONE statement moves the job, guarded by its status.
# The heartbeat row is keyed by the worker id: the per-boot owner without its final "/<suffix>" (the whole owner when
# the worker has no separate id). A worker id never contains "/" (`serve` refuses one). A row started after the lease's
# last heartbeat belongs to a restarted worker, so this lease's boot is dead.
_LEASE_LOCK = """
SELECT NOT EXISTS (SELECT 1 FROM worker_heartbeats h
                    WHERE h.worker_id IN (l.owner, regexp_replace(l.owner, '/[^/]*$', ''))
                      AND h.at > clock_timestamp() - make_interval(secs => %s) AND h.started_at <= l.heartbeat_at)
  FROM leases l WHERE l.id = %s AND l.until <= clock_timestamp() FOR UPDATE OF l SKIP LOCKED
"""
_REAP_JOB = """
WITH old AS (SELECT external_ref AS old_ref FROM jobs WHERE id = %s FOR UPDATE)
UPDATE jobs SET status = CASE WHEN sent_at IS NULL THEN 'queued' ELSE 'unknown' END,
       account_id = CASE WHEN sent_at IS NULL THEN NULL ELSE account_id END,
       external_ref = CASE WHEN sent_at IS NULL THEN NULL ELSE external_ref END,
       provider_state = CASE WHEN sent_at IS NULL THEN '{}'::jsonb ELSE provider_state END,
       next_poll_at = CASE WHEN sent_at IS NULL THEN NULL ELSE clock_timestamp() + make_interval(secs => %s) END,
       attempts = CASE WHEN sent_at IS NULL THEN attempts + %s ELSE attempts END,
       updated_at = clock_timestamp()
  FROM old WHERE jobs.id = %s AND jobs.status = 'submitting' RETURNING jobs.status, old.old_ref, jobs.attempts
"""

_LATE = """
SELECT status, provider_state, render_since, prompt, sent_text FROM jobs
 WHERE id = %%s AND provider = %%s AND status = ANY(%%s)
   AND %s + make_interval(secs => %%s) <= clock_timestamp() %s FOR UPDATE
"""
_LATE_IDS = """
SELECT id FROM jobs WHERE provider = %%s AND status = ANY(%%s)
   AND %s + make_interval(secs => %%s) <= clock_timestamp() %s ORDER BY id
"""
# The render deadline waits for a final read where one will happen: a poll for a `waiting` job, a reconcile for an
# `unknown` job that is reconciled at all (the claim's own conditions, `claim._RECONCILE_DUE`). It lets the job go
# when a read that STARTED after the deadline was answered, or one hour after a running worker first saw the job late
# (`late_seen_at`, not the deadline: a worker that was down for hours still reads the job first), and never while a
# read of the job is in flight (a claimed poll, a live reconcile lease): its result may be the video.
# Params: the deadline, whether the backstop applies (not in the startup grace), the deadline, the backstop.
_FINAL_READ = """AND (answered_at > {render} + make_interval(secs => %s)
       OR (%s AND late_seen_at > {render} + make_interval(secs => %s)
           AND late_seen_at + make_interval(secs => %s) <= clock_timestamp())
       OR NOT (status = 'waiting' AND {poll} OR status = 'unknown' AND {reconcile} AND after_sent = 'reconcile'
               AND account_id IS NOT NULL))
   AND NOT (status = 'waiting' AND poll_token IS NOT NULL AND next_poll_at > clock_timestamp()
            OR status = 'unknown' AND EXISTS (SELECT 1 FROM leases l WHERE l.job_id = jobs.id
                                               AND l.purpose = 'reconcile' AND l.until > clock_timestamp()))
""".format(render=RENDER_FROM, poll="{poll}", reconcile="{reconcile}")
# The first pass that sees a sent job past its deadline stamps `late_seen_at` (again after a Re-check moved the
# deadline past it) and makes the job's next read due now, unless a read of it is in flight.
_STAMP_LATE = """
UPDATE jobs SET late_seen_at = clock_timestamp(),
       next_poll_at = CASE WHEN status = 'waiting' AND poll_token IS NULL
                                OR status = 'unknown' AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.job_id = jobs.id
                                       AND l.purpose = 'reconcile' AND l.until > clock_timestamp())
                           THEN LEAST(COALESCE(next_poll_at, clock_timestamp()), clock_timestamp())
                           ELSE next_poll_at END
 WHERE provider = %%s AND status IN ('waiting', 'unknown', 'fetching') AND sent_at IS NOT NULL
   AND %(r)s + make_interval(secs => %%s) <= clock_timestamp()
   AND (late_seen_at IS NULL OR late_seen_at <= %(r)s + make_interval(secs => %%s))
""" % {"r": RENDER_FROM}
_LAST_TEXT = ("SELECT text FROM job_events WHERE job_id = %s AND source = 'upstream' AND at >= %s "
              "ORDER BY id DESC LIMIT 1")


class Reaper:
    def __init__(self, db: AsyncConnectionPool, spec: ProviderSpec, settings: Any, *, interval_s: float = INTERVAL_S,
                 drift_s: float = DRIFT_S, owner_gone_s: float = OWNER_GONE_S, grace: StartupGrace | None = None):
        self.db, self.spec, self.settings, self.interval_s = db, spec, settings, interval_s
        self.drift_s, self.owner_gone_s = drift_s, owner_gone_s
        self.grace = grace or StartupGrace(0)    # no grace unless the worker passes its own
        self._drift_at: float | None = None      # monotonic time of the last drift pass
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _pools(self) -> dict[str, pool.PoolConfig]:
        return pool.pool_configs(self.spec.caps, self.settings.get)

    # ---- one pass ----------------------------------------------------------------------------------------------
    async def tick(self) -> Counter:
        """One pass over every rule; returns how many transitions each made. A rule that errors is logged and the
        rest still run."""
        done: Counter = Counter()
        for name, step in (("submit_lease", self._submit_leases), ("other_lease", self._other_leases),
                           ("stalled_command", self._stalled_commands),
                           ("never_started", self._never_started), ("render_deadline", self._render_deadline),
                           ("visibility", self._visibility), ("pool_drift", self._pool_drift)):
            try:
                done[name] += await step()
            except Exception:
                log.exception("reaper: %s pass failed", name)
        return done

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.tick()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.interval_s)

    # ---- expired leases ----------------------------------------------------------------------------------------
    async def _submit_leases(self) -> int:
        async with tx(self.db) as conn:
            expired = await (await conn.execute(_SUBMIT_LEASES, (self.spec.name,))).fetchall()
        return sum([await self._reap_submit(lease_id, job_id) for lease_id, job_id in expired])

    async def _reap_submit(self, lease_id: int, job_id) -> bool:
        async with tx(self.db) as conn:
            locked = await (await conn.execute(_LEASE_LOCK, (self.owner_gone_s, lease_id))).fetchone()
            if locked is None:
                return False                    # gone, extended, or held by a write that is still alive
            counts = int(locked[0])             # a live owner's stalled lease is requeued without an attempt
            row = await (await conn.execute(_REAP_JOB, (job_id, UNKNOWN_FIRST_S, counts, job_id))).fetchone()
            await leases.release(conn, lease_id)
            if row is None:
                return False                    # the job had already left `submitting`: only the lease was stale
            if row[0] == "queued" and counts and row[2] >= self.spec.caps.max_attempts:
                # every claim of this job ended with a dead worker before the send: stop feeding it to workers
                await fail_in_tx(conn, job_id, ("queued",), NEVER_STARTED,
                                 "the worker died before the send on every attempt", self._pools())
            elif row[0] == "queued":
                await add_event(conn, job_id, "system", "requeued", "the lease expired before the send: re-queued%s"
                                % (" (dropped reference %s)" % row[1] if row[1] else ""))
            else:
                await add_event(conn, job_id, "system", "unknown", "the lease expired after the send: outcome unknown")
            return True

    async def _other_leases(self) -> int:
        """Delete expired check, command and reconcile leases; a command that was running under one has stalled."""
        async with tx(self.db) as conn:
            gone = await leases.reap_expired(conn)
            ids = [lease.id for lease in gone if lease.purpose == "command"]
            if ids:
                await conn.execute(
                    "UPDATE commands SET status = 'failed', finished_at = clock_timestamp(), result = %s "
                    "WHERE lease_id = ANY(%s) AND status = 'running'",
                    (Jsonb({"error": "the command's lease expired before it finished"}), ids))
        return len(gone)

    async def _stalled_commands(self) -> int:
        """A command that holds no lease has nothing that expires, so a crash leaves it `running`: fail it once it
        has run past its bound. A command with a lease is the lease pass's business."""
        async with tx(self.db) as conn:
            cur = await conn.execute(
                "UPDATE commands SET status = 'failed', finished_at = clock_timestamp(), result = %(r)s "
                "WHERE provider = %(p)s AND status = 'running' AND lease_id IS NULL "
                "AND COALESCE(started_at, created_at) + make_interval(secs => "
                "COALESCE((%(b)s::jsonb ->> kind)::float8, %(d)s) + %(m)s) <= clock_timestamp()",
                {"r": Jsonb({"error": "the command stalled: it ran past its time limit without finishing"}),
                 "p": self.spec.name, "b": Jsonb(leases.COMMAND_HARD_S), "d": COMMAND_STALL_S,
                 "m": COMMAND_STALL_MARGIN_S})
            return cur.rowcount

    # ---- deadlines ---------------------------------------------------------------------------------------------
    async def _fail_late(self, expr: str, statuses: tuple[str, ...], seconds: float, why, *,
                         report: bool = False, final_read: bool = False) -> int:
        hold = ""
        extra: tuple = ()
        if final_read:
            hold = _FINAL_READ.format(poll=str(self.spec.caps.has_poll).lower(),
                                      reconcile=str(self.spec.caps.has_reconcile).lower())
            extra = (seconds, not self.grace, seconds, RENDER_BACKSTOP_S)
        async with tx(self.db) as conn:
            ids = [r[0] for r in await (await conn.execute(
                _LATE_IDS % (expr, hold), (self.spec.name, list(statuses), seconds, *extra))).fetchall()]
        failed = 0
        for job_id in ids:
            try:
                failed += await self._fail_one(expr, statuses, seconds, why, job_id, report, hold, extra)
            except Exception:
                log.exception("reaper: job %s could not be failed", job_id)
        return failed

    async def _fail_one(self, expr: str, statuses: tuple[str, ...], seconds: float, why, job_id,
                        report: bool = False, hold: str = "", extra: tuple = ()) -> bool:
        async with tx(self.db) as conn:
            row = await (await conn.execute(_LATE % (expr, hold), (job_id, self.spec.name, list(statuses), seconds,
                                                                   *extra))).fetchone()
            if row is None:
                return False            # the condition no longer holds: a Re-check, a result or a claim got there
            status, state, render_since, prompt, sent_text = row
            text = await self._last_text(conn, job_id, render_since)
            code, detail = why(text, state)
            failed = await fail_in_tx(conn, job_id, (status,), code, detail, self._pools())
            if failed and report:
                # After the failure and the refund, so nothing about the report can hold them back: the hook and the
                # builder are guarded, the insert has its savepoint.
                built = self._unclear(job_id, text, state, alerts.JobTexts(prompt, sent_text))
                if built is not None:
                    await alerts.store(conn, job_id, built)
                    await failpoints.hit("reaper.unclear_reported")
            return failed

    def _unclear(self, job_id, text: str, state, texts: alerts.JobTexts) -> alerts.Report | None:
        """The provider's report of a deadline it cannot explain, as a row, or None. The job's texts are those of the
        row the reaper locked, for the builder to take the customer's own words out."""
        try:
            said = self.spec.unclear_at_deadline(text, state)
        except Exception as exc:     # noqa: BLE001
            log.error("%s: unclear_at_deadline failed for job %s: %s", self.spec.name, job_id, type(exc).__name__)
            return None
        return alerts.prepare(job_id, self.spec.name, said, texts)

    @staticmethod
    async def _last_text(conn, job_id, since) -> str:
        """The newest upstream text of this render: anything first seen before `render_since` is ignored (I17)."""
        row = await (await conn.execute(_LAST_TEXT, (job_id, since or "-infinity"))).fetchone()
        return row[0] if row else ""

    async def _never_started(self) -> int:
        return await self._fail_late(UNSENT_FROM, ("queued",), self.settings.get("unsent_deadline_s"),
                                     lambda _text, _state: (NEVER_STARTED, "the job was never started"))

    def _cause(self, text: str, state) -> tuple[str, str]:
        code = self.spec.deadline_cause(text, state)
        if code not in RULES:
            log.error("%s: deadline_cause returned the unregistered code %r", self.spec.name, code)
            code = DEADLINE_CODE
        if code == DEADLINE_CODE:
            return code, "no result before the render deadline"
        return code, "ended at the render deadline; cause: " + code

    async def _render_deadline(self) -> int:
        seconds = self.settings.get(self.spec.name + ".render_deadline_s")
        async with tx(self.db) as conn:
            await conn.execute(_STAMP_LATE, (self.spec.name, seconds, seconds))
        return await self._fail_late(RENDER_FROM, ("waiting", "unknown"), seconds, self._cause,
                                     report=True, final_read=True)

    # ---- visibility --------------------------------------------------------------------------------------------
    async def _visibility(self) -> int:
        """A waiting or fetching job with no `next_poll_at` is claimed by nobody: make it due now."""
        async with tx(self.db) as conn:
            cur = await conn.execute(
                "UPDATE jobs SET next_poll_at = clock_timestamp(), poll_token = NULL, updated_at = clock_timestamp() "
                "WHERE provider = %s AND status IN ('waiting', 'fetching') AND next_poll_at IS NULL",
                (self.spec.name,))
            return cur.rowcount

    # ---- pool counters -----------------------------------------------------------------------------------------
    async def _pool_drift(self) -> int:
        """Every `drift_s`: correct the pool counters that disagree with their charges. Returns the corrections."""
        now = time.monotonic()
        if self._drift_at is not None and now - self._drift_at < self.drift_s:
            return 0
        async with tx(self.db) as conn:
            fixed = await pool.check_drift(conn, self.spec.name, self._pools())
        self._drift_at = now
        return len(fixed)
