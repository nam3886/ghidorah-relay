"""The request log (contracts §4.4, §13): who is logged, what is stored, what never is."""

import logging
import uuid

from apikit import request_logs, run_app
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from starlette.requests import ClientDisconnect

from relay_core.api.deps import require_key, skip_body_log
from relay_core.api.errors_http import ApiError
from relay_core.testing.mutation import mutation

LOG = "packages/relay-core/src/relay_core/api/request_log.py"
ERR = "packages/relay-core/src/relay_core/api/errors_http.py"
JOB = uuid.UUID("11111111-2222-3333-4444-555555555555")
SECRET = "super-secret-cookie-value-0123456789"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = ""


def routes() -> APIRouter:
    r = APIRouter()

    @r.post("/v1/jobs")
    async def create(request: Request, body: dict, key=Depends(require_key)):
        request.state.job_id, request.state.provider = JOB, "fake"
        return {"id": str(JOB)}

    @r.post("/v1/jobs/refused")
    async def refused(body: dict, key=Depends(require_key)):
        raise ApiError("input.bad_ratio", "ratio")

    @r.post("/v1/strict")
    async def strict(body: Strict, key=Depends(require_key)):
        return {"ok": True}

    @r.get("/v1/read/{n}")
    async def read(n: int, key=Depends(require_key)):
        if n == 0:
            raise ApiError("resource.not_found")
        return {"n": n}

    @r.get("/v1/crash")
    async def crash(key=Depends(require_key)):
        raise RuntimeError(SECRET)

    @r.post("/v1/hangup")
    async def hangup(body: dict, key=Depends(require_key)):
        raise ClientDisconnect()

    @r.post("/v1/credential", dependencies=[Depends(skip_body_log)])
    async def credential(body: dict, key=Depends(require_key)):
        return {"ok": True}

    return r


@mutation(LOG, "redact_log(parsed) if isinstance", 'parsed if isinstance', expect="the password is masked")
@mutation(LOG, "redact_log(parsed) if isinstance",
          '__import__("relay_core.redact", fromlist=["redact"]).redact(parsed) if isinstance',
          expect="long values are omitted")
def test_a_request_is_logged_with_its_body_redacted_and_long_values_omitted(db):
    long_prompt = "p" * 2500
    image = "data:image/png;base64," + "A" * 5000

    async def scenario(env):
        key = await env.new_key()
        body = {"prompt": long_prompt, "model": "fake-video", "images": [image],
                "metadata": {"password": "hunter2hunter2", "note": "sessionid=abcdef0123456789abcdef"}}
        r = await env.client.post("/v1/jobs", json=body, headers=key.headers)
        assert r.status_code == 200
        [row] = request_logs(db)
        assert (row["key_id"], row["provider"], row["route"], row["status_code"], row["job_id"], row["error_code"]) \
            == (key.id, "fake", "POST /v1/jobs", 200, JOB, None)
        assert row["response_redacted"] is None
        stored = row["body_redacted"]
        assert stored["model"] == "fake-video"
        assert stored["metadata"]["password"] == "[redacted]", "the password is masked"
        assert stored["prompt"] == "[omitted len=2500]", "long values are omitted"
        assert stored["images"] == ["[omitted len=%d]" % len(image)]
        assert "abcdef0123456789abcdef" not in str(stored)
        assert key.raw not in str(row), "the key itself is never logged"

    run_app(db, scenario, routers=[routes()])


@mutation(LOG, 'if key_id is None or (', "if False or (", expect="no valid key")
@mutation(LOG, "(scope[\"method\"] in _READS and status < 400)", "False", expect="reads that worked")
def test_only_a_known_key_is_logged_and_a_read_that_worked_is_not(db):
    async def scenario(env):
        for headers in ({}, {"Authorization": "Bearer rk-" + "x" * 43}):
            await env.client.post("/v1/jobs", json={"prompt": "hi"}, headers=headers)
        await env.client.get("/v1/no-such-route")
        await env.client.post("/admin/login", json={"username": "a", "password": "b"})
        assert request_logs(db) == [], "no valid key, no row"
        key = await env.new_key()
        assert (await env.client.get("/v1/read/1", headers=key.headers)).status_code == 200
        assert request_logs(db) == [], "reads that worked are not logged"
        assert (await env.client.get("/v1/read/0", headers=key.headers)).status_code == 404
        [row] = request_logs(db)
        assert (row["route"], row["status_code"], row["error_code"]) == ("GET /v1/read/{n}", 404, "resource.not_found")

    run_app(db, scenario, routers=[routes()])


