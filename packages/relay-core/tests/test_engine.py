"""The claim loop (invariants I1, I2, I9, I14): a submit runs with no connection held under
its timeout, the result is applied outside it and shielded, anything that ends a sent submit without a result is
`unknown` and never `queued`, five consecutive unexpected errors exit non-zero, a browser provider follows the live
slot setting, and the worker's heartbeat row and loop-lag watchdog report."""

import asyncio
import json
import logging
import time

import pytest
from enginekit import in_status, job_status, lease_count, make_loop, running, until, within
from jobkit import POOLS, SPEC, account, create, key, run

from relay_core import crypto, failpoints
from relay_core.budget import BrowserBudget
from relay_core.db import tx
from relay_core.jobs import apply, store
from relay_core.jobs.runctx import Renewal, load_account_view
from relay_core.providers import Accepted, Completed, Fail, Output, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, FakeSpec, sent, unsent
from relay_core.testing.mutation import mutation

E = "packages/relay-core/src/relay_core/jobs/engine.py"
A = "packages/relay-core/src/relay_core/jobs/apply.py"
EXPIRED_OK = "pools=self._pools(), expired_ok=True, pause=pause))"
EXPIRED_NOT_OK = "pools=self._pools(), expired_ok=False, pause=pause))"

APPLY_IN_TIMEOUT = "result = await self.engine.submit(ctx, view)"
APPLY_IN_TIMEOUT_MUT = ("result = await self.engine.submit(ctx, view)\n"
                        "                    await self._apply(claim, result)")
SHIELD = "return await asyncio.shield(inner)"
NO_SHIELD = "return await inner"
UNKNOWN = ("if job.sent_at is None:\n"
           "            await _set(conn, job.id, \"submitting\", status=\"queued\", account_id=None,\n")
QUEUED_ALWAYS = ("if True:\n"
                 "            await _set(conn, job.id, \"submitting\", status=\"queued\", account_id=None,\n")
RESET = "def _ok(self) -> None:\n        self._tick_errors = self._job_errors = 0"
NO_RESET = "def _ok(self) -> None:\n        pass"
CHECKED = "if self.errors >= self.max_errors and not self.exit_code:"
NEVER_CHECKED = "if False and not self.exit_code:"
RESIZE = "self.budget.resize(self.settings.get(self._slots_key))"
LIMITS_UPDATE = ", limits = excluded.limits"
LIMITS_INSERT = "%(err)s, %(lim)s)"
LIMITS_NULL_INSERT = "%(err)s, NULL)"
SECRET = "sessionid=abcdef0123456789abcdef0123456789"
OWN_TIMER = "timed_out=timer is not None and timer.expired())"
ANY_TIMEOUT = "timed_out=isinstance(e, TimeoutError))"
PAUSE_SET = "next_poll_at=_in(REQUEUE_PAUSE_S) if pause else None, **_reset({}))"
PAUSE_NONE = "next_poll_at=None, **_reset({}))"
APPLY_IN_LEASE = "                await self._apply(claim, result)"
APPLY_AFTER_LEASE = "            await self._apply(claim, result)"
MARGIN = "hard_s=timeout_s + leases.APPLY_MARGIN_S,"
NO_MARGIN = "hard_s=timeout_s,"
DRAIN_WRITES = "await asyncio.wait(writes, timeout=left())"
DRAIN_WRITES_FOREVER = "await asyncio.wait(writes)"
C = "packages/relay-core/src/relay_core/jobs/claim.py"
L = "packages/relay-core/src/relay_core/leases.py"
SETTLE = "settle()                    # the fenced write"
NO_SETTLE = "pass                        # the fenced write"
ROW_JUDGED = "gone -= (gone & self._settling) - rows.keys()"
ROW_NOT_JUDGED = "gone -= gone & self._settling"
FENCE = "    else:\n        await leases.fence(conn, claim.lease_id)\n    job = await _lock_job(conn, claim.job_id)"
NO_FENCE = "    else:\n        pass\n    job = await _lock_job(conn, claim.job_id)"


@pytest.fixture(autouse=True)
def _no_requeue_pause(monkeypatch):
    """A requeued job is claimable at once, except in the test of the pause itself."""
    monkeypatch.setattr(apply, "REQUEUE_PAUSE_S", 0)


def row(db, job_id, names: str):
    return db.one("SELECT %s FROM jobs WHERE id = %%s" % names, job_id)


def ok() -> SubmitResult:
    return SubmitResult(Completed((Output("fake://x/0", "video/mp4", 0, "up-x-0"),)))


async def hold_job(p, k, engine, *, script=None, prompt="a prompt", **opts):
    """Create one job (hold completion unless told otherwise), script its submit, and return (loop, job id)."""
    opts.setdefault("completion", "hold")
    made = await create(p, k, prompt, **opts)
    if script:
        engine.script_submit(made.job_id, *script)
    return made.job_id


