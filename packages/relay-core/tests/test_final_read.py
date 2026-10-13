"""The final read before the render deadline fails a sent job (phase 7 step 1a), and the stats indexes (step 1b).

A `waiting` job past its deadline fails only after a poll made after the deadline that the engine ANSWERED said "not
ready"; an `unknown` job only after a reconcile that answered found nothing; a read that failed or ran out of time is
not an answer; `RENDER_BACKSTOP_S` after the reaper first saw the job late it fails whatever happened (not within
`STARTUP_GRACE_S` of a worker starting). A job found by the final read is
delivered (one account charge, no refund) and its download keeps the normal retries for `FETCH_WINDOW_S`."""

import asyncio
import dataclasses
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jobkit import CAPS, POOLS, PROVIDER, account, claim, key, run, status, submit
from lifecyclekit import (
    MediaEngine,
    age,
    age_found,
    answered_ago,
    due,
    fetcher,
    late_seen,
    pass_once,
    poller,
    reaper,
    reconciler,
    row,
    to_fetching,
    to_unknown,
    to_waiting,
)

from relay_core import failpoints
from relay_core.jobs import ops, recheck, store
from relay_core.jobs.apply import StartupGrace
from relay_core.providers import Done, Found, NotSent, Pending, PollResult, StillUnknown
from relay_core.storage import ObjectStore
from relay_core.testing.fake_provider import FakeEngine, FakeSpec
from relay_core.testing.mutation import mutation

REAPER = "packages/relay-core/src/relay_core/jobs/reaper.py"
APPLY = "packages/relay-core/src/relay_core/jobs/apply.py"
CLAIM = "packages/relay-core/src/relay_core/jobs/claim.py"
OPS = "packages/relay-core/src/relay_core/jobs/ops.py"
FETCHER = "packages/relay-core/src/relay_core/jobs/fetcher.py"
POLLER = "packages/relay-core/src/relay_core/jobs/poller.py"
PROVIDERS = "packages/relay-core/src/relay_core/providers.py"
M0018 = "packages/relay-core/src/relay_core/db/migrations/0018_stats_indexes.sql"
M0017 = "packages/relay-core/src/relay_core/db/migrations/0017_job_answered_at.sql"

LATE = 9_100                                    # past the 9000 s render deadline of the fake provider
VERY_LATE = 9_000 + 3_600 + 100                # past the deadline AND the backstop (an hour), as literals

HELD = "a late job was failed before its final read"


@pytest.fixture
def objects():
    """A store on a bucket of its own, emptied and removed afterwards."""
    endpoint = os.environ.get("RELAY_TEST_MINIO", "")
    if not endpoint:
        raise RuntimeError("RELAY_TEST_MINIO is not set: run the tests through scripts/check.sh")
    s = ObjectStore(endpoint, os.environ["RELAY_TEST_MINIO_ACCESS_KEY"], os.environ["RELAY_TEST_MINIO_SECRET_KEY"],
                    bucket="relay-test-" + uuid.uuid4().hex[:12])
    asyncio.run(s.ensure_bucket())
    yield s
    for o in s._client.list_objects(s.bucket, recursive=True):
        s._client.remove_object(s.bucket, o.object_name)
    s._client.remove_bucket(s.bucket)


def refunds(db, job_id) -> int:
    return db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'refund'", job_id)[0]


def charges(db, job_id) -> tuple[int, int]:
    """(account charges, of them refunded)."""
    return db.one("SELECT count(*), count(refunded_at) FROM account_charges WHERE job_id = %s", job_id)


def events(db, job_id) -> list[str]:
    return [r[0] for r in db.all("SELECT text FROM job_events WHERE job_id = %s ORDER BY id", job_id)]


async def poll_once(p, db, job_id, engine, **kw) -> None:
    due(db, job_id)
    await pass_once(poller(p, engine, **kw))


async def reconcile_once(p, db, job_id, engine) -> None:
    due(db, job_id)
    await pass_once(reconciler(p, engine))


