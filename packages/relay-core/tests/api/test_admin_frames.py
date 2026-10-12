"""Admin: prompt frames and keep-own locks (contracts 4.7, 5.1, 16): list, history, save, rollback, reset, the
lock, apply to all and the preview. Every write records the admin session's user; a refusal stores nothing."""

import asyncio

from apikit import ADMIN_USER, run_app
from doorkit import error_of
from jobkit import PROVIDER

from relay_core import failpoints, frames
from relay_core.api import doors
from relay_core.jobs.create import marker_for
from relay_core.providers import ModelSpec
from relay_core.testing.fake_provider import FAKE_FRAMES, FAKE_MAX_PROMPT_CHARS, FakeSpec
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_frames.py"
FRAMES = "packages/relay-core/src/relay_core/frames.py"
BASE = "/admin/frames/" + PROVIDER
DEFAULT_VIDEO, DEFAULT_IMAGE = FAKE_FRAMES.defaults["video"], FAKE_FRAMES.defaults["image"]
CORE = frames.CORE_FRAME
A, B, C = "fake-a-video", "fake-b-video", "fake-c-video"


class Three(FakeSpec):
    """Three video models and one image model: apply to all has more than one model to walk."""

    def catalog(self):
        return [ModelSpec(m, "video", ("generate",), prices={"default": 10}) for m in (A, B, C)] + [
            ModelSpec("fake-image", "image", ("generate",), prices={"default": 1})]


def spec() -> Three:
    return Three(frames=FAKE_FRAMES)


def put(db, kind, model, text, provider=PROVIDER) -> int:
    return db.one("INSERT INTO prompt_frames (provider, kind, model, text, created_by) VALUES (%s, %s, %s, %s, 'test') "
                  "RETURNING id", provider, kind, model, text)[0]


def seed(db) -> tuple[int, int]:
    return put(db, "video", None, DEFAULT_VIDEO), put(db, "image", None, DEFAULT_IMAGE)


def rows(db, where="TRUE") -> int:
    return db.one("SELECT count(*) FROM prompt_frames WHERE " + where)[0]


async def go(env, method, path, **kw):
    return await env.client.request(method, "/admin" + path, headers=await env.admin_headers(), **kw)


# ---- the list -----------------------------------------------------------------------------------------------------
@mutation(ROUTES, '"shipped_differs": default is not None and default.text != shipped',
          '"shipped_differs": False', expect="shipped_differs is always false")
def test_the_list_shows_every_model_with_its_default_source_lock_and_whether_the_default_differs_from_the_shipped(db):
    video_default, _ = seed(db)
    own = put(db, "video", A, "Own. " + CORE)

    async def scenario(env):
        before = await go(env, "GET", "/frames?provider=" + PROVIDER)
        put(db, "video", None, "Edited. " + CORE)                              # the admin changed the default
        await go(env, "PUT", "/frames/%s/video/%s/lock" % (PROVIDER, B), json={"locked": True})
        return before, await go(env, "GET", "/frames?provider=" + PROVIDER)

    before, after = run_app(db, scenario, spec=spec())
    assert before.status_code == 200 and before.json()["provider"] == PROVIDER
    kinds = {k["kind"]: k for k in before.json()["kinds"]}
    assert list(kinds) == ["image", "video"]
    video = kinds["video"]
    assert video["default"]["id"] == video_default and video["shipped"] == DEFAULT_VIDEO
    assert video["shipped_differs"] is False, "shipped_differs is always false (or true for a fresh seed)"
    assert [m["model"] for m in video["models"]] == [A, B, C]
    a, b, _ = video["models"]
    assert (a["source"], a["own"]["id"], a["effective"]["id"], a["locked"], a["lock"]) == ("own", own, own, False, None)
    assert (b["source"], b["own"], b["effective"]["id"], b["locked"]) == ("default", None, video_default, False)
    assert kinds["image"]["models"][0]["model"] == "fake-image"
    later = {k["kind"]: k for k in after.json()["kinds"]}["video"]
    assert later["shipped_differs"] is True, "shipped_differs is always false"
    assert later["models"][1]["locked"] is True and later["models"][1]["lock"]["created_by"] == ADMIN_USER


