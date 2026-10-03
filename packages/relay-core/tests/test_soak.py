"""The soak in the fast tier (contracts §14.2): 20 seeds of `scripts/soak.py`, each a random but replayable workload
against the FakeProvider with every loop running and chaos interfering, judged by the strict invariant checker and the
soak's own rules. `scripts/check.sh --full` runs at least 200 seeds. A failing seed is replayed alone with
`scripts/soak.py --seed N`.

The second test proves the verdict itself: each rule of `soak.verify` has a planted violation that it must name."""

import asyncio
import importlib.util
import random
import uuid
from pathlib import Path

import pytest
from accountkit import object_store
from jobkit import PROVIDER

from relay_core.budget import BrowserBudget
from relay_core.providers import NotSent
from relay_core.testing.mutation import mutation

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("soak", REPO / "scripts" / "soak.py")
soak = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(soak)

SOAK = "scripts/soak.py"
APPLY = "packages/relay-core/src/relay_core/jobs/apply.py"
SEEDS = range(20)

# An interrupted submit that queued a sent job again would send the same order twice; `dies_sent` is in nearly every
# seed, so the soak kills this mutation every run. The reaper's twin (a sent job whose lease ran out) needs a worker
# restart during a hung send, which a seed only sometimes produces, so it is NOT marked here: test_reaper and
# test_crash_windows guard it deterministically (a mutation the soak kills only by luck is no guard).
INTERRUPTED_SENT = ("if job.sent_at is None:\n"
                    "            await _set(conn, job.id, \"submitting\", status=\"queued\", account_id=None,")
INTERRUPTED_ALWAYS_QUEUED = ("if True:\n"
                             "            await _set(conn, job.id, \"submitting\", status=\"queued\", account_id=None,")
JUDGE_CRASHED = "problems = list(crashed)"
JUDGE_CRASHED_OFF = "problems = []"
JUDGE_STUCK = 'problems.append("job %s never finished: it is %s" % (job_id, status))'
JUDGE_LEASES = 'problems.append("%d lease(s) left behind" % left)'
JUDGE_STRICT = "problems += await asyncio.to_thread(_strict, owner_dsn)"
LICENCE_ON_REQUEUE = 'if isinstance(result, NotSent) and before == "unknown" and status == "queued":'
LICENCE_ON_ANY_NOTSENT = 'if isinstance(result, NotSent) and before == "unknown" and True:'
LICENCE_ON_QUEUED_JOB = 'if isinstance(result, NotSent) and True and status == "queued":'
JUDGE_RESEND = "if n > 1 + w.engine.allowed[job_id]:"
JUDGE_TAKEN = "if n:\n            problems.append(\"%d done job(s) were never proven taken\" % n)"
JUDGE_REFUND = "if n:\n            problems.append(\"I7 %d failed job(s) kept an unrefunded account charge\" % n)"
JUDGE_PEAK = "if w.budget is not None and w.engine.peak > SLOTS:"
OFF = "pass"
FALSE = "if False:"


def dsns(db):
    return soak.Dsns(db.dsn, db.api_dsn, db.worker_dsn)


@pytest.fixture(scope="module")
def objects():
    with object_store() as store:
        yield store


@pytest.mark.parametrize("seed", SEEDS)
@mutation(APPLY, INTERRUPTED_SENT, INTERRUPTED_ALWAYS_QUEUED,
           expect="SOAK FAILED seed")
def test_a_seeded_random_workload_keeps_every_invariant(db, objects, seed):
    problems, stats = asyncio.run(soak.run_seed(dsns(db), seed, objects))
    assert problems == [], "SOAK FAILED seed %d (replay: scripts/soak.py --seed %d): %s" % (seed, seed, problems)
    assert sum(stats.get(s, 0) for s in ("done", "failed")) > 0, "the seed made no job"


