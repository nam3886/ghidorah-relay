"""The create doors (invariant I3): every door goes through `create_job_for`, every
input check happens before the charge, and the status of a job is the one shape of the contract."""

import base64
import dataclasses
import uuid

import httpx
import psycopg
from apikit import request_logs, run_app
from doorkit import (
    IMAGE,
    PNG,
    VIDEO,
    StrictFake,
    charges,
    error_of,
    finish_done,
    finish_failed,
    jobs,
    png_uri,
    used,
)

from relay_core import errors
from relay_core.api import doors
from relay_core.api import facade_common as fc
from relay_core.providers import ProviderRegistry
from relay_core.testing.fake_provider import FAKE_MAX_PROMPT_CHARS, FakeSpec
from relay_core.testing.mutation import mutation

DOORS = "packages/relay-core/src/relay_core/api/doors.py"
NATIVE = "packages/relay-core/src/relay_core/api/routes_native.py"
VIDEO_PATH, IMAGE_PATH, CHAT_PATH = "/v1/video/generations", "/v1/images/generations", "/v1/chat/completions"
GEMINI_PATH = "/v1beta/models/fake-video:generateContent"
ERROR_KEYS = {"code", "message", "type", "param"}
STATUS_KEYS = {"id", "status", "stage", "result_urls", "images", "error", "error_code", "refunded", "points", "model",
               "duration", "ratio", "n", "style", "title", "warnings", "queue_position", "created_at", "finished_at",
               "progress", "render_started_at", "estimated_finish_at", "source_job_id"}
POINTS_OF_FAILED = "THEN (array_agg(l.amount ORDER BY l.id DESC) FILTER (WHERE l.entry = 'charge'))[1] "
POINTS_NET = "ELSE sum(CASE l.entry WHEN 'charge' THEN l.amount ELSE -l.amount END) END "
POINTS_READ = "FROM ledger l WHERE l.job_id = j.id AND l.scope = '*' AND l.entry IN ('charge', 'refund')), 0) "
POINTS_ALL_SCOPES = "FROM ledger l WHERE l.job_id = j.id AND l.entry IN ('charge', 'refund')), 0) "
REFUNDED_READ = "EXISTS (SELECT 1 FROM ledger l WHERE l.job_id = j.id AND l.scope = '*' AND l.entry = 'refund'), "
REFUNDED_ALL_SCOPES = "EXISTS (SELECT 1 FROM ledger l WHERE l.job_id = j.id AND l.entry = 'refund'), "


def video(**kw) -> dict:
    return {"model": VIDEO, "prompt": "a cat", **kw}


def chat(content="a cat", model=VIDEO, **kw) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": content}], **kw}


def gemini(text="a cat", **kw) -> dict:
    return {"contents": [{"role": "user", "parts": [{"text": text}]}], **kw}


def nothing_made(db, key) -> str:
    return "jobs %s, charges %d" % (jobs(db, key.id), charges(db, key_id=key.id))


async def quick_holds(monkeypatch):
    """A facade request waits for its job; these tests have no worker, so the wait is made short."""
    monkeypatch.setattr(fc, "HOLD_LIMIT_S", 0.3)
    monkeypatch.setattr(fc, "POLL_S", 0.05)


# ---- one gate, charged once (I3) ----------------------------------------------------------------------------------
@mutation(DOORS, "idem_key=req.idem_key, idem_body_hash=idem_hash,", "idem_key=None, idem_body_hash=None,",
          expect="I3")
def test_i3_every_door_creates_and_charges_exactly_once_for_one_idempotency_key(db, monkeypatch):
    doors_under_test = [
        ("native video", VIDEO_PATH, video(), "video", lambda r: r["id"]),
        ("native image", IMAGE_PATH, {"model": IMAGE, "prompt": "a cat"}, "image", lambda r: r["id"]),
        ("openai", CHAT_PATH, chat(), "video", lambda r: r["job_id"]),
        ("openai stream", CHAT_PATH, chat(stream=True), "video", None),
        ("gemini", GEMINI_PATH, gemini(), "video", lambda r: r["responseId"]),
        ("gemini stream", "/models/fake-video:streamGenerateContent", gemini(), "video", None),
    ]

    async def scenario(env):
        await quick_holds(monkeypatch)
        for name, path, body, kind, job_of in doors_under_test:
            key = await env.new_key()
            first = await env.client.post(path, json=body, headers={**key.headers, "Idempotency-Key": "k-1"})
            again = await env.client.post(path, json=body, headers={**key.headers, "Idempotency-Key": "k-1"})
            assert first.status_code == again.status_code == 200, (name, first.text, again.text)
            rows = jobs(db, key.id)
            assert len(rows) == 1, "I3: door %s made %d jobs for one idempotency key" % (name, len(rows))
            assert charges(db, key_id=key.id) == 1, "I3: door %s charged %d times" % (name, charges(db, key_id=key.id))
            price = 10 if kind == "video" else 1
            assert used(db, key.id) == used(db, key.id, "fake") == price, "I3: door %s moved the wallet by %d" % (
                name, used(db, key.id))
            if job_of is not None:
                assert job_of(first.json()) == job_of(again.json()) == str(rows[0][0]), name
        # the request log of a create names its job and provider
        row = [r for r in request_logs(db) if r["route"].endswith("/video/generations")][0]
        assert row["provider"] == "fake" and row["job_id"] is not None, "the log row must carry job_id: %s" % row

    run_app(db, scenario)


@mutation(NATIVE, "request.state.job_id, request.state.provider = made.job_id, made.provider", "pass",
          expect="job_id")
def test_a_create_marks_its_job_and_provider_for_the_request_log(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, json=video(), headers={**key.headers, "Idempotency-Key": "k"})
        assert r.status_code == 200, r.text
        (row,) = [x for x in request_logs(db) if x["route"] == "POST " + VIDEO_PATH]
        assert str(row["job_id"]) == r.json()["id"] and row["provider"] == "fake", "job_id missing: %s" % row

    run_app(db, scenario)


def test_native_create_answers_id_status_position_and_warnings_only(db):
    async def scenario(env):
        key = await env.new_key()
        a = await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)
        b = await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)
        assert set(a.json()) == {"id", "status", "queue_position", "warnings"}, a.json()
        assert (a.json()["status"], a.json()["queue_position"], a.json()["warnings"]) == ("queued", 1, [])
        assert b.json()["queue_position"] == 2

    run_app(db, scenario)


# ---- idempotency --------------------------------------------------------------------------------------------------
@mutation(DOORS, '"prompt": prompt, "duration"', '"prompt": "", "duration"', expect="different prompt")
@mutation(DOORS, '"images": [await asyncio.to_thread(_sha256, r.ref.encode()) for r in req.images]})',
          '"images": []})', expect="different image")
