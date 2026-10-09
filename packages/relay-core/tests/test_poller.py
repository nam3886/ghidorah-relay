"""The poller (contracts 7.1 "Poller"; I1, I21, I23): visibility claims, every poll result in both completion modes,
the poll token, disabled accounts, drain, and the browser share."""

import asyncio

import pytest
from jobkit import account, key, run, status
from lifecyclekit import (
    MediaEngine,
    accepted,
    due,
    pass_once,
    poller,
    row,
    to_waiting,
    until,
)

from relay_core import failpoints
from relay_core.budget import BrowserBudget, Purpose
from relay_core.providers import Accepted, Done, Facts, Failed, Output, Pending, PollResult, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, FakeSpec, sent
from relay_core.testing.mutation import mutation

A = "packages/relay-core/src/relay_core/jobs/apply.py"
P = "packages/relay-core/src/relay_core/jobs/poller.py"
POLL_TOKEN = "or job.poll_token is None or job.poll_token != poll_token"
STOPPING = "        if self.stopping:\n            return 0\n        free = "
DRAIN_STOPS = ("        if self.stopping or not self.settings.get(self.spec.name + '.claims_enabled'):\n"
               "            return 0\n        free = ")

MODES = ("hold", "detach")


async def waiting(db, p, k, completion="detach"):
    """A job in `waiting` whose poll is due, for the given completion mode (a hold job that fell back to polling
    is accepted exactly like a detach job)."""
    engine = FakeEngine()
    engine.script_submit("*", sent(accepted()))
    job_id = await to_waiting(p, k, completion=completion, engine=engine)
    due(db, job_id)
    return job_id


@pytest.mark.parametrize("completion", MODES)
def test_a_pending_poll_keeps_the_job_waiting_and_schedules_the_next_poll(db, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await waiting(db, p, k, completion)
        engine = FakeEngine()
        engine.script_poll("*", PollResult(Pending("rendering", 120, {"pct": 40})))
        await pass_once(poller(p, engine))
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, poll_token, provider_state ->> 'pct', "
                           "next_poll_at > clock_timestamp() + interval '100 seconds'") == ("waiting", None, "40", True)


@pytest.mark.parametrize("completion", MODES)
def test_a_done_poll_moves_the_job_to_fetching_with_its_outputs_and_charges_the_account_once(db, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await waiting(db, p, k, completion)
        engine = FakeEngine()
        out = Output("ref://a", "video/mp4", 0, "up-1")
        engine.script_poll("*", PollResult(Done((out,), Facts(7, "m-up"))))
        eng = poller(p, engine)
        await pass_once(eng)
        await pass_once(eng)             # a second pass finds nothing waiting
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, upstream_price, upstream_model, provider_state -> 'outputs' -> 0 ->> 'ref', "
                           "taken_at IS NOT NULL") == ("fetching", 7, "m-up", "ref://a", True)
    assert db.one("SELECT count(*) FROM account_charges WHERE job_id = %s", job_id) == (1,)
    assert db.one("SELECT count(*) FROM job_events WHERE job_id = %s AND stage = 'completed'", job_id) == (1,)