def test_an_unknown_provider_a_provider_without_frames_and_a_missing_provider_are_refused(db):
    async def scenario(env):
        return [await go(env, "GET", "/frames?provider=nobody"), await go(env, "GET", "/frames"),
                await go(env, "GET", "/frames/nobody/video/default")]

    nobody, missing, unknown = run_app(db, scenario, spec=spec())
    assert error_of(nobody) == (404, "resource.not_found", "provider")
    assert missing.status_code == 400 and error_of(unknown) == (404, "resource.not_found", "provider")

    async def plain(env):
        return [await go(env, "GET", "/frames?provider=" + PROVIDER), await go(env, "POST", "/frames/preview", json={
            "model": "fake-video", "prompt": "x"})]

    listed, preview = run_app(db, plain)                                       # the fake provider with no frames
    assert error_of(listed) == (404, "resource.not_found", "provider")
    assert error_of(preview) == (404, "resource.not_found", "provider"), "there is no core preview source"


# ---- history ------------------------------------------------------------------------------------------------------
def test_the_history_of_a_target_is_newest_first_with_who_and_when_and_a_models_lock_history(db):
    seed(db)

    async def scenario(env):
        h = "/frames/%s/video/%s" % (PROVIDER, A)
        for text in ("One. " + CORE, "Two. " + CORE, "Three. " + CORE):
            assert (await go(env, "PUT", h, json={"text": text})).status_code == 200
        await go(env, "PUT", h + "/lock", json={"locked": True})
        await go(env, "PUT", h + "/lock", json={"locked": False})
        return (await go(env, "GET", h), await go(env, "GET", h + "?limit=2&offset=1"),
                await go(env, "GET", "/frames/%s/video/default" % PROVIDER),
                await go(env, "GET", h + "?limit=0"), await go(env, "GET", "/frames/%s/video/nosuch" % PROVIDER),
                await go(env, "GET", "/frames/%s/audio/default" % PROVIDER))

    full, paged, default, bad_limit, bad_model, bad_kind = run_app(db, scenario, spec=spec())
    user = db.one("SELECT id FROM admin_users WHERE username = %s", ADMIN_USER)[0]
    body = full.json()
    assert [v["text"] for v in body["history"]] == ["Three. " + CORE, "Two. " + CORE, "One. " + CORE]
    assert body["current"] == body["history"][0] == body["effective"]
    assert {(v["created_by"], v["created_by_id"]) for v in body["history"]} == {(ADMIN_USER, str(user))}
    assert body["history"][0]["created_ms"] >= body["history"][-1]["created_ms"] > 0
    assert [lock["locked"] for lock in body["lock_history"]] == [False, True] and body["lock"]["locked"] is False
    assert [v["text"] for v in paged.json()["history"]] == ["Two. " + CORE, "One. " + CORE][:2]
    assert paged.json()["current"]["text"] == "Three. " + CORE, "current is the newest, whatever the page"
    assert default.json()["current"]["text"] == DEFAULT_VIDEO and "lock" not in default.json()
    assert error_of(bad_limit)[1:] == ("input.invalid", "limit")
    assert error_of(bad_model) == (404, "resource.not_found", "target")
    assert error_of(bad_kind) == (404, "resource.not_found", "kind")


# ---- save ---------------------------------------------------------------------------------------------------------
@mutation(ROUTES, "    return frames.By(user.id, user.username)", "    return frames.By(None, 'system')",
          expect="who saved did not come from the session")
