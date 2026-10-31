"""Reconciling unknown jobs (I1, I2, I14, I27, I28): found, not sent (only with proof),
still unknown with a growing interval, the lease fence, the browser share and the render deadline."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from jobkit import account, key, run, status
from lifecyclekit import (
    MediaEngine,
    age,
    claim_new,
    due,
    pass_once,
    reaper,
    reconciler,
    row,
    to_unknown,
    until,
)

from relay_core import failpoints
from relay_core.budget import BrowserBudget, Purpose
from relay_core.jobs import store
from relay_core.providers import Cooldown, Found, NotSent, StillUnknown, Suspect
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, FakeSpec
from relay_core.testing.mutation import mutation

RC = "packages/relay-core/src/relay_core/jobs/reconcile.py"
FENCED = "lease_id=claim.lease_id, release_lease=True,"
UNFENCED = "lease_id=None, release_lease=False,"
MODES = ("hold", "detach")


def events(db, job_id) -> list[str]:
    return [r[0] for r in db.all("SELECT stage FROM job_events WHERE job_id = %s ORDER BY id", job_id)]


def delay_s(db, job_id) -> int:
    return round(db.one("SELECT extract(epoch FROM next_poll_at - clock_timestamp()) FROM jobs WHERE id = %s",
                        job_id)[0])


async def unknown_due(p, db, k, **kw):
    job_id = await to_unknown(p, k, **kw)
    due(db, job_id)
    return job_id


# ---- Found --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("completion", MODES)
def test_a_found_order_moves_the_job_to_waiting_and_charges_the_account_only_when_taken(db, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        taken = await unknown_due(p, db, k, prompt="a", completion=completion)
        unproven = await unknown_due(p, db, k, prompt="b", completion=completion)
        engine = FakeEngine()
        engine.script_reconcile("a", Found("ref-a", {"c": 1}, taken=True))
        engine.script_reconcile("b", Found("ref-b", {}, taken=False))
        eng = reconciler(p, engine)
        await pass_once(eng)                       # one slot per account: the second job waits for the next pass
        await pass_once(eng)
        return taken, unproven

    taken, unproven = run(db, scenario)
    assert row(db, taken, "status, external_ref, provider_state ->> 'c', taken_at IS NOT NULL") == (
        "waiting", "ref-a", "1", True)
    assert row(db, unproven, "status, external_ref, taken_at IS NOT NULL") == ("waiting", "ref-b", False)
    assert db.one("SELECT count(*) FROM account_charges WHERE job_id = %s", taken) == (1,)
    assert db.one("SELECT count(*) FROM account_charges WHERE job_id = %s", unproven) == (0,)
    assert db.one("SELECT count(*) FROM leases") == (0,), "the lease goes with the result"


@mutation("packages/relay-core/src/relay_core/jobs/apply.py",
          "provider_state=_found_state(result.provider_state)", "provider_state=_merge(result.provider_state)",
          expect="the found link is stored as given")
def test_a_found_order_keeps_its_output_links_as_given_and_redacts_only_their_meta(db):
    """A link with a long path segment (an fbcdn URL) is what the fetcher downloads: the redactor's long-token rule
    must not touch it, as for the outputs of a poll; their meta and the rest of the patch are still redacted (I24)."""
    k, _ = key(db), account(db, left=100)
    link = "https://scontent.fbcdn.net/o1/v/t6/f2/m69/" + "AQNx7s2" * 8 + ".mp4?_nc_ht=x&oh=00_y"
    token = "B" * 45

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", Found("conv-1", {"conversation_id": "conv-1", "debug": token, "outputs": [
            {"ref": link, "mime": "video/mp4", "meta": {"signed": token}}]}))
        await pass_once(reconciler(p, engine))
        return job_id

    job_id = run(db, scenario)
    state = row(db, job_id, "provider_state")[0]
    assert state["outputs"][0]["ref"] == link, "the found link is stored as given"
    assert state["outputs"][0]["meta"] == {"signed": "[redacted]"} and state["debug"] == "[redacted]"
    assert (state["outputs"][0]["mime"], state["outputs"][0]["idx"], state["conversation_id"]) == (
        "video/mp4", 0, "conv-1")


@mutation("packages/relay-core/src/relay_core/jobs/apply.py",
          "if not (isinstance(outs, list) and all(", "if outs is None or (False and all(",
          expect="a found order with malformed outputs still moves to waiting")
def test_a_found_order_with_malformed_outputs_still_moves_to_waiting(db):
    """Outputs the core cannot read as links (here a ref that is not a string, an entry that is not a mapping) are
    stored redacted like the rest of the patch, as before; the found transition never raises over them."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", Found("conv-1", {"conversation_id": "conv-1", "outputs": [{"ref": 5}, "x"]}))
        await pass_once(reconciler(p, engine))
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, external_ref, provider_state -> 'outputs'") == (
        "waiting", "conv-1", [{"ref": 5}, "x"]), "a found order with malformed outputs still moves to waiting"


