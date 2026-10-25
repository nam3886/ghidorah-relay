"""Pinned jobs: a job that works on an output of the customer's own earlier job runs only on the account that made it.
Creation checks the source in its transaction, the claim offers the job to that account alone and never lets it block
or join another account's work, a pinned job whose account can no longer run it ends at once with its refund, and a
mode with nothing to read back (`fail_refund`) fails an unclear send at once with its refund."""

import uuid

import psycopg
import pytest
from accountkit import done_job
from jobkit import account, claim, create, key, new_job, run, status, submit
from lifecyclekit import claim_new, reaper, row

from relay_core.jobs import store
from relay_core.jobs.create import SourceUnusable
from relay_core.providers import Ambiguous, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, sent
from relay_core.testing.mutation import mutation

K = "packages/relay-core/src/relay_core/jobs/claim.py"
C = "packages/relay-core/src/relay_core/jobs/create.py"
A = "packages/relay-core/src/relay_core/jobs/apply.py"
R = "packages/relay-core/src/relay_core/jobs/reaper.py"
S = "packages/relay-core/src/relay_core/jobs/store.py"


def charges(db, job_id) -> int:
    return db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'charge'", job_id)[0]


def refunded(db, job_id) -> bool:
    return db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND scope = '*' AND entry = 'refund'", job_id)[0] == 1


def source_of(db, k, acct) -> uuid.UUID:
    return done_job(db, k, acct, ref="batch-%s" % uuid.uuid4().hex[:8])


# ---- creation --------------------------------------------------------------------------------------------------------
@mutation(C, "AND key_id = %s AND provider = %s AND status = 'done' ",
          "AND %s::uuid IS NOT NULL AND provider = %s AND status = 'done' ",
          expect="another key's job is never a source")
def test_a_pinned_job_is_pinned_to_the_account_that_made_its_source_and_only_a_finished_job_of_the_key_is_one(db):
    k, other = key(db), key(db)
    acct = account(db)
    src = source_of(db, k, acct)
    foreign = source_of(db, other, acct)
    running = db.job(k, provider="fake")

    async def scenario(p):
        made = await create(p, k, "extend it", source=src)
        refused = []
        for bad in (foreign, running, uuid.uuid4()):
            try:
                await create(p, k, "extend it", source=bad)
            except SourceUnusable as e:
                refused.append(e.code)
        return made, refused

    made, refused = run(db, scenario)
    assert row(db, made.job_id, "source_job_id, pin_account_id") == (src, acct), "pinned to the source's account"
    assert refused == ["input.bad_source"] * 3, "another key's job is never a source: %s" % refused
    assert db.one("SELECT count(*) FROM jobs WHERE source_job_id IS NOT NULL") == (1,)
    assert db.one("SELECT count(*) FROM ledger WHERE key_id = %s", k) == (2,), "a refused source is never charged"


def test_a_source_job_goes_with_a_plan_that_uses_one_and_only_with_it():
    k = uuid.uuid4()
    with pytest.raises(ValueError, match="source job"):
        new_job(k, uses_source=True)
    plain = new_job(k)
    with pytest.raises(ValueError, match="source job"):
        store.NewJob(plain.provider, k, plain.plan, "p", source_job_id=uuid.uuid4())


# ---- the claim -------------------------------------------------------------------------------------------------------
PINNED_ONLY = '"tried": list(head_tried), "only": pin}'
NOT_PINNED = '"tried": list(head_tried), "only": None}'


@mutation(K, PINNED_ONLY, NOT_PINNED, expect="jobs_pinned_account")    # the last guard refuses the claim
def test_a_pinned_job_is_claimed_only_by_the_account_that_made_its_source(db):
    k = key(db)
    owner = account(db, used_ago_s=10)
    account(db)                               # never used: first in the LRU order, free, able to pay

    async def scenario(p):
        made = await create(p, k, "extend it", source=source_of(db, k, owner))
        return made, await claim(p)

    made, got = run(db, scenario)
    assert got.job_id == made.job_id and got.account_id == owner, "a pinned job runs only on its account"
    assert row(db, made.job_id, "status, account_id") == ("submitting", owner)


@mutation(K, "   AND pin_account_id IS NULL\n ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED",
          "\n ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED",
          expect="jobs_pinned_account")      # the last guard refuses the claim
def test_a_waiting_pinned_job_never_blocks_newer_jobs_and_no_other_account_takes_it(db):
    k = key(db)
    owner = account(db, cooldown_s=600)       # cooling: its pinned job waits
    free = account(db)

    async def scenario(p):
        pinned = await create(p, k, "extend it", source=source_of(db, k, owner))
        newer = await create(p, k, "a plain job")
        first, second = await claim(p), await claim(p)
        return pinned, newer, first, second

    pinned, newer, first, second = run(db, scenario)
    assert first is not None and first.job_id == newer.job_id and first.account_id == free, (
        "another account's pinned job is never picked, nor does it hold up a newer job: %s" % (first,))
    assert second is None and status(db, pinned.job_id) == "queued", "another account's pinned job is never picked"