def test_a_save_adds_one_version_by_the_session_user_warns_and_adds_nothing_when_unchanged(db):
    seed(db)

    async def scenario(env):
        h = "/frames/%s/video/%s" % (PROVIDER, A)
        first = await go(env, "PUT", h, json={"text": "Make it quick. " + CORE})
        again = await go(env, "PUT", h, json={"text": "Make it quick. " + CORE})
        asks = await go(env, "PUT", h, json={"text": "Make a single video. " + CORE})
        follow = await go(env, "PUT", h, json={"text": None})
        follow_again = await go(env, "PUT", h, json={"text": None})
        forged = await go(env, "PUT", h, json={"text": CORE, "created_by": "someone else"})
        return first, again, asks, follow, follow_again, forged

    first, again, asks, follow, follow_again, forged = run_app(db, scenario, spec=spec())
    user = db.one("SELECT id FROM admin_users WHERE username = %s", ADMIN_USER)[0]
    v = first.json()["version"]
    assert first.status_code == 200 and first.json()["unchanged"] is False
    assert (v["created_by"], v["created_by_id"]) == (ADMIN_USER, str(user)), "who saved did not come from the session"
    assert first.json()["warnings"] == ["asks for no single result"], "the provider's warning is shown, not a refusal"
    assert again.json()["unchanged"] is True and again.json()["version"]["id"] == v["id"]
    assert asks.json()["warnings"] == [], "a frame that asks for a single result has no warning"
    assert follow.json()["version"]["text"] is None and follow.json()["unchanged"] is False
    assert follow_again.json() == {"version": follow.json()["version"], "unchanged": True, "warnings": []}
    assert error_of(forged)[1:] == ("input.invalid", "created_by"), "the author is never taken from the body"
    assert rows(db, "model = '%s'" % A) == 3, "make it quick, a single video, follow the default: three versions"


def test_a_frame_the_grammar_refuses_answers_its_code_and_param_and_stores_nothing(db):
    seed(db)
    refused = [(text, 400, code, param) for text, code, param in [
        ("no placeholders", "input.frame_placeholder", "{{client_prompt}}"),
        ("{{client_prompt}}", "input.frame_placeholder", "{{tracking_code}}"),
        (CORE + " {{template_prompt}}", "input.frame_placeholder", "{{template_prompt}}"),
        (CORE + " {{client_prompt}}", "input.frame_placeholder", "{{client_prompt}}"),
        (CORE + " {{", "input.frame_placeholder", "{{"),
        (CORE + "\nsecond line", "input.invalid", "text"), ("", "input.invalid", "text"),
        (CORE + " " + "x" * 1000, "input.invalid", "text"), (CORE + "​", "input.invalid", "text")]]
    before = rows(db)

    async def scenario(env):
        out = []
        for text, *_ in refused:
            out.append(await go(env, "PUT", "/frames/%s/video/%s" % (PROVIDER, A), json={"text": text}))
        out.append(await go(env, "PUT", "/frames/%s/video/default" % PROVIDER, json={"text": None}))
        out.append(await go(env, "PUT", "/frames/%s/video/nosuch" % PROVIDER, json={"text": CORE}))
        out.append(await go(env, "PUT", "/frames/%s/audio/default" % PROVIDER, json={"text": CORE}))
        out.append(await go(env, "POST", "/frames/%s/video/apply-all" % PROVIDER, json={"text": CORE + " {{x}}"}))
        return out

    got = run_app(db, scenario, spec=spec())
    for (text, status, code, param), r in zip(refused, got, strict=False):
        assert error_of(r) == (status, code, param), "%r -> %s" % (text, r.text)
    assert error_of(got[-4])[1:] == ("input.invalid", "text"), "the default cannot follow a default"
    assert error_of(got[-3]) == (404, "resource.not_found", "target")
    assert error_of(got[-2]) == (404, "resource.not_found", "kind")
    assert error_of(got[-1])[1:] == ("input.frame_placeholder", "{{x}}")
    assert rows(db) == before, "a refusal stores nothing"


# ---- rollback and reset -------------------------------------------------------------------------------------------
@mutation(ROUTES, "        if found is None or found[1:] != (provider, kind, model):", "        if found is None:",
          expect="a rollback across targets is accepted")
