"""The Gemini facade (contracts 8): request bodies the real `google-genai` SDK sends (golden snapshots), prompt and
image extraction, url-safe and unpadded base64, the Gemini error body, D27 strictness."""

import base64
import functools

import httpx
from apikit import run_app
from doorkit import (
    PNG,
    PNG_URLSAFE_ONLY,
    VIDEO,
    charges,
    error_of,
    finish_done,
    finish_failed,
    finish_later,
    genai_request,
    golden,
    jobs,
    replay,
)
from google.genai import types

from relay_core.api import doors
from relay_core.api import facade_common as fc
from relay_core.testing.mutation import mutation

DECODE = "packages/relay-core/src/relay_core/api/doors.py"
GEMINI = "packages/relay-core/src/relay_core/api/routes_gemini.py"
PATH = "/v1beta/models/fake-video:generateContent"
STREAM = "/v1beta/models/fake-video:streamGenerateContent"
WHERE = "contents.0.parts.1.inlineData.data"


def content(*parts, role="user") -> dict:
    return {"role": role, "parts": list(parts)}


def text(t: str) -> dict:
    return {"text": t}


def inline(data: bytes, mime_key="mimeType", mime="image/png", *, b64=None) -> dict:
    return {"inlineData": {"data": b64 if b64 is not None else base64.urlsafe_b64encode(data).decode(),
                           mime_key: mime}}


@functools.cache
def snapshots() -> dict[str, dict]:
    def with_bytes(client):
        return client.models.generate_content(model=VIDEO, contents=[
            types.Part.from_text(text="a cat"), types.Part.from_bytes(data=PNG_URLSAFE_ONLY, mime_type="image/png")])

    return {
        "gemini_generate_text": genai_request(lambda c: c.models.generate_content(model=VIDEO, contents="a cat")),
        "gemini_generate_inline_data": genai_request(with_bytes),
        "gemini_generate_temperature": genai_request(lambda c: c.models.generate_content(
            model=VIDEO, contents="a cat", config=types.GenerateContentConfig(temperature=0.5))),
        "gemini_generate_system_instruction": genai_request(lambda c: c.models.generate_content(
            model=VIDEO, contents="a cat", config=types.GenerateContentConfig(system_instruction="be brief"))),
        "gemini_stream_text": genai_request(lambda c: list(c.models.generate_content_stream(
            model=VIDEO, contents="a cat"))),
    }


def quick(monkeypatch, hold=0.3):
    monkeypatch.setattr(fc, "HOLD_LIMIT_S", hold)
    monkeypatch.setattr(fc, "POLL_S", 0.05)


def prompt_of(db, key) -> str:
    return db.one("SELECT prompt FROM jobs WHERE key_id = %s ORDER BY created_at DESC LIMIT 1", key.id)[0]


def test_the_sdk_request_bodies_match_the_committed_snapshots():
    for name, captured in snapshots().items():
        golden(name, captured)


