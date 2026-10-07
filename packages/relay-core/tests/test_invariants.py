"""The invariant suite's own guards (contracts §12, §14.3).

1. Every rule of the global checker (`testing/checker.py`) has a planted violation that it must name, and a mutation
   that switches that one rule off.
2. The matrix: one row per invariant, naming the tests that prove it. The matrix is itself checked: a cited test
   that was renamed or lost its mutation turns this file red.
3. The invariants that had no test in both completion modes, or none at all (I1, I2, I8, I13).
"""

import ast
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from accountkit import object_store
from crashkit import strict
from enginekit import in_status, job_status, make_loop, running, until
from jobkit import CAPS, POOLS, account, claim, create, key, run
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
    to_waiting,
)

from relay_core.jobs import store
from relay_core.jobs.claim import Claim
from relay_core.jobs.recheck import JobRefused, StoredOutput, recheck_job, recover_job
from relay_core.leases import LeaseLost
from relay_core.providers import Accepted, Done, Found, Pending, PollResult, SubmitResult
from relay_core.testing.checker import violations
from relay_core.testing.fake_provider import FakeCtx, FakeEngine, sent
from relay_core.testing.mutation import mutation

CK = "packages/relay-core/src/relay_core/testing/checker.py"
TESTS = Path(__file__).resolve().parent
SELF = "packages/relay-core/tests/test_invariants.py"


# ---- 1. every checker rule has a planted violation and a mutation ---------------------------------------------------
def make_job(db, *sets: str) -> uuid.UUID:
    """A queued p1 job with `sets` (SQL assignments) applied on top."""
    job = db.job(db.key(("p1", "video", None, None)))
    if sets:
        db.run("UPDATE jobs SET %s WHERE id = %%s" % ", ".join(sets), job)
    return job


def terminal(*sets: str) -> tuple[str, ...]:
    return ("status = 'done'", "finished_at = now()", *sets)


def charge(db, job, amount: int, entry: str = "charge") -> None:
    kid = db.one("SELECT key_id FROM jobs WHERE id = %s", job)[0]
    db.run("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
           "VALUES (%s, %s, 'p1', 'video', %s, %s, current_date)", kid, job, entry, amount)


def command(db, status: str, *, lease: int | None = None) -> uuid.UUID:
    cid = uuid.uuid4()
    db.run("INSERT INTO commands (id, provider, kind, args, status, lease_id) VALUES (%s, 'p1', 'wake', '{}', %s, %s)",
           cid, status, lease)
    return cid


def long_text() -> str:
    return "x" * 2200


# case id -> (tag, a keyword of the rule's text, planter returning the id the line must name)
CASES = {
    "queued_with_sent_at": ("I9", "still has sent_at", lambda db: make_job(db, "sent_at = now()")),
    "failed_not_refunded": ("I21", "key charge is not refunded", lambda db: _failed_charged(db)),
    "charge_without_taken": ("I7", "account charge for a job whose order", lambda db: _untaken_charge(db)),
    "facts_without_taken": ("I7", "facts stored", lambda db: make_job(db, "upstream_price = 3")),
    "taken_without_charge": ("I7", "without its account charge",
                             lambda db: make_job(db, "taken_at = now()", "pool = 'video'", "account_cost = 2")),
    "charge_not_account_cost": ("I7", "not the job's account_cost", lambda db: _wrong_amount(db)),
    "done_without_media": ("I11", "without an output media row", lambda db: make_job(db, *terminal())),
    "done_with_fewer_rows": ("I11", "fewer output rows", lambda db: _fewer_rows(db)),
    "waiting_without_ref": ("I1", "without an upstream reference", lambda db: make_job(db, "status = 'waiting'",
                                                                                      "next_poll_at = now()")),
    "unknown_never_sent": ("I1", "never sent", lambda db: make_job(db, "status = 'unknown'")),
    "submitting_without_lease": ("I1", "no lease row", lambda db: make_job(db, "status = 'submitting'")),
    "priced_job_without_charge": ("I3", "no charge row", lambda db: make_job(db, "price = 5")),
    "net_charge_neither": ("I8", "neither 0 nor its price", lambda db: _half_charged(db)),
    "terminal_without_finished_at": ("I13", "terminal job without finished_at",
                                     lambda db: make_job(db, "status = 'done'")),
    "open_job_with_finished_at": ("I13", "not terminal still has finished_at",
                                  lambda db: make_job(db, "finished_at = now()")),
    "failed_without_code": ("I13", "without an error code",
                            lambda db: make_job(db, "status = 'failed'", "finished_at = now()")),
    "terminal_with_token": ("I13", "still holds a poll token",
                            lambda db: make_job(db, *terminal("poll_token = gen_random_uuid()"))),
    "token_on_queued_job": ("I23", "neither waiting nor fetching",
                            lambda db: make_job(db, "poll_token = gen_random_uuid()")),
    "waiting_without_next_poll": ("I14", "no next_poll_at",
                                  lambda db: make_job(db, "status = 'waiting'", "external_ref = 'r'")),
    "pending_hook_incomplete": ("I16", "pending webhook without",
                                lambda db: make_job(db, "callback_status = 'pending'", "callback_url = 'https://h.test'")),
    "hook_over_three_attempts": ("I16", "more than 3 attempts", lambda db: make_job(db, "callback_attempts = 4")),
    "terminal_hook_without_status": ("I16", "no webhook status",
                                     lambda db: make_job(db, *terminal("callback_url = 'https://h.test'"))),
    "running_command_lease_gone": ("I14", "running command whose lease is gone", lambda db: command(db, "running",
                                                                                                  lease=987654)),
    "finished_command_without_time": ("I14", "finished command without finished_at",
                                      lambda db: command(db, "done")),
    "debug_media_with_token": ("I12", "debug media row with a share token", lambda db: _media(db, "debug", "tok-1")),
    "input_link_without_expiry": ("I12", "input media link with no expiry", lambda db: _media(db, "input", "tok-2")),
    "job_error_not_redacted": ("I24", "job error or webhook answer",
                               lambda db: make_job(db, "error = '%s'" % long_text())),
    "account_error_not_redacted": ("I24", "account error over", lambda db: _account_error(db)),
    "event_not_redacted": ("I24", "event text over", lambda db: _long_event(db)),
    "live_reconcile_lease_on_waiting_job": ("I14", "live reconcile lease", lambda db: _reconcile_lease(db)),
}