# ---- a waiting job ------------------------------------------------------------------------------------------------
@mutation(REAPER, "report=True, final_read=True)", "report=True, final_read=False)", expect=HELD)
def test_a_waiting_job_past_its_deadline_is_not_failed_before_a_poll_made_after_it(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", LATE, answered=False)
        return job_id, await reaper(p).tick()

    job_id, done = run(db, scenario)
    assert done["render_deadline"] == 0 and status(db, job_id) == "waiting", HELD
    assert refunds(db, job_id) == 0


@mutation(REAPER, "OR NOT (status = 'waiting' AND {poll} OR", "OR NOT (status = 'waiting' AND true OR",
          expect="a job no read will reach was held")
@mutation(REAPER, "AND {reconcile} AND after_sent = 'reconcile'", "AND true AND after_sent = 'reconcile'",
          expect="a job no read will reach was held")
def test_a_job_that_no_read_will_reach_fails_at_the_deadline_as_before(db):
    k, _ = key(db), account(db, left=100)
    spec = FakeSpec()
    spec.caps = dataclasses.replace(spec.caps, has_poll=False, has_reconcile=False)

    async def scenario(p):
        waiting = await to_waiting(p, k, prompt="w")
        unknown = await to_unknown(p, k, prompt="u")
        age(db, waiting, "render_since", LATE, answered=False)
        age(db, unknown, "sent_at", LATE, answered=False)
        return waiting, unknown, await reaper(p, spec=spec).tick()

    waiting, unknown, done = run(db, scenario)
    assert (status(db, waiting), status(db, unknown)) == ("failed", "failed"), "a job no read will reach was held"
    assert done["render_deadline"] == 2


def test_the_live_scenario_a_late_video_is_found_by_the_final_poll_and_delivered_with_one_charge(db, objects):
    """Accepted, the worker down past the deadline, the worker restarts: the reaper's first pass finds the job late and
    leaves it, the poller reads it, finds the video, the fetcher delivers it. One account charge, no refund."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", LATE, answered=False)
        answered_ago(db, job_id, 9_500)                              # polled every 30 s until the worker went down
        engine = MediaEngine()
        first_pass = await reaper(p).tick()                         # the restart: the reaper runs before the poller
        await poll_once(p, db, job_id, engine)
        await pass_once(fetcher(p, engine, objects))
        second_pass = await reaper(p).tick()
        return job_id, first_pass, second_pass

    job_id, first_pass, second_pass = run(db, scenario)
    assert first_pass["render_deadline"] == 0 and second_pass["render_deadline"] == 0, HELD
    assert status(db, job_id) == "done", "the late video was not delivered"
    assert charges(db, job_id) == (1, 0) and refunds(db, job_id) == 0


@mutation(PROVIDERS, "    answered: bool = True\n", "    answered: bool = False\n",
          expect="a Pending that did not say otherwise was not the final read")
def test_a_poll_that_answered_not_ready_after_the_deadline_fails_the_job_with_the_report_and_the_refund(db):
    k, _ = key(db), account(db, left=100)
    spec = FakeSpec(unclear="the reply that explains nothing")

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", LATE, answered=False)
        engine = FakeEngine()
        engine.script_poll("*", PollResult(Pending("still rendering")))
        await poll_once(p, db, job_id, engine)
        stamped = row(db, job_id, "answered_at IS NOT NULL")[0]
        return job_id, stamped, await reaper(p, spec=spec).tick()

    job_id, stamped, done = run(db, scenario)
    assert stamped and done["render_deadline"] == 1, "a Pending that did not say otherwise was not the final read"
    assert row(db, job_id, "status, error_code") == ("failed", "render.deadline")
    assert refunds(db, job_id) == 1 and db.one("SELECT count(*) FROM unclear_replies WHERE job_id = %s", job_id) == (1,)


@mutation(POLLER, "PollResult(Pending(\"the poll did not answer\", self.error_retry_s, {}, False))",
          "PollResult(Pending(\"the poll did not answer\", self.error_retry_s, {}, True))",
          expect="a poll that did not answer was taken for the final read")
def test_a_poll_that_did_not_answer_after_the_deadline_is_not_the_final_read_and_the_backstop_ends_it(db):
    k, _ = key(db), account(db, left=100)

    async def boom():
        raise ConnectionError("the proxy is down")

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", LATE, answered=False)
        failpoints.arm("fake.poll", boom)
        await poll_once(p, db, job_id, FakeEngine())                 # the engine raised: no answer
        failpoints.disarm()
        engine = FakeEngine()
        engine.script_poll("*", PollResult(Pending("poll failed", 30, {}, False)))   # a Pending that says it failed
        await poll_once(p, db, job_id, engine)
        held = (row(db, job_id, "status, answered_at IS NULL"), (await reaper(p).tick())["render_deadline"])
        age(db, job_id, "render_since", 20_000, answered=False)
        late_seen(db, job_id, 3_700)                                 # a worker saw it late an hour ago
        return job_id, held, (await reaper(p).tick())["render_deadline"]

    job_id, held, at_backstop = run(db, scenario)
    assert held == (("waiting", True), 0), "a poll that did not answer was taken for the final read"
    assert at_backstop == 1 and row(db, job_id, "status, error_code") == ("failed", "render.deadline")
    assert refunds(db, job_id) == 1


@mutation(REAPER, 'RENDER_FROM = "COALESCE(render_since, sent_at)"', 'RENDER_FROM = "created_at"',
          expect="the clock restarted")
def test_a_recheck_across_the_deadline_restarts_the_clock_even_for_a_job_the_backstop_would_end(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "created_at", 30_000)
        age(db, job_id, "render_since", 20_000, answered=False)
        late_seen(db, job_id, 3_700)                                 # without the Re-check the backstop ends it
        await recheck.recheck_job(p, job_id)                         # the clock restarts now
        return job_id, (await reaper(p).tick())["render_deadline"]

    job_id, done = run(db, scenario)
    assert done == 0 and status(db, job_id) == "waiting", "the clock restarted; the job must survive"


# ---- an unknown job -----------------------------------------------------------------------------------------------
@mutation(CLAIM, "AND (j.answered_at IS NULL", "AND (j.answered_at IS NOT NULL AND FALSE",
          expect="the final reconcile was not made")
def test_the_final_reconcile_finds_the_order_and_the_job_is_delivered_with_one_charge(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", LATE, answered=False)
        engine = MediaEngine()
        engine.script_reconcile("*", Found("ref-1", {}, taken=True))
        held = (await reaper(p).tick())["render_deadline"]            # still waiting for its final reconcile
        await reconcile_once(p, db, job_id, engine)
        found = row(db, job_id, "status, answered_at IS NULL")
        still_held = (await reaper(p).tick())["render_deadline"]      # a found order needs a poll after the deadline
        await poll_once(p, db, job_id, engine)
        await pass_once(fetcher(p, engine, objects))
        return job_id, held, found, still_held

    job_id, held, found, still_held = run(db, scenario)
    assert held == 0, HELD
    assert found == ("waiting", True), "the final reconcile was not made"
    assert still_held == 0 and status(db, job_id) == "done"
    assert charges(db, job_id) == (1, 0) and refunds(db, job_id) == 0


@mutation(APPLY, "provider_state=_merge(result.provider_state), answered_at=None)",
          "provider_state=_merge(result.provider_state))", expect="a found order kept its answer")
def test_a_found_order_forgets_an_answered_read_so_that_a_poll_after_the_deadline_is_still_needed(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", LATE)                              # past the deadline, answered just now
        await store.apply_reconciled(p, job_id, Found("ref-1", {}, taken=True), read_started_at=_now(db), caps=CAPS,
                                     pools=POOLS, render_deadline_s=9000)
        return job_id, row(db, job_id, "status, answered_at IS NULL"), (await reaper(p).tick())["render_deadline"]

    job_id, found, done = run(db, scenario)
    assert found == ("waiting", True), "a found order kept its answer"
    assert done == 0 and status(db, job_id) == "waiting", HELD


def _now(db):
    return db.one("SELECT clock_timestamp()")[0]


@mutation(PROVIDERS, "    answered: bool = False\n", "    answered: bool = True\n",
          expect="a StillUnknown that did not say it answered was the final read")
def test_a_final_reconcile_that_found_nothing_fails_the_job_and_one_that_failed_does_not(db):
    k, _ = key(db), account(db, left=100)

    async def boom():
        raise ConnectionError("the browser is gone")

    async def scenario(p):
        a = await to_unknown(p, k, prompt="a")
        b = await to_unknown(p, k, prompt="b")
        c = await to_unknown(p, k, prompt="c")
        for job_id in (a, b, c):
            age(db, job_id, "sent_at", LATE, answered=False)
        engine = FakeEngine()
        engine.script_reconcile(str(a), StillUnknown(answered=True))      # it read and found nothing
        engine.script_reconcile(str(b), StillUnknown())                   # it did not say it answered
        await reconcile_once(p, db, a, engine)
        await reconcile_once(p, db, b, engine)
        failpoints.arm("fake.reconcile", boom)
        await reconcile_once(p, db, c, FakeEngine())                      # the read itself raised
        failpoints.disarm()
        return a, b, c, await reaper(p).tick()

    a, b, c, done = run(db, scenario)
    assert row(db, a, "status, error_code") == ("failed", "render.deadline") and refunds(db, a) == 1
    assert (status(db, b), status(db, c)) == ("unknown", "unknown"), \
        "a StillUnknown that did not say it answered was the final read"
    assert done["render_deadline"] == 1


@mutation(CLAIM, "\"backstop\": RENDER_BACKSTOP_S", "\"backstop\": 100 * RENDER_BACKSTOP_S",
          expect="a job past the backstop was read again")
def test_an_unknown_job_past_the_backstop_is_not_read_again_and_the_reaper_fails_it(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", 20_000, answered=False)
        late_seen(db, job_id, 3_700)
        engine = FakeEngine()
        await reconcile_once(p, db, job_id, engine)
        return job_id, engine.calls, await reaper(p).tick()

    job_id, calls, done = run(db, scenario)
    assert [c for c in calls if c[0] == "reconcile"] == [], "a job past the backstop was read again"
    assert done["render_deadline"] == 1 and refunds(db, job_id) == 1


@mutation(APPLY, "if proven_late and proven_covered and late:", "if False:",
          expect="a proven NotSent past the deadline was queued for a re-send")
def test_a_proven_not_sent_after_the_deadline_is_failed_with_a_refund_and_never_sent_again(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", LATE, answered=False)
        engine = FakeEngine()
        engine.script_reconcile("*", NotSent(True))
        await reconcile_once(p, db, job_id, engine)
        after_read = row(db, job_id, "status, sent_at IS NOT NULL, attempts")
        await reaper(p).tick()
        again = FakeEngine()
        await pass_once(reconciler(p, again))
        got = await claim(p)                                         # a re-queued job would be claimed here ...
        if got is not None:
            await submit(p, got, again)                              # ... and sent a second time
        return job_id, after_read, again.calls, engine.calls

    job_id, after_read, again_calls, calls = run(db, scenario)
    assert after_read == ("unknown", True, 0), "a proven NotSent past the deadline was queued for a re-send"
    assert row(db, job_id, "status, error_code") == ("failed", "render.deadline") and refunds(db, job_id) == 1
    assert not [c for c in calls + again_calls if c[0] == "submit"], "the order was sent twice"
    assert any("not sent again" in text for text in events(db, job_id))


# ---- the download window ------------------------------------------------------------------------------------------
@mutation(APPLY, "FETCH_WINDOW_S = 1800 ", "FETCH_WINDOW_S = 0 ", expect="a video found after the deadline failed")
@mutation(APPLY, "if found_at is not None and found_at >= due_at", "if False",
          expect="a video found after the deadline failed")
def test_a_video_found_after_the_deadline_keeps_its_download_retries_for_the_window_then_fails(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_fetching(p, k)                              # found now ...
        age(db, job_id, "render_since", 9_000 + 4_000)                 # ... long after the deadline
        results = []
        for found_ago in (0, 1_800 + 60):
            if found_ago:
                age_found(db, job_id, found_ago)
            due(db, job_id)
            claimed = await store.claim_visible(p, provider=PROVIDER, status="fetching", visibility_s=900, limit=4)
            if not claimed:                                           # already failed: nothing left to retry
                break
            results.append(await store.apply_fetch_failed(
                p, job_id, claimed[0].token, code="render.download_failed", detail="502", retry_in_s=1,
                deadline_s=9000, pools=POOLS))
        return job_id, results

    job_id, results = run(db, scenario)
    assert results == ["fetching", "failed"], "a video found after the deadline failed: %r" % (results,)
    assert refunds(db, job_id) == 1


# ---- the migrations -----------------------------------------------------------------------------------------------
IDX_ACCOUNT = "CREATE INDEX jobs_account_idx ON jobs (account_id);"
IDX_CREATED = "CREATE INDEX jobs_created_at_idx ON jobs (created_at);"
IDX_CHARGES = "CREATE INDEX account_charges_account_pool_day_idx ON account_charges (account_id, pool, account_day);"
INDEXES = ("jobs_account_idx", "jobs_created_at_idx", "account_charges_account_pool_day_idx")


@mutation(M0018, IDX_ACCOUNT, "", expect="jobs_account_idx is missing")
@mutation(M0018, IDX_CREATED, "", expect="jobs_created_at_idx is missing")
@mutation(M0018, IDX_CHARGES, "", expect="account_charges_account_pool_day_idx is missing")
def test_the_stats_indexes_exist_on_the_columns_the_stats_read(db):
    found = {name for (name,) in db.all("SELECT indexname FROM pg_indexes WHERE schemaname = 'relay'")}
    for name in INDEXES:
        assert name in found, "%s is missing" % name
    definition = db.one("SELECT indexdef FROM pg_indexes WHERE schemaname = 'relay' "
                        "AND indexname = 'account_charges_account_pool_day_idx'")[0]
    assert "(account_id, pool, account_day)" in definition


COLUMNS = "ALTER TABLE jobs ADD COLUMN answered_at timestamptz NULL, ADD COLUMN late_seen_at timestamptz NULL;"


@mutation(M0017, COLUMNS, "ALTER TABLE jobs ADD COLUMN late_seen_at timestamptz NULL;", expect="answered_at is missing")
@mutation(M0017, COLUMNS, "ALTER TABLE jobs ADD COLUMN answered_at timestamptz NULL;", expect="late_seen_at is missing")
@mutation(M0017, "ALTER TABLE jobs ADD COLUMN found_late_at timestamptz NULL;", "", expect="found_late_at is missing")
def test_the_read_columns_are_nullable_and_both_service_roles_can_write_them(db):
    for column in ("answered_at", "late_seen_at", "found_late_at"):
        assert db.one("SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'relay' "
                      "AND table_name = 'jobs' AND column_name = %s", column) == ("YES",), column + " is missing"
        for role in ("relay_api", "relay_worker"):
            assert db.one("SELECT has_column_privilege(%s, 'relay.jobs', %s, 'UPDATE')", role,
                          column) == (True,), role


# ---- fix round 1: the backstop counts from when a running worker first saw the job late ---------------------------
SEVEN_HOURS = 27_720                    # the 12/10 incident: a job 7.7 h old with a 9000 s deadline
BEFORE_DEADLINE = SEVEN_HOURS - 9_000 + 300     # a read that started 300 s before the deadline, as every live job has
BACKSTOP_FROM_DEADLINE = "AND late_seen_at + make_interval(secs => %s) <= clock_timestamp())"
BACKSTOP_FROM_RENDER = "AND COALESCE(render_since, sent_at) + make_interval(secs => %s) <= clock_timestamp())"
STAMP = "UPDATE jobs SET late_seen_at = clock_timestamp(),"
NO_STAMP = "UPDATE jobs SET late_seen_at = late_seen_at,"
HELD_7H = "the 12/10 job was failed before its final read"


def late_seen_at(db, job_id):
    return db.one("SELECT late_seen_at FROM jobs WHERE id = %s", job_id)[0]


@mutation(REAPER, BACKSTOP_FROM_DEADLINE, BACKSTOP_FROM_RENDER, expect=HELD_7H)
@mutation(REAPER, STAMP, NO_STAMP, expect="the reaper did not stamp late_seen_at")
def test_the_1210_timeline_a_waiting_job_seven_hours_late_is_read_first_and_delivered(db, objects):
    """Accepted, polled until the worker went down, the worker back 7.7 h after acceptance (long past the deadline AND
    the hour after it): the first reaper pass holds the job, the poller reads it, the video is delivered."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", SEVEN_HOURS, answered=False)
        answered_ago(db, job_id, BEFORE_DEADLINE)
        engine = MediaEngine()
        first_pass = await reaper(p).tick()
        stamped = late_seen_at(db, job_id) is not None
        await poll_once(p, db, job_id, engine)
        await pass_once(fetcher(p, engine, objects))
        return job_id, first_pass, stamped

    job_id, first_pass, stamped = run(db, scenario)
    assert stamped, "the reaper did not stamp late_seen_at"
    assert first_pass["render_deadline"] == 0, HELD_7H
    assert status(db, job_id) == "done" and charges(db, job_id) == (1, 0) and refunds(db, job_id) == 0