@pytest.mark.parametrize("completion", MODES)
def test_a_failed_poll_fails_the_job_and_refunds_the_key_once(db, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await waiting(db, p, k, completion)
        engine = FakeEngine()
        engine.script_poll("*", PollResult(Failed("render.deadline", "gave up", taken=False)))
        eng = poller(p, engine)
        await pass_once(eng)
        await pass_once(eng)
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, error_code, finished_at IS NOT NULL") == ("failed", "render.deadline", True)
    assert db.one("SELECT sum(CASE entry WHEN 'charge' THEN amount ELSE -amount END) FROM ledger "
                  "WHERE job_id = %s", job_id) == (0,)


@mutation(A, "                if out.taken:                           # proven taken",
          "                if False:                               # proven taken",
           expect="assert ('failed', False) == ('failed', True)")
def test_a_failed_poll_that_proves_the_order_taken_charges_the_account_and_refunds_it_in_one_go(db):
    """Accepted with `taken=False` (no account charge yet), then the poll fails with `taken=True`: the account's
    history must show the taken order, charged and refunded together with the failure (I7, D14)."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        engine = FakeEngine()
        engine.script_submit("*", sent(SubmitResult(Accepted("ref-t", taken=False))))
        job_id = await to_waiting(p, k, engine=engine)
        due(db, job_id)
        assert db.all("SELECT amount FROM account_charges WHERE job_id = %s", job_id) == []
        engine.script_poll("*", PollResult(Failed("render.deadline", "gave up", taken=True)))
        await pass_once(poller(p, engine))
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, taken_at IS NOT NULL") == ("failed", True)
    assert db.all("SELECT amount, refunded_at IS NOT NULL FROM account_charges WHERE job_id = %s", job_id) \
        == [(1, True)], "charged as taken, refunded with the failure"


def test_a_poll_that_raises_or_hangs_leaves_the_job_waiting_and_tries_again_later(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        a = await waiting(db, p, k)
        eng = poller(p, FakeEngine(), bound_s=0.2, error_retry_s=120)
        failpoints.arm("fake.poll", _raise)
        await pass_once(eng)
        raised = row(db, a, "status, poll_token, next_poll_at > clock_timestamp() + interval '100 seconds'")
        failpoints.arm("fake.poll", lambda: asyncio.sleep(5))
        due(db, a)
        await pass_once(eng)
        hung = row(db, a, "status, poll_token, next_poll_at > clock_timestamp() + interval '100 seconds'")
        failpoints.disarm()
        return raised, hung

    raised, hung = run(db, scenario)
    assert raised == hung == ("waiting", None, True)


async def _raise() -> None:
    raise RuntimeError("upstream broke")


# ---- the claim ---------------------------------------------------------------------------------------------------
def test_two_pollers_never_poll_the_same_job_twice(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        ids = [await waiting(db, p, k) for _ in range(6)]
        engines = [FakeEngine(), FakeEngine()]
        await asyncio.gather(*(pass_once(poller(p, e)) for e in engines))
        return ids, engines

    ids, engines = run(db, scenario)
    polled = [c[1] for e in engines for c in e.calls if c[0] == "poll"]
    assert sorted(polled) == sorted(str(i) for i in ids), "each due job is polled by exactly one poller, once"


def test_a_claim_that_is_never_applied_becomes_due_again_with_a_new_token(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await waiting(db, p, k)
        eng = poller(p, FakeEngine(), visibility_s=1.5)
        claimed = await eng._claim(5)                       # a poller that crashed after claiming
        assert [c.job_id for c in claimed] == [job_id]
        assert await eng._claim(5) == []                    # hidden meanwhile
        await asyncio.sleep(1.7)
        again = await eng._claim(5)
        return claimed[0].token, again

    first, again = run(db, scenario)
    assert len(again) == 1 and again[0].token != first


# ---- I23: the poll token ------------------------------------------------------------------------------------------
@mutation(A, POLL_TOKEN, "or False", expect="AssertionError: the stale result must not apply")
def test_a_poll_result_is_dropped_when_a_recheck_or_another_claim_took_the_token(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await waiting(db, p, k)
        engine = FakeEngine()
        engine.script_poll("*", PollResult(Done((Output("ref://a", "video/mp4", 0, "u"),))))
        release, inside = asyncio.Event(), asyncio.Event()

        async def stall() -> None:
            inside.set()
            await release.wait()

        failpoints.arm("fake.poll", stall)
        eng = poller(p, engine)
        await eng.tick()
        await asyncio.wait_for(inside.wait(), 5)
        db.run("UPDATE jobs SET poll_token = gen_random_uuid(), next_poll_at = clock_timestamp() + interval '1 hour'")
        release.set()
        await eng.idle()
        failpoints.disarm()
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, poll_token IS NOT NULL") == ("waiting", True), "the stale result must not apply"


# ---- reads only: disabled accounts and drain -----------------------------------------------------------------------
@mutation(P, STOPPING, DRAIN_STOPS,
           expect="AssertionError: open jobs on a disabled account still finish while the provider drains")
def test_drain_mode_and_a_disabled_account_do_not_stop_the_poller(db):
    k = key(db)
    acct = account(db, left=100)

    async def scenario(p):
        job_id = await waiting(db, p, k)
        db.run("UPDATE accounts SET state = 'disabled' WHERE id = %s", acct)
        settings = FakeSettings(fake__claims_enabled=False)
        await pass_once(poller(p, FakeEngine(), settings=settings))
        return job_id

    job_id = run(db, scenario)
    assert status(db, job_id) == "fetching", "open jobs on a disabled account still finish while the provider drains"


# ---- the browser share -------------------------------------------------------------------------------------------
def test_a_poll_that_needs_a_browser_holds_a_reconcile_share_token_only_while_it_runs(db):
    k, _ = key(db), account(db, left=100)
    spec = FakeSpec("fake", execution="browser")

    async def scenario(p):
        a = await waiting(db, p, k)
        b = await waiting(db, p, k)
        budget = BrowserBudget(6)
        engine = MediaEngine()
        busy = []

        async def watch() -> None:
            busy.append(budget.busy_by(Purpose.RECONCILE))

        failpoints.arm("fake.poll", watch)
        eng = poller(p, engine, spec=spec, budget=budget, needs_browser=lambda job: job.id == str(a))
        await pass_once(eng)
        failpoints.disarm()
        return a, b, engine, budget, busy

    a, b, engine, budget, busy = run(db, scenario)
    assert sorted(busy) == [0, 1], "only the poll that needs a browser holds a token"
    assert sum(t is not None for t in engine.tokens) == 1 and budget.busy == 0


def test_a_poll_that_needs_a_browser_needs_the_budget_and_a_provider_without_poll_refuses_one(db):
    async def scenario(p):
        with pytest.raises(ValueError, match="budget"):
            poller(p, FakeEngine(), needs_browser=lambda job: True)
        spec = FakeSpec("fake")
        object.__setattr__(spec.caps, "has_poll", False)
        with pytest.raises(ValueError, match="no poll"):
            poller(p, FakeEngine(), spec=spec)

    run(db, scenario)


def test_the_run_loop_polls_until_stopped_and_waits_for_what_is_in_flight(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await waiting(db, p, k)
        eng = poller(p, FakeEngine())
        task = asyncio.create_task(eng.run())
        await until(lambda: status(db, job_id) == "fetching", what="the poll")
        eng.stop()
        await asyncio.wait_for(task, 5)

    run(db, scenario)