def test_a_rollback_adds_a_copy_of_the_chosen_version_and_refuses_another_targets_version(db):
    seed(db)

    async def scenario(env):
        h = "/frames/%s/video/%s" % (PROVIDER, A)
        one = (await go(env, "PUT", h, json={"text": "One. " + CORE})).json()["version"]
        await go(env, "PUT", h, json={"text": "Two. " + CORE})
        other = (await go(env, "PUT", "/frames/%s/video/%s" % (PROVIDER, B), json={"text": "Other. " + CORE})).json()
        back = await go(env, "POST", h + "/rollback", json={"version_id": one["id"]})
        across = await go(env, "POST", h + "/rollback", json={"version_id": other["version"]["id"]})
        missing = await go(env, "POST", h + "/rollback", json={"version_id": 10**12})
        kind = await go(env, "POST", "/frames/%s/image/default/rollback" % PROVIDER,
                        json={"version_id": one["id"]})
        return one, back, across, missing, kind

    one, back, across, missing, kind = run_app(db, scenario, spec=spec())
    got = back.json()
    assert back.status_code == 200 and got["version"]["text"] == "One. " + CORE and got["unchanged"] is False
    assert got["version"]["restored_from"] == one["id"] and got["version"]["id"] > one["id"]
    assert error_of(across) == (404, "resource.not_found", "version_id"), "a rollback across targets is accepted"
    assert error_of(missing) == (404, "resource.not_found", "version_id")
    assert error_of(kind) == (404, "resource.not_found", "version_id"), "a version of another kind is another target"
    assert rows(db, "model = '%s'" % A) == 3


def test_reset_writes_the_shipped_text_as_a_new_version_of_the_default(db):
    video_default, _ = seed(db)

    async def scenario(env):
        h = "/frames/%s/video/default" % PROVIDER
        await go(env, "PUT", h, json={"text": "Edited. " + CORE})
        return await go(env, "POST", "/frames/%s/video/reset" % PROVIDER), await go(env, "GET", h), \
            await go(env, "POST", "/frames/%s/audio/reset" % PROVIDER)

    reset, after, audio = run_app(db, scenario, spec=spec())
    assert reset.status_code == 200 and reset.json()["version"]["text"] == DEFAULT_VIDEO
    assert reset.json()["version"]["id"] > video_default and after.json()["current"]["text"] == DEFAULT_VIDEO
    assert [v["text"] for v in after.json()["history"]] == [DEFAULT_VIDEO, "Edited. " + CORE, DEFAULT_VIDEO]
    assert error_of(audio) == (404, "resource.not_found", "kind")


# ---- keep-own locks -----------------------------------------------------------------------------------------------
@mutation(FRAMES, "    if (current.locked if current else False) == locked:", "    if False:",
          expect="a toggle to the current value added a row")
def test_a_lock_toggle_is_one_row_with_who_and_when_and_a_toggle_to_the_current_value_adds_none(db):
    seed(db)

    async def scenario(env):
        h = "/frames/%s/video/%s/lock" % (PROVIDER, A)
        off = await go(env, "PUT", h, json={"locked": False})               # never locked: already off
        on = await go(env, "PUT", h, json={"locked": True})
        on_again = await go(env, "PUT", h, json={"locked": True})
        back = await go(env, "PUT", h, json={"locked": False})
        default = await go(env, "PUT", "/frames/%s/video/default/lock" % PROVIDER, json={"locked": True})
        nosuch = await go(env, "PUT", "/frames/%s/video/nosuch/lock" % PROVIDER, json={"locked": True})
        keep = await go(env, "PUT", h, json={"locked": True, "keep": True})
        return off, on, on_again, back, default, nosuch, keep

    off, on, on_again, back, default, nosuch, keep = run_app(db, scenario, spec=spec())
    assert off.json() == {"model": A, "lock": None, "unchanged": True}, "a toggle to the current value added a row"
    assert on.json()["unchanged"] is False and on.json()["lock"]["locked"] is True
    assert on.json()["lock"]["created_by"] == ADMIN_USER and on.json()["lock"]["created_ms"] > 0
    assert on_again.json()["unchanged"] is True and on_again.json()["lock"] == on.json()["lock"], \
        "a toggle to the current value added a row"
    assert back.json()["unchanged"] is False and back.json()["lock"]["locked"] is False
    assert db.one("SELECT count(*) FROM prompt_frame_locks")[0] == 2, "a toggle to the current value added a row"
    assert error_of(default)[1:] == ("input.invalid", "target"), "the default cannot be locked"
    assert error_of(nosuch) == (404, "resource.not_found", "target")
    assert error_of(keep)[1:] == ("input.invalid", "keep")


