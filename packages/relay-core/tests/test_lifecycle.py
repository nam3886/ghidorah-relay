"""The job lifecycle in the store (contracts 7.1, 5.3; I1, I2, I7, I9, I21, I23, I24, I27): every submit, poll and
reconcile result in both completion modes, fenced by the lease id, with the account charged only when the order is
proven taken, and a failed job refunded in one transaction."""

import asyncio
import uuid

import pytest
from jobkit import CAPS, POOLS, account, claim, create, events, key, run, status, submit

from relay_core import failpoints, leases
from relay_core.db import tx
from relay_core.jobs import apply, store
from relay_core.providers import (
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Done,
    Facts,
    Fail,
    Failed,
    Found,
    NotSent,
    NotTaken,
    Output,
    Pending,
    PollResult,
    Retry,
    StillUnknown,
    SubmitResult,
    Success,
)
from relay_core.testing.fake_provider import FakeEngine, FakeSpec, sent, unsent
from relay_core.testing.mutation import mutation

A = "packages/relay-core/src/relay_core/jobs/apply.py"
F = "packages/relay-core/src/relay_core/jobs/fail.py"
SECRET = "sessionid=abcdef0123456789abcdef0123456789"

FENCED = "await leases.fence(conn, claim.lease_id)\n    job = await _lock_job(conn, claim.job_id)"
UNFENCED = "pass\n    job = await _lock_job(conn, claim.job_id)"
OWNER_FENCE = ("if (await (await conn.execute(\"SELECT 1 FROM leases WHERE owner = %s AND until > clock_timestamp()\", "
               "(claim.owner,))).fetchone()) is None:\n            raise LeaseLost(claim.lease_id)\n"
               "    job = await _lock_job(conn, claim.job_id)")
CHARGE_IF_TAKEN = "if outcome.taken:\n                await _charge_taken(conn, job, pools)"
CHARGE_ALWAYS = "if True:\n                await _charge_taken(conn, job, pools)"
TIMING = "proven_late = job.sent_at is not None and read_started_at > job.sent_at + timedelta("
NO_TIMING = "proven_late = True or job.sent_at is not None and read_started_at > job.sent_at + timedelta("
COVERED = "proven_covered = result.covers_send_window"
NOT_COVERED = "proven_covered = True"
POLL_TOKEN = "or job.poll_token is None or job.poll_token != poll_token"
REFUND_IN_TX = "return await fail_in_tx(conn, job_id, expect, code, detail, pools)"
REFUND_AFTER_COMMIT = ("if not await _mark_failed(conn, job_id, expect, code, detail):\n            return False\n"
                       "    async with tx(db) as other:\n        await _refund(other, job_id, pools)\n    return True")


@pytest.fixture(autouse=True)
def _no_requeue_pause(monkeypatch):
    """A requeued job is claimable at once, except in the tests of the pause itself."""
    monkeypatch.setattr(apply, "REQUEUE_PAUSE_S", 0)


def cols(db, job_id, names: str):
    return db.one("SELECT %s FROM jobs WHERE id = %%s" % names, job_id)


def charges(db, job_id):
    return db.all("SELECT amount, refunded_at IS NOT NULL FROM account_charges WHERE job_id = %s", job_id)


def left(db, pool_name="video"):
    return db.one("SELECT left_n, source FROM account_capacity WHERE pool = %s", pool_name)


def lease_count(db) -> int:
    return db.one("SELECT count(*) FROM leases")[0]


def wallet(db, k):
    return db.one("SELECT used_day, used_total FROM key_wallets WHERE key_id = %s AND kind = 'video'", k)


async def start(p, k, prompt="x", **opts):
    """Create a job and claim it: it is `submitting` under a live lease."""
    await create(p, k, prompt, **opts)
    return await claim(p)


def engine_with(*steps) -> FakeEngine:
    engine = FakeEngine()
    engine.script_submit("*", *steps)
    return engine


# ---- both completion modes end in fetching, charged once --------------------------------------------------------
@pytest.mark.parametrize("mode", ["hold", "detach"])
def test_a_job_reaches_fetching_in_either_completion_mode_charged_once_with_its_outputs(db, mode):
    k, _ = key(db), account(db)
    token = uuid.uuid4()

    async def scenario(p):
        got = await start(p, k, completion=mode, account_cost=2, price=7)
        engine = FakeEngine()
        if mode == "hold":
            first = await submit(p, got, engine)
        else:
            engine.script_submit("*", sent(SubmitResult(Accepted("ref-1", {"conv": "c1"}, Facts(5, "up-model")),
                                                         account=(Success(),))))
            first = await submit(p, got, engine)
            assert first == "waiting"
            assert cols(db, got.job_id, "external_ref, render_since = sent_at, next_poll_at > clock_timestamp()"
                        ) == ("ref-1", True, True)
            db.run("UPDATE jobs SET poll_token = %s WHERE id = %s", token, got.job_id)
            outs = (Output("u://1", "video/mp4", 0, "a"), Output("u://2", "video/mp4", 1, "b"))
            first = await store.apply_poll(p, got.job_id, token, PollResult(Done(outs, Facts(6, "up-model"))),
                                           caps=CAPS, pools=POOLS)
        return got, first

    got, outcome = run(db, scenario)
    assert outcome == "fetching" and status(db, got.job_id) == "fetching"
    assert charges(db, got.job_id) == [(2, False)] and left(db) == (2, "counted")
    model = "up-model" if mode == "detach" else None
    assert cols(db, got.job_id, "taken_at IS NOT NULL, poll_token IS NULL, upstream_model") == (True, True, model)
    outputs = db.one("SELECT provider_state -> 'outputs' FROM jobs WHERE id = %s", got.job_id)[0]
    assert [o["idx"] for o in outputs] == ([0] if mode == "hold" else [0, 1])
    assert lease_count(db) == 0 and "completed" in events(db, got.job_id)