# ---- the happy path ----------------------------------------------------------------------------------------------
def test_the_loop_claims_submits_and_applies_in_both_completion_modes(db):
    k = key(db)
    account(db)
    account(db)

    async def scenario(p):
        engine = FakeEngine()
        held = await create(p, k, "held", completion="hold")
        detached = await create(p, k, "detached", completion="detach")
        loop = make_loop(p, engine)
        async with running(loop) as task:
            await until(lambda: lease_count(db) == 0 and all(
                job_status(db, j.job_id) not in ("queued", "submitting") for j in (held, detached)),
                what="both applied")
        return held.job_id, detached.job_id, task.result(), loop.errors

    held, detached, code, errors = run(db, scenario)
    assert (job_status(db, held), job_status(db, detached), code, errors) == ("fetching", "waiting", 0, 0)
    assert row(db, held, "sent_at IS NOT NULL, taken_at IS NOT NULL") == (True, True)


def test_a_job_with_no_account_to_run_on_stays_queued_and_nothing_errors(db):
    k = key(db)

    async def scenario(p):
        made = await create(p, k)
        loop = make_loop(p)
        async with running(loop):
            await asyncio.sleep(0.2)
        return made.job_id, loop.errors

    job_id, errors = run(db, scenario)
    assert (job_status(db, job_id), errors) == ("queued", 0)


# ---- a hung submit (I1, I14) -------------------------------------------------------------------------------------
@mutation(A, UNKNOWN, QUEUED_ALWAYS, expect="timed out waiting for the hung submit to be resolved")
def test_a_hung_submit_after_the_send_ends_unknown_after_its_timeout_and_is_never_resent(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, script=[sent(SubmitResult(Accepted("never")), delay_s=30)],
                                submit_timeout_s=1)
        loop = make_loop(p, engine)
        started = time.monotonic()
        async with running(loop):
            await until(in_status(db, job_id, "unknown"), what="the hung submit to be resolved")
            took = time.monotonic() - started
            await asyncio.sleep(0.3)              # more ticks: a resend would show as a second submit
        return job_id, engine, took, loop.errors

    job_id, engine, took, errors = run(db, scenario)
    assert job_status(db, job_id) == "unknown" and 0.9 < took < 3
    assert [c for c in engine.calls if c[0] == "submit"] == [("submit", str(job_id))]
    assert row(db, job_id, "sent_at IS NOT NULL, attempts, claims") == (True, 0, 1)
    assert lease_count(db) == 0 and errors == 0       # a timeout is expected and bounded; the lease was released


TIMEOUT_IS_EXPECTED = "if timed_out:"


@mutation(E, TIMEOUT_IS_EXPECTED, "if False:", expect="assert alive and errors == 0 and code == 0")
def test_submit_timeouts_never_count_toward_the_error_exit_and_are_logged_as_warnings(db, caplog):
    k = key(db)
    for _ in range(4):
        account(db, left=100)

    async def scenario(p):
        engine = FakeEngine()
        jobs = [await hold_job(p, k, engine, script=[sent(SubmitResult(Accepted("never")), delay_s=30)],
                               submit_timeout_s=1, prompt="p%d" % i) for i in range(4)]
        loop = make_loop(p, engine, max_errors=3)             # three counted timeouts would stop it
        with caplog.at_level(logging.WARNING, logger="relay_core.jobs.engine"):
            async with running(loop) as task:
                for job_id in jobs:
                    await until(in_status(db, job_id, "unknown"), timeout_s=15, what="every hung submit to resolve")
                alive = not task.done()
        return alive, loop.errors, task.result()

    alive, errors, code = run(db, scenario)
    assert alive and errors == 0 and code == 0
    assert any(r.levelno == logging.WARNING and "ran past" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.name == "relay_core.jobs.engine" and r.levelno >= logging.ERROR]


@mutation(E, OWN_TIMER, ANY_TIMEOUT, expect="timed out waiting for the worker to stop after its error limit")
def test_a_timeout_error_the_engine_raises_itself_counts_like_any_other_unexpected_error(db):
    """Only the core's own submit timer is exempt. A dead proxy makes the engine's connect raise TimeoutError
    before the send: that job is requeued, and it must reach the five-error exit instead of looping unseen."""
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        await hold_job(p, k, engine, script=[unsent(TimeoutError("connect timed out"))])
        loop = make_loop(p, engine, max_inflight=1)         # one submit at a time: no sixth error can overlap the fifth
        async with running(loop) as task:
            await until(lambda: loop.exit_code == 1, what="the worker to stop after its error limit")
        return task.result(), loop.errors

    assert run(db, scenario) == (1, 5)


@mutation(A, PAUSE_SET, PAUSE_NONE, expect="claimed again on every tick")
def test_a_submit_that_fails_before_the_send_is_paused_and_not_claimed_on_every_tick(db, monkeypatch):
    monkeypatch.setattr(apply, "REQUEUE_PAUSE_S", 30)
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, script=[unsent(TimeoutError("connect timed out"))])
        loop = make_loop(p, engine)
        async with running(loop):
            await until(lambda: row(db, job_id, "claims")[0] >= 1, what="the first claim")
            await asyncio.sleep(0.6)                        # thirty ticks of 20 ms
        return job_id, loop.errors

    job_id, errors = run(db, scenario)
    claims, status, pause = row(db, job_id, "claims, status, extract(epoch FROM next_poll_at - clock_timestamp())")
    assert claims == 1, "the unsent job was claimed again on every tick (%d claims)" % claims
    assert (status, errors) == ("queued", 1) and 20 < pause <= 30


