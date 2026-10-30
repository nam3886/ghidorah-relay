"""muse's results through the core's money path (the job test kit, a real database): what the key's points wallet
and the account's pool record for a refusal, a partial result and a lost send found later.

The results are muse's own, made by the engine against the in-process muse (fixtures/muse/spike-261026), then
applied as the worker applies them. The money ruling (Q4): any output is a full charge, zero outputs fail and refund
exactly once, nothing is charged or refunded twice."""

import asyncio
import dataclasses

import jobkit
from musekit import Ctx, Site, folders, history, job, settings, vm_routes

from relay_core.jobs import store
from relay_core.providers import Done, PollResult
from relay_core.testing.mutation import mutation
from relay_muse.engine import results
from relay_muse.engine import submit as submit_mod

SUBMIT_FILE = "packages/provider-muse/src/relay_muse/engine/submit.py"
CHAT_FILE = "packages/provider-muse/src/relay_muse/engine/chat.py"
RESULTS_FILE = "packages/provider-muse/src/relay_muse/engine/results.py"
PRICE = 10      # the fake provider's video price, charged to the key's points wallet at create


def ledger(db, key_id) -> list[tuple[str, int]]:
    return [tuple(r) for r in db.all("SELECT entry, amount FROM ledger WHERE key_id = %s AND scope = '*' ORDER BY id",
                                     key_id)]


def account_charges(db, job_id) -> list[tuple[int, bool]]:
    return [tuple(r) for r in db.all("SELECT amount, refunded_at IS NOT NULL FROM account_charges WHERE job_id = %s",
                                     job_id)]


def answered(name):
    return vm_routes([lambda req: history(name, req.path.rsplit("session_id=", 1)[1])])


async def muse_submit(name, **params):
    site = Site(vm_routes_=answered(name))
    return await submit_mod.submit(Ctx(), job(**params), settings(), deps=site.deps())


async def sent_job(p, key_id):
    """A job of the kit's fake provider, created (the key is charged), claimed and marked sent."""
    created = await jobkit.create(p, key_id, completion="hold")
    claimed = await jobkit.claim(p)
    await store.mark_sent(p, claimed)
    return created.job_id, claimed


REFUSED = "                        outcome = _result(Fail(errors.REFUSED, why), Success())"


@mutation(SUBMIT_FILE, REFUSED, "                        outcome = _result(Ambiguous(why), Success())",
          expect="refunded exactly once")
def test_an_answer_with_no_media_fails_and_refunds_exactly_once_and_a_later_read_changes_nothing(db):
    key_id, _ = jobkit.key(db), jobkit.account(db)

    async def scenario(p):
        job_id, claimed = await sent_job(p, key_id)
        status = await store.apply_submit(p, claimed, await muse_submit("job_j07.json"), caps=jobkit.CAPS,
                                          pools=jobkit.POOLS)
        late = await muse_reread(p, job_id)
        return job_id, status, late

    job_id, status, late = jobkit.run(db, scenario)
    assert ledger(db, key_id) == [("charge", PRICE), ("refund", PRICE)], "refunded exactly once"
    assert status == "failed" and jobkit.status(db, job_id) == "failed"
    assert all(refunded for _, refunded in account_charges(db, job_id)), "the account's charge stays"
    assert late == (None, "failed"), "a later poll or reconcile applied to a failed job"
    assert db.wallet(key_id)[1] == 0


async def muse_reread(p, job_id):
    """A poll and a reconcile that come late (a second worker, a Re-check): both muse results are offered to the
    core; the core must drop them for a job that is no longer waiting or unknown."""
    view = dataclasses.replace(await store.job_view(p, job_id), params={"kind": "image", "n": 1})
    found = await results.reconcile(Ctx(), view, settings(), deps=Site(vm_routes_=answered("job_j01.json")).deps())
    polled = await results.poll(Ctx(), view, settings(), deps=Site(vm_routes_=answered("job_j01.json")).deps())
    dropped = await store.apply_poll(p, job_id, "no-token", polled, caps=jobkit.CAPS, pools=jobkit.POOLS)
    after = await store.apply_reconciled(p, job_id, found, read_started_at=await _now(p), caps=jobkit.CAPS,
                                         pools=jobkit.POOLS, render_deadline_s=9000)
    return dropped, after


async def _now(p):
    async with p.connection() as conn:
        return (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]


PARTIAL = '    if r.media:\n        return "done"\n    return "refused" if r.answered else None'


@mutation(CHAT_FILE, PARTIAL, '    if r.media:\n        return None\n    return "refused" if r.answered else None',
          expect="a partial result is delivered")
def test_a_partial_result_is_a_full_charge_with_no_refund(db):
    key_id, _ = jobkit.key(db), jobkit.account(db)

    async def scenario(p):
        job_id, claimed = await sent_job(p, key_id)
        result = await muse_submit("job_j01.json", n=3)      # one image made of the three asked
        status = await store.apply_submit(p, claimed, result, caps=jobkit.CAPS, pools=jobkit.POOLS)
        late = await store.apply_poll(p, job_id, "no-token", PollResult(Done(getattr(result.job, "outputs", ()))),
                                      caps=jobkit.CAPS, pools=jobkit.POOLS)
        return job_id, status, len(getattr(result.job, "outputs", ())), late

    job_id, status, delivered, late = jobkit.run(db, scenario)
    assert status == "fetching" and delivered == 1, "a partial result is delivered: %s" % status
    assert ledger(db, key_id) == [("charge", PRICE)], "a partial result is charged in full and never refunded"
    assert account_charges(db, job_id) == [(1, False)]
    assert late is None


MEDIA_DIRS = 'MEDIA_DIRS = ("workspace/imagine_media/", "workspace/your_files/")'


