"""Every route the app serves, called well-formed and malformed: none answers 500, every admin route refuses without
a session and sends no CORS header.

The route list comes from the schema (`app.openapi()`; FastAPI nests routers, so `app.routes` shows one entry) plus
the routes the schema leaves out. A route module that is mistyped or missing changes the set and fails here.
"""

import re
import uuid

from apikit import run_app
from doorkit import VIDEO

from relay_core.admin.router import MAX_BODY, MAX_IMPORT_BODY
from relay_core.testing.mutation import mutation

APP = "packages/relay-core/src/relay_core/api/app.py"
ROUTER = "packages/relay-core/src/relay_core/admin/router.py"
DOORS = "packages/relay-core/src/relay_core/api/doors.py"

PUBLIC = {
    "GET /healthz", "GET /m/{token}", "GET /models", "GET /models/{model}", "GET /v1/models", "GET /v1/models/aliases",
    "GET /v1/points", "GET /v1/providers/{provider}/options", "GET /v1beta/models", "GET /v1beta/models/{model}",
    "POST /models/{model}:generateContent", "POST /models/{model}:streamGenerateContent",
    "POST /v1beta/models/{model}:generateContent", "POST /v1beta/models/{model}:streamGenerateContent",
    "POST /v1/chat/completions", "POST /v1/images/generations", "POST /v1/video/generations",
    "GET /v1/images/generations/{job_id}", "GET /v1/video/generations/{job_id}",
    "GET /v1/video/generations/{job_id}/content", "GET /v1/video/generations/{job_id}/content/{idx}",
    "GET /v1/images/generations/{job_id}/content/{idx}",
}
NOT_IN_SCHEMA = {"GET /openapi.json"}
ADMIN = {
    "GET /admin/accounts", "POST /admin/accounts/import", "PATCH /admin/accounts/{account_id}",
    "DELETE /admin/accounts/{account_id}", "POST /admin/accounts/{account_id}/check",
    "POST /admin/accounts/{account_id}/proxy-recheck", "GET /admin/accounts/{account_id}/screenshot",
    "POST /admin/accounts/{account_id}/wake", "GET /admin/alerts", "POST /admin/alerts/{alert_id}/acknowledge",
    "GET /admin/alerts/groups", "GET /admin/alerts/groups/{kind}/{group}",
    "POST /admin/alerts/groups/{kind}/{group}/acknowledge", "GET /admin/alerts/resolved", "GET /admin/alerts/rules",
    "POST /admin/alerts/rules/{source}/{kind}",
    "GET /admin/commands/{command_id}", "GET /admin/jobs", "GET /admin/jobs/{job_id}", "PATCH /admin/jobs/{job_id}",
    "GET /admin/jobs/{job_id}/debug/{idx}", "GET /admin/jobs/{job_id}/content",
    "GET /admin/jobs/{job_id}/content/{idx}", "GET /admin/jobs/{job_id}/outputs",
    "GET /admin/jobs/{job_id}/outputs/{idx}/content", "GET /admin/jobs/{job_id}/images",
    "GET /admin/jobs/{job_id}/images/{idx}/content", "POST /admin/jobs/{job_id}/fetch-title",
    "POST /admin/jobs/{job_id}/recheck", "POST /admin/jobs/{job_id}/recover", "POST /admin/jobs/{job_id}/regenerate",
    "GET /admin/keys", "POST /admin/keys", "GET /admin/keys/{key_id}", "PATCH /admin/keys/{key_id}",
    "DELETE /admin/keys/{key_id}", "POST /admin/keys/{key_id}/disable", "POST /admin/keys/{key_id}/enable",
    "POST /admin/login", "POST /admin/logout", "GET /admin/me", "POST /admin/password",
    "POST /admin/providers/{provider}/check-all", "POST /admin/providers/{provider}/refresh-catalog",
    "GET /admin/settings", "PUT /admin/settings/{key}", "DELETE /admin/settings/{key}", "GET /admin/settings/history",
    "GET /admin/accounts/{account_id}/history",
    "GET /admin/models", "GET /admin/stats", "GET /admin/stats/hourly", "GET /admin/stats/pool",
    "GET /admin/providers/summary",
    "GET /admin/logs", "GET /admin/logs/{log_id}", "DELETE /admin/logs", "GET /admin/logs/today",
    "GET /admin/logs/export",
    "POST /admin/jobs/bulk-recheck", "POST /admin/jobs/bulk-regenerate", "POST /admin/jobs/{job_id}/resend-webhook",
    "POST /admin/jobs/{job_id}/revoke-links", "GET /admin/prices",
    "GET /admin/frames", "GET /admin/frames/{provider}/{kind}/{target}", "PUT /admin/frames/{provider}/{kind}/{target}",
    "POST /admin/frames/{provider}/{kind}/{target}/rollback", "POST /admin/frames/{provider}/{kind}/reset",
    "PUT /admin/frames/{provider}/{kind}/{model}/lock", "POST /admin/frames/{provider}/{kind}/apply-all",
    "POST /admin/frames/preview",
}
KEYLESS = {"GET /healthz", "GET /m/{token}", "GET /openapi.json"}
OPEN_ADMIN = {"POST /admin/login"}            # answers before there is a session
ENDS_SESSION = {"POST /admin/logout"}         # called last
OK_VALUE = {"idx": "0", "provider": "fake", "model": "fake-video", "key": "log_retention_days"}
JUNK_BODIES = [{"json": {}}, {"json": []}, {"json": "text"}, {"json": {"zzz": 1}}, {"content": b"not json"},
               {"json": {"model": 1, "prompt": [], "messages": 5, "contents": "x", "items": "x", "wallets": 5,
                         "value": {}, "label": [], "models": 1, "credential": 5}}]