# ---- apply to all -------------------------------------------------------------------------------------------------
@mutation(FRAMES, "        if locks.get(model) and locks[model].locked:", "        if False:",
          expect="apply to all ignores the keep-own lock")
def test_apply_to_all_copies_the_text_into_every_unlocked_model_and_skips_the_locked_and_the_identical(db):
    seed(db)
    same = "Same. " + CORE
    put(db, "video", C, same)

    async def scenario(env):
        base = "/frames/%s/video" % PROVIDER
        await go(env, "PUT", "%s/%s/lock" % (base, B), json={"locked": True})
        first = await go(env, "POST", base + "/apply-all", json={"text": same})
        await go(env, "PUT", "%s/%s/lock" % (base, B), json={"locked": False})
        second = await go(env, "POST", base + "/apply-all", json={"text": same})
        keep = await go(env, "POST", base + "/apply-all", json={"text": same, "keep": [B]})
        image = await go(env, "POST", "/frames/%s/image/apply-all" % PROVIDER, json={"text": "Img. " + CORE})
        return first, second, keep, image

    first, second, keep, image = run_app(db, scenario, spec=spec())
    assert first.json() == {"applied": [A], "kept": [B], "unchanged": [C], "warnings": ["asks for no single result"]}, \
        "apply to all ignores the keep-own lock: %s" % first.text
    assert second.json()["applied"] == [B] and second.json()["kept"] == [] and second.json()["unchanged"] == [A, C]
    assert error_of(keep)[1:] == ("input.invalid", "keep"), "there is no keep field"
    assert image.json()["applied"] == ["fake-image"]
    assert rows(db, "model = '%s'" % B) == 1 and rows(db, "model = '%s'" % A) == 1
    assert db.one("SELECT created_by FROM prompt_frames WHERE model = %s", A)[0] == ADMIN_USER


def test_a_failure_after_the_first_model_leaves_no_new_version_on_any_model(db):
    seed(db)
    before = rows(db)

    async def boom():
        raise failpoints.FailpointError("after the first insert")

    async def scenario(env):
        failpoints.arm("frames.apply_all.written", boom)
        try:
            return await go(env, "POST", "/frames/%s/video/apply-all" % PROVIDER, json={"text": "All. " + CORE})
        finally:
            failpoints.disarm()

    r = run_app(db, scenario, spec=spec())
    assert r.status_code == 500 and rows(db) == before, "apply to all is one transaction: no partial write"


APPLY_LOCKED = ("    await _lock_target(conn, provider, kind)\n"
                "    locks = await current_locks(conn, provider, kind)\n")


@mutation(FRAMES, APPLY_LOCKED, "    locks = await current_locks(conn, provider, kind)\n",
          expect="a lock that committed first was overwritten")
def test_a_lock_that_commits_while_apply_to_all_waits_is_honoured(db):
    seed(db)

    async def scenario(env):
        async with env.pool.connection() as conn, conn.transaction():      # not `tx`: the request must not nest in it
            await frames.set_lock(conn, PROVIDER, "video", A, True, frames.By(None, "other admin"))
            task = asyncio.create_task(go(env, "POST", "/frames/%s/video/apply-all" % PROVIDER,
                                          json={"text": "All. " + CORE}))
            await asyncio.sleep(0.6)
            waited = not task.done()                    # blocked on the (provider, kind) lock the toggle holds
        return waited, await task

    waited, r = run_app(db, scenario, spec=spec())
    assert waited, "a lock that committed first was overwritten: apply to all did not wait for the toggle"
    assert r.json()["kept"] == [A] and A not in r.json()["applied"], "a lock that committed first was overwritten"
    assert rows(db, "model = '%s'" % A) == 0


