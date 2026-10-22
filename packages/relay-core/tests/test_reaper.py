"""The reaper (I1, I9, I14, I17, I21): every transition is one compare-and-set
transaction, a sent job never goes back to `queued`, and deadlines are counted from the right clock."""

import asyncio
import json
import uuid

import pytest
from jobkit import CAPS, POOLS, account, claim, create, key, run, status
from lifecyclekit import age, claim_new, reaper, row, to_unknown, to_waiting

from relay_core import failpoints
from relay_core.jobs import store
from relay_core.jobs.worker import boot_owner
from relay_core.providers import Fail, Failed, PollResult, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, FakeSpec, sent
from relay_core.testing.mutation import mutation

R = "packages/relay-core/src/relay_core/jobs/reaper.py"
REQUEUES_SENT = "CASE WHEN sent_at IS NULL THEN 'queued' ELSE 'unknown' END,\n       account_id"
ALWAYS_QUEUED = "'queued',\n       account_id"
CAS = " FROM old WHERE jobs.id = %s AND jobs.status = 'submitting' RETURNING"
NO_CAS = " FROM old WHERE jobs.id = %s RETURNING"
FROM_RENDER = 'RENDER_FROM = "COALESCE(render_since, sent_at)"'
FROM_CREATION = 'RENDER_FROM = "created_at"'


def expire(db, lease_id) -> None:
    db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", lease_id)


def events(db, job_id) -> list[str]:
    return [r[0] for r in db.all("SELECT stage FROM job_events WHERE job_id = %s ORDER BY id", job_id)]


def refunds(db, job_id) -> int:
    return db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'refund'", job_id)[0]


class CauseSpec(FakeSpec):
    """A provider whose deadline code depends on the last upstream text it saw."""

    def deadline_cause(self, upstream_text, provider_state):
        if provider_state.get("signed_out"):
            return "account.dead_session"
        if "watermark" in upstream_text:
            return "render.watermarked_only"
        return "unregistered.code" if "bogus" in upstream_text else "render.deadline"


# ---- expired submit leases ----------------------------------------------------------------------------------------
def test_an_expired_lease_on_an_unsent_job_requeues_it_counting_an_attempt_and_it_can_be_claimed_again(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x")
        expire(db, got.lease_id)
        done = await reaper(p).tick()
        again = await claim(p)
        return got, done, again

    got, done, again = run(db, scenario)
    assert done["submit_lease"] == 1 and again is not None and again.job_id == got.job_id
    assert row(db, got.job_id, "attempts, claims") == (1, 2)
    assert events(db, got.job_id) == ["queued", "requeued"]


COUNTS_ATTEMPT = "attempts = CASE WHEN sent_at IS NULL THEN attempts + %s ELSE attempts END,"
NO_COUNT = "attempts = CASE WHEN sent_at IS NULL THEN attempts + 0 * %s ELSE attempts END,"
AT_LIMIT = "row[2] >= self.spec.caps.max_attempts"


@mutation(R, COUNTS_ATTEMPT, NO_COUNT, expect="the job must fail at its last attempt, not queue again")
@mutation(R, AT_LIMIT, "False", expect="the job must fail at its last attempt, not queue again")
def test_a_job_whose_claims_keep_killing_the_worker_before_the_send_fails_at_max_attempts_and_is_refunded(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "poison")
        states = []
        for _ in range(CAPS.max_attempts):
            expire(db, got.lease_id)                       # the worker died with the job claimed, nothing sent
            await reaper(p).tick()
            states.append(status(db, got.job_id))
            if states[-1] == "queued":
                got = await claim(p)
        return got.job_id, states

    job_id, states = run(db, scenario)
    assert states == ["queued"] * (CAPS.max_attempts - 1) + ["failed"], \
        "the job must fail at its last attempt, not queue again"
    assert row(db, job_id, "error_code, attempts") == ("queue.never_started", CAPS.max_attempts)
    assert refunds(db, job_id) == 1 and db.one("SELECT count(*) FROM leases") == (0,)


OWNER_GONE = "counts = int(locked[0])"
HEARTBEAT_AGE = "AND h.at > clock_timestamp() - make_interval(secs => %s)"
HEARTBEAT_ANY_AGE = "AND h.at > clock_timestamp() - make_interval(secs => %s) - interval '1 year'"
SAME_BOOT = "AND h.started_at <= l.heartbeat_at"
OWNER_MATCH = "h.worker_id IN (l.owner, regexp_replace(l.owner, '/[^/]*$', ''))"
WHOLE_OWNER_ONLY = "h.worker_id = l.owner"
FIRST_PART_ONLY = "h.worker_id IN (l.owner, split_part(l.owner, '/', 1))"


def heartbeat_row(db, *, age_s=0, started_ago_s=3600, worker="w1"):
    """The worker's own heartbeat row (keyed by the worker id, the owner's part before the "/")."""
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
           "(%s, 'fake', clock_timestamp() - make_interval(secs => %s), clock_timestamp() - make_interval(secs => %s))",
           worker, started_ago_s, age_s)


