"""The crash windows: what a dying, stalled or hung worker leaves behind, and that recovery neither
sends twice nor loses money. The kills are `os._exit(137)` in a real subprocess (`crashkit`); the rest run in process.

Here: killed before mark_sent and right after it; killed during fetching; a lease lost before a second send; a stalled
event loop with an injected TTL; a webhook answered 2xx whose sender died before the mark; N unknowns + checks + full
submit slots against the browser semaphore (I28).

Elsewhere (the report's table names the tests): a hung submit (test_engine), two pollers (test_poller), Re-check during
a poll (test_recheck_recover), charge vs insert (test_billing), cancel during commit (test_engine), the reaper beside
a live write (test_reaper), a refund across midnight (test_billing, test_pool), the stale now() fence (test_leases), a
hung check, command or reconcile (test_leases, test_checks, test_commands, test_reconcile).
"""

import asyncio
import time
from collections import Counter

import pytest
from accountkit import KitEngine, KitSpec, make_env, minio_settings, object_store
from crashkit import CLAIM_LOOP, FETCHER, WEBHOOK_SENDER, crash_child, strict, wait_for
from enginekit import in_status, job_status, make_loop, running, until
from hookkit import Receiver, hook, hook_job, make_due, resolver
from jobkit import account, create, key, run
from lifecyclekit import (
    MediaEngine,
    accepted,
    age,
    due,
    fetcher,
    pass_once,
    poller,
    reaper,
    reconciler,
    to_fetching,
    to_unknown,
    to_waiting,
)

from relay_core import failpoints, leases
from relay_core.budget import BrowserBudget
from relay_core.db import tx
from relay_core.jobs import store, webhooks
from relay_core.jobs.checks import Checks
from relay_core.providers import Accepted, Done, Found, PollResult, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, sent
from relay_core.testing.mutation import mutation

R = "packages/relay-core/src/relay_core/jobs/reaper.py"
A = "packages/relay-core/src/relay_core/jobs/apply.py"
RUNCTX = "packages/relay-core/src/relay_core/jobs/runctx.py"
L = "packages/relay-core/src/relay_core/leases.py"
WH = "packages/relay-core/src/relay_core/jobs/webhooks.py"
RC = "packages/relay-core/src/relay_core/jobs/reconcile.py"

MODES = ("hold", "detach")
REQUEUES_SENT = "CASE WHEN sent_at IS NULL THEN 'queued' ELSE 'unknown' END,\n       account_id"
ALWAYS_QUEUED = "'queued',\n       account_id"
ALWAYS_UNKNOWN = "'unknown',\n       account_id"
ROWS_AND_DONE_TOGETHER = ("async with tx(db) as conn:\n        job = await _lock_job(conn, job_id)\n"
                          "        if _not_the_fetcher(job, poll_token):\n            return False\n"
                          "        for i, m in enumerate(media):")
ROWS_COMMIT_ALONE = ("async with db.connection() as conn:\n        job = await _lock_job(conn, job_id)\n"
                     "        if _not_the_fetcher(job, poll_token):\n            return False\n"
                     "        for i, m in enumerate(media):")
GATE_FENCED = "await leases.fence_once(self._db, self._claim.lease_id)"
GATE_UNFENCED = "pass"
# A lease registered while the heartbeat statement runs is protected twice: it is not in the snapshot the statement
# was asked about, and a lease missing from the answer is lost only when its row is gone or expired. The mutation
# removes both, which is the bug the test stands for.
IN_FLIGHT_SAFE = ("        gone = {i for i in held if i not in alive}\n"
                  "        if gone:\n"
                  "            rows = await self._rows(gone)\n"
                  "            gone -= {i for i, live in rows.items() if live}")
IN_FLIGHT_JUDGED = ("        gone = {i for i in self._held if i not in alive}\n"
                    "        if gone:\n"
                    "            rows = await self._rows(gone)\n"
                    "            pass")
NO_REVIVAL = "WHERE owner = %s AND id = ANY(%s) AND until > clock_timestamp() FOR UPDATE SKIP LOCKED) RETURNING id"
REVIVAL = "WHERE owner = %s AND id = ANY(%s) FOR UPDATE SKIP LOCKED) RETURNING id"
EVENT_ID = 'return "%s:%d" % (self.job_id, (self.callback_for - _EPOCH) // timedelta(milliseconds=1))'
EVENT_ID_PER_ATTEMPT = 'return "%s:%d" % (self.job_id, self.attempt)'
ATTEMPT_AT_CLAIM = "UPDATE jobs SET callback_attempts = callback_attempts + 1,"
NO_ATTEMPT_AT_CLAIM = "UPDATE jobs SET callback_attempts = callback_attempts + 0,"
RECONCILE_SHARE = ("token = self.budget.try_acquire(Purpose.RECONCILE) if self.budget is not None else None\n"
                   "            if self.budget is not None and token is None:")