@mutation("packages/provider-muse/src/relay_muse/constants.py", MEDIA_DIRS,
          'MEDIA_DIRS = ("workspace/imagine_media/",)', expect="a your_files video is delivered")
def test_a_video_in_your_files_is_delivered_and_charged_in_full(db):
    """Live 30/10 (job fba992d9): muse saved the video in workspace/your_files; read as no media, it was refunded."""
    key_id, _ = jobkit.key(db), jobkit.account(db)

    async def scenario(p):
        job_id, claimed = await sent_job(p, key_id)
        result = await muse_submit("ref-spike-301030/history_your_files.json", operation="text_to_video",
                                   ratio="16:9")
        status = await store.apply_submit(p, claimed, result, caps=jobkit.CAPS, pools=jobkit.POOLS)
        return job_id, status, getattr(result.job, "outputs", ())

    job_id, status, outs = jobkit.run(db, scenario)
    assert status == "fetching" and [o.ref for o in outs] == ["workspace/your_files/ocean_waves_beach_5s.mp4"], \
        "a your_files video is delivered: %s" % status
    assert ledger(db, key_id) == [("charge", PRICE)], "charged in full, never refunded"
    assert account_charges(db, job_id) == [(1, False)]


NEW_ONLY = ("    new = sorted((f for f in files if f.ms > since and f.mime.startswith(kind + \"/\")), "
            "key=lambda f: -f.ms)")
ANY_TIME = ("    new = sorted((f for f in files if f.ms > 0 and f.mime.startswith(kind + \"/\")), "
            "key=lambda f: -f.ms)")


@mutation(CHAT_FILE, NEW_ONLY, ANY_TIME, expect="an old file is refunded")
def test_a_new_unshown_file_is_charged_in_full_and_an_old_one_alone_is_refunded(db):
    """Live 30/10 (job aae305b4): muse saved the image and showed none; it was refunded while muse had made it."""
    key_id, _ = jobkit.key(db), jobkit.account(db)
    unlinked = "new-files-301030/history_unlinked_image.json"

    async def scenario(p, routes):
        job_id, claimed = await sent_job(p, key_id)
        result = await submit_mod.submit(Ctx(), job(), settings(), deps=Site(vm_routes_=routes).deps())
        return job_id, await store.apply_submit(p, claimed, result, caps=jobkit.CAPS, pools=jobkit.POOLS)

    made = vm_routes([answered_events(unlinked)], folders=folders())
    job_id, status = jobkit.run(db, lambda p: scenario(p, made))
    assert status == "fetching", "a new unshown file is delivered: %s" % status
    assert ledger(db, key_id) == [("charge", PRICE)] and account_charges(db, job_id) == [(1, False)]
    old = vm_routes([answered_events(unlinked)], folders=folders("2099-01-01T00:00:00.000Z"))
    job_id, status = jobkit.run(db, lambda p: scenario(p, old))
    assert status == "failed", "an old file is refunded: %s" % status
    assert ledger(db, key_id)[1:] == [("charge", PRICE), ("refund", PRICE)], "an old file is refunded"


def answered_events(name):
    return lambda req: history(name, req.path.rsplit("session_id=", 1)[1])


FOUND = '        return Found(session, {"session_id": session})'


@mutation(RESULTS_FILE, FOUND, "        return StillUnknown(answered=True)", expect="the lost send was found")
def test_a_lost_send_found_later_is_charged_once_whatever_reads_follow(db):
    key_id, _ = jobkit.key(db), jobkit.account(db)
    sid = "11111111-2222-4333-8444-555555555555"

    async def scenario(p):
        job_id, claimed = await sent_job(p, key_id)
        await store.apply_submit(p, claimed, submit_mod.SubmitResult(submit_mod.Ambiguous("no answer to the send")),
                                 caps=jobkit.CAPS, pools=jobkit.POOLS)
        view = dataclasses.replace(await store.job_view(p, job_id), external_ref=sid, params={"kind": "image", "n": 1})
        site = Site(vm_routes_=answered("job_j01.json"))
        found = await results.reconcile(Ctx(), view, settings(), deps=site.deps())
        first = await store.apply_reconciled(p, job_id, found, read_started_at=await _now(p), caps=jobkit.CAPS,
                                             pools=jobkit.POOLS, render_deadline_s=9000)
        again = await store.apply_reconciled(p, job_id, found, read_started_at=await _now(p), caps=jobkit.CAPS,
                                             pools=jobkit.POOLS, render_deadline_s=9000)
        token = (await _token(p, job_id))
        polled = await results.poll(Ctx(), view, settings(), deps=Site(vm_routes_=answered("job_j01.json")).deps())
        done = await store.apply_poll(p, job_id, token, polled, caps=jobkit.CAPS, pools=jobkit.POOLS)
        stale = await store.apply_poll(p, job_id, token, polled, caps=jobkit.CAPS, pools=jobkit.POOLS)
        return job_id, first, again, done, stale

    job_id, first, again, done, stale = jobkit.run(db, scenario)
    assert first == "waiting", "the lost send was found: %s" % first
    assert again == "waiting" and done == "fetching" and stale is None
    assert ledger(db, key_id) == [("charge", PRICE)], "charged once, never refunded"
    assert account_charges(db, job_id) == [(1, False)], "the account is charged once"


async def _token(p, job_id):
    """What the poller does when it claims a waiting job: a fresh poll token."""
    async with p.connection() as conn:
        row = await (await conn.execute(
            "UPDATE jobs SET poll_token = gen_random_uuid() WHERE id = %s RETURNING poll_token", (job_id,))).fetchone()
        await conn.commit()
        return row[0]


def test_the_kit_runs_muses_engine_not_a_fake():
    result = asyncio.run(muse_submit("job_j07.json"))
    assert result.job.code == "muse.refused"