@mutation(R, OWNER_GONE, "counts = 1", expect="a live owner's expired lease must be requeued without an attempt")
@mutation(R, OWNER_MATCH, WHOLE_OWNER_ONLY, expect="a live owner's expired lease must be requeued without an attempt")
def test_a_live_owners_expired_lease_requeues_the_unsent_job_without_counting_an_attempt(db):
    """The keeper stalled but the worker still beats: it may still record its own zero-attempt interruption, so the
    reaper must not spend an attempt on it, not even the last one. The owner is a real per-boot one
    (`worker-<p>/<hex>` from `boot_owner`), which matches its worker's heartbeat row only after the suffix is cut."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x", owner=boot_owner("w1"))
        db.run("UPDATE jobs SET attempts = %s WHERE id = %s", CAPS.max_attempts - 1, got.job_id)
        heartbeat_row(db)
        expire(db, got.lease_id)
        await reaper(p, owner_gone_s=45).tick()
        return got

    got = run(db, scenario)
    assert row(db, got.job_id, "status, attempts") == ("queued", CAPS.max_attempts - 1), \
        "a live owner's expired lease must be requeued without an attempt"
    assert db.one("SELECT count(*) FROM leases") == (0,) and refunds(db, got.job_id) == 0


@mutation(R, "row[0] == \"queued\" and counts and row[2] >=", "row[0] == \"queued\" and row[2] >=",
          expect="a live owner's expired lease must never fail the job as a dead worker's")
def test_a_live_owners_expired_lease_never_fails_the_job_with_the_worker_died_text(db):
    """An unsent job that already has `max_attempts` attempts (proven-not-sent requeues are not capped) is still
    requeued, not failed, when only a live worker's lease expired."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x", owner=boot_owner("w1"))
        db.run("UPDATE jobs SET attempts = %s WHERE id = %s", CAPS.max_attempts, got.job_id)
        heartbeat_row(db)
        expire(db, got.lease_id)
        await reaper(p, owner_gone_s=45).tick()
        return got

    got = run(db, scenario)
    assert row(db, got.job_id, "status, attempts, error") == ("queued", CAPS.max_attempts, None), \
        "a live owner's expired lease must never fail the job as a dead worker's"
    assert refunds(db, got.job_id) == 0


@pytest.mark.parametrize("dead", ["no_row", "stale_row", "restarted_worker"])
@mutation(R, OWNER_GONE, "counts = 0", expect="a dead owner's expired lease must cost an attempt")
@mutation(R, HEARTBEAT_AGE, HEARTBEAT_ANY_AGE, expect="a dead owner's expired lease must cost an attempt")
@mutation(R, SAME_BOOT, "AND true", expect="a dead owner's expired lease must cost an attempt")
def test_a_dead_owners_expired_lease_requeues_the_unsent_job_and_counts_an_attempt(db, dead):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x", owner=boot_owner("w1"))
        if dead == "stale_row":
            heartbeat_row(db, age_s=600)                    # silent for 10 minutes: past the bound
        elif dead == "restarted_worker":
            heartbeat_row(db, age_s=1, started_ago_s=0)     # fresh row, but the worker booted after this lease's boot
            db.run("UPDATE leases SET heartbeat_at = clock_timestamp() - interval '1 minute' WHERE id = %s",
                   got.lease_id)
        expire(db, got.lease_id)
        await reaper(p, owner_gone_s=45).tick()
        return got

    got = run(db, scenario)
    assert row(db, got.job_id, "status, attempts") == ("queued", 1), "a dead owner's expired lease must cost an attempt"