def test_completed_outputs_are_all_kept_in_one_transaction_with_the_status_move(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k, completion="hold")
        outs = tuple(Output("u://%d" % i, "video/mp4", i, "id%d" % i, {"w": 1}) for i in range(3))
        return await store.apply_submit(p, got, SubmitResult(Completed(outs, Facts(1, "m"))), caps=CAPS, pools=POOLS)

    assert run(db, scenario) == "fetching"
    state = db.one("SELECT provider_state FROM jobs")[0]
    assert [o["upstream_id"] for o in state["outputs"]] == ["id0", "id1", "id2"]
    assert db.one("SELECT upstream_price, upstream_model FROM jobs") == (1, "m")


# ---- taken or not (I7) -----------------------------------------------------------------------------------------
@mutation(A, CHARGE_IF_TAKEN, CHARGE_ALWAYS,
          expect="assert charges(db, got.job_id) == [] and left(db) == (4, \"reset\")")
def test_an_order_not_yet_proven_taken_charges_no_account_and_stores_no_facts(db):
    k, _ = key(db), account(db)
    token = uuid.uuid4()

    async def scenario(p):
        got = await start(p, k, account_cost=2)
        engine = engine_with(sent(SubmitResult(Accepted("ref", taken=False, facts=Facts(9, "m")))))
        assert await submit(p, got, engine) == "waiting"
        assert charges(db, got.job_id) == [] and left(db) == (4, "reset")
        assert cols(db, got.job_id, "taken_at, upstream_price, upstream_model") == (None, None, None)
        db.run("UPDATE jobs SET poll_token = %s WHERE id = %s", token, got.job_id)
        await store.apply_poll(p, got.job_id, token, PollResult(Done((Output("u", "video/mp4", 0),), Facts(9, "m"))),
                               caps=CAPS, pools=POOLS)
        return got

    got = run(db, scenario)
    assert charges(db, got.job_id) == [(2, False)] and left(db) == (2, "counted")     # charged on the proof


def test_a_reported_balance_in_the_same_result_wins_over_the_counted_charge(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k, account_cost=1)
        await submit(p, got, engine_with(sent(SubmitResult(Accepted("r"), account=(Balance("video", 2),)))))

    run(db, scenario)
    assert left(db) == (2, "reported")


def test_a_poll_that_fails_the_job_refunds_key_and_account(db):
    k, _ = key(db), account(db)
    token = uuid.uuid4()

    async def scenario(p):
        got = await start(p, k, account_cost=2, price=7)
        await submit(p, got, FakeEngine())
        assert wallet(db, k) == (7, 7) and left(db) == (2, "counted")
        db.run("UPDATE jobs SET poll_token = %s", token)
        return await store.apply_poll(p, got.job_id, token, PollResult(Failed("account.dead_session", "gone")),
                                      caps=CAPS, pools=POOLS)

    assert run(db, scenario) == "failed"
    assert wallet(db, k) == (0, 0) and left(db) == (4, "counted") and charges(db, db.one("SELECT id FROM jobs")[0]) == [
        (2, True)]


def test_pending_keeps_the_job_waiting_and_clears_the_token(db):
    k, _ = key(db), account(db)
    token = uuid.uuid4()

    async def scenario(p):
        got = await start(p, k)
        await submit(p, got, FakeEngine())
        db.run("UPDATE jobs SET poll_token = %s", token)
        return await store.apply_poll(p, got.job_id, token, PollResult(Pending("rendering", 120, {"pct": 40})),
                                      caps=CAPS, pools=POOLS)

    assert run(db, scenario) == "waiting"
    assert db.one("SELECT poll_token, provider_state ->> 'pct', next_poll_at > clock_timestamp() + interval '100 s' "
                  "FROM jobs") == (None, "40", True)


@mutation(A, POLL_TOKEN, "or False",
          expect="assert await store.apply_poll(p, got.job_id, other, done, caps=CAPS, pools=POOLS")
def test_a_poll_result_applies_only_with_the_token_the_poller_holds(db):
    k, _ = key(db), account(db)
    mine, other = uuid.uuid4(), uuid.uuid4()

    async def scenario(p):
        got = await start(p, k)
        await submit(p, got, FakeEngine())
        db.run("UPDATE jobs SET poll_token = %s", mine)
        done = PollResult(Done((Output("u", "video/mp4", 0),)))
        assert await store.apply_poll(p, got.job_id, other, done, caps=CAPS, pools=POOLS) is None
        assert status(db, got.job_id) == "waiting"
        assert await store.apply_poll(p, got.job_id, mine, done, caps=CAPS, pools=POOLS) == "fetching"
        assert await store.apply_poll(p, got.job_id, mine, done, caps=CAPS, pools=POOLS) is None   # token cleared

    run(db, scenario)


