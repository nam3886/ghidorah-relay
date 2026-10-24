"""Admin login and sessions on the API's port."""

import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from apikit import ADMIN_PASSWORD, ADMIN_USER, run_app
from fastapi import APIRouter

from relay_core.admin import auth, users
from relay_core.testing.mutation import mutation

AUTH = "packages/relay-core/src/relay_core/admin/auth.py"
USERS = "packages/relay-core/src/relay_core/admin/users.py"
ROUTER = "packages/relay-core/src/relay_core/admin/router.py"
APP = "packages/relay-core/src/relay_core/api/app.py"
LOGIN = {"username": ADMIN_USER, "password": ADMIN_PASSWORD}
OTHER_PASSWORD = "a different long phrase"


def code_of(r) -> str:
    return r.json()["error"]["code"]


async def login(env, password: str = ADMIN_PASSWORD, **headers):
    return await env.client.post("/admin/login", json={"username": ADMIN_USER, "password": password}, headers=headers)


async def make_user(env) -> None:
    with contextlib.suppress(users.UserExists):
        await users.create_user(env.pool, ADMIN_USER, ADMIN_PASSWORD)


@mutation(AUTH, "(hash_token(token), row[0],", "(token, row[0],", expect="token_hash")
def test_login_returns_the_token_once_and_the_database_keeps_only_its_hash(db):
    async def scenario(env):
        await make_user(env)
        assert db.one("SELECT password_hash FROM admin_users")[0].startswith("$argon2id$")
        r = await login(env)
        assert r.status_code == 200 and set(r.json()) == {"token", "expires_at"}
        token = r.json()["token"]
        assert db.one("SELECT count(*) FROM admin_sessions WHERE token_hash = %s", token)[0] == 0, "token_hash"
        assert db.one("SELECT count(*) FROM admin_sessions")[0] == 1
        me = await env.client.get("/admin/me", headers={"X-Admin-Session": token})
        assert me.status_code == 200 and me.json() == {"username": ADMIN_USER}

    run_app(db, scenario)


def test_a_wrong_password_and_an_unknown_user_get_the_same_401(db):
    async def scenario(env):
        await make_user(env)
        wrong = await login(env, "not the password")
        ghost = await env.client.post("/admin/login", json={"username": "ghost", "password": ADMIN_PASSWORD})
        for r in (wrong, ghost):
            assert (r.status_code, code_of(r)) == (401, "auth.invalid_login")
        assert wrong.json() == ghost.json()
        assert db.one("SELECT count(*) FROM admin_sessions")[0] == 0

    run_app(db, scenario)


@mutation(AUTH, 'raise ApiError("auth.invalid_session")', "pass", expect="auth.invalid_session")
def test_a_request_without_a_live_session_is_401(db):
    async def scenario(env):
        for headers in ({}, {"X-Admin-Session": ""}, {"X-Admin-Session": "nonsense"}):
            r = await env.client.get("/admin/me", headers=headers)
            assert (r.status_code, code_of(r)) == (401, "auth.invalid_session"), headers
        # an API key is not an admin session
        key = await env.new_key()
        r = await env.client.get("/admin/me", headers=key.headers)
        assert (r.status_code, code_of(r)) == (401, "auth.invalid_session")

    run_app(db, scenario)


@mutation(AUTH, "WHERE token_hash = %s\"", "WHERE token_hash = %s AND false\"",
          expect="auth.invalid_session")
def test_logout_revokes_the_token_at_once(db):
    async def scenario(env):
        headers = await env.admin_headers()
        assert (await env.client.post("/admin/logout", headers=headers)).status_code == 200
        r = await env.client.get("/admin/me", headers=headers)
        assert (r.status_code, code_of(r)) == (401, "auth.invalid_session")

    run_app(db, scenario)


@mutation(AUTH, "AND s.expires_at > clock_timestamp()", "AND s.expires_at IS NOT NULL", expect="expired")
@mutation(AUTH, "DELETE FROM admin_sessions WHERE expires_at <= clock_timestamp()",
          "DELETE FROM admin_sessions WHERE false", expect="== 1")
def test_an_expired_session_is_refused_and_the_sweep_purges_it(db):
    async def scenario(env):
        headers = await env.admin_headers()
        db.run("UPDATE admin_sessions SET expires_at = clock_timestamp() - interval '1 second'")
        r = await env.client.get("/admin/me", headers=headers)
        assert (r.status_code, code_of(r)) == (401, "auth.invalid_session"), "expired session accepted"
        assert await auth.purge_expired(env.pool) == 1
        assert db.one("SELECT count(*) FROM admin_sessions")[0] == 0

    run_app(db, scenario)


