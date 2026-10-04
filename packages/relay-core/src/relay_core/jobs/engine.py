"""The worker's claim loop for one provider (contracts 3, 7.1, 7.7, 15; invariants I1, I2, I13, I14).

One asyncio loop drives every slot. A tick (1 s) claims queued jobs through the store, each claim in its own task:
the engine's `submit` runs with NO database connection held, under `asyncio.timeout(submit_timeout_s)`, in a
`LeaseKeeper` that cancels it when its lease cannot be extended. The result is applied AFTER the timeout block (so
a result that arrives just before it still has `APPLY_MARGIN_S` of lease) but inside the same `holding`, in a new
transaction shielded from cancellation: a result that was received is applied, or dropped to the reaper by a failed
fence, never half-applied.

Whatever ends a submit without a result goes through `apply_interrupted`: a job that was sent ends `unknown`
(never `queued`, so it is never re-sent, I1); one that was not sent goes back to `queued` with no attempt.

Stop (SIGTERM or `stop()`): claims end, `fence()` and `mark_sent()` refuse (no new irreversible action), in-flight
submits finish (after `grace_s` they are cancelled and so interrupted), and the loop exits 0. Five consecutive
unexpected errors (the claim tick, a store write, an exception the engine raises) stop it the same way and exit 1,
so a broken worker is loud instead of looking idle; a submit timeout does not count (D42).
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from psycopg_pool import AsyncConnectionPool

from relay_core import pool
from relay_core.budget import BrowserBudget, Purpose, Token, effective_slots
from relay_core.db import tx
from relay_core.jobs import store
from relay_core.jobs.claim import Claim
from relay_core.jobs.runctx import EvidenceSink, LeaseCtx, Stopping
from relay_core.leases import HEARTBEAT_S, LEASE_TTL_S, LeaseKeeper, LeaseLost
from relay_core.providers import AccountView, ProviderEngine, ProviderSpec, SubmitResult

log = logging.getLogger(__name__)

TICK_S = 1.0
BEAT_S = 15.0
LAG_CHECK_S = 0.25
LAG_WARN_S = 1.0
MAX_ERRORS = 5
MAX_INFLIGHT = 32
WRITE_RESERVE_S = 10.0       # of the shutdown deadline, kept for the writes of the submits it cancels

LoadAccount = Callable[[Claim], Awaitable[AccountView]]


class ClaimLoop:
    """The claim loop of one worker. `owner` (one per boot) owns the leases; `worker_id` (stable across restarts,
    default `owner`) names the heartbeat row."""

    def __init__(self, db: AsyncConnectionPool, spec: ProviderSpec, engine: ProviderEngine, settings: Any, *,
                 owner: str, load_account: LoadAccount, budget: BrowserBudget | None = None,
                 browser_ceiling: int | None = None, evidence_sink: EvidenceSink | None = None,
                 tick_s: float = TICK_S, lease_ttl_s: float = LEASE_TTL_S, lease_beat_s: float = HEARTBEAT_S,
                 beat_s: float = BEAT_S, lag_check_s: float = LAG_CHECK_S, lag_warn_s: float = LAG_WARN_S,
                 max_errors: int = MAX_ERRORS, max_inflight: int = MAX_INFLIGHT, grace_s: float | None = None,
                 worker_id: str | None = None):
        if spec.caps.execution == "browser" and (budget is None or browser_ceiling is None):
            raise ValueError("a browser provider needs a budget and its env ceiling")
        self.db, self.spec, self.caps, self.engine, self.settings = db, spec, spec.caps, engine, settings
        self.owner, self.load_account, self.budget, self.ceiling = owner, load_account, budget, browser_ceiling
        self.worker_id = worker_id or owner     # the stable name of the heartbeat row; `owner` is per boot
        self._evidence_sink = evidence_sink
        self.tick_s, self.lease_ttl_s, self.beat_s = tick_s, lease_ttl_s, beat_s
        self.lag_check_s, self.lag_warn_s = lag_check_s, lag_warn_s
        self.max_errors, self.max_inflight, self.grace_s = max_errors, max_inflight, grace_s
        self._drain_key = spec.name + ".claims_enabled"
        self._slots_key = spec.name + ".browser_slots"
        self._keeper = LeaseKeeper(db, owner, ttl_s=lease_ttl_s, heartbeat_s=lease_beat_s)
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task] = set()
        self._applying: set[asyncio.Future] = set()
        self._tick_errors = 0           # consecutive unexpected errors of the claim tick
        self._job_errors = 0            # consecutive unexpected errors of submits
        self.exit_code = 0
        self._registered = False        # the worker process registers before any loop starts; the claim loop only once
        self.lag_max_s = 0.0            # worst loop lag since the last heartbeat row

    # ---- stop and loud failure ---------------------------------------------------------------------------------
    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        """Claims end; in-flight submits finish or are fenced; `run()` returns."""
        self._stop.set()

    @property
    def errors(self) -> int:
        """Consecutive unexpected errors. A submit that was applied clears both kinds; a clean tick clears only the
        tick's own, so a worker whose submits all fail cannot hide behind idle ticks."""
        return self._tick_errors + self._job_errors

    def _ok(self) -> None:
        self._tick_errors = self._job_errors = 0

    def _error(self, what: str, exc: BaseException | None = None, *, tick: bool = False) -> None:
        """One unexpected error. At `max_errors` in a row the worker stops and exits non-zero (v4 worker.py)."""
        if tick:
            self._tick_errors += 1
        else:
            self._job_errors += 1
        log.error("%s (%d/%d)", what, self.errors, self.max_errors, exc_info=exc)
        if self.errors >= self.max_errors and not self.exit_code:
            log.critical("%d consecutive unexpected errors: the worker stops with a failure", self.errors)
            self.exit_code = 1
            self.stop()

    # ---- the claim tick ----------------------------------------------------------------------------------------
    def _take(self) -> Token | None:
        return self.budget.try_acquire(Purpose.SUBMIT)

    async def _claim(self) -> Claim | None:
        drained = not self.settings.get(self._drain_key)
        if drained:
            return None
        return await store.claim_one(self.db, provider=self.spec.name, caps=self.caps, settings=self.settings,
                                     owner=self.owner, take_token=self._take if self.budget else None,
                                     ttl_s=self.lease_ttl_s)

    async def tick(self) -> int:
        """One pass: follow the live slot setting (D20), then claim until nothing more can run. Returns the number
        of jobs started. A drained provider claims nothing."""
        if self.budget is not None:
            self.budget.resize(effective_slots(self.settings.get(self._slots_key), self.ceiling))
        started = 0
        while not self.stopping and len(self._inflight) < self.max_inflight:
            claim = await self._claim()
            if claim is None:
                break
            task = asyncio.create_task(self._run_job(claim), name="job-%s" % claim.job_id)
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
            started += 1
        self._tick_errors = 0
        return started

    async def _claim_loop(self) -> None:
        while not self.stopping:
            try:
                await self.tick()
            except Exception as e:
                self._error("claim tick failed", e, tick=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.tick_s)

    def _pools(self) -> dict[str, pool.PoolConfig]:
        return pool.pool_configs(self.caps, self.settings.get)

    # ---- one job -----------------------------------------------------------------------------------------------
    async def _shielded(self, work: Coroutine) -> Any:
        """Run `work` to its end even if the caller is cancelled: it is a task of its own, and `run()` waits for
        every one before it returns."""
        inner = asyncio.ensure_future(work)
        self._applying.add(inner)
        inner.add_done_callback(self._applying.discard)
        # a waiter that was cancelled never reads the outcome: read it here so it is not reported as lost
        inner.add_done_callback(lambda f: f.cancelled() or f.exception())
        return await asyncio.shield(inner)

    async def _run_job(self, claim: Claim) -> None:
        try:
            await self._submit_and_apply(claim)
        except asyncio.CancelledError:
            raise
        except LeaseLost:
            log.warning("job %s: lease %s lost, the reaper takes over", claim.job_id, claim.lease_id)
        except Exception as e:
            self._error("job %s: the result could not be applied" % claim.job_id, e)
        finally:
            if claim.token is not None:
                claim.token.release()

    async def _submit_and_apply(self, claim: Claim) -> None:
        """The submit with no connection held, then its result applied: both inside one `holding`, so the keeper
        extends the lease until the fenced write. The timeout covers only the engine's I/O; the result is applied
        after it, shielded. A submit that ends without a result goes through `_interrupted`."""
        timer: asyncio.Timeout | None = None
        applying = False
        try:
            async with self._keeper.holding(claim.lease_id) as settle:
                async with asyncio.timeout(claim.submit_timeout_s) as timer:
                    view = await store.job_view(self.db, claim.job_id)
                    ctx = LeaseCtx(self.db, claim, account=await self.load_account(claim), settings=self.settings,
                                   stopping=lambda: self.stopping, evidence_sink=self._evidence_sink)
                    result = await self.engine.submit(ctx, view)
                applying = True
                settle()                    # the fenced write decides from here; a beat after its commit is no verdict
                await self._apply(claim, result)
        except BaseException as e:
            if applying:
                raise
            await self._interrupted(claim, e, timed_out=timer is not None and timer.expired())
            if not isinstance(e, Exception):
                raise

    async def _interrupted(self, claim: Claim, cause: BaseException, *, timed_out: bool) -> None:
        """A submit that ended without a result: `unknown` if it was sent, else `queued`, never re-sent (I1)."""
        # D42: only the core's own submit timer (`timed_out`) is an expected, bounded outcome: a warning, not an
        # error. An exception the engine raises, a TimeoutError of its own included, is unexpected and counts, so a
        # broken engine cannot spin forever unseen.
        if timed_out:
            log.warning("job %s: the submit ran past its %s s limit", claim.job_id, claim.submit_timeout_s)
        elif not isinstance(cause, asyncio.CancelledError | LeaseLost | Stopping):
            self._error("job %s: submit ended without a result" % claim.job_id, cause)
        # an unsent job that was given back waits like a NotTaken one; a stop or a lost lease needs no pause
        pause = not isinstance(cause, asyncio.CancelledError | LeaseLost | Stopping)
        try:
            status = await self._shielded(store.apply_interrupted(
                self.db, claim, caps=self.caps, pools=self._pools(), expired_ok=True, pause=pause))
            log.info("job %s interrupted (%s): now %s", claim.job_id, type(cause).__name__, status)
        except LeaseLost:
            log.warning("job %s: lease %s is gone, the reaper takes over", claim.job_id, claim.lease_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._error("job %s: the interruption could not be recorded" % claim.job_id, e)

    async def _apply(self, claim: Claim, result: SubmitResult) -> None:
        """Outside the timeout, shielded, in a new transaction fenced by the lease id. The shield means a keeper
        cancel while it runs stops the waiting, not the transaction."""
        applied: list[str] = []

        async def work() -> str:
            applied.append(await store.apply_submit(self.db, claim, result, caps=self.caps, pools=self._pools()))
            return applied[0]

        try:
            status = await self._shielded(work())
        except LeaseLost:
            log.warning("job %s: result dropped, lease %s is not live; the reaper takes over", claim.job_id,
                        claim.lease_id)
            return
        except asyncio.CancelledError:
            if applied:             # the write had committed (it deleted the lease, which the keeper then saw as lost)
                log.info("job %s applied: now %s", claim.job_id, applied[0])
                self._ok()
            raise
        log.info("job %s applied: now %s", claim.job_id, status)
        self._ok()

    # ---- heartbeat row and loop-lag watchdog -------------------------------------------------------------------
    async def beat(self) -> None:
        """Write this worker's `worker_heartbeats` row: loop lag, busy slots, consecutive errors (contracts 15)."""
        busy = self.budget.busy if self.budget is not None else len(self._inflight)
        lag_ms, self.lag_max_s = int(self.lag_max_s * 1000), 0.0
        async with tx(self.db) as conn:
            await conn.execute(
                "INSERT INTO worker_heartbeats (worker_id, provider, started_at, at, loop_lag_ms, slots_busy, "
                "consecutive_errors) VALUES (%(w)s, %(p)s, clock_timestamp(), clock_timestamp(), %(lag)s, %(busy)s, "
                "%(err)s) ON CONFLICT (worker_id) DO UPDATE SET at = clock_timestamp(), "
                "loop_lag_ms = excluded.loop_lag_ms, slots_busy = excluded.slots_busy, "
                "consecutive_errors = excluded.consecutive_errors",
                {"w": self.worker_id, "p": self.spec.name, "lag": lag_ms, "busy": busy, "err": self.errors})

    async def _beats(self) -> None:
        while True:
            try:
                await self.beat()
            except Exception as e:
                log.error("worker heartbeat failed: %s", e)
            await asyncio.sleep(self.beat_s)

    async def _watch_lag(self) -> None:
        """A coroutine that sleeps `lag_check_s` and measures how late it wakes: a blocked loop shows as lag."""
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            await asyncio.sleep(self.lag_check_s)
            now = loop.time()
            lag, last = now - last - self.lag_check_s, now
            self.lag_max_s = max(self.lag_max_s, lag)
            if lag > self.lag_warn_s:
                log.warning("event loop lag %.2f s (a blocking call on the loop?)", lag)

    async def _register(self) -> None:
        """A heartbeat row left by an earlier run means that run ended abnormally: the browser budget ramps up
        again instead of starting at full load (contracts 3, restart back-off). Once per run."""
        if self._registered:
            return
        async with tx(self.db) as conn:
            left_behind = await (await conn.execute("SELECT 1 FROM worker_heartbeats WHERE worker_id = %s",
                                                    (self.worker_id,))).fetchone()
            # Restarted in place, never deleted and re-inserted: a monitor never sees the worker missing.
            await conn.execute("UPDATE worker_heartbeats SET started_at = clock_timestamp() WHERE worker_id = %s",
                               (self.worker_id,))
        if left_behind is not None and self.budget is not None:
            log.warning("worker %s restarted after an abnormal exit: the browser budget ramps up", self.worker_id)
            self.budget.start_ramp()
        await self.beat()
        self._registered = True

    # ---- run and shutdown --------------------------------------------------------------------------------------
    async def run(self, *, install_signals: bool = False) -> int:
        """Run until `stop()`, SIGTERM/SIGINT (with `install_signals`) or the error limit. Returns the exit code:
        0 after a clean stop, 1 after `max_errors` consecutive unexpected errors."""
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT) if install_signals else ()
        for sig in signals:
            loop.add_signal_handler(sig, self.stop)
        background: list[asyncio.Task] = []
        try:
            await self._register()
            keeper_stop = asyncio.Event()
            background = [asyncio.create_task(self._keeper.run(keeper_stop)), asyncio.create_task(self._beats()),
                          asyncio.create_task(self._watch_lag())]
            try:
                await self._claim_loop()
            finally:
                await self._drain_inflight(None if self.grace_s is None else loop.time() + self.grace_s)
                keeper_stop.set()
        finally:
            for sig in signals:
                loop.remove_signal_handler(sig)
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
        await self._leave()
        return self.exit_code

    async def _drain_inflight(self, deadline: float | None = None) -> None:
        """In-flight submits finish; shortly before `deadline` (a loop time: the worker's one shutdown deadline) the
        rest are cancelled, which interrupts them (a sent job ends `unknown`). The writes of the cancelled submits and
        every other shielded write are waited for, but never past `deadline`: a write still running then finishes
        on its own, and the reaper moves the job if the process ends first."""
        loop = asyncio.get_running_loop()

        def left() -> float | None:
            return None if deadline is None else max(0.0, deadline - loop.time())

        tasks = list(self._inflight)
        if tasks:
            budget = left()
            cancel_after = None if budget is None else budget - min(WRITE_RESERVE_S, budget / 2)
            _, pending = await asyncio.wait(tasks, timeout=cancel_after)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=left())
        writes = list(self._applying)
        if writes:
            await asyncio.wait(writes, timeout=left())

    async def _leave(self) -> None:
        """A clean stop removes the heartbeat row (so the next start is not a back-off start); after a failure the
        row stays, with its error count, for the alert."""
        try:
            if self.exit_code == 0:
                async with tx(self.db) as conn:
                    await conn.execute("DELETE FROM worker_heartbeats WHERE worker_id = %s", (self.worker_id,))
            else:
                await self.beat()
        except Exception as e:
            log.error("worker %s could not write its last heartbeat: %s", self.worker_id, e)
