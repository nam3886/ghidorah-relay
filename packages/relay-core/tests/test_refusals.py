"""Account refusals (`relay_core.refusals`): a job one account refused, with proof nothing was made, moves to another
account, at most `<provider>.refusal_max_accounts` of them, then fails with its full refund; no attempt is used, and the
claim never gives the job back to an account that refused it."""

import pytest
from jobkit import PROVIDER, SPEC, account, claim, create, key, run, status, submit

from relay_core import refusals
from relay_core.errors import rule
from relay_core.jobs import apply, store
from relay_core.providers import REFUSED_BY, Fail, JobView, NotTaken, SourceJob, SubmitResult
from relay_core.refusals import account_refused, max_accounts, refusal_max_accounts_setting
from relay_core.settings import SettingsError
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, unsent
from relay_core.testing.mutation import mutation

R = "packages/relay-core/src/relay_core/refusals.py"
K = "packages/relay-core/src/relay_core/jobs/claim.py"
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
async def refused_by_its_account(p, got) -> str:
    """One submit on the claimed account that ends in its refusal; returns the job's new status."""
    view = await store.job_view(p, got.job_id)
    engine = FakeEngine()
    engine.script_submit("*", unsent(SubmitResult(refuse(view, got.account_id))))
    return await submit(p, got, engine)


def ledger(db, job_id):
    return db.all("SELECT entry, amount FROM ledger WHERE job_id = %s AND scope = '*' ORDER BY id", job_id)


@mutation(K, SKIP_REFUSED, "jsonb_build_array(%(account)s::text) AND false, false)",
          expect="the account that refused the job was given it again")
def test_a_refused_job_goes_to_another_account_uses_no_attempt_and_fails_refunded_at_the_bound(db):
    k, first = key(db), account(db)

    async def scenario(p):
        made = await create(p, k, "refused everywhere", price=7)
        got = await claim(p)
        assert got.account_id == first
        assert await refused_by_its_account(p, got) == "queued"
        again = await claim(p)        # the account rests on nothing: only the job's `refused_by` keeps it away
        assert again is None, "the account that refused the job was given it again: %r" % (again,)
        newer = await create(p, k, "a newer job", price=7)
        behind = await claim(p)       # a refused head never holds back the jobs behind it from that account
        second = account(db)
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