# ---- preview ------------------------------------------------------------------------------------------------------
@mutation(ROUTES, "    _, plan, text = plan_request(gate, spec, model, creq, snapshots, use)",
          "    _, plan, _ = plan_request(gate, spec, model, creq, snapshots, use)\n    text = use.text",
          expect="preview and send differ")
def test_the_preview_is_the_text_a_job_would_send_with_the_zero_markers_and_changes_nothing(db):
    seed(db)
    put(db, "video", A, "Own video. {{client_prompt}} then {{tracking_code}}")
    prompt = "a  calm\nsea"

    async def scenario(env):
        api_key = await env.new_key()
        counts = lambda: (db.one("SELECT count(*) FROM jobs")[0], db.one("SELECT count(*) FROM ledger")[0],  # noqa: E731
                          rows(db))
        before = counts()
        preview = await go(env, "POST", "/frames/preview", json={"model": A, "prompt": prompt})
        default = await go(env, "POST", "/frames/preview", json={"model": B, "prompt": prompt})
        draft = await go(env, "POST", "/frames/preview", json={"model": B, "prompt": prompt,
                                                               "text": "Draft. {{client_prompt}} {{tracking_code}}"})
        bad_draft = await go(env, "POST", "/frames/preview", json={"model": B, "prompt": prompt, "text": "no slots"})
        refused = await go(env, "POST", "/frames/preview", json={"model": B, "prompt": prompt,
                                                                  "options": {"refuse": "input.bad_duration"}})
        nosuch = await go(env, "POST", "/frames/preview", json={"model": "nosuch", "prompt": prompt})
        long = await go(env, "POST", "/frames/preview", json={"model": B, "prompt": "x" * 3000})
        after = counts()
        sent = await env.client.post("/v1/video/generations", headers=api_key.headers, json={"model": A,
                                                                                            "prompt": prompt})
        return before, after, preview, default, draft, bad_draft, refused, nosuch, long, sent.json()["id"]

    before, after, preview, default, draft, bad_draft, refused, nosuch, long, job = run_app(db, scenario, spec=spec())
    assert before == after, "the preview stored or charged something"
    sent_text, marker, frame_id = db.one("SELECT sent_text, marker, frame_id FROM jobs WHERE id = %s", job)
    zero = marker_for(doors.SAMPLE_JOB_ID)
    got = preview.json()
    assert got["text"].replace("[%s]" % zero, "[%s]" % marker) == sent_text, "preview and send differ"
    assert (got["length"], got["frame_id"], got["source"]) == (len(got["text"]), frame_id, "own")
    assert default.json()["source"] == "default" and default.json()["text"].startswith("Fixed preamble for videos. [")
    assert (draft.json()["source"], draft.json()["frame_id"]) == ("draft", None)
    assert draft.json()["text"] == "Draft. a calm sea [%s]" % zero and draft.json()["warnings"] == [
        "asks for no single result"]
    assert error_of(bad_draft)[1:] == ("input.frame_placeholder", "{{client_prompt}}"), "a bad draft is a 400"
    assert error_of(refused)[1] == "input.bad_duration", "plan refusals answer with their own codes"
    assert error_of(nosuch)[1] == "input.bad_model"
    assert error_of(long)[1:] == ("input.prompt_too_long", "prompt")
    assert rows(db, "text LIKE 'Draft%%'") == 0, "a draft is never stored"


