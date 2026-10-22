"""Every error leaves in one shape: the OpenAI envelope, or the Gemini body on Gemini doors."""

from apikit import run_app
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from relay_core import errors
from relay_core.api.deps import require_key
from relay_core.api.errors_http import ApiError, gemini_body
from relay_core.errors import Rule
from relay_core.testing.mutation import mutation

ERR = "packages/relay-core/src/relay_core/api/errors_http.py"
CORE_ERRORS = "packages/relay-core/src/relay_core/errors.py"
HIDDEN = "secret.ui_chip_row_missing"
LEAK = "secret upstream text sk-live-1234567890abcdef"


class Echo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration: int = 5
    ratio: str = "16:9"


def routes() -> APIRouter:
    r = APIRouter()

    @r.get("/v1/code/{code}")
    async def raise_code(code: str, param: str | None = None):
        raise ApiError(code, param)

    @r.get("/v1beta/code/{code}")
    async def raise_gemini_code(code: str, param: str | None = None):
        raise ApiError(code, param)

    @r.get("/v1/crash")
    async def crash():
        raise RuntimeError(LEAK)

    @r.post("/v1/echo")
    async def echo(body: Echo, key=Depends(require_key)):
        return {"duration": body.duration}

    return r


def hidden_rule(monkeypatch):
    monkeypatch.setitem(errors.RULES, HIDDEN, Rule(HIDDEN, 500, "api_error", False, False, False, False, True,
                                                   "the chip row did not appear", "the render failed on our side"))


@mutation(CORE_ERRORS, '"param": param}}', '"param": param, "retryable": False}}',
          expect="retryable and refunded are not sent")
@mutation(CORE_ERRORS, '"type": rule(shown_code).error_type', '"type": "api_error"', expect="authentication_error")
def test_an_error_has_exactly_code_message_type_param(db):
    async def scenario(env):
        r = await env.client.get("/v1/code/auth.invalid_key")
        assert set(r.json()["error"]) == {"code", "message", "type", "param"}, "retryable and refunded are not sent"
        assert r.status_code == 401
        assert r.json() == {"error": {"code": "auth.invalid_key", "message": "the API key is not valid",
                                      "type": "authentication_error", "param": None}}
        r = await env.client.get("/v1/code/quota.key_exceeded", params={"param": "model"})
        assert r.status_code == 429
        assert r.json() == {"error": {"code": "quota.key_exceeded", "message": "the API key's limit is reached",
                                      "type": "rate_limit_error", "param": "model"}}
        assert set(r.json()["error"]) == {"code", "message", "type", "param"}

    run_app(db, scenario, routers=[routes()])


@mutation(ERR, "@app.exception_handler(StarletteHTTPException)", "@app.exception_handler(KeyError)",
          expect='"resource.not_found"')
@mutation(ERR, 'status=exc.status_code if code == "input.invalid" else None', "status=None", expect="405")
def test_framework_404_and_405_use_the_envelope(db):
    async def scenario(env):
        r = await env.client.get("/v1/no-such-route")
        assert r.status_code == 404
        assert r.json() == {"error": {"code": "resource.not_found", "message": "the resource does not exist",
                                      "type": "not_found_error", "param": None}}
        r = await env.client.post("/healthz")
        assert r.status_code == 405 and r.headers["allow"] == "GET"
        assert r.json()["error"]["code"] == "input.invalid" and "detail" not in r.json()

    run_app(db, scenario, routers=[routes()])


@mutation(ERR, 'return clean_param(".".join(parts) or None)', "return None", expect="the bad field is named")
@mutation(ERR, '_FIELD_CODE = {"duration": "input.bad_duration"', '_FIELD_CODE = {"duration": "input.invalid"',
          expect='"input.bad_duration"')