class DelayedApply:
    """Stand in for `store.apply_submit` with a stall before it starts (a pool that has no free connection)."""

    def __init__(self, monkeypatch, delay_s, before=None):
        self.original, self.delay_s, self.before, self.finished = store.apply_submit, delay_s, before, False
        monkeypatch.setattr(store, "apply_submit", self)

    async def __call__(self, *a, **kw):
        if self.before is not None:
            await self.before()
        await asyncio.sleep(self.delay_s)
        try:
            return await self.original(*a, **kw)
        finally:
            self.finished = True


@mutation(E, APPLY_IN_LEASE, APPLY_AFTER_LEASE, expect="the result that arrived before the timeout was dropped")
@mutation(C, MARGIN, NO_MARGIN, expect="the result that arrived before the timeout was dropped")
def test_a_result_that_arrives_just_before_the_submit_timeout_is_applied_even_when_the_application_starts_late(
        db, monkeypatch):
    """The lease is kept alive through the apply and its hard bound is the timeout plus a margin: a result received
    at 1 s of a 3 s timeout, applied 2.5 s later, still finds its lease."""
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, submit_timeout_s=3, script=[sent(ok(), delay_s=1.0)])
        stall = DelayedApply(monkeypatch, 2.5)
        loop = make_loop(p, engine, lease_ttl_s=1.5)       # a lease nobody holds would lapse within 1.5 s
        async with running(loop):
            await until(lambda: stall.finished, timeout_s=20, what="the application to end")
        return job_id, loop.errors

    job_id, errors = run(db, scenario)
    assert job_status(db, job_id) == "fetching", "the result that arrived before the timeout was dropped"
    assert errors == 0 and lease_count(db) == 0


@mutation(E, SHIELD, NO_SHIELD, expect="the apply was cancelled with its waiter")
@mutation(L, ROW_JUDGED, ROW_NOT_JUDGED, expect="the keeper must stop waiting for an apply whose lease expired")
def test_a_lease_lost_during_the_apply_drops_the_result_and_does_not_cancel_the_apply_itself(db, monkeypatch):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, script=[sent(ok(), delay_s=0.1)])
        stall = DelayedApply(monkeypatch, 2.0)
        loop = make_loop(p, engine)
        async with running(loop):
            await until(lambda: bool(loop._applying), what="the apply to be waiting")
            db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second'")   # the next beat cancels it
            await until(lambda: not loop._inflight, what="the job task to end")
            waiter_left_early = not stall.finished
            await asyncio.sleep(3.5)                       # the apply itself must still run to its end
        return job_id, stall.finished, loop.errors, waiter_left_early

    job_id, finished, errors, waiter_left_early = run(db, scenario)
    assert waiter_left_early, "the keeper must stop waiting for an apply whose lease expired"
    assert finished, "the apply was cancelled with its waiter"
    assert job_status(db, job_id) == "submitting" and errors == 0           # fenced: nothing was written


@mutation(E, DRAIN_WRITES, DRAIN_WRITES_FOREVER, expect="a blocked apply held the shutdown past its deadline")
def test_a_blocked_apply_cannot_hold_the_shutdown_past_the_deadline(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        await hold_job(p, k, engine, script=[sent(SubmitResult(Fail("render.watermarked_only", "no clean copy")))])
        loop = make_loop(p, engine, grace_s=0.6)
        gate = asyncio.Event()
        with Slow("job.fail.refund", gate=gate) as slow:
            task = asyncio.create_task(loop.run())
            try:
                await asyncio.wait_for(slow.reached.wait(), 5)         # the apply is blocked inside its transaction
                loop.stop()
                started = time.monotonic()
                try:
                    await asyncio.wait_for(asyncio.shield(task), 4)
                except TimeoutError:
                    pytest.fail("a blocked apply held the shutdown past its deadline")
                return time.monotonic() - started
            finally:
                gate.set()                                             # let the shielded write end
                await asyncio.wait_for(task, 10)

    took = run(db, scenario)
    assert took < 2.5


def test_a_hung_submit_before_the_send_goes_back_to_queued_with_no_attempt_and_runs_again(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, submit_timeout_s=1,
                                script=[unsent(SubmitResult(Accepted("x")), delay_s=30), sent(ok())])
        async with running(make_loop(p, engine)):
            await until(in_status(db, job_id, "fetching"), timeout_s=8, what="the second run to finish")
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "attempts, claims, sent_at IS NOT NULL") == (0, 2, True)
    assert "interrupted before the send" in [r[0] for r in db.all("SELECT text FROM job_events WHERE job_id = %s",
                                                                  job_id)]


