"""The plan step attaches the prompt frame: a job's text is rendered once from the model's
frame, at insert, and stored; nothing falls back to the core frame; the length cap answers before any charge; and
Regenerate gets today's frame."""

import logging
import uuid

from apikit import run_app
from doorkit import IMAGE, VIDEO, StrictFake, charges, error_of, jobs
from jobkit import POOLS, account, claim, create, key, run, submit

from relay_core import frames
from relay_core.api import doors
from relay_core.jobs import store
from relay_core.jobs.create import marker_for
from relay_core.testing.fake_provider import FAKE_FRAMES, FakeEngine, FakeSpec
from relay_core.testing.mutation import mutation

DOORS = "packages/relay-core/src/relay_core/api/doors.py"
FRAMES = "packages/relay-core/src/relay_core/frames.py"
VIDEO_PATH = "/v1/video/generations"
DEFAULT_VIDEO, DEFAULT_IMAGE = FAKE_FRAMES.defaults["video"], FAKE_FRAMES.defaults["image"]


def framed(**kw) -> FakeSpec:
    return FakeSpec(frames=FAKE_FRAMES, **kw)


def put(db, kind, model, text, provider="fake") -> int:
    """A frame version, as `relay serve` or the admin would have written it."""
    return db.one("INSERT INTO prompt_frames (provider, kind, model, text, created_by) VALUES (%s, %s, %s, %s, 'test') "
                  "RETURNING id", provider, kind, model, text)[0]


def seed(db, provider="fake") -> tuple[int, int]:
    return put(db, "video", None, DEFAULT_VIDEO, provider), put(db, "image", None, DEFAULT_IMAGE, provider)


def stored(db, job_id):
    return db.one("SELECT sent_text, marker, frame_id FROM jobs WHERE id = %s", job_id)


async def make(env, api_key, prompt="a cat", model=VIDEO, **extra):
    return await env.client.post(VIDEO_PATH if model.endswith("video") else "/v1/images/generations",
                                 headers=api_key.headers, json={"model": model, "prompt": prompt, **extra})


def test_a_job_stores_the_text_rendered_from_its_models_frame_and_the_version_it_used(db):
    video_default, image_default = seed(db)
    own = put(db, "video", "fake-video", "Own video words. {{client_prompt}} / {{tracking_code}}")

    async def scenario(env):
        api_key = await env.new_key()
        a = await make(env, api_key, "a  cat\non a mat")
        b = await make(env, api_key, "a dog", model=IMAGE)
        put(db, "video", "fake-video", None)                                  # follow the default again
        c = await make(env, api_key, "a fox")
        return [r.json()["id"] for r in (a, b, c)]

    a, b, c = run_app(db, scenario, spec=framed())
    ta, tb, tc = stored(db, a), stored(db, b), stored(db, c)
    assert ta[0] == "Own video words. a cat on a mat / [%s]" % ta[1] and ta[2] == own, "the own frame is ignored"
    assert tb[0] == "Fixed preamble for images. [%s] a dog" % tb[1] and tb[2] == image_default
    assert tc[0] == "Fixed preamble for videos. [%s] a fox" % tc[1] and tc[2] == video_default
    assert ta[1] == marker_for(uuid.UUID(a)), "the marker comes from the job's own id"


def test_a_provider_without_frames_gets_the_core_frame_with_no_frame_id(db):
    async def scenario(env):
        return (await make(env, await env.new_key(), "a cat")).json()["id"]

    got = stored(db, run_app(db, scenario))
    assert got[0] == "a cat [%s]" % got[1] and got[2] is None


@mutation(DOORS, "        if use is None:\n            raise frame_unusable(spec, model)",
          "        if use is None:\n            use = FrameUse(None, frames.CORE_FRAME)", expect="no silent fallback")
def test_a_provider_with_frames_and_no_row_is_500_frame_unusable_with_no_charge_and_no_job(db, caplog):
    async def scenario(env):
        api_key = await env.new_key()
        with caplog.at_level(logging.ERROR, logger="relay.api"):
            r = await make(env, api_key, "a cat")
        return api_key, r

    api_key, r = run_app(db, scenario, spec=framed())
    # the client sees the generic error: the detail is ours to fix
    assert error_of(r)[:2] == (500, "internal.unexpected"), "no silent fallback: %s" % r.text
    assert jobs(db, api_key.id) == [] and charges(db, key_id=api_key.id) == 0, "no silent fallback: a job or a charge"
    assert any("provider=fake kind=video model=fake-video" in rec.getMessage() for rec in caplog.records
               if rec.levelno == logging.ERROR), "the error log names provider, kind and model"


@mutation(DOORS, "FrameUse(frame.id, frames.check_frame(frame.text))", "FrameUse(frame.id, frame.text)",
          expect="the stored text was not validated again")