@mutation(REAPER, BACKSTOP_FROM_DEADLINE, BACKSTOP_FROM_RENDER, expect=HELD_7H)
def test_the_1210_timeline_an_unknown_job_seven_hours_late_is_reconciled_first_and_delivered(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", SEVEN_HOURS, answered=False)
        answered_ago(db, job_id, BEFORE_DEADLINE)                     # an earlier routine reconcile
        engine = MediaEngine()
        engine.script_reconcile("*", Found("ref-1", {}, taken=True))
        first_pass = await reaper(p).tick()
        await reconcile_once(p, db, job_id, engine)
        second_pass = await reaper(p).tick()                          # a found order still needs a poll
        await poll_once(p, db, job_id, engine)
        await pass_once(fetcher(p, engine, objects))
        return job_id, first_pass, second_pass

    job_id, first_pass, second_pass = run(db, scenario)
    assert first_pass["render_deadline"] == 0 and second_pass["render_deadline"] == 0, HELD_7H
    assert status(db, job_id) == "done" and charges(db, job_id) == (1, 0) and refunds(db, job_id) == 0


@mutation(APPLY, "RENDER_BACKSTOP_S = 3600 ", "RENDER_BACKSTOP_S = 3600000 ",
          expect="a job whose reads never answer was not failed one hour after it was seen late")
def test_a_job_whose_reads_never_answer_fails_one_hour_after_it_was_first_seen_late(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", SEVEN_HOURS, answered=False)
        first = (await reaper(p).tick())["render_deadline"]
        late_seen(db, job_id, 3_500)
        before = (await reaper(p).tick())["render_deadline"]
        late_seen(db, job_id, 3_700)
        return job_id, first, before, (await reaper(p).tick())["render_deadline"]

    job_id, first, before, after = run(db, scenario)
    assert (first, before) == (0, 0), "failed before the backstop"
    assert after == 1 and status(db, job_id) == "failed", \
        "a job whose reads never answer was not failed one hour after it was seen late"
    assert refunds(db, job_id) == 1


@mutation(APPLY, "seen_at if seen_at is not None and seen_at >= due_at else None", "None",
          expect="a download that began during an outage failed at once")
@mutation(REAPER, "status IN ('waiting', 'unknown', 'fetching') AND sent_at IS NOT NULL",
          "status IN ('waiting', 'unknown') AND sent_at IS NOT NULL", expect="a fetching job was not stamped")
def test_a_video_found_before_the_deadline_but_not_yet_downloaded_keeps_its_retries_from_the_first_late_pass(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_fetching(p, k)
        age(db, job_id, "render_since", SEVEN_HOURS)
        age_found(db, job_id, SEVEN_HOURS)                            # found long before the deadline
        await reaper(p).tick()
        stamped = late_seen_at(db, job_id) is not None
        results = []
        for seen_ago in (0, 1_860):
            if seen_ago:
                late_seen(db, job_id, seen_ago)
            due(db, job_id)
            claimed = await store.claim_visible(p, provider=PROVIDER, status="fetching", visibility_s=900, limit=4)
            if not claimed:
                break
            results.append(await store.apply_fetch_failed(
                p, job_id, claimed[0].token, code="render.download_failed", detail="502", retry_in_s=1,
                deadline_s=9000, pools=POOLS))
        return job_id, stamped, results

    job_id, stamped, results = run(db, scenario)
    assert stamped, "a fetching job was not stamped"
    assert results == ["fetching", "failed"], "a download that began during an outage failed at once: %r" % (results,)


# ---- stamping ----------------------------------------------------------------------------------------------------
LOWER = "THEN LEAST(COALESCE(next_poll_at, clock_timestamp()), clock_timestamp())"
KEEP_LOW = "THEN next_poll_at"
RESTAMP = "late_seen_at <= %(r)s + make_interval(secs => %%s))"
NEVER_RESTAMP = "late_seen_at <= %(r)s + make_interval(secs => %%s) - interval '100 years')"
LEASE_SAFE_STAMP = "OR status = 'unknown' AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.job_id = jobs.id"
LEASE_BLIND_STAMP = "OR status = 'unknown' AND TRUE OR NOT EXISTS (SELECT 1 FROM leases l WHERE l.job_id = jobs.id"


@mutation(REAPER, LOWER, KEEP_LOW, expect="the first late pass did not make the read due")
def test_the_first_late_pass_makes_the_next_read_of_a_waiting_or_unknown_job_due_now(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        waiting = await to_waiting(p, k, prompt="w")
        unknown = await to_unknown(p, k, prompt="u")
        age(db, waiting, "render_since", LATE, answered=False)
        age(db, unknown, "sent_at", LATE, answered=False)
        db.run("UPDATE jobs SET next_poll_at = clock_timestamp() + interval '25 minutes' WHERE id = ANY(%s)",
               [waiting, unknown])
        await reaper(p).tick()
        return db.all("SELECT next_poll_at <= clock_timestamp() FROM jobs WHERE id = ANY(%s)", [waiting, unknown])

    assert run(db, scenario) == [(True,), (True,)], "the first late pass did not make the read due"


@mutation(REAPER, RESTAMP, NEVER_RESTAMP, expect="a stamp from before the new deadline was kept")
def test_a_stamp_from_before_a_recheck_moved_the_deadline_is_stamped_again(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", LATE, answered=False)         # the deadline passed 100 s ago
        late_seen(db, job_id, 5_000)                                  # the stamp is from a clock before the Re-check
        await reaper(p).tick()
        return db.one("SELECT late_seen_at > clock_timestamp() - interval '60 seconds' FROM jobs WHERE id = %s",
                      job_id)

    assert run(db, scenario) == (True,), "a stamp from before the new deadline was kept"


# ---- a read in flight is never cut -------------------------------------------------------------------------------
NO_POLL_HOLD = "AND NOT (status = 'waiting' AND poll_token IS NOT NULL AND next_poll_at > clock_timestamp()"
NO_POLL_HOLD_MUT = "AND NOT (FALSE AND poll_token IS NOT NULL AND next_poll_at > clock_timestamp()"
LEASE_HOLD = "OR status = 'unknown' AND EXISTS (SELECT 1 FROM leases l WHERE l.job_id = jobs.id"
NO_LEASE_HOLD = "OR status = 'unknown' AND FALSE AND EXISTS (SELECT 1 FROM leases l WHERE l.job_id = jobs.id"


@mutation(REAPER, NO_POLL_HOLD, NO_POLL_HOLD_MUT, expect="an in-flight poll was cut")
def test_a_poll_in_flight_is_not_cut_by_the_reaper_and_its_video_is_delivered(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", LATE)                         # late, with an answered read: it would be failed
        due(db, job_id)
        [vis] = await store.claim_visible(p, provider=PROVIDER, status="waiting", visibility_s=300, limit=4)
        done = (await reaper(p).tick())["render_deadline"]            # the poll is out reading
        outs = FakeEngine().outputs(await store.job_view(p, job_id), 1)
        landed = await store.apply_poll(p, job_id, vis.token, PollResult(Done(outs)), caps=CAPS, pools=POOLS,
                                        read_started_at=vis.claimed_at)
        return job_id, done, landed

    job_id, done, landed = run(db, scenario)
    assert done == 0 and landed == "fetching", "an in-flight poll was cut"
    assert refunds(db, job_id) == 0 and charges(db, job_id) == (1, 0)


FOUND_RESTARTS_HOUR = 'cols["late_seen_at"] = None'
FOUND_KEEPS_STAMP = "pass"
DEADLINE_LONG_AGO = 14_000                                         # the 9000 s deadline passed 5000 s ago
STAMP_OK = "found order was failed before its first poll"


async def land_found_past_the_backstop(p, db, job_id, claim_):
    """The reconcile claimed in time is still out reading when the hour passes; its Found lands after it."""
    late_seen(db, job_id, 3_700)
    held = (await reaper(p).tick())["render_deadline"]                # the reconcile is out reading
    landed = await store.apply_reconciled(
        p, job_id, Found("ref-1", {}, taken=True), read_started_at=claim_.read_started_at, caps=CAPS, pools=POOLS,
        lease_id=claim_.lease_id, release_lease=True, render_deadline_s=9000)
    return held, landed


async def deliver_after_found(p, db, job_id):
    """The reaper's next pass, then the poll that finds the video: the job must end in `fetching`."""
    next_pass = (await reaper(p).tick())["render_deadline"]
    due_now = db.one("SELECT next_poll_at <= clock_timestamp() FROM jobs WHERE id = %s", job_id)
    claimed = await store.claim_visible(p, provider=PROVIDER, status="waiting", visibility_s=300, limit=4)
    if not claimed:
        return next_pass, due_now, "nothing to poll"                 # it was failed first
    [vis] = claimed
    outs = FakeEngine().outputs(await store.job_view(p, job_id), 1)
    ended = await store.apply_poll(p, job_id, vis.token, PollResult(Done(outs)), caps=CAPS, pools=POOLS,
                                   read_started_at=vis.claimed_at)
    return next_pass, due_now, ended


@mutation(REAPER, LEASE_HOLD, NO_LEASE_HOLD, expect="an in-flight reconcile was cut")
@mutation(REAPER, LEASE_SAFE_STAMP, LEASE_BLIND_STAMP, expect="the stamp lowered the time of a read in flight")
@mutation(APPLY, FOUND_RESTARTS_HOUR, FOUND_KEEPS_STAMP, expect=STAMP_OK)
def test_a_reconcile_in_flight_is_not_cut_by_the_reaper_and_its_found_order_is_kept(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", DEADLINE_LONG_AGO, answered=False)
        due(db, job_id)
        claim_ = await store.claim_reconcile(p, provider=PROVIDER, caps=CAPS, deadline_s=9000, owner="w1")
        assert claim_ is not None and claim_.job_id == job_id
        db.run("UPDATE jobs SET answered_at = clock_timestamp() WHERE id = %s", job_id)   # the reaper would fail it
        done = (await reaper(p).tick())["render_deadline"]            # the reconcile is out reading
        hidden = db.one("SELECT next_poll_at > clock_timestamp() FROM jobs WHERE id = %s", job_id)
        held, landed = await land_found_past_the_backstop(p, db, job_id, claim_)
        return job_id, done, hidden, held, landed, await deliver_after_found(p, db, job_id)

    job_id, done, hidden, held, landed, (next_pass, due_now, ended) = run(db, scenario)
    assert done == 0 and held == 0 and landed == "waiting", "an in-flight reconcile was cut"
    assert hidden == (True,), "the stamp lowered the time of a read in flight"
    assert next_pass == 0 and due_now == (True,) and ended == "fetching", "a " + STAMP_OK
    assert refunds(db, job_id) == 0 and charges(db, job_id) == (1, 0)


@mutation(APPLY, FOUND_RESTARTS_HOUR, FOUND_KEEPS_STAMP, expect=STAMP_OK)
def test_an_order_found_in_the_last_seconds_before_the_backstop_is_polled_before_it_can_be_failed(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", DEADLINE_LONG_AGO, answered=False)
        late_seen(db, job_id, 3_000)
        due(db, job_id)
        claim_ = await store.claim_reconcile(p, provider=PROVIDER, caps=CAPS, deadline_s=9000, owner="w1")
        assert claim_ is not None
        late_seen(db, job_id, 3_590)                                  # the Found lands 10 s before the backstop
        landed = await store.apply_reconciled(
            p, job_id, Found("ref-1", {}, taken=True), read_started_at=claim_.read_started_at, caps=CAPS, pools=POOLS,
            lease_id=claim_.lease_id, release_lease=True, render_deadline_s=9000)
        db.run("UPDATE jobs SET late_seen_at = late_seen_at - interval '60 seconds' WHERE id = %s", job_id)
        # ^ the next pass is a minute later: a kept stamp is now past the hour, a cleared one stays NULL
        return job_id, landed, await deliver_after_found(p, db, job_id)

    job_id, landed, (next_pass, due_now, ended) = run(db, scenario)
    assert landed == "waiting" and next_pass == 0 and due_now == (True,) and ended == "fetching", "a " + STAMP_OK
    assert refunds(db, job_id) == 0 and charges(db, job_id) == (1, 0)


# ---- only a read that STARTED after the deadline counts ------------------------------------------------------------
async def let_the_deadline_pass() -> None:
    """The deadline (2 s away when the read was claimed) passes while the read is out."""
    await asyncio.sleep(2.5)


@mutation(POLLER, "pools=self._pools(), read_started_at=c.claimed_at))", "pools=self._pools()))",
          expect="a poll that started before the deadline was taken for the final read")
@mutation(APPLY, "(read_started_at,))) if out.answered else {}", "(None,))) if out.answered else {}",
          expect="a poll that started before the deadline was taken for the final read")
def test_a_poll_that_started_before_the_deadline_and_was_applied_after_it_is_not_the_final_read(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", 9_000 - 2, answered=False)    # the deadline is 2 s away
        failpoints.arm("fake.poll", let_the_deadline_pass)
        engine = FakeEngine()
        engine.script_poll("*", PollResult(Pending("still rendering")))
        await poll_once(p, db, job_id, engine)
        failpoints.disarm()
        return job_id, (await reaper(p).tick())["render_deadline"]

    job_id, done = run(db, scenario)
    assert done == 0 and status(db, job_id) == "waiting", \
        "a poll that started before the deadline was taken for the final read"


@mutation(APPLY, "answered_at=read_started_at if result.answered else None",
          "answered_at=_NOW if result.answered else None",
          expect="a reconcile that started before the deadline was taken for the final read")
def test_a_reconcile_that_started_before_the_deadline_and_was_applied_after_it_is_not_the_final_read(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", 9_000 - 2, answered=False)
        failpoints.arm("fake.reconcile", let_the_deadline_pass)
        engine = FakeEngine()
        engine.script_reconcile("*", StillUnknown(answered=True))
        await reconcile_once(p, db, job_id, engine)
        failpoints.disarm()
        return job_id, (await reaper(p).tick())["render_deadline"]

    job_id, done = run(db, scenario)
    assert done == 0 and status(db, job_id) == "unknown", \
        "a reconcile that started before the deadline was taken for the final read"


# ---- a read from before the deadline (every live job has one) is not the final read -------------------------------
ANY_ANSWER = "AND (answered_at > {render} + make_interval(secs => %s)"
ANY_ANSWER_MUT = "AND (answered_at > {render} + make_interval(secs => %s) - interval '100 days'"
CLAIM_BEFORE_BRANCH = ("                 OR j.answered_at <= COALESCE(j.render_since, j.sent_at) "
                       "+ make_interval(secs => %(deadline)s))))")
CLAIM_NO_BEFORE_BRANCH = "                 OR FALSE)))"
CLAIM_NULL_ONLY = "            AND (j.answered_at IS NULL"
CLAIM_ALWAYS = "            AND (TRUE"


@mutation(REAPER, ANY_ANSWER, ANY_ANSWER_MUT, expect="a poll from before the deadline was taken for the final read")
def test_a_poll_from_before_the_deadline_is_not_the_final_read_a_fresh_one_after_it_is(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", LATE, answered=False)
        answered_ago(db, job_id, BEFORE_DEADLINE - 27_720 + LATE + 200)     # polled 200 s before the deadline
        before = (await reaper(p).tick())["render_deadline"]
        engine = FakeEngine()
        engine.script_poll("*", PollResult(Pending("still rendering")))
        await poll_once(p, db, job_id, engine)
        return job_id, before, (await reaper(p).tick())["render_deadline"]

    job_id, before, after = run(db, scenario)
    assert before == 0, "a poll from before the deadline was taken for the final read"
    assert after == 1 and status(db, job_id) == "failed"


@mutation(CLAIM, CLAIM_BEFORE_BRANCH, CLAIM_NO_BEFORE_BRANCH, expect="a reconcile from before the deadline blocked")
@mutation(CLAIM, CLAIM_NULL_ONLY, CLAIM_ALWAYS, expect="a job answered after its deadline was read again")
def test_a_reconcile_from_before_the_deadline_is_not_the_final_read_and_one_after_it_is_not_repeated(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        before_job = await to_unknown(p, k, prompt="b")
        after_job = await to_unknown(p, k, prompt="a")
        for job_id in (before_job, after_job):
            age(db, job_id, "sent_at", LATE, answered=False)
        answered_ago(db, before_job, LATE + 200)                       # a routine reconcile before the deadline
        db.run("UPDATE jobs SET answered_at = clock_timestamp() WHERE id = %s", after_job)   # the final one, done
        engine = FakeEngine()
        due(db, after_job)                                             # one account, one slot: one job per pass
        await pass_once(reconciler(p, engine))
        again = [c[1] for c in engine.calls if c[0] == "reconcile"]
        due(db, before_job)
        await pass_once(reconciler(p, engine))
        return before_job, after_job, again, [c[1] for c in engine.calls if c[0] == "reconcile"]

    before_job, after_job, again, read = run(db, scenario)
    assert again == [], "a job answered after its deadline was read again"
    assert str(before_job) in read, "a reconcile from before the deadline blocked the final one"


# ---- the final reconcile retries after five minutes ------------------------------------------------------------------
RETRY = "FINAL_RECONCILE_RETRY_S = 300 "
RETRY_SLOW = "FINAL_RECONCILE_RETRY_S = 1800 "
NOT_LATE_BACKOFF = "wait = FINAL_RECONCILE_RETRY_S if late else min(UNKNOWN_MAX_S, UNKNOWN_FIRST_S * 2 ** tried)"
ALWAYS_FINAL = "wait = FINAL_RECONCILE_RETRY_S"


@mutation(APPLY, RETRY, RETRY_SLOW, expect="the final reconcile retry is not five minutes")
@mutation(APPLY, NOT_LATE_BACKOFF, ALWAYS_FINAL, expect="a job before its deadline lost its back-off")
def test_a_final_reconcile_that_did_not_answer_is_tried_again_in_five_minutes_and_a_routine_one_backs_off(db):
    k, _ = key(db), account(db, left=100)

    async def next_in(p, job_id, engine) -> float:
        await reconcile_once(p, db, job_id, engine)
        return db.one("SELECT extract(epoch FROM next_poll_at - clock_timestamp()) FROM jobs WHERE id = %s",
                      job_id)[0]

    async def scenario(p):
        late_job = await to_unknown(p, k, prompt="l")
        early_job = await to_unknown(p, k, prompt="e")
        age(db, late_job, "sent_at", LATE, answered=False)
        engine = FakeEngine()
        engine.script_reconcile("*", StillUnknown())                   # did not say it answered
        return await next_in(p, late_job, engine), await next_in(p, early_job, engine)

    late_in, early_in = run(db, scenario)
    assert 240 < late_in <= 300, "the final reconcile retry is not five minutes: %.0f s" % late_in
    assert 40 < early_in <= 70, "a job before its deadline lost its back-off: %.0f s" % early_in


# ---- D41: a restore-marked job has no account and is failed at its deadline ---------------------------------------
@mutation(REAPER, "               AND account_id IS NOT NULL))", "               ))",
          expect="a restore-marked job with no account was held")
def test_a_restore_marked_job_with_no_account_still_fails_at_its_deadline(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        db.run("UPDATE jobs SET account_id = NULL WHERE id = %s", job_id)
        age(db, job_id, "sent_at", LATE, answered=False)
        return job_id, (await reaper(p).tick())["render_deadline"]

    job_id, done = run(db, scenario)
    assert done == 1 and row(db, job_id, "status, error_code") == ("failed", "render.deadline"), \
        "a restore-marked job with no account was held"
    assert refunds(db, job_id) == 1


# ---- the refused NotSent is an answered read; a missing deadline is not allowed -------------------------------------
REFUSED_ANSWERS = "answered_at=read_started_at, late=late)       # the read worked"
REFUSED_SILENT = "late=late)       # the read worked"


@mutation(APPLY, REFUSED_ANSWERS, REFUSED_SILENT, expect="a refused NotSent was not taken for an answered read")
def test_a_not_sent_that_proves_nothing_is_an_answered_read_whether_too_early_or_not_covering(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        uncovered = await to_unknown(p, k, prompt="u")
        too_early = await to_unknown(p, k, prompt="t")
        for job_id in (uncovered, too_early):
            age(db, job_id, "sent_at", LATE, answered=False)
        db.run("UPDATE jobs SET submit_timeout_s = 50000 WHERE id = %s", too_early)    # the read is "too early"
        engine = FakeEngine()
        engine.script_reconcile(str(uncovered), NotSent(False))
        engine.script_reconcile(str(too_early), NotSent(True))
        await reconcile_once(p, db, uncovered, engine)
        await reconcile_once(p, db, too_early, engine)
        stamped = db.all("SELECT answered_at IS NOT NULL FROM jobs WHERE id = ANY(%s)", [uncovered, too_early])
        return stamped, (await reaper(p).tick())["render_deadline"]

    stamped, done = run(db, scenario)
    assert stamped == [(True,), (True,)] and done == 2, "a refused NotSent was not taken for an answered read"


@mutation(APPLY, "release_lease: bool = False, render_deadline_s: float) -> str:",
          "release_lease: bool = False, render_deadline_s: float | None = None) -> str:",
          expect="a caller may leave out the render deadline")
def test_apply_reconciled_cannot_be_called_without_the_render_deadline(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", LATE, answered=False)
        try:
            await store.apply_reconciled(p, job_id, NotSent(True), read_started_at=_now(db), caps=CAPS, pools=POOLS)
        except TypeError:
            return job_id, "refused"
        return job_id, "accepted"

    job_id, outcome = run(db, scenario)
    assert outcome == "refused", "a caller may leave out the render deadline"


# ---- the fetch window anchor ---------------------------------------------------------------------------------------
SYSTEM_ONLY = "WHERE job_id = %s AND source = 'system' AND stage = 'completed'"
ANY_SOURCE = "WHERE job_id = %s AND stage = 'completed'"
NEWEST_FIND = "(SELECT max(at) FROM job_events"
OLDEST_FIND = "(SELECT min(at) FROM job_events"


@mutation(APPLY, SYSTEM_ONLY, ANY_SOURCE, expect="an engine event moved the window anchor")
def test_only_the_cores_own_completed_event_anchors_the_download_window(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_fetching(p, k)
        age(db, job_id, "render_since", SEVEN_HOURS)
        age_found(db, job_id, SEVEN_HOURS)                             # found long before the deadline
        late_seen(db, job_id, 1_900)                                   # seen late 31 min ago: the window is over
        db.run("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'upstream', 'completed', 'x')",
               job_id)
        due(db, job_id)
        [vis] = await store.claim_visible(p, provider=PROVIDER, status="fetching", visibility_s=900, limit=4)
        return await store.apply_fetch_failed(p, job_id, vis.token, code="render.download_failed", detail="502",
                                              retry_in_s=1, deadline_s=9000, pools=POOLS)

    assert run(db, scenario) == "failed", "an engine event moved the window anchor"


@mutation(APPLY, NEWEST_FIND, OLDEST_FIND, expect="a second find after a Re-check did not anchor the window")
def test_a_second_find_after_a_recheck_anchors_the_window_not_the_first(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_fetching(p, k)
        age(db, job_id, "render_since", 9_000 + 2_000)                 # the (new) deadline passed 2000 s ago
        age_found(db, job_id, 20_000)                                  # the first find, long before it
        db.run("INSERT INTO job_events (job_id, at, source, stage, text) VALUES "
               "(%s, clock_timestamp() - interval '1900 seconds', 'system', 'completed', 'again')", job_id)
        db.run("UPDATE jobs SET late_seen_at = clock_timestamp() WHERE id = %s", job_id)   # a restart just now
        due(db, job_id)
        [vis] = await store.claim_visible(p, provider=PROVIDER, status="fetching", visibility_s=900, limit=4)
        return await store.apply_fetch_failed(p, job_id, vis.token, code="render.download_failed", detail="502",
                                              retry_in_s=1, deadline_s=9000, pools=POOLS)

    assert run(db, scenario) == "failed", "a second find after a Re-check did not anchor the window"


# ---- after a late Found only a video or the backstop ends the job -------------------------------------------------
FOUND_LATE = 'cols["found_late_at"] = _NOW'
FOUND_NOT_LATE = "pass"
LATE_GUARD = "CASE WHEN found_late_at > COALESCE(render_since, sent_at) THEN answered_at "
LATE_GUARD_OFF = "CASE WHEN FALSE THEN answered_at "
STILL_RENDERING = "a still-rendering poll after a late Found ended the job"


async def late_found_job(p, db, k):
    """An unknown job 5000 s past its deadline whose final reconcile finds the order: waiting, stamped again."""
    job_id = await to_unknown(p, k)
    age(db, job_id, "sent_at", DEADLINE_LONG_AGO, answered=False)
    due(db, job_id)
    claim_ = await store.claim_reconcile(p, provider=PROVIDER, caps=CAPS, deadline_s=9000, owner="w1")
    assert claim_ is not None and claim_.job_id == job_id
    await store.apply_reconciled(
        p, job_id, Found("ref-1", {}, taken=True), read_started_at=claim_.read_started_at, caps=CAPS, pools=POOLS,
        lease_id=claim_.lease_id, release_lease=True, render_deadline_s=9000)
    await reaper(p).tick()                                            # stamps it again and makes the poll due
    return job_id


async def poll_result(p, db, job_id, result):
    due(db, job_id)
    claimed = await store.claim_visible(p, provider=PROVIDER, status="waiting", visibility_s=300, limit=4)
    if not claimed:
        return None                                                   # the job was ended before this poll
    return await store.apply_poll(p, job_id, claimed[0].token, PollResult(result), caps=CAPS, pools=POOLS,
                                  read_started_at=claimed[0].claimed_at)


@mutation(APPLY, FOUND_LATE, FOUND_NOT_LATE, expect=STILL_RENDERING)
@mutation(APPLY, LATE_GUARD, LATE_GUARD_OFF, expect=STILL_RENDERING)
def test_after_a_late_found_still_rendering_polls_do_not_end_the_job_and_the_video_is_delivered(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await late_found_job(p, db, k)
        held = []
        for _ in range(2):
            await poll_result(p, db, job_id, Pending("still rendering"))
            held.append((await reaper(p).tick())["render_deadline"])
        outs = FakeEngine().outputs(await store.job_view(p, job_id), 1)
        return job_id, held, await poll_result(p, db, job_id, Done(outs))

    job_id, held, ended = run(db, scenario)
    assert held == [0, 0], STILL_RENDERING
    assert ended == "fetching" and refunds(db, job_id) == 0 and charges(db, job_id) == (1, 0)


FOUND_RENDER_SINCE = 'cols["render_since"] = _Raw("COALESCE(render_since, sent_at)")'


@mutation(APPLY, FOUND_RENDER_SINCE, FOUND_NOT_LATE, expect="a found order has no render_since")
def test_a_found_order_is_judged_from_its_send_time_like_an_accepted_one(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        view = await store.job_view(p, await late_found_job(p, db, k))
        return view.render_since, view.sent_at

    since, sent = run(db, scenario)
    assert sent is not None and since == sent, "a found order has no render_since: its polls judge from nothing"


@mutation(APPLY, FOUND_LATE, FOUND_NOT_LATE, expect=STILL_RENDERING)
def test_after_a_late_found_a_job_that_keeps_rendering_fails_at_the_restarted_backstop_with_one_refund(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await late_found_job(p, db, k)
        await poll_result(p, db, job_id, Pending("still rendering"))
        early = (await reaper(p).tick())["render_deadline"]
        late_seen(db, job_id, 3_700)
        await poll_result(p, db, job_id, Pending("still rendering"))
        return job_id, early, (await reaper(p).tick())["render_deadline"]

    job_id, early, last = run(db, scenario)
    assert early == 0, STILL_RENDERING
    assert last == 1 and status(db, job_id) == "failed" and refunds(db, job_id) == 1
    assert charges(db, job_id) == (1, 1)


# ---- a restore starts the reads again -----------------------------------------------------------------------------
MARK_FORGETS = "late_seen_at = NULL, answered_at = NULL,"


@mutation(OPS, MARK_FORGETS, "", expect="a restored job was failed before it was read")
def test_a_job_that_was_late_at_dump_time_is_read_before_it_fails_after_a_restore(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", DEADLINE_LONG_AGO)           # answered_at is stamped by age()
        late_seen(db, job_id, 3_700)                                  # stamped long before the restore
        assert await ops.restore_mark(p, datetime.now(UTC) + timedelta(hours=1)) == 1
        first = (await reaper(p).tick())["render_deadline"]
        engine = FakeEngine()
        await reconcile_once(p, db, job_id, engine)                   # nothing found, but it answered
        return job_id, first, engine.calls, (await reaper(p).tick())["render_deadline"]

    job_id, first, calls, last = run(db, scenario)
    assert first == 0, "a restored job was failed before it was read"
    assert [c[0] for c in calls if c[0] == "reconcile"] == ["reconcile"] and last == 1
    assert refunds(db, job_id) == 1


# ---- the startup grace: a restarted stack reads first -------------------------------------------------------------
GRACE_SKIPS_BACKSTOP = "extra = (seconds, not self.grace, seconds, RENDER_BACKSTOP_S)"
GRACE_IGNORED = "extra = (seconds, True, seconds, RENDER_BACKSTOP_S)"
CLAIM_GRACE = '"grace": bool(grace), "n": 8}'
CLAIM_NO_GRACE = '"grace": False, "n": 8}'
FETCH_GRACE = "and not in_grace)"
FETCH_NO_GRACE = ")"
FETCHER_PASSES_GRACE = "in_grace=bool(self.grace)))"
FETCHER_DROPS_GRACE = "in_grace=False))"


@mutation(REAPER, GRACE_SKIPS_BACKSTOP, GRACE_IGNORED, expect="the backstop failed a job inside the startup grace")
def test_inside_the_startup_grace_the_backstop_fails_nothing_and_after_it_does(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", DEADLINE_LONG_AGO, answered=False)
        late_seen(db, job_id, 3_700)                                  # the hour has passed (the stack was down)
        inside = (await reaper(p, grace=StartupGrace(300)).tick())["render_deadline"]
        return job_id, inside, (await reaper(p, grace=StartupGrace(0)).tick())["render_deadline"]

    job_id, inside, after = run(db, scenario)
    assert inside == 0, "the backstop failed a job inside the startup grace"
    assert after == 1 and status(db, job_id) == "failed"


@mutation(CLAIM, CLAIM_GRACE, CLAIM_NO_GRACE, expect="a restarted reconciler did not read a job past the backstop")
def test_inside_the_startup_grace_the_reconciler_reads_a_job_past_the_backstop(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_unknown(p, k)
        age(db, job_id, "sent_at", DEADLINE_LONG_AGO, answered=False)
        late_seen(db, job_id, 3_700)
        engine = FakeEngine()
        due(db, job_id)
        await pass_once(reconciler(p, engine, grace=StartupGrace(300)))
        return [c for c in engine.calls if c[0] == "reconcile"]

    assert run(db, scenario) != [], "a restarted reconciler did not read a job past the backstop"


@mutation(APPLY, FETCH_GRACE, FETCH_NO_GRACE, expect="a download failed inside the startup grace")
def test_inside_the_startup_grace_a_download_window_that_ran_out_does_not_fail_the_job(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_fetching(p, k)
        age(db, job_id, "render_since", 9_000 + 4_000)
        age_found(db, job_id, 1_900)                                  # found after the deadline, window over
        results = []
        for grace in (True, False):
            due(db, job_id)
            [vis] = await store.claim_visible(p, provider=PROVIDER, status="fetching", visibility_s=900, limit=4)
            results.append(await store.apply_fetch_failed(
                p, job_id, vis.token, code="render.download_failed", detail="502", retry_in_s=1, deadline_s=9000,
                pools=POOLS, in_grace=grace))
        return results

    assert run(db, scenario) == ["fetching", "failed"], "a download failed inside the startup grace"


@mutation(FETCHER, FETCHER_PASSES_GRACE, FETCHER_DROPS_GRACE, expect="the fetcher ignored the startup grace")
def test_the_fetcher_passes_the_startup_grace_to_the_failure_rule(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_fetching(p, k)
        age(db, job_id, "render_since", 9_000 + 4_000)
        age_found(db, job_id, 1_900)
        engine = MediaEngine()
        engine.mode("error")
        due(db, job_id)
        await pass_once(fetcher(p, engine, objects, grace=StartupGrace(300)))
        return status(db, job_id)

    assert run(db, scenario) == "fetching", "the fetcher ignored the startup grace"


# ---- no final reconcile is claimed in the last minute before the backstop -----------------------------------------
CLAIM_STOP = "RECONCILE_CLAIM_STOP_S = 60 "
CLAIM_NO_STOP = "RECONCILE_CLAIM_STOP_S = 0 "


@mutation(APPLY, CLAIM_STOP, CLAIM_NO_STOP,
          expect="a final reconcile was claimed in the last minute before the backstop")
def test_no_final_reconcile_is_claimed_in_the_last_minute_before_the_backstop(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        reads = []
        for seen_ago in (3_500, 3_550):                               # 100 s and 50 s before the hour is over
            job_id = await to_unknown(p, k, prompt=str(seen_ago))
            age(db, job_id, "sent_at", DEADLINE_LONG_AGO, answered=False)
            late_seen(db, job_id, seen_ago)
            engine = FakeEngine()
            await reconcile_once(p, db, job_id, engine)
            reads.append(len([c for c in engine.calls if c[0] == "reconcile"]))
        return reads

    assert run(db, scenario) == [1, 0], "a final reconcile was claimed in the last minute before the backstop"


# ---- only a late job is stamped -----------------------------------------------------------------------------------
STAMP_ONLY_LATE = "   AND %(r)s + make_interval(secs => %%s) <= clock_timestamp()\n"
STAMP_ANY = "   AND %(r)s + make_interval(secs => %%s) <= clock_timestamp() + interval '100 years'\n"


@mutation(REAPER, STAMP_ONLY_LATE, STAMP_ANY, expect="a job before its deadline was stamped")
def test_a_job_before_its_deadline_is_not_stamped_and_keeps_its_back_off(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        waiting = await to_waiting(p, k, prompt="w")
        unknown = await to_unknown(p, k, prompt="u")
        db.run("UPDATE jobs SET next_poll_at = clock_timestamp() + interval '25 minutes' WHERE id = ANY(%s)",
               [waiting, unknown])
        await reaper(p).tick()
        return db.all("SELECT late_seen_at IS NULL, next_poll_at > clock_timestamp() + interval '20 minutes' "
                      "FROM jobs WHERE id = ANY(%s)", [waiting, unknown])

    assert run(db, scenario) == [(True, True), (True, True)], "a job before its deadline was stamped"