@mutation(R, OWNER_MATCH, FIRST_PART_ONLY, expect="a live owner's expired lease must be requeued without an attempt")
def test_the_worker_id_of_an_owner_is_the_owner_without_only_its_final_suffix(db):
    """A worker id with a "/" inside (`serve` refuses one, but a row may exist): the owner `eu/dola/<hex>` belongs to
    the heartbeat row `eu/dola`, not to `eu`."""
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x", owner=boot_owner("eu/dola"))
        heartbeat_row(db, worker="eu/dola")
        expire(db, got.lease_id)
        await reaper(p, owner_gone_s=45).tick()
        return got

    got = run(db, scenario)
    assert row(db, got.job_id, "status, attempts") == ("queued", 0), \
        "a live owner's expired lease must be requeued without an attempt"


REAP_CLEARS_REF = "external_ref = CASE WHEN sent_at IS NULL THEN NULL ELSE external_ref END,\n"
REAP_KEEPS_REF = "external_ref = external_ref,\n"
DROPPED_NAMED = '% (" (dropped reference %s)" % row[1] if row[1] else "")'


REAP_CLEARS_STATE = "provider_state = CASE WHEN sent_at IS NULL THEN '{}'::jsonb ELSE provider_state END,\n"


@mutation(R, REAP_CLEARS_REF, REAP_KEEPS_REF, expect="assert ('queued', 'conv-A', {}) == ('queued', None, {})")
@mutation(R, REAP_CLEARS_STATE, "provider_state = provider_state,\n", expect="At index 2 diff: {'conv': 'A'} != {}")
@mutation(R, DROPPED_NAMED, '""',
           expect="any(\"conv-A\" in r[0] for r in db.all(\"SELECT text FROM job_events")