def test_the_same_key_and_body_is_one_job_and_another_body_is_409(db):
    async def scenario(env):
        key = await env.new_key()
        h = {**key.headers, "Idempotency-Key": "k-1"}
        first = await env.client.post(VIDEO_PATH, json=video(images=[png_uri()]), headers=h)
        same = await env.client.post(VIDEO_PATH, json=video(images=[png_uri()]), headers=h)
        assert first.json()["id"] == same.json()["id"]
        conflict = (409, "input.idempotency_conflict")
        other_prompt = await env.client.post(VIDEO_PATH, json=video(prompt="a dog", images=[png_uri()]), headers=h)
        assert error_of(other_prompt)[:2] == conflict, "a different prompt must be 409"
        other_image = await env.client.post(VIDEO_PATH, json=video(images=[png_uri(PNG + b"x")]), headers=h)
        assert error_of(other_image)[:2] == conflict, "a different image must be 409"
        other_door = await env.client.post(CHAT_PATH, json=chat(), headers=h)
        assert error_of(other_door)[:2] == conflict, "another door is another request"
        assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1
        # keys are per API key: another key may reuse the string
        other = await env.new_key()
        assert (await env.client.post(VIDEO_PATH, json=video(), headers={**other.headers, "Idempotency-Key": "k-1"})
                ).status_code == 200

    run_app(db, scenario)


@mutation(DOORS, "    if req.idem_key is not None:                 # first,", "    if False:                 # first,",
          expect="replay refused")
def test_a_replay_is_answered_before_the_plan_the_allow_list_and_any_upload(db, monkeypatch):
    async def scenario(env):
        strict = StrictFake()
        env.add_provider(strict)
        key = await env.new_key()
        h = {**key.headers, "Idempotency-Key": "k-1"}
        body = video(model="strict-video", images=[png_uri()])
        first = await env.client.post(VIDEO_PATH, json=body, headers=h)
        assert first.status_code == 200, first.text
        planned, uploads = len(strict.seen), []
        real_put = env.store.put_bytes

        async def counting(*a, **kw):
            uploads.append(a[0])
            return await real_put(*a, **kw)

        monkeypatch.setattr(env.store, "put_bytes", counting)
        db.run("UPDATE api_keys SET models = %s WHERE id = %s", ["fake-video"], key.id)   # the model left the list
        same = await env.client.post(VIDEO_PATH, json=body, headers=h)
        assert same.status_code == 200, "replay refused after the model left the allow-list: %s" % same.text
        assert same.json()["id"] == first.json()["id"]
        assert uploads == [] and len(strict.seen) == planned, "a replay must not plan or upload: %s" % uploads
        other = await env.client.post(VIDEO_PATH, json=video(model="strict-video", prompt="a dog"), headers=h)
        assert error_of(other)[:2] == (409, "input.idempotency_conflict"), "a different body is 409: %s" % other.text
        assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1

    run_app(db, scenario)


@mutation(DOORS, "(IdempotencyConflict, TooManyQueued, billing.BillingError) as refused",
          "(TooManyQueued, billing.BillingError) as refused", expect="a lost race")
def test_a_request_that_loses_the_race_between_the_lookup_and_the_insert_still_replays_or_gets_409(db, monkeypatch):
    async def scenario(env):
        key = await env.new_key()
        h = {**key.headers, "Idempotency-Key": "k-1"}
        first = await env.client.post(VIDEO_PATH, json=video(), headers=h)

        async def missed(*args):
            return None                      # the lookup saw nothing: the winner commits just after it

        monkeypatch.setattr(doors, "_replay_of", missed)
        same = await env.client.post(VIDEO_PATH, json=video(), headers=h)
        assert same.status_code == 200 and same.json()["id"] == first.json()["id"], same.text
        other = await env.client.post(VIDEO_PATH, json=video(prompt="a dog"), headers=h)
        assert error_of(other)[:2] == (409, "input.idempotency_conflict"), "a lost race: another body is 409"
        assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1

    run_app(db, scenario)


def test_an_idempotency_key_that_is_empty_too_long_or_not_printable_is_400(db):
    async def scenario(env):
        key = await env.new_key()
        for bad in ("x" * 201, "a\tb"):
            r = await env.client.post(VIDEO_PATH, json=video(), headers={**key.headers, "Idempotency-Key": bad})
            assert error_of(r) == (400, "input.invalid", "Idempotency-Key"), r.text
        assert not jobs(db, key.id)

    run_app(db, scenario)


# ---- the cap and the wallet ---------------------------------------------------------------------------------
@mutation(DOORS, "(IdempotencyConflict, TooManyQueued, billing.BillingError) as refused",
          "(IdempotencyConflict, billing.BillingError) as refused", expect="too_many_queued")
def test_max_unfinished_null_uses_the_default_and_zero_is_unlimited(db):
    async def create_n(env, key, n):
        return [(await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)) for _ in range(n)]

    async def scenario(env):
        await env.settings.set("max_unfinished_default", 2)
        null_key, zero_key, three_key = await env.new_key(), await env.new_key(), await env.new_key()
        db.run("UPDATE api_keys SET max_unfinished = 0 WHERE id = %s", zero_key.id)
        db.run("UPDATE api_keys SET max_unfinished = 3 WHERE id = %s", three_key.id)
        rs = await create_n(env, null_key, 3)
        assert [r.status_code for r in rs[:2]] == [200, 200]
        assert error_of(rs[2])[:2] == (429, "quota.too_many_queued"), "NULL must use the default of 2: %s" % rs[2].text
        assert len(jobs(db, null_key.id)) == 2 and charges(db, key_id=null_key.id) == 2, "a refusal charges nothing"
        assert all(r.status_code == 200 for r in await create_n(env, zero_key, 5)), "0 means unlimited"
        rs = await create_n(env, three_key, 4)
        assert [r.status_code for r in rs] == [200, 200, 200, 429], "the key's own value wins over the default"

    run_app(db, scenario)


def test_a_default_of_zero_is_unlimited_too(db):
    async def scenario(env):
        await env.settings.set("max_unfinished_default", 0)
        key = await env.new_key()
        for _ in range(4):
            assert (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).status_code == 200

    run_app(db, scenario)


@mutation(DOORS, "(IdempotencyConflict, TooManyQueued, billing.BillingError) as refused",
          "(IdempotencyConflict, TooManyQueued) as refused", expect="key_exceeded")
def test_a_closed_wallet_is_429_key_exceeded_and_makes_no_job(db):
    async def scenario(env):
        key = await env.new_key(unlimited=False)       # every wallet closed
        r = await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)
        assert error_of(r)[:2] == (429, "quota.key_exceeded"), r.text
        assert not jobs(db, key.id)

    run_app(db, scenario)


