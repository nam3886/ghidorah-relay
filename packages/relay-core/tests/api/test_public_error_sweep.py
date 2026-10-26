"""A failed job through every public surface (owner's ruling 26/10): the native status, the webhook, the OpenAI and
Gemini facades held and streamed. Each shows only a neutral public code and its customer text, never the internal
code, its message or an internal word; the job row, the request log and the admin keep the internal code."""

import json
import re

import httpx
from apikit import request_logs, run_app
from doorkit import VIDEO, finish_failed, finish_later, live
from hookkit import URL, resolver
from test_errors import INTERNAL_WORDS
from test_runners import Receiver
from test_streaming import CHAT_BODY, GEN_BODY, GEN_STREAM, collect, quick

from relay_core import errors
from relay_core.api import facade_common as fc
from relay_core.jobs import webhooks
from relay_core.testing.mutation import mutation

OPENAI = "packages/relay-core/src/relay_core/api/routes_openai.py"
GEMINI = "packages/relay-core/src/relay_core/api/routes_gemini.py"
ERR = "packages/relay-core/src/relay_core/api/errors_http.py"
LOG = "packages/relay-core/src/relay_core/api/request_log.py"
DOORS = "packages/relay-core/src/relay_core/api/doors.py"
VIDEO_PATH = "/v1/video/generations"
CHAT = "/v1/chat/completions"
GEN = "/v1beta/models/%s:generateContent" % VIDEO
DETAIL = "engine said: the session cookie died on acct-7 via proxy 10.1.2.3"
# One internal code of each kind: platform, each provider, a refusal, a hidden one, the fallback.
CODES = ("account.dead_session", "render.deadline", "source.account_unavailable", "queue.never_started",
         "qwen.waf_refused", "flow.captcha_failed", "flow.captcha_key_invalid", "dola.refused_on_accounts",
         "dola.ui_send_failed", "gemini.not_entitled", "vibes.source_missing", "meta.no_image", errors.INTERNAL)


def leaks(text: str, code: str, said: str = "") -> list[str]:
    """What of `code`'s internals a public answer `text` shows: an internal code, the code's message, the engine
    detail anywhere; an internal word in `said`, the error's own code and text (ids and times are not words)."""
    r = errors.rule(code)
    inner = [c for c in errors.RULES if c.split(".")[0] not in errors.CLIENT_DOMAINS and c in text]
    word = INTERNAL_WORDS.search(said)
    shown = [w for w in (r.message, DETAIL, "acct-7", "10.1.2.3") if w in text]
    return inner + shown + ([word.group(0)] if word else [])


def neutral(code: str, shown: str, message: str, text: str, where: str) -> None:
    public, sentence = errors.shown(code)
    assert shown == public and shown in errors.PUBLIC_CODES, "%s: %s is shown as %s" % (where, code, shown)
    assert message == sentence, "%s: %s shows its customer sentence, not %r" % (where, code, message)
    found = leaks(text, code, shown + " " + message)
    assert not found, "%s: %s leaks %s" % (where, code, found)


def later_failed(db, key, code: str, which: int):
    return finish_later(db, key.id, lambda d, j: finish_failed(d, j, code, DETAIL), 0.2, which=which)


@mutation(OPENAI, "        raise ApiError(fc.failure_code(state))",
          "        return JSONResponse({'error': {'code': fc.failure_code(state), 'message': ''}}, status_code=500)",
          expect="is shown as")
@mutation(ERR, "    shown_code, message = shown(code)\n    status = http_status(code)",
          "    shown_code, message = code, rule(code).message\n    status = http_status(code)", expect="is shown as")
@mutation(LOG, 'state.get("error_code") or _error_code(answer)', "_error_code(answer)",
          expect="the request log keeps the internal code")
@mutation(DOORS, 'errors.shown(code or errors.INTERNAL) if status == "failed"', '(code, code) if status == "failed"',
          expect="is shown as")