def test_a_422_names_the_first_bad_field_and_maps_it_to_its_code(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post("/v1/echo", json={"duration": "soon", "ratio": 5}, headers=key.headers)
        assert r.status_code == 400
        assert r.json()["error"]["param"] == "duration", "the bad field is named"
        message = errors.rule("input.bad_duration").message
        assert r.json() == {"error": {"code": "input.bad_duration", "message": message,
                                      "type": "invalid_request_error", "param": "duration"}}
        r = await env.client.post("/v1/echo", json={"duration": 5, "surprise": 1}, headers=key.headers)
        assert r.status_code == 400
        assert (r.json()["error"]["code"], r.json()["error"]["param"]) == ("input.invalid", "surprise")
        broken = {**key.headers, "content-type": "application/json"}
        r = await env.client.post("/v1/echo", content=b"{not json", headers=broken)
        assert r.status_code == 400 and r.json()["error"]["param"] is None

    run_app(db, scenario, routers=[routes()])


@mutation(CORE_ERRORS, "if r.public else (INTERNAL, r.hidden_message)", "if True else (INTERNAL, r.hidden_message)",
          expect="internal.unexpected")
def test_a_non_public_code_is_shown_as_internal_with_its_hidden_message(db, monkeypatch):
    hidden_rule(monkeypatch)

    async def scenario(env):
        r = await env.client.get("/v1/code/" + HIDDEN)
        assert r.status_code == 500
        assert r.json() == {"error": {"code": "internal.unexpected", "message": "the render failed on our side",
                                      "type": "api_error", "param": None}}
        assert "chip" not in r.text and HIDDEN not in r.text

    run_app(db, scenario, routers=[routes()])


@mutation(ERR, "return error_response(path, INTERNAL)", 'return error_response(path, "resource.not_found")',
          expect="a crash answers internal.unexpected")
def test_a_crash_is_the_internal_envelope_and_never_shows_the_exception_text(db):
    async def scenario(env):
        r = await env.client.get("/v1/crash")
        assert r.status_code == 500, "a crash answers internal.unexpected"
        assert r.json()["error"]["code"] == "internal.unexpected"
        assert "sk-live" not in r.text and "secret upstream" not in r.text

    run_app(db, scenario, routers=[routes()])


@mutation(ERR, '"domain": "relay"', '"domain": "other"', expect="domain is relay")
@mutation(ERR, 'if status is not None and is_gemini_path(path):', "if False:", expect='["code"] == 405')
def test_a_gemini_door_answers_in_the_gemini_shape(db):
    async def scenario(env):
        r = await env.client.get("/v1beta/code/input.bad_ratio", params={"param": "ratio"})
        assert r.status_code == 400
        assert r.json()["error"]["details"][0]["domain"] == "relay", "domain is relay"
        assert r.json() == {"error": {"code": 400, "message": "the aspect ratio is not supported by this model",
                                      "status": "INVALID_ARGUMENT",
                                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                                   "reason": "input.bad_ratio", "domain": "relay",
                                                   "metadata": {"param": "ratio"}}]}}
        r = await env.client.get("/v1beta/no-such-route")
        assert r.status_code == 404 and r.json()["error"]["status"] == "NOT_FOUND"
        assert r.json()["error"]["details"][0]["reason"] == "resource.not_found"
        r = await env.client.post("/v1beta/code/job.busy")
        assert r.status_code == 405
        assert r.json()["error"]["code"] == 405 and r.json()["error"]["status"] == "INVALID_ARGUMENT"
        assert "refunded" not in r.text

    run_app(db, scenario, routers=[routes()])


def test_the_gemini_helper_masks_a_non_public_code_the_same_way(monkeypatch):
    hidden_rule(monkeypatch)
    body = gemini_body(HIDDEN)
    assert body["error"]["message"] == "the render failed on our side"
    assert body["error"]["details"][0]["reason"] == "internal.unexpected" and body["error"]["code"] == 500


@mutation(CORE_ERRORS, '"param": param}}', '"param": param, "refunded": False}}',
          expect="every error body is exactly the envelope")
def test_every_error_body_across_the_doors_and_the_admin_is_exactly_the_envelope(db):
    chat = {"model": "fake-video", "messages": [{"role": "user", "content": "x"}]}
    gemini = {"contents": [{"role": "user", "parts": [{"text": "x"}]}]}
    failing = [                                   # (method, path, body, who)
        ("POST", "/v1/video/generations", {"model": "fake-video", "prompt": "x", "zzz": 1}, "key"),
        ("POST", "/v1/video/generations", {"model": "nope", "prompt": "x"}, "key"),
        ("POST", "/v1/video/generations", {"model": "fake-video", "prompt": ""}, "key"),
        ("POST", "/v1/video/generations", {"model": "fake-video", "prompt": "x"}, None),
        ("POST", "/v1/images/generations", {"model": "fake-image", "prompt": "x", "duration": 5}, "key"),
        ("GET", "/v1/video/generations/not-an-id", None, "key"),
        ("POST", "/v1/chat/completions", {**chat, "temperature": 1}, "key"),
        ("POST", "/v1/chat/completions", chat, None),
        ("GET", "/v1/no-such-route", None, "key"),
        ("DELETE", "/v1/models", None, "key"),
        ("GET", "/m/" + "a" * 40, None, None),
        ("POST", "/admin/keys", {"zzz": 1}, "admin"),
        ("GET", "/admin/jobs/not-an-id", None, "admin"),
        ("GET", "/admin/me", None, None),
        ("POST", "/admin/login", {"username": "x", "password": "y" * 12}, None),
    ]
    gemini_failing = [
        ("POST", "/v1beta/models/fake-video:generateContent", {**gemini, "zzz": 1}, "key"),
        ("POST", "/models/fake-video:generateContent", gemini, None),
        ("GET", "/v1beta/models/nope", None, "key"),
    ]

    async def scenario(env):
        headers = {"key": (await env.new_key()).headers, "admin": await env.admin_headers(), None: {}}
        seen = 0
        for gem, cases in ((False, failing), (True, gemini_failing)):
            for method, path, body, who in cases:
                r = await env.client.request(method, path, json=body, headers=headers[who])
                assert r.status_code >= 400, (method, path, r.status_code)
                err = r.json()["error"]
                want = {"code", "message", "status", "details"} if gem else {"code", "message", "type", "param"}
                assert set(err) == want, "every error body is exactly the envelope: %s %s %s" % (method, path, err)
                assert "retryable" not in r.text and "refunded" not in r.text
                seen += 1
        assert seen == len(failing) + len(gemini_failing)

    run_app(db, scenario)