def _failed_charged(db):
    job = make_job(db, "status = 'failed'", "finished_at = now()", "error_code = 'x.y'", "price = 5")
    charge(db, job, 5)
    return job


def _untaken_charge(db):
    job = make_job(db, "status = 'waiting'", "external_ref = 'r'", "next_poll_at = now()")
    acct = account_row(db)
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) VALUES (%s, %s, 'video', 1, "
           "current_date)", acct, job)
    return job


def _wrong_amount(db):
    job = make_job(db, "account_cost = 2", "pool = 'video'")
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) VALUES (%s, %s, 'video', 1, "
           "current_date)", account_row(db), job)
    return job


def _fewer_rows(db):
    job = make_job(db, *terminal("provider_state = '{\"outputs\": [{}, {}]}'"))
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
           "VALUES (%s, 'output', 0, 'k', 'video/mp4', 1)", job)
    return job


def _half_charged(db):
    job = make_job(db, "price = 5")
    charge(db, job, 5)
    charge(db, job, 3)
    return job


def _media(db, role: str, token: str):
    job = make_job(db)
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, share_token) "
           "VALUES (%s, %s, 0, 'k', 'image/png', 1, %s)", job, role, token)
    return job


def account_row(db) -> uuid.UUID:
    acct = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state) VALUES (%s, 'p1', '\\x01', 'available')", acct)
    return acct


def _account_error(db):
    acct = account_row(db)
    db.run("UPDATE accounts SET last_error = %s WHERE id = %s", long_text(), acct)
    return acct


def _long_event(db):
    job = make_job(db)
    return db.one("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'system', 's', %s) RETURNING id",
                  job, long_text())[0]


def _reconcile_lease(db):
    job = make_job(db, "status = 'waiting'", "external_ref = 'r'", "next_poll_at = now()")
    db.run("INSERT INTO leases (account_id, job_id, purpose, owner, until, hard_until) VALUES (%s, %s, 'reconcile', "
           "'w', now() + interval '1 h', now() + interval '1 h')", account_row(db), job)
    return db.one("SELECT id FROM leases")[0]


QUEUED_SENT = "WHERE status = 'queued' AND sent_at IS NOT NULL"
QUEUED_SENT_OFF = "WHERE false AND status = 'queued' AND sent_at IS NOT NULL"
FAILED_CHARGED = "WHERE job_id IN (SELECT id FROM jobs WHERE status = 'failed')"
FAILED_CHARGED_OFF = "WHERE false AND job_id IN (SELECT id FROM jobs WHERE status = 'failed')"
UNTAKEN_CHARGE = "WHERE j.taken_at IS NULL AND j.status IN"
UNTAKEN_CHARGE_OFF = "WHERE false AND j.taken_at IS NULL AND j.status IN"
UNTAKEN_FACTS = "WHERE taken_at IS NULL AND (upstream_price"
UNTAKEN_FACTS_OFF = "WHERE false AND taken_at IS NULL AND (upstream_price"
TAKEN_UNCHARGED = "WHERE taken_at IS NOT NULL AND pool IS NOT NULL"
TAKEN_UNCHARGED_OFF = "WHERE false AND taken_at IS NOT NULL AND pool IS NOT NULL"
WRONG_AMOUNT = "WHERE c.amount <> j.account_cost"
WRONG_AMOUNT_OFF = "WHERE false AND c.amount <> j.account_cost"
DONE_NO_MEDIA = "WHERE status = 'done' AND NOT EXISTS "
DONE_NO_MEDIA_OFF = "WHERE false AND status = 'done' AND NOT EXISTS "
DONE_FEW_ROWS = "WHERE status = 'done' AND jsonb_typeof"
DONE_FEW_ROWS_OFF = "WHERE false AND status = 'done' AND jsonb_typeof"
WAITING_NO_REF = "WHERE status = 'waiting' AND external_ref IS NULL"
WAITING_NO_REF_OFF = "WHERE false AND status = 'waiting' AND external_ref IS NULL"
UNKNOWN_UNSENT = "WHERE status = 'unknown' AND sent_at IS NULL"
UNKNOWN_UNSENT_OFF = "WHERE false AND status = 'unknown' AND sent_at IS NULL"
SUBMITTING_NO_LEASE = "jobs WHERE status = 'submitting' "
SUBMITTING_NO_LEASE_OFF = "jobs WHERE false AND status = 'submitting' "
PRICED_NO_CHARGE = "WHERE price > 0 AND NOT EXISTS "
PRICED_NO_CHARGE_OFF = "WHERE false AND price > 0 AND NOT EXISTS "
NET_CHARGE = "WHERE j.price > 0 GROUP BY"
NET_CHARGE_OFF = "WHERE false AND j.price > 0 GROUP BY"
TERMINAL_NO_END = "WHERE status IN %s AND finished_at IS NULL"
TERMINAL_NO_END_OFF = "WHERE false AND status IN %s AND finished_at IS NULL"
OPEN_WITH_END = "WHERE status NOT IN %s AND finished_at IS NOT NULL"
OPEN_WITH_END_OFF = "WHERE false AND status NOT IN %s AND finished_at IS NOT NULL"
FAILED_NO_CODE = "WHERE status = 'failed' AND error_code IS NULL"
FAILED_NO_CODE_OFF = "WHERE false AND status = 'failed' AND error_code IS NULL"
TERMINAL_TOKEN = "WHERE status IN %s AND (poll_token IS NOT NULL"
TERMINAL_TOKEN_OFF = "WHERE false AND status IN %s AND (poll_token IS NOT NULL"
TOKEN_ON_IDLE = "WHERE poll_token IS NOT NULL AND status NOT IN"
TOKEN_ON_IDLE_OFF = "WHERE false AND poll_token IS NOT NULL AND status NOT IN"
WAITING_NO_POLL = "WHERE status IN ('waiting', 'fetching') AND next_poll_at IS NULL"
WAITING_NO_POLL_OFF = "WHERE false AND status IN ('waiting', 'fetching') AND next_poll_at IS NULL"
HOOK_INCOMPLETE = "jobs WHERE callback_status = 'pending' "
HOOK_INCOMPLETE_OFF = "jobs WHERE false AND callback_status = 'pending' "
HOOK_ATTEMPTS = "WHERE callback_attempts > 3"
HOOK_ATTEMPTS_OFF = "WHERE false AND callback_attempts > 3"
HOOK_NO_STATUS = "WHERE status IN %s AND callback_url IS NOT NULL"
HOOK_NO_STATUS_OFF = "WHERE false AND status IN %s AND callback_url IS NOT NULL"
COMMAND_LEASE_GONE = "FROM commands c WHERE status = 'running'"
COMMAND_LEASE_GONE_OFF = "FROM commands c WHERE false AND status = 'running'"
COMMAND_NO_END = "FROM commands WHERE status IN ('done', 'failed')"
COMMAND_NO_END_OFF = "FROM commands WHERE false AND status IN ('done', 'failed')"
DEBUG_TOKEN = "WHERE role = 'debug' AND share_token IS NOT NULL"
DEBUG_TOKEN_OFF = "WHERE false AND role = 'debug' AND share_token IS NOT NULL"
INPUT_NO_EXPIRY = "WHERE role = 'input' AND share_token IS NOT NULL"
INPUT_NO_EXPIRY_OFF = "WHERE false AND role = 'input' AND share_token IS NOT NULL"
LONG_JOB_TEXT = "FROM jobs WHERE length(error) > 2100"
LONG_JOB_TEXT_OFF = "FROM jobs WHERE false AND length(error) > 2100"
LONG_ACCOUNT_TEXT = "FROM accounts WHERE length(last_error) > 2100"
LONG_ACCOUNT_TEXT_OFF = "FROM accounts WHERE false AND length(last_error) > 2100"
LONG_EVENT_TEXT = "FROM job_events WHERE length(text) > 2100"
LONG_EVENT_TEXT_OFF = "FROM job_events WHERE false AND length(text) > 2100"
RECONCILE_LEASE = 'if live and purpose == "reconcile" and job_id is not None and job_status != "unknown":'
RECONCILE_LEASE_OFF = "if False:"