@mutation("packages/relay-core/src/relay_core/api/deps.py",
          "        request.state.key_id = e.key_id ", "        pass  # ", expect="a disabled key is logged")
def test_a_disabled_key_is_logged_under_that_key(db):
    """The admin sees a client that keeps calling after its key was disabled; a forged key still logs nothing."""
    async def scenario(env):
        key = await env.new_key(enabled=False)
        r = await env.client.post("/v1/jobs", json={"prompt": "hi"}, headers=key.headers)
        assert r.status_code == 403
        rows = request_logs(db)
        assert [(x["key_id"], x["status_code"], x["error_code"]) for x in rows] \
            == [(key.id, 403, "auth.key_disabled")], "a disabled key is logged under that key: %r" % rows
        assert key.raw not in str(rows)

    run_app(db, scenario, routers=[routes()])


@mutation(LOG, 'Jsonb(redact_log(answer)) if answer is not None else None', "None", expect="input.bad_ratio")
def test_a_failure_before_a_job_exists_is_logged_with_its_error_and_a_crash_with_the_internal_code(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post("/v1/jobs/refused", json={"prompt": "hi"}, headers=key.headers)
        assert r.status_code == 400
        r = await env.client.get("/v1/crash", headers=key.headers)
        assert r.status_code == 500
        refused, crash = request_logs(db)
        assert (refused["status_code"], refused["error_code"], refused["body_redacted"]) \
            == (400, "input.bad_ratio", {"prompt": "hi"})
        assert refused["response_redacted"]["error"]["param"] == "ratio" and "input.bad_ratio" in str(refused)
        assert (crash["status_code"], crash["error_code"]) == (500, "internal.unexpected")
        assert SECRET not in str(crash), "the exception text never reaches the log row"

    run_app(db, scenario, routers=[routes()])


@mutation(ERR, "        await resolve_key_quietly(request)\n        first = ", "        first = ",
          expect="a 422 with a valid key is logged")
def test_a_422_is_logged_only_with_a_valid_key(db):
    broken = {"content-type": "application/json"}

    async def scenario(env):
        for headers in (broken, {**broken, "Authorization": "Bearer rk-" + "y" * 43}):
            r = await env.client.post("/v1/strict", content=b"{not json", headers=headers)
            assert r.status_code == 400
        assert request_logs(db) == [], "broken JSON without a valid key is not written"
        key = await env.new_key()
        r = await env.client.post("/v1/strict", content=b"{not json", headers={**broken, **key.headers})
        assert r.status_code == 400
        r = await env.client.post("/v1/strict", json={"prompt": "x", "extra": 1}, headers=key.headers)
        assert r.status_code == 400
        rows = request_logs(db)
        assert len(rows) == 2, "a 422 with a valid key is logged"
        unparsed, extra = rows
        assert (unparsed["status_code"], unparsed["error_code"], unparsed["body_redacted"]) \
            == (400, "input.invalid", {"_unparsed": True}), "a 422 with a valid key is logged"
        assert (extra["error_code"], extra["response_redacted"]["error"]["param"]) == ("input.invalid", "extra")
        assert extra["body_redacted"] == {"prompt": "x", "extra": 1}

    run_app(db, scenario, routers=[routes()])


@mutation(ERR, "        await resolve_key_quietly(request)\n        code = _BY_STATUS", "        code = _BY_STATUS",
          expect="a wrong path or method with a known key is logged")
def test_a_wrong_path_or_method_with_a_known_key_is_logged_under_it(db):
    """No route resolves the key of a 404 or 405 the router makes itself; the request log still names it (D52), so
    the admin sees a client calling a path that does not exist. Without a known key nothing is written."""
    async def scenario(env):
        forged = {"Authorization": "Bearer rk-" + "z" * 43}
        assert (await env.client.get("/v1/no-such-route", headers=forged)).status_code == 404
        assert request_logs(db) == [], "an unknown key on a wrong path writes nothing"
        key = await env.new_key()
        assert (await env.client.get("/v1/no-such-route", headers=key.headers)).status_code == 404
        assert (await env.client.delete("/v1/jobs", headers=key.headers)).status_code == 405
        rows = [(r["key_id"], r["route"], r["status_code"], r["error_code"]) for r in request_logs(db)]
        assert rows == [(key.id, "GET /v1/no-such-route", 404, "resource.not_found"),
                        (key.id, "DELETE /v1/jobs", 405, "input.invalid")], (
            "a wrong path or method with a known key is logged: %r" % rows)

    run_app(db, scenario, routers=[routes()])


@mutation(LOG, 'if state.get("log_body") is False:', "if False:", expect="super-secret")
def test_a_credential_route_keeps_its_line_but_never_its_body(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post("/v1/credential", json={"cookie": [{"name": "k", "value": SECRET}],
                                                          "note": "super-secret"}, headers=key.headers)
        assert r.status_code == 200
        [row] = request_logs(db)
        assert (row["route"], row["status_code"], row["body_redacted"]) == ("POST /v1/credential", 200, {})
        dump = " ".join(c for r in db.all("SELECT body_redacted::text, coalesce(response_redacted::text, '') "
                                          "FROM request_logs") for c in r)
        assert "super-secret" not in dump and SECRET not in dump

    run_app(db, scenario, routers=[routes()])


@mutation(LOG, "MAX_BODY_BYTES = 1 << 20", "MAX_BODY_BYTES = 1 << 40", expect="[omitted len=")
def test_a_body_too_large_to_parse_is_logged_as_its_size(db):
    async def scenario(env):
        key = await env.new_key()
        big = {"prompt": "x", "blob": ["abcdefghij" * 100] * 1500}        # about 1.5 MB of JSON
        r = await env.client.post("/v1/jobs", json=big, headers=key.headers)
        assert r.status_code == 200
        [row] = request_logs(db)
        assert row["body_redacted"]["_body"].startswith("[omitted len="), row["body_redacted"]

    run_app(db, scenario, routers=[routes()])


@mutation(LOG, 'log.warning("request log row not written", exc_info=True)', "raise", expect="no_such_table")
def test_a_log_write_that_fails_never_fails_the_request(db, monkeypatch):
    from relay_core.api import request_log
    monkeypatch.setattr(request_log, "_INSERT", "INSERT INTO no_such_table VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")

    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post("/v1/jobs", json={"prompt": "hi"}, headers=key.headers)
        assert r.status_code == 200
        assert request_logs(db) == []

    run_app(db, scenario, routers=[routes()], raise_app_exceptions=True)


@mutation(LOG, "except ClientDisconnect:", "except KeyError:", expect="a hang-up is not an error")
def test_a_client_hang_up_is_a_normal_disconnect_not_an_error(db, caplog):
    async def scenario(env):
        key = await env.new_key()
        sent: list = []
        body = b'{"prompt": "hi"}'
        events = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            return events.pop(0) if events else {"type": "http.disconnect"}

        async def send(msg):
            sent.append(msg)

        scope = {"type": "http", "method": "POST", "path": "/v1/hangup", "raw_path": b"/v1/hangup",
                 "query_string": b"", "headers": [(b"content-type", b"application/json"),
                                                  (b"authorization", ("Bearer " + key.raw).encode())],
                 "client": ("203.0.113.7", 1), "server": ("relay.test", 80), "scheme": "http", "http_version": "1.1",
                 "root_path": "", "app": env.app}
        with caplog.at_level(logging.DEBUG, logger="relay.api"):
            await env.app(scope, receive, send)          # must not raise
        assert not [x for x in caplog.records if x.levelno >= logging.ERROR], "a hang-up is not an error"
        assert sent == [], "nobody is left to answer"
        [row] = request_logs(db)
        assert (row["route"], row["status_code"]) == ("POST /v1/hangup", 499)

    run_app(db, scenario, routers=[routes()])
