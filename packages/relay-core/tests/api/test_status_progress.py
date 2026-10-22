"""The estimate on a job's status answer: `progress`, `render_started_at`, `estimated_finish_at`. The pure rules first
(no database), then the real app on real Postgres with planted jobs. The estimate read may never fail or stall the
answer, and only the two native status routes may cost one."""

import asyncio
import dataclasses
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from apikit import run_app
from doorkit import IMAGE, VIDEO, StrictFake, error_of, finish_failed

from relay_core.api import doors, progress
from relay_core.api.progress import Expected, bucket, finish_of, progress_of, render_started
from relay_core.providers import ModelSpec
from relay_core.testing.mutation import mutation

DOORS = "packages/relay-core/src/relay_core/api/doors.py"
NATIVE = "packages/relay-core/src/relay_core/api/routes_native.py"
PROG = "packages/relay-core/src/relay_core/api/progress.py"
VIDEO_PATH, IMAGE_PATH, CHAT_PATH = "/v1/video/generations", "/v1/images/generations", "/v1/chat/completions"
OLD_KEYS = ["id", "status", "stage", "result_urls", "images", "error", "error_code", "refunded", "points", "model",
            "duration", "ratio", "n", "style", "title", "warnings", "queue_position", "created_at", "finished_at"]
NEW_KEYS = ["progress", "render_started_at", "estimated_finish_at"]
DB_STATUSES = ("queued", "submitting", "unknown", "waiting", "fetching", "done", "failed")
CLOCK = datetime.fromisoformat("2026-10-18T10:03:40+00:00")
FALLBACK_VIDEO_S, FALLBACK_IMAGE_S = 3900, 90      # as progress.FALLBACK_S: the tests state them on purpose


@pytest.fixture(autouse=True)
def _fresh_estimate_state():
    """The shape cache, the per-job pin and the in-flight reads live in the process: every test starts without them."""
    for state in (progress._SHAPES, progress._PINNED, progress._INFLIGHT):
        state.clear()
    yield
    for state in (progress._SHAPES, progress._PINNED, progress._INFLIGHT):
        state.clear()


# ---- the pure rules -------------------------------------------------------------------------------------------------
@mutation(PROG, "WAIT_FLOOR = 5", "WAIT_FLOOR = 0", expect="waiting starts at 5")
@mutation(PROG, "WAIT_CAP = 90", "WAIT_CAP = 95", expect="waiting never above 90")
@mutation(PROG, "min(WAIT_CAP, ", "min(1000, ", expect="waiting never above 90")
@mutation(PROG, "FETCH = 95", "FETCH = 100", expect="fetching is 95")
@mutation(PROG, "DONE = 100", "DONE = 99", expect="done is 100")
@mutation(PROG, '    if status == "failed":\n        return None', '    if status == "failed":\n        return 0',
          expect="failed has no progress")
@mutation(PROG, 'if status in ("queued", "submitting", "unknown"):', 'if status in ("queued", "submitting"):',
          expect="before the render is 0")
def test_progress_of_every_status_by_name():
    assert progress_of("waiting", 0, 2520) == 5, "waiting starts at 5"
    assert progress_of("waiting", 1260, 2520) == 47, "waiting never above 90: halfway through the expected time is 47"
    assert progress_of("waiting", 2520, 2520) == 90, "waiting never above 90: the expected time reached is the cap"
    assert progress_of("waiting", 25200, 2520) == 90, "waiting never above 90, however long it runs"
    assert progress_of("fetching", 5, 2520) == 95, "fetching is 95"
    assert progress_of("done", 5, 2520) == 100, "done is 100"
    assert progress_of("failed", 5, 2520) is None, "failed has no progress"
    for before in ("queued", "submitting", "unknown"):
        assert progress_of(before, 600, 2520) == 0, "before the render is 0 (%s)" % before


@mutation(PROG, "max(0.0, elapsed_s / expected_s)", "(elapsed_s / expected_s)", expect="negative elapsed")
def test_progress_of_waiting_without_a_clock_or_an_expected_time_or_with_a_negative_one_is_5():
    assert progress_of("waiting", None, 2520) == 5, "no clock"
    assert progress_of("waiting", 100, None) == 5, "no expected time"
    assert progress_of("waiting", -252, 2520) == 5, "negative elapsed never goes below the floor"
    with pytest.raises(KeyError):
        progress_of("exploded", 1, 1)


def test_progress_never_goes_down_with_time_and_never_reaches_100_before_done():
    for expected in (60, 600, 2520, 3900, 9000):
        last = 0
        for tenth in range(0, 201):
            now = progress_of("waiting", expected * tenth / 10, expected)
            assert last <= now <= 90, "waiting never above 90 and never down (%d at %d)" % (now, expected)
            last = now
    for status in DB_STATUSES:
        if status != "done":
            assert progress_of(status, 10 ** 6, 60) != 100, "no status but done is 100: %s" % status