def test_a_real_sdk_request_creates_a_job_and_the_answer_is_a_generate_content_response(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        r = await replay(env, key, golden("gemini_generate_text", snapshots()["gemini_generate_text"]))
        assert r.status_code == 200, r.text
        response = types.GenerateContentResponse.model_validate(r.json())      # the SDK's own type accepts it
        (job_id, status) = jobs(db, key.id)[0]
        assert status == "queued" and response.response_id == str(job_id)
        assert response.text == "still rendering — poll job %s" % job_id
        assert r.json()["candidates"][0]["finishReason"] == "MAX_TOKENS"
        assert prompt_of(db, key) == "a cat" and charges(db, key_id=key.id) == 1

    run_app(db, scenario)


def test_a_real_sdk_stream_request_is_server_sent_events_whatever_alt_says(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        snap = golden("gemini_stream_text", snapshots()["gemini_stream_text"])
        assert snap["query"] == "alt=sse"
        r = await replay(env, key, snap)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), r.text
        assert r.text.startswith("data: ") and r.text.endswith("\n\n")
        r = await env.client.post("/models/fake-video:streamGenerateContent?alt=json", json=snap["body"],
                                  headers=key.headers)
        assert r.headers["content-type"].startswith("text/event-stream"), "alt is ignored"

    run_app(db, scenario)


def test_a_real_sdk_inline_image_with_url_safe_base64_is_decoded_and_stored(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        snap = golden("gemini_generate_inline_data", snapshots()["gemini_generate_inline_data"])
        assert "_" in snap["body"]["contents"][0]["parts"][1]["inlineData"]["data"], "the SDK sends url-safe base64"
        r = await replay(env, key, snap)
        assert r.status_code == 200, r.text
        (row,) = db.all("SELECT object_key, mime FROM job_media WHERE job_id = %s AND role = 'input'",
                        jobs(db, key.id)[0][0])
        assert row[1] == "image/png" and await env.store.read(row[0]) == PNG_URLSAFE_ONLY

    run_app(db, scenario)


def test_the_sdk_system_instruction_with_its_empty_generation_config_is_accepted_and_prepended(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        snap = golden("gemini_generate_system_instruction", snapshots()["gemini_generate_system_instruction"])
        assert snap["body"]["generationConfig"] == {}
        r = await replay(env, key, snap)
        assert r.status_code == 200, r.text
        assert prompt_of(db, key) == "be brief\n\na cat", "the system instruction comes first"

    run_app(db, scenario)


@mutation(GEMINI, "    if body.generation_config:", "    if False:", expect="generationConfig")
def test_a_real_sdk_generation_config_with_content_is_400_input_invalid_in_the_gemini_shape(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        r = await replay(env, key, golden("gemini_generate_temperature", snapshots()["gemini_generate_temperature"]))
        assert error_of(r) == (400, "input.invalid", "generationConfig"), "generationConfig: %s" % r.text
        body = r.json()["error"]
        assert (body["code"], body["status"]) == (400, "INVALID_ARGUMENT")
        d = body["details"][0]
        assert d["@type"] == "type.googleapis.com/google.rpc.ErrorInfo" and d["domain"] == "relay"
        assert not jobs(db, key.id) and charges(db, key_id=key.id) == 0

    run_app(db, scenario)


# ---- base64 ---------------------------------------------------------------------------------------------------------
@mutation(DECODE, 's = s.replace("-", "+").replace("_", "/")', "s = s", expect="urlsafe")
@mutation(DECODE, 's += "=" * (-len(s) % 4)', 's += ""', expect="unpadded")
def test_gemini_base64_may_be_url_safe_or_unpadded_and_a_malformed_string_is_400(db, monkeypatch):
    data = PNG_URLSAFE_ONLY + b"\xfb"                     # 25 bytes: the base64 ends in padding
    safe = base64.urlsafe_b64encode(data).decode()
    assert safe.endswith("=") and ("-" in safe or "_" in safe)

    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        standard = base64.b64encode(data).decode()
        good = {"urlsafe": safe, "urlsafe unpadded": safe.rstrip("="), "standard": standard,
                "standard unpadded": standard.rstrip("=")}
        for name, b64 in good.items():
            body = {"contents": [content(text("a"), inline(data, b64=b64))]}
            r = await env.client.post(PATH, json=body, headers=key.headers)
            assert r.status_code == 200, "%s base64 must be accepted: %s" % (name, r.text)
        stored = db.all("SELECT object_key FROM job_media WHERE role = 'input'")
        assert len(stored) == 4 and {await env.store.read(k) for (k,) in stored} == {data}
        for name, b64 in (("illegal characters", "@@@@"), ("one stray character", safe[:-4] + "A"),
                          ("mixed alphabets with spaces", "AA AA")):
            r = await env.client.post(PATH, json={"contents": [content(text("a"), inline(data, b64=b64))]},
                                      headers=key.headers)
            assert error_of(r) == (400, "input.image_unreadable", WHERE), "malformed (%s): %s" % (name, r.text)
        assert len(jobs(db, key.id)) == 4

    run_app(db, scenario)


def test_the_sdk_and_the_rest_docs_spell_the_mime_key_differently_and_both_work(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        for mime_key in ("mime_type", "mimeType"):
            r = await env.client.post(PATH, json={"contents": [content(text("a"), inline(PNG, mime_key))]},
                                      headers=key.headers)
            assert r.status_code == 200, (mime_key, r.text)
        r = await env.client.post(PATH, json={"contents": [content(text("a"), inline(PNG, mime="application/pdf"))]},
                                  headers=key.headers)
        assert error_of(r) == (400, "input.invalid", "contents.0.parts.1.inlineData.mimeType"), r.text

    run_app(db, scenario)


def test_a_file_uri_is_an_https_image_url_fetched_through_the_ssrf_guard(db, monkeypatch):
    monkeypatch.setitem(doors.NET_OPTIONS, "transport", httpx.MockTransport(lambda r: httpx.Response(200, content=PNG)))

    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        part = lambda uri, mime="image/png": {"fileData": {"fileUri": uri, "mimeType": mime}}  # noqa: E731
        r = await env.client.post(PATH, json={"contents": [content(text("a"), part("https://93.184.216.34/c.png"))]},
                                  headers=key.headers)
        assert r.status_code == 200, r.text
        for bad, expected in ((part("http://93.184.216.34/c.png"), "contents.0.parts.1.fileData.fileUri"),
                              (part("https://10.0.0.1/c.png"), "contents.0.parts.1.fileData.fileUri"),
                              (part("gs://bucket/c.png"), "contents.0.parts.1.fileData.fileUri"),
                              (part("https://93.184.216.34/c.pdf", "application/pdf"),
                               "contents.0.parts.1.fileData.mimeType")):
            r = await env.client.post(PATH, json={"contents": [content(text("a"), bad)]}, headers=key.headers)
            assert error_of(r)[0] == 400 and error_of(r)[2] == expected, (bad, r.text)
        assert len(jobs(db, key.id)) == 1

    run_app(db, scenario)


# ---- prompt extraction ----------------------------------------------------------------------------------------------
@mutation(GEMINI, 'at = max((i for i, c in enumerate(body.contents) if (c.role or "user") == "user"), '
          'default=len(body.contents) - 1)', "at = 0", expect="last user")
@mutation(GEMINI, '(system + "\\n\\n" + prompt).strip() if system else prompt', "prompt", expect="system")
def test_the_prompt_is_the_last_user_content_and_the_system_instruction_goes_first(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        body = {"contents": [content(text("first")), content(text("ok"), role="model"),
                             content(text(" second "), text("third"))],
                "systemInstruction": {"parts": [text("be brief"), text("be kind")]}}
        for path in (PATH, "/models/fake-video:generateContent"):
            assert (await env.client.post(path, json=body, headers=key.headers)).status_code == 200
            assert prompt_of(db, key) == "be brief\nbe kind\n\nsecond\nthird", "last user content, system first"

    run_app(db, scenario)


def test_empty_contents_and_a_content_without_text_are_empty_prompt_and_a_bad_role_is_invalid(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post(PATH, json={"contents": []}, headers=key.headers)
        assert error_of(r) == (400, "input.empty_prompt", "contents"), r.text
        r = await env.client.post(PATH, json={"contents": [content()]}, headers=key.headers)
        assert error_of(r)[:2] == (400, "input.empty_prompt"), r.text
        r = await env.client.post(PATH, json={"contents": [content(text("a"), role="system")]}, headers=key.headers)
        assert error_of(r)[:2] == (400, "input.invalid"), r.text
        assert not jobs(db, key.id)

    run_app(db, scenario)


# ---- auth and errors ------------------------------------------------------------------------------------------------
def test_the_key_comes_from_x_goog_api_key_or_the_standard_headers_never_from_the_query(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        body = {"contents": [content(text("a"))]}
        for headers in ({"x-goog-api-key": key.raw}, key.headers, {"X-API-Key": key.raw}):
            assert (await env.client.post(PATH, json=body, headers=headers)).status_code == 200, headers
        r = await env.client.post(PATH, params={"key": key.raw}, json=body)
        assert error_of(r)[:2] == (401, "auth.invalid_key") and r.json()["error"]["status"] == "UNAUTHENTICATED", r.text

    run_app(db, scenario)


def test_every_refusal_has_the_gemini_error_body(db):
    async def scenario(env):
        key = await env.new_key(models=("fake-image",))
        cases = [(PATH.replace("fake-video", "nope"), {"contents": [content(text("a"))]}, 400, "INVALID_ARGUMENT",
                  "input.bad_model"),
                 (PATH, {"contents": [content(text("a"))]}, 403, "PERMISSION_DENIED", "auth.model_forbidden"),
                 ("/models/fake-image:generateContent", {"contents": [content(text(""))]}, 400, "INVALID_ARGUMENT",
                  "input.empty_prompt"),
                 ("/models/fake-image:generateContent", {"nope": 1}, 400, "INVALID_ARGUMENT", "input.invalid")]
        for path, body, status, grpc, code in cases:
            r = await env.client.post(path, json=body, headers=key.headers)
            err = r.json()["error"]
            shown = (r.status_code, err["code"], err["status"], err["details"][0]["reason"])
            assert shown == (status, status, grpc, code), r.text
            assert "refunded" not in str(err), "no refunded in the Gemini body"
        assert not jobs(db, key.id)

    run_app(db, scenario)


# ---- the end of a held request --------------------------------------------------------------------------------------
@mutation(GEMINI, "        raise ApiError(fc.failure_code(state))", "        pass", expect="render.deadline")
def test_a_done_job_answers_with_its_urls_and_stop_and_a_failed_one_is_a_gemini_http_error(db, monkeypatch):
    async def scenario(env):
        monkeypatch.setattr(fc, "POLL_S", 0.05)
        key = await env.new_key()
        body = {"contents": [content(text("a"))]}
        later = finish_later(db, key.id, finish_done, 0.2)
        r = await env.client.post(PATH, json=body, headers=key.headers)
        await later
        response = types.GenerateContentResponse.model_validate(r.json())
        assert r.json()["candidates"][0]["finishReason"] == "STOP"
        assert response.text.startswith("http://relay.test/m/tok-"), r.text
        later = finish_later(db, key.id, lambda d, j: finish_failed(d, j, "render.deadline", "engine secret"), 0.2,
                             which=1)
        r = await env.client.post("/models/fake-video:generateContent", json=body, headers=key.headers)
        await later
        assert "error" in r.json(), "a failed job must be a Gemini HTTP error with render.deadline: %s" % r.text
        err = r.json()["error"]
        assert (r.status_code, err["code"], err["status"], err["details"][0]["reason"]) == (
            504, 504, "DEADLINE_EXCEEDED", "render.deadline"), r.text
        assert "secret" not in r.text

    run_app(db, scenario)