# ---- NotSent: only with proof (I27) ---------------------------------------------------------------------------------
@pytest.mark.parametrize("completion", MODES)
def test_not_sent_is_refused_when_the_read_is_too_early_and_the_job_stays_unknown(db, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k, completion=completion)
        engine = FakeEngine()
        engine.script_reconcile("*", NotSent(covers_send_window=True))
        await pass_once(reconciler(p, engine))
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, sent_at IS NOT NULL, attempts") == ("unknown", True, 0)
    assert events(db, job_id)[-1] == "reconcile"


@pytest.mark.parametrize("completion", MODES)
def test_not_sent_is_refused_when_the_listing_does_not_cover_the_send_window(db, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k, completion=completion)
        age(db, job_id, "sent_at", 1_000)                            # long after sent_at + timeout + 60 s
        engine = FakeEngine()
        engine.script_reconcile("*", NotSent(covers_send_window=False))
        await pass_once(reconciler(p, engine))
        return job_id

    assert status(db, run(db, scenario)) == "unknown"


@pytest.mark.parametrize("completion", MODES)
def test_not_sent_with_both_proofs_requeues_the_job_with_an_attempt(db, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k, completion=completion)
        age(db, job_id, "sent_at", 1_000)
        engine = FakeEngine()
        engine.script_reconcile("*", NotSent(covers_send_window=True))
        await pass_once(reconciler(p, engine))
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, sent_at, account_id, attempts") == ("queued", None, None, 1)
    assert events(db, job_id)[-1] == "requeued"


# ---- StillUnknown and the deadline ----------------------------------------------------------------------------------
def test_still_unknown_grows_the_interval_from_60_seconds_and_stops_at_30_minutes(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        eng = reconciler(p, FakeEngine())
        seen = []
        for _ in range(7):
            await pass_once(eng)
            seen.append(delay_s(db, job_id))
            due(db, job_id)
        return job_id, seen

    job_id, seen = run(db, scenario)
    assert seen == [60, 120, 240, 480, 960, 1800, 1800] and status(db, job_id) == "unknown"


@mutation("packages/relay-core/src/relay_core/jobs/apply.py",
          "        await _effects(conn, job, effects, caps, pools)\n", "",
          expect="the read's strike reaches the account")
def test_a_still_unknown_read_that_shows_a_signed_out_account_strikes_and_rests_it(db):
    k, acc = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", StillUnknown(effects=(
            Suspect("not signed in"), Cooldown("signed out", datetime.now(UTC) + timedelta(hours=1), recheck=True))))
        await pass_once(reconciler(p, engine))
        return job_id

    job_id = run(db, scenario)
    assert status(db, job_id) == "unknown", "the job keeps waiting"
    assert db.one("SELECT strikes, cooldown_until > clock_timestamp() + interval '50 minutes' FROM accounts "
                  "WHERE id = %s", acc) == (1, True), "the read's strike reaches the account"


def test_a_reconcile_that_raises_or_hangs_counts_as_still_unknown_and_frees_the_account(db):
    k, _ = key(db), account(db, left=100)

    async def boom() -> None:
        raise RuntimeError("upstream broke")

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        eng = reconciler(p, FakeEngine(), hard_s=1.5)
        failpoints.arm("fake.reconcile", boom)
        await pass_once(eng)
        raised = (status(db, job_id), events(db, job_id).count("reconcile"), db.one("SELECT count(*) FROM leases")[0])
        failpoints.arm("fake.reconcile", lambda: asyncio.sleep(5))
        due(db, job_id)
        await pass_once(eng)
        failpoints.disarm()
        return raised, (status(db, job_id), events(db, job_id).count("reconcile"), db.one(
            "SELECT count(*) FROM leases")[0])

    raised, hung = run(db, scenario)
    assert raised == ("unknown", 1, 0) and hung == ("unknown", 2, 0)