def test_a_requeue_by_the_reaper_drops_the_previous_attempts_reference_and_names_it(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x")
        await store.report_ref(p, got, "conv-A", {"conv": "A"})
        expire(db, got.lease_id)
        await reaper(p).tick()
        return got

    got = run(db, scenario)
    assert row(db, got.job_id, "status, external_ref, provider_state") == ("queued", None, {})
    assert any("conv-A" in r[0] for r in db.all("SELECT text FROM job_events WHERE job_id = %s", got.job_id))


@mutation(R, REQUEUES_SENT, ALWAYS_QUEUED,
           expect="assert ('queued', True, True, True) == ('unknown', True, True, True)")
def test_an_expired_lease_on_a_sent_job_makes_it_unknown_never_queued(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x")
        await store.mark_sent(p, got)
        expire(db, got.lease_id)
        done = await reaper(p).tick()
        return got, done

    got, done = run(db, scenario)
    assert done["submit_lease"] == 1
    assert row(db, got.job_id, "status, sent_at IS NOT NULL, account_id IS NOT NULL, "
                               "next_poll_at > clock_timestamp() + interval '50 seconds'") == (
        "unknown", True, True, True)
    assert db.one("SELECT count(*) FROM leases") == (0,)
    assert events(db, got.job_id)[-1] == "unknown"


def test_a_live_lease_is_left_alone(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x")
        done = await reaper(p).tick()
        return got, done

    got, done = run(db, scenario)
    assert sum(done.values()) == 0 and status(db, got.job_id) == "submitting"
    assert db.one("SELECT count(*) FROM leases") == (1,)


@mutation(R, CAS, NO_CAS, expect="AssertionError: the CAS on the status must hold")
def test_a_stale_lease_of_a_job_that_already_moved_on_changes_only_the_lease(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        acct = db.one("SELECT account_id FROM jobs WHERE id = %s", job_id)[0]
        db.run("INSERT INTO leases (account_id, job_id, purpose, owner, until, hard_until) "
               "VALUES (%s, %s, 'job', 'w1', clock_timestamp() - interval '1 second', "
               "clock_timestamp() + interval '60 seconds')", acct, job_id)
        done = await reaper(p).tick()
        return job_id, done

    job_id, done = run(db, scenario)
    assert row(db, job_id, "status, sent_at IS NOT NULL") == ("waiting", True), "the CAS on the status must hold"
    assert db.one("SELECT count(*) FROM leases") == (0,) and done["submit_lease"] == 0


def test_two_reapers_at_once_make_each_transition_exactly_once(db):
    k, _ = key(db), account(db, left=100)
    for _ in range(3):
        account(db, left=100)

    async def scenario(p):
        unsent, sent_jobs = [], []
        for i in range(4):
            got = await claim_new(p, k, "u%d" % i)
            (sent_jobs if i % 2 else unsent).append(got)
            if i % 2:
                await store.mark_sent(p, got)
            expire(db, got.lease_id)
        await asyncio.gather(*(reaper(p).tick() for _ in range(3)))
        return unsent, sent_jobs

    unsent, sent_jobs = run(db, scenario)
    for got in unsent:
        assert status(db, got.job_id) == "queued" and events(db, got.job_id).count("requeued") == 1
    for got in sent_jobs:
        assert status(db, got.job_id) == "unknown" and events(db, got.job_id).count("unknown") == 1


def test_the_reaper_does_not_fight_a_fenced_write_that_is_still_running(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        created = await create(p, k, "x")
        got = await claim(p, ttl_s=3.0)
        engine = FakeEngine()
        engine.script_submit("*", sent(SubmitResult(Fail("input.bad_model", "no such model"))))
        view = await store.job_view(p, got.job_id)
        await store.mark_sent(p, got)
        result = await engine.submit(_Ctx(), view)
        inside, release = asyncio.Event(), asyncio.Event()

        async def stall() -> None:
            inside.set()
            await release.wait()

        failpoints.arm("job.fail.refund", stall)
        applying = asyncio.create_task(store.apply_submit(p, got, result, caps=CAPS, pools=POOLS))
        await asyncio.wait_for(inside.wait(), 5)
        await asyncio.sleep(3.2)                       # the lease is now past its time, its row held by the fence
        during = await reaper(p).tick()
        release.set()
        outcome = await applying
        failpoints.disarm()
        after = await reaper(p).tick()
        return created.job_id, during, outcome, after

    job_id, during, outcome, after = run(db, scenario)
    assert during["submit_lease"] == 0, "a lease held by a fence in flight is skipped"
    assert outcome == "failed" and status(db, job_id) == "failed" and sum(after.values()) == 0
    assert refunds(db, job_id) == 1 and "unknown" not in events(db, job_id)


class _Ctx:
    async def mark_sent(self) -> None:
        return None


# ---- deadlines ---------------------------------------------------------------------------------------------------
def test_a_job_that_never_started_fails_after_the_unsent_deadline_with_the_refund(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        old = (await create(p, k, "old")).job_id
        young = (await create(p, k, "young")).job_id
        other = db.job(k, provider="other")
        age(db, old, "created_at", 10_900)                   # past the 10 800 s default
        age(db, other, "created_at", 10_900)
        done = await reaper(p).tick()
        again = await reaper(p).tick()
        return old, young, other, done, again

    old, young, other, done, again = run(db, scenario)
    assert row(db, old, "status, error_code") == ("failed", "queue.never_started") and refunds(db, old) == 1
    assert status(db, young) == "queued" and status(db, other) == "queued", "only this provider's job, only the old one"
    assert done["never_started"] == 1 and again["never_started"] == 0


def test_a_requeued_job_still_counts_its_unsent_time_from_its_creation(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x")
        expire(db, got.lease_id)
        await reaper(p).tick()                                # back to queued
        age(db, got.job_id, "created_at", 11_000)
        await reaper(p).tick()
        return got.job_id

    assert row(db, run(db, scenario), "status, error_code") == ("failed", "queue.never_started")


def test_a_waiting_job_past_the_render_deadline_fails_with_the_providers_cause_and_the_refund(db):
    k, _ = key(db), account(db, left=100)
    spec = CauseSpec()

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", 9_100)
        db.run("INSERT INTO job_events (job_id, at, source, stage, text) VALUES "
               "(%s, clock_timestamp() - interval '10000 seconds', 'upstream', 'poll', 'watermark seen long ago'), "
               "(%s, clock_timestamp() - interval '60 seconds', 'upstream', 'poll', 'still rendering')",
               job_id, job_id)
        await reaper(p, spec=spec).tick()
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, error_code") == ("failed", "render.deadline"), \
        "a block first seen before render_since is ignored (I17)"
    assert refunds(db, job_id) == 1


@mutation(R, '        if code == DEADLINE_CODE:', '        if True:', expect="cause: render.watermarked_only")
def test_the_deadline_cause_reads_the_newest_text_of_this_render_and_an_unregistered_code_falls_back(db):
    k, _ = key(db), account(db, left=100)
    spec = CauseSpec()

    async def scenario(p):
        a = await to_waiting(p, k, prompt="a")
        b = await to_waiting(p, k, prompt="b")
        c = await to_waiting(p, k, prompt="c")
        db.run("UPDATE jobs SET provider_state = '{\"signed_out\": true}' WHERE id = %s", c)
        for job_id, text in ((a, "output is watermark only"), (b, "bogus"), (c, "anything")):
            age(db, job_id, "render_since", 9_100)
            db.run("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'upstream', 'poll', %s)",
                   job_id, text)
        await reaper(p, spec=spec).tick()
        return a, b, c

    a, b, c = run(db, scenario)
    assert row(db, a, "error_code") == ("render.watermarked_only",) and row(db, b, "error_code") == ("render.deadline",)
    # a failure that has a cause names it, whether it came from the upstream's words or from the job's state
    assert row(db, c, "error_code") == ("account.dead_session",)
    assert row(db, a, "error")[0].endswith("cause: render.watermarked_only")
    assert row(db, c, "error")[0].endswith("cause: account.dead_session")
    assert "cause" not in row(db, b, "error")[0], "a plain timeout names no cause"


def test_the_render_deadline_is_read_live_and_an_unknown_job_counts_from_its_send(db):
    k, _ = key(db), account(db, left=100)
    settings = FakeSettings()

    async def scenario(p):
        waiting = await to_waiting(p, k, prompt="w")
        unknown = await to_unknown(p, k, prompt="u")
        age(db, waiting, "render_since", 1_000)
        age(db, unknown, "sent_at", 1_000)
        first = await reaper(p, settings=settings).tick()                   # 9000 s deadline: nobody is late
        settings.set("fake.render_deadline_s", 600)
        second = await reaper(p, settings=settings).tick()
        return waiting, unknown, first, second

    waiting, unknown, first, second = run(db, scenario)
    assert first["render_deadline"] == 0 and second["render_deadline"] == 2
    assert row(db, waiting, "status") == ("failed",)
    assert row(db, unknown, "status, error_code, account_id IS NOT NULL") == ("failed", "render.deadline", True)
    assert refunds(db, waiting) == refunds(db, unknown) == 1


@mutation(R, FROM_RENDER, FROM_CREATION, expect="assert 'failed' == 'waiting'")
def test_the_render_clock_starts_at_render_since_not_at_creation(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "created_at", 20_000)                # created long ago, sent and Re-checked just now
        db.run("UPDATE jobs SET answered_at = clock_timestamp() WHERE id = %s", job_id)   # a read the clock ignores
        await reaper(p).tick()
        return job_id

    assert status(db, run(db, scenario)) == "waiting"


def test_a_recheck_restarts_the_render_clock_and_the_new_failure_is_refunded_again(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", 9_100)
        await reaper(p).tick()
        failed = status(db, job_id)
        await store.recheck_job(p, job_id)
        await reaper(p).tick()                               # the clock restarted: the job survives
        alive = status(db, job_id)
        age(db, job_id, "render_since", 9_100)
        await reaper(p).tick()
        return job_id, failed, alive

    job_id, failed, alive = run(db, scenario)
    assert (failed, alive, status(db, job_id)) == ("failed", "waiting", "failed")
    assert db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'charge'", job_id) == (2,)
    assert refunds(db, job_id) == 2, "the re-charge is refundable like the first (I10, I21)"


def test_a_poll_result_in_flight_wins_over_the_deadline_and_the_job_is_failed_once(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):

        job_id = await to_waiting(p, k)
        age(db, job_id, "render_since", 9_100)
        token = uuid.uuid4()
        db.run("UPDATE jobs SET poll_token = %s", token)
        inside, release = asyncio.Event(), asyncio.Event()

        async def stall() -> None:
            inside.set()
            await release.wait()

        failpoints.arm("job.fail.refund", stall)
        applying = asyncio.create_task(store.apply_poll(
            p, job_id, token, PollResult(Failed("render.deadline", "x", taken=True)), caps=CAPS, pools=POOLS))
        await asyncio.wait_for(inside.wait(), 5)
        racing = asyncio.create_task(reaper(p).tick())
        await asyncio.sleep(0.2)
        release.set()
        await applying
        done = await racing
        failpoints.disarm()
        return job_id, done

    job_id, done = run(db, scenario)
    assert status(db, job_id) == "failed" and refunds(db, job_id) == 1
    assert done["render_deadline"] == 0 and events(db, job_id).count("failed") == 1


# ---- other leases and visibility ---------------------------------------------------------------------------------
def test_expired_check_command_and_reconcile_leases_are_deleted_and_a_stalled_command_fails(db):
    acct = account(db, left=100)
    ids = {}
    for purpose in ("check", "command", "reconcile"):
        for live in (False, True):
            ids[(purpose, live)] = db.one(
                "INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES "
                "(%s, %s, 'w1', clock_timestamp() + make_interval(secs => %s), clock_timestamp() + interval '60 s') "
                "RETURNING id", acct, purpose, 30 if live else -1)[0]
    stalled = uuid.uuid4()
    healthy, queued = uuid.uuid4(), uuid.uuid4()
    for cid, st, lease in ((stalled, "running", ids[("command", False)]), (healthy, "running", ids[("command", True)]),
                           (queued, "queued", None)):
        db.run("INSERT INTO commands (id, provider, kind, status, lease_id) VALUES (%s, 'fake', 'recover', %s, %s)",
               cid, st, lease)

    async def scenario(p):
        return await reaper(p).tick()

    done = run(db, scenario)
    assert done["other_lease"] == 3
    assert sorted(r[0] for r in db.all("SELECT id FROM leases")) == sorted(v for (_, live), v in ids.items() if live)
    states = {r[0]: r[1:] for r in db.all("SELECT id, status, finished_at IS NOT NULL, result FROM commands")}
    assert states[stalled][:2] == ("failed", True) and "lease expired" in json.dumps(states[stalled][2])
    assert states[healthy][0] == "running" and states[queued][0] == "queued"


@mutation(R, "WHERE provider = %(p)s AND status = 'running' AND lease_id IS NULL ",
          "WHERE provider = %(p)s AND status = 'running' ",
           expect="{n: got[i][0] for n, i in ids.items()} == {\"stalled\": \"failed\", \"young\"")
@mutation(R, "COALESCE((%(b)s::jsonb ->> kind)::float8, %(d)s) + %(m)s) <= clock_timestamp()",
          "COALESCE((%(b)s::jsonb ->> kind)::float8, %(d)s) + %(m)s) >= clock_timestamp()",
           expect="assert {'stalled': '...running', ...}")
def test_a_running_command_with_no_lease_fails_once_past_its_bound_and_not_before(db):
    acct = account(db, left=100)
    live = db.one("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, 'command', 'w1', "
                  "clock_timestamp() + interval '30 s', clock_timestamp() + interval '60 s') RETURNING id", acct)[0]
    rows = {"stalled": ("wake", None, 3600), "young": ("wake", None, 5), "leased": ("recover", live, 3600),
            "check": ("check", None, 300), "check_young": ("check", None, 100)}
    ids = {}
    for name, (kind, lease, age_s) in rows.items():
        ids[name] = db.one(
            "INSERT INTO commands (provider, kind, status, lease_id, started_at) VALUES ('fake', %s, 'running', %s, "
            "clock_timestamp() - make_interval(secs => %s)) RETURNING id", kind, lease, age_s)[0]
    other = db.one("INSERT INTO commands (provider, kind, status, started_at) VALUES ('other', 'wake', 'running', "
                   "clock_timestamp() - interval '1 hour') RETURNING id")[0]

    async def scenario(p):
        return await reaper(p).tick()

    done = run(db, scenario)
    got = {r[0]: r[1:] for r in db.all("SELECT id, status, finished_at IS NOT NULL, result FROM commands")}
    assert {n: got[i][0] for n, i in ids.items()} == {"stalled": "failed", "young": "running", "leased": "running",
                                                       "check": "failed", "check_young": "running"}
    assert got[ids["stalled"]][1] and "stalled" in json.dumps(got[ids["stalled"]][2])
    assert got[other][0] == "running" and done["stalled_command"] == 2          # another provider's is left alone


def test_a_waiting_or_fetching_job_nobody_can_see_is_made_due_again(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        a = await to_waiting(p, k, prompt="a")
        b = await to_waiting(p, k, prompt="b", completion="hold")
        db.run("UPDATE jobs SET next_poll_at = NULL, poll_token = gen_random_uuid() WHERE id = %s", a)
        done = await reaper(p).tick()
        return a, b, done

    a, b, done = run(db, scenario)
    assert row(db, a, "next_poll_at <= clock_timestamp(), poll_token") == (True, None) and done["visibility"] == 1


def test_the_run_loop_reaps_on_its_interval_until_stopped(db):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        got = await claim_new(p, k, "x")
        expire(db, got.lease_id)
        loop = reaper(p)
        task = asyncio.create_task(loop.run())
        for _ in range(100):
            if status(db, got.job_id) == "queued":
                break
            await asyncio.sleep(0.05)
        loop.stop()
        await asyncio.wait_for(task, 5)
        return got.job_id

    assert status(db, run(db, scenario)) == "queued"