def test_the_statuses_the_estimate_knows_are_the_statuses_the_table_allows():
    sql = (Path(progress.__file__).parents[1] / "db" / "migrations" / "0003_jobs.sql").read_text()
    allowed = set(re.findall(r"'(\w+)'", re.search(r"status\s+text NOT NULL CHECK \(status IN\s*\(([^)]*)\)", sql)[1]))
    assert allowed == set(DB_STATUSES), "the table allows %s" % sorted(allowed)
    for status in allowed:
        progress_of(status, 1.0, 100)       # a status the estimate does not classify raises KeyError


@mutation(PROG, "elapsed_s < expected.seconds", "elapsed_s <= expected.seconds", expect="no estimate once overrun")
@mutation(PROG, 'status == "waiting" and clock is not None', 'True and clock is not None',
          expect="only while waiting")
@mutation(PROG, "and expected.measured", "and True", expect="no finish time without data")
@mutation(PROG, "expected.seconds + 30", "expected.seconds + 0", expect="rounded to the minute")
def test_finish_of_is_a_measured_time_to_the_minute_only_while_waiting_and_not_overdue():
    measured = Expected(2520, True)
    assert finish_of("waiting", CLOCK, 100, measured) == datetime.fromisoformat("2026-10-18T10:46:00+00:00"), (
        "the finish is the clock plus the expected time, rounded to the minute")
    assert finish_of("waiting", CLOCK, 100, Expected(2520, False)) is None, "no finish time without data"
    assert finish_of("waiting", CLOCK, 2520, measured) is None, "no estimate once overrun"
    assert finish_of("waiting", CLOCK, 9000, measured) is None, "no estimate once overrun"
    assert finish_of("waiting", None, 100, measured) is None, "no clock, no finish"
    assert finish_of("waiting", CLOCK, 100, None) is None, "a degraded read gives no finish"
    assert finish_of("waiting", CLOCK, None, measured) is None, "no clock, no finish"
    for status in (s for s in DB_STATUSES if s != "waiting"):
        assert finish_of(status, CLOCK, 100, measured) is None, "only while waiting (%s)" % status


@mutation(PROG, '_RENDERED = ("waiting", "fetching", "done")', '_RENDERED = ("queued", "waiting", "fetching", "done")',
          expect="no start before the render")
def test_render_started_is_the_clock_only_once_a_render_has_begun():
    for status in ("waiting", "fetching", "done"):
        assert render_started(status, CLOCK) == CLOCK, "a render that began shows its clock: %s" % status
    for status in ("queued", "submitting", "unknown", "failed"):
        assert render_started(status, CLOCK) is None, "no start before the render (%s), stale column or not" % status
    assert render_started("waiting", None) is None


@mutation(PROG, "int(median_s / BUCKET_S + 0.5)", "round(median_s / BUCKET_S)", expect="rounds half up")
@mutation(PROG, "max(BUCKET_S, BUCKET_S * ", "(BUCKET_S * ", expect="at least one bucket")
def test_bucket_is_a_whole_minute_rounded_half_up_and_never_zero():
    assert bucket(29) == 60, "at least one bucket"
    assert bucket(90) == 120 and bucket(2530) == 2520
    assert bucket(150) == 180, "a bucket rounds half up, not to the even one"


# ---- planting -------------------------------------------------------------------------------------------------------
class DurFake(StrictFake):
    """Models `strict-video`, `strict-video-pro` (its name extends the first one's) and `strict-image`; a plan stores
    the length the way a real provider's does, and the model name carries the variant the client wrote."""

    def catalog(self):
        return [*super().catalog(), ModelSpec("strict-video-pro", "video", ("generate",), prices={"default": 10})]

    def plan(self, req, settings, snapshots):
        plan = super().plan(req, settings, snapshots)
        return dataclasses.replace(plan, params={**plan.params, "duration": req.duration or 5})


SV = "strict-video"


def plant(db, key_id, status, *, model=VIDEO, kind="video", provider=None, params=None, clock_s=None, took_s=None,
          ended_s=60) -> uuid.UUID:
    """A job row in `status`. `clock_s`: its render clock started that many seconds ago (sent_at and render_since).
    `took_s`: a finished job that took that long and ended `ended_s` ago. A job past `queued` has an upstream
    reference."""
    job_id = db.job(key_id, provider=provider or model.split("-")[0], kind=kind)     # `fake-video` is the fake's
    params = {"duration": 5} if params is None and kind == "video" else (params or {})
    start_s = clock_s if took_s is None else ended_s + took_s
    started = "clock_timestamp() - make_interval(secs => %(start)s)"
    with db.connect() as c:
        c.execute("UPDATE jobs SET status = %(status)s, model = %(model)s, params = %(params)s::jsonb, "
                  "sent_at = " + started + ", render_since = " + started + ", taken_at = " + started + ", "
                  "accepted_at = " + started + ", "
                  "next_poll_at = CASE WHEN %(status)s IN ('waiting', 'fetching') THEN clock_timestamp() END, "
                  "error_code = CASE WHEN %(status)s = 'failed' THEN 'render.deadline' END, "
                  "finished_at = CASE WHEN %(took)s::float8 IS NULL THEN NULL "
                  "ELSE clock_timestamp() - make_interval(secs => %(ended)s) END, "
                  "external_ref = CASE WHEN %(status)s = 'queued' THEN NULL ELSE %(ref)s END WHERE id = %(id)s",
                  {"status": status, "model": model, "params": json.dumps(params), "start": start_s, "took": took_s,
                   "ended": ended_s, "ref": "ref-" + str(job_id), "id": job_id})
    if status == "done":
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, share_token) VALUES "
               "(%s, 'output', 0, %s, 'video/mp4', 1, %s)", job_id, "%s/output/0.bin" % job_id, "tok-" + str(job_id))
    return job_id


