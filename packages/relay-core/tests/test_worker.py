"""The worker process (contracts 3, 7.7, 15; invariants I1, I14): every loop of a provider runs under one stop signal,
shutdown goes in order and leaves no lease that a job still depends on, a different BILLING_TZ refuses to start, and
the claim loop's error limit is the process's exit code."""

import asyncio
import os
import signal
from datetime import UTC, datetime, timedelta

import pytest
from accountkit import minio_settings, new_command, object_store
from enginekit import until, within
from jobkit import POOLS, SPEC, account, create, key
from lifecyclekit import MediaEngine, load_account

from relay_core import crypto, failpoints
from relay_core.db import open_pool, tx
from relay_core.jobs import ops
from relay_core.jobs import worker as worker_module
from relay_core.jobs.worker import Worker, serve
from relay_core.providers import Accepted, Ambiguous, Found, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, FakeSpec, sent
from relay_core.testing.mutation import mutation

W = "packages/relay-core/src/relay_core/jobs/worker.py"
R = "packages/relay-core/src/relay_core/jobs/reaper.py"
TIMINGS = dict(tick_s=0.02, lease_beat_s=0.1, beat_s=0.05, reaper_interval_s=0.05, housekeeping_s=0.2, grace_s=5.0)


@pytest.fixture
def objects():
    with object_store() as s:
        yield s


def make_worker(p, engine, objects, *, settings=None, **kw) -> Worker:
    return Worker(p, SPEC, engine, settings or FakeSettings(SPEC), owner="w1", load_account=load_account,
                  store=objects, **{**TIMINGS, **kw})


def run_as_worker(db, scenario):
    """`scenario(owner_pool, worker_pool)`: jobs are made with the owner role, the worker runs as `relay_worker`."""
    async def main():
        owner, wp = await open_pool(db.dsn), await open_pool(db.worker_dsn)
        try:
            return await scenario(owner, wp)
        finally:
            await asyncio.gather(owner.close(), wp.close())

    return asyncio.run(main())


def nudge(pool, job_id):
    """A predicate for `until`: makes the job's next poll or fetch due, true once it is done. It runs on the loop (a
    blocking write from the test thread would wait for a row lock the loop itself holds). A job that a loop has
    claimed (it holds a `poll_token`) is left alone: re-arming it every 20 ms hands the next claim a new token, and a
    fetch that takes longer than that (about 100 ms with the probe and the object store) is dropped every time."""
    async def check() -> bool:
        async with tx(pool) as conn:
            await conn.execute("UPDATE jobs SET next_poll_at = clock_timestamp() - interval '1 second' "
                               "WHERE id = %s AND status IN ('waiting', 'fetching', 'unknown') "
                               "AND poll_token IS NULL", (job_id,))
            row = await (await conn.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))).fetchone()
        return row[0] == "done"
    return check


# ---- every loop runs -----------------------------------------------------------------------------------------------
def test_a_job_goes_through_every_loop_of_the_composed_worker_to_done(db, objects):
    k = key(db)
    account(db, left=100)
    stale = account(db, checked=False)
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES "
           "(%s, 'check', 'gone', clock_timestamp() - interval '1 minute', clock_timestamp() - interval '30 s')", stale)

    async def scenario(owner, wp):
        engine = MediaEngine()
        engine.script_submit("two", sent(SubmitResult(Ambiguous("no answer"))))
        engine.script_reconcile("two", Found("ref-two"))
        one = (await create(owner, k, "one", completion="detach")).job_id
        two = (await create(owner, k, "two", completion="detach")).job_id
        worker = make_worker(wp, engine, objects)
        task = asyncio.create_task(worker.run())
        try:
            first, second = nudge(owner, one), nudge(owner, two)

            async def both() -> bool:
                return [await first(), await second()] == [True, True]
            await until(both, timeout_s=30, what="both jobs done")
        finally:
            worker.stop()
            code = await asyncio.wait_for(task, 20)
        return one, two, engine, code

    one, two, engine, code = run_as_worker(db, scenario)
    done = {c for c, _ in engine.calls}
    assert code == 0 and {"submit", "poll", "reconcile"} <= done               # claim, poller, reconciler
    for job in (one, two):
        assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND role = 'output'", job)[0] == 1  # fetcher
    assert db.one("SELECT check_due_at IS NOT NULL FROM accounts WHERE id = %s", stale)[0]       # the check ran
    assert db.one("SELECT count(*) FROM leases WHERE owner = 'gone'")[0] == 0                    # the reaper ran
    assert db.one("SELECT count(*) FROM leases")[0] == 0 and db.one("SELECT count(*) FROM worker_heartbeats")[0] == 0


