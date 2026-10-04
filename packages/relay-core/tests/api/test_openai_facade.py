"""The OpenAI facade (contracts 8): request bodies the real `openai` SDK sends (golden snapshots), prompt extraction,
the hold, a failed job as an HTTP error, and D27 strictness."""

import functools
import json

from apikit import run_app
from doorkit import (
    IMAGE,
    PNG,
    VIDEO,
    charges,
    error_of,
    finish_done,
    finish_failed,
    finish_later,
    golden,
    jobs,
    openai_request,
    png_uri,
    replay,
)
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from relay_core import errors
from relay_core.api import facade_common as fc
from relay_core.testing.mutation import mutation

OPENAI = "packages/relay-core/src/relay_core/api/routes_openai.py"
DOORS = "packages/relay-core/src/relay_core/api/doors.py"
CHAT = "/v1/chat/completions"
USER = {"role": "user"}


@functools.cache
def snapshots() -> dict[str, dict]:
    """Name -> the request the SDK sends. Compared with the committed files by `golden`."""
    image_content = [{"type": "text", "text": "a cat"}, {"type": "image_url", "image_url": {"url": png_uri()}}]
    return {
        "openai_chat_text": openai_request(model=VIDEO, messages=[{**USER, "content": "a cat"}]),
        "openai_chat_stream": openai_request(model=VIDEO, messages=[{**USER, "content": "a cat"}], stream=True),
        "openai_chat_image_url": openai_request(model=VIDEO, messages=[{**USER, "content": image_content}]),
        "openai_chat_temperature": openai_request(model=VIDEO, messages=[{**USER, "content": "a cat"}],
                                                  temperature=0.7),
        "openai_chat_max_tokens": openai_request(model=VIDEO, messages=[{**USER, "content": "a cat"}], max_tokens=16),
        "openai_chat_user": openai_request(model=VIDEO, messages=[{**USER, "content": "a cat"}], user="u-1"),
    }


async def quick(monkeypatch, hold=0.3):
    monkeypatch.setattr(fc, "HOLD_LIMIT_S", hold)
    monkeypatch.setattr(fc, "POLL_S", 0.05)


def test_the_sdk_request_bodies_match_the_committed_snapshots():
    for name, captured in snapshots().items():
        golden(name, captured)


def test_a_real_sdk_request_creates_a_job_and_the_answer_is_a_chat_completion(db, monkeypatch):
    async def scenario(env):
        await quick(monkeypatch)
        key = await env.new_key()
        r = await replay(env, key, golden("openai_chat_text", snapshots()["openai_chat_text"]))
        assert r.status_code == 200, r.text
        done = ChatCompletion.model_validate(r.json())               # the SDK's own type accepts it
        (job_id, status) = jobs(db, key.id)[0]
        assert (done.id, r.json()["job_id"], status) == ("chatcmpl-%s" % job_id, str(job_id), "queued")
        assert done.choices[0].finish_reason == "length" and done.choices[0].message.content == (
            "still rendering — poll job %s" % job_id)
        assert db.one("SELECT prompt FROM jobs WHERE id = %s", job_id)[0] == "a cat"
        assert charges(db, key_id=key.id) == 1, "a held request that gives up leaves the job charged and running"

    run_app(db, scenario)


def test_a_real_sdk_stream_request_gets_server_sent_events_ending_in_done(db, monkeypatch):
    async def scenario(env):
        await quick(monkeypatch)
        key = await env.new_key()
        r = await replay(env, key, golden("openai_chat_stream", snapshots()["openai_chat_stream"]))
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), r.text
        frames = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
        assert frames[-1] == "[DONE]"
        chunks = [ChatCompletionChunk.model_validate(json.loads(f)) for f in frames[:-1]]
        assert chunks[0].choices[0].delta.role == "assistant"
        assert chunks[-1].choices[0].finish_reason == "length"
        assert len({c.id for c in chunks}) == 1 and chunks[0].id == "chatcmpl-%s" % jobs(db, key.id)[0][0]

    run_app(db, scenario)