RECONCILE_UNBOUNDED = "token = None\n            if False:"


def submits(engine: FakeEngine) -> list:
    return [c for c in engine.calls if c[0] == "submit"]


def claims(db, job_id) -> int:
    return db.one("SELECT claims FROM jobs WHERE id = %s", job_id)[0]


def lease_expired(db) -> bool:
    return db.one("SELECT count(*) FROM leases WHERE until < clock_timestamp()")[0] == 1


# ---- killed before and after mark_sent (I1, I9, I14) ----------------------------------------------------------------
@pytest.mark.parametrize("mode", MODES)
@mutation(R, REQUEUES_SENT, ALWAYS_UNKNOWN, expect="timed out waiting for the requeued job to be sent")
def test_a_worker_killed_before_mark_sent_leaves_a_job_the_reaper_requeues_and_one_new_send_completes(db, mode):
    k, _ = key(db), account(db, left=100)
    job_id = run(db, lambda p: create(p, k, "p", completion=mode)).job_id
    assert crash_child(CLAIM_LOOP, db.worker_dsn, "fake.submit.start") == 137
    wait_for(lambda: lease_expired(db), what="the dead worker's lease to run out")
    assert db.one("SELECT status, sent_at IS NULL FROM jobs WHERE id = %s", job_id) == ("submitting", True)
    died_at = claims(db, job_id)        # 1, or more if load cost the child a lease before it reached the send

    async def recover(p):
        await reaper(p).tick()
        mid = db.one("SELECT status, attempts, account_id IS NULL FROM jobs WHERE id = %s", job_id)
        engine = FakeEngine()
        async with running(make_loop(p, engine)):
            await until(in_status(db, job_id, "fetching", "waiting"), what="the requeued job to be sent")
        return mid, engine

    mid, engine = run(db, recover)
    assert mid == ("queued", 1, True), "never sent: back in the queue, the lost claim counted as one attempt"
    assert len(submits(engine)) == 1 and claims(db, job_id) == died_at + 1
    assert db.wallet(k) == (10, 10)
    assert db.all("SELECT amount FROM account_charges") == [(1,)], "the account is charged once, by the second send"
    assert strict(db) == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("ending", ("found", "deadline"))
@mutation(R, REQUEUES_SENT, ALWAYS_QUEUED, expect="assert job_status(db, job_id) == \"unknown\"")
def test_a_worker_killed_right_after_mark_sent_leaves_an_unknown_job_that_is_never_sent_again(db, mode, ending):
    k, _ = key(db), account(db, left=100)
    job_id = run(db, lambda p: create(p, k, "p", completion=mode)).job_id
    assert crash_child(CLAIM_LOOP, db.worker_dsn, "fake.submit.sent") == 137
    wait_for(lambda: lease_expired(db), what="the dead worker's lease to run out")
    assert db.one("SELECT status, sent_at IS NOT NULL FROM jobs WHERE id = %s", job_id) == ("submitting", True)
    died_at = claims(db, job_id)        # an unsent claim lost to load before the send may have come first

    async def recover(p):
        await reaper(p).tick()
        assert job_status(db, job_id) == "unknown"
        engine = FakeEngine()
        async with running(make_loop(p, engine)):             # a fresh worker, several ticks: it must not send again
            await asyncio.sleep(0.4)
        if ending == "found":
            engine.script_reconcile("*", Found("ref-1", {}, taken=True))
            due(db, job_id)
            await pass_once(reconciler(p, engine))
        else:
            age(db, job_id, "sent_at", 10_000)                # no answer by the render deadline
            await reaper(p).tick()
        return engine

    engine = run(db, recover)
    assert submits(engine) == [] and claims(db, job_id) == died_at, "the recovering workers never claim it"
    if ending == "found":
        assert job_status(db, job_id) == "waiting"
        assert db.all("SELECT amount FROM account_charges") == [(1,)] and db.wallet(k) == (10, 10)
    else:
        assert db.one("SELECT status, error_code FROM jobs WHERE id = %s", job_id) == ("failed", "render.deadline")
        assert db.wallet(k) == (0, 0) and db.all("SELECT amount FROM account_charges") == []
    assert strict(db) == []