# ---- stop signal -----------------------------------------------------------------------------------------------------
@mutation(W, "        self._stop_commands.set()\n", "        self._stop_commands.set()\n        self.fetcher.stop()\n",
          expect="assert code == 0 and set(during) <= {\"commands\"} and order[-1] == \"release\"")
@mutation(W, "WHERE l.owner = %s AND NOT EXISTS", "WHERE l.owner <> %s AND NOT EXISTS",
          expect="assert [r[0] for r in db.all(\"SELECT id FROM leases\")] == [job_lease]")
@mutation(W, "AND j.status = 'submitting') RETURNING l.id\"", "AND j.status = 'none') RETURNING l.id\"",
          expect="assert [r[0] for r in db.all(\"SELECT id FROM leases\")] == [job_lease]")
def test_sigterm_stops_claims_first_then_the_other_loops_then_releases_leases_but_not_a_submitting_jobs(db, objects):
    k = key(db)
    account(db, left=100)
    other, third = account(db, checked=False), account(db, checked=False)
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES "
           "(%s, 'check', 'w1', clock_timestamp() + interval '1 hour', clock_timestamp() + interval '2 hours')", other)
    submitting = db.job(k, provider="fake")
    db.run("UPDATE jobs SET status = 'submitting', claims = 1, account_id = %s WHERE id = %s", third, submitting)
    job_lease = db.one("INSERT INTO leases (account_id, job_id, purpose, owner, until, hard_until) VALUES "
                       "(%s, %s, 'job', 'w1', clock_timestamp() + interval '1 hour', "
                       "clock_timestamp() + interval '2 hours') RETURNING id", third, submitting)[0]

    async def scenario(owner, wp):
        started, release = asyncio.Event(), asyncio.Event()

        async def gate(ctx, job):
            started.set()
            await release.wait()
            return SubmitResult(Accepted("ref-gate"))

        engine = FakeEngine()
        engine.script_submit("*", sent(gate))
        job = (await create(owner, k, "one", completion="detach")).job_id
        worker = make_worker(wp, engine, objects)
        order: list[str] = []

        def trace(name, fn):
            async def wrapper(*a, **kw):
                try:
                    return await fn(*a, **kw)
                finally:
                    order.append(name)
            return wrapper

        for name in ("claims", "poller", "fetcher", "reconciler", "reaper", "commands"):
            loop_ = getattr(worker, name)
            loop_.run = trace(name, loop_.run)
        worker._release = trace("release", worker._release)
        task = asyncio.create_task(worker.run(install_signals=True))
        await asyncio.wait_for(started.wait(), 10)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.5)
        during = list(order)                           # the submit is still open: only claims-side loops may be over
        release.set()
        code = await asyncio.wait_for(task, 20)
        return job, during, order, code

    job, during, order, code = run_as_worker(db, scenario)
    assert code == 0 and set(during) <= {"commands"} and order[-1] == "release"
    assert order.index("claims") < min(order.index(n) for n in ("poller", "fetcher", "reconciler", "reaper"))
    assert db.one("SELECT status FROM jobs WHERE id = %s", job)[0] == "waiting"        # the submit finished and applied
    assert [r[0] for r in db.all("SELECT id FROM leases")] == [job_lease]               # only the submitting job's
    assert db.one("SELECT count(*) FROM worker_heartbeats")[0] == 0


# ---- a stop leaves no command running, and has one deadline --------------------------------------------------------
FAIL_IDS = "                if ids:\n                    failed ="
FAIL_NONE = "                if False:\n                    failed ="


