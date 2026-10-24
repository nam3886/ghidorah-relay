"""The soak in the fast tier: 20 seeds of `scripts/soak.py`, each a random but replayable workload
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
                    "completion, after_sent, submit_timeout_s, price_key, price) VALUES (%s, %s, %s, 'generate', "
                    "'video', 'm', 'p', %s, %s, 'detach', 'reconcile', 60, 'fake-video', 0)",
                    job_id, PROVIDER, key_id, "planted-" + job_id.hex[:8], status)
        if sets:
            await w.sql("UPDATE jobs SET %s WHERE id = %%s" % ", ".join(sets), job_id)
        return job_id

    await job("fetching", "next_poll_at = clock_timestamp()")                              # never finished
    resent = await job("done", "finished_at = clock_timestamp()")                          # done, never proven taken
    failed = await job("failed", "error_code = 'account.dead_session'")                    # failed, no finished_at
    await w.sql("INSERT INTO account_charges (account_id, job_id, pool, amount) VALUES (%s, %s, 'video', 0)",
                account_id, failed)
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


# ---- --jobs N: the seeds in N processes; a failing seed is still printed with its number and its replay command -----
CHILD = '''
import sys
seeds = [int(n) for n in sys.argv[sys.argv.index("--seed-list") + 1].split(",")]
if seeds[0] == 1000:
    sys.exit(2)                                                      # a process that dies without a word
if seeds[0] == 2000:
    sys.exit(0)                                                      # a process that runs none of its seeds and says ok
if seeds[0] == 3000:
    print("seed %-4d ok     0.1s  done=1" % seeds[0], flush=True)    # a seed that is reported twice
bad = False
for seed in seeds:
    print("seed %-4d %-4s   0.1s  done=1" % (seed, "FAIL" if seed % 7 == 3 else "ok"), flush=True)
    if seed % 7 == 3:
        bad = True
        print("   SOAK FAILED seed %d (replay: scripts/soak.py --seed %d)" % (seed, seed))
        print("   - a lease was left behind")
print("soak: %d seed(s), %d failed, 1s" % (len(seeds), int(bad)))
sys.exit(1 if bad else 0)
'''


def split_run(tmp_path, monkeypatch, capsys, seeds, jobs):
    child = tmp_path / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    monkeypatch.setattr(soak, "__file__", str(child))
    monkeypatch.setenv("RELAY_TEST_PG_TEMPLATE", "x")
    code = soak.run_split(seeds, jobs, False)
    return code, capsys.readouterr().out


@mutation(SOAK, "slices = [seeds[i::jobs] for i in range(min(jobs, len(seeds)))]",
          "slices = [seeds[:3]] * min(jobs, len(seeds))", expect="a seed ran twice or not at all")
@mutation(SOAK, "failed.append(int(found.group(1)))", "pass", expect="a failing seed was not reported")
@mutation(SOAK, "return 1 if bad else 0", "return 0", expect="a failing seed did not fail the run")
def test_a_split_soak_runs_every_seed_once_and_reports_the_failing_one_with_its_replay_command(
        tmp_path, monkeypatch, capsys):
    code, out = split_run(tmp_path, monkeypatch, capsys, list(range(12)), 4)
    lines = out.splitlines()
    for seed in range(12):
        assert len([ln for ln in lines if ln.startswith("seed %-4d " % seed)]) == 1, \
            "a seed ran twice or not at all: %d\n%s" % (seed, out)
    assert "   SOAK FAILED seed 3 (replay: scripts/soak.py --seed 3)" in lines and \
        "   SOAK FAILED seed 10 (replay: scripts/soak.py --seed 10)" in lines, "the replay line is missing\n" + out
    assert lines[-1].startswith("soak: 12 seed(s), 2 failed (3, 10), ") and out.count("soak: ") == 1, \
        "a failing seed was not reported\n" + out
    assert code == 1, "a failing seed did not fail the run"


@mutation(SOAK, "if proc.wait() != 0 and not any(s in failed for s in mine):",
          "if False and not any(s in failed for s in mine):", expect="a process that died went unnoticed")
def test_a_split_soak_whose_process_dies_without_a_failing_seed_fails_and_says_which_process(
        tmp_path, monkeypatch, capsys):
    code, out = split_run(tmp_path, monkeypatch, capsys, [1000, 1, 2], 3)
    assert code == 1 and "SOAK FAILED process 0 (seeds [1000]) exited with status 2" in out, \
        "a process that died went unnoticed\n" + out
    assert "soak: 3 seed(s), 1 failed, " in out, out


def test_a_split_soak_with_no_failure_is_green(tmp_path, monkeypatch, capsys):
    code, out = split_run(tmp_path, monkeypatch, capsys, [0, 1, 2, 4], 2)
    assert code == 0 and out.splitlines()[-1].startswith("soak: 4 seed(s), 0 failed, "), out


@mutation(SOAK, "missing = [s for s in seeds if ran[s] == 0 and s not in skipped]", "missing = []",
          expect="a seed that never ran went unnoticed")
def test_a_split_soak_fails_when_a_process_exits_zero_without_running_its_seed(tmp_path, monkeypatch, capsys):
    code, out = split_run(tmp_path, monkeypatch, capsys, [2000, 1, 2], 3)
    assert code == 1 and "SOAK FAILED seeds [2000] never ran" in out, "a seed that never ran went unnoticed\n" + out
    assert "soak: 3 seed(s), 1 failed, " in out, out


@mutation(SOAK, "twice = [s for s in seeds if ran[s] > 1]", "twice = []",
          expect="a seed that ran twice went unnoticed")
def test_a_split_soak_fails_when_a_seed_is_reported_twice(tmp_path, monkeypatch, capsys):
    code, out = split_run(tmp_path, monkeypatch, capsys, [3000, 1, 2], 3)
    assert code == 1 and "SOAK FAILED seeds [3000] ran more than once" in out, \
        "a seed that ran twice went unnoticed\n" + out


@mutation(SOAK, "            future.result()\n", "            pass\n", expect="an exception in a worker was lost")
def test_a_split_soak_fails_when_a_process_cannot_be_started(tmp_path, monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise OSError("no more processes")

    monkeypatch.setattr(soak.subprocess, "Popen", refuse)
    code, out = split_run(tmp_path, monkeypatch, capsys, [0, 1, 2, 3], 2)
    assert code == 1 and "could not be run or read: OSError('no more processes')" in out, \
        "an exception in a worker was lost\n" + out
