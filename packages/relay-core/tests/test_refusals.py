"""Account refusals (`relay_core.refusals`): a job one account refused, with proof nothing was made, moves to another
account, at most `<provider>.refusal_max_accounts` of them, then fails with its full refund; no attempt is used, and the
claim never gives the job back to an account that refused it."""

import pytest
from jobkit import PROVIDER, SPEC, account, claim, create, key, run, status, submit
from lifecyclekit import reaper

from relay_core import failpoints, refusals
from relay_core.errors import rule
from relay_core.jobs import apply, store
from relay_core.providers import REFUSED_BY, Fail, JobView, NotTaken, SourceJob, SubmitResult
from relay_core.refusals import account_refused, max_accounts, refusal_max_accounts_setting
from relay_core.settings import SettingsError
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, unsent
from relay_core.testing.mutation import mutation

R = "packages/relay-core/src/relay_core/refusals.py"
K = "packages/relay-core/src/relay_core/jobs/claim.py"
A = "packages/relay-core/src/relay_core/jobs/apply.py"
P = "packages/relay-core/src/relay_core/jobs/reaper.py"
STRANDED = '                           ("stranded_refusals", self._stranded_refusals),\n'
STILL_USABLE = ("            if await usable_account(conn, job_id, name, cost, refused, "
                "self.settings.get(refusals.WAIT_KEY), pools):\n")
ONLY_REFUSED = "                   \"AND provider_state ? '\" + REFUSED_FINAL + \"' ORDER BY created_at, id\")\n"
STILL_QUEUED = "                 \"WHERE id = %s AND status = 'queued' AND sent_at IS NULL FOR UPDATE SKIP LOCKED\")\n"
ALONE = "        case NotTaken(final=Fail() as final) if not await _another_account(conn, job, outcome, pools):\n"
AFFORD = "    return any(pool.can_afford(states[(a, pool_name)].standing.left, 0, cost) for a, _ in rows)\n"
REST_WAIT = "           AND c.rest_until > clock_timestamp() + make_interval(secs => %(wait)s)))\n"
NOT_REFUSED = "   AND NOT a.id::text = ANY(%(refused)s::text[])\n"
BELOW = "    if len(refused) < max_accounts(settings, job.provider):\n"
DISTINCT = "    return before if account_id in before else [*before, account_id]\n"
PINNED = "    if job.source is not None:\n"
LIVE = "        cap = settings.get(max_accounts_key(provider)) if settings is not None else None\n"
SKIP_REFUSED = "jsonb_build_array(%(account)s::text), false)"
THROTTLED = "pool.account_throttled"     # a requeue that uses no attempt
FINAL = "queue.never_started"            # terminal, with its refund


@pytest.fixture(autouse=True)
def _no_requeue_pause(monkeypatch):
    monkeypatch.setattr(apply, "REQUEUE_PAUSE_S", 0)


def job(refused=None, source=None) -> JobView:
    return JobView("j1", PROVIDER, "generate", PROVIDER + "-video", {}, "p", "m", None, None, None, None,
                   {} if refused is None else {REFUSED_BY: refused}, inputs=None, source=source)


def cap(n=None) -> FakeSettings:
    settings = FakeSettings(SPEC)
    if n is not None:
        settings.set(refusals.max_accounts_key(PROVIDER), n)
    return settings


def refuse(view: JobView, account_id, settings=None):
    return account_refused(view, str(account_id), settings or cap(), not_taken=THROTTLED, final=FINAL,
                           detail="refused", patch={"seen": 1})


# ---- the outcome (pure) -----------------------------------------------------------------------------------------
def test_the_codes_used_here_are_a_free_requeue_and_a_refunded_end():
    assert (rule(THROTTLED).terminal, rule(THROTTLED).counts_attempt) == (False, False)
    assert (rule(FINAL).terminal, rule(FINAL).refund) == (True, True)


@mutation(R, BELOW, "    if False:\n", expect="below the bound the job did not go to another account")
def test_below_the_bound_the_refusal_requeues_the_job_with_the_account_added():
    got = refuse(job(), "acct-A")
    assert got == NotTaken(THROTTLED, {"seen": 1, REFUSED_BY: ["acct-A"]}), \
        "below the bound the job did not go to another account: %r" % (got,)