@mutation(W, FAIL_IDS, FAIL_NONE, expect="the admin would see this command running forever")
def test_a_graceful_stop_fails_the_command_that_was_running_under_a_released_lease(db, objects):
    account(db, left=100)
    held = account(db, checked=False)
    lease_id = db.one("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES "
                      "(%s, 'command', 'w1', clock_timestamp() + interval '1 hour', "
                      "clock_timestamp() + interval '2 hours') RETURNING id", held)[0]
    command = db.one("INSERT INTO commands (provider, kind, account_id, status, lease_id, started_at) VALUES "
                     "('fake', 'recover', %s, 'running', %s, clock_timestamp()) RETURNING id", held, lease_id)[0]

    async def scenario(owner, wp):
        worker = make_worker(wp, FakeEngine(), objects)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        worker.stop()
        return await asyncio.wait_for(task, 20)

    assert run_as_worker(db, scenario) == 0
    status, result = db.one("SELECT status, result FROM commands WHERE id = %s", command)
    assert status == "failed" and "stopped" in result["error"], "the admin would see this command running forever"
    assert db.one("SELECT count(*) FROM leases")[0] == 0


@mutation(W, "return None if deadline is None else max(0.0, deadline - loop.time())", "return self.grace_s",
          expect="the stop took")
def test_the_whole_stop_has_one_deadline_not_one_grace_per_stage(db, objects):
    """A command and the reaper both ignore the stop: with a grace of 3 s the stop ends near 3 s, not 6 s."""
    async def scenario(owner, wp):
        worker = make_worker(wp, FakeEngine(), objects, grace_s=3.0)

        async def deaf(*_):
            await asyncio.sleep(30)
        worker.commands.run = deaf
        worker.reaper.run = deaf
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        started = asyncio.get_running_loop().time()
        worker.stop()
        code = await asyncio.wait_for(task, 20)
        return code, asyncio.get_running_loop().time() - started

    _, took = run_as_worker(db, scenario)
    assert 2.9 < took < 5.0, "the stop took %.2f s for a 3 s grace" % took


@mutation(W, 'self._wait(tasks["claims"], remaining())', 'self._wait(tasks["claims"], None)',
          expect="the claim loop held the shutdown past its deadline")
def test_a_claim_loop_that_outlasts_the_shutdown_deadline_is_cancelled_with_the_other_loops(db, objects):
    """The claim loop has its own drain, which a blocked write can hold. The worker's one deadline covers it too: at
    the deadline the loop is cancelled (the shielded write may finish on its own; the reaper moves what it left)."""
    async def scenario(owner, wp):
        worker = make_worker(wp, FakeEngine(), objects, grace_s=0.8)

        async def stuck():
            await asyncio.sleep(3600)
        worker.claims.run = stuck
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        started = asyncio.get_running_loop().time()
        worker.stop()
        try:
            try:
                await asyncio.wait_for(asyncio.shield(task), 4)
            except TimeoutError:
                pytest.fail("the claim loop held the shutdown past its deadline")
            return asyncio.get_running_loop().time() - started
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    took = run_as_worker(db, scenario)
    assert took < 2.0, "the stop took %.2f s for a 0.8 s grace" % took


DRIFT_STEP = '("visibility", self._visibility), ("pool_drift", self._pool_drift)'
@mutation(R, DRIFT_STEP, '("visibility", self._visibility)',
          expect="timed out waiting for the drift pass to correct the counter")
def test_the_running_worker_corrects_a_planted_pool_drift(db, objects):
    acct = account(db, left=None)
    db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) VALUES "
           "(%s, 'video', 0, 'counted', (clock_timestamp() AT TIME ZONE 'UTC')::date)", acct)
    budget = POOLS["video"].budget

    def counter() -> int:                       # the video row: a check of the account also adds the image row
        return db.one("SELECT left_n FROM account_capacity WHERE account_id = %s AND pool = 'video'", acct)[0]

    async def scenario(owner, wp):
        worker = make_worker(wp, FakeEngine(), objects, drift_check_s=0.2)
        task = asyncio.create_task(worker.run())
        try:
            await until(lambda: counter() == budget, timeout_s=10, what="the drift pass to correct the counter")
            async with tx(owner) as conn:                                                  # drifts again
                await conn.execute("UPDATE account_capacity SET left_n = 1 WHERE account_id = %s AND pool = 'video'",
                                   (acct,))
            await until(lambda: counter() == budget, timeout_s=10, what="the next drift pass")
        finally:
            worker.stop()
            await asyncio.wait_for(task, 20)

    run_as_worker(db, scenario)