# ---- strict fields ------------------------------------------------------------------------------------------
@mutation(DOORS, 'model_config = ConfigDict(extra="forbid")', 'model_config = ConfigDict(extra="ignore")',
          expect="unknown field")
def test_an_unknown_field_is_400_input_invalid_naming_it_on_every_door(db):
    cases = [
        (VIDEO_PATH, video(resolution="720p"), "resolution"),
        (VIDEO_PATH, video(style="noir"), "style"),               # a field of the other native door
        (IMAGE_PATH, {"model": IMAGE, "prompt": "a", "duration": 5}, "duration"),
        (VIDEO_PATH, video(poll_deadline_s=5), "poll_deadline_s"),
        (CHAT_PATH, chat(temperature=0.7), "temperature"),
        (CHAT_PATH, chat(stream_options={"include_usage": True}), "stream_options"),
        (CHAT_PATH, {"model": VIDEO, "messages": [{"role": "user", "content": "a", "name": "n"}]},
         "messages.0.name"),
        (GEMINI_PATH, gemini(generationConfig={"temperature": 0.5}), "generationConfig"),
        (GEMINI_PATH, gemini(safetySettings=[]), "safetySettings"),
        ("/models/fake-video:streamGenerateContent", gemini(tools=[]), "tools"),
        (GEMINI_PATH, {"contents": [{"role": "user", "parts": [{"text": "a", "functionCall": {}}]}]},
         "contents.0.parts.0.functionCall"),
    ]

    async def scenario(env):
        key = await env.new_key()
        for path, body, param in cases:
            r = await env.client.post(path, json=body, headers=key.headers)
            assert error_of(r) == (400, "input.invalid", param), "unknown field %s: %s" % (param, r.text)
            shape = {"code", "message", "status", "details"} if path.startswith(("/v1beta", "/models")) else ERROR_KEYS
            assert set(r.json()["error"]) == shape, r.text
        assert not jobs(db, key.id) and charges(db, key_id=key.id) == 0, nothing_made(db, key)

    run_app(db, scenario)


def test_a_body_that_is_not_a_json_object_is_400_input_invalid_with_no_field(db):
    async def scenario(env):
        key = await env.new_key()
        for path in (VIDEO_PATH, CHAT_PATH, GEMINI_PATH):
            for content in (b"[1]", b"not json", b""):
                r = await env.client.post(path, content=content, headers=key.headers)
                assert error_of(r) == (400, "input.invalid", None), (path, content, r.text)

    run_app(db, scenario)


def test_a_wrong_type_names_its_own_code_and_a_missing_prompt_is_input_invalid(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, json=video(duration="five"), headers=key.headers)
        assert error_of(r) == (400, "input.bad_duration", "duration"), r.text
        r = await env.client.post(VIDEO_PATH, json={"model": VIDEO}, headers=key.headers)
        assert error_of(r) == (400, "input.invalid", "prompt"), r.text

    run_app(db, scenario)


# ---- https only, SSRF ---------------------------------------------------------------------------------------
@mutation(DOORS, "        await check_callback(req.callback_url)", "        pass", expect="callback")
@mutation(DOORS, 'raise ApiError("input.invalid", ref.param) from None', "pass", expect="http image")
def test_an_http_or_private_callback_or_image_url_is_400_and_charges_nothing(db):
    async def scenario(env):
        key = await env.new_key()
        for url in ("http://93.184.216.34/hook", "https://127.0.0.1/hook", "https://10.1.2.3/hook",
                    "https://169.254.169.254/latest", "ftp://93.184.216.34/x", "https://u:p@93.184.216.34/x"):
            r = await env.client.post(VIDEO_PATH, json=video(callback_url=url), headers=key.headers)
            assert error_of(r) == (400, "input.invalid", "callback_url"), "callback %s: %s" % (url, r.text)
        for field, url in (("image", "http://93.184.216.34/a.png"), ("image", "https://192.168.0.9/a.png"),
                           ("image", "https://[::1]/a.png")):
            r = await env.client.post(VIDEO_PATH, json=video(**{field: url}), headers=key.headers)
            assert error_of(r) == (400, "input.invalid", "image"), "http image %s: %s" % (url, r.text)
        r = await env.client.post(VIDEO_PATH, json=video(images=[png_uri(), "http://93.184.216.34/a.png"]),
                                  headers=key.headers)
        assert error_of(r) == (400, "input.invalid", "images.1"), "http image: %s" % r.text
        assert not jobs(db, key.id) and charges(db, key_id=key.id) == 0, nothing_made(db, key)

    run_app(db, scenario)


def test_an_https_callback_and_an_https_image_url_are_accepted_and_stored(db, monkeypatch):
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    monkeypatch.setitem(doors.NET_OPTIONS, "transport", httpx.MockTransport(serve))

    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, json=video(image="https://93.184.216.34/cat.png",
                                                         callback_url="https://93.184.216.34/hook"),
                                  headers=key.headers)
        assert r.status_code == 200, r.text
        job_id = r.json()["id"]
        assert db.one("SELECT callback_url FROM jobs WHERE id = %s", job_id)[0] == "https://93.184.216.34/hook"
        (row,) = db.all("SELECT object_key, mime, size, share_token IS NOT NULL, share_expires_at IS NOT NULL "
                        "FROM job_media WHERE job_id = %s AND role = 'input'", job_id)
        assert row[1:] == ("image/png", len(PNG), True, True), row
        assert (await env.store.stat(row[0])).size == len(PNG), "the input is in the object store"

    run_app(db, scenario)


# ---- input checks happen before the charge (R15) ------------------------------------------------------------------
@mutation(DOORS, "    if not prompt:", "    if False:", expect="empty_prompt")
@mutation(DOORS, "    if len(prompt) > max_chars:", "    if False:", expect="prompt_too_long")
def test_a_blank_or_too_long_prompt_is_400_with_no_job_and_no_charge(db):
    async def scenario(env):
        key = await env.new_key()
        for path, build in ((VIDEO_PATH, lambda t: video(prompt=t)), (CHAT_PATH, lambda t: chat(t)),
                            (GEMINI_PATH, lambda t: gemini(t))):
            r = await env.client.post(path, json=build("  \n "), headers=key.headers)
            assert error_of(r)[:2] == (400, "input.empty_prompt"), (path, r.text)
            r = await env.client.post(path, json=build("x" * (FAKE_MAX_PROMPT_CHARS + 1)), headers=key.headers)
            assert error_of(r)[:2] == (400, "input.prompt_too_long"), (path, r.text)
        r = await env.client.post(CHAT_PATH, json={"model": VIDEO, "messages": []}, headers=key.headers)
        assert error_of(r) == (400, "input.empty_prompt", "messages"), r.text
        r = await env.client.post(VIDEO_PATH, json=video(prompt="x" * FAKE_MAX_PROMPT_CHARS), headers=key.headers)
        assert r.status_code == 200, "a prompt at the cap is accepted"
        assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1

    run_app(db, scenario)