def test_the_database_refuses_a_pinned_job_on_any_other_account(db):
    k = key(db)
    owner, other = account(db), account(db)
    made = run(db, lambda p: create(p, k, "extend it", source=source_of(db, k, owner)))
    with pytest.raises(psycopg.errors.CheckViolation, match="jobs_pinned_account"):
        db.run("UPDATE jobs SET account_id = %s WHERE id = %s", other, made.job_id)
    db.run("UPDATE jobs SET account_id = %s WHERE id = %s", owner, made.job_id)    # its own account is fine
    db.run("UPDATE jobs SET account_id = NULL WHERE id = %s", made.job_id)


# ---- a pinned job whose account can no longer run it ----------------------------------------------------------------
@pytest.mark.parametrize("state", ["dead", "disabled"])
@mutation(R, '            if state != "available":', '            if False:', expect="ends at once")
def test_a_pinned_job_of_a_dead_or_disabled_account_ends_at_once_with_its_refund(db, state):
    k = key(db)
    owner = account(db)

    async def scenario(p):
        made = await create(p, k, "extend it", source=source_of(db, k, owner))
        db.run("UPDATE accounts SET state = %s WHERE id = %s", state, owner)
        return made, (await reaper(p).tick())["blocked_pins"]

    made, ended = run(db, scenario)
    assert ended == 1, "ends at once: %d" % ended
    assert row(db, made.job_id, "status, error_code") == ("failed", "source.account_unavailable"), "ends at once"
    assert refunded(db, made.job_id), "the charge is returned"


@mutation(R, "if cap is None or cap.left_n is None or cap.left_n >= cost:",
          "if cap is None or cap.left_n is None or cap.left_n >= 0:", expect="an account out of quota")
def test_a_pinned_job_of_an_account_out_of_quota_ends_at_once_and_one_that_can_pay_waits_its_turn(db):
    k = key(db)
    empty, busy = account(db, left=0), account(db, left=5, cooldown_s=600)

    async def scenario(p):
        broke = await create(p, k, "extend it", source=source_of(db, k, empty))
        later = await create(p, k, "extend it", source=source_of(db, k, busy))
        return broke, later, (await reaper(p).tick())["blocked_pins"]

    broke, later, ended = run(db, scenario)
    assert ended == 1, "an account out of quota ends its pinned job at once: %d" % ended
    assert row(db, broke.job_id, "status, error_code") == ("failed", "source.account_exhausted"), (
        "an account out of quota ends its pinned job at once")
    assert refunded(db, broke.job_id)
    assert status(db, later.job_id) == "queued", "a pause that passes (a cooldown) only waits"


# ---- the job view ----------------------------------------------------------------------------------------------------
@mutation(S, "FROM jobs j LEFT JOIN jobs s ON s.id = j.source_job_id WHERE j.id = %s",
          "FROM jobs j LEFT JOIN jobs s ON false WHERE j.id = %s", expect="the engine sees the source")
def test_the_engine_sees_the_source_jobs_reference_and_state(db):
    k, acct = key(db), account(db)
    src = source_of(db, k, acct)
    db.run("UPDATE jobs SET external_ref = 'batch-src', "
           "provider_state = '{\"batch\": \"batch-src\", \"outputs\": [{\"ref\": \"c-0\"}]}' WHERE id = %s", src)

    async def scenario(p):
        made = await create(p, k, "extend it", source=src)
        plain = await create(p, k, "plain")
        return await store.job_view(p, made.job_id), await store.job_view(p, plain.job_id)

    view, plain = run(db, scenario)
    assert view.source is not None, "the engine sees the source"
    assert (view.source.id, view.source.external_ref, dict(view.source.provider_state), view.source.idx) == (
        str(src), "batch-src", {"batch": "batch-src", "outputs": [{"ref": "c-0"}]}, 0)
    assert plain.source is None


# ---- fail_refund: an unclear send of a mode with nothing to read back ------------------------------------------------
@mutation(A, '    if job.after_sent == "fail_refund":', '    if False:', expect="fails at once")
def test_an_unclear_send_of_a_fail_refund_mode_fails_at_once_with_its_refund_and_a_reconcile_mode_waits(db):
    k = key(db)
    account(db)

    async def scenario(p):
        out = {}
        for mode in ("fail_refund", "reconcile"):
            got = await claim_new(p, k, mode, after_sent=mode)
            engine = FakeEngine()
            engine.script_submit("*", sent(SubmitResult(Ambiguous("no answer"))))
            out[mode] = (got.job_id, await submit(p, got, engine))
            db.run("UPDATE accounts SET last_used_at = NULL")
        return out

    out = run(db, scenario)
    job, st = out["fail_refund"]
    assert st == "failed" and row(db, job, "error_code, sent_at IS NOT NULL") == ("render.unclear_send", True), (
        "an unclear send of a fail_refund mode fails at once")
    assert refunded(db, job)
    assert out["reconcile"][1] == "unknown"


@mutation(R, '            elif row[3] == "fail_refund":', '            elif False:', expect="never left unknown")
def test_a_lease_that_expires_after_the_send_of_a_fail_refund_mode_fails_it_with_its_refund(db):
    k = key(db)
    account(db)

    async def scenario(p):
        got = await claim_new(p, k, "x", after_sent="fail_refund")
        await store.mark_sent(p, got)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", got.lease_id)
        await reaper(p).tick()
        return got.job_id

    job = run(db, scenario)
    assert row(db, job, "status, error_code") == ("failed", "render.unclear_send"), "never left unknown"
    assert refunded(db, job)