# ---- start and exit codes -----------------------------------------------------------------------------------------
@mutation(W, "            await self._verify_billing()\n        except BillingTzMismatch", "            pass\n"
          "        except BillingTzMismatch",
          expect="timed out waiting for the worker to refuse a billing timezone that differs from the pin")
def test_a_billing_timezone_that_differs_from_the_pin_refuses_to_start(db, objects):
    db.pin("UTC")

    async def scenario(owner, wp):
        worker = make_worker(wp, FakeEngine(), objects, billing_tz="Asia/Tokyo")
        return await within(worker.run(), 5, what="the worker to refuse a billing timezone that differs from the pin")

    assert run_as_worker(db, scenario) == 2
    assert db.one("SELECT count(*) FROM worker_heartbeats")[0] == 0


@mutation(W, "self.exit_code = max(self.exit_code, self.claims.exit_code)", "pass",
          expect="assert run_as_worker(db, scenario) == 1")
def test_the_claim_loops_error_limit_stops_the_whole_worker_with_exit_code_1(db, objects):
    class Unreachable(FakeSettings):
        def get(self, key):
            if key.endswith(".claims_enabled"):
                raise RuntimeError("settings unreachable")
            return super().get(key)

    async def scenario(owner, wp):
        worker = make_worker(wp, FakeEngine(), objects, settings=Unreachable(SPEC), max_errors=3)
        return await asyncio.wait_for(worker.run(), 20)

    assert run_as_worker(db, scenario) == 1
    assert db.one("SELECT consecutive_errors >= 3 FROM worker_heartbeats")[0]          # left for the alert


@mutation(W, "        self.exit_code = EXIT_FAILED\n\n    # ---- shutdown", "        pass\n\n    # ---- shutdown",
          expect="assert run_as_worker(db, scenario) == 1")
def test_a_loop_that_ends_on_its_own_is_a_failure_not_an_idle_worker(db, objects):
    async def scenario(owner, wp):
        worker = make_worker(wp, FakeEngine(), objects)

        async def quits():                              # a loop that returns without being asked to stop
            return None
        worker.reaper.run = quits
        return await asyncio.wait_for(worker.run(), 20)

    assert run_as_worker(db, scenario) == 1


# ---- the process, from the environment -----------------------------------------------------------------------------
def env_for(db, **extra) -> dict[str, str]:
    endpoint, access, secret = minio_settings()
    return {"DATABASE_URL": db.worker_dsn, "MINIO_ENDPOINT": endpoint.removeprefix("http://"),
            "MINIO_ACCESS_KEY": access, "MINIO_SECRET_KEY": secret, "CREDENTIAL_SEAL_PRIVATE_KEY": "unused-in-test",
            **extra}


def test_serve_refuses_a_missing_dsn_and_a_missing_private_key_before_connecting(db):
    spec = FakeSpec()
    assert asyncio.run(serve(spec, lambda s, st: FakeEngine(), {})) == 2
    no_keys = {k: v for k, v in env_for(db).items() if k != "CREDENTIAL_SEAL_PRIVATE_KEY"}
    assert asyncio.run(serve(spec, lambda s, st: FakeEngine(), no_keys)) == 2


@mutation(W, "                source.pop(crypto.PRIVATE_KEY_VAR, None)", "                pass",
          expect="the private key is still in the environment")
@mutation(W, "        for source in (os.environ, env):", "        for source in (env,):",
          expect="the private key is still in the environment")