def clear_jobs(db) -> None:
    db.run("DELETE FROM job_media")
    db.run("DELETE FROM jobs")


def samples(db, key_id, n, took_s, status="done", ago_s=10, **kw) -> None:
    """`n` finished jobs of one shape that each took `took_s`, the newest ended `ago_s` ago."""
    for i in range(n):
        plant(db, key_id, status, took_s=took_s, ended_s=ago_s + i, **kw)


async def get(env, key, job_id, path=VIDEO_PATH) -> dict:
    r = await env.client.get("%s/%s" % (path, job_id), headers=key.headers)
    assert r.status_code == 200, "the answer must be served: %s" % r.text
    return r.json()


def clock_of(db, job_id) -> datetime:
    return db.one("SELECT COALESCE(render_since, sent_at) FROM jobs WHERE id = %s", job_id)[0]


def spy(monkeypatch, delay: float = 0.0) -> list:
    """Every call of the median read, with its shape."""
    calls: list = []
    real = progress._render_samples

    async def counted(conn, shape):
        calls.append(shape)
        if delay:
            await asyncio.sleep(delay)
        return await real(conn, shape)

    monkeypatch.setattr(progress, "_render_samples", counted)
    return calls


def spy_expected(monkeypatch) -> list:
    """Every ask for an expected time: a status call that must stay read-free never makes one."""
    calls: list = []
    real = doors._expected

    async def counted(*args, **kw):
        calls.append(args)
        return await real(*args, **kw)

    monkeypatch.setattr(doors, "_expected", counted)
    return calls


async def shown(env, db, key, samples_n, took_s, *, model=VIDEO, clock_s=300, **kw) -> dict:
    """Plant `samples_n` finished jobs, then a waiting job whose clock started `clock_s` ago; the poll of that job."""
    progress._SHAPES.clear()
    samples(db, key.id, samples_n, took_s, model=model, **kw)
    job = plant(db, key.id, "waiting", model=model, clock_s=clock_s)
    return await get(env, key, job)


# ---- every stage ----------------------------------------------------------------------------------------------------
@mutation(DOORS, '"progress": progress.progress_of(status, elapsed, expected.seconds if expected else None),',
          '"progress": 0,', expect="each stage")
def test_each_stage_shows_its_three_values(db):
    async def scenario(env):
        key = await env.new_key()
        made = (await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a cat"}, headers=key.headers)).json()
        queued = await get(env, key, made["id"])
        assert (queued["status"], queued["queue_position"]) == ("queued", 1)
        assert [queued[k] for k in NEW_KEYS] == [0, None, None], "each stage: queued"
        # a requeued job keeps stale clock columns: still no start
        for stage in ("submitting", "unknown"):
            body = await get(env, key, plant(db, key.id, stage, clock_s=600))
            assert [body[k] for k in NEW_KEYS] == [0, None, None], "each stage: %s" % stage
            db.run("UPDATE jobs SET status = 'queued', sent_at = NULL WHERE status = 'submitting'")

        waiting = plant(db, key.id, "waiting", clock_s=1950)
        body = await get(env, key, waiting)
        assert body["progress"] == 47 and body["estimated_finish_at"] is None, "each stage: waiting on the fallback"
        assert datetime.fromisoformat(body["render_started_at"]) == clock_of(db, waiting)

        measured = await shown(env, db, key, 5, 600)
        assert measured["progress"] == 47, "each stage: waiting on a measured shape"
        start = datetime.fromisoformat(measured["render_started_at"])
        assert datetime.fromisoformat(measured["estimated_finish_at"]) == (
            start + timedelta(seconds=630)).replace(second=0, microsecond=0), "each stage: finish to the minute"

        fetching = plant(db, key.id, "fetching", clock_s=600)
        body = await get(env, key, fetching)
        assert [body["progress"], body["estimated_finish_at"]] == [95, None], "each stage: fetching"
        assert datetime.fromisoformat(body["render_started_at"]) == clock_of(db, fetching)

        done = plant(db, key.id, "done", took_s=700)
        body = await get(env, key, done)
        assert [body["progress"], body["estimated_finish_at"]] == [100, None], "each stage: done"
        assert datetime.fromisoformat(body["render_started_at"]) == clock_of(db, done)

        # failed (refunded): nothing at all, with a stale clock and without one
        for clock in (600, None):
            job = (await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a cat"},
                                         headers=key.headers)).json()["id"]
            if clock:
                db.run("UPDATE jobs SET status = 'waiting', external_ref = %s, next_poll_at = now(), "
                       "sent_at = now() - make_interval(secs => %s), render_since = sent_at WHERE id = %s",
                       "r-" + job, clock, job)
            await finish_failed(db, job, expect=("waiting",) if clock else ("queued",))
            body = await get(env, key, job)
            assert (body["status"], body["refunded"]) == ("failed", True)
            assert [body[k] for k in NEW_KEYS] == [None, None, None], "each stage: failed has all three null"

        warned = plant(db, key.id, "queued")
        db.run("UPDATE jobs SET warnings = '[{\"code\": \"w\", \"text\": \"t\"}]'::jsonb WHERE id = %s", warned)
        body = await get(env, key, warned)
        assert body["warnings"] == [{"code": "w", "text": "t"}] and body["progress"] == 0

    run_app(db, scenario)