# ---- killed during fetching (I11) ----------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", MODES)
@mutation(A, ROWS_AND_DONE_TOGETHER, ROWS_COMMIT_ALONE, expect="the media rows and done are one commit")
def test_a_worker_killed_between_the_media_rows_and_done_leaves_the_outputs_and_a_rerun_finishes(db, mode):
    k, _ = key(db), account(db, left=100)

    async def prepare(p):
        engine = MediaEngine()
        if mode == "hold":
            job_id = await to_fetching(p, k, n=2)
        else:
            engine.script_submit("*", sent(accepted()))
            job_id = await to_waiting(p, k, engine=engine, completion="detach")
            outputs = engine.outputs(await store.job_view(p, job_id), 2)
            engine.script_poll(job_id, PollResult(Done(outputs)))
            due(db, job_id)
            await pass_once(poller(p, engine))
        due(db, job_id)
        return job_id

    with object_store() as objects:
        job_id = run(db, prepare)
        endpoint, access, secret = minio_settings()
        crashed = crash_child(FETCHER, db.worker_dsn, "fetch.rows_written", T_MINIO=endpoint, T_AK=access,
                              T_SK=secret, T_BUCKET=objects.bucket)
        assert crashed == 137
        stored = sorted(o.object_name for o in objects._client.list_objects(objects.bucket, recursive=True))
        assert len(stored) == 2, "the paid outputs survive the crash under their final keys"
        assert db.one("SELECT status FROM jobs WHERE id = %s", job_id) == ("fetching",)
        rows = db.one("SELECT count(*) FROM job_media WHERE job_id = %s", job_id)
        assert rows == (0,), "the media rows and done are one commit"

        async def rerun(p):
            due(db, job_id)                                    # the dead fetcher's claim has run out
            await pass_once(fetcher(p, MediaEngine(), objects))

        run(db, rerun)
        again = sorted(o.object_name for o in objects._client.list_objects(objects.bucket, recursive=True))
    assert db.one("SELECT status FROM jobs WHERE id = %s", job_id) == ("done",)
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND role = 'output'", job_id) == (2,)
    assert again == stored, "the rerun overwrote the same keys"
    assert db.wallet(k) == (10, 10) and db.all("SELECT amount FROM account_charges") == [(1,)]
    assert strict(db) == []


# ---- a lease lost between the first send and a second irreversible action (I1, I2) -----------------------------------
@pytest.mark.parametrize("mode", MODES)
@mutation(RUNCTX, GATE_FENCED, GATE_UNFENCED, expect="timed out waiting for the gate to stop")
def test_a_lease_lost_before_the_second_send_stops_the_gate_and_the_job_ends_unknown(db, mode):
    k, _ = key(db), account(db, left=100)
    sends = []

    async def gate(ctx, job):
        sends.append("first")                          # mark_sent ran just before this step
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second'")        # the heartbeat stalled
        await ctx.fence()                              # contract 5.2: before every irreversible action after the first
        sends.append("second")
        return SubmitResult(Accepted("never"))

    async def scenario(p):
        engine = FakeEngine()
        made = await create(p, k, "p", completion=mode)
        engine.script_submit(made.job_id, sent(gate))
        async with running(make_loop(p, engine)):
            await until(in_status(db, made.job_id, "unknown"), what="the gate to stop")
            await asyncio.sleep(0.3)                   # more ticks: nothing may send again
        return made.job_id, engine

    job_id, engine = run(db, scenario)
    assert sends == ["first"] and len(submits(engine)) == 1
    assert db.one("SELECT status, claims, attempts FROM jobs WHERE id = %s", job_id) == ("unknown", 1, 0)
    assert db.one("SELECT count(*) FROM leases") == (0,) and db.wallet(k) == (10, 10)
    assert strict(db) == []