def test_serve_takes_the_private_keys_out_of_the_environment_once_it_has_read_them(db, monkeypatch):
    db.pin("UTC")
    keys = "FAKE-key-one,FAKE-key-two"
    monkeypatch.setenv(crypto.PRIVATE_KEY_VAR, keys)
    env = env_for(db, BILLING_TZ="UTC", **{crypto.PRIVATE_KEY_VAR: keys})
    seen = {}

    class Stop(Exception):
        pass

    def build_engine(spec, settings):               # runs after the keys were read, before any engine or browser
        seen["process"] = os.environ.get(crypto.PRIVATE_KEY_VAR)
        seen["passed"] = env.get(crypto.PRIVATE_KEY_VAR)
        raise Stop

    with pytest.raises(Stop):
        asyncio.run(serve(FakeSpec(), build_engine, env, install_signals=False))
    assert seen == {"process": None, "passed": None}, "the private key is still in the environment: %r" % seen
    assert crypto.private_keys_from_env({crypto.PRIVATE_KEY_VAR: keys}) == keys.split(","), "premise: a comma list"


REGISTER_CHECK = "        ProviderRegistry().register(spec)"


@mutation(W, REGISTER_CHECK, "        pass",
          expect="timed out waiting for serve to refuse a spec that lacks a setting the core reads")
def test_serve_refuses_a_spec_that_does_not_declare_the_settings_the_core_reads(db):
    class NoRenderDeadline(FakeSpec):
        def settings(self):
            return {k: v for k, v in super().settings().items() if not k.endswith(".render_deadline_s")}

    async def refused():
        return await within(serve(NoRenderDeadline(), lambda s, st: FakeEngine(), env_for(db, BILLING_TZ="UTC"),
                                  install_signals=False), 15,
                            what="serve to refuse a spec that lacks a setting the core reads")

    assert asyncio.run(refused()) == 2


def test_serve_refuses_a_billing_timezone_other_than_the_pin(db):
    db.pin("UTC")
    code = asyncio.run(serve(FakeSpec(), lambda s, st: FakeEngine(), env_for(db, BILLING_TZ="Asia/Tokyo"),
                             install_signals=False))
    assert code == 2 and db.one("SELECT count(*) FROM worker_heartbeats")[0] == 0


SLASH_CHECK = '    if "/" in worker_id:'
NO_SLASH_CHECK = '    if False:'


@mutation(W, SLASH_CHECK, NO_SLASH_CHECK, expect="timed out waiting for serve to refuse a WORKER_ID with a slash")
def test_serve_refuses_a_worker_id_that_contains_a_slash_with_a_clear_message(db, caplog):
    """The reaper cuts the owner's final "/<suffix>" to find the heartbeat row: a "/" inside the id would make a live
    worker look gone. The refusal comes before any connection is opened."""
    db.pin("UTC")

    async def refused():
        return await within(serve(FakeSpec(), lambda s, st: FakeEngine(),
                                  env_for(db, BILLING_TZ="UTC", WORKER_ID="dola/eu"), install_signals=False), 15,
                            what="serve to refuse a WORKER_ID with a slash")

    assert asyncio.run(refused()) == 2
    assert "WORKER_ID 'dola/eu' contains '/'" in caplog.text
    assert db.one("SELECT count(*) FROM worker_heartbeats")[0] == 0


def test_a_boot_owner_is_the_worker_id_and_one_suffix_and_a_plain_id_is_accepted():
    assert worker_module.check_worker_id("dola-eu") == "dola-eu"
    owner = worker_module.boot_owner("dola-eu")
    assert owner.rpartition("/")[0] == "dola-eu" and len(owner.rpartition("/")[2]) == 8


def test_serve_runs_a_worker_from_the_environment_until_sigterm(db):
    db.pin("UTC")

    async def scenario():
        task = asyncio.create_task(serve(FakeSpec(), lambda s, st: FakeEngine(),
                                         env_for(db, BILLING_TZ="UTC", WORKER_GRACE_S="2")))
        await until(lambda: db.one("SELECT count(*) FROM worker_heartbeats WHERE worker_id = 'worker-fake'")[0] == 1,
                    timeout_s=15, what="the worker's heartbeat row")
        os.kill(os.getpid(), signal.SIGTERM)
        return await asyncio.wait_for(task, 20)

    assert asyncio.run(scenario()) == 0
    assert db.one("SELECT count(*) FROM worker_heartbeats")[0] == 0 and worker_module.DEFAULT_GRACE_S > 2


REGISTER_FIRST = "            registered = await self._register_when_free()\n"


