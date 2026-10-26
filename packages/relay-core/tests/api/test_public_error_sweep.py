"""Every error a client can get, through every public surface (owner's ruling 26/10). A failed job, for EVERY
job-ending rule of the registry (core and every provider): the native status, the webhook, the OpenAI and Gemini
facades held and streamed. Every registered code raised at request time, on both error shapes, and the paths that
bypass a route's own `ApiError`: a validation error (422 -> 400), a raw HTTPException, a 404, a 405 and a crash. Each
whole answer shows only a neutral public code (or a client code as itself) with its customer text: never an internal
code, its message, the engine detail or an internal word. The job row, the request log and the admin keep the
internal code."""

import json
import re

import httpx
from apikit import request_logs, run_app
from doorkit import VIDEO, finish_failed, finish_later, live
from fastapi import APIRouter
from hookkit import URL, resolver
from starlette.exceptions import HTTPException
from test_errors import INTERNAL_WORDS, PROVIDER_SPECS
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
CORE = "packages/relay-core/src/relay_core/errors.py"
VIDEO_PATH = "/v1/video/generations"
CHAT = "/v1/chat/completions"
GEN = "/v1beta/models/%s:generateContent" % VIDEO
DETAIL = "engine said: the session cookie died on acct-7 via proxy 10.1.2.3"
assert PROVIDER_SPECS, "the provider codes must be registered before the sweep is built"
# Every rule a job can end `failed` with, the hidden ones and the fallback included.
ENDING = tuple(sorted(c for c, r in errors.RULES.items() if errors.ends_job(r)))


def leaks(text: str, code: str) -> list[str]:
    """What of `code`'s internals the whole serialized answer `text` shows: an internal code, the code's own message
    when it is not shown as itself, the engine detail, an internal word anywhere."""
    r = errors.rule(code)
    inner = [c for c in errors.RULES if c.split(".")[0] not in errors.CLIENT_DOMAINS and c in text]
    own = [r.message] if errors.shown(code)[0] != code else []
    detail = [w for w in (*own, DETAIL, "acct-7", "10.1.2.3") if w in text]
    word = INTERNAL_WORDS.search(text)
    return inner + detail + ([word.group(0)] if word else [])


def neutral(code: str, shown: str, message: str, text: str, where: str) -> None:
    """The answer to `code` shows exactly what `errors.shown` allows, and nothing internal anywhere in it."""
    public, sentence = errors.shown(code)
    allowed = public in errors.PUBLIC_CODES or (public == code and code.split(".")[0] in errors.CLIENT_DOMAINS)
    assert shown == public and allowed, "%s: %s is shown as %s" % (where, code, shown)
    assert message == sentence, "%s: %s shows its customer sentence, not %r" % (where, code, message)
    found = leaks(text, code)
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
def test_every_job_ending_code_shows_only_a_neutral_code_on_the_status_webhook_and_held_facades(db, monkeypatch):
    assert len(ENDING) > 40 and {"render.deadline", "gemini.limit_reached", errors.INTERNAL} <= set(ENDING)

    async def scenario(env):
        monkeypatch.setattr(fc, "POLL_S", 0.05)
        key, h = await env.new_key(), await env.admin_headers()
        for n, code in enumerate(ENDING):
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
            sent = json.dumps(body) + json.dumps(headers)
            assert not leaks(sent, code), "webhook: %s leaks %s" % (code, leaks(sent, code))

            admin = (await env.client.get("/admin/jobs/%s" % job, headers=h)).json()
            assert (admin["error_code"], admin["status"]) == (code, "failed"), "the admin keeps the internal code"
            assert admin["raw_response"]["body"]["error_code"] == errors.shown(code)[0], "raw: what the client saw"

            later = later_failed(db, key, code, 3 * n + 1)
            r = await env.client.post(CHAT, json={**CHAT_BODY, "stream": False}, headers=key.headers)
            await later
            neutral(code, r.json()["error"]["code"], r.json()["error"]["message"], r.text, "chat")
            assert r.status_code == errors.http_status(code) and r.headers["x-should-retry"] == "false", r.text

            later = later_failed(db, key, code, 3 * n + 2)
            r = await env.client.post(GEN, json=GEN_BODY, headers=key.headers)
            await later
            err = r.json()["error"]
            neutral(code, err["details"][0]["reason"], err["message"], r.text, "gemini")
        return key

    key = run_app(db, scenario)
    logged = [row["error_code"] for row in request_logs(db) if row["status_code"] >= 400]
    assert logged == [c for c in ENDING for _ in range(2)], "the request log keeps the internal code: %s" % logged
    stored = [r[0] for r in db.all("SELECT error_code FROM jobs WHERE key_id = %s ORDER BY created_at", key.id)]
    assert stored == [c for c in ENDING for _ in range(3)], "the job row keeps the internal code"