# ---- a stalled event loop with an injected TTL (I1, I14) -------------------------------------------------------------
@pytest.mark.parametrize("mode", MODES)
@mutation(L, NO_REVIVAL, REVIVAL, expect="timed out waiting for both stalled submits to end unknown")
def test_a_stalled_event_loop_expires_the_leases_and_the_jobs_end_unknown_never_sent_again(db, mode):
    """The contract's 70 s stall against a 60 s TTL, scaled: a 0.7 s block against a 0.4 s TTL. The stall blocks the
    heartbeat with everything else, so the leases run out; when the loop wakes, a stale lease must not come back."""
    k = key(db)
    account(db, left=100)
    account(db, left=100)

    sends: Counter = Counter()          # `stall` runs only after mark_sent succeeded: one call is one real send

    async def stall(ctx, job):
        sends[str(job.id)] += 1
        time.sleep(0.7)                                # a blocking call on the loop: nothing else runs, the beat too
        await asyncio.sleep(0.3)                       # the heartbeat gets its turn before this submit returns
        return SubmitResult(Accepted("late"))

    async def scenario(p):
        engine = FakeEngine()
        ids = [(await create(p, k, "p%d" % i, completion=mode)).job_id for i in range(2)]
        engine.script_submit("*", sent(stall))

        async def both_unknown() -> bool:
            # Under load the late result can beat the heartbeat's cancel: its fence then drops it and, as in a
            # worker, the reaper ends the job. Either path must end `unknown`.
            await reaper(p).tick()
            return all(job_status(db, j) == "unknown" for j in ids)

        async with running(make_loop(p, engine, lease_ttl_s=0.4)):
            await until(both_unknown, timeout_s=8, what="both stalled submits to end unknown")
            await asyncio.sleep(0.3)                   # more ticks: nothing may send again
        await reaper(p).tick()
        return ids, engine

    ids, engine = run(db, scenario)
    # Under load one job's lease can also run out BEFORE its send (the other job's stall blocks the loop): that
    # submit is fenced at mark_sent and requeued unsent, which is legal. What must hold is one real send each.
    assert sends == Counter({str(j): 1 for j in ids}), "each job is sent exactly once"
    for j in ids:
        assert db.one("SELECT status, attempts FROM jobs WHERE id = %s", j) == ("unknown", 0)
    assert db.one("SELECT count(*) FROM leases") == (0,) and db.wallet(k) == (20, 20)
    assert strict(db) == []


# ---- a webhook answered 2xx whose sender died before it could mark (I16) ---------------------------------------------
@mutation(WH, EVENT_ID, EVENT_ID_PER_ATTEMPT, expect="the receiver can deduplicate")
@mutation(WH, ATTEMPT_AT_CLAIM, NO_ATTEMPT_AT_CLAIM, expect="the attempt was counted at the claim, before the send")
def test_a_webhook_whose_2xx_came_after_the_sender_died_is_sent_again_with_the_same_event_id(db, tmp_path):
    job_id = hook_job(db, db.key())
    seen = tmp_path / "event-id"
    assert crash_child(WEBHOOK_SENDER, db.worker_dsn, T_SEEN=str(seen)) == 137
    assert hook(db, job_id)[:2] == ("pending", 1), "the attempt was counted at the claim, before the send"
    make_due(db, job_id)                               # the dead sender's hold has run out
    receiver = Receiver(200)
    run(db, lambda p: webhooks.deliver_due(p, resolver=resolver, transport=receiver.transport))
    assert [r.headers["x-relay-event-id"] for r in receiver.seen] == [seen.read_text()], "the receiver can deduplicate"
    assert hook(db, job_id)[:2] == ("sent", 2)


# ---- N unknown jobs, checks and full submit slots against the browser semaphore (I28) -------------------------------
class Gauge(KitEngine):
    """Every browser-using call holds for a moment, so overlaps are real; `peak` is the most that ever ran at once."""

    def __init__(self) -> None:
        super().__init__()
        self.live, self.peak, self.ran = 0, 0, Counter()

    async def _hold(self, kind: str, call):
        self.live += 1
        self.peak = max(self.peak, self.live)
        self.ran[kind] += 1
        try:
            await asyncio.sleep(0.4)
            return await call()
        finally:
            self.live -= 1

    async def submit(self, ctx, job):
        return await self._hold("submit", lambda: KitEngine.submit(self, ctx, job))

    async def reconcile(self, ctx, job):
        return await self._hold("reconcile", lambda: KitEngine.reconcile(self, ctx, job))

    async def check_account(self, ctx):
        return await self._hold("check", lambda: KitEngine.check_account(self, ctx))


@mutation(RC, RECONCILE_SHARE, RECONCILE_UNBOUNDED,
          expect="no mix of purposes may run more browsers than the semaphore")