def routes_of(app) -> set[str]:
    return {"%s %s" % (m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}


def fill(path: str, junk: bool = False, **given: str) -> str:
    """Path parameters: the given ones, else a plausible value (a fresh uuid), or with `junk` text no id parses from."""
    other = lambda name: "not-an-id" if junk else OK_VALUE.get(name, str(uuid.uuid4()))  # noqa: E731
    return re.sub(r"\{(\w+)\}", lambda m: given.get(m.group(1), other(m.group(1))), path)


def bad(r) -> bool:
    return r.status_code >= 500 or "internal.unexpected" in r.text


@mutation(APP, "PUBLIC_ROUTES = (routes_native, routes_media, routes_catalog,",
          "PUBLIC_ROUTES = (routes_native, routes_media,", expect="the route set is exactly the contract's")
def test_the_route_set_is_exactly_the_expected_one(db):
    async def scenario(env):
        assert routes_of(env.app) | NOT_IN_SCHEMA == PUBLIC | ADMIN | NOT_IN_SCHEMA, \
            "the route set is exactly the contract's: %s" % sorted(routes_of(env.app) ^ (PUBLIC | ADMIN))
        r = await env.client.get("/openapi.json")
        assert r.status_code == 200 and r.json()["paths"]

    run_app(db, scenario)


@mutation(DOORS, "wanted = uuid.UUID(job_id)\n    except ValueError:",
          "wanted = uuid.UUID(job_id)\n    except KeyError:", expect="answered 500")
def test_no_route_answers_500_to_a_well_formed_or_a_malformed_request(db):
    async def scenario(env):
        key = await env.new_key()
        admin = await env.admin_headers()
        made = await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "hi"},
                                     headers=key.headers)
        job_id = made.json()["id"] if made.status_code == 200 else str(uuid.uuid4())
        calls = 0
        for route in sorted(PUBLIC | ADMIN | NOT_IN_SCHEMA):
            method, path = route.split(" ", 1)
            if route in ENDS_SESSION or route == "POST /admin/password":
                continue
            headers = {} if route in KEYLESS else admin if path.startswith("/admin") else key.headers
            variants = [{}] if method in ("GET", "DELETE") else [{"json": {}}, *JUNK_BODIES]
            if method == "POST" and path.endswith("generations"):
                variants.append({"json": {"model": "fake-" + ("video" if "video" in path else "image"), "prompt": "x"}})
            for given in ({"job_id": job_id}, {}, {"junk": True}):
                for extra in variants:
                    url = fill(path, **given)
                    if method == "GET" and path == "/m/{token}":
                        url = "/m/" + "a" * 40
                    r = await env.client.request(method, url, headers=headers, **extra)
                    assert not bad(r), "%s %s answered 500: %s" % (method, url, r.text[:200])
                    calls += 1
        r = await env.client.post("/admin/logout", headers=admin)
        assert r.status_code == 200
        assert calls > 200, "the sweep really ran"

    run_app(db, scenario)


@mutation(ROUTER, "behind = APIRouter(dependencies=[Depends(auth.require_admin)])",
          "behind = APIRouter(dependencies=[])",
          expect="admin route answered without a session")
@mutation(ROUTER, "if not is_login and not headers.get(", "if False and not headers.get(",
          expect="admin route answered without a session")
def test_every_admin_route_refuses_without_a_session_and_sends_no_cors_header(db):
    async def scenario(env):
        origin = {"Origin": "https://evil.example"}
        for route in sorted(ADMIN - OPEN_ADMIN):
            method, path = route.split(" ", 1)
            for headers in ({}, {"X-Admin-Session": "nope"}, {"X-Admin-Session": ""}):
                bodies = [{}] if method in ("GET", "DELETE") else [{"json": {}}, {"content": b"not json"}]
                if method not in ("GET", "DELETE") and not headers.get("X-Admin-Session"):
                    bodies.append({"content": b"x" * (MAX_BODY + 1)})      # no session: 401, not 413, not read
                for extra in bodies:
                    r = await env.client.request(method, fill(path), headers={**origin, **headers}, **extra)
                    assert r.status_code == 401, "admin route answered without a session: %s %s %d" % (
                        method, path, r.status_code)
                    assert r.json()["error"]["code"] == "auth.invalid_session"
                    assert not [h for h in r.headers if h.startswith("access-control")], (route, dict(r.headers))
        # a preflight on an admin path is not answered with CORS either
        r = await env.client.options("/admin/keys", headers={**origin, "Access-Control-Request-Method": "GET"})
        assert not [h for h in r.headers if h.startswith("access-control")], dict(r.headers)

    run_app(db, scenario)