def test_a_real_sdk_image_url_part_becomes_an_input_image_of_the_job(db, monkeypatch):
    async def scenario(env):
        await quick(monkeypatch)
        key = await env.new_key()
        r = await replay(env, key, golden("openai_chat_image_url", snapshots()["openai_chat_image_url"]))
        assert r.status_code == 200, r.text
        (job_id, _) = jobs(db, key.id)[0]
        (row,) = db.all("SELECT object_key, size FROM job_media WHERE job_id = %s AND role = 'input'", job_id)
        assert row[1] == len(PNG) and await env.store.read(row[0]) == PNG

    run_app(db, scenario)


def test_a_field_relay_does_not_support_is_400_input_invalid_with_its_name(db):
    async def scenario(env):
        key = await env.new_key()
        for name, param in (("openai_chat_temperature", "temperature"), ("openai_chat_max_tokens", "max_tokens"),
                            ("openai_chat_user", "user")):
            r = await replay(env, key, golden(name, snapshots()[name]))
            assert error_of(r) == (400, "input.invalid", param), (name, r.text)
        assert not jobs(db, key.id) and charges(db, key_id=key.id) == 0

    run_app(db, scenario)


# ---- prompt extraction --------------------------------------------------------------------------------------------
@mutation(OPENAI, "last, at = body.messages[-1], len(body.messages) - 1", "last, at = body.messages[0], 0",
          expect="last message")
def test_the_prompt_is_the_last_message_and_text_parts_join_with_a_newline(db, monkeypatch):
    async def scenario(env):
        await quick(monkeypatch)
        key = await env.new_key()
        body = {"model": VIDEO, "messages": [{**USER, "content": "first"}, {"role": "assistant", "content": "ok"},
                                             {**USER, "content": [{"type": "text", "text": " a "},
                                                                  {"type": "text", "text": "b"}]}]}
        assert (await env.client.post(CHAT, json=body, headers=key.headers)).status_code == 200
        assert db.one("SELECT prompt FROM jobs WHERE key_id = %s", key.id)[0] == "a\nb", "last message"

    run_app(db, scenario)


def test_an_unknown_content_part_a_missing_role_and_a_null_content_are_refused(db):
    async def scenario(env):
        key = await env.new_key()
        for body in ({"model": VIDEO, "messages": [{**USER, "content": [{"type": "input_audio", "input_audio": {}}]}]},
                     {"model": VIDEO, "messages": [{"content": "a"}]},
                     {"model": VIDEO, "messages": [{**USER, "content": None}]}):
            r = await env.client.post(CHAT, json=body, headers=key.headers)
            assert r.status_code == 400 and error_of(r)[1] in ("input.invalid", "input.empty_prompt"), r.text
        assert not jobs(db, key.id)

    run_app(db, scenario)


@mutation(DOORS, 'param = field_param(_field_path(tuple(first.get("loc") or ()), str(first.get("type"))), '
                 'str(first.get("type")))',
          'param = field_param(tuple(first.get("loc") or ()), str(first.get("type")))', expect="content.0")
@mutation(DOORS, 'next((x for x in found if (x.get("loc") or ("",))[-1] != "str"), found[0])', "found[0]",
          expect="content.0")
def test_an_error_inside_a_content_part_names_the_real_field_not_a_union_branch(db):
    part = {"type": "text", "text": "a"}
    cases = (
        ([{**part, "cache_control": {"type": "ephemeral"}}], "messages.0.content.0.cache_control"),
        ([{"type": "input_audio", "input_audio": {}}], "messages.0.content.0.type"),
        ([{"type": "image_url", "image_url": {"url": "x", "bogus": 1}}], "messages.0.content.0.image_url.bogus"),
        ([part, {**part, "oops": 1}], "messages.0.content.1.oops"),
        (5, "messages.0.content"),
    )

    async def scenario(env):
        key = await env.new_key()
        for content, param in cases:
            r = await env.client.post(CHAT, json={"model": VIDEO, "messages": [{**USER, "content": content}]},
                                      headers=key.headers)
            assert error_of(r)[0] == 400 and error_of(r)[2] == param, "content.0 param for %r: %s" % (content, r.text)
        assert not jobs(db, key.id)

    run_app(db, scenario)