@mutation(DOORS, '"render_started_at": _iso(progress.render_started(status, clock)),',
          '"estimated_finish_at": _iso(progress.finish_of(status, clock, elapsed, expected)), '
          '"render_started_at": _iso(progress.render_started(status, clock)),', expect="key order")
def test_the_new_keys_come_after_the_nineteen_old_ones_which_keep_their_order_and_values(db):
    async def scenario(env):
        key = await env.new_key()
        job = (await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a", "title": "t"},
                                     headers=key.headers)).json()["id"]
        for path, ident in ((VIDEO_PATH, job), (VIDEO_PATH, plant(db, key.id, "waiting", clock_s=100)),
                            (IMAGE_PATH, plant(db, key.id, "waiting", model=IMAGE, kind="image", clock_s=10))):
            body = await get(env, key, ident, path)
            assert list(body)[:19] == OLD_KEYS, "key order: the old keys keep their names and order"
            assert list(body)[19:] == NEW_KEYS, "key order: the new keys come last, in this order"
        body = await get(env, key, job)
        assert (body["status"], body["stage"], body["queue_position"], body["title"], body["model"], body["points"],
                body["refunded"], body["finished_at"]) == ("queued", None, 1, "t", VIDEO, 10, False, None)

    run_app(db, scenario)


# ---- where "expected" comes from ------------------------------------------------------------------------------------
@mutation(PROG, "MIN_SAMPLES = 5", "MIN_SAMPLES = 1", expect="below the minimum uses the fallback")
@mutation(PROG, "measured = n >= MIN_SAMPLES", "measured = True", expect="no finish time without data")
def test_below_the_minimum_progress_runs_on_the_fallback_and_no_finish_is_promised(db):
    async def scenario(env):
        key = await env.new_key()
        for n in (0, 4):
            body = await shown(env, db, key, n, 600)
            assert body["progress"] == 11, "below the minimum uses the fallback, no finish time without data: %s" % (
                body)
            assert body["estimated_finish_at"] is None, "no finish time without data (%d samples)" % n
            clear_jobs(db)
        body = await shown(env, db, key, 5, 600)
        assert body["progress"] == 47 and body["estimated_finish_at"], "the fifth sample makes it measured: %s" % body

    run_app(db, scenario)


@mutation(PROG, "WINDOW = 20", "WINDOW = 200", expect="newest window")
@mutation(PROG, "ORDER BY j.finished_at DESC", "ORDER BY j.finished_at ASC", expect="newest window")
def test_the_median_is_over_the_newest_twenty_jobs(db):
    async def scenario(env):
        key = await env.new_key()
        for i in range(20):
            plant(db, key.id, "done", took_s=600, ended_s=100 + i)
        for i in range(25):
            plant(db, key.id, "done", took_s=6000, ended_s=10000 + i)
        job = plant(db, key.id, "waiting", clock_s=300)
        assert (await get(env, key, job))["progress"] == 47, "newest window: 20 jobs at 600 s decide"

    run_app(db, scenario)


@mutation(PROG, "percentile_cont(0.5) WITHIN GROUP (ORDER BY d)", "avg(d)", expect="median not mean")
def test_it_is_a_median_not_an_average(db):
    async def scenario(env):
        key = await env.new_key()
        body = await shown(env, db, key, 5, 600)
        samples(db, key.id, 1, 3000)
        progress._SHAPES.clear()
        progress._PINNED.clear()
        again = await get(env, key, plant(db, key.id, "waiting", clock_s=300))
        assert body["progress"] == again["progress"] == 47, "median not mean: one long job does not move it"

    run_app(db, scenario)


@mutation(PROG, '"AND j.finished_at > clock_timestamp() - make_interval(days => %(age_days)s) "',
          '"AND %(age_days)s::int IS NOT NULL "', expect="age bound")
