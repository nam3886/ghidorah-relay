"""The fetcher (contracts 7.1 "Fetching", 9; I11, I21, I23): outputs go through the engine into the real object
store, and the job becomes `done` with its media rows in ONE transaction, never before."""

import asyncio
import os
import uuid

import pytest
from jobkit import CAPS, POOLS, PROVIDER, account, key, run, status
from lifecyclekit import (
    MediaEngine,
    accepted,
    age,
    due,
    fetcher,
    pass_once,
    poller,
    reaper,
    row,
    to_fetching,
    to_waiting,
    until,
)

from relay_core import failpoints
from relay_core.jobs import recheck, store
from relay_core.providers import Done, Failed, PollResult
from relay_core.storage import ObjectStore, StorageError
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, sent
from relay_core.testing.mutation import mutation

F = "packages/relay-core/src/relay_core/jobs/fetcher.py"
A = "packages/relay-core/src/relay_core/jobs/apply.py"
PARTIAL = "rows.append(await self._store_one(ctx, c.job_id, i, out))"
PARTIAL_DONE = ("rows.append(await self._store_one(ctx, c.job_id, i, out))\n"
                "                    await apply_fetched(self.db, c.job_id, c.token, rows)")
MODES = ("hold", "detach")


@pytest.fixture
def objects():
    """A store on a bucket of its own, emptied and removed afterwards."""
    endpoint = os.environ.get("RELAY_TEST_MINIO", "")
    if not endpoint:
        raise RuntimeError("RELAY_TEST_MINIO is not set: run the tests through scripts/check.sh")
    s = ObjectStore(endpoint, os.environ["RELAY_TEST_MINIO_ACCESS_KEY"], os.environ["RELAY_TEST_MINIO_SECRET_KEY"],
                    bucket="relay-test-" + uuid.uuid4().hex[:12])
    asyncio.run(s.ensure_bucket())
    yield s
    for o in s._client.list_objects(s.bucket, recursive=True):
        s._client.remove_object(s.bucket, o.object_name)
    s._client.remove_bucket(s.bucket)


def media(db, job_id):
    return db.all("SELECT idx, object_key, mime, size, width, height, duration_s IS NOT NULL, upstream_id, "
                  "share_token IS NOT NULL FROM job_media WHERE job_id = %s AND role = 'output' ORDER BY idx", job_id)


def refunds(db, job_id) -> int:
    return db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'refund'", job_id)[0]


async def hold_fetching(p, db, k, n=1, model="fake-video"):
    """A job completed in the session with `n` outputs: `fetching`, due now."""
    job_id = await to_fetching(p, k, n=n, model=model)
    due(db, job_id)
    return job_id


@pytest.mark.parametrize("completion", MODES)
def test_a_fetched_job_is_done_with_real_files_in_the_store_and_its_media_rows(db, objects, completion):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        engine = MediaEngine()
        if completion == "hold":
            job_id = await to_fetching(p, k, model="fake-video")
        else:       # detach: accepted, polled to Done, then fetched
            engine.script_submit("*", sent(accepted()))
            job_id = await to_waiting(p, k, engine=engine, completion="detach")
            due(db, job_id)
            await pass_once(poller(p, engine))
        due(db, job_id)
        await pass_once(fetcher(p, engine, objects))
        sizes = {o.key: o.size for o in await objects.list_prefix(str(job_id) + "/")}
        return job_id, sizes

    job_id, sizes = run(db, scenario)
    assert status(db, job_id) == "done"
    [(idx, key_, mime, size, width, height, has_len, upstream, shared)] = media(db, job_id)
    assert (idx, key_, mime, width, height, has_len, shared) == (0, "%s/output/0.mp4" % job_id, "video/mp4", 64, 48,
                                                                 True, True)
    assert upstream == "up-%s-0" % job_id and sizes == {key_: size}, "the object is stored with the verified size"
    assert row(db, job_id, "finished_at IS NOT NULL, poll_token, next_poll_at") == (True, None, None)


def test_an_image_job_is_probed_for_its_dimensions_and_two_outputs_keep_their_order(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k, 2, "fake-image")
        await pass_once(fetcher(p, MediaEngine(), objects))
        return job_id

    job_id = run(db, scenario)
    rows = media(db, job_id)
    assert [(r[0], r[2], r[4], r[5], r[6]) for r in rows] == [(0, "image/png", 4, 3, False),
                                                              (1, "image/png", 4, 3, False)]
    assert [r[1] for r in rows] == ["%s/output/0.png" % job_id, "%s/output/1.png" % job_id]
    assert status(db, job_id) == "done"