# ---- the end of a held request ------------------------------------------------------------------------------------
def test_a_job_that_finishes_while_held_answers_with_its_urls_and_stop(db, monkeypatch):
    async def scenario(env):
        monkeypatch.setattr(fc, "POLL_S", 0.05)
        key = await env.new_key()
        later = finish_later(db, key.id, lambda d, j: finish_done(d, j, 2), delay_s=0.3)
        r = await env.client.post(CHAT, json={"model": VIDEO, "messages": [{**USER, "content": "a"}]},
                                  headers=key.headers)
        await later
        done = ChatCompletion.model_validate(r.json())
        assert done.choices[0].finish_reason == "stop", r.text
        urls = done.choices[0].message.content.splitlines()
        assert len(urls) == 2 and all(u.startswith("http://relay.test/m/tok-") for u in urls), urls
        assert r.json()["job_id"] == str(jobs(db, key.id)[0][0])
        # an image model answers with its images
        key2 = await env.new_key()
        later = finish_later(db, key2.id, finish_done, delay_s=0.2)
        r = await env.client.post(CHAT, json={"model": IMAGE, "messages": [{**USER, "content": "a"}]},
                                  headers=key2.headers)
        await later
        assert r.json()["choices"][0]["message"]["content"].startswith("http://relay.test/m/tok-"), r.text

    run_app(db, scenario)


@mutation(OPENAI, "        raise ApiError(fc.failure_code(state))", "        pass", expect="render.deadline")
def test_a_failed_job_is_an_http_error_with_the_job_code_not_a_200(db, monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(errors.RULES))
    errors.register(errors.Rule("ours.secret", 502, "api_error", True, True, False, False, True, "internal detail",
                                "the request could not be completed"))

    async def scenario(env):
        monkeypatch.setattr(fc, "POLL_S", 0.05)
        key = await env.new_key()
        body = {"model": VIDEO, "messages": [{**USER, "content": "a"}]}
        later = finish_later(db, key.id, lambda d, j: finish_failed(d, j, "render.deadline", "engine secret"), 0.2)
        r = await env.client.post(CHAT, json=body, headers=key.headers)
        await later
        assert error_of(r) == (504, "render.deadline", None), "the failed job must be an HTTP error: %s" % r.text
        assert set(r.json()["error"]) == {"code", "message", "type", "param"} and "secret" not in r.text
        job_id = jobs(db, key.id)[0][0]
        status = (await env.client.get("/v1/video/generations/%s" % job_id, headers=key.headers)).json()
        assert status["status"] == "failed" and status["refunded"] is True, "the refund state is in GET status"
        later = finish_later(db, key.id, lambda d, j: finish_failed(d, j, "ours.secret", "engine secret"), 0.2,
                             which=1)
        r = await env.client.post(CHAT, json=body, headers=key.headers)
        await later
        assert error_of(r)[1] == "internal.unexpected" and r.json()["error"]["message"] == (
            "the request could not be completed"), r.text

    run_app(db, scenario)


def test_a_refused_create_is_a_real_http_error_even_with_stream_true(db):
    async def scenario(env):
        key = await env.new_key(models=("fake-image",))
        for body, expected in (({"model": "nope", "messages": [{**USER, "content": "a"}], "stream": True},
                                (400, "input.bad_model")),
                               ({"model": VIDEO, "messages": [{**USER, "content": "a"}], "stream": True},
                                (403, "auth.model_forbidden")),
                               ({"model": IMAGE, "messages": [{**USER, "content": ""}], "stream": True},
                                (400, "input.empty_prompt"))):
            r = await env.client.post(CHAT, json=body, headers=key.headers)
            assert error_of(r)[:2] == expected, r.text
            assert r.headers["content-type"].startswith("application/json")
        assert not jobs(db, key.id)

    run_app(db, scenario)