@mutation(PROG, '"AND j.finished_at <= COALESCE(j.render_since, j.sent_at) + make_interval(secs => %(max_s)s) "',
          '"AND %(max_s)s::int IS NOT NULL "', expect="deadline bound")
@mutation(PROG, '"AND j.finished_at > COALESCE(j.render_since, j.sent_at) "', '"AND %(max_s)s::int IS NOT NULL "',
          expect="finished before it started")
def test_a_sample_must_be_recent_inside_the_deadline_and_finish_after_it_started(db):
    async def scenario(env):
        key = await env.new_key()
        old = await shown(env, db, key, 5, 600, ago_s=31 * 86400)
        assert old["progress"] == 11 and old["estimated_finish_at"] is None, "age bound: 31 days old is ignored"
        clear_jobs(db)
        progress._SHAPES.clear()
        samples(db, key.id, 5, 600)
        samples(db, key.id, 5, 20000)
        long_ones = await get(env, key, plant(db, key.id, "waiting", clock_s=300))
        assert long_ones["progress"] == 47, "deadline bound: a sample past the render deadline is ignored"
        clear_jobs(db)
        backwards = await shown(env, db, key, 5, -30)
        assert backwards["progress"] == 11, "a sample that finished before it started is ignored: %s" % backwards

    run_app(db, scenario)


@mutation(PROG, "AND j.status = 'done' ", "AND j.status <> 'x' ", expect="comparable only")
@mutation(PROG, '"AND (j.model = ANY(%(names)s) "', '"AND (%(names)s::text[] IS NOT NULL "', expect="comparable only")
@mutation(PROG, '"FROM jobs j WHERE j.provider = %(provider)s "', '"FROM jobs j WHERE %(provider)s::text IS NOT NULL "',
          expect="comparable only")
@mutation(PROG, "IS NOT DISTINCT FROM %(duration)s::numeric ", "IS NOT DISTINCT FROM %(duration)s::numeric OR true ",
          expect="comparable only")
def test_only_comparable_jobs_make_a_sample(db):
    async def scenario(env):
        key = await env.new_key()
        progress._SHAPES.clear()
        samples(db, key.id, 5, 600, model=IMAGE)                           # another model
        samples(db, key.id, 5, 600, provider="other")                      # another provider
        samples(db, key.id, 5, 600, status="failed")                       # not done
        samples(db, key.id, 5, 600, params={"duration": 10})               # another length
        body = await get(env, key, plant(db, key.id, "waiting", clock_s=300))
        assert body["progress"] == 11 and body["estimated_finish_at"] is None, "comparable only: %s" % body

    run_app(db, scenario)


@mutation(PROG, "jsonb_typeof(j.params -> 'duration') = 'number'", "jsonb_typeof(j.params -> 'duration') = 'string'",
          expect="duration types")
def test_a_length_is_a_json_number_and_a_variant_shares_samples_with_the_native_job(db, monkeypatch):
    from relay_core.api import facade_common as fc
    monkeypatch.setattr(fc, "HOLD_LIMIT_S", 0.3)
    monkeypatch.setattr(fc, "POLL_S", 0.05)

    async def scenario(env):
        key = await env.new_key()
        chat = {"model": "strict-video-5s", "messages": [{"role": "user", "content": "a cat"}]}
        made = await env.client.post(CHAT_PATH, json=chat, headers=key.headers)
        assert made.status_code == 200, made.text
        variant = made.json()["job_id"]
        assert db.one("SELECT model FROM jobs WHERE id = %s", variant)[0] == "strict-video-5s"
        samples(db, key.id, 4, 600, model="strict-video-5s-16:9")
        samples(db, key.id, 1, 600, model="strict-video-5s-16:9", params={"duration": "5"})
        db.run("UPDATE jobs SET status = 'waiting', external_ref = 'r', next_poll_at = now(), "
               "sent_at = now() - interval '300 seconds', render_since = sent_at WHERE id = %s", variant)
        body = await get(env, key, variant)
        assert body["progress"] == 11, "duration types: the string 5 is not the number 5: %s" % body
        samples(db, key.id, 1, 600, model="strict-video-5s-16:9", params={"duration": 5.0})
        progress._SHAPES.clear()
        native = (await env.client.post(VIDEO_PATH, json={"model": "strict-video", "prompt": "a", "duration": 5},
                                        headers=key.headers)).json()["id"]
        db.run("UPDATE jobs SET status = 'waiting', external_ref = 'r2', next_poll_at = now(), "
               "sent_at = now() - interval '300 seconds', render_since = sent_at WHERE id = %s", native)
        body = await get(env, key, native)
        assert body["progress"] == 47, "duration types: 5.0 is the shape of 5, shared with the variant: %s" % body

    run_app(db, scenario, spec=DurFake())


@mutation(PROG, '"OR EXISTS (SELECT 1 FROM unnest(%(stems)s::text[]) s WHERE starts_with(j.model, s))) "',
          '"OR false) "', expect="two ratios share samples")