@mutation(R, BELOW, "    if True:\n", expect="the refusal that reached the bound did not end the job")
def test_the_refusal_that_reaches_the_bound_fails_the_job_with_the_final_code():
    got = refuse(job(["acct-A"]), "acct-B")
    assert isinstance(got, Fail) and got.code == FINAL, \
        "the refusal that reached the bound did not end the job: %r" % (got,)
    assert got.state_patch == {"seen": 1, REFUSED_BY: ["acct-A", "acct-B"]}
    assert got.detail == "refused (refused on 2 account(s), the most %s allows)" % refusals.max_accounts_key(PROVIDER)


@mutation(R, DISTINCT, "    return [*before, account_id]\n", expect="one account counted twice")
def test_an_account_counts_once_however_often_it_refuses():
    got = refuse(job(["acct-A"]), "acct-A")
    assert got == NotTaken(THROTTLED, {"seen": 1, REFUSED_BY: ["acct-A"]}), "one account counted twice: %r" % (got,)


@mutation(R, PINNED, "    if False:\n", expect="a pinned job was sent back to the queue")
def test_a_pinned_job_fails_at_its_first_refusal_no_other_account_may_run_it():
    source = SourceJob("j0", PROVIDER + "-video", "generate", "ref", {})
    got = refuse(job(source=source), "acct-A")
    assert isinstance(got, Fail) and got.code == FINAL, "a pinned job was sent back to the queue: %r" % (got,)
    assert got.state_patch[REFUSED_BY] == ["acct-A"]


@mutation(R, LIVE, "        cap = None\n", expect="the live setting was not read")
def test_the_bound_is_the_live_setting_else_the_default():
    assert max_accounts(cap(1), PROVIDER) == 1, "the live setting was not read"
    assert isinstance(refuse(job(), "acct-A", cap(1)), Fail), "the live setting was not read"
    assert refuse(job(["acct-A"]), "acct-B", cap(3)).code == THROTTLED
    assert max_accounts(cap(), PROVIDER) == max_accounts(None, PROVIDER) == refusals.DEFAULT_MAX_ACCOUNTS == 2


def test_every_provider_declares_the_setting_the_same_way():
    name, spec = refusal_max_accounts_setting("acme", "How many accounts a refused acme job may try.")
    assert name == spec.key == "acme.refusal_max_accounts"
    assert (spec.type, spec.default, spec.min, spec.max) == (int, 2, 1, 9)
    with pytest.raises(SettingsError):
        spec.validate(0)


# ---- through the store and the claim ----------------------------------------------------------------------------
async def refused_by_its_account(p, got, settings=None) -> str:
    """One submit on the claimed account that ends in its refusal; returns the job's new status."""
    view = await store.job_view(p, got.job_id)
    engine = FakeEngine()
    engine.script_submit("*", unsent(SubmitResult(refuse(view, got.account_id, settings))))
    return await submit(p, got, engine)


def ledger(db, job_id):
    return db.all("SELECT entry, amount FROM ledger WHERE job_id = %s AND scope = '*' ORDER BY id", job_id)


@mutation(K, SKIP_REFUSED, "jsonb_build_array(%(account)s::text) AND false, false)",
          expect="the account that refused the job was given it again")
def test_a_refused_job_goes_to_another_account_uses_no_attempt_and_fails_refunded_at_the_bound(db):
    k, first, second = key(db), account(db), account(db, cooldown_s=30)    # second: free within the wait

    async def scenario(p):
        made = await create(p, k, "refused everywhere", price=7)
        got = await claim(p)
        assert got.account_id == first
        assert await refused_by_its_account(p, got) == "queued"
        again = await claim(p)        # the account rests on nothing: only the job's `refused_by` keeps it away
        assert again is None, "the account that refused the job was given it again: %r" % (again,)
        newer = await create(p, k, "a newer job", price=7)
        behind = await claim(p)       # a refused head never holds back the jobs behind it from that account
        db.run("UPDATE accounts SET cooldown_until = NULL WHERE id = %s", second)
        moved = await claim(p)
        assert await refused_by_its_account(p, moved) == "failed"
        return made, newer, behind, second, moved

    made, newer, behind, second, moved = run(db, scenario)
    assert (behind.job_id, behind.account_id) == (newer.job_id, first)
    assert (moved.job_id, moved.account_id) == (made.job_id, second)
    row = db.one("SELECT status, error_code, attempts, provider_state FROM jobs WHERE id = %s", made.job_id)
    assert row[:3] == ("failed", FINAL, 0), "a refusal used an attempt or did not end the job: %r" % (row,)
    assert row[3][REFUSED_BY] == [str(first), str(second)]
    assert ledger(db, made.job_id) == [("charge", 7), ("refund", 7)], "the key was not refunded exactly once"
    assert ledger(db, newer.job_id) == [("charge", 7)] and status(db, newer.job_id) == "submitting"


