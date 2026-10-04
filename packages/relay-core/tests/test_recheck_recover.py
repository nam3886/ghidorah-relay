"""Re-check, Recover, Regenerate and input-link expiry (contracts 7.1, 9; D14, D15; invariants I8, I10, I12, I13, I17,
I21, I23, I25): the only ways a terminal job changes, the key re-charge after a refund that a later failure can refund
again, the render clock restart, a NEW marker on Regenerate, and links that stop working at expiry or revoke."""

import uuid

import pytest
from jobkit import CAPS, POOLS, PROVIDER, SETTINGS, account, claim, create, key, new_job, run, status, submit

from relay_core import billing, storage
from relay_core.db import tx
from relay_core.jobs import store
from relay_core.jobs.create import marker_for
from relay_core.jobs.recheck import JobRefused, StoredOutput
from relay_core.providers import Failed, Pending, PollResult
from relay_core.testing.fake_provider import FakeEngine
from relay_core.testing.mutation import mutation

RC = "packages/relay-core/src/relay_core/jobs/recheck.py"
SH = "packages/relay-core/src/relay_core/jobs/shares.py"

RECHARGE = "recharged = await _recharge(conn, job_id, key_id, provider, kind, price)\n        await conn.execute("
NO_RECHARGE = "recharged = 0\n        await conn.execute("
NET_CHECK = "if net >= price:\n        return 0"
NET_IGNORED = "if False:\n        return 0"
FLAG = "if bulk and state.get(\"no_recheck\") is True:"
FLAG_IGNORED = "if False:"
SEEN = "if out.upstream_id in have:\n            continue"
SEEN_IGNORED = "if False:\n            continue"
MARKER = "    created = await create_job(db, new, settings)\n"
MARKER_COPIED = (
    "    created = await create_job(db, new, settings)\n"
    "    async with tx(db) as conn:\n"
    "        await conn.execute(\"UPDATE jobs SET marker = (SELECT marker FROM jobs WHERE id = %s) WHERE id = %s\",\n"
    "                           (job_id, created.job_id))\n")
PLAN_USED = "    new = NewJob(provider, key_id, plan, prompt, title, request=request)"
PLAN_FREE = "    new = NewJob(provider, key_id, replace(plan, price=0), prompt, title, request=request)"
SWEEP = "AND share_expires_at <= clock_timestamp()"
SWEEP_ALL = "AND share_expires_at IS NOT NULL"


def row(db, job_id, names: str):
    return db.one("SELECT %s FROM jobs WHERE id = %%s" % names, job_id)


def ledger(db, job_id) -> tuple[int, int]:
    """(charges, refunds) of the job's ledger rows."""
    return db.one("SELECT count(*) FILTER (WHERE entry = 'charge'), count(*) FILTER (WHERE entry = 'refund') "
                  "FROM ledger WHERE job_id = %s", job_id)


async def waiting_job(p, k, prompt="x", **opts) -> uuid.UUID:
    """A job that was accepted upstream (it has an external ref and a taken order) and now waits for its poll."""
    made = await create(p, k, prompt, **opts)
    got = await claim(p)
    assert await submit(p, got, FakeEngine()) == "waiting"
    return made.job_id