@mutation(AUTH, 'settings.get("session_ttl_s")', "86400", expect="3600")
def test_the_session_lifetime_comes_from_session_ttl_s(db):
    async def scenario(env):
        await env.settings.set("session_ttl_s", 3600)
        await make_user(env)
        r = await login(env)
        left = datetime.fromisoformat(r.json()["expires_at"]) - datetime.now(UTC)
        assert timedelta(seconds=3500) < left <= timedelta(seconds=3600), left   # session_ttl_s = 3600 stored

    run_app(db, scenario)


@mutation(AUTH, "if count >= LOGIN_MAX_FAILS:", "if False:", expect="auth.too_many_attempts")
def test_eleven_failed_logins_from_one_ip_get_429_and_a_restart_does_not_reset_it(db):
    async def scenario(env):
        await make_user(env)
        for i in range(auth.LOGIN_MAX_FAILS):
            r = await login(env, "wrong-%d" % i)
            assert (r.status_code, code_of(r)) == (401, "auth.invalid_login")
        r = await login(env)         # the right password, but the 11th attempt from this address
        assert (r.status_code, code_of(r)) == (429, "auth.too_many_attempts")

    run_app(db, scenario)

    async def after_restart(env):          # a new app and a new pool over the same database
        r = await login(env)
        assert (r.status_code, code_of(r)) == (429, "auth.too_many_attempts"), "the throttle must live in Postgres"

    run_app(db, after_restart)

    async def other_address(env):
        r = await login(env)
        assert r.status_code == 200, "another IP is not throttled"
        assert db.one("SELECT count(*) FROM admin_login_attempts WHERE ip = %s::inet", "203.0.113.8")[0] == 0

    run_app(db, other_address, peer_ip="203.0.113.8")


@mutation(AUTH, "DELETE FROM admin_login_attempts WHERE at < clock_timestamp()",
          "DELETE FROM admin_login_attempts WHERE false AND at < clock_timestamp()", expect="the throttle window ends")
def test_the_login_throttle_ends_with_its_window(db):
    """Failures older than LOGIN_WINDOW_S stop counting: an address is not locked out for good."""
    async def scenario(env):
        await make_user(env)
        for i in range(auth.LOGIN_MAX_FAILS):
            await login(env, "wrong-%d" % i)
        age = "UPDATE admin_login_attempts SET at = clock_timestamp() - make_interval(secs => %s)"
        db.run(age, auth.LOGIN_WINDOW_S - 10)
        r = await login(env)
        assert (r.status_code, code_of(r)) == (429, "auth.too_many_attempts"), "still inside the window"
        db.run(age, auth.LOGIN_WINDOW_S + 1)
        r = await login(env)
        assert r.status_code == 200, "the throttle window ends: %s" % r.text

    run_app(db, scenario)


@mutation(AUTH, "DELETE FROM admin_login_attempts WHERE ip = %s::inet\", (ip,))\n    return",
          "SELECT %s\", (ip,))\n    return", expect="\"again-%d\"")
def test_a_good_login_clears_the_ips_failures(db):
    async def scenario(env):
        await make_user(env)
        for i in range(auth.LOGIN_MAX_FAILS - 1):
            assert (await login(env, "wrong-%d" % i)).status_code == 401
        assert (await login(env)).status_code == 200
        for i in range(auth.LOGIN_MAX_FAILS - 1):      # a fresh budget: these would be 429 had the count survived
            assert (await login(env, "again-%d" % i)).status_code == 401
        assert (await login(env)).status_code == 200

    run_app(db, scenario)


@mutation("packages/relay-core/src/relay_core/api/deps.py", "if peer_ip is None or not is_trusted(peer_ip):",
          "if peer_ip is None:", expect="an untrusted peer cannot choose")