def test_an_unexpected_exception_ends_unknown_after_the_send_and_queued_before_it(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        after = await hold_job(p, k, engine, script=[sent(RuntimeError("boom"))], prompt="after")
        loop = make_loop(p, engine, max_errors=50)
        async with running(loop):
            await until(in_status(db, after, "unknown"), what="the job to be unknown")
            before = await hold_job(p, k, engine, script=[unsent(RuntimeError("boom")), sent(ok())], prompt="before")
            await until(in_status(db, before, "fetching"), what="the retried job to finish")
        return after, before, loop.errors

    after, before, errors = run(db, scenario)
    assert row(db, after, "status, attempts, sent_at IS NOT NULL") == ("unknown", 0, True)
    assert row(db, before, "attempts, claims") == (0, 2)
    assert errors == 0                                  # the finished retry cleared the count


# ---- the result is applied outside the timeout and shielded ------------------------------------------------------
class Slow:
    """Arm a failpoint that waits: the application is slow while the lease and the timeout are still ticking."""

    def __init__(self, name, seconds=0.0, gate: asyncio.Event | None = None):
        self.name, self.seconds, self.gate, self.reached = name, seconds, gate, asyncio.Event()

    async def _wait(self):
        self.reached.set()
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(self.seconds)

    def __enter__(self):
        failpoints.arm(self.name, self._wait)
        return self

    def __exit__(self, *_):
        failpoints.disarm(self.name)


@mutation(E, APPLY_IN_TIMEOUT, APPLY_IN_TIMEOUT_MUT,
          expect="the timeout fired during the application: it must not cover the apply")
def test_the_result_is_applied_outside_the_timeout_even_when_the_application_outlasts_it(db, caplog):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, submit_timeout_s=1,
                                script=[sent(SubmitResult(Fail("render.watermarked_only", "no clean copy")))])
        loop = make_loop(p, engine)
        with Slow("job.fail.refund", seconds=1.4):       # applying takes longer than the 1 s timeout
            async with running(loop):
                await until(in_status(db, job_id, "failed"), timeout_s=8, what="the failure to be applied")
        return job_id, loop.errors

    job_id, errors = run(db, scenario)
    assert job_status(db, job_id) == "failed"
    assert errors == 0
    assert not [r for r in caplog.records if "ran past" in r.getMessage()], \
        "the timeout fired during the application: it must not cover the apply"
    assert lease_count(db) == 0


@mutation(E, SHIELD, NO_SHIELD, expect="the received result must be applied, not rolled back")
def test_a_cancellation_during_the_application_cannot_lose_the_result(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine,
                                script=[sent(SubmitResult(Fail("render.watermarked_only", "no clean copy")))])
        loop = make_loop(p, engine, grace_s=5.0)         # the in-flight task is cancelled 2.5 s after stop()
        gate = asyncio.Event()
        with Slow("job.fail.refund", gate=gate) as slow:
            task = asyncio.create_task(loop.run())
            await asyncio.wait_for(slow.reached.wait(), 5)       # the failure is half applied: inside its transaction
            loop.stop()
            await asyncio.sleep(3.75)                            # cancelled, and still inside the shutdown deadline
            gate.set()
            code = await asyncio.wait_for(task, 10)
        return job_id, code

    job_id, code = run(db, scenario)
    assert code == 0 and job_status(db, job_id) == "failed", "the received result must be applied, not rolled back"
    assert lease_count(db) == 0