def test_the_claim_takes_a_token_with_a_long_visibility_and_no_account_lease(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        eng = fetcher(p, MediaEngine(), objects)
        claimed = await eng._claim(3)
        assert await eng._claim(3) == [], "a claimed fetch is hidden"
        return job_id, claimed

    job_id, claimed = run(db, scenario)
    assert [c.job_id for c in claimed] == [job_id]
    assert row(db, job_id, "poll_token IS NOT NULL, next_poll_at > clock_timestamp() + interval '800 seconds'") \
        == (True, True)
    assert db.one("SELECT count(*) FROM leases") == (0,)


# ---- failures --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ("error", "empty"))
def test_a_failed_download_is_retried_and_then_succeeds(db, objects, mode):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        engine = MediaEngine()
        engine.mode(mode, "ok")
        eng = fetcher(p, engine, objects)
        await pass_once(eng)
        first = (row(db, job_id, "status, poll_token, next_poll_at > clock_timestamp()"), media(db, job_id))
        await asyncio.sleep(0.25)
        await pass_once(eng)
        return job_id, first

    job_id, first = run(db, scenario)
    assert first == (("fetching", None, True), []), "no rows and not done after a failed download"
    assert status(db, job_id) == "done" and len(media(db, job_id)) == 1
    assert refunds(db, job_id) == 0


@mutation("packages/relay-core/src/relay_core/jobs/fail.py",
          "order_completed = (await (await conn.execute(_ORDER_COMPLETED, (job_id,))).fetchone())[0]",
          "order_completed = False",
          expect="the account keeps the charge: upstream produced the output")
def test_a_download_that_keeps_failing_fails_the_job_at_the_render_deadline_and_refunds_once(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        age(db, job_id, "render_since", 10_000)                      # past the 9000 s render deadline
        engine = MediaEngine()
        engine.mode("error")
        eng = fetcher(p, engine, objects)
        await pass_once(eng)
        due(db, job_id)
        await pass_once(eng)                                         # nothing left to do for a failed job
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, error_code") == ("failed", "render.download_failed")
    assert refunds(db, job_id) == 1
    assert db.one("SELECT count(*) FROM account_charges WHERE job_id = %s AND refunded_at IS NULL",
                  job_id) == (1,), "the account keeps the charge: upstream produced the output"
    assert media(db, job_id) == []


@pytest.mark.parametrize("then", ["poll_failed", "render_deadline"])
@mutation("packages/relay-core/src/relay_core/jobs/fail.py",
          "order_completed = (await (await conn.execute(_ORDER_COMPLETED, (job_id,))).fetchone())[0]",
          "order_completed = False",
          expect="the order upstream completed keeps its account charge whichever path fails the job")
def test_an_order_upstream_completed_keeps_its_account_charge_after_a_recheck_and_a_later_failure(db, then):
    """D47 is decided by the job row (it records the outputs), not by the path that fails the job: a fetch failure,
    then a Re-check, then a poll failure or the render deadline, must still keep the account charge."""
    k, _ = key(db), account(db)

    async def claim_visible(p, job_id, state, visibility_s):
        due(db, job_id)
        [vis] = await store.claim_visible(p, provider=PROVIDER, status=state, visibility_s=visibility_s, limit=4)
        return vis

    async def scenario(p):
        job_id = await to_waiting(p, k)                              # a detach job: accepted upstream, has a reference
        vis = await claim_visible(p, job_id, "waiting", 300)
        outs = FakeEngine().outputs(await store.job_view(p, job_id), 1)
        assert await store.apply_poll(p, job_id, vis.token, PollResult(Done(outs)), caps=CAPS, pools=POOLS) \
            == "fetching"
        age(db, job_id, "render_since", 10_000)
        vis = await claim_visible(p, job_id, "fetching", 900)
        assert await store.apply_fetch_failed(p, job_id, vis.token, code="render.download_failed", detail="502",
                                              retry_in_s=1, deadline_s=9000, pools=POOLS) == "failed"
        await recheck.recheck_job(p, job_id)                         # back to waiting; the row still has the outputs
        if then == "poll_failed":
            vis = await claim_visible(p, job_id, "waiting", 300)
            await store.apply_poll(p, job_id, vis.token, PollResult(Failed("render.watermarked_only", "gone")),
                                   caps=CAPS, pools=POOLS)
        else:
            age(db, job_id, "render_since", 10_000)
            await reaper(p).tick()
        return job_id

    job_id = run(db, scenario)
    assert status(db, job_id) == "failed"
    assert db.all("SELECT refunded_at IS NULL FROM account_charges WHERE job_id = %s", job_id) == [(True,)], \
        "the order upstream completed keeps its account charge whichever path fails the job"
    assert db.one("SELECT sum(CASE entry WHEN 'charge' THEN amount ELSE -amount END) FROM ledger "
                  "WHERE job_id = %s", job_id) == (0,), "the key is refunded for every charge"


def test_a_file_that_does_not_probe_fails_the_job_at_once_with_the_refund(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        engine = MediaEngine()
        engine.mode("garbage")
        await pass_once(fetcher(p, engine, objects))
        return job_id, await objects.list_prefix(str(job_id) + "/")

    job_id, stored = run(db, scenario)
    assert row(db, job_id, "status, error_code") == ("failed", "render.file_unreadable")
    assert refunds(db, job_id) == 1 and stored == [], "nothing unreadable is kept in the store"


def test_an_object_store_failure_is_a_retried_download_not_a_lost_result(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        real = objects.put_file
        calls = []

        async def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise StorageError("object store unreachable")
            return await real(*a, **kw)

        objects.put_file = flaky
        eng = fetcher(p, MediaEngine(), objects)
        await pass_once(eng)
        mid = status(db, job_id)
        await asyncio.sleep(0.25)
        await pass_once(eng)
        return job_id, mid

    job_id, mid = run(db, scenario)
    assert (mid, status(db, job_id)) == ("fetching", "done")


# ---- I11: done only with every file and every row -----------------------------------------------------------------
@mutation(F, PARTIAL, PARTIAL_DONE, expect="one stored file is not enough: no rows, not done")
def test_a_job_is_not_done_while_any_output_is_missing(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k, 2)
        engine = MediaEngine()
        engine.mode("ok", "error")                        # the second output cannot be downloaded
        eng = fetcher(p, engine, objects)
        await pass_once(eng)
        first = (status(db, job_id), media(db, job_id))
        engine.fetch_modes.clear()
        await asyncio.sleep(0.25)
        await pass_once(eng)
        return job_id, first

    job_id, first = run(db, scenario)
    assert first == ("fetching", []), "one stored file is not enough: no rows, not done"
    assert status(db, job_id) == "done" and [r[0] for r in media(db, job_id)] == [0, 1]


def test_the_media_rows_and_the_done_status_commit_together_or_not_at_all(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)

        async def die() -> None:
            raise failpoints.FailpointError("between the rows and the status")

        failpoints.arm("fetch.rows_written", die)
        eng = fetcher(p, MediaEngine(), objects)
        await pass_once(eng)
        failpoints.disarm()
        return job_id

    job_id = run(db, scenario)
    assert status(db, job_id) == "fetching" and media(db, job_id) == [], "rolled back as one"


def test_a_fetch_result_is_dropped_when_the_token_is_no_longer_the_jobs(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        release, inside = asyncio.Event(), asyncio.Event()

        class Slow(MediaEngine):
            async def fetch(self, ctx, out):
                inside.set()
                await release.wait()
                async for chunk in super().fetch(ctx, out):
                    yield chunk

        eng = fetcher(p, Slow(), objects)
        await eng.tick()
        await asyncio.wait_for(inside.wait(), 5)
        db.run("UPDATE jobs SET poll_token = gen_random_uuid(), next_poll_at = clock_timestamp() + interval '1 hour'")
        release.set()
        await eng.idle()
        return job_id

    job_id = run(db, scenario)
    assert status(db, job_id) == "fetching" and media(db, job_id) == []


def test_drain_does_not_stop_the_fetcher_and_the_run_loop_stops_cleanly(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        eng = fetcher(p, MediaEngine(), objects, settings=FakeSettings(fake__claims_enabled=False))
        task = asyncio.create_task(eng.run())
        await until(lambda: status(db, job_id) == "done", what="the fetch")
        eng.stop()
        await asyncio.wait_for(task, 5)

    run(db, scenario)


def test_a_job_with_no_recorded_outputs_fails_instead_of_looping(db, objects):
    k, _ = key(db), account(db, left=100)

    async def scenario(p):
        job_id = await hold_fetching(p, db, k)
        db.run("UPDATE jobs SET provider_state = '{}'::jsonb WHERE id = %s", job_id)
        await pass_once(fetcher(p, MediaEngine(), objects))
        return job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "status, error_code") == ("failed", "render.file_unreadable") and refunds(db, job_id) == 1