def test_every_public_route_that_needs_a_key_refuses_without_one(db):
    async def scenario(env):
        for route in sorted(PUBLIC - KEYLESS):
            method, path = route.split(" ", 1)
            extra = {} if method == "GET" else {"json": {}}
            r = await env.client.request(method, fill(path), **extra)
            assert r.status_code == 401 and r.json().get("error") is not None, (route, r.status_code, r.text[:200])

    run_app(db, scenario)


# ---- the admin guard: no session means no body read; a body over the cap is cut ----------------------------------
async def asgi(app, method: str, path: str, *, headers=(), chunks=(b"",), length: int | None = None):
    """One request straight into the app's ASGI callable. `chunks` are served one per `receive`; the count served is
    returned with the answer, so a test sees how much of the body the app read."""
    sent: list[dict] = []
    served = 0
    head = [(k.lower().encode(), v.encode()) for k, v in headers]
    if length is not None:
        head.append((b"content-length", str(length).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "query_string": b"", "headers": head, "client": ("203.0.113.7", 1),
             "server": ("relay.test", 80), "scheme": "http", "root_path": ""}

    async def receive():
        nonlocal served
        if served >= len(chunks):
            return {"type": "http.disconnect"}
        served += 1
        return {"type": "http.request", "body": chunks[served - 1], "more_body": served < len(chunks)}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body, served


@mutation(ROUTER, "if not is_login and not headers.get(", "if False and not headers.get(",
          expect="the body was read without a session")
def test_an_admin_request_without_a_session_is_refused_before_its_body_is_read(db):
    async def scenario(env):
        for method, path in (("POST", "/admin/keys"), ("POST", "/admin/accounts/import"), ("PUT", "/admin/settings/x"),
                             ("POST", "/admin/password"), ("POST", "/admin/nowhere")):
            status, headers, body, served = await asgi(env.app, method, path, chunks=[b"{" * 1000] * 8)
            assert status == 401 and b"auth.invalid_session" in body, (path, status, body[:100])
            assert served == 0, "the body was read without a session: %s read %d chunks" % (path, served)
            assert b"default-src" in headers[b"content-security-policy"], "the 401 still carries the admin headers"
        # the login is the one route that takes a body without a session
        status, _, body, served = await asgi(env.app, "POST", "/admin/login", chunks=[b"{}"])
        assert status == 400 and served >= 1, (status, body[:100])

    run_app(db, scenario)


@mutation(ROUTER, "if declared.isdigit() and int(declared) > cap:", "if False and int(declared) > cap:",
          expect="an oversized Content-Length was read")
@mutation(ROUTER, "cap = MAX_IMPORT_BODY if path == IMPORT_PATH else MAX_BODY", "cap = MAX_BODY",
          expect="the import takes its own, larger cap")
def test_an_admin_body_over_the_cap_is_refused_413_by_its_length_before_it_is_read(db):
    async def scenario(env):
        admin = await env.admin_headers()
        status, headers, body, served = await asgi(env.app, "POST", "/admin/keys", headers=admin.items(),
                                                   chunks=[b"{}"], length=MAX_BODY + 1)
        assert status == 413 and b"input.invalid" in body, "an oversized Content-Length was read: %s" % status
        assert served == 0, "an oversized Content-Length was read: %d chunks" % served
        assert b"default-src" in headers[b"content-security-policy"]
        # the import is the one route with a big cap, and a body between the two caps gets past the guard
        status, _, body, _ = await asgi(env.app, "POST", "/admin/accounts/import", headers=admin.items(),
                                        chunks=[b"{}"], length=MAX_BODY + 1)
        assert status != 413, "the import takes its own, larger cap: %s" % body[:100]
        status, _, _, served = await asgi(env.app, "POST", "/admin/accounts/import", headers=admin.items(),
                                          chunks=[b"{}"], length=MAX_IMPORT_BODY + 1)
        assert status == 413 and served == 0

    run_app(db, scenario)


@mutation(ROUTER, "                if seen > cap:", "                if False:",
          expect="a chunked body was read past the cap")
def test_a_chunked_admin_body_is_cut_at_the_cap(db):
    async def scenario(env):
        admin = await env.admin_headers()
        chunk = b"a" * (256 * 1024)
        status, _, body, served = await asgi(env.app, "POST", "/admin/keys", headers=admin.items(),
                                             chunks=[chunk] * 40)           # 10 MiB, no Content-Length
        assert status == 413 and b"input.invalid" in body, "a chunked body was read past the cap: %s" % status
        assert served <= MAX_BODY // len(chunk) + 1, "a chunked body was read past the cap: %d chunks" % served
        # a body under the cap is still read whole
        status, _, _, served = await asgi(env.app, "POST", "/admin/keys", headers=admin.items(), chunks=[b"{}"])
        assert status != 413 and served >= 1

    run_app(db, scenario)