# ---- loud failure ------------------------------------------------------------------------------------------------
@mutation(E, CHECKED, NEVER_CHECKED, expect="timed out waiting for the worker to exit 1 after five errors")
def test_five_consecutive_unexpected_errors_stop_the_worker_with_a_failure_and_leave_the_alert_row(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        await hold_job(p, k, engine, script=[unsent(RuntimeError("broken engine"))])     # the last entry repeats
        loop = make_loop(p, engine, max_inflight=1)         # one submit at a time: no sixth error can overlap the fifth
        return await within(loop.run(), 10, what="the worker to exit 1 after five errors"), loop.errors

    code, errors = run(db, scenario)
    assert (code, errors) == (1, 5)
    assert db.one("SELECT consecutive_errors FROM worker_heartbeats WHERE worker_id = 'w1'") == (5,)


@mutation(E, RESET, NO_RESET, expect="timed out waiting for both jobs to finish through the errors")
def test_a_success_resets_the_error_count(db):
    k = key(db)
    account(db)

    async def scenario(p):
        engine = FakeEngine()
        bad, good = unsent(RuntimeError("flaky")), sent(ok())
        engine.script_submit("*", bad, bad, bad, bad, good, bad, bad, bad, bad, good)    # never 5 in a row
        first, second = await create(p, k, "one", completion="hold"), await create(p, k, "two", completion="hold")
        loop = make_loop(p, engine)
        async with running(loop) as task:
            await until(lambda: job_status(db, first.job_id) == job_status(db, second.job_id) == "fetching",
                        what="both jobs to finish through the errors")
            alive = not task.done()
        return alive, loop.errors

    alive, errors = run(db, scenario)
    assert alive and errors == 0, "eight errors in all, never five in a row: the worker keeps running"


def test_a_tick_that_fails_counts_and_a_clean_idle_tick_resets(db):
    class Flaky:
        """A settings view whose reads fail a few times: the claim tick raises, the loop survives."""
        def __init__(self, inner, fails):
            self.inner, self.fails, self.loop, self.seen = inner, fails, None, []

        def get(self, name):
            if self.fails > 0 and name.endswith("claims_enabled"):
                self.fails -= 1
                self.seen.append(self.loop.errors)
                raise RuntimeError("settings unavailable")
            return self.inner.get(name)

    async def scenario(p):
        settings = Flaky(FakeSettings(SPEC), 4)
        loop = settings.loop = make_loop(p, settings=settings)
        async with running(loop) as task:
            await until(lambda: settings.fails == 0, what="the failures to be used up")
            await until(lambda: loop.errors == 0, what="a clean tick to reset the count")
            return settings.seen, not task.done()

    seen, alive = run(db, scenario)
    assert seen == [0, 1, 2, 3] and alive          # four failed ticks counted one by one; the loop survived


# ---- a browser provider: the token and the live slot setting -----------------------------------------------
class Peak:
    """A scripted submit that records how many run at once."""

    def __init__(self, hold_s=1.2):
        self.now = self.peak = 0
        self.hold_s = hold_s

    async def __call__(self, ctx, job):
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(self.hold_s)
        finally:
            self.now -= 1
        return ok()


@mutation(E, RESIZE, "pass", expect="assert [4, 4] == [2, 5]")
def test_a_browser_budget_follows_the_live_slot_setting_at_every_tick(db):
    spec = FakeSpec("fake", execution="browser", account_slots=1)

    async def scenario(p):
        settings = FakeSettings(spec, fake__browser_slots=2)
        budget = BrowserBudget(4)
        loop = make_loop(p, spec=spec, settings=settings, budget=budget)
        sizes = []
        await loop.tick()
        sizes.append(budget.size)
        settings.set("fake.browser_slots", 5)
        await loop.tick()
        sizes.append(budget.size)
        return sizes

    assert run(db, scenario) == [2, 5]


def test_a_browser_provider_needs_a_budget(db):
    async def scenario(p):
        with pytest.raises(ValueError, match="budget"):
            make_loop(p, spec=FakeSpec("fake", execution="browser"))

    run(db, scenario)


@mutation(E, "conn.execute(\"UPDATE worker_heartbeats SET started_at = clock_timestamp() WHERE worker_id = %s\",",
          "conn.execute(\"DELETE FROM worker_heartbeats WHERE worker_id = %s\",",
          expect="the row stays and its start time is the new run's")
def test_a_restart_keeps_the_heartbeat_row_in_place_so_the_worker_never_looks_missing(db):
    """Restarting deletes nothing: between registering and the first beat a monitor still sees the row (a delete
    then a later insert left a gap, seen under load)."""
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) "
           "VALUES ('w1', 'fake', now() - interval '2 hours', now() - interval '1 hour')")

    async def scenario(p):
        loop = make_loop(p)

        async def no_beat() -> None:                      # stop right after registering, before the first beat
            pass
        loop.beat = no_beat
        await loop._register()

    run(db, scenario)
    assert db.one("SELECT started_at > now() - interval '1 minute' FROM worker_heartbeats WHERE worker_id = 'w1'") \
        == (True,), "the row stays and its start time is the new run's"
    db.run("DELETE FROM worker_heartbeats")


def test_a_restart_after_an_abnormal_exit_starts_the_budget_on_a_ramp_and_a_clean_stop_leaves_no_row(db):
    spec = FakeSpec("fake", execution="browser")

    async def scenario(p):
        budget = BrowserBudget(8)
        wide = FakeSettings(spec, fake__browser_slots=8)
        loop = make_loop(p, spec=spec, settings=wide, budget=budget)
        full = budget.submit_cap()
        db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) "      # an earlier run that
               "VALUES ('w1', 'fake', now(), now() - interval '1 hour')")                   # never cleaned up
        async with running(loop):
            await until(lambda: db.one("SELECT at > now() - interval '1 minute' FROM worker_heartbeats")[0],
                        what="the new run to write its row")
            ramped = budget.submit_cap()
        fresh = BrowserBudget(8)
        async with running(make_loop(p, spec=spec, settings=wide, budget=fresh)):
            await asyncio.sleep(0.1)
        return full, ramped, fresh.submit_cap()

    full, ramped, after_clean = run(db, scenario)
    assert (full, ramped, after_clean) == (5, 2, 5)
    assert db.one("SELECT count(*) FROM worker_heartbeats") == (0,)


# ---- heartbeat row and loop-lag watchdog ----------------------------------------------------------
def test_the_heartbeat_row_reports_busy_slots_errors_and_loop_lag_and_a_blocked_loop_is_logged(db, caplog):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        # The submit outlasts the whole check (a short one could finish before the lagged row is read under load);
        # the short grace cancels it at the end, so the test does not wait for it.
        await hold_job(p, k, engine, script=[sent(SubmitResult(Accepted("r")), delay_s=10)])
        loop = make_loop(p, engine, lag_warn_s=0.1, grace_s=0.2)
        row = []
        async with running(loop):
            await until(lambda: loop._inflight, what="a job in flight")
            await asyncio.sleep(0.1)
            time.sleep(0.4)                                   # a blocking call on the loop

            def lagged() -> bool:
                row[:] = db.one("SELECT loop_lag_ms, slots_busy FROM worker_heartbeats")
                return row[0] >= 250

            await until(lagged, what="the lag in the heartbeat row")
        return row[1]                                         # busy slots of the same row that shows the lag

    with caplog.at_level(logging.WARNING, logger="relay_core.jobs.engine"):
        busy = run(db, scenario)
    assert busy == 1
    assert any("event loop lag" in r.message for r in caplog.records), "a lag over the limit must be logged"