@pytest.mark.parametrize("case", sorted(CASES))
@mutation(CK, QUEUED_SENT, QUEUED_SENT_OFF,
           expect="('queued_with_sent_at', [])")
@mutation(CK, FAILED_CHARGED, FAILED_CHARGED_OFF,
           expect="('failed_not_refunded', ['I25 wallet")
@mutation(CK, UNTAKEN_CHARGE, UNTAKEN_CHARGE_OFF,
           expect='(\'charge_without_taken\', ["I7 job')
@mutation(CK, UNTAKEN_FACTS, UNTAKEN_FACTS_OFF,
           expect="('facts_without_taken', [])")
@mutation(CK, TAKEN_UNCHARGED, TAKEN_UNCHARGED_OFF,
           expect="('taken_without_charge', [])")
@mutation(CK, WRONG_AMOUNT, WRONG_AMOUNT_OFF,
           expect="('charge_not_account_cost', [])")
@mutation(CK, DONE_NO_MEDIA, DONE_NO_MEDIA_OFF,
           expect="('done_without_media', [])")
@mutation(CK, DONE_FEW_ROWS, DONE_FEW_ROWS_OFF,
           expect="('done_with_fewer_rows', [])")
@mutation(CK, WAITING_NO_REF, WAITING_NO_REF_OFF,
           expect="('waiting_without_ref', [])")
@mutation(CK, UNKNOWN_UNSENT, UNKNOWN_UNSENT_OFF,
           expect="('unknown_never_sent', [])")
@mutation(CK, SUBMITTING_NO_LEASE, SUBMITTING_NO_LEASE_OFF,
           expect="('submitting_without_lease', [])")
@mutation(CK, PRICED_NO_CHARGE, PRICED_NO_CHARGE_OFF,
           expect="('priced_job_without_charge', [])")
@mutation(CK, NET_CHARGE, NET_CHARGE_OFF,
           expect="('net_charge_neither', ['I25 wallet")
@mutation(CK, TERMINAL_NO_END, TERMINAL_NO_END_OFF,
           expect="('terminal_without_finished_at', ['I11 job")
@mutation(CK, OPEN_WITH_END, OPEN_WITH_END_OFF,
           expect="('open_job_with_finished_at', [])")
@mutation(CK, FAILED_NO_CODE, FAILED_NO_CODE_OFF,
           expect="('failed_without_code', [])")
@mutation(CK, TERMINAL_TOKEN, TERMINAL_TOKEN_OFF,
           expect="('terminal_with_token', ['I11 job")
@mutation(CK, TOKEN_ON_IDLE, TOKEN_ON_IDLE_OFF,
           expect="('token_on_queued_job', [])")
@mutation(CK, WAITING_NO_POLL, WAITING_NO_POLL_OFF,
           expect="('waiting_without_next_poll', [])")
@mutation(CK, HOOK_INCOMPLETE, HOOK_INCOMPLETE_OFF,
           expect="('pending_hook_incomplete', [])")
@mutation(CK, HOOK_ATTEMPTS, HOOK_ATTEMPTS_OFF,
           expect="('hook_over_three_attempts', [])")
@mutation(CK, HOOK_NO_STATUS, HOOK_NO_STATUS_OFF,
           expect="('terminal_hook_without_status', ['I11 job")
@mutation(CK, COMMAND_LEASE_GONE, COMMAND_LEASE_GONE_OFF,
           expect="('running_command_lease_gone', [])")