# ---- no other account can take it: the refusal ends the job at once ---------------------------------------------
def rest(db, account_id, seconds, pool_name="video"):
    db.run("INSERT INTO account_pools (account_id, pool, rest_until) "
           "VALUES (%s, %s, clock_timestamp() + make_interval(secs => %s)) "
           "ON CONFLICT (account_id, pool) DO UPDATE SET rest_until = EXCLUDED.rest_until",
           account_id, pool_name, seconds)


def refused_once(db, settings=None):
    """A job claimed by a fresh account (the least recently used one) and refused there: (job id, new status)."""
    k, first = key(db), account(db, used_ago_s=None)

    async def scenario(p):
        made = await create(p, k, "refused", price=7)
        got = await claim(p)
        assert got.account_id == first, "premise: the fresh account took the job"
        return made.job_id, await refused_by_its_account(p, got, settings)

    return run(db, scenario)


@mutation(A, ALONE, "        case NotTaken(final=Fail() as final) if False:\n",
          expect="the job waited in the queue with no other account to take it")
def test_a_refusal_with_no_other_usable_account_fails_the_job_at_once_refunded(db):
    account(db, state="dead", used_ago_s=60)
    account(db, state="disabled", used_ago_s=60)
    account(db, checked=False, used_ago_s=60)
    account(db, cooldown_s=3600, used_ago_s=60)
    account(db, left=0, used_ago_s=60)                  # no balance left for the job until its refill
    rest(db, account(db, used_ago_s=60), 3600)          # its pool rests an hour (the 06/11 grok rate limit)
    job_id, got = refused_once(db)
    assert got == "failed", "the job waited in the queue with no other account to take it: %s" % got
    row = db.one("SELECT error_code, attempts, error FROM jobs WHERE id = %s", job_id)
    assert row[:2] == (FINAL, 0) and "no other account can take it" in row[2], row
    assert ledger(db, job_id) == [("charge", 7), ("refund", 7)], "the key was not refunded exactly once"


@mutation(A, AFFORD, "    return False\n", expect="the job failed though another account could take it")
def test_a_refusal_with_another_usable_account_requeues_the_job(db):
    account(db, used_ago_s=60)                          # free, its pool's balance from the budget
    job_id, got = refused_once(db)
    assert got == "queued", "the job failed though another account could take it: %s" % got
    assert ledger(db, job_id) == [("charge", 7)]


@mutation(A, REST_WAIT, "           AND c.rest_until > clock_timestamp()))\n",
          expect="an account resting less than the wait did not count")
def test_an_account_resting_less_than_the_wait_still_counts(db):
    rest(db, account(db, used_ago_s=60), 30)            # rests 30 s, refusal_wait_s is 60
    job_id, got = refused_once(db)
    assert got == "queued", "an account resting less than the wait did not count: %s" % got
    assert status(db, job_id) == "queued"


def test_with_no_wait_only_an_account_free_now_counts(db):
    rest(db, account(db, used_ago_s=60), 30)
    settings = cap()
    settings.set(refusals.WAIT_KEY, 0)
    assert refused_once(db, settings)[1] == "failed"


@mutation(A, NOT_REFUSED, "   AND true\n", expect="an account that already refused the job counted")
def test_an_account_that_already_refused_the_job_does_not_count(db):
    k, first, second = key(db), account(db), account(db, cooldown_s=30)
    three = cap(3)

    async def scenario(p):
        made = await create(p, k, "refused twice", price=7)
        got = await claim(p)
        assert got.account_id == first
        assert await refused_by_its_account(p, got, three) == "queued"     # second: free within the wait
        db.run("UPDATE accounts SET cooldown_until = NULL WHERE id = %s", second)
        moved = await claim(p)
        assert moved.account_id == second
        return made.job_id, await refused_by_its_account(p, moved, three)

    job_id, got = run(db, scenario)
    # first is free and has balance, but refused this job: the claim never gives it back, so it does not count
    assert got == "failed", "an account that already refused the job counted: %s" % got
    assert db.one("SELECT error_code FROM jobs WHERE id = %s", job_id)[0] == FINAL
    assert ledger(db, job_id) == [("charge", 7), ("refund", 7)]