def test_forwarded_for_counts_only_behind_a_trusted_proxy(db):
    async def behind_proxy(env):
        await make_user(env)
        for i in range(auth.LOGIN_MAX_FAILS):
            await login(env, "wrong-%d" % i, **{"X-Forwarded-For": "1.2.3.4, 198.51.100.4"})
        r = await login(env, **{"X-Forwarded-For": "1.2.3.4, 198.51.100.4"})
        assert r.status_code == 429, "the rightmost untrusted address is the client"
        assert (await login(env, **{"X-Forwarded-For": "198.51.100.5"})).status_code == 200

    run_app(db, behind_proxy, peer_ip="192.168.18.13", trusted_proxies="192.168.0.0/16")

    async def direct(env):
        await make_user(env)
        for i in range(auth.LOGIN_MAX_FAILS):        # the caller invents a new address every time
            await login(env, "wrong-%d" % i, **{"X-Forwarded-For": "10.9.9.%d" % i})
        r = await login(env, **{"X-Forwarded-For": "10.9.9.99"})
        assert r.status_code == 429, "an untrusted peer cannot choose the address that is counted"

    run_app(db, direct, peer_ip="203.0.113.50")


@mutation(USERS, "WHERE user_id = %s\"", "WHERE user_id = %s AND false\"",
          expect="auth.invalid_session")
def test_a_password_change_revokes_every_session_and_the_old_password_stops_working(db):
    async def scenario(env):
        first = await env.admin_headers()
        second = {"X-Admin-Session": (await login(env)).json()["token"]}
        assert db.one("SELECT count(*) FROM admin_sessions")[0] == 2
        r = await env.client.post("/admin/password", headers=first,
                                  json={"old_password": "not mine", "new_password": OTHER_PASSWORD})
        assert (r.status_code, r.json()["error"]["param"]) == (400, "old_password")
        assert (await env.client.get("/admin/me", headers=first)).status_code == 200
        r = await env.client.post("/admin/password", headers=first,
                                  json={"old_password": ADMIN_PASSWORD, "new_password": "short"})
        assert (r.status_code, r.json()["error"]["param"]) == (400, "new_password")
        r = await env.client.post("/admin/password", headers=first,
                                  json={"old_password": ADMIN_PASSWORD, "new_password": OTHER_PASSWORD})
        assert r.status_code == 200
        for headers in (first, second):
            r = await env.client.get("/admin/me", headers=headers)
            assert (r.status_code, code_of(r)) == (401, "auth.invalid_session")
        assert (await login(env)).status_code == 401
        assert (await login(env, OTHER_PASSWORD)).status_code == 200

    run_app(db, scenario)


@mutation(AUTH, "SELECT 1 FROM admin_users WHERE id = %s AND password_hash = %s FOR SHARE",
          "SELECT 1 FROM admin_users WHERE id = %s AND %s::text IS NOT NULL FOR SHARE",
          expect="made with the old password")
def test_a_login_that_raced_a_password_change_gets_no_session(db, monkeypatch):
    async def scenario(env):
        await make_user(env)
        uid = db.one("SELECT id FROM admin_users")[0]
        checked = users.verify_or_burn

        async def change_after_the_check(stored, password):
            ok = await checked(stored, password)
            assert await users.change_password(env.pool, uid, ADMIN_PASSWORD, OTHER_PASSWORD)
            return ok

        monkeypatch.setattr(users, "verify_or_burn", change_after_the_check)
        r = await login(env)
        assert r.status_code == 401, "a session was made with the old password"
        assert db.one("SELECT count(*) FROM admin_sessions")[0] == 0

    run_app(db, scenario)


@mutation(ROUTER, '(b"x-content-type-options", b"nosniff")', '(b"x-content-type-options", b"")', expect="nosniff")
@mutation(ROUTER, '(b"content-security-policy", CSP.encode())', '(b"content-security-policy", b"")',
          expect="default-src 'self'")
def test_every_admin_response_carries_csp_and_nosniff_and_no_public_one_does(db):
    csp = "default-src 'self'; script-src 'self'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"

    async def scenario(env):
        headers = await env.admin_headers()
        answers = [await env.client.get("/admin/me", headers=headers),                     # 200
                   await env.client.get("/admin/me"),                                      # 401
                   await env.client.get("/admin/no-such-page", headers=headers),           # 404
                   await env.client.post("/admin/login", json={"username": 1}),            # 400
                   await env.client.post("/admin/me", headers=headers)]                    # 405
        assert [a.status_code for a in answers] == [200, 401, 404, 400, 405]
        for a in answers:
            assert a.headers["content-security-policy"] == csp, a.status_code
            assert a.headers["x-content-type-options"] == "nosniff", a.status_code
        assert "content-security-policy" not in (await env.client.get("/healthz")).headers

    run_app(db, scenario)