@mutation(DOORS, "    if len(req.images) > MAX_IMAGES:", "    if len(req.images) > MAX_IMAGES + 5:",
          expect="too_many_images")
def test_more_than_ten_images_is_the_same_400_on_every_door(db):
    def gemini_images(n):
        part = {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}
        return {"contents": [{"role": "user", "parts": [{"text": "a"}] + [part] * n}]}

    def chat_images(n):
        part = {"type": "image_url", "image_url": {"url": png_uri()}}
        return chat([{"type": "text", "text": "a"}] + [part] * n)

    async def scenario(env):
        key = await env.new_key()
        for path, eleven, ten in ((VIDEO_PATH, video(images=[png_uri()] * 11), video(images=[png_uri()] * 10)),
                                  (CHAT_PATH, chat_images(11), None), (GEMINI_PATH, gemini_images(11), None)):
            r = await env.client.post(path, json=eleven, headers=key.headers)
            assert error_of(r)[:2] == (400, "input.too_many_images"), (path, r.text)
            if ten is not None:
                assert (await env.client.post(path, json=ten, headers=key.headers)).status_code == 200
        # `image` and `images` count together
        r = await env.client.post(VIDEO_PATH, json=video(image=png_uri(), images=[png_uri()] * 10),
                                  headers=key.headers)
        assert error_of(r)[:2] == (400, "input.too_many_images"), r.text
        assert len(jobs(db, key.id)) == 1

    run_app(db, scenario)


def test_an_image_that_is_not_a_jpeg_png_or_webp_or_not_base64_is_400_image_unreadable(db):
    async def scenario(env):
        key = await env.new_key()
        text = "data:image/png;base64," + base64.b64encode(b"just text, declared as a png").decode()
        too_big = "data:image/png;base64," + base64.b64encode(PNG + b"\x00" * (doors.MAX_IMAGE_BYTES + 1)).decode()
        for field, value in (("image", "data:image/png;base64,@@@@"), ("image", text), ("image", too_big),
                             ("image", "data:image/png;base64,"), ("image", "not a url or a data uri")):
            r = await env.client.post(VIDEO_PATH, json=video(**{field: value}), headers=key.headers)
            assert error_of(r)[:2] in ((400, "input.image_unreadable"), (400, "input.invalid")), (value[:30], r.text)
            assert error_of(r)[2] == "image", r.text
        for good in (b"\xff\xd8\xff\xe0" + b"\0" * 8, b"RIFF\0\0\0\0WEBPVP8 ", PNG):
            r = await env.client.post(VIDEO_PATH, json=video(image="data:image/x;base64,"
                                                             + base64.b64encode(good).decode()), headers=key.headers)
            assert r.status_code == 200, "the bytes decide the type, not the declared one: %s" % r.text
        assert len(jobs(db, key.id)) == 3

    run_app(db, scenario)


@mutation(DOORS, "        return resolve_model(registry, requested)[0].max_prompt_chars",
          "        return max(registry.spec(n).max_prompt_chars for n in registry.names())",
          expect="the model's own cap")
def test_the_cap_is_the_one_of_the_models_provider_and_an_unknown_model_gets_the_largest(db):
    small, large = FakeSpec("small", max_prompt_chars=10), FakeSpec("large", max_prompt_chars=30)
    registry = ProviderRegistry()
    registry.register(small)
    registry.register(large)
    assert doors.prompt_cap(registry, "small-video") == 10, "the model's own cap: small-video is small's"
    assert doors.prompt_cap(registry, "large-image") == 30, "the model's own cap: large-image is large's"
    assert doors.prompt_cap(registry, "large-video-5s") == 30, "the model's own cap: a variant is its base model's"
    assert doors.prompt_cap(registry, "nope") == 30, "an unknown model gets the largest cap"

    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, json=video(model="nope", prompt="x" * (FAKE_MAX_PROMPT_CHARS + 1)),
                                  headers=key.headers)
        assert error_of(r)[:2] == (400, "input.prompt_too_long"), "unknown model, over every cap: " + r.text
        r = await env.client.post(VIDEO_PATH, json=video(model="nope", prompt="x" * FAKE_MAX_PROMPT_CHARS),
                                  headers=key.headers)
        assert error_of(r) == (400, "input.bad_model", "model"), "unknown model, a prompt within the cap: " + r.text
        assert not jobs(db, key.id)

    run_app(db, scenario)


# ---- the model gate: resolution, media type, the key's allow-list, plan() refusals --------------------------------
@mutation(DOORS, "    if req.media_type is not None and plan.media_type != req.media_type:", "    if False:",
          expect="media type")
def test_an_unknown_model_or_the_other_kind_of_model_is_400_bad_model(db):
    async def scenario(env):
        key = await env.new_key()
        for path, body in ((VIDEO_PATH, video(model="nope")), (CHAT_PATH, chat(model="nope")),
                           (GEMINI_PATH.replace("fake-video", "nope"), gemini())):
            r = await env.client.post(path, json=body, headers=key.headers)
            assert error_of(r) == (400, "input.bad_model", "model"), (path, r.text)
        r = await env.client.post(VIDEO_PATH, json=video(model=IMAGE), headers=key.headers)
        assert error_of(r) == (400, "input.bad_model", "model"), "media type: an image model on the video door"
        r = await env.client.post(IMAGE_PATH, json={"model": VIDEO, "prompt": "a"}, headers=key.headers)
        assert error_of(r) == (400, "input.bad_model", "model"), "media type: a video model on the image door"
        assert not jobs(db, key.id)

    run_app(db, scenario)


@mutation(DOORS, "catalog.setdefault(requested, model.id)", "pass", expect="variant")
@mutation(DOORS, 'raise ApiError("auth.permission_unknown") from None   # fail closed', "ok = True",
          expect="permission_unknown")
def test_the_key_allow_list_covers_variants_refuses_others_and_fails_closed(db):
    async def scenario(env):
        strict = StrictFake()
        env.add_provider(strict)
        key = await env.new_key(models=("strict-video",))
        h = key.headers
        r = await env.client.post(VIDEO_PATH, json=video(model="strict-video-5s"), headers=h)
        assert r.status_code == 200, "variant: an entry covers the variants of its model: %s" % r.text
        r = await env.client.post(VIDEO_PATH, json=video(model=VIDEO), headers=h)
        assert error_of(r) == (403, "auth.model_forbidden", "model"), r.text
        r = await env.client.post(IMAGE_PATH, json={"model": "strict-image", "prompt": "a"}, headers=h)
        assert error_of(r)[:2] == (403, "auth.model_forbidden"), r.text
        everything = await env.new_key()
        assert (await env.client.post(VIDEO_PATH, json=video(), headers=everything.headers)).status_code == 200
        strict.broken = True
        r = await env.client.post(VIDEO_PATH, json=video(model="strict-video"), headers=h)
        assert error_of(r)[:2] == (503, "auth.permission_unknown"), "permission_unknown: %s" % r.text
        assert len(jobs(db, key.id)) == 1 and len(jobs(db, everything.id)) == 1

    run_app(db, scenario)