@mutation(PROG, '"AND NOT (j.model = ANY(%(others)s) "', '"AND NOT (false "', expect="a longer name is not mixed in")
@mutation(PROG, '"OR EXISTS (SELECT 1 FROM unnest(%(other_stems)s::text[]) o WHERE starts_with(j.model, o))) "',
          '"OR false) "', expect="a longer name is not mixed in")
def test_the_shape_is_the_base_model_and_length_whatever_ratio_the_variant_names(db):
    async def scenario(env):
        key = await env.new_key()
        samples(db, key.id, 2, 600, model="strict-video-5s-16:9")
        samples(db, key.id, 2, 600, model="strict-video-5s-21:9")
        samples(db, key.id, 1, 600, model="strict-video")
        other_ratio = await get(env, key, plant(db, key.id, "waiting", model="strict-video-5s-9:16", clock_s=300))
        assert other_ratio["progress"] == 47, "two ratios share samples: 5 jobs of the base model and length"
        clear_jobs(db)
        progress._SHAPES.clear()
        samples(db, key.id, 2, 600, model="strict-video-5s-16:9")
        samples(db, key.id, 2, 600, model="strict-video-5s-21:9")
        samples(db, key.id, 5, 600, model="strict-video-pro-5s-16:9")      # a variant of the longer name
        samples(db, key.id, 5, 600, model="strict-video-pro")              # the longer name itself
        base = await get(env, key, plant(db, key.id, "waiting", model="strict-video-5s-16:9", clock_s=300))
        assert base["progress"] == 11, "a longer name is not mixed in: its samples are not the base model's"
        pro = await get(env, key, plant(db, key.id, "waiting", model="strict-video-pro-5s-21:9", clock_s=300))
        assert pro["progress"] == 47, "the longer name has its own shape, and the base model's samples stay out"

    run_app(db, scenario, spec=DurFake())


@mutation(DOORS, 'if estimate and status == "waiting" and clock is not None:', 'if estimate and status == "waiting":',
          expect="no read without a clock")
def test_an_overrun_sits_at_90_with_no_finish_and_a_job_without_a_clock_reads_nothing(db, monkeypatch):
    async def scenario(env):
        key = await env.new_key()
        over = await shown(env, db, key, 5, 600, clock_s=3 * 3600)
        assert (over["progress"], over["estimated_finish_at"]) == (90, None), "an overrun stays at 90: %s" % over
        assert over["render_started_at"]
        calls = spy_expected(monkeypatch)
        none = plant(db, key.id, "waiting")
        db.run("UPDATE jobs SET sent_at = NULL, render_since = NULL WHERE id = %s", none)
        body = await get(env, key, none)
        assert [body[k] for k in NEW_KEYS] == [5, None, None], "no clock: %s" % body
        assert calls == [], "no read without a clock"

    run_app(db, scenario)


def test_an_image_job_runs_on_the_image_fallback_and_promises_no_finish(db):
    async def scenario(env):
        key = await env.new_key()
        spec = dict(model=IMAGE, kind="image")
        waiting = await get(env, key, plant(db, key.id, "waiting", clock_s=45, **spec), IMAGE_PATH)
        assert [waiting["progress"], waiting["estimated_finish_at"]] == [47, None], "image waiting: %s" % waiting
        for status, value in (("queued", 0), ("fetching", 95)):
            body = await get(env, key, plant(db, key.id, status, clock_s=None if status == "queued" else 5, **spec),
                             IMAGE_PATH)
            assert body["progress"] == value and body["estimated_finish_at"] is None, "image %s: %s" % (status, body)
        done = await get(env, key, plant(db, key.id, "done", took_s=55, **spec), IMAGE_PATH)
        assert done["progress"] == 100

    run_app(db, scenario)


# ---- the clock ------------------------------------------------------------------------------------------------------
@mutation(DOORS, '", COALESCE(j.render_since, j.sent_at), "', '", j.accepted_at, "', expect="restarts the clock")
@mutation(DOORS, "clock_timestamp() - COALESCE(j.render_since, j.sent_at)", "clock_timestamp() - j.accepted_at",
          expect="restarts the clock")
def test_a_recheck_restarts_the_clock_and_a_rechecked_failed_job_starts_low(db):
    async def scenario(env):
        key = await env.new_key()
        admin = await env.admin_headers()
        waiting = plant(db, key.id, "waiting", clock_s=1950)
        assert (await get(env, key, waiting))["progress"] == 47
        assert (await env.client.post("/admin/jobs/%s/recheck" % waiting, headers=admin)).status_code == 200
        again = await get(env, key, waiting)
        assert again["progress"] == 5, "a re-check restarts the clock: low again"
        assert datetime.fromisoformat(again["render_started_at"]) == clock_of(db, waiting), (
            "a re-check restarts the clock: the start is the new one")
        assert db.one("SELECT render_since > accepted_at FROM jobs WHERE id = %s", waiting)[0], "restarts the clock"

        job = (await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a"},
                                     headers=key.headers)).json()["id"]
        db.run("UPDATE jobs SET status = 'waiting', external_ref = 'r', next_poll_at = now(), "
               "sent_at = now() - interval '9000 seconds', render_since = sent_at WHERE id = %s", job)
        await finish_failed(db, job, expect=("waiting",))
        assert [(await get(env, key, job))[k] for k in NEW_KEYS] == [None, None, None]
        assert (await env.client.post("/admin/jobs/%s/recheck" % job, headers=admin)).status_code == 200
        body = await get(env, key, job)
        assert (body["status"], body["progress"]) == ("running", 5), "a rechecked failed job starts low: %s" % body

    run_app(db, scenario)