@mutation(CK, COMMAND_NO_END, COMMAND_NO_END_OFF,
           expect="('finished_command_without_time', [])")
@mutation(CK, DEBUG_TOKEN, DEBUG_TOKEN_OFF,
           expect="('debug_media_with_token', [])")
@mutation(CK, INPUT_NO_EXPIRY, INPUT_NO_EXPIRY_OFF,
           expect="('input_link_without_expiry', [])")
@mutation(CK, LONG_JOB_TEXT, LONG_JOB_TEXT_OFF,
           expect="('job_error_not_redacted', [])")
@mutation(CK, LONG_ACCOUNT_TEXT, LONG_ACCOUNT_TEXT_OFF,
           expect="('account_error_not_redacted', [])")
@mutation(CK, LONG_EVENT_TEXT, LONG_EVENT_TEXT_OFF,
           expect="('event_not_redacted', [])")
@mutation(CK, RECONCILE_LEASE, RECONCILE_LEASE_OFF,
           expect="('live_reconcile_lease_on_waiting_job', [])")
def test_every_checker_rule_names_its_planted_violation(db, case):
    tag, keyword, plant = CASES[case]
    ident = plant(db)
    with db.connect() as c:
        found = violations(c, strict=True)
    db.reset()                                    # the autouse checker must not see the planted rows
    assert any(line.startswith(tag) and str(ident) in line and keyword in line for line in found), (case, found)


# ---- 3. invariants that needed a test in both completion modes, or any test ------------------------------------------
A = "packages/relay-core/src/relay_core/jobs/apply.py"
F = "packages/relay-core/src/relay_core/jobs/fail.py"
MODES = ("hold", "detach")
SENT_NOT_QUEUED = ("if job.sent_at is None:\n"
                   "            await _set(conn, job.id, \"submitting\", status=\"queued\", account_id=None,")
ALWAYS_QUEUED = ("if True:\n"
                 "            await _set(conn, job.id, \"submitting\", status=\"queued\", account_id=None,")
FENCED = "await leases.fence(conn, claim.lease_id)\n    job = await _lock_job(conn, claim.job_id)"
UNFENCED = "pass\n    job = await _lock_job(conn, claim.job_id)"
CHARGE_ONCE = "pools[job.pool], job.account_cost)"
CHARGE_PER_OUTPUT = "pools[job.pool], job.account_cost * 3)"
UNKNOWN_ONLY = 'if job.status != "unknown":\n        return job.status'
UNKNOWN_ANY = 'if False:\n        return job.status'
STATUS_GUARD = " WHERE id = %(id)s AND status = ANY(%(expect)s)"
NO_STATUS_GUARD = " WHERE id = %(id)s"


@pytest.mark.parametrize("mode", MODES)
@mutation(A, SENT_NOT_QUEUED, ALWAYS_QUEUED,
           expect="invariants broken by this test: ['I9 job")
def test_a_submit_that_died_after_the_send_is_never_sent_again_in_either_mode(db, mode):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        engine = FakeEngine()
        made = await create(p, k, "p", completion=mode)
        engine.script_submit(made.job_id, sent(ConnectionError("the socket died after the send")))
        async with running(make_loop(p, engine)):
            await until(in_status(db, made.job_id, "unknown"), what="the dead submit to be resolved")
            for _ in range(3):                              # every other loop gets its chance to re-send it
                due(db, made.job_id)
                await reaper(p).tick()
                await pass_once(reconciler(p, engine))
                await asyncio.sleep(0.1)
        return made.job_id, engine

    job_id, engine = run(db, scenario)
    assert [c for c in engine.calls if c[0] == "submit"] == [("submit", str(job_id))]
    assert db.one("SELECT status, claims, attempts, sent_at IS NOT NULL FROM jobs WHERE id = %s", job_id) == (
        "unknown", 1, 0, True)
    assert db.wallet(k, "fake", "video") == (10, 10), "the key stays charged: the order may exist upstream"
    assert strict(db) == [] and job_status(db, job_id) == "unknown"


@pytest.mark.parametrize("mode", MODES)
@mutation(A, FENCED, UNFENCED,
           expect='an expired lease applied a result')
def test_a_result_is_applied_only_under_a_live_lease_in_either_mode(db, mode):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        await create(p, k, "p", completion=mode)
        got = await claim(p)
        engine = FakeEngine()
        view = await store.job_view(p, got.job_id)
        await store.mark_sent(p, got)
        result = await engine.submit(FakeCtx(), view)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", got.lease_id)
        try:
            await store.apply_submit(p, got, result, caps=CAPS, pools=POOLS)
        except LeaseLost:
            return got.job_id
        raise AssertionError("an expired lease applied a result")

    job_id = run(db, scenario)
    assert db.one("SELECT status, external_ref, taken_at IS NULL FROM jobs WHERE id = %s", job_id) == (
        "submitting", None, True), "nothing was written"
    assert db.one("SELECT count(*) FROM account_charges")[0] == 0
    db.run("DELETE FROM leases")
    db.run("UPDATE jobs SET status = 'queued', sent_at = NULL")       # leave a state the strict checker accepts
    assert strict(db) == []


@pytest.mark.parametrize("mode", MODES)
@mutation(A, CHARGE_ONCE, CHARGE_PER_OUTPUT,
           expect='invariants broken by this test: ["I7 job')