# ---- the RunCtx and the account view -----------------------------------------------------------------------------
def test_the_ctx_reports_the_ref_and_events_through_the_lease_and_redacts_what_it_stores(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()

        async def step(ctx, job):
            await ctx.mark_sent()
            await ctx.fence()
            await ctx.report_ref("ref-1", {"conversation": "c1"})
            await ctx.event("upstream said %s" % SECRET, "reply", "upstream")
            with pytest.raises(ValueError):
                await ctx.event("x", "reply", "system")           # an engine cannot write a platform conclusion
            return SubmitResult(Accepted("ref-1"))

        job_id = await hold_job(p, k, engine, script=[sent(step)])
        async with running(make_loop(p, engine)):
            await until(in_status(db, job_id, "waiting"), what="the job to be waiting")
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "external_ref, provider_state ->> 'conversation'") == ("ref-1", "c1")
    texts = [r[0] for r in db.all("SELECT text FROM job_events WHERE job_id = %s AND source = 'upstream'", job_id)]
    assert len(texts) == 1 and SECRET not in texts[0]


def test_the_account_view_opens_the_sealed_credential_and_proxy_with_the_workers_key(db):
    pair = crypto.generate_keypair()
    other = crypto.generate_keypair()
    acct = account(db, left=3)
    db.run("UPDATE accounts SET credential_enc = %s, proxy_host = %s, proxy_port = 8080, proxy_auth_enc = %s, "
           "label = %s, provider_state = %s::jsonb WHERE id = %s",
           crypto.seal(pair.public, b"cookie=1"), "h", crypto.seal(pair.public, b"user:pw"), "main",
           json.dumps({"composer": 1}), acct)

    async def scenario(p):
        view = await load_account_view(p, acct, [other.private, pair.private], POOLS)
        with pytest.raises(crypto.CryptoError):
            await load_account_view(p, acct, [other.private], POOLS)
        return view

    view = run(db, scenario)
    assert (view.label, view.credential, view.credential_gen) == ("main", b"cookie=1", 1)
    assert (view.proxy.host, view.proxy.port, view.proxy.auth) == ("h", 8080, "user:pw")
    assert view.provider_state == {"composer": 1} and view.capacity == {"video": 3, "image": None}


RUNCTX = "packages/relay-core/src/relay_core/jobs/runctx.py"
CAS = '"WHERE id = %s AND credential_gen = %s AND credential_enc = %s",\n'


def merge_json(stored: bytes, mine: bytes) -> bytes:
    """A provider's merge: every key of both, the newer `at` winning."""
    a, b = json.loads(stored), json.loads(mine)
    old, new = (a, b) if a["at"] <= b["at"] else (b, a)
    return json.dumps({**old, **new}, sort_keys=True).encode()


def sealed_account(db, plaintext: bytes):
    pair, other = crypto.generate_keypair(), crypto.generate_keypair()
    acct = account(db)
    db.run("UPDATE accounts SET credential_enc = %s WHERE id = %s", crypto.seal(pair.public, plaintext), acct)
    return pair, other, acct


def stored(db, acct, pair) -> tuple[bytes, int]:
    sealed, gen = db.one("SELECT credential_enc, credential_gen FROM accounts WHERE id = %s", acct)
    return crypto.open_blob(bytes(sealed), [pair.private]), gen


@mutation(RUNCTX, '"UPDATE accounts SET credential_enc = %s, updated_at',
          '"UPDATE accounts SET credential_enc = %s, credential_gen = credential_gen + 1, updated_at',
          expect="a renewal changed the generation")
@mutation(RUNCTX, "capacity, crypto.sealing_key(sealed, keys), sealed,",
          "capacity, crypto.generate_keypair().public, sealed,",
          expect="the renewed credential is not sealed for the worker's key")
@mutation(RUNCTX, "                self.sealed, self.plain = sealed, blob\n",
          "                self.plain = blob\n", expect="a second renewal of one run was refused")
@mutation(RUNCTX, "                self.sealed, self.plain = sealed, blob\n",
          "                self.sealed = sealed\n", expect="the run's own newer write was undone")
def test_a_renewed_credential_replaces_the_sealed_one_with_the_same_key_and_generation(db):
    """The one credential push (a provider's cookie rotation): sealed with the key that sealed the account's
    credential, never bumping the generation; a run may renew twice (rotation, then the cookies of its page loads),
    and a later write of an older copy is merged with the run's own newer write, never undoing it."""
    pair, other, acct = sealed_account(db, b'{"at": 1}')

    async def scenario(p):
        view = await load_account_view(p, acct, [other.private, pair.private], {})
        renewal = Renewal(p, view)
        first, second = await renewal(b'{"at": 2}'), await renewal(b'{"at": 3}')
        newer = await renewal(b'{"at": 10, "k": "x"}', merge_json)
        older = await renewal(b'{"at": 4, "p": "page"}', merge_json)
        return view, first, second, newer, older

    view, first, second, newer, older = run(db, scenario)
    assert view.seal_key == pair.public, "the renewed credential is not sealed for the worker's key"
    got, gen = stored(db, acct, pair)
    assert gen == 1, "a renewal changed the generation"
    assert first and second and newer and older, "a second renewal of one run was refused"
    assert json.loads(got) == {"at": 10, "k": "x", "p": "page"}, "the run's own newer write was undone: %s" % got
    assert older == got