def test_the_deadline_is_read_live_a_longer_one_keeps_the_job_in_reconciliation(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        settings = FakeSettings()
        job_id = await unknown_due(p, db, k)
        age(db, job_id, "sent_at", 9_100)
        settings.set("fake.render_deadline_s", 20_000)
        engine = FakeEngine()
        await pass_once(reconciler(p, engine, settings=settings))
        return len(engine.calls)

    assert run(db, scenario) == 1


# ---- the lease ----------------------------------------------------------------------------------------------------
def test_a_reconcile_runs_under_a_bounded_lease_on_any_account_state_and_a_second_reconciler_skips_it(db):
    k, acct = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        db.run("UPDATE accounts SET state = 'disabled' WHERE id = %s", acct)
        seen = []

        async def watch() -> None:
            seen.append(db.one("SELECT purpose, job_id, hard_until - clock_timestamp() <= interval '300 s', "
                               "hard_until > clock_timestamp() + interval '290 s' FROM leases"))
            seen.append(await reconciler(p, FakeEngine())._claim(3))     # the job is hidden from a second one

        failpoints.arm("fake.reconcile", watch)
        await pass_once(reconciler(p, FakeEngine()))
        failpoints.disarm()
        return job_id, seen

    job_id, seen = run(db, scenario)
    assert seen == [("reconcile", job_id, True, True), []]


@mutation("packages/relay-core/src/relay_core/jobs/apply.py",
          "if lease_id is not None and isinstance(result, StillUnknown):",
          "if False:", expect="the failed read is recorded")
def test_a_still_unknown_read_is_recorded_when_its_lease_just_expired_but_was_not_taken(db):
    """A read that ran out of time near its hard bound (the lease has just expired, the reaper has not taken it) still
    records its try and gives the account back; only a found result needs a live lease."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", StillUnknown())
        inside, release = asyncio.Event(), asyncio.Event()

        async def stall() -> None:
            inside.set()
            await release.wait()

        failpoints.arm("fake.reconcile", stall)
        eng = reconciler(p, engine, lease_beat_s=60)
        await eng.tick()
        await asyncio.wait_for(inside.wait(), 5)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second'")
        release.set()
        await eng.idle()
        failpoints.disarm()
        return job_id

    job_id = run(db, scenario)
    assert events(db, job_id).count("reconcile") == 1, "the failed read is recorded"
    assert status(db, job_id) == "unknown" and db.one("SELECT count(*) FROM leases") == (0,)


@mutation(RC, "async with self._keeper.holding(claim.lease_id) as settle:", "if (settle := lambda: None):",
          expect="the reconcile result was dropped though its lease could have been kept")
def test_a_reconcile_result_applied_late_still_finds_its_lease_because_the_keeper_holds_it_until_then(db, monkeypatch):
    k, _ = key(db), account(db, left=100)
    original, ended = store.apply_reconciled, []

    async def late(*args, **kw):                           # the apply starts well after the lease's TTL
        await asyncio.sleep(2.0)
        try:
            return await original(*args, **kw)
        finally:
            ended.append(True)
    monkeypatch.setattr(store, "apply_reconciled", late)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", Found("ref-x", {}, taken=True))
        eng = reconciler(p, engine, lease_ttl_s=1.5)
        task = asyncio.create_task(eng.run())              # `run` keeps the heartbeat beside the loop
        await until(lambda: ended, timeout_s=20, what="the application to end")
        eng.stop()
        await asyncio.wait_for(task, 5)
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, external_ref") == ("waiting", "ref-x"), \
        "the reconcile result was dropped though its lease could have been kept"
    assert db.one("SELECT count(*) FROM leases") == (0,)


@mutation(RC, "                settle()\n", "", expect="a reconcile that was applied was logged as dropped")
def test_a_heartbeat_between_the_commit_and_the_return_of_the_reconcile_write_does_not_cancel_the_holder(
        db, monkeypatch, caplog):
    k, _ = key(db), account(db, left=100)
    original, holder = store.apply_reconciled, []

    async def beat_after_commit(*args, **kw):             # the commit deleted the lease; a beat runs before the return
        status = await original(*args, **kw)
        await holder[0]._keeper.beat()
        await asyncio.sleep(0)
        return status
    monkeypatch.setattr(store, "apply_reconciled", beat_after_commit)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", Found("ref-x", {}, taken=True))
        holder.append(reconciler(p, engine))
        await pass_once(holder[0])
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, external_ref") == ("waiting", "ref-x")
    assert not [r for r in caplog.records if "dropped" in r.getMessage()], \
        "a reconcile that was applied was logged as dropped"


@mutation(RC, FENCED, UNFENCED, expect="the result must be dropped")
def test_a_reconcile_that_lost_its_lease_writes_nothing(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", Found("ref-x", {}, taken=True))
        inside, release = asyncio.Event(), asyncio.Event()

        async def stall() -> None:
            inside.set()
            await release.wait()

        failpoints.arm("fake.reconcile", stall)
        eng = reconciler(p, engine, lease_beat_s=60)             # no heartbeat notices: only the fence can
        await eng.tick()
        await asyncio.wait_for(inside.wait(), 5)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second'")
        release.set()
        await eng.idle()
        failpoints.disarm()
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, external_ref") == ("unknown", None), "the result must be dropped"
    assert db.one("SELECT count(*) FROM account_charges") == (0,)


def test_a_lease_the_heartbeat_cannot_extend_cancels_the_read_and_the_reaper_frees_the_account(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k)
        engine = FakeEngine()
        engine.script_reconcile("*", Found("ref-x", {}, taken=True))
        inside = asyncio.Event()

        async def stall() -> None:
            inside.set()
            await asyncio.sleep(5)

        failpoints.arm("fake.reconcile", stall)
        eng = reconciler(p, engine, lease_beat_s=0.05)
        task = asyncio.create_task(eng.run())
        await asyncio.wait_for(inside.wait(), 5)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second'")
        await until(lambda: not eng._inflight, what="the cancelled read to end")
        eng.stop()
        await asyncio.wait_for(task, 5)
        failpoints.disarm()
        before = db.one("SELECT count(*) FROM leases")[0]
        await reaper(p).tick()
        return job_id, before, db.one("SELECT count(*) FROM leases")[0]

    job_id, before, after = run(db, scenario)
    assert (status(db, job_id), before, after) == ("unknown", 1, 0)


def test_a_busy_account_is_reconciled_when_its_slot_frees(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await unknown_due(p, db, k, prompt="old")
        busy = await claim_new(p, k, "new")                       # the one slot is held by a live submit
        engine = FakeEngine()
        eng = reconciler(p, engine)
        await pass_once(eng)
        blocked = len(engine.calls)
        async with p.connection() as conn:
            await conn.execute("DELETE FROM leases WHERE id = %s", (busy.lease_id,))
        await pass_once(eng)
        return job_id, blocked, len(engine.calls)

    _, blocked, after = run(db, scenario)
    assert (blocked, after) == (0, 1)


# ---- the browser share (I28) ---------------------------------------------------------------------------------------
def test_a_browser_provider_reconciles_at_most_two_at_once_and_returns_every_token(db):
    k = key(db)
    for _ in range(4):
        account(db, left=100)
    spec = FakeSpec("fake", execution="browser", account_slots=1)

    async def scenario(p):
        ids = [await unknown_due(p, db, k, prompt="j%d" % i) for i in range(4)]
        budget = BrowserBudget(6)
        engine = MediaEngine()
        running, peak = [0], [0]

        async def hold() -> None:
            running[0] += 1
            peak[0] = max(peak[0], running[0], budget.busy_by(Purpose.RECONCILE))
            await asyncio.sleep(1.2)
            running[0] -= 1

        failpoints.arm("fake.reconcile", hold)
        eng = reconciler(p, engine, spec=spec, budget=budget)
        task = asyncio.create_task(eng.run())
        await until(lambda: len([c for c in engine.calls if c[0] == "reconcile"]) >= 4, what="four reads")
        eng.stop()
        await asyncio.wait_for(task, 10)
        failpoints.disarm()
        return peak[0], budget.busy, ids

    peak, busy, ids = run(db, scenario)
    assert peak == 2 and busy == 0


def test_reconcile_needs_a_provider_that_declares_it_and_a_browser_provider_needs_the_budget(db):
    async def scenario(p):
        spec = FakeSpec("fake", has_reconcile=False)
        with pytest.raises(ValueError, match="no reconcile"):
            reconciler(p, FakeEngine(), spec=spec)
        with pytest.raises(ValueError, match="budget"):
            reconciler(p, FakeEngine(), spec=FakeSpec("fake", execution="browser"))

    run(db, scenario)