async def plant(w):
    """Everything the verdict looks for, planted after the system has stopped."""
    key_id = (await w.sql("SELECT id FROM api_keys"))[0][0]
    account_id = (await w.sql("SELECT id FROM accounts"))[0][0]

    async def job(status: str, *sets: str) -> uuid.UUID:
        job_id = uuid.uuid4()
        await w.sql("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, "
                    "completion, after_sent, submit_timeout_s, wallet_kind, price) VALUES (%s, %s, %s, 'generate', "
                    "'video', 'm', 'p', %s, %s, 'detach', 'reconcile', 60, 'video', 0)",
                    job_id, PROVIDER, key_id, "planted-" + job_id.hex[:8], status)
        if sets:
            await w.sql("UPDATE jobs SET %s WHERE id = %%s" % ", ".join(sets), job_id)
        return job_id

    await job("fetching", "next_poll_at = clock_timestamp()")                              # never finished
    resent = await job("done", "finished_at = clock_timestamp()")                          # done, never proven taken
    failed = await job("failed", "error_code = 'account.dead_session'")                    # failed, no finished_at
    await w.sql("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) "
                "VALUES (%s, %s, 'video', 0, (clock_timestamp() AT TIME ZONE 'UTC')::date)", account_id, failed)
    await w.sql("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, 'check', 'x', "
                "clock_timestamp() + interval '1 h', clock_timestamp() + interval '1 h')", account_id)
    w.engine.sends[resent] = 5                                                             # sent 5 times, no re-send
    w.budget = w.budget or BrowserBudget(soak.SLOTS)
    w.engine.peak = soak.SLOTS + 1


@mutation(SOAK, JUDGE_CRASHED, JUDGE_CRASHED_OFF,
           expect="('the planted crash',")
@mutation(SOAK, JUDGE_STUCK, OFF,
           expect="('never finished',")
@mutation(SOAK, JUDGE_LEASES, OFF,
           expect="('lease(s) left behind',")
@mutation(SOAK, JUDGE_STRICT, OFF,
           expect="('I13 job',")
@mutation(SOAK, JUDGE_RESEND, FALSE,
           expect="('I1 job',")
@mutation(SOAK, JUDGE_TAKEN, OFF,
           expect="('never proven taken',")
@mutation(SOAK, JUDGE_REFUND, OFF,
           expect="('unrefunded account charge',")
@mutation(SOAK, JUDGE_PEAK, FALSE,
           expect="('I28',")
def test_the_soak_verdict_names_each_planted_violation(db, objects):
    problems, _ = asyncio.run(soak.run_seed(dsns(db), 3, objects, chaos_s=0.2, sabotage=plant,
                                            crash_note="the planted crash"))
    db.reset()                                                  # the planted rows are not for the autouse checker
    for expected in ("never finished", "lease(s) left behind", "I1 job", "never proven taken",
                     "unrefunded account charge", "I28", "I13 job", "the planted crash"):
        assert any(expected in p for p in problems), (expected, problems)


@mutation(SOAK, LICENCE_ON_REQUEUE, LICENCE_ON_ANY_NOTSENT, expect="== (0, 0, 1)")
@mutation(SOAK, LICENCE_ON_REQUEUE, LICENCE_ON_QUEUED_JOB, expect="== (0, 0, 1)")
def test_a_notsent_licenses_a_second_send_only_when_it_moves_an_unknown_job_to_queued(monkeypatch):
    engine = soak.SoakEngine(random.Random(0))
    reads = iter([("unknown", "unknown"), ("queued", "queued"), ("unknown", "queued")])    # (before, after) per call
    afters = []

    async def fake_status(db, job_id):
        before, after = next(reads)
        afters.append(after)
        return before

    async def fake_apply(db, job_id, result, **kw):
        return afters.pop(0)

    monkeypatch.setattr(soak, "job_status", fake_status)
    monkeypatch.setattr(soak.store, "apply_reconciled", fake_apply)

    async def go():
        got = []
        with soak.licence_requeues(engine):
            for _ in range(3):
                await soak.store.apply_reconciled(None, "j", NotSent(True))
                got.append(engine.allowed["j"])
        return tuple(got)

    # refused (still unknown): 0; the job was already queued: 0; unknown to queued: 1
    assert asyncio.run(go()) == (0, 0, 1)