@mutation(DOORS, "    return model.id in settings.get(spec.name + \".disabled_models\")", "    return False",
          expect="paused model was accepted")
def test_a_paused_model_is_refused_at_every_door_before_any_charge_and_accepted_jobs_stay(db):
    async def scenario(env):
        strict = StrictFake()
        env.add_provider(strict)
        key = await env.new_key()
        before = await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)
        assert before.status_code == 200, before.text
        await env.settings.set("fake.disabled_models", ["fake-video"])
        await env.settings.set("strict.disabled_models", ["strict-video"])
        for path, body in ((VIDEO_PATH, video()), (CHAT_PATH, chat()), (GEMINI_PATH, gemini()),
                           (VIDEO_PATH, video(model="strict-video-5s")), (VIDEO_PATH, video(model="strict-video"))):
            r = await env.client.post(path, json=body, headers=key.headers)
            assert error_of(r) == (403, "model.disabled", "model"), "paused model was accepted: %s %s" % (path, r.text)
        assert jobs(db, key.id) == [(uuid.UUID(before.json()["id"]), "queued")], "an accepted job runs on"
        assert charges(db, key_id=key.id) == 1, nothing_made(db, key)
        r = await env.client.post(IMAGE_PATH, json={"model": IMAGE, "prompt": "a"}, headers=key.headers)
        assert r.status_code == 200, "another model of the provider still works: %s" % r.text
        await env.settings.set("fake.disabled_models", [])
        assert (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).status_code == 200
        assert errors.rule("model.disabled").message == "this model is paused"

    run_app(db, scenario)


@mutation(DOORS, "if isinstance(exc, InputError) and exc.code != errors.INTERNAL and errors.rule(exc.code).code",
          "if False and exc.code != errors.INTERNAL and errors.rule(exc.code).code", expect="bad_duration")
@mutation(DOORS, "if isinstance(exc, InputError) and exc.code", "if exc.code", expect="duck-typed")
def test_a_plan_refusal_is_its_own_400_and_an_engine_crash_never_leaks_its_text(db):
    async def scenario(env):
        strict = StrictFake()
        env.add_provider(strict)
        key = await env.new_key()
        post = lambda **options: env.client.post(  # noqa: E731
            VIDEO_PATH, json=video(model="strict-video", options=options), headers=key.headers)
        r = await post(refuse="input.bad_duration")
        assert error_of(r) == (400, "input.bad_duration", "duration"), r.text
        r = await post(refuse="input.bad_ratio", param="options.ratio")
        assert error_of(r) == (400, "input.bad_ratio", "options.ratio"), r.text
        r = await post(refuse="input.prompt_ratio_conflict")
        assert error_of(r) == (400, "input.prompt_ratio_conflict", None), r.text
        r = await post(refuse="not.registered")
        assert r.status_code == 500 and error_of(r)[1] == "internal_error", r.text
        r = await post(duck="input.bad_duration")
        assert r.status_code == 500 and error_of(r)[1] == "internal_error", "duck-typed: %s" % r.text
        r = await post(boom=True)
        assert r.status_code == 500 and error_of(r)[1] == "internal_error" and "secret" not in r.text, r.text
        assert not jobs(db, key.id) and charges(db, key_id=key.id) == 0, nothing_made(db, key)

    run_app(db, scenario)


@mutation(NATIVE, "\n    style: str | None = Field(",
          "\n    template: StrictInt | None = None\n    style: str | None = Field(",
          expect="the door took a template field")
def test_the_fields_of_each_door_reach_plan_as_one_request(db, monkeypatch):
    async def scenario(env):
        await quick_holds(monkeypatch)
        strict = StrictFake()
        env.add_provider(strict)
        key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, json={
            "model": "strict-video", "prompt": "  a cat  ", "duration": 5, "ratio": "16:9", "n": 2,
            "images": [png_uri()], "options": {"seed": 3}, "title": "My cat",
            "callback_url": "https://93.184.216.34/hook"}, headers=key.headers)
        assert r.status_code == 200, r.text
        got = strict.seen[-1]
        assert (got.model, got.prompt, got.duration, got.ratio, got.n, got.title, got.door) == (
            "strict-video", "a cat", 5, "16:9", 2, "My cat", "native")
        assert dict(got.options) == {"seed": 3} and len(got.images) == 1 and got.images[0].mime == "image/png"
        assert got.images[0].size == len(PNG) and len(got.images[0].sha256) == 64
        r = await env.client.post(IMAGE_PATH, json={"model": "strict-image", "prompt": "a", "style": "noir"},
                                  headers=key.headers)
        assert r.status_code == 200, r.text
        assert dict(strict.seen[-1].options) == {"style": "noir"}
        for field in ("template", "template_category"):
            r = await env.client.post(IMAGE_PATH, json={"model": "strict-image", "prompt": "a", field: 3},
                                      headers=key.headers)
            assert r.status_code == 400 and error_of(r) == (400, "input.invalid", field), (
                "the door took a template field: %s" % r.text)
        r = await env.client.post(CHAT_PATH, json=chat(model="strict-video-9s"), headers=key.headers)
        assert r.status_code == 200, r.text
        assert strict.seen[-1].door == "facade", "a facade request tells plan() it came by a facade"

    run_app(db, scenario)


@mutation(DOORS, 'raise ApiError("input.invalid", "title")', "pass", expect="title")
@mutation(DOORS, 'j.params, " + SHOWN_TITLE + ", j.warnings', "j.params, j.title, j.warnings",
          expect="untitled: the prompt's first words")
def test_a_title_is_one_line_up_to_200_characters_and_comes_back_in_status(db):
    async def scenario(env):
        key = await env.new_key()
        for bad in ("x" * 201, "two\nlines"):
            r = await env.client.post(VIDEO_PATH, json=video(title=bad), headers=key.headers)
            assert error_of(r) == (400, "input.invalid", "title"), "title %r: %s" % (bad[:10], r.text)
        r = await env.client.post(VIDEO_PATH, json=video(title="x" * 200), headers=key.headers)
        assert r.status_code == 200, r.text
        s = await env.client.get("%s/%s" % (VIDEO_PATH, r.json()["id"]), headers=key.headers)
        assert s.json()["title"] == "x" * 200
        r = await env.client.post(VIDEO_PATH, json=video(prompt="a red fox trotting through a snowy forest at dawn"),
                                  headers=key.headers)
        s = await env.client.get("%s/%s" % (VIDEO_PATH, r.json()["id"]), headers=key.headers)
        assert s.json()["title"] == "a red fox trotting through a snowy forest", "untitled: the prompt's first words"

    run_app(db, scenario)