# ---- requeue paths (I1, I9) ------------------------------------------------------------------------------------
def test_not_taken_requeues_with_sent_at_cleared_and_charges_nothing(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k)
        result = SubmitResult(NotTaken("pool.account_throttled", {"seen": 1}), account=(Success(),))
        return got, await submit(p, got, engine_with(sent(result)))

    got, outcome = run(db, scenario)
    assert outcome == "queued"
    assert cols(db, got.job_id, "sent_at, account_id, attempts, provider_state ->> 'seen', taken_at") == (
        None, None, 0, "1", None)
    assert charges(db, got.job_id) == [] and lease_count(db) == 0
    assert events(db, got.job_id) == ["queued", "requeued"]


def test_an_attempt_is_counted_only_for_a_code_whose_rule_counts_it_and_the_cap_fails_the_job(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        outcomes = []
        for _ in range(3):
            got = await start(p, k) if not outcomes else await claim(p)
            outcomes.append(await submit(p, got, engine_with(sent(SubmitResult(NotTaken("render.download_failed"))))))
        return outcomes

    assert run(db, scenario) == ["queued", "queued", "failed"]          # max_attempts is 3
    assert cols(db, db.one("SELECT id FROM jobs")[0], "status, attempts, error_code, finished_at IS NOT NULL") == (
        "failed", 3, "render.download_failed", True)
    assert wallet(db, k) == (0, 0)                                      # the failed job is refunded


def test_retry_before_the_send_requeues_and_after_the_send_it_is_unknown(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k, "a")
        early = await submit(p, got, engine_with(unsent(SubmitResult(Retry("pool.account_logged_out")))))
        assert cols(db, got.job_id, "status, sent_at") == ("queued", None)
        again = await claim(p)
        late = await submit(p, again, engine_with(sent(SubmitResult(Retry("pool.account_logged_out")))))
        return early, late, again

    early, late, again = run(db, scenario)
    assert (early, late) == ("queued", "unknown")                       # a send that may have happened is never retried
    assert cols(db, again.job_id, "status, sent_at IS NOT NULL, attempts") == ("unknown", True, 0)


def test_ambiguous_becomes_unknown_with_a_reconcile_due_in_a_minute(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k)
        return got, await submit(p, got, engine_with(sent(SubmitResult(Ambiguous("browser died")))))

    got, outcome = run(db, scenario)
    assert outcome == "unknown" and charges(db, got.job_id) == [] and lease_count(db) == 0
    assert cols(db, got.job_id, "sent_at IS NOT NULL, next_poll_at BETWEEN clock_timestamp() + interval '50 s' "
                                "AND clock_timestamp() + interval '70 s'") == (True, True)


def test_a_cancelled_submit_is_unknown_when_sent_and_queued_without_an_attempt_when_not(db):
    k = key(db)
    account(db)
    account(db)

    async def scenario(p):
        await create(p, k, "a")
        await create(p, k, "b")
        first, second = await claim(p), await claim(p)
        await store.mark_sent(p, second)
        return (await store.apply_interrupted(p, first, caps=CAPS, pools=POOLS),
                await store.apply_interrupted(p, second, caps=CAPS, pools=POOLS))

    assert run(db, scenario) == ("queued", "unknown")
    assert db.one("SELECT sum(attempts) FROM jobs")[0] == 0 and lease_count(db) == 0


# ---- failing (I21, I24) ----------------------------------------------------------------------------------------
def test_fail_refunds_the_key_stores_the_redacted_detail_and_shows_the_rule_message(db):
    k, _ = key(db), account(db)
    long_detail = "x" * 3000

    async def scenario(p):
        got = await start(p, k, price=7)
        result = SubmitResult(Fail("account.prompt_mismatch", "upstream said: %s %s" % (SECRET, long_detail)))
        return got, await submit(p, got, engine_with(sent(result)))

    got, outcome = run(db, scenario)
    assert outcome == "failed" and wallet(db, k) == (0, 0) and charges(db, got.job_id) == []
    code, error = cols(db, got.job_id, "error_code, error")
    assert code == "account.prompt_mismatch" and SECRET not in error and "abcdef0123" not in error
    assert "[omitted len=" in error or "[redacted]" in error
    text = db.all("SELECT text FROM job_events WHERE job_id = %s AND stage = 'failed'", got.job_id)[0][0]
    assert "the upstream order does not match this job" in text and "sessionid" not in text
    assert db.all("SELECT entry FROM ledger WHERE job_id = %s ORDER BY id", got.job_id) == [("charge",), ("refund",)]


def test_a_long_engine_text_is_stored_as_an_omitted_marker(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k)
        return await submit(p, got, engine_with(sent(SubmitResult(Fail("account.prompt_mismatch", "y" * 2500)))))

    run(db, scenario)
    assert db.one("SELECT error FROM jobs")[0] == "[omitted len=2500]"


@mutation(F, "    if not order_completed:\n        await pool.refund_job(conn, job_id, pools)",
          "    if True:\n        pass", expect="assert wallet(db, k) == (0, 0) and left(db) == (4, \"counted\")")
def test_failing_a_taken_job_refunds_the_exact_account_charge_and_sets_the_callback(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k, account_cost=3, price=4)
        await submit(p, got, FakeEngine())
        db.run("UPDATE jobs SET callback_url = 'https://hook.example/x' WHERE id = %s", got.job_id)
        assert await store.fail_job(p, got.job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
        assert not await store.fail_job(p, got.job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
        return got

    got = run(db, scenario)
    assert wallet(db, k) == (0, 0) and left(db) == (4, "counted") and charges(db, got.job_id) == [(3, True)]
    assert cols(db, got.job_id, "status, callback_status, callback_attempts, callback_for IS NOT NULL, "
                                "poll_token IS NULL") == ("failed", "pending", 0, True, True)
    assert db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 1          # the second call did nothing


@mutation(F, REFUND_IN_TX, REFUND_AFTER_COMMIT,
          expect="assert cols(db, got.job_id, \"status, error_code\") == (\"waiting\", None)")
def test_the_status_the_error_and_the_refunds_commit_together_or_not_at_all(db):
    k, _ = key(db), account(db)

    async def boom():
        raise failpoints.FailpointError("job.fail.refund")

    async def scenario(p):
        got = await start(p, k, price=7)
        await submit(p, got, FakeEngine())
        failpoints.arm("job.fail.refund", boom)
        try:
            with pytest.raises(failpoints.FailpointError):
                await store.fail_job(p, got.job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
        finally:
            failpoints.disarm()
        return got

    got = run(db, scenario)
    assert cols(db, got.job_id, "status, error_code") == ("waiting", None)
    assert wallet(db, k) == (7, 7) and db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 0


def test_fail_job_of_a_job_in_another_status_changes_nothing(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k, price=7)
        return await store.fail_job(p, got.job_id, expect=("waiting",), code="render.deadline", pools=POOLS)

    assert run(db, scenario) is False and wallet(db, k) == (7, 7)
    assert db.one("SELECT status FROM jobs")[0] == "submitting"


# ---- the fence (I2) --------------------------------------------------------------------------------------------
@mutation(A, FENCED, UNFENCED, expect="Failed: DID NOT RAISE LeaseLost")
def test_a_result_cannot_be_applied_after_the_lease_expired(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k, account_cost=2)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", got.lease_id)
        with pytest.raises(leases.LeaseLost):
            await submit(p, got, engine_with(unsent(SubmitResult(Accepted("ref", taken=True)))))
        return got

    got = run(db, scenario)
    assert cols(db, got.job_id, "status, external_ref, taken_at") == ("submitting", None, None)
    assert charges(db, got.job_id) == [] and left(db) == (4, "reset")


@mutation(A, FENCED, OWNER_FENCE, expect="Failed: DID NOT RAISE LeaseLost")
def test_the_fence_is_the_lease_id_not_the_owner(db):
    """Two jobs of one worker: the first one's lease expired while the second's is live. A fence that asks only
    whether the owner still holds some live lease would let the stale result through."""
    k = key(db)
    account(db)
    account(db)

    async def scenario(p):
        await create(p, k, "a")
        await create(p, k, "b")
        stale, live = await claim(p, owner="w1"), await claim(p, owner="w1")
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", stale.lease_id)
        with pytest.raises(leases.LeaseLost):
            await submit(p, stale, engine_with(unsent(SubmitResult(Accepted("ref")))))
        assert await submit(p, live, FakeEngine()) == "waiting"
        return stale

    stale = run(db, scenario)
    assert status(db, stale.job_id) == "submitting"


def test_a_claim_that_was_reclaimed_by_the_reaper_cannot_apply_its_old_result(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        old = await start(p, k)
        db.run("DELETE FROM leases WHERE id = %s", old.lease_id)               # the reaper's two writes
        db.run("UPDATE jobs SET status = 'queued', account_id = NULL WHERE id = %s", old.job_id)
        new = await claim(p)
        assert new.lease_id != old.lease_id and new.job_id == old.job_id
        with pytest.raises(leases.LeaseLost):
            await submit(p, old, FakeEngine())
        return await submit(p, new, FakeEngine())

    assert run(db, scenario) == "waiting"


@mutation(A, "UPDATE jobs SET sent_at = COALESCE(sent_at, clock_timestamp()), updated_at",
          "UPDATE jobs SET sent_at = clock_timestamp(), updated_at",
          expect="assert cols(db, got.job_id, \"sent_at\")[0] == first")
def test_mark_sent_is_fenced_and_keeps_the_first_time(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k)
        await store.mark_sent(p, got)
        first = cols(db, got.job_id, "sent_at")[0]
        await asyncio.sleep(0.05)
        await store.mark_sent(p, got)
        assert cols(db, got.job_id, "sent_at")[0] == first
        db.run("UPDATE jobs SET sent_at = NULL WHERE id = %s", got.job_id)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", got.lease_id)
        with pytest.raises(leases.LeaseLost):
            await store.mark_sent(p, got)
        return got

    got = run(db, scenario)
    assert cols(db, got.job_id, "sent_at") == (None,)


# ---- unknown and the NotSent rule (I27) ------------------------------------------------------------------------
async def unknown_job(p, db, k, *, sent_ago_s, **opts):
    got = await start(p, k, **opts)
    await submit(p, got, engine_with(sent(SubmitResult(Ambiguous("lost")))))
    db.run("UPDATE jobs SET sent_at = clock_timestamp() - make_interval(secs => %s) WHERE id = %s",
           sent_ago_s, got.job_id)
    return got


async def reconcile(p, got, result, *, read_ago_s=0, **kw):
    """The read's start comes from the database clock, like `sent_at` and like a real claim: never from this
    machine's, which can be tens of seconds off a Docker VM's."""
    async with tx(p) as conn:
        started = (await (await conn.execute("SELECT clock_timestamp() - make_interval(secs => %s)",
                                             (read_ago_s,))).fetchone())[0]
    return await store.apply_reconciled(p, got.job_id, result, read_started_at=started, caps=CAPS, pools=POOLS, **kw)


@mutation(A, TIMING, NO_TIMING, expect="assert (refused, accepted) == (\"unknown\", \"queued\")")
def test_not_sent_is_refused_before_the_send_window_has_passed(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await unknown_job(p, db, k, sent_ago_s=100, submit_timeout_s=60)      # 100 s < 60 + 60
        refused = await reconcile(p, got, NotSent(True))
        db.run("UPDATE jobs SET sent_at = clock_timestamp() - interval '200 seconds' WHERE id = %s", got.job_id)
        accepted = await reconcile(p, got, NotSent(True))
        return got, refused, accepted

    got, refused, accepted = run(db, scenario)
    assert (refused, accepted) == ("unknown", "queued")
    assert cols(db, got.job_id, "sent_at, attempts, account_id") == (None, 1, None)
    assert "requeued" in events(db, got.job_id) and any("refused" in t[0] for t in db.all(
        "SELECT text FROM job_events WHERE stage = 'reconcile'"))


def test_not_sent_needs_a_read_that_started_after_the_window_not_just_a_late_job(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await unknown_job(p, db, k, sent_ago_s=500, submit_timeout_s=60)
        early_read = await reconcile(p, got, NotSent(True), read_ago_s=450)     # the read began 50 s after the send
        late_read = await reconcile(p, got, NotSent(True), read_ago_s=0)
        return early_read, late_read

    assert run(db, scenario) == ("unknown", "queued")


@mutation(A, COVERED, NOT_COVERED,
          expect="assert status(db, got.job_id) == \"unknown\" and cols(db, got.job_id, \"sent_at IS")
def test_not_sent_is_refused_when_the_listing_does_not_cover_the_send_window(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await unknown_job(p, db, k, sent_ago_s=500, submit_timeout_s=60)
        capped = await reconcile(p, got, NotSent(False))
        assert status(db, got.job_id) == "unknown" and cols(db, got.job_id, "sent_at IS NOT NULL") == (True,)
        return capped, await reconcile(p, got, NotSent(True))

    assert run(db, scenario) == ("unknown", "queued")


def test_still_unknown_backs_off_60_120_240_and_never_past_30_minutes(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await unknown_job(p, db, k, sent_ago_s=10)
        waits = []
        for _ in range(7):
            assert await reconcile(p, got, StillUnknown()) == "unknown"
            waits.append(round(cols(db, got.job_id, "extract(epoch FROM next_poll_at - clock_timestamp())")[0] / 10))
        return waits

    assert run(db, scenario) == [6, 12, 24, 48, 96, 180, 180]


def test_found_moves_an_unknown_job_to_waiting_and_charges_only_a_proven_order(db):
    k = key(db)
    account(db)
    account(db)

    async def scenario(p):
        await create(p, k, "a", account_cost=2)
        await create(p, k, "b", account_cost=2)
        one, two = await claim(p), await claim(p)
        for got in (one, two):
            await submit(p, got, engine_with(sent(SubmitResult(Ambiguous()))))
        taken = await reconcile(p, one, Found("r1", {"c": 1}, taken=True))
        unproven = await reconcile(p, two, Found("r2", taken=False))
        return one, two, taken, unproven

    one, two, taken, unproven = run(db, scenario)
    assert (taken, unproven) == ("waiting", "waiting")
    assert charges(db, one.job_id) == [(2, False)] and charges(db, two.job_id) == []
    assert cols(db, one.job_id, "external_ref, provider_state ->> 'c', taken_at IS NOT NULL") == ("r1", "1", True)
    assert cols(db, two.job_id, "taken_at") == (None,)


def test_a_reconcile_of_a_job_that_is_no_longer_unknown_changes_nothing(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k)
        return await reconcile(p, got, NotSent(True))

    assert run(db, scenario) == "submitting"


def test_reconcile_applies_under_its_own_fenced_lease(db):
    k, acct = key(db), account(db)

    async def scenario(p):
        got = await unknown_job(p, db, k, sent_ago_s=10)
        async with tx(p) as conn:
            lease_id = await leases.acquire(conn, account_id=acct, purpose="reconcile", owner="w1", hard_s=60)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", lease_id)
        with pytest.raises(leases.LeaseLost):
            await reconcile(p, got, Found("r"), lease_id=lease_id)
        return got

    got = run(db, scenario)
    assert status(db, got.job_id) == "unknown"
    db.run("DELETE FROM leases")


# ---- title and warnings ride along, effects apply --------------------------------------------------------------
def test_title_warnings_and_account_effects_are_applied_with_the_transition(db):
    from relay_core.providers import Warning
    k, acct = key(db), account(db)
    db.run("UPDATE accounts SET strikes = 2 WHERE id = %s", acct)

    async def scenario(p):
        got = await start(p, k)
        result = SubmitResult(Accepted("r"), account=(Success(),), warnings=(Warning("w.x", "careful"),),
                              title="A title")
        return await submit(p, got, engine_with(sent(result)))

    run(db, scenario)
    assert db.one("SELECT title, warnings -> 0 ->> 'code' FROM jobs") == ("A title", "w.x")
    assert db.one("SELECT strikes FROM accounts WHERE id = %s", acct)[0] == 0


# ---- the checker's job-level rules -----------------------------------------------------------------------------
CHECKER = "packages/relay-core/src/relay_core/testing/checker.py"


@mutation(CHECKER, "WHEN 'refund' THEN -amount ELSE 0 END) <> 0", "WHEN 'refund' THEN -amount ELSE 0 END) > 99999",
          expect="assert any(line.startswith(\"%s job %s\" % (tag, job)) for line in found), (tag, j")
def test_the_checker_names_each_broken_job_rule(db):
    from relay_core.testing.checker import violations
    k = key(db)
    acct = account(db)
    sent_queued, failed_charged, uncharged_taken, facts_only, done_empty, waiting_no_ref = (db.job(k) for _ in range(6))
    db.run("UPDATE jobs SET sent_at = clock_timestamp() WHERE id = %s", sent_queued)
    db.run("UPDATE jobs SET status = 'failed' WHERE id = %s", failed_charged)
    db.run("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
           "VALUES (%s, %s, 'fake', 'video', 'charge', 5, current_date)", k, failed_charged)
    db.run("UPDATE key_wallets SET used_day = 5, used_total = 5, day_anchor = current_date WHERE key_id = %s "
           "AND kind = 'video'", k)
    db.run("UPDATE jobs SET status = 'waiting', external_ref = 'r' WHERE id = %s", uncharged_taken)
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) VALUES (%s, %s, 'video', 1, "
           "current_date)", acct, uncharged_taken)
    db.run("UPDATE jobs SET upstream_price = 3 WHERE id = %s", facts_only)
    db.run("UPDATE jobs SET status = 'done' WHERE id = %s", done_empty)
    db.run("UPDATE jobs SET status = 'waiting' WHERE id = %s", waiting_no_ref)
    with db.connect() as c:
        found = violations(c)
    db.reset()
    for tag, job in (("I9", sent_queued), ("I21", failed_charged), ("I7", uncharged_taken), ("I7", facts_only),
                     ("I11", done_empty), ("I1", waiting_no_ref)):
        assert any(line.startswith("%s job %s" % (tag, job)) for line in found), (tag, job, found)


# ---- the engine's view and the fake provider itself ------------------------------------------------------------
def test_report_ref_is_fenced_and_the_job_view_shows_what_was_reported(db):
    k, _ = key(db), account(db)
    media = (store.InputMedia(0, "in/0.png", "image/png", 10),)

    async def scenario(p):
        await create(p, k, "x", inputs=media)
        got = await claim(p)
        await store.report_ref(p, got, "ref-9", {"conv": "c9"})
        view = await store.job_view(p, got.job_id)
        refs = await view.inputs()
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", got.lease_id)
        with pytest.raises(leases.LeaseLost):
            await store.report_ref(p, got, "ref-10", {})
        return view, refs

    view, refs = run(db, scenario)
    assert (view.external_ref, view.provider_state, view.prompt) == ("ref-9", {"conv": "c9"}, "x")
    assert [(r.idx, r.object_key) for r in refs] == [(0, "in/0.png")]


def test_the_fake_spec_registers_and_its_engine_scripts_are_consumed_in_order():
    from relay_core.providers import ProviderRegistry
    registry = ProviderRegistry()
    registry.register(FakeSpec())
    assert registry.resolve("fake-video")[1].media_type == "video"
    engine = FakeEngine()
    job = type("J", (), {"id": "j1", "prompt": "p", "params": {}})()
    engine.script_poll("j1", PollResult(Pending()), PollResult(Done(engine.outputs(job))))

    got =[asyncio.run(engine.poll(None, job)).outcome for _ in range(3)]
    assert [type(g).__name__ for g in got] == ["Pending", "Done", "Done"]       # the last entry repeats


# ---- the requeue pause (a failing upstream is not hit on every tick) ----------------------------------------------
PICK_PAUSE = "(next_poll_at IS NULL OR next_poll_at <= clock_timestamp())\n   AND (%(afford)s"
HEADS_PAUSE = "AND (next_poll_at IS NULL OR next_poll_at <= clock_timestamp())\n     ORDER BY account_cost"
PAUSE_SET = "next_poll_at=_in(REQUEUE_PAUSE_S), **_reset(patch or {}))"
PAUSE_NONE = "next_poll_at=None, **_reset(patch or {}))"


@mutation(A, PAUSE_SET, PAUSE_NONE, expect="the paused job was claimed again at once")
@mutation("packages/relay-core/src/relay_core/jobs/claim.py", PICK_PAUSE, "(%(afford)s",
          expect="assert newer.job_id != got.job_id and newer.account_id == first      # its own o")
@mutation("packages/relay-core/src/relay_core/jobs/claim.py", HEADS_PAUSE, "     ORDER BY account_cost",
          expect="assert newer.job_id != got.job_id and newer.account_id == first      # its own o")
@pytest.mark.parametrize("outcome", [NotTaken("pool.account_throttled"), Retry("pool.account_logged_out")])
def test_a_job_requeued_after_not_taken_or_retry_waits_before_it_is_claimed_again(db, monkeypatch, outcome):
    monkeypatch.setattr(apply, "REQUEUE_PAUSE_S", 30)
    k, first = key(db), account(db, used_ago_s=100)
    account(db, used_ago_s=50)

    async def scenario(p):
        got = await start(p, k, "older")
        assert got.account_id == first                                   # the least recently used
        assert await submit(p, got, engine_with(unsent(SubmitResult(outcome)))) == "queued"
        assert await claim(p) is None, "the paused job was claimed again at once"
        await create(p, k, "newer")
        db.run("UPDATE accounts SET last_used_at = clock_timestamp() - interval '1 hour' WHERE id = %s", first)
        newer = await claim(p)                                           # only the unpaused job may run
        db.run("UPDATE jobs SET next_poll_at = clock_timestamp() - interval '1 second' WHERE id = %s", got.job_id)
        return got, newer, await claim(p)

    got, newer, later = run(db, scenario)
    assert newer.job_id != got.job_id and newer.account_id == first      # its own order: not the paused job's `tried`
    assert later is not None and later.job_id == got.job_id              # due again once the pause is over


# ---- a requeue drops what the previous attempt's account made --------------------------------------------------------
RESET_STATE = 'return dict(external_ref=None, provider_state=_Raw("%s::jsonb", (_state(patch),)))'
KEEP_STATE = 'return dict(provider_state=_Raw("provider_state || %s::jsonb", (_state(patch),)))'
DROPPED_TEXT = 'return " (dropped reference %s)" % job.external_ref if job.external_ref else ""'


@mutation(A, RESET_STATE, KEEP_STATE, expect="the next account would reconcile the previous account's conversation")
@mutation(A, DROPPED_TEXT, 'return ""', expect="assert any(\"conv-A\" in t for t in texts), texts")
@pytest.mark.parametrize("how", ["not_taken", "retry", "interrupted", "not_sent"])
def test_a_requeue_clears_the_reference_and_state_of_the_previous_attempt_and_records_the_old_reference(db, how):
    k, _ = key(db), account(db)

    async def scenario(p):
        if how == "not_sent":
            got = await unknown_job(p, db, k, sent_ago_s=500, submit_timeout_s=60)
            db.run("UPDATE jobs SET external_ref = 'conv-A', provider_state = '{\"conv\": \"A\"}' WHERE id = %s",
                   got.job_id)
            assert await reconcile(p, got, NotSent(True)) == "queued"
            return got
        got = await start(p, k)
        await store.report_ref(p, got, "conv-A", {"conv": "A"})
        if how == "interrupted":
            await store.apply_interrupted(p, got, caps=CAPS, pools=POOLS)
        else:
            result = SubmitResult(NotTaken("pool.account_throttled", {"seen": 1}) if how == "not_taken"
                                  else Retry("pool.account_logged_out"))
            await submit(p, got, engine_with(unsent(result)))
        return got

    got = run(db, scenario)
    ref, state = cols(db, got.job_id, "external_ref, provider_state")
    assert ref is None and "conv" not in state, "the next account would reconcile the previous account's conversation"
    assert state == ({"seen": 1} if how == "not_taken" else {})
    texts = [r[0] for r in db.all("SELECT text FROM job_events WHERE job_id = %s AND stage = 'requeued'", got.job_id)]
    assert any("conv-A" in t for t in texts), texts


# ---- mark_sent refuses a job that left this claim ---------------------------------------------------------------
@mutation(A, "        if cur.rowcount != 1:       # the job left", "        if False:       # the job left",
          expect="Failed: DID NOT RAISE LeaseLost")
def test_mark_sent_refuses_a_job_that_is_no_longer_submitting_on_this_claims_account(db):
    k, accounts = key(db), (account(db), account(db))

    async def scenario(p):
        got = await start(p, k)
        other = next(a for a in accounts if a != got.account_id)
        db.run("UPDATE jobs SET status = 'queued', account_id = NULL WHERE id = %s", got.job_id)   # left the claim
        with pytest.raises(leases.LeaseLost):
            await store.mark_sent(p, got)
        db.run("UPDATE jobs SET status = 'submitting', account_id = %s WHERE id = %s", other, got.job_id)
        with pytest.raises(leases.LeaseLost):                                                  # another account's
            await store.mark_sent(p, got)
        db.run("UPDATE jobs SET account_id = %s WHERE id = %s", got.account_id, got.job_id)    # put back
        return got

    got = run(db, scenario)
    assert cols(db, got.job_id, "sent_at") == (None,)


# ---- a pending reason that changes is a timeline line ------------------------------------------------------------
NOTE_CHANGED = 'if note and note != job.provider_state.get("pending_note"):'


@mutation(A, NOTE_CHANGED, "if note:",
          expect="assert lines == [\"slot 2/3 pending\", \"quiet for 40 min\", \"slot 2/3 pending\"]")
@mutation(A, 'await add_event(conn, job_id, "system", "pending", note)', "pass",
          expect="assert lines == [\"slot 2/3 pending\", \"quiet for 40 min\", \"slot 2/3 pending\"]")
def test_a_pending_reason_writes_a_system_event_only_when_it_changes(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k)
        await submit(p, got, engine_with(sent(SubmitResult(Accepted("r")))))
        for note in ("slot 2/3 pending", "slot 2/3 pending", "", "quiet for 40 min", "slot 2/3 pending"):
            token = uuid.uuid4()
            db.run("UPDATE jobs SET poll_token = %s WHERE id = %s", token, got.job_id)
            await store.apply_poll(p, got.job_id, token, PollResult(Pending(note, 30)), caps=CAPS, pools=POOLS)
        return got

    got = run(db, scenario)
    lines = [r[0] for r in db.all("SELECT text FROM job_events WHERE job_id = %s AND stage = 'pending' ORDER BY id",
                                  got.job_id)]
    assert lines == ["slot 2/3 pending", "quiet for 40 min", "slot 2/3 pending"]
    assert db.one("SELECT source FROM job_events WHERE stage = 'pending' LIMIT 1") == ("system",)


# ---- a title from a result is cleaned, an empty stored title is unset ---------------------------------------------
@mutation(A, "title = clean_title(result.title)\n    if title is not None:",
          "title = result.title\n    if title is not None:",
          expect="assert title is not None and \"\\n\" not in title and len(title) <= 200 and title.s")
@mutation(A, "COALESCE(NULLIF(title, ''), %s)", "COALESCE(title, %s)",
          expect="assert title is not None and \"\\n\" not in title and len(title) <= 200 and title.s")
def test_a_title_from_a_result_is_one_short_line_and_fills_an_empty_stored_title(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k)
        db.run("UPDATE jobs SET title = '' WHERE id = %s", got.job_id)
        title = "first line\n  second\tline " + "x" * 500
        await submit(p, got, engine_with(sent(SubmitResult(Accepted("r"), title=title))))
        return got

    got = run(db, scenario)
    title = cols(db, got.job_id, "title")[0]
    assert title is not None and "\n" not in title and len(title) <= 200 and title.startswith("first line second line")


# ---- planted secrets in every job-state writer (I24) --------------------------------------------------------------
STATE_REDACT = "return Jsonb(redact(dict(patch)))"
META_REDACT = '"meta": redact(dict(o.meta))}'
LEAK = "abcdef0123456789abcdef0123456789"


def stored_text(db, job_id) -> str:
    return db.one("SELECT provider_state::text FROM jobs WHERE id = %s", job_id)[0]


@mutation(A, STATE_REDACT, "return Jsonb(dict(patch))", expect="a secret reached provider_state")
@pytest.mark.parametrize("writer", ["report_ref", "accepted", "not_taken", "fail", "pending", "found"])
def test_a_secret_in_a_provider_state_patch_is_not_stored(db, writer):
    k, _ = key(db), account(db)
    state = {"debug": "Cookie: " + SECRET, "note": "sessionid=" + LEAK}

    async def scenario(p):
        got = await start(p, k)
        if writer == "report_ref":
            await store.report_ref(p, got, "ref-1", state)
        elif writer == "accepted":
            await submit(p, got, engine_with(sent(SubmitResult(Accepted("r", state)))))
        elif writer == "not_taken":
            await submit(p, got, engine_with(unsent(SubmitResult(NotTaken("pool.account_throttled", state)))))
        elif writer == "fail":
            await submit(p, got, engine_with(sent(SubmitResult(Fail("render.deadline", "no", state)))))
        elif writer == "pending":
            await submit(p, got, engine_with(sent(SubmitResult(Accepted("r")))))
            token = uuid.uuid4()
            db.run("UPDATE jobs SET poll_token = %s WHERE id = %s", token, got.job_id)
            await store.apply_poll(p, got.job_id, token, PollResult(Pending("p", 30, state)), caps=CAPS, pools=POOLS)
        else:
            await submit(p, got, engine_with(sent(SubmitResult(Ambiguous("lost")))))
            await reconcile(p, got, Found("ref-2", state, False))
        return got

    got = run(db, scenario)
    text = stored_text(db, got.job_id)
    assert LEAK not in text and "sessionid=" + LEAK not in text, "a secret reached provider_state"
    if writer != "not_taken":
        assert "[redacted]" in text


@mutation(A, META_REDACT, '"meta": dict(o.meta)}', expect="assert LEAK not in stored_text(db, got.job_id)")
@pytest.mark.parametrize("via", ["completed", "done"])
def test_a_secret_in_an_output_meta_is_not_stored_but_the_output_ref_is_kept_as_given(db, via):
    k, _ = key(db), account(db)
    signed = "https://cdn.example/v.mp4?oh=0123456789abcdef&oe=65f"
    out = Output(signed, "video/mp4", 0, "up-0", {"debug": "sessionid=" + LEAK, "w": 4})

    async def scenario(p):
        got = await start(p, k, completion="hold" if via == "completed" else "detach")
        if via == "completed":
            await submit(p, got, engine_with(sent(SubmitResult(Completed((out,))))))
        else:
            await submit(p, got, engine_with(sent(SubmitResult(Accepted("r")))))
            token = uuid.uuid4()
            db.run("UPDATE jobs SET poll_token = %s WHERE id = %s", token, got.job_id)
            await store.apply_poll(p, got.job_id, token, PollResult(Done((out,))), caps=CAPS, pools=POOLS)
        return got

    got = run(db, scenario)
    stored = db.one("SELECT provider_state -> 'outputs' -> 0 FROM jobs WHERE id = %s", got.job_id)[0]
    assert LEAK not in stored_text(db, got.job_id)
    assert stored["ref"] == signed and stored["meta"]["w"] == 4          # the fetcher still gets its URL


MODEL_REDACT = "upstream_model=redact_log(outcome.facts.upstream_model),\n                       provider_state"
MODEL_RAW = "upstream_model=outcome.facts.upstream_model,\n                       provider_state"


@mutation(A, MODEL_REDACT, MODEL_RAW, expect="assert LEAK not in cols(db, got.job_id, \"upstream_model\")[0]")
def test_a_secret_in_the_upstream_model_fact_is_not_stored(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        got = await start(p, k, completion="hold")
        out = Output("u://1", "video/mp4", 0, "up-0")
        await submit(p, got, engine_with(sent(SubmitResult(Completed((out,), Facts(5, "veo sessionid=" + LEAK))))))
        return got

    got = run(db, scenario)
    assert LEAK not in cols(db, got.job_id, "upstream_model")[0]