# ---- refused jobs in the queue whose accounts all went to rest later: the reaper ends them --------------------------
def refused_queued(db, n):
    """n jobs, each claimed by its own account and refused there while the others were still free (06/11 21:29: ten
    grok jobs): (accounts, job ids), every job queued with its refusal kept."""
    k, accounts = key(db), [account(db) for _ in range(n)]

    async def scenario(p):
        ids = [(await create(p, k, "refused %d" % i, price=7)).job_id for i in range(n)]
        claims = [await claim(p) for _ in range(n)]
        assert len({c.account_id for c in claims}) == n, "premise: each job on its own account"
        for got in claims:
            assert await refused_by_its_account(p, got) == "queued", "premise: another account was free"
        return ids

    return accounts, run(db, scenario)


def reap(db, times=1):
    async def scenario(p):
        for _ in range(times):
            await reaper(p).tick()

    run(db, scenario)


@mutation(P, STRANDED, "", expect="a refused job waited with no account left for it")
def test_refused_jobs_fail_at_once_refunded_once_when_every_account_went_to_rest(db):
    accounts, ids = refused_queued(db, 3)
    for a in accounts:
        rest(db, a, 3600)                               # the hour-long grok rest that came seconds later
    reap(db, times=2)
    for job_id in ids:
        assert status(db, job_id) == "failed", "a refused job waited with no account left for it"
        code, error = db.one("SELECT error_code, error FROM jobs WHERE id = %s", job_id)
        assert code == FINAL and "no other account can take it" in error, (code, error)
        assert ledger(db, job_id) == [("charge", 7), ("refund", 7)], "the key was not refunded exactly once"


@mutation(P, STILL_USABLE, "            if False:\n", expect="a refused job failed though an account could take it")
def test_refused_jobs_wait_and_are_claimed_when_an_account_is_usable_within_the_wait(db):
    accounts, ids = refused_queued(db, 3)
    for a in accounts:
        rest(db, a, 3600)
    spare = account(db)
    rest(db, spare, 30)                                 # free within refusal_wait_s (60)
    reap(db)
    assert [status(db, j) for j in ids] == ["queued"] * 3, "a refused job failed though an account could take it"
    db.run("DELETE FROM account_pools WHERE account_id = %s", spare)

    async def scenario(p):
        return await claim(p)

    got = run(db, scenario)
    assert got is not None and got.account_id == spare and got.job_id in ids
    assert all(ledger(db, j) == [("charge", 7)] for j in ids)


@mutation(P, ONLY_REFUSED, "                   \"ORDER BY created_at, id\")\n",
          expect="a job never refused was judged")
def test_a_queued_job_never_refused_is_left_alone(db, caplog):
    k, a = key(db), account(db)
    rest(db, a, 3600)

    async def scenario(p):
        return (await create(p, k, "plain", price=7)).job_id

    job_id = run(db, scenario)
    reap(db)
    assert status(db, job_id) == "queued" and ledger(db, job_id) == [("charge", 7)]
    assert "could not be judged" not in caplog.text, "a job never refused was judged: %s" % caplog.text


@mutation(P, STILL_QUEUED, "                 \"WHERE id = %s AND sent_at IS NULL FOR UPDATE SKIP LOCKED\")\n",
          expect="a job a claim took was also failed")
def test_a_claim_that_takes_the_job_while_the_reaper_judges_it_is_the_only_outcome(db, caplog):
    k, first, second = key(db), account(db, used_ago_s=None), account(db, used_ago_s=60)

    async def scenario(p):
        made = await create(p, k, "raced", price=7)
        got = await claim(p)
        assert got.account_id == first and await refused_by_its_account(p, got) == "queued"
        taken = []

        async def claim_then_rest():                    # between the reaper's list and its lock
            taken.append(await claim(p))
            rest(db, first, 3600)
            rest(db, second, 3600)

        failpoints.arm("reaper.refused_listed", claim_then_rest)
        try:
            await reaper(p).tick()
        finally:
            failpoints.disarm("reaper.refused_listed")
        return made.job_id, taken

    job_id, taken = run(db, scenario)
    assert taken[0].job_id == job_id and taken[0].account_id == second, "premise: the claim took the job"
    assert status(db, job_id) == "submitting", "a job a claim took was also failed"
    assert ledger(db, job_id) == [("charge", 7)]
    assert "pass failed" not in caplog.text, caplog.text           # the pass itself ran through