def test_all_outputs_of_one_job_are_delivered_at_the_one_price_in_either_mode(db, mode):
    k, _ = key(db), account(db, left=100)

    with object_store() as objects:
        async def scenario(p):
            engine = MediaEngine()
            if mode == "hold":
                job_id = await to_fetching(p, k, n=3, account_cost=2)
            else:
                engine.script_submit("*", sent(accepted()))
                job_id = await to_waiting(p, k, engine=engine, completion="detach", account_cost=2)
                engine.script_poll(job_id, PollResult(Done(engine.outputs(await store.job_view(p, job_id), 3))))
                due(db, job_id)
                await pass_once(poller(p, engine))
            due(db, job_id)
            await pass_once(fetcher(p, engine, objects))
            extra = StoredOutput("%s/output/9.mp4" % job_id, "video/mp4", 1, "extra")
            recovered = await recover_job(p, job_id, [extra])
            return job_id, recovered

        job_id, recovered = run(db, scenario)
    assert db.one("SELECT status FROM jobs WHERE id = %s", job_id) == ("done",)
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND role = 'output'", job_id) == (4,)
    assert db.all("SELECT entry, amount FROM ledger WHERE job_id = %s", job_id) == [("charge", 10)]
    assert db.all("SELECT amount FROM account_charges WHERE job_id = %s", job_id) == [(2,)]
    assert db.wallet(k, "fake", "video") == (10, 10) and (recovered.attached, recovered.recharged) == (1, 0)
    assert strict(db) == []


def snapshot(db, job_id) -> tuple:
    """Everything a writer could change about a job: its row, its events, its money and its media."""
    return (db.one("SELECT to_jsonb(j) - 'updated_at' FROM jobs j WHERE id = %s", job_id),
            db.one("SELECT count(*) FROM job_events WHERE job_id = %s", job_id),
            db.all("SELECT entry, amount FROM ledger WHERE job_id = %s ORDER BY id", job_id),
            db.all("SELECT amount, refunded_at IS NULL FROM account_charges WHERE job_id = %s ORDER BY id", job_id),
            db.one("SELECT count(*) FROM job_media WHERE job_id = %s", job_id))


@pytest.mark.parametrize("final", ("done", "failed"))
@mutation(A, UNKNOWN_ONLY, UNKNOWN_ANY,
           expect="invariants broken by this test: ['I14 live job lease 1 on a job that i")
@mutation(F, STATUS_GUARD, NO_STATUS_GUARD,
           expect="invariants broken by this test: ['I14 live job lease 1 on a job that i")
def test_a_terminal_job_changes_only_through_recheck_and_recover(db, final):
    k, acct = key(db), account(db, left=100)

    async def scenario(p):
        job_id = (await create(p, k, "p")).job_id
        if final == "failed":
            assert await store.fail_job(p, job_id, expect=("queued",), code="account.prompt_mismatch", pools=POOLS)
            db.run("UPDATE jobs SET external_ref = 'r', sent_at = now() WHERE id = %s", job_id)
        else:
            db.run("UPDATE jobs SET status = 'done', finished_at = now(), external_ref = 'r' WHERE id = %s", job_id)
            db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, upstream_id) "
                   "VALUES (%s, 'output', 0, 'k', 'video/mp4', 1, 'up-0')", job_id)
        age(db, job_id, "created_at", 100_000)
        db.run("INSERT INTO leases (account_id, job_id, purpose, owner, until, hard_until) VALUES (%s, %s, 'job', "
               "'w1', now() + interval '1 h', now() + interval '1 h')", acct, job_id)
        lease_id = db.one("SELECT id FROM leases")[0]
        claimed = Claim(job_id, lease_id, acct, "fake", "video", 1, 60, "w1")
        found = Found("ref", {}, taken=True)
        late = datetime.now(UTC) + timedelta(days=1)
        stored = [StoredOutput("k2", "video/mp4", 1, "up-9")]
        kw = dict(caps=CAPS, pools=POOLS)
        gone = ("queued", "submitting", "waiting", "unknown", "fetching")
        writers = {
            "apply_poll": lambda: store.apply_poll(p, job_id, uuid.uuid4(), PollResult(Pending("x", 5)), **kw),
            "apply_reconciled": lambda: store.apply_reconciled(p, job_id, found, read_started_at=late, **kw),
            "apply_fetched": lambda: store.apply_fetched(p, job_id, uuid.uuid4(), stored),
            "apply_fetch_failed": lambda: store.apply_fetch_failed(
                p, job_id, uuid.uuid4(), code="render.download_failed", detail="x", retry_in_s=1, deadline_s=1,
                pools=POOLS),
            "fail_job": lambda: store.fail_job(p, job_id, expect=gone, code="account.prompt_mismatch", pools=POOLS),
            "apply_submit": lambda: store.apply_submit(p, claimed, SubmitResult(Accepted("r2")), **kw),
            "apply_interrupted": lambda: store.apply_interrupted(p, claimed, **kw),
            "mark_sent": lambda: store.mark_sent(p, claimed),
            "report_ref": lambda: store.report_ref(p, claimed, "r3", {}),
            "reaper": lambda: reaper(p).tick(),
            "restore_mark": lambda: store.restore_mark(p, late),
        }
        refused = {}
        for name, call in writers.items():
            before = snapshot(db, job_id)
            try:
                await call()
            except (LeaseLost, RuntimeError, JobRefused, LookupError) as e:
                refused[name] = type(e).__name__
            assert snapshot(db, job_id) == before, "%s changed a %s job" % (name, final)
        db.run("DELETE FROM leases")
        return job_id, refused

    job_id, refused = run(db, scenario)
    assert refused["apply_submit"] == "LeaseLost" and refused["apply_interrupted"] == "LeaseLost"
    assert "apply_reconciled" not in refused, "a reconcile result for a finished job is dropped quietly"
    assert strict(db) == []

    async def allowed(p):
        if final == "done":
            with pytest.raises(JobRefused) as refusal:
                await recheck_job(p, job_id)
            assert refusal.value.code == "job.already_finished"
            await recover_job(p, job_id, [StoredOutput("k3", "video/mp4", 1, "up-3")])
        else:
            await recheck_job(p, job_id)

    run(db, allowed)
    assert db.one("SELECT status FROM jobs WHERE id = %s", job_id) == ("done" if final == "done" else "waiting",)


# ---- 2. the matrix: one row per invariant, the tests that prove it ---------------------------------------------------
REQUIRED = ("I1 I2 I3 I4 I5 I6 I7 I8 I9 I10 I11 I13 I14 I16 I17 I19 I20 I21 I23 I24 I25 I26 I27 I28 I29").split()