ALWAYS_MERGE = ("            if merge is not None and self.plain is not None:\n"
                "                blob = merge(self.plain, blob)")
MERGE_ON_RETRY_ONLY = ("            if attempt and merge is not None and self.plain is not None:\n"
                       "                blob = merge(self.plain, blob)")


@mutation(RUNCTX, CAS, '"WHERE id = %s AND credential_gen = %s AND %s IS NOT NULL",\n',
          expect="the later renewal regressed the newer session")
@mutation(RUNCTX, ALWAYS_MERGE, MERGE_ON_RETRY_ONLY,
          expect="the stale copy regressed the newer session")
@mutation(RUNCTX, "            self.sealed, self.key, self.plain = fresh.sealed, fresh.seal_key, fresh.credential",
          "            self.sealed, self.key = fresh.sealed, fresh.seal_key",
          expect="the later renewal regressed the newer session")
@mutation(RUNCTX, "        for attempt in range(2):", "        for attempt in range(1):",
          expect="the merge was not written")
def test_two_runs_that_read_one_generation_never_lose_a_rotation_even_when_the_later_writes_its_stale_copy_again(db):
    """Two runs load generation 1; A renews; B's swap misses (the ciphertext moved), so B reads the account again and
    writes merge(stored, mine). B then writes its own stale copy once more (a page load refreshed a cookie): that write
    too is merged with what B knows is stored, so A's newer rotation stays."""
    pair, other, acct = sealed_account(db, b'{"a": "old", "at": 1, "b": "old", "c": "old"}')

    async def scenario(p):
        one = await load_account_view(p, acct, [pair.private], {})
        two = await load_account_view(p, acct, [pair.private], {})
        first = await Renewal(p, one)(b'{"a": "rotated", "at": 5, "b": "old", "c": "old"}')
        b = Renewal(p, two)
        second = await b(b'{"a": "old", "at": 3, "b": "old", "c": "page", "d": "new"}', merge_json)
        mid, _ = stored(db, acct, pair)
        third = await b(b'{"a": "old", "at": 3, "b": "page-again", "c": "page", "d": "new"}', merge_json)
        return first, second, mid, third

    first, second, mid, third = run(db, scenario)
    assert first and second and third, "the merge was not written"
    assert json.loads(mid) == {"a": "rotated", "at": 5, "b": "old", "c": "old", "d": "new"}, \
        "the later renewal regressed the newer session: %s" % mid
    got, gen = stored(db, acct, pair)
    assert json.loads(got)["a"] == "rotated", "the stale copy regressed the newer session: %s" % got
    assert third == got and gen == 1, "the call returns exactly what is stored"


def test_concurrent_renewals_never_regress_and_without_a_merge_a_lost_swap_writes_nothing(db):
    pair, other, acct = sealed_account(db, b'{"at": 1}')

    async def concurrent(p):
        views = [await load_account_view(p, acct, [pair.private], {}) for _ in range(3)]
        return await asyncio.gather(*(Renewal(p, v)(b'{"at": %d}' % (10 + i), merge_json)
                                      for i, v in enumerate(views)))

    results = run(db, concurrent)
    written = [10 + i for i, ok in enumerate(results) if ok]
    assert written and json.loads(stored(db, acct, pair)[0])["at"] >= max(written), \
        "a concurrent renewal regressed the newest session"

    async def no_merge(p):
        old = await load_account_view(p, acct, [pair.private], {})
        assert await Renewal(p, await load_account_view(p, acct, [pair.private], {}))(b'{"at": 20}')
        return await Renewal(p, old)(b'{"at": 13}')

    assert run(db, no_merge) is None and json.loads(stored(db, acct, pair)[0])["at"] == 20

    async def stale(p):        # a run that read generation 1, while a re-import moved the account to generation 2
        old = await load_account_view(p, acct, [pair.private], {})
        async with tx(p) as conn:
            await conn.execute("UPDATE accounts SET credential_enc = %s, credential_gen = 2 WHERE id = %s",
                               (crypto.seal(pair.public, b'{"at": 0}'), acct))
        return await Renewal(p, old)(b'{"at": 30}', merge_json)

    assert run(db, stale) is None, "a credential imported meanwhile was overwritten"
    assert stored(db, acct, pair) == (b'{"at": 0}', 2), "a credential imported meanwhile was overwritten"


class BeatAfterCommit:
    """Stand in for `store.apply_submit`: the write commits (it deletes the lease), then one lease heartbeat runs and
    the commit acknowledgement is processed after it, the order a beat blocked on the fence's row lock gives."""

    def __init__(self, monkeypatch):
        self.original, self.loop = store.apply_submit, None
        monkeypatch.setattr(store, "apply_submit", self)

    async def __call__(self, *a, **kw):
        status = await self.original(*a, **kw)
        await self.loop._keeper.beat()
        await asyncio.sleep(0)
        return status