def test_a_failed_job_shows_only_a_neutral_code_on_the_status_the_webhook_and_the_held_facades(db, monkeypatch):
    async def scenario(env):
        monkeypatch.setattr(fc, "POLL_S", 0.05)
        key, h = await env.new_key(), await env.admin_headers()
        for n, code in enumerate(CODES):
            job = (await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a cat"},
                                         headers=key.headers)).json()["id"]
            db.run("UPDATE jobs SET callback_url = %s WHERE id = %s", URL, job)
            await finish_failed(db, job, code, DETAIL)
            status = await env.client.get("%s/%s" % (VIDEO_PATH, job), headers=key.headers)
            neutral(code, status.json()["error_code"], status.json()["error"], status.text, "status")
            assert status.json()["refunded"] is True

            rx = Receiver(204)
            await webhooks.deliver_due(env.pool, resolver=resolver, transport=rx.transport)
            (_, headers, body), = rx.seen
            assert set(body) == {"event", "event_id", "job_id", "status"} and body["status"] == "failed", body
            assert not leaks(json.dumps(body) + json.dumps(headers), code), "webhook: %s" % code

            admin = (await env.client.get("/admin/jobs/%s" % job, headers=h)).json()
            assert (admin["error_code"], admin["status"]) == (code, "failed"), "the admin keeps the internal code"
            assert admin["raw_response"]["body"]["error_code"] == errors.shown(code)[0], "raw: what the client saw"

            later = later_failed(db, key, code, 3 * n + 1)
            r = await env.client.post(CHAT, json={**CHAT_BODY, "stream": False}, headers=key.headers)
            await later
            assert r.status_code == errors.http_status(code) and r.headers["x-should-retry"] == "false", r.text
            neutral(code, r.json()["error"]["code"], r.json()["error"]["message"], r.text, "chat")

            later = later_failed(db, key, code, 3 * n + 2)
            r = await env.client.post(GEN, json=GEN_BODY, headers=key.headers)
            await later
            err = r.json()["error"]
            neutral(code, err["details"][0]["reason"], err["message"], r.text, "gemini")
        return key

    key = run_app(db, scenario)
    logged = [row["error_code"] for row in request_logs(db) if row["status_code"] >= 400]
    assert logged == [c for c in CODES for _ in range(2)], "the request log keeps the internal code: %s" % logged
    stored = [r[0] for r in db.all("SELECT error_code FROM jobs WHERE key_id = %s ORDER BY created_at", key.id)]
    assert stored == [c for c in CODES for _ in range(3)], "the job row keeps the internal code"


@mutation(OPENAI, "yield fc.sse(errors.envelope(fc.failure_code(value)))",
          "yield fc.sse({'error': {'code': fc.failure_code(value), 'message': ''}})", expect="is shown as")
@mutation(GEMINI, "yield fc.sse(gemini_body(fc.failure_code(value)))",
          "yield fc.sse({'error': {'message': '', 'details': [{'reason': fc.failure_code(value)}]}})",
          expect="is shown as")
def test_a_failed_job_ends_both_streams_with_only_a_neutral_code(db, monkeypatch):
    streamed = ("qwen.waf_refused", "dola.ui_send_failed", "account.dead_session")

    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            for n, code in enumerate(streamed):
                later = later_failed(db, key, code, 2 * n)
                frames = await collect(client, CHAT, key, CHAT_BODY)
                await later
                (err,) = [f["error"] for f in frames if isinstance(f, dict) and "error" in f]
                neutral(code, err["code"], err["message"], json.dumps(frames, ensure_ascii=False), "chat stream")
                later = later_failed(db, key, code, 2 * n + 1)
                frames = await collect(client, GEN_STREAM, key, GEN_BODY)
                await later
                err = frames[-1]["error"]
                neutral(code, err["details"][0]["reason"], err["message"], json.dumps(frames, ensure_ascii=False),
                        "gemini stream")

    run_app(db, scenario)


def test_the_leak_check_itself_sees_an_internal_code_word_and_detail():
    assert leaks('{"code": "qwen.waf_refused"}', "qwen.waf_refused")
    assert leaks("", "render.deadline", "the account was logged out")
    assert leaks(DETAIL, "render.deadline")
    assert not leaks(json.dumps(errors.envelope("qwen.waf_refused")), "qwen.waf_refused")
    assert re.search(INTERNAL_WORDS, "captcha")