def test_restore_requeue_and_re_accept_follow_the_clock_not_the_stale_columns(db):
    async def scenario(env):
        key = await env.new_key()
        job = plant(db, key.id, "waiting", clock_s=1950)
        assert (await get(env, key, job))["progress"] == 47
        db.run("UPDATE jobs SET status = 'unknown' WHERE id = %s", job)                   # the restore mark
        body = await get(env, key, job)
        assert [body[k] for k in NEW_KEYS] == [0, None, None], "restore mark: unknown shows no start: %s" % body
        db.run("UPDATE jobs SET status = 'queued', sent_at = NULL WHERE id = %s", job)    # a proven NotSent requeue
        body = await get(env, key, job)
        assert [body[k] for k in NEW_KEYS] == [0, None, None], "requeue keeps stale columns, shows none: %s" % body
        db.run("UPDATE jobs SET status = 'waiting', sent_at = now(), render_since = now(), accepted_at = now(), "
               "taken_at = now() WHERE id = %s", job)                                     # the re-accept
        body = await get(env, key, job)
        assert body["progress"] == 5 and body["render_started_at"], "re-accept: a new clock, a low value: %s" % body

    run_app(db, scenario)


# ---- the estimate may never fail the answer -------------------------------------------------------------------------
@mutation(PROG, '    except Exception as exc:\n        log.warning("render estimate read failed',
          '    except KeyError as exc:\n        log.warning("render estimate read failed',
          expect="answer must be served")
def test_a_failing_or_stalled_estimate_read_still_serves_the_answer_and_caches_nothing(db, monkeypatch, caplog):
    real = progress._render_samples
    monkeypatch.setattr(progress, "GUARD_TIMEOUT_S", 0.2)

    async def canceled(conn, shape):
        raise psycopg.errors.QueryCanceled("secret row text")

    async def crashed(conn, shape):
        raise RuntimeError("secret row text")

    async def stalled(conn, shape):
        await asyncio.sleep(10)

    async def scenario(env):
        key = await env.new_key()
        job = plant(db, key.id, "waiting", clock_s=1950)
        with caplog.at_level(logging.WARNING, logger="relay_core.api.progress"):
            for fake in (canceled, crashed, stalled):
                monkeypatch.setattr(progress, "_render_samples", fake)
                body = await get(env, key, job)
                assert list(body)[:19] == OLD_KEYS and body["status"] == "running", "the 19 old keys stay: %s" % body
                assert (body["progress"], body["estimated_finish_at"]) == (5, None), "degraded to the stage only"
                assert body["render_started_at"], "the start is a fact and stays"
                assert not progress._SHAPES and not progress._PINNED, "a failed read is never cached"
        text = caplog.text
        assert all(name in text for name in ("QueryCanceled", "RuntimeError", "TimeoutError")), text
        assert "secret row text" not in text, "the log carries the exception class, never its text"
        monkeypatch.setattr(progress, "_render_samples", real)
        assert (await get(env, key, job))["progress"] == 47, "nothing was cached: the next poll reads"

    run_app(db, scenario)


@mutation(PROG, '"SELECT set_config(\'statement_timeout\', %s, true)"', '"SELECT %s::text"',
          expect="the read is time limited")
def test_the_estimate_read_is_time_limited_by_the_database(db, monkeypatch, caplog):
    async def slow(conn, shape):
        await conn.execute("SELECT pg_sleep(2)")
        return 5, 600.0

    monkeypatch.setattr(progress, "_render_samples", slow)
    monkeypatch.setattr(progress, "GUARD_TIMEOUT_S", 5)

    async def scenario(env):
        key = await env.new_key()
        job = plant(db, key.id, "waiting", clock_s=1950)
        with caplog.at_level(logging.WARNING, logger="relay_core.api.progress"):
            body = await get(env, key, job)
        assert body["progress"] == 5 and "QueryCanceled" in caplog.text, "the read is time limited: %s" % caplog.text

    run_app(db, scenario)


@mutation(DOORS, "    except Exception as exc:     # a model gone from the catalog",
          "    except KeyError as exc:     # a model gone from the catalog", expect="answer must be served")