@mutation(E, SETTLE, NO_SETTLE, expect="the success did not reset the error count")
def test_a_heartbeat_between_the_commit_and_the_return_of_the_apply_does_not_hide_the_success(
        db, monkeypatch, caplog):
    k, _ = key(db), account(db)

    async def scenario(p):
        beat = BeatAfterCommit(monkeypatch)
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, script=[sent(ok())])
        beat.loop = loop = make_loop(p, engine)
        loop._job_errors = 3
        with caplog.at_level(logging.INFO):
            async with running(loop):
                await until(in_status(db, job_id, "fetching"), what="the job to be applied")
                await asyncio.sleep(0.3)
        return job_id, loop.errors

    job_id, errors = run(db, scenario)
    assert errors == 0, "the success did not reset the error count"
    assert not [r for r in caplog.records if "lost" in r.getMessage()], "an applied job was logged as a lost lease"
    assert lease_count(db) == 0


@mutation(A, FENCE, NO_FENCE, expect="a result whose lease row was deleted must be dropped")
def test_a_lease_row_deleted_before_the_apply_still_drops_the_result(db, monkeypatch, caplog):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, script=[sent(ok(), delay_s=0.1)])
        stall = DelayedApply(monkeypatch, 1.5)
        loop = make_loop(p, engine)
        with caplog.at_level(logging.INFO):
            async with running(loop):
                await until(lambda: bool(loop._applying), what="the apply to be waiting")
                db.run("DELETE FROM leases")                   # the reaper took it, after the result came
                await until(lambda: stall.finished, what="the application to end")
        return job_id, loop.errors

    job_id, errors = run(db, scenario)
    assert job_status(db, job_id) == "submitting", "a result whose lease row was deleted must be dropped"
    assert errors == 0 and [r for r in caplog.records if "result dropped" in r.getMessage()]
    db.run("UPDATE jobs SET status = 'unknown', next_poll_at = clock_timestamp() WHERE id = %s", job_id)  # reaper


# ---- a lease the heartbeat could not extend (I2, I14) ------------------------------------------------------------
@mutation(E, EXPIRED_OK, EXPIRED_NOT_OK, expect="timed out waiting for the cancelled submit to be resolved")
def test_a_lease_that_expired_under_a_sent_job_cancels_the_submit_and_the_job_ends_unknown_never_requeued(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, script=[sent(SubmitResult(Accepted("never")), delay_s=30)])
        async with running(make_loop(p, engine)):
            await until(lambda: lease_count(db) == 1 and row(db, job_id, "sent_at IS NOT NULL")[0], what="a sent job")
            db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second'")     # a stalled heartbeat
            await until(in_status(db, job_id, "unknown"), what="the cancelled submit to be resolved")
        return job_id, engine

    job_id, engine = run(db, scenario)
    assert [c for c in engine.calls if c[0] == "submit"] == [("submit", str(job_id))] and lease_count(db) == 0


def test_when_the_lease_is_gone_the_submit_is_cancelled_the_result_is_dropped_and_the_reaper_owns_the_job(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine = FakeEngine()
        job_id = await hold_job(p, k, engine, script=[sent(SubmitResult(Accepted("lost")), delay_s=0.6)])
        loop = make_loop(p, engine)
        async with running(loop):
            await until(lambda: lease_count(db) == 1 and row(db, job_id, "sent_at IS NOT NULL")[0], what="a sent job")
            db.run("DELETE FROM leases")                                  # the reaper took it
            await until(lambda: not loop._inflight, what="the task to end")
        return job_id, loop.errors

    job_id, errors = run(db, scenario)
    assert row(db, job_id, "status, external_ref") == ("submitting", None), "nothing written: the reaper decides"
    assert errors == 0


CONTAINER = {"read": True, "cpus": 1.5, "memory_bytes": 12 * 1024**3, "pids": 2048, "shm_bytes": 1024**3,
             "boot": {"WORKER_ID": "w1"}}


@mutation(E, LIMITS_INSERT, LIMITS_NULL_INSERT, expect="assert None == {")
@mutation("packages/relay-core/src/relay_core/db/migrations/0019_worker_limits.sql", "ADD COLUMN limits jsonb",
          "ADD COLUMN limits_x jsonb", expect='column "limits" of relation "worker_heartbeats" does not exist')
def test_a_fresh_heartbeat_row_carries_the_container_limits(db):
    async def scenario(p):
        await make_loop(p, container=CONTAINER).beat()

    run(db, scenario)
    assert db.one("SELECT limits FROM worker_heartbeats WHERE worker_id = 'w1'")[0] == CONTAINER
    db.run("DELETE FROM worker_heartbeats")


@mutation(E, LIMITS_UPDATE, "", expect="assert None == {")
def test_a_row_left_by_an_older_worker_gets_its_limits_at_the_next_beat(db):
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES ('w1', 'fake', now(), now())")
    assert db.one("SELECT limits FROM worker_heartbeats")[0] is None, "premise: an older worker wrote no limits"

    async def scenario(p):
        await make_loop(p, container=CONTAINER).beat()

    run(db, scenario)
    assert db.one("SELECT limits FROM worker_heartbeats WHERE worker_id = 'w1'")[0] == CONTAINER
    db.run("DELETE FROM worker_heartbeats")