# ---- the core's price (relay_core.pricing) ------------------------------------------------------------------------
PRICE_STEP = "        return spec, pricing.price_plan(spec, plan, gate.settings)"


class NoDefaults(FakeSpec):
    """A provider whose shipped tables leave an option out: its video has no default price, its image no default
    cost (an option sold before anyone priced it). A stored table is read over the defaults, so only an option with no
    default can be missing."""

    def settings(self):
        out = super().settings()
        n = self.name
        prices, costs = out[n + ".prices"], out[n + ".costs"]
        out[n + ".prices"] = dataclasses.replace(prices, default={n + "-image": 1})
        out[n + ".costs"] = dataclasses.replace(costs, default={n + "-video": 1})
        return out


@mutation(DOORS, PRICE_STEP, "        return spec, plan", expect="expected an error")
def test_an_option_with_no_price_or_no_cost_is_refused_and_a_queued_job_keeps_its_price(db):
    """No fallback price and no guessed cost: the door refuses, nothing is charged; a job already made keeps the
    price it was charged when the owner later changes it."""
    async def scenario(env):
        env.add_provider(NoDefaults("np"))
        key = await env.new_key()
        made = (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).json()["id"]
        unpriced = await env.client.post(VIDEO_PATH, json=video(model="np-video"), headers=key.headers)
        uncosted = await env.client.post(IMAGE_PATH, json={"model": "np-image", "prompt": "a cat"}, headers=key.headers)
        await env.settings.set("fake.prices", {"fake-video": 25, "fake-image": 1})
        await env.settings.set("fake.costs", {"fake-video": 9, "fake-image": 1})
        too_costly = await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)   # fake.video_budget is 4
        status = (await env.client.get("%s/%s" % (VIDEO_PATH, made), headers=key.headers)).json()
        return key, unpriced, uncosted, too_costly, status

    key, unpriced, uncosted, too_costly, status = run_app(db, scenario)
    assert error_of(unpriced) == (403, "model.unpriced", "model"), "unpriced: %s" % unpriced.text
    assert error_of(uncosted) == (403, "model.uncosted", "model"), "uncosted: %s" % uncosted.text
    assert error_of(too_costly) == (400, "input.too_costly", "model"), "too costly: %s" % too_costly.text
    assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1, nothing_made(db, key)
    assert status["points"] == 10, "a job already made keeps the price it was charged"


@mutation(DOORS, PRICE_STEP, "        return spec, plan", expect="the settings price it")
def test_the_door_charges_the_settings_price_and_records_the_settings_cost_never_the_options(db):
    async def scenario(env):
        key = await env.new_key()
        await env.settings.set("fake.prices", {"fake-video": 7, "fake-image": 1})
        await env.settings.set("fake.costs", {"fake-video": 3, "fake-image": 1})
        r = await env.client.post(VIDEO_PATH, json=video(options={"price": 999, "account_cost": 50}),
                                  headers=key.headers)
        return r

    r = run_app(db, scenario)
    assert r.status_code == 200, "the settings price it: %s" % r.text
    assert db.one("SELECT price, account_cost FROM jobs WHERE id = %s", r.json()["id"]) == (7, 3), (
        "the settings price it, the options never do")


# ---- status --------------------------------------------------------------------------------------------------------
@mutation(DOORS, '"error": message, "error_code": shown_code,', '"error": message, "result_url": None, '
          '"error_code": shown_code,', expect="result_url")
def test_status_has_the_contract_fields_and_none_of_the_older_compatibility_ones(db):
    async def scenario(env):
        key = await env.new_key()
        made = [(await env.client.post(VIDEO_PATH, json=video(title="t"), headers=key.headers)).json()
                for _ in range(2)]
        s = await env.client.get("%s/%s" % (VIDEO_PATH, made[1]["id"]), headers=key.headers)
        body = s.json()
        assert s.status_code == 200 and set(body) == STATUS_KEYS, "fields differ: %s" % (set(body) ^ STATUS_KEYS)
        assert not {"poll_deadline_s", "result_url", "warning"} & set(body)
        assert (body["status"], body["stage"], body["queue_position"], body["title"], body["model"]) == (
            "queued", None, 2, "t", VIDEO)
        assert (body["result_urls"], body["images"], body["error"], body["error_code"], body["refunded"],
                body["points"], body["finished_at"]) == ([], [], None, None, False, 10, None)

    run_app(db, scenario)


@mutation(DOORS, '"points": points,', '"points": 0,', expect="charged when it was made")
@mutation(DOORS, POINTS_OF_FAILED, "THEN sum(CASE l.entry WHEN 'charge' THEN l.amount ELSE -l.amount END) ",
          expect="the charge its refund gave back")
@mutation(DOORS, POINTS_NET, "ELSE (array_agg(l.amount ORDER BY l.id DESC) FILTER (WHERE l.entry = 'charge'))[1] END ",
          expect="a refused charge")
@mutation(DOORS, POINTS_NET, "ELSE sum(l.amount) FILTER (WHERE l.entry = 'charge') END ",
          expect="charged once more")