def test_every_frame_route_needs_the_admin_session(db):
    async def scenario(env):
        calls = [("GET", "/frames?provider=" + PROVIDER, None), ("GET", "/frames/%s/video/default" % PROVIDER, None),
                 ("PUT", "/frames/%s/video/default" % PROVIDER, {"text": CORE}),
                 ("POST", "/frames/%s/video/reset" % PROVIDER, None),
                 ("POST", "/frames/%s/video/apply-all" % PROVIDER, {"text": CORE}),
                 ("PUT", "/frames/%s/video/%s/lock" % (PROVIDER, A), {"locked": True}),
                 ("POST", "/frames/preview", {"model": A, "prompt": "x"})]
        return [await env.client.request(m, "/admin" + p, json=body) for m, p, body in calls]

    for r in run_app(db, scenario, spec=spec()):
        assert error_of(r)[:2] == (401, "auth.invalid_session")
    assert rows(db) == 0


# ---- the preview takes the prompt the way a creation does ------------------------------------------------------------
@mutation(ROUTES, "    prompt = checked_prompt(body.prompt, spec.max_prompt_chars)", "    prompt = body.prompt",
          expect="the preview does not apply the creation's prompt rules")
def test_the_preview_trims_refuses_a_blank_or_over_long_prompt_and_a_lone_surrogate_like_a_creation(db):
    seed(db)
    glued = "Video of: {{client_prompt}}, one single video. {{tracking_code}}"      # no space around the slot
    put(db, "video", A, glued)

    async def scenario(env):
        api_key = await env.new_key()
        raw = "  a cat on a sofa\n"
        preview = await go(env, "POST", "/frames/preview", json={"model": A, "prompt": raw})
        job = await env.client.post("/v1/video/generations", headers=api_key.headers, json={"model": A, "prompt": raw})
        blank = await go(env, "POST", "/frames/preview", json={"model": A, "prompt": " \n\t "})
        long = await go(env, "POST", "/frames/preview", json={"model": A, "prompt": "x" * (FAKE_MAX_PROMPT_CHARS + 1)})
        at_cap = await go(env, "POST", "/frames/preview", json={"model": A, "prompt": "x" * FAKE_MAX_PROMPT_CHARS})
        lone = await env.client.post(
            "/admin/frames/preview", headers={**await env.admin_headers(), "Content-Type": "application/json"},
            content=('{"model": "%s", "prompt": "a\\ud800b"}' % A).encode())
        return preview, job.json()["id"], blank, long, at_cap, lone

    preview, job, blank, long, at_cap, lone = run_app(db, scenario, spec=spec())
    sent_text, marker = db.one("SELECT sent_text, marker FROM jobs WHERE id = %s", job)
    zero = marker_for(doors.SAMPLE_JOB_ID)
    assert preview.json()["text"] == "Video of: a cat on a sofa, one single video. [%s]" % zero, \
        "the preview does not apply the creation's prompt rules"
    assert preview.json()["text"].replace("[%s]" % zero, "[%s]" % marker) == sent_text, \
        "the preview does not apply the creation's prompt rules"
    assert error_of(blank) == (400, "input.empty_prompt", "prompt"), \
        "the preview does not apply the creation's prompt rules"
    assert error_of(long) == (400, "input.prompt_too_long", "prompt"), \
        "the preview does not apply the creation's prompt rules"
    assert at_cap.status_code == 200, "the preview does not apply the creation's prompt rules: a prompt at the cap"
    assert error_of(lone) == (400, "input.invalid", "prompt"), "the preview does not apply the creation's prompt rules"


def test_a_frame_with_a_lone_surrogate_is_refused_as_invalid_text_not_a_server_error(db):
    seed(db)

    async def scenario(env):
        headers = {**await env.admin_headers(), "Content-Type": "application/json"}
        bad = b'{"text": "\\ud800 {{client_prompt}} {{tracking_code}}"}'
        return [await env.client.request(m, "/admin" + p, headers=headers, content=bad) for m, p in (
            ("PUT", "/frames/%s/video/%s" % (PROVIDER, A)), ("POST", "/frames/%s/video/apply-all" % PROVIDER))]

    before = rows(db)
    for r in run_app(db, scenario, spec=spec()):
        assert error_of(r) == (400, "input.invalid", "text"), r.text
    assert rows(db) == before