@mutation(W, REGISTER_FIRST, "            registered = True\n", expect="the command loop of a booting worker ran")
def test_a_worker_that_boots_during_the_restore_mark_runs_no_loop_until_the_mark_has_committed(db, objects):
    """The first heartbeat write is made before any loop starts, so it waits for the mark's table lock as a whole:
    a queued command is neither run nor skipped by the mark while the mark is paused."""
    cid = new_command(db, "wake", account_id=account(db, checked=False))
    dump = datetime.now(UTC) + timedelta(hours=1)

    async def scenario(owner, wp):
        checked, go_on = asyncio.Event(), asyncio.Event()

        async def pause():
            checked.set()
            await go_on.wait()

        failpoints.arm("ops.restore.checked", pause)
        worker = make_worker(wp, FakeEngine(), objects)
        running = None
        try:
            marking = asyncio.create_task(ops.restore_mark(owner, dump))
            await asyncio.wait_for(checked.wait(), 5)
            running = asyncio.create_task(worker.run())
            await asyncio.sleep(1.0)                     # 50 ticks of the command loop
            async with tx(owner) as conn:
                status = (await (await conn.execute("SELECT status FROM commands WHERE id = %s", (cid,))).fetchone())[0]
            go_on.set()
            await marking
            return status
        finally:
            failpoints.disarm("ops.restore.checked")
            go_on.set()
            worker.stop()
            if running is not None:
                await asyncio.wait_for(running, 20)

    status = run_as_worker(db, scenario)
    assert status == "queued", "the command loop of a booting worker ran during the mark"


def hold_heartbeat_table(owner_dsn: str, held: "asyncio.Event", release: "asyncio.Event"):
    """What the restore mark does to the heartbeat table: SHARE mode in an open transaction until released."""
    import psycopg

    async def hold() -> None:
        async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=False) as conn:
            await conn.execute("LOCK TABLE worker_heartbeats IN SHARE MODE")
            held.set()
            await release.wait()
            await conn.commit()

    return hold()


@mutation(W, "            except LockNotAvailable:\n", "            except KeyError:\n",
          expect="the worker exited instead of waiting for the restore mark")
def test_a_worker_waits_for_a_restore_mark_longer_than_the_lock_timeout_and_then_starts_normally(db, objects):
    cid = new_command(db, "wake", account_id=account(db, checked=False))

    async def scenario(owner, wp):
        held, release = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(hold_heartbeat_table(db.dsn, held, release))
        worker = make_worker(wp, FakeEngine(), objects)
        running = None
        try:
            await asyncio.wait_for(held.wait(), 5)
            running = asyncio.create_task(worker.run())
            await asyncio.sleep(4.5)                                  # past the 3 s lock_timeout of the session
            if running.done():
                return "the worker exited instead of waiting for the restore mark: %r" % (
                    running.exception() or running.result())
            release.set()
            await holder
            await until(lambda: db.one("SELECT count(*) FROM worker_heartbeats")[0] == 1, timeout_s=15)
            await until(lambda: db.one("SELECT status FROM commands WHERE id = %s", cid)[0] != "queued",
                        timeout_s=15)
            return "ok"
        finally:
            release.set()
            worker.stop()
            if running is not None:
                await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), 20)
            await asyncio.gather(holder, return_exceptions=True)

    assert run_as_worker(db, scenario) == "ok"


@mutation(W, "        while not self.stopping:\n            try:\n                await self.claims._register()",
          "        while True:\n            try:\n                await self.claims._register()",
          expect="a stop signal did not end the wait")
def test_a_stop_signal_ends_a_worker_that_is_waiting_for_the_restore_mark(db, objects):
    async def scenario(owner, wp):
        held, release = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(hold_heartbeat_table(db.dsn, held, release))
        worker = make_worker(wp, FakeEngine(), objects)
        try:
            await asyncio.wait_for(held.wait(), 5)
            running = asyncio.create_task(worker.run())
            await asyncio.sleep(0.3)
            worker.stop()
            try:
                return await asyncio.wait_for(running, 4)
            except TimeoutError:
                return "a stop signal did not end the wait"
        finally:
            release.set()
            await asyncio.gather(holder, return_exceptions=True)

    assert run_as_worker(db, scenario) == 0