# invariant -> "file::test". A row needs at least one cited test that carries an @mutation; a row cites both completion
# modes through parametrized tests (marked "(modes)" in the report) wherever the invariant has two paths.
MATRIX = {
    "I1": ("test_engine.py::test_a_hung_submit_after_the_send_ends_unknown_after_its_timeout_and_is_never_resent",
           "test_invariants.py::test_a_submit_that_died_after_the_send_is_never_sent_again_in_either_mode",
           "test_reaper.py::test_an_expired_lease_on_a_sent_job_makes_it_unknown_never_queued",
           "test_ops.py::test_restore_marks_possibly_sent_jobs_unknown_never_queued"),
    "I2": ("test_lifecycle.py::test_a_result_cannot_be_applied_after_the_lease_expired",
           "test_lifecycle.py::test_the_fence_is_the_lease_id_not_the_owner",
           "test_leases.py::test_fence_reads_the_clock_not_the_transaction_start",
           "test_invariants.py::test_a_result_is_applied_only_under_a_live_lease_in_either_mode"),
    "I3": ("test_create.py::test_a_replay_is_answered_even_when_its_own_job_fills_the_cap",
           "test_create.py::test_eight_concurrent_creates_with_one_key_make_one_job_and_one_charge",
           "test_create.py::test_a_failure_between_the_charge_and_the_job_row_leaves_no_charge",
           "test_billing.py::test_charge_outside_a_transaction_is_refused_and_changes_nothing"),
    "I4": ("test_billing.py::test_eight_concurrent_refunders_give_one_refund_row_and_one_decrement",
           "test_billing.py::test_two_refunds_of_different_charges_on_one_wallet_do_not_deadlock",
           "test_billing.py::test_a_unique_violation_other_than_the_refund_of_one_is_an_error_not_an_already_refunded_answer"),
    "I5": ("test_billing.py::test_a_refund_that_waits_across_midnight_does_not_touch_the_new_days_counter",
           "test_billing.py::test_a_refund_never_takes_a_counter_below_zero_and_raises_the_alert",
           "test_billing.py::test_concurrent_charges_never_pass_the_day_limit"),
    "I6": ("test_claim.py::test_a_head_no_account_can_afford_does_not_stall_the_cheaper_jobs_behind_it",
           "test_claim.py::test_an_unknown_balance_allows_any_cost",
           "test_claim.py::test_no_browser_token_is_taken_when_no_account_can_afford_any_head",
           "test_pool.py::test_can_afford_follows_i6"),
    "I7": ("test_lifecycle.py::test_an_order_not_yet_proven_taken_charges_no_account_and_stores_no_facts",
           "test_pool.py::test_eight_concurrent_refunds_of_one_charge_restore_it_once",
           "test_pool.py::test_a_refund_after_the_pool_day_rolled_credits_nothing",
           "test_invariants.py::test_every_checker_rule_names_its_planted_violation"),
    "I8": ("test_invariants.py::test_all_outputs_of_one_job_are_delivered_at_the_one_price_in_either_mode",
           "test_recheck_recover.py::test_recover_attaches_outputs_once_by_upstream_id_charges_a_refunded_job_again_"
           "and_adds_to_a_done_job_free"),
    "I9": ("test_reaper.py::test_an_expired_lease_on_a_sent_job_makes_it_unknown_never_queued",
           "test_ops.py::test_restore_leaves_unsent_terminal_and_later_jobs_alone",
           "test_invariants.py::test_every_checker_rule_names_its_planted_violation"),
    "I10": ("test_recheck_recover.py::test_recheck_after_a_refund_charges_the_key_again_restarts_the_clock_and_a_new_"
            "failure_refunds_once",
            "test_billing.py::test_a_charge_made_again_after_a_refund_can_itself_be_refunded"),
    "I11": ("test_fetcher.py::test_a_job_is_not_done_while_any_output_is_missing",
            "test_invariants.py::test_every_checker_rule_names_its_planted_violation"),
    "I12": ("test_storage.py::test_debug_media_is_never_served_by_link",
            "test_storage.py::test_an_input_link_stops_working_at_its_expiry",
            "test_recheck_recover.py::test_input_links_stop_at_expiry_and_at_revoke_and_only_the_ones_meant"),
    "I13": ("test_invariants.py::test_a_terminal_job_changes_only_through_recheck_and_recover",
            "test_recheck_recover.py::test_recheck_acts_only_on_failed_waiting_and_unknown_jobs_that_were_sent"),
    "I14": ("test_leases.py::test_a_hung_holder_is_cancelled_within_its_bound_and_frees_the_account",
            "test_leases.py::test_the_heartbeat_never_extends_past_the_hard_bound",
            "test_reaper.py::test_expired_check_command_and_reconcile_leases_are_deleted_and_a_stalled_command_fails"),
    "I16": ("test_webhooks.py::test_4xx_is_final",
            "test_webhooks.py::test_event_id_is_stable_across_attempts",
            "test_webhooks.py::test_a_send_of_a_cleared_event_does_not_mark_the_job",
            "test_webhooks.py::test_two_claimers_never_take_one_delivery"),
    "I17": ("test_reaper.py::test_the_render_clock_starts_at_render_since_not_at_creation",
            "test_reaper.py::test_a_recheck_restarts_the_render_clock_and_the_new_failure_is_refunded_again"),
    "I19": ("test_settings.py::test_a_value_that_fails_validation_is_refused_and_the_stored_value_stays",),
    "I20": ("test_roles.py::test_the_api_cannot_update_a_credential_or_worker_owned_account_columns",
            "test_roles.py::test_the_api_is_refused_what_belongs_to_the_worker",
            "test_roles.py::test_a_plain_delete_of_a_used_account_is_refused_by_the_database_and_removes_nothing"),
    "I21": ("test_lifecycle.py::test_the_status_the_error_and_the_refunds_commit_together_or_not_at_all",
            "test_invariants.py::test_every_checker_rule_names_its_planted_violation"),
    "I23": ("test_poller.py::test_a_poll_result_is_dropped_when_a_recheck_or_another_claim_took_the_token",
            "test_lifecycle.py::test_a_poll_result_applies_only_with_the_token_the_poller_holds"),
    "I24": ("test_redaction_writers.py::test_a_long_value_is_stored_as_an_omitted_marker_by_every_writer",
            "test_pool.py::test_a_long_reason_is_cut_and_redacted_before_it_is_stored",
            "test_checks.py::test_an_account_error_over_2000_characters_is_stored_as_omitted_len"),
    "I25": ("test_checker.py::test_a_total_that_drifted_from_the_ledger_is_reported",
            "test_sweeps.py::test_ledger_check_finds_a_planted_total_drift"),
    "I26": ("test_errors.py::test_every_registered_code_is_assigned",
            "test_errors.py::test_refund_equals_job_ends_failed"),
    "I27": ("test_lifecycle.py::test_not_sent_is_refused_before_the_send_window_has_passed",
            "test_lifecycle.py::test_not_sent_is_refused_when_the_listing_does_not_cover_the_send_window"),
    "I28": ("test_browser_budget.py::test_i28_a_crowd_of_unknown_jobs_checks_and_submits_never_exceeds_the_semaphore",
            "test_crash_windows.py::test_unknown_jobs_checks_and_full_submit_slots_never_exceed_the_browser_semaphore"),
    "I29": ("test_billing.py::test_a_provider_added_after_the_key_is_closed_on_it_until_the_admin_opens_it",
            "test_billing.py::test_a_day_limit_of_zero_blocks_even_a_zero_point_charge"),
}