def test_unknown_jobs_checks_and_full_submit_slots_never_exceed_the_browser_semaphore(db):
    k = key(db)
    for _ in range(8):
        account(db, left=100)
    spec, budget, engine = KitSpec(execution="browser", titles=False, catalog_refresh_s=None), BrowserBudget(4), Gauge()

    async def scenario(p):
        unknown = [await to_unknown(p, k, prompt="u%d" % i) for i in range(4)]        # four accounts hold one each
        for j in unknown:
            due(db, j)
        for i in range(6):
            await create(p, k, "q%d" % i, completion="hold")                           # and six more wait to be sent
        loop = make_loop(p, engine, spec=spec, budget=budget)
        recon = reconciler(p, engine, spec=spec, budget=budget)
        checks = Checks(make_env(p, engine, spec=spec, budget=budget))
        stop, busiest = asyncio.Event(), 0

        async def sample():
            nonlocal busiest
            while not stop.is_set():
                busiest = max(busiest, budget.busy)
                await asyncio.sleep(0.01)

        tasks = [asyncio.create_task(recon.run()), asyncio.create_task(checks.run(stop, 0.05)),
                 asyncio.create_task(sample())]
        async with running(loop):
            await asyncio.sleep(4.0)
        stop.set()
        recon.stop()
        await asyncio.gather(*tasks)
        await checks.wait()
        return busiest

    busiest = run(db, scenario)
    assert engine.peak <= 4 and busiest <= 4, "no mix of purposes may run more browsers than the semaphore"
    assert engine.ran["submit"] >= 1 and engine.ran["reconcile"] >= 2 and engine.ran["check"] >= 1
    assert engine.peak >= 3, "the test saturates the slots, so the bound is really exercised"


# ---- a claim that fails after another claim was committed -----------------------------------------------------------
@mutation(RC, "if claims and isinstance(e, Exception):", "if False:",
          expect="the claim that did commit keeps a lease nobody works on, and the heart")
def test_a_reconcile_claim_that_fails_after_the_first_does_not_orphan_the_first_lease(db):
    k = key(db)
    account(db, left=100)
    account(db, left=100)

    async def scenario(p):
        unknown = [await to_unknown(p, k, prompt="u%d" % i) for i in range(2)]
        for j in unknown:
            due(db, j)
        hits = 0

        async def second_claim_fails():
            nonlocal hits
            hits += 1
            if hits == 2:
                raise failpoints.FailpointError("the second claim fails")

        failpoints.arm("lease.acquire.counted", second_claim_fails)
        loop = reconciler(p, FakeEngine())
        try:
            await loop.tick()
        except failpoints.FailpointError:
            pass
        finally:
            failpoints.disarm("lease.acquire.counted")
        await loop.idle()
        await asyncio.sleep(0.2)

    run(db, scenario)
    assert db.one("SELECT count(*) FROM leases WHERE purpose = 'reconcile'") == (0,), \
        "the claim that did commit keeps a lease nobody works on, and the heartbeat keeps it alive"


# ---- a lease taken while a heartbeat statement is in flight --------------------------------------------------------
@mutation(L, IN_FLIGHT_SAFE, IN_FLIGHT_JUDGED,
          expect="a lease that is live and held was declared lost and its task cancelled")
def test_a_lease_taken_while_a_heartbeat_is_in_flight_is_not_declared_lost(db, monkeypatch):
    acct = account(db, left=100)

    async def scenario(p):
        keeper = leases.LeaseKeeper(p, "w1", ttl_s=30, heartbeat_s=15)
        real = leases.heartbeat

        async def slow(pool, owner, held, *, ttl_s):
            answer = await real(pool, owner, held, ttl_s=ttl_s)  # the UPDATE has run: this lease is not in its answer
            await asyncio.sleep(1.5)
            return answer

        monkeypatch.setattr(leases, "heartbeat", slow)

        async def holder() -> bool:
            await asyncio.sleep(0.1)
            async with tx(p) as conn:
                lease_id = await leases.acquire(conn, account_id=acct, purpose="check", owner="w1", hard_s=60, ttl_s=30)
            try:
                async with keeper.holding(lease_id):
                    await asyncio.sleep(2.5)
                return True
            except leases.LeaseLost:
                return False

        task = asyncio.create_task(holder())
        await keeper.beat()
        return await task

    healthy_task_survived = run(db, scenario)
    db.run("DELETE FROM leases")
    assert healthy_task_survived, "a lease that is live and held was declared lost and its task cancelled"