@mutation(DOORS, POINTS_OF_FAILED, "THEN sum(l.amount) FILTER (WHERE l.entry = 'charge') ", expect="no double count")
def test_status_shows_the_points_the_job_charged_the_key(db):
    async def scenario(env):
        key, other = await env.new_key(), await env.new_key()
        admin = await env.admin_headers()

        async def make(path=VIDEO_PATH, **body) -> str:
            return (await env.client.post(path, json=body or video(), headers=key.headers)).json()["id"]

        async def seen(job_id, path=VIDEO_PATH) -> tuple:
            body = (await env.client.get("%s/%s" % (path, job_id), headers=key.headers)).json()
            return body["status"], body["points"], body["refunded"]

        async def recheck(job_id) -> int:
            db.run("UPDATE jobs SET external_ref = %s WHERE id = %s", "ref-%s" % job_id, job_id)
            r = await env.client.post("/admin/jobs/%s/recheck" % job_id, headers=admin)
            assert r.status_code == 200, r.text
            return r.json()["recharged"]

        queued, done, failed, again, refused = [await make() for _ in range(5)]
        image = await make(IMAGE_PATH, model=IMAGE, prompt="a", n=1)
        await env.settings.set("fake.prices", {"fake-video": 0, "fake-image": 1})      # the owner sells it free
        free = await make()
        await env.settings.delete("fake.prices")
        assert await seen(queued) == ("queued", 10, False), "charged when it was made"
        assert await seen(image, IMAGE_PATH) == ("queued", 1, False), "charged when it was made"
        finish_done(db, done)
        assert await seen(done) == ("done", 10, False), "charged when it was made"
        await finish_failed(db, failed)
        assert await seen(failed) == ("failed", 10, True), "a failed job shows the charge its refund gave back"
        assert await seen(free) == ("queued", 0, False), "a free job shows 0"

        # Three charge/refund cycles: failed, re-checked (charged once more), failed, re-checked, failed.
        for expect in (("queued",), ("waiting",)):
            await finish_failed(db, again, expect=expect)
            assert await seen(again) == ("failed", 10, True), "the charge its refund gave back, no double count"
            assert await recheck(again) == 10
            assert await seen(again) == ("running", 10, False), "charged once more after the refund, not twice"
        await finish_failed(db, again, expect=("waiting",))
        assert await seen(again) == ("failed", 10, True), "the charge its refund gave back, no double count"
        assert charges(db, job_id=again) == 3

        await finish_failed(db, refused)                  # the re-charge is refused: the key pays nothing now
        db.run("UPDATE key_wallets SET day_limit = 0 WHERE key_id = %s", key.id)
        assert await recheck(refused) == 0
        assert await seen(refused) == ("running", 0, False), "a refused charge leaves nothing to pay"
        await finish_failed(db, refused, expect=("waiting",))       # nothing new to refund: the first refund stands
        assert await seen(refused) == ("failed", 10, True), "the charge its refund gave back"

        r = await env.client.get("%s/%s" % (VIDEO_PATH, done), headers=other.headers)
        assert error_of(r) == (404, "resource.not_found", None), "another key's job stays unknown"

    run_app(db, scenario)


@mutation(DOORS, POINTS_READ, POINTS_ALL_SCOPES, expect="the points of the points wallet")
@mutation(DOORS, REFUNDED_READ, REFUNDED_ALL_SCOPES, expect="refunded of the points wallet")
def test_status_points_count_only_the_star_rows(db):
    """A charge writes a row on the points wallet and one on the provider's cap: the status shows the price once."""
    async def scenario(env):
        key = await env.new_key()
        job_id = (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).json()["id"]
        body = (await env.client.get("%s/%s" % (VIDEO_PATH, job_id), headers=key.headers)).json()
        assert db.one("SELECT count(*) FROM ledger WHERE job_id = %s", uuid.UUID(job_id))[0] == 2
        assert body["points"] == 10, "the points of the points wallet, once: %r" % body["points"]
        # only the cap row was refunded (a half refund planted by hand): the job is not shown as refunded
        db.run("UPDATE jobs SET status = 'failed', error_code = 'render.deadline', finished_at = now() WHERE id = %s",
               uuid.UUID(job_id))
        db.run("INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day, refund_of) "
               "SELECT key_id, scope, job_id, provider, 'refund', amount, billing_day, id FROM ledger "
               "WHERE job_id = %s AND scope = 'fake' AND entry = 'charge'", uuid.UUID(job_id))
        db.run("UPDATE key_wallets SET used_total = 0, used_day = 0 WHERE key_id = %s AND scope = 'fake'", key.id)
        body = (await env.client.get("%s/%s" % (VIDEO_PATH, job_id), headers=key.headers)).json()
        assert body["refunded"] is False, "refunded of the points wallet only: %r" % body["refunded"]
        db.run("INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day, refund_of) "
               "SELECT key_id, scope, job_id, provider, 'refund', amount, billing_day, id FROM ledger "
               "WHERE job_id = %s AND scope = '*' AND entry = 'charge'", uuid.UUID(job_id))     # now both are refunded
        db.run("UPDATE key_wallets SET used_total = 0, used_day = 0 WHERE key_id = %s AND scope = '*'", key.id)
        body = (await env.client.get("%s/%s" % (VIDEO_PATH, job_id), headers=key.headers)).json()
        assert body["refunded"] is True and body["points"] == 10

    run_app(db, scenario)


@mutation(DOORS, POINTS_READ, "FROM ledger l WHERE l.job_id::text = j.id::text "
          "AND l.scope = '*' AND l.entry IN ('charge', 'refund')), 0) ", expect="served by ledger_job")
def test_the_status_query_reads_the_ledger_through_its_job_index(db):
    """EXPLAIN with the sequential scan switched off (the tables are nearly empty): both ledger reads of the status
    (`refunded` and `points`) are lookups on `ledger_job`, never a ledger scan."""
    with db.connect() as conn:
        conn.execute("SET enable_seqscan = off")
        plan = "\n".join(r[0] for r in psycopg.ClientCursor(conn).execute(
            "EXPLAIN " + doors._STATUS, (uuid.uuid4(), uuid.uuid4())))
    print("\nSTATUS PLAN\n" + plan)
    assert "Seq Scan on ledger" not in plan and plan.count("Index Cond: (job_id = j.id)") == 2 \
        and "ledger_job" in plan, "both ledger reads are served by ledger_job:\n" + plan


@mutation(DOORS, "if status == \"failed\" else (None, None)", "if True else (None, None)", expect="live")
def test_a_running_job_shows_its_stage_and_never_internal_error_text(db):
    async def scenario(env):
        key = await env.new_key()
        job_id = (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).json()["id"]
        for status, stage in (("submitting", "submitting"), ("waiting", "waiting"), ("fetching", "fetching"),
                              ("unknown", "unknown")):
            db.run("UPDATE jobs SET status = %s, error = 'curl: (28) engine detail secret', external_ref = 'r', "
                   "sent_at = now(), next_poll_at = now() WHERE id = %s", status, job_id)
            if status == "submitting":
                db.run("UPDATE jobs SET external_ref = NULL, next_poll_at = NULL WHERE id = %s", job_id)
            body = (await env.client.get("%s/%s" % (VIDEO_PATH, job_id), headers=key.headers)).json()
            assert (body["status"], body["stage"]) == ("running", stage), body
            assert body["error"] is None and body["error_code"] is None and body["queue_position"] is None, (
                "live job shows no error text: %s" % body)

    run_app(db, scenario)


def test_a_done_job_lists_every_output_url_from_the_public_base_url(db):
    async def scenario(env):
        key = await env.new_key()
        video_job = (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).json()["id"]
        image_job = (await env.client.post(IMAGE_PATH, json={"model": IMAGE, "prompt": "a", "n": 2},
                                           headers=key.headers)).json()["id"]
        (vt,), tokens = finish_done(db, video_job, 1), finish_done(db, image_job, 2)
        body = (await env.client.get("%s/%s" % (VIDEO_PATH, video_job), headers=key.headers)).json()
        assert body["status"] == "done" and body["stage"] is None and body["finished_at"]
        assert body["result_urls"] == ["https://api.example.com/m/" + vt] and body["images"] == []
        body = (await env.client.get("%s/%s" % (IMAGE_PATH, image_job), headers=key.headers)).json()
        assert body["images"] == ["https://api.example.com/m/" + t for t in tokens] and body["result_urls"] == []
        db.run("UPDATE job_media SET share_token = NULL WHERE job_id = %s AND idx = 0", image_job)
        body = (await env.client.get("%s/%s" % (IMAGE_PATH, image_job), headers=key.headers)).json()
        assert body["images"] == ["https://api.example.com/m/" + tokens[1]], "a revoked link is not listed"

    run_app(db, scenario, public_base_url="https://api.example.com/")