async def failed_job(p, k, prompt="x", **opts) -> uuid.UUID:
    job_id = await waiting_job(p, k, prompt, **opts)
    assert await store.fail_job(p, job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
    return job_id


def out(n: int, *, size=10) -> StoredOutput:
    return StoredOutput("obj/%d.mp4" % n, "video/mp4", size, "up-%d" % n)


# ---- Re-check ----------------------------------------------------------------------------------------------------
@mutation(RC, RECHARGE, NO_RECHARGE, expect="assert refunded_wallet == (0, 0) and recharged == 10")
def test_recheck_after_a_refund_charges_the_key_again_restarts_the_clock_and_a_new_failure_refunds_once(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        job_id = await failed_job(p, k)
        db.run("UPDATE jobs SET render_since = clock_timestamp() - interval '3 hours', callback_url = 'https://h/c', "
               "callback_status = 'sent', callback_attempts = 2, callback_for = finished_at WHERE id = %s", job_id)
        refunded_wallet = db.wallet(k, PROVIDER)
        recharged = await store.recheck_job(p, job_id)
        revived = row(db, job_id, "status, error, error_code, finished_at, callback_status, callback_attempts, "
                                  "callback_for, poll_token IS NOT NULL, next_poll_at <= clock_timestamp(), "
                                  "clock_timestamp() - render_since < interval '10 seconds'")
        after_recheck = db.wallet(k, PROVIDER), ledger(db, job_id)
        await store.fail_job(p, job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
        return job_id, refunded_wallet, recharged, revived, after_recheck

    job_id, refunded_wallet, recharged, revived, after_recheck = run(db, scenario)
    assert refunded_wallet == (0, 0) and recharged == 10
    assert revived == ("waiting", None, None, None, None, 0, None, True, True, True)
    assert after_recheck == ((10, 10), (2, 1))
    assert db.wallet(k, PROVIDER) == (0, 0) and ledger(db, job_id) == (2, 2), "the new charge is refunded once"
    assert db.one("SELECT count(*), count(refunded_at) FROM account_charges WHERE job_id = %s", job_id) == (1, 1), \
        "Re-check never charges the account pool again"
    assert any("charged again" in t[0] for t in db.all("SELECT text FROM job_events WHERE stage = 'recheck'"))


@mutation(RC, NET_CHECK, NET_IGNORED,
          expect="assert recharged == 0 and db.wallet(k, PROVIDER) == (10, 10) and ledger(db, job_")
def test_recheck_of_a_job_that_was_never_refunded_charges_nothing(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        job_id = await waiting_job(p, k)
        return job_id, await store.recheck_job(p, job_id)

    job_id, recharged = run(db, scenario)
    assert recharged == 0 and db.wallet(k, PROVIDER) == (10, 10) and ledger(db, job_id) == (1, 0)


def test_recheck_revives_a_refunded_job_even_when_the_key_can_no_longer_pay_and_never_refunds_twice(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        job_id = await failed_job(p, k)
        db.run("UPDATE api_keys SET enabled = false WHERE id = %s", k)
        recharged = await store.recheck_job(p, job_id)
        revived = status(db, job_id)
        await store.fail_job(p, job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
        return job_id, recharged, revived

    job_id, recharged, revived = run(db, scenario)
    assert (recharged, revived) == (0, "waiting")
    assert ledger(db, job_id) == (1, 1) and db.wallet(k, PROVIDER) == (0, 0), "no charge, so no second refund"


def test_recheck_acts_only_on_failed_waiting_and_unknown_jobs_that_were_sent(db):
    k = key(db)
    for _ in range(3):
        account(db)

    async def scenario(p):
        failed, waiting = await failed_job(p, k, "failed"), await waiting_job(p, k, "waiting")
        unknown, fetching = await waiting_job(p, k, "unknown"), await waiting_job(p, k, "fetching")
        done, never_sent = await waiting_job(p, k, "done"), await failed_job(p, k, "never")
        queued, submitting = (await create(p, k, "queued")).job_id, (await create(p, k, "sub")).job_id
        for job_id, state in ((unknown, "unknown"), (fetching, "fetching"), (done, "done"), (submitting, "submitting")):
            db.run("UPDATE jobs SET status = %s WHERE id = %s", state, job_id)
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
               "VALUES (%s, 'output', 0, 'o', 'm', 1)", done)
        db.run("UPDATE jobs SET external_ref = NULL WHERE id = %s", never_sent)
        refused = {}
        for name, job_id in (("done", done), ("submitting", submitting), ("fetching", fetching), ("queued", queued),
                             ("no ref", never_sent), ("missing", uuid.uuid4())):
            with pytest.raises(JobRefused) as e:
                await store.recheck_job(p, job_id)
            refused[name] = e.value.code
        for job_id in (failed, waiting, unknown):
            await store.recheck_job(p, job_id)
        return refused, [status(db, j) for j in (failed, waiting, unknown)]

    refused, after = run(db, scenario)
    assert refused == {"done": "job.already_finished", "submitting": "job.busy", "fetching": "job.busy",
                       "queued": "job.not_submitted", "no ref": "job.not_submitted", "missing": "resource.not_found"}
    assert after == ["waiting"] * 3


def test_a_poll_result_that_was_in_flight_when_the_job_was_rechecked_is_dropped(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        job_id = await waiting_job(p, k)
        old = uuid.uuid4()
        db.run("UPDATE jobs SET poll_token = %s WHERE id = %s", old, job_id)         # a poller took the job
        await store.recheck_job(p, job_id)
        dropped = await store.apply_poll(p, job_id, old, PollResult(Failed("render.deadline", "late", taken=True)),
                                         caps=CAPS, pools=POOLS)
        fresh = db.one("SELECT poll_token FROM jobs WHERE id = %s", job_id)[0]
        kept = await store.apply_poll(p, job_id, fresh, PollResult(Pending("still", 30)), caps=CAPS, pools=POOLS)
        return job_id, dropped, kept

    job_id, dropped, kept = run(db, scenario)
    assert (dropped, kept, status(db, job_id)) == (None, "waiting", "waiting")


@mutation(RC, FLAG, FLAG_IGNORED, expect="assert result.rechecked == (str(plain),)")
def test_bulk_recheck_skips_jobs_the_provider_flagged_and_reports_why_the_others_were_left(db):
    k = key(db)
    for _ in range(3):
        account(db)

    async def scenario(p):
        plain, flagged = await failed_job(p, k, "plain"), await failed_job(p, k, "flagged")
        done = await waiting_job(p, k, "done")
        db.run("UPDATE jobs SET provider_state = '{\"no_recheck\": true}' WHERE id = %s", flagged)
        db.run("UPDATE jobs SET status = 'done' WHERE id = %s", done)
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
               "VALUES (%s, 'output', 0, 'o', 'm', 1)", done)
        result = await store.recheck_bulk(p, [plain, flagged, done])
        single = await store.recheck_job(p, flagged)              # an explicit single Re-check is the admin's call
        return plain, flagged, done, result, single

    plain, flagged, done, result, single = run(db, scenario)
    assert result.rechecked == (str(plain),)
    assert result.skipped == {str(flagged): "the provider flagged this job as not re-checkable",
                              str(done): "job.already_finished"}
    assert status(db, plain) == "waiting" and status(db, flagged) == "waiting" and single == 10


# ---- Recover -----------------------------------------------------------------------------------------------------
@mutation(RC, SEEN, SEEN_IGNORED, expect="duplicate key value violates unique constraint \"job_media_job_id_role_")
def test_recover_attaches_outputs_once_by_upstream_id_charges_a_refunded_job_again_and_adds_to_a_done_job_free(db):
    k, acct = key(db), account(db)

    async def scenario(p):
        job_id = await failed_job(p, k)
        db.run("UPDATE jobs SET callback_url = 'https://h/c', callback_status = 'failed' WHERE id = %s", job_id)
        first = await store.recover_job(p, job_id, [out(0), out(1)], account_id=acct, note="from the conversation")
        again = await store.recover_job(p, job_id, [out(0), out(1)])                 # the same command, repeated
        extra = await store.recover_job(p, job_id, [out(1), out(2)])                 # one new output on a done job
        return job_id, first, again, extra

    job_id, first, again, extra = run(db, scenario)
    assert (first.status, first.attached, first.recharged) == ("done", 2, 10)
    assert (again.attached, again.recharged, extra.attached, extra.recharged) == (0, 0, 1, 0), "I8: one price"
    assert row(db, job_id, "status, error, error_code, finished_at IS NOT NULL, account_id IS NOT NULL, "
                           "callback_status, callback_attempts") == ("done", None, None, True, True, "pending", 0)
    assert [r[:2] for r in db.all("SELECT idx, upstream_id FROM job_media WHERE job_id = %s AND role = 'output' "
                                  "ORDER BY idx", job_id)] == [(0, "up-0"), (1, "up-1"), (2, "up-2")]
    assert db.all("SELECT 1 FROM job_media WHERE job_id = %s AND role = 'output' AND share_token IS NULL", job_id) == []
    assert db.wallet(k, PROVIDER) == (10, 10) and ledger(db, job_id) == (2, 1)


def test_recover_acts_only_on_a_finished_job_and_needs_outputs_with_ids(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        waiting = await waiting_job(p, k)
        with pytest.raises(JobRefused) as busy:
            await store.recover_job(p, waiting, [out(0)])
        with pytest.raises(ValueError):
            await store.recover_job(p, waiting, [])
        with pytest.raises(ValueError):
            await store.recover_job(p, waiting, [StoredOutput("o", "video/mp4", 1, "")])
        with pytest.raises(JobRefused) as missing:
            await store.recover_job(p, uuid.uuid4(), [out(0)])
        return waiting, busy.value.code, missing.value.code

    waiting, busy, missing = run(db, scenario)
    assert (busy, missing, status(db, waiting)) == ("job.busy", "resource.not_found", "waiting")


# ---- Regenerate --------------------------------------------------------------------------------------------------
async def copy_inputs(new_id, old):
    return tuple(store.InputMedia(m.idx, "%s/input/%d.png" % (new_id, m.idx), m.mime, m.size, m.width, m.height)
                 for m in old)


@mutation(RC, MARKER, MARKER_COPIED, expect="a marker from its own id")
def test_regenerate_makes_a_new_job_with_a_new_marker_charged_to_the_same_key_with_its_own_input_copies(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        made = await store.create_job(p, new_job(k, "a cat", price=7, inputs=[
            store.InputMedia(0, "old/input/0.png", "image/png", 5, 4, 3)]), SETTINGS)
        got = await claim(p)
        await submit(p, got, FakeEngine())
        db.run("UPDATE jobs SET title = 'my title' WHERE id = %s", made.job_id)
        await store.fail_job(p, made.job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
        again = await store.regenerate_job(p, made.job_id, SETTINGS, plan=new_job(k, "a cat", price=7).plan,
                                           copy_inputs=copy_inputs)
        return made.job_id, again

    old, again = run(db, scenario)
    cols = "key_id, provider, prompt, title, model, price, wallet_kind, params, status, marker"
    assert again.job_id != old and not again.replayed and again.status == "queued"
    new_row, old_row = row(db, again.job_id, cols), row(db, old, cols)
    assert new_row[:-2] == old_row[:-2] and new_row[-2] == "queued" and old_row[-2] == "failed"
    assert old_row[-1] == marker_for(old) and new_row[-1] == marker_for(again.job_id), "a marker from its own id"
    assert db.wallet(k, PROVIDER) == (7, 7) and ledger(db, again.job_id) == (1, 0), "charged once more, to the key"
    copies = db.all("SELECT object_key, share_token IS NOT NULL FROM job_media WHERE job_id = %s AND role = 'input'",
                    again.job_id)
    assert copies == [("%s/input/0.png" % again.job_id, True)]
    assert any("regenerated as job %s" % again.job_id in t[0] for t in db.all(
        "SELECT text FROM job_events WHERE job_id = %s", old))


@mutation(RC, PLAN_USED, PLAN_FREE, expect="assert row(db, again.job_id, \"price, submit_timeout_s\") == (14, 99)")
def test_regenerate_charges_the_callers_plan_not_the_old_jobs_price(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        old = await failed_job(p, k, price=10)                          # the old job was sold at 10
        today = new_job(k, "x", price=14, submit_timeout_s=99).plan     # today's price and timeout
        return old, await store.regenerate_job(p, old, SETTINGS, plan=today)

    old, again = run(db, scenario)
    assert row(db, again.job_id, "price, submit_timeout_s") == (14, 99)
    assert row(db, old, "price, submit_timeout_s")[0] == 10
    assert db.one("SELECT amount FROM ledger WHERE job_id = %s AND entry = 'charge'", again.job_id) == (14,)


def test_regenerate_goes_through_the_creation_gate_the_cap_and_the_wallet_refuse_it_with_no_charge(db):
    k = key(db, wallets=((PROVIDER, "video", 10, None), (PROVIDER, "image", None, None)), max_unfinished=1)
    account(db)
    today = new_job(k, "x").plan

    async def scenario(p):
        job_id = await failed_job(p, k)                    # charged 10, refunded: the day wallet is free again
        first = await store.regenerate_job(p, job_id, SETTINGS, plan=today)
        with pytest.raises(store.TooManyQueued):           # the new job is unfinished and the key's cap is 1
            await store.regenerate_job(p, job_id, SETTINGS, plan=today)
        db.run("UPDATE api_keys SET max_unfinished = 0 WHERE id = %s", k)
        with pytest.raises(billing.QuotaExceeded):         # the day limit of 10 is used up by the first one
            await store.regenerate_job(p, job_id, SETTINGS, plan=today)
        with pytest.raises(ValueError):
            made = await store.create_job(p, new_job(k, "with input", price=0, inputs=[
                store.InputMedia(0, "o/input/0.png", "image/png", 1)], completion="hold"), SETTINGS)
            await store.regenerate_job(p, made.job_id, SETTINGS, plan=today)   # copies are required with inputs
        return first

    first = run(db, scenario)
    assert db.one("SELECT count(*) FROM jobs WHERE prompt = 'x'") == (2,) and db.wallet(k, PROVIDER) == (10, 10)
    assert first.status == "queued"


async def db_now(p):
    """The database's clock: link expiry is written with it, so it is also the clock a link is judged against."""
    async with tx(p) as conn:
        return (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]


# ---- input links (I12) -------------------------------------------------------------------------------------------
@mutation(SH, SWEEP, SWEEP_ALL, expect="unknown_or_revoked")
def test_input_links_stop_at_expiry_and_at_revoke_and_only_the_ones_meant(db):
    k = key(db)

    async def scenario(p):
        table = store.PgShareTable(p)
        now = await db_now(p)
        jobs = []
        for name in ("a", "b", "c"):
            made = await store.create_job(p, new_job(k, name, inputs=[
                store.InputMedia(0, "%s/input/0.png" % name, "image/png", 1)]), SETTINGS)
            jobs.append(made.job_id)
        tokens = {j: db.one("SELECT share_token FROM job_media WHERE job_id = %s", j)[0] for j in jobs}
        a, b, c = jobs
        served = (await storage.resolve_share(table, tokens[a], now=now)).key
        db.run("UPDATE job_media SET share_expires_at = clock_timestamp() - interval '1 second' WHERE job_id = %s", a)
        with pytest.raises(storage.ShareRefused) as expired:                       # refused by its date alone
            await storage.resolve_share(table, tokens[a], now=await db_now(p))
        swept = await store.expire_input_links(p)
        with pytest.raises(storage.ShareRefused) as gone:                          # and now the token is unknown
            await storage.resolve_share(table, tokens[a], now=await db_now(p))
        still = (await storage.resolve_share(table, tokens[b], now=await db_now(p))).key
        revoked = await storage.revoke_job_shares(table, str(b))
        with pytest.raises(storage.ShareRefused) as revoked_gone:
            await storage.resolve_share(table, tokens[b], now=await db_now(p))
        other = (await storage.resolve_share(table, tokens[c], now=await db_now(p))).key
        return served, expired.value.reason, swept, gone.value.reason, still, revoked, revoked_gone.value.reason, other

    served, expired, swept, gone, still, revoked, revoked_gone, other = run(db, scenario)
    assert served == "a/input/0.png" and expired == "expired" and swept == 1 and gone == "unknown_or_revoked"
    assert still == "b/input/0.png", "a link that has not expired survives the sweep"
    assert revoked == 1 and revoked_gone == "unknown_or_revoked" and other == "c/input/0.png"


@mutation(RC, "except (billing.QuotaExceeded, billing.KeyDisabled) as e:", "except billing.BillingError as e:",
          expect="a missing wallet row is an internal error")
def test_recheck_with_a_missing_wallet_row_fails_loudly_and_revives_nothing(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        job_id = await failed_job(p, k)
        db.run("UPDATE jobs SET wallet_kind = 'audio' WHERE id = %s", job_id)      # the key has no such wallet
        try:
            await store.recheck_job(p, job_id)
        except billing.WalletMissing:
            return job_id, True
        return job_id, False

    job_id, raised = run(db, scenario)
    assert raised, "a missing wallet row is an internal error: Re-check must not swallow it"
    assert status(db, job_id) == "failed", "the job is not revived"
    assert ledger(db, job_id) == (1, 1)
