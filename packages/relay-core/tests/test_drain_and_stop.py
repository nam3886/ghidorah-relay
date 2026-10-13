"""Drain mode and graceful stop (contracts 7.7; invariants I1, I2, I14): a drained provider claims nothing while its
open jobs still finish; SIGTERM during a submit ends the job applied or `unknown`, never lost and never re-sent; no
new irreversible action starts after the signal; leases are released and the loop exits 0."""

import asyncio
import os
import signal

from enginekit import in_status, job_status, lease_count, make_loop, running, until
from jobkit import SPEC, account, create, key, run
from lifecyclekit import age, reaper, to_waiting

from relay_core.providers import Completed, Output, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, sent, unsent
from relay_core.testing.mutation import mutation

REAPER = "packages/relay-core/src/relay_core/jobs/reaper.py"
E = "packages/relay-core/src/relay_core/jobs/engine.py"
R = "packages/relay-core/src/relay_core/jobs/runctx.py"

DRAIN_GATES = ("drained = not self.settings.get(self._drain_key)\n"
               "        if drained:\n"
               "            return None\n"
               "        return await store.claim_one(self.db, provider=self.spec.name, caps=self.caps, "
               "settings=self.settings,")
NO_DRAIN_GATES = ("return await store.claim_one(self.db, provider=self.spec.name, caps=self.caps, settings=type("
                  "\"Open\", (), {\"get\": lambda _s, k: True if k.endswith(\".claims_enabled\") "
                  "else self.settings.get(k)})(),")
REAPER_RUNS = ("            await conn.execute(_STAMP_LATE, (self.spec.name, seconds, seconds))\n"
               "        return await self._fail_late(")
REAPER_SKIPS_DRAINED = ("            await conn.execute(_STAMP_LATE, (self.spec.name, seconds, seconds))\n"
                        "        if not self.settings.get(self.spec.name + \".claims_enabled\"):\n"
                        "            return 0\n"
                        "        return await self._fail_late(")
FENCE_STOPS = "async def fence(self) -> None:\n        if self._stopping():"
FENCE_IGNORES = "async def fence(self) -> None:\n        if False:"
MARK_SENT_STOPS = "async def mark_sent(self) -> None:\n        if self._stopping():"
MARK_SENT_IGNORES = "async def mark_sent(self) -> None:\n        if False:"


def ok() -> SubmitResult:
    return SubmitResult(Completed((Output("fake://x/0", "video/mp4", 0, "up-x-0"),)))


def row(db, job_id, names: str):
    return db.one("SELECT %s FROM jobs WHERE id = %%s" % names, job_id)


class Gate:
    """A scripted submit that says it started, waits to be released, and optionally fences first."""

    def __init__(self, *, fence_after=False, mark_first=False):
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.fence_after, self.mark_first = fence_after, mark_first

    async def __call__(self, ctx, job):
        if self.mark_first:
            self.started.set()
            await self.release.wait()
            await ctx.mark_sent()                 # the first irreversible action, after the signal
        else:
            self.started.set()
            await self.release.wait()
            if self.fence_after:
                await ctx.fence()                 # the next irreversible action (a gate re-send), after the signal
        return ok()


# ---- drain mode (7.7) --------------------------------------------------------------------------------------------
@mutation(E, DRAIN_GATES, NO_DRAIN_GATES,
           expect="assert ('fetching', 'fetching') == ('queued', 'queued')")
def test_a_drained_provider_claims_nothing_while_its_open_jobs_still_finish_and_resume_claims_again(db):
    k = key(db)
    account(db)
    account(db)

    async def scenario(p):
        engine, settings = FakeEngine(), FakeSettings(SPEC)
        gate = Gate()
        first = await create(p, k, "first", completion="hold")
        engine.script_submit(first.job_id, sent(gate))
        loop = make_loop(p, engine, settings=settings)
        claims = {"started": 0, "ended": 0}
        real_claim = loop._claim

        async def counted():
            claims["started"] += 1
            try:
                return await real_claim()
            finally:
                claims["ended"] += 1
        loop._claim = counted
        async with running(loop):
            await asyncio.wait_for(gate.started.wait(), 5)
            settings.set("fake.claims_enabled", False)             # drain while the first job is mid-submit
            # A claim that began before the drain was seen may still take one job (contracts 7.7, I9): wait until
            # that claim and the next one, which starts after the drain, have both ended before creating the job.
            seen = claims["started"]
            await until(lambda: claims["ended"] >= seen + 1, what="the loop to see the drain")
            second = await create(p, k, "second", completion="hold")
            await asyncio.sleep(0.3)                               # many ticks
            while_drained = job_status(db, second.job_id)
            gate.release.set()
            await until(in_status(db, first.job_id, "fetching"), what="the open job to finish")
            await asyncio.sleep(0.2)
            still_drained = job_status(db, second.job_id)
            settings.set("fake.claims_enabled", True)
            await until(in_status(db, second.job_id, "fetching"), what="the queued job to run after resume")
        return while_drained, still_drained, [c for c in engine.calls if c[0] == "submit"], second.job_id

    while_drained, still_drained, calls, second = run(db, scenario)
    assert (while_drained, still_drained) == ("queued", "queued")
    assert [c[1] for c in calls].count(str(second)) == 1 and len(calls) == 2