def test_media_urls_fall_back_to_the_request_host(db):
    async def scenario(env):
        key = await env.new_key()
        job_id = (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).json()["id"]
        (token,) = finish_done(db, job_id)
        body = (await env.client.get("%s/%s" % (VIDEO_PATH, job_id), headers=key.headers)).json()
        assert body["result_urls"] == ["http://relay.test/m/" + token]

    run_app(db, scenario)


@mutation(DOORS, 'errors.shown(code or errors.INTERNAL) if status == "failed"', '(code, code) if status == "failed"',
          expect="message")
def test_a_failed_job_shows_its_neutral_code_its_customer_sentence_and_refunded_never_internals(db, monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(errors.RULES))
    errors.register(errors.Rule("ours.secret", 502, "api_error", True, True, False, True, "internal detail",
                                public_code=errors.INTERNAL_ERROR))

    async def scenario(env):
        key = await env.new_key()
        public = (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).json()["id"]
        hidden = (await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)).json()["id"]
        await finish_failed(db, public, "render.deadline", "upstream said: engine detail secret")
        await finish_failed(db, hidden, "ours.secret", "upstream said: engine detail secret")
        a = (await env.client.get("%s/%s" % (VIDEO_PATH, public), headers=key.headers)).json()
        assert (a["status"], a["error_code"], a["error"], a["refunded"]) == (
            "failed", "timeout", errors.rule("render.deadline").public_text, True), "message: %s" % a
        b = (await env.client.get("%s/%s" % (VIDEO_PATH, hidden), headers=key.headers)).json()
        assert (b["error_code"], b["error"]) == ("internal_error", errors.PUBLIC_CODES["internal_error"][1]), (
            "message: %s" % b)
        assert "secret" not in str(a) + str(b) and a["finished_at"]
        assert "render.deadline" not in str(a) and "internal detail" not in str(b) and "ours" not in str(b)
        stored = db.all("SELECT error_code FROM jobs WHERE id IN (%s, %s) ORDER BY error_code", public, hidden)
        assert [r[0] for r in stored] == ["ours.secret", "render.deadline"], "the row keeps the internal code"

    run_app(db, scenario)


@mutation(DOORS, "FROM jobs j WHERE j.id = %s AND j.key_id = %s",
          "FROM jobs j WHERE j.id = %s AND %s::uuid IS NOT NULL", expect="resource.not_found")
def test_only_the_owning_key_reads_a_job_and_the_media_kind_must_match(db):
    async def scenario(env):
        owner, other = await env.new_key(), await env.new_key()
        video_job = (await env.client.post(VIDEO_PATH, json=video(), headers=owner.headers)).json()["id"]
        ok = await env.client.get("%s/%s" % (VIDEO_PATH, video_job), headers=owner.headers)
        assert ok.status_code == 200
        for path in ("%s/%s" % (VIDEO_PATH, video_job), "%s/%s" % (IMAGE_PATH, video_job),
                     VIDEO_PATH + "/not-a-uuid", "%s/%s" % (VIDEO_PATH, "00000000-0000-0000-0000-000000000000")):
            r = await env.client.get(path, headers=other.headers if path.endswith(video_job) else owner.headers)
            assert error_of(r) == (404, "resource.not_found", None), (path, r.text)
        r = await env.client.get("%s/%s" % (VIDEO_PATH, video_job), headers=other.headers)
        assert error_of(r)[:2] == (404, "resource.not_found"), "resource.not_found for another key: %s" % r.text
        assert (await env.client.get("%s/%s" % (VIDEO_PATH, video_job))).status_code == 401

    run_app(db, scenario)


@mutation(DOORS, '"style": params.get("style")', '"style": None', expect="style comes from the job's params")
def test_the_image_door_takes_a_title_and_its_status_shows_the_title_and_the_style(db):
    async def scenario(env):
        key = await env.new_key()

        def image(title: str):
            body = {"model": IMAGE, "prompt": "a", "title": title}
            return env.client.post(IMAGE_PATH, json=body, headers=key.headers)

        for bad in ("x" * 201, "two\nlines"):
            r = await image(bad)
            assert error_of(r) == (400, "input.invalid", "title"), "title %r: %s" % (bad[:10], r.text)
        r = await image("My cat")
        assert r.status_code == 200, r.text
        job_id = r.json()["id"]
        # what a plan that keeps the style stores (the fake plan keeps none): status reads it from the params
        db.run("UPDATE jobs SET params = params || %s::jsonb WHERE id = %s::uuid", '{"style": "noir"}', job_id)
        s = (await env.client.get("%s/%s" % (IMAGE_PATH, job_id), headers=key.headers)).json()
        assert (s["title"], s["style"]) == ("My cat", "noir"), "style comes from the job's params: %r" % s

    run_app(db, scenario)


@mutation(NATIVE, 'refs = [ImageRef(body.image, "image")] if body.image else []',
          'refs = [ImageRef(body.image, "image")] if body.image is not None else []',
          expect="an empty image reference is no image")
def test_an_empty_image_reference_is_no_image(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, json=video(image=""), headers=key.headers)
        assert r.status_code == 200, "an empty image reference is no image: %s" % r.text
        assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s::uuid", r.json()["id"])[0] == 0

    run_app(db, scenario)


@mutation(DOORS, "MAX_OPTIONS_BYTES = 16 * 1024", "MAX_OPTIONS_BYTES = 16 * 1024 * 1024", expect="options are bounded")
def test_the_options_of_a_request_are_bounded_and_nothing_is_made_over_the_bound(db):
    """The client's options are stored with the job for good: 16 KiB of JSON at most."""
    async def scenario(env):
        key = await env.new_key()
        over = await env.client.post(VIDEO_PATH, json=video(options={"note": "x" * (16 * 1024)}), headers=key.headers)
        assert error_of(over) == (400, "input.invalid", "options"), "the options are bounded: %s" % over.text[:200]
        assert not jobs(db, key.id) and charges(db, key_id=key.id) == 0
        under = await env.client.post(VIDEO_PATH, json=video(options={"note": "x" * 1000}), headers=key.headers)
        assert under.status_code == 200, under.text

    run_app(db, scenario)