def mutation_index(directory: Path) -> dict[str, dict[str, bool]]:
    """file name -> {top-level test name: carries an @mutation}, read from the source (nothing is imported)."""
    found: dict[str, dict[str, bool]] = {}
    for path in sorted(directory.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found[path.name] = {
            n.name: any(isinstance(d, ast.Call) and getattr(d.func, "id", "") == "mutation" for d in n.decorator_list)
            for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    return found


def matrix_problems(matrix: dict[str, tuple[str, ...]], directory: Path, required=REQUIRED) -> list[str]:
    known = mutation_index(directory)
    problems = []
    for inv in required:
        if not matrix.get(inv):
            problems.append("%s has no row" % inv)
    for inv, cited in matrix.items():
        has_mutation = False
        for ref in cited:
            file, _, name = ref.partition("::")
            funcs = known.get(file)
            if funcs is None:
                problems.append("%s cites %s: no such file" % (inv, ref))
            elif name not in funcs:
                problems.append("%s cites %s: no such test" % (inv, ref))
            else:
                has_mutation = has_mutation or funcs[name]
        if cited and not has_mutation:
            problems.append("%s: none of its tests carries a mutation" % inv)
    return problems


# each anchor starts with a real newline and the code's indentation, so it matches the function and not these lines
NO_ROW_CHECK = '\n        if not matrix.get(inv):'
NO_ROW_CHECK_OFF = '\n        if False:'
NO_TEST_CHECK = '\n            elif name not in funcs:'
NO_TEST_CHECK_OFF = '\n            elif False:'
NO_MUTATION_CHECK = '\n        if cited and not has_mutation:'
NO_MUTATION_CHECK_OFF = '\n        if False:'


def test_the_real_matrix_names_existing_tests_and_every_row_has_a_mutation():
    assert matrix_problems(MATRIX, TESTS) == []


@mutation(SELF, NO_ROW_CHECK, NO_ROW_CHECK_OFF,
           expect="assert ['I2: none of")
@mutation(SELF, NO_TEST_CHECK, NO_TEST_CHECK_OFF,
           expect="KeyError: 'test_gone'")
@mutation(SELF, NO_MUTATION_CHECK, NO_MUTATION_CHECK_OFF,
           expect="assert ['I5 has no r")
def test_a_matrix_with_a_missing_row_test_or_mutation_is_reported(tmp_path):
    (tmp_path / "test_x.py").write_text("@mutation('f', 'a', 'b')\ndef test_with(): pass\ndef test_without(): pass\n")
    matrix = {"I1": ("test_x.py::test_with",), "I2": ("test_x.py::test_without",), "I3": ("test_x.py::test_gone",),
              "I4": ("test_nofile.py::test_with",), "I5": ()}
    got = matrix_problems(matrix, tmp_path, required=("I1", "I2", "I3", "I4", "I5", "I6"))
    assert got == ["I5 has no row", "I6 has no row", "I2: none of its tests carries a mutation",
                   "I3 cites test_x.py::test_gone: no such test", "I3: none of its tests carries a mutation",
                   "I4 cites test_nofile.py::test_with: no such file", "I4: none of its tests carries a mutation"]


# ---- the strict tier is the default after every test (conftest.FABRICATED) -----------------------------------------
GONE_OK = "if name.split(" '"["' ")[0] not in " "defined]"
GONE_OK_OFF = "if False]"
BASE_NAME = 'return tuple(tests.get(node.name) or tests.get(getattr(node, "originalname", node.name), ()))'
ONLY_BASE_NAME = 'return tuple(tests.get(getattr(node, "originalname", node.name), ()))'
ONLY_FULL_NAME = "return tuple(tests.get(node.name, ()))"
MARKER_RULES = "return tuple(marker.args[1:])"
MARKER_ALL_ARGS = "return tuple(marker.args)"
MARKER_NEEDS_RULES = "if len(marker.args) < 2:"
MARKER_NEEDS_NONE = "if False:"
HIDE_STRICT_ONLY = "if v not in base and any(rule in v for rule in planted)]"
HIDE_BASE_TOO = "if any(rule in v for rule in planted)]"
HIDE_EVERYTHING = "if v not in base and True]"
STALE_CHECK = "for r in planted if not any(r in v for v in hidden)]"
STALE_OFF = "for r in planted if False]"
ONE_RULE = "if sum(rule in t for t " "in texts) != 1:"
ONE_RULE_OFF = "if sum(rule in t for t in texts) == 0:"
NAMES_RULE = "if not " "rules:"
NAMES_RULE_OFF = "if False:"
CONFTEST = "packages/relay-core/tests/conftest.py"


def missing_exempt_tests(fabricated, here: Path) -> list[str]:
    gone = []
    for file, (_why, names) in fabricated.items():
        tree = ast.parse((here / file).read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
        gone += ["%s::%s" % (file, name) for name in names if name.split("[")[0] not in defined]
    return gone


@mutation(SELF, GONE_OK, GONE_OK_OFF, expect="test_x.py::test_gone")
def test_a_renamed_exempt_test_is_reported(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_here(): pass\n")
    got = missing_exempt_tests({"test_x.py": ("why", ("test_here", "test_here[a-b]", "test_gone"))}, tmp_path)
    assert got == ["test_x.py::test_gone"]


def test_every_test_exempted_from_the_strict_checker_still_exists():
    from conftest import FABRICATED

    gone = missing_exempt_tests(FABRICATED, Path(__file__).parent)
    assert gone == [], "renamed or deleted: fix FABRICATED so no test drops out of the strict tier unnoticed: %s" % gone


def rule_problems(fabricated, texts: list[str]) -> list[str]:
    """Every exempt test names at least one rule, and every rule is a substring of exactly one strict rule text."""
    problems = []
    for file, (_why, tests) in fabricated.items():
        for name, rules in tests.items():
            if not rules:
                problems.append("%s::%s names no rule" % (file, name))
            for rule in rules:
                if sum(rule in t for t in texts) != 1:
                    problems.append("%s::%s: %r is not one strict rule" % (file, name, rule))
    return problems


@mutation(SELF, ONE_RULE, ONE_RULE_OFF, expect="'terminal job' is not one strict rule")
@mutation(SELF, NAMES_RULE, NAMES_RULE_OFF, expect="test_x.py::t names no rule")
def test_an_exempt_test_must_name_rules_that_each_match_one_strict_rule():
    texts = ["a terminal job without finished_at", "a terminal job still holds a poll token", "a failed job"]
    planted = {"test_x.py": ("why", {"t": (), "u": ("without finished_at", "terminal job", "no such rule")})}
    assert rule_problems(planted, texts) == ["test_x.py::t names no rule",
                                             "test_x.py::u: 'terminal job' is not one strict rule",
                                             "test_x.py::u: 'no such rule' is not one strict rule"]


def test_every_planted_rule_names_exactly_one_strict_rule():
    from conftest import FABRICATED

    from relay_core.testing.checker import _STRICT_JOB_RULES

    assert rule_problems(FABRICATED, [text for _rule, text, _sql in _STRICT_JOB_RULES]) == []


def fake_node(name, path, original=None, marker=None):
    from types import SimpleNamespace

    return SimpleNamespace(name=name, originalname=original or name, path=Path(path),
                           get_closest_marker=lambda _m: marker)


@mutation(CONFTEST, BASE_NAME, ONLY_BASE_NAME, expect="assert () == ('r1',)")
@mutation(CONFTEST, BASE_NAME, ONLY_FULL_NAME, expect="assert () == ('r2',)")
@mutation(CONFTEST, MARKER_RULES, MARKER_ALL_ARGS, expect="assert ('why', 'r3') == ('r3',)")
@mutation(CONFTEST, MARKER_NEEDS_RULES, MARKER_NEEDS_NONE, expect="DID NOT RAISE")
def test_a_test_hides_only_the_strict_rules_it_names(monkeypatch):
    from types import SimpleNamespace

    import conftest

    monkeypatch.setitem(conftest.FABRICATED, "test_x.py", ("why", {"t[a]": ("r1",), "u": ("r2",)}))
    planted = conftest.planted_rules
    assert planted(fake_node("t[a]", "test_x.py", "t")) == ("r1",)            # a parametrized test by its full name
    assert planted(fake_node("t[b]", "test_x.py", "t")) == ()                 # another parameter of it: strict
    assert planted(fake_node("u[1]", "test_x.py", "u")) == ("r2",)            # every parameter, by the plain name
    assert planted(fake_node("test_some_new_test", "test_x.py")) == ()
    assert planted(fake_node("t[a]", "test_other_file.py", "t")) == ()
    assert planted(fake_node("t", "test_x.py", marker=SimpleNamespace(args=("why", "r3")))) == ("r3",)
    with pytest.raises(pytest.UsageError):
        planted(fake_node("t", "test_x.py", marker=SimpleNamespace(args=("why",))))


@mutation(CONFTEST, HIDE_STRICT_ONLY, HIDE_BASE_TOO,
           expect="At index 0 diff: 'I13 job b: a failed job without an error code' != 'I1 job a")
@mutation(CONFTEST, HIDE_STRICT_ONLY, HIDE_EVERYTHING,
           expect="Right contains 2 more items, first extra item: 'I13 job b: a failed job")
@mutation(CONFTEST, STALE_CHECK, STALE_OFF,
           expect="Right contains one more item: \"exempt rule 'no lease row' fires no more")
def test_the_judge_hides_only_planted_strict_only_lines_and_reports_a_rule_that_fires_nothing():
    from conftest import judge

    base = ["I1 job a: a submitting job with no lease row"]
    strict_only = ["I13 job b: a failed job without an error code", "I13 job c: a terminal job without finished_at"]
    found = base + strict_only
    assert judge(base, found, ()) == found
    assert judge(base, found, ("without an error code",)) == base + strict_only[1:]
    assert judge(base, found, ("without an error code", "without finished_at")) == base
    # a base-tier line is never hidden, even when it matches a planted rule
    assert judge(base, found, ("no lease row",)) == found + ["exempt rule 'no lease row' fires no more: "
                                                             "remove it from the test's exemption"]
    assert judge(base, found, ("poll token",))[-1] == ("exempt rule 'poll token' fires no more: "
                                                       "remove it from the test's exemption")