def test_a_stored_text_that_fails_the_grammar_is_refused_in_the_plan_step_as_frame_unusable(db, caplog):
    put(db, "video", None, DEFAULT_VIDEO + "​")           # the table CHECK cannot see an invisible character
    put(db, "image", None, DEFAULT_IMAGE)

    async def scenario(env):
        api_key = await env.new_key()
        with caplog.at_level(logging.ERROR, logger="relay.api"):
            return api_key, await make(env, api_key, "a cat")

    api_key, r = run_app(db, scenario, spec=framed())
    assert r.status_code == 500 and jobs(db, api_key.id) == [], "the stored text was not validated again: %s" % r.text
    assert charges(db, key_id=api_key.id) == 0
    assert any("no usable prompt frame" in rec.getMessage() for rec in caplog.records), \
        "the stored text was not validated again: the plan step did not refuse it itself"


@mutation(DOORS, "    if rules is not None and len(text) > rules.max_sent_chars:", "    if False:",
          expect="the length cap was not applied")
def test_a_rendered_text_over_the_providers_cap_is_400_prompt_too_long_before_any_charge(db):
    seed(db)
    sample = marker_for(doors.SAMPLE_JOB_ID)
    room = FAKE_FRAMES.max_sent_chars - len(frames.render(DEFAULT_VIDEO, "", sample)) - 1   # letters that still fit

    async def scenario(env):
        api_key = await env.new_key()
        fits = await make(env, api_key, "x" * room)
        over = await make(env, api_key, "x" * (room + 1))
        return api_key, fits, over

    # the prompt cap is out of the way (as large as the rendered cap): this test is about the rendered text
    spec = framed(max_prompt_chars=FAKE_FRAMES.max_sent_chars)
    api_key, fits, over = run_app(db, scenario, spec=spec)
    assert fits.status_code == 200, "the exact cap is allowed: %s" % fits.text
    assert error_of(over) == (400, "input.prompt_too_long", "prompt"), "the length cap was not applied: %s" % over.text
    assert len(jobs(db, api_key.id)) == 1 and charges(db, key_id=api_key.id) == 1, "the refused request was charged"
    assert len(stored(db, fits.json()["id"])[0]) == FAKE_FRAMES.max_sent_chars


def test_a_variant_of_a_model_uses_the_frame_of_its_base_model(db):
    seed(db, "strict")
    own = put(db, "video", "strict-video", "Base model words. {{client_prompt}} {{tracking_code}}", "strict")

    async def scenario(env):
        r = await env.client.post(VIDEO_PATH, headers=(await env.new_key()).headers,
                                  json={"model": "strict-video-5s", "prompt": "a cat"})
        return r.json()["id"]

    spec = StrictFake(frames=FAKE_FRAMES)
    got = stored(db, run_app(db, scenario, spec=spec))
    assert got[0] == "Base model words. a cat [%s]" % got[1] and got[2] == own


def test_regenerate_gets_the_frame_of_today_and_a_marker_of_its_own(db):
    seed(db)
    k, _ = key(db), account(db)

    async def failed(p) -> object:
        made = await create(p, k, "a cat")
        got = await claim(p)
        assert await submit(p, got, FakeEngine()) == "waiting"
        assert await store.fail_job(p, made.job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
        return made.job_id

    old = run(db, failed)
    put(db, "video", "fake-video", "Today. {{client_prompt}} {{tracking_code}}")      # saved after the old job was made

    async def scenario(env):
        r = await env.client.post("/admin/jobs/%s/regenerate" % old, headers=await env.admin_headers(),
                                  json={"operation_key": uuid.uuid4().hex})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    new = run_app(db, scenario, spec=framed())
    before, after = stored(db, old), stored(db, new)
    assert before[0] == "a cat [%s]" % before[1] and before[2] is None, "the old job keeps the text it was sent with"
    assert after[0] == "Today. a cat [%s]" % after[1] and after[1] != before[1], "a new frame and a new marker"
    assert after[2] is not None, "the new job records the frame version it used"


@mutation(DOORS, "    if frames.has_surrogate(prompt):", "    if False:",
          expect="a lone surrogate prompt reached the creation")
def test_a_prompt_with_a_lone_surrogate_is_refused_as_invalid_not_a_server_error(db):
    seed(db)

    async def scenario(env):
        api_key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, headers={**api_key.headers, "Content-Type": "application/json"},
                                  content=b'{"model": "fake-video", "prompt": "a\\ud800b"}')
        return api_key, r

    api_key, r = run_app(db, scenario, spec=framed())
    assert error_of(r) == (400, "input.invalid", "prompt"), "a lone surrogate prompt reached the creation: %s" % r.text
    assert jobs(db, api_key.id) == [] and charges(db, key_id=api_key.id) == 0