@mutation(OPENAI, "yield fc.sse(errors.envelope(fc.failure_code(value)))",
          "yield fc.sse({'error': {'code': fc.failure_code(value), 'message': ''}})", expect="is shown as")
@mutation(GEMINI, "yield fc.sse(gemini_body(fc.failure_code(value)))",
          "yield fc.sse({'error': {'message': '', 'details': [{'reason': fc.failure_code(value)}]}})",
          expect="is shown as")
def test_every_job_ending_code_ends_both_streams_with_only_a_neutral_code(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            for n, code in enumerate(ENDING):
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


def routes() -> APIRouter:
    r = APIRouter()
    from relay_core.api.errors_http import ApiError

    for prefix in ("/v1", "/v1beta"):
        @r.get(prefix + "/sweep/code/{code}")
        async def raise_code(code: str):
            raise ApiError(code, "model")

        @r.get(prefix + "/sweep/raw/{status}")
        async def raise_raw(status: int):
            raise HTTPException(status_code=status, detail=DETAIL)

        @r.get(prefix + "/sweep/crash")
        async def crash():
            raise RuntimeError(DETAIL)
    return r


def public_error(r: httpx.Response) -> tuple[str, str]:
    """(code, message) of either error shape."""
    err = r.json()["error"]
    return (err["details"][0]["reason"] if isinstance(err["code"], int) else err["code"]), err["message"]


@mutation(CORE, "    shown_code, message = (r.code, r.message) if internal else shown(code)\n",
          "    shown_code, message = r.code, r.message\n", expect="is shown as")
@mutation(ERR, "envelope(code, param, internal=admin)", "envelope(code, param, internal=True)", expect="is shown as")
def test_every_request_time_code_and_every_bypass_path_shows_only_a_public_code(db):
    async def scenario(env):
        key = await env.new_key()
        for prefix in ("/v1", "/v1beta"):
            for code in sorted(errors.RULES):
                r = await env.client.get("%s/sweep/code/%s" % (prefix, code), headers=key.headers)
                shown, message = public_error(r)
                neutral(code, shown, message, r.text, "request-time %s" % prefix)
                assert r.status_code == errors.http_status(code), r.text
            bypass = [
                ("validation", await env.client.post(prefix + ("/models/%s:generateContent" % VIDEO if prefix ==
                                                               "/v1beta" else "/video/generations"),
                                                     json={"model": [DETAIL], "prompt": {"x": DETAIL},
                                                           "contents": DETAIL}, headers=key.headers)),
                ("raw 418", await env.client.get(prefix + "/sweep/raw/418", headers=key.headers)),
                ("raw 500", await env.client.get(prefix + "/sweep/raw/500", headers=key.headers)),
                ("404", await env.client.get(prefix + "/no-such-route", headers=key.headers)),
                ("405", await env.client.delete(prefix + "/sweep/crash", headers=key.headers)),
                ("crash", await env.client.get(prefix + "/sweep/crash", headers=key.headers))]
            for where, r in bypass:
                assert r.status_code >= 400, (where, r.text)
                shown, _ = public_error(r)
                allowed = shown in errors.PUBLIC_CODES or shown.split(".")[0] in errors.CLIENT_DOMAINS
                assert allowed, "bypass %s %s: shown as %s" % (prefix, where, shown)
                found = [w for w in (DETAIL, "acct-7", "10.1.2.3") if w in r.text]
                word = INTERNAL_WORDS.search(r.text)
                assert not found and not word, "bypass %s %s leaks %s %s" % (prefix, where, found, word)

    run_app(db, scenario, routers=[routes()])


def test_the_leak_check_itself_sees_an_internal_code_word_and_detail():
    assert leaks('{"code": "qwen.waf_refused"}', "qwen.waf_refused")
    assert leaks('{"id": "1", "message": "the account was logged out"}', "render.deadline")
    assert leaks(DETAIL, "render.deadline")
    assert not leaks(json.dumps(errors.envelope("qwen.waf_refused")), "qwen.waf_refused")
    assert not leaks(json.dumps(errors.envelope("input.bad_model")), "input.bad_model")
    assert re.search(INTERNAL_WORDS, "captcha")