def test_a_model_gone_from_the_catalog_or_a_catalog_that_fails_still_serves_the_answer(db):
    async def scenario(env):
        key = await env.new_key()
        gone = await get(env, key, plant(db, key.id, "waiting", model="vanished-9", clock_s=300))
        assert (gone["progress"], gone["estimated_finish_at"]) == (5, None), gone
        env.registry.spec("strict").broken = True
        down = await get(env, key, plant(db, key.id, "waiting", model="strict-video-5s", clock_s=300))
        assert (down["progress"], down["estimated_finish_at"]) == (5, None), down

    run_app(db, scenario, spec=DurFake())


# ---- cost: one read per shape per TTL -------------------------------------------------------------------------------
@mutation(PROG, "TTL_S = 300", "TTL_S = 0", expect="one read per shape per TTL")
@mutation(PROG, "if shape in _INFLIGHT:", "if False:", expect="single read for concurrent polls")
@mutation(PROG, "if job_id in _PINNED:", "if False:", expect="pinned job does not move")
def test_one_read_per_shape_per_ttl_and_a_job_already_shown_does_not_move(db, monkeypatch):
    calls = spy(monkeypatch, delay=0.05)
    moment = [1000.0]
    monkeypatch.setattr(progress, "_now", lambda: moment[0])

    async def scenario(env):
        key = await env.new_key()
        jobs = [plant(db, key.id, "waiting", clock_s=300) for _ in range(4)]
        answers = await asyncio.gather(*[env.client.get("%s/%s" % (VIDEO_PATH, jobs[i % 4]), headers=key.headers)
                                         for i in range(20)])
        assert [r.status_code for r in answers] == [200] * 20, "single read for concurrent polls: %s" % answers[0].text
        assert len(calls) == 1, "single read for concurrent polls: %d reads for 20 polls" % len(calls)
        later = plant(db, key.id, "waiting", clock_s=300)
        assert (await get(env, key, later))["progress"] == 11 and len(calls) == 1, (
            "one read per shape per TTL: a later job of the shape must not read again")
        other = plant(db, key.id, "waiting", clock_s=300, params={"duration": 10})
        await get(env, key, other)
        assert len(calls) == 2, "a second shape makes one more read"

        samples(db, key.id, 5, 600)                       # the shape now has data
        moment[0] += progress.TTL_S + 1
        fresh = plant(db, key.id, "waiting", clock_s=300)
        assert (await get(env, key, fresh))["progress"] == 47 and len(calls) == 3, "after the TTL one more read"
        assert (await get(env, key, jobs[0]))["progress"] == 11, "a pinned job does not move on a refresh"

    run_app(db, scenario)


# ---- who may cost a read --------------------------------------------------------------------------------------------
@mutation(DOORS, 'if estimate and status == "waiting"', 'if status == "waiting"', expect="zero reads")
@mutation(DOORS, "media_type: str | None = None, estimate: bool = False,",
          "media_type: str | None = None, estimate: bool = True,", expect="zero reads")
def test_the_facade_the_replay_and_a_foreign_key_make_zero_estimate_reads(db, monkeypatch):
    from relay_core.api import facade_common as fc
    calls = spy_expected(monkeypatch)

    async def scenario(env):
        key, other = await env.new_key(), await env.new_key()
        job = plant(db, key.id, "waiting", clock_s=300)
        await fc.result_text(env.pool, "", key.id, job, ("done", None))                  # the facade's result text
        made = await doors._made_of(env.pool, key.id, job, "queued", "fake", [])         # a replay's queue position
        assert made.queue_position == 0
        r = await env.client.get("%s/%s" % (VIDEO_PATH, job), headers=other.headers)
        assert error_of(r)[:2] == (404, "resource.not_found")
        for status in ("queued", "fetching"):
            await get(env, key, plant(db, key.id, status, clock_s=None if status == "queued" else 60))
        await get(env, key, plant(db, key.id, "done", took_s=600))
        failed = (await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a"},
                                        headers=key.headers)).json()["id"]
        await finish_failed(db, failed)
        await get(env, key, failed)
        first = await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "b"},
                                      headers={**key.headers, "Idempotency-Key": "k"})
        again = await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "b"},
                                      headers={**key.headers, "Idempotency-Key": "k"})
        assert first.json()["id"] == again.json()["id"]
        assert calls == [], "zero reads: %d asks for an expected time" % len(calls)

    run_app(db, scenario)


@mutation(NATIVE, 'media_type="video", estimate=True,', 'media_type="video",',
          expect="native status reads the estimate")
@mutation(NATIVE, 'media_type="image", estimate=True,', 'media_type="image",',
          expect="native status reads the estimate")
def test_both_native_status_routes_read_the_estimate(db, monkeypatch):
    calls = spy_expected(monkeypatch)

    async def scenario(env):
        key = await env.new_key()
        video = await get(env, key, plant(db, key.id, "waiting", clock_s=300))
        assert video["progress"] == 11 and len(calls) == 1, "native status reads the estimate (video): %s" % video
        image = await get(env, key, plant(db, key.id, "waiting", model=IMAGE, kind="image", clock_s=45), IMAGE_PATH)
        assert image["progress"] == 47 and len(calls) == 2, "native status reads the estimate (image): %s" % image

    run_app(db, scenario)