def public_routes() -> APIRouter:
    r = APIRouter()

    @r.get("/v1/ping")
    async def ping():
        return {"ok": True}

    return r


@mutation(APP, 'if scope["type"] == "http" and _is_public(scope["path"]):', 'if scope["type"] == "http":',
          expect="access-control-allow-origin")
@mutation(APP, "allow_credentials=False", "allow_credentials=True", expect="no credentials: star origin")
def test_cors_is_on_the_public_paths_only_and_never_with_credentials(db):
    origin = {"Origin": "https://app.example"}
    preflight = {**origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}

    async def scenario(env):
        r = await env.client.get("/v1/ping", headers=origin)
        assert r.headers["access-control-allow-origin"] == "*", "no credentials: star origin"
        assert "access-control-allow-credentials" not in r.headers, "no credentials: star origin"
        r = await env.client.options("/v1/ping", headers=preflight)
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*", "no credentials: star origin"
        assert "access-control-allow-credentials" not in r.headers, "no credentials: star origin"
        for path in ("/v1beta/models/x:generateContent", "/models/x", "/m/sometoken", "/v1/models"):
            r = await env.client.options(path, headers=preflight)
            assert r.headers["access-control-allow-origin"] == "*", path
        for path in ("/admin/login", "/admin/me", "/healthz", "/administrator", "/mine"):
            r = await env.client.options(path, headers=preflight)
            assert not [h for h in r.headers if h.startswith("access-control")], (path, dict(r.headers))
            r = await env.client.get(path, headers=origin)
            assert not [h for h in r.headers if h.startswith("access-control")], (path, dict(r.headers))

    run_app(db, scenario, routers=[public_routes()])


@pytest.mark.parametrize("name, password, fragment", [
    ("", ADMIN_PASSWORD, "username"), ("x" * 65, ADMIN_PASSWORD, "username"), ("op", "short", "password"),
])
def test_create_user_refuses_a_bad_name_or_password_and_a_duplicate(db, name, password, fragment):
    async def scenario(env):
        with pytest.raises(users.UserError, match=fragment):
            await users.create_user(env.pool, name, password)
        await users.create_user(env.pool, "op", ADMIN_PASSWORD)
        with pytest.raises(users.UserExists):
            await users.create_user(env.pool, "op", ADMIN_PASSWORD)

    run_app(db, scenario)


@mutation(USERS, "    if is_example_secret(password):\n", "    if False:\n",
          expect="DID NOT RAISE")
# long enough for the length rule, so only the example check can refuse them
@pytest.mark.parametrize("example", ["thay-bang-mat-khau-manh", "thay-bang-mat-khau-minio-manh", "minioadmin",
                                     " MinioAdmin "])
def test_an_example_password_is_refused_on_create_and_on_change(db, example):
    """Example secrets are refused (the boot guard): the first admin and every password change."""
    async def scenario(env):
        with pytest.raises(users.UserError, match="example password"):
            await users.create_user(env.pool, "op2", example)
        user_id = await users.create_user(env.pool, "op3", ADMIN_PASSWORD)
        with pytest.raises(users.UserError, match="example password"):
            await users.change_password(env.pool, user_id, ADMIN_PASSWORD, example)

    run_app(db, scenario)


@mutation(USERS, "HASH_CONCURRENCY = 2 ", "HASH_CONCURRENCY = 64 ", expect="at most 2 hashes at a time")
def test_password_hashing_runs_at_most_two_at_a_time_across_hash_and_verify(monkeypatch):
    import asyncio
    import threading
    import time

    lock = threading.Lock()
    running = peak = 0

    class SlowHasher:
        def _work(self) -> None:
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.05)
            with lock:
                running -= 1

        def hash(self, password: str) -> str:
            self._work()
            return "hashed"

        def verify(self, stored: str, password: str) -> bool:
            self._work()
            return True

    monkeypatch.setattr(users, "_hasher", SlowHasher())

    async def flood():
        await asyncio.gather(*[users.hash_password("p") if i % 2 else users.verify_password("h", "p")
                               for i in range(12)])

    asyncio.run(flood())
    assert peak == 2, "at most 2 hashes at a time (and the bound is used): peak %d" % peak
    asyncio.run(flood())          # a second event loop gets its own slots and works the same