@mutation(REAPER, REAPER_RUNS, REAPER_SKIPS_DRAINED, expect="('waiting', None) == ('failed', 'pending')")
def test_a_drained_provider_still_fails_refunds_and_notifies_a_job_past_its_render_deadline(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        settings = FakeSettings(SPEC)
        settings.set("fake.claims_enabled", False)
        job_id = await to_waiting(p, k, account_cost=5)
        db.run("UPDATE jobs SET callback_url = 'https://hooks.example.test/in' WHERE id = %s", job_id)
        age(db, job_id, "render_since", 10_000)                    # past the render deadline
        await reaper(p, settings=settings).tick()
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, callback_status") == ("failed", "pending")
    assert db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'refund'", job_id) == (1,)
    assert db.one("SELECT count(*) FROM account_charges WHERE job_id = %s AND refunded_at IS NOT NULL",
                  job_id) == (1,), "the account charge is refunded with the key"


# ---- graceful stop -----------------------------------------------------------------------------------------------
def _signalled(loop):
    """Send SIGTERM to this process and wait until the loop's handler has turned it into a stop."""
    async def go():
        os.kill(os.getpid(), signal.SIGTERM)
        await until(lambda: loop.stopping, what="the SIGTERM handler")
    return go()


def test_sigterm_during_a_submit_lets_it_finish_and_apply_then_exits_clean_and_releases_the_lease(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine, gate = FakeEngine(), Gate()
        made = await create(p, k, "x", completion="hold")
        engine.script_submit(made.job_id, sent(gate))
        loop = make_loop(p, engine)
        task = asyncio.create_task(loop.run(install_signals=True))
        await asyncio.wait_for(gate.started.wait(), 5)
        await _signalled(loop)
        assert job_status(db, made.job_id) == "submitting" and not task.done(), "stop waits for the in-flight submit"
        gate.release.set()
        return made.job_id, await asyncio.wait_for(task, 10)

    job_id, code = run(db, scenario)
    assert (job_status(db, job_id), code, lease_count(db)) == ("fetching", 0, 0)


@mutation(R, FENCE_STOPS, FENCE_IGNORES,
           expect="assert ('fetching', 0, True) == ('unknown', 0, True)")
def test_after_sigterm_the_next_irreversible_step_is_refused_and_the_sent_job_ends_unknown_never_requeued(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine, gate = FakeEngine(), Gate(fence_after=True)
        made = await create(p, k, "x", completion="hold")
        engine.script_submit(made.job_id, sent(gate))
        loop = make_loop(p, engine)
        task = asyncio.create_task(loop.run(install_signals=True))
        await asyncio.wait_for(gate.started.wait(), 5)
        await _signalled(loop)
        gate.release.set()
        return made, engine, await asyncio.wait_for(task, 10)

    made, engine, code = run(db, scenario)
    assert row(db, made.job_id, "status, attempts, sent_at IS NOT NULL") == ("unknown", 0, True)
    assert code == 0 and lease_count(db) == 0 and [c for c in engine.calls if c[0] == "submit"] == [
        ("submit", str(made.job_id))], "never re-sent"


@mutation(R, MARK_SENT_STOPS, MARK_SENT_IGNORES,
           expect="assert ('fetching', 0, False, False) == ('queued', 0, True, True)")
def test_after_sigterm_a_job_that_was_not_sent_yet_is_not_sent_and_goes_back_to_queued_with_no_attempt(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine, gate = FakeEngine(), Gate(mark_first=True)
        made = await create(p, k, "x", completion="hold")
        engine.script_submit(made.job_id, unsent(gate))
        loop = make_loop(p, engine)
        task = asyncio.create_task(loop.run(install_signals=True))
        await asyncio.wait_for(gate.started.wait(), 5)
        await _signalled(loop)
        gate.release.set()
        return made, await asyncio.wait_for(task, 10)

    made, code = run(db, scenario)
    assert row(db, made.job_id, "status, attempts, sent_at IS NULL, account_id IS NULL") == ("queued", 0, True, True)
    assert code == 0 and lease_count(db) == 0


def test_a_submit_still_running_when_the_grace_period_ends_is_cancelled_and_a_sent_job_ends_unknown(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        engine, gate = FakeEngine(), Gate()          # never released: a hung browser
        made = await create(p, k, "x", completion="hold")
        engine.script_submit(made.job_id, sent(gate))
        loop = make_loop(p, engine, grace_s=0.1)
        task = asyncio.create_task(loop.run())
        await asyncio.wait_for(gate.started.wait(), 5)
        loop.stop()
        return made, await asyncio.wait_for(task, 10)

    made, code = run(db, scenario)
    assert row(db, made.job_id, "status, sent_at IS NOT NULL") == ("unknown", True)
    assert code == 0 and lease_count(db) == 0


def test_stop_with_nothing_in_flight_exits_at_once_and_removes_the_heartbeat_row(db):
    async def scenario(p):
        loop = make_loop(p)
        async with running(loop) as task:
            await until(lambda: db.one("SELECT count(*) FROM worker_heartbeats")[0] == 1, what="the heartbeat row")
        return task.result()

    assert run(db, scenario) == 0
    assert db.one("SELECT count(*) FROM worker_heartbeats") == (0,)
