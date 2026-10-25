"""API-key auth on the HTTP door."""

import asyncio
import secrets
import time

from apikit import run_app
from fastapi import APIRouter, Depends

from relay_core import keys
from relay_core.api.deps import require_key
from relay_core.testing.mutation import mutation

DEPS = "packages/relay-core/src/relay_core/api/deps.py"


def routes() -> APIRouter:
    r = APIRouter()

    @r.get("/v1/whoami")
    async def whoami(key: keys.ApiKey = Depends(require_key)):
        return {"id": str(key.id)}

    return r


def code_of(r) -> str:
    return r.json()["error"]["code"]


@mutation(DEPS, "raise ApiError(\"auth.invalid_key\") from None", "pass", expect="auth.invalid_key")
def test_a_missing_wrong_or_malformed_key_is_401_invalid_key(db):
    forged_tail = keys.PREFIX + secrets.token_urlsafe(32)

    async def scenario(env):
        real = await env.new_key()
        # the same lookup prefix as a real key, the rest forged: still 401, never a hint that the prefix exists
        forged = real.raw[:keys.LOOKUP_LEN] + forged_tail[keys.LOOKUP_LEN:]
        for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer rk-short"},
                        {"Authorization": "Bearer " + forged_tail}, {"Authorization": "Bearer " + forged},
                        {"X-API-Key": "not-a-key"}):
            r = await env.client.get("/v1/whoami", headers=headers)
            assert (r.status_code, code_of(r)) == (401, "auth.invalid_key"), (headers, r.text)

    run_app(db, scenario, routers=[routes()])


@mutation(DEPS, 'raise ApiError("auth.key_disabled") from None', 'raise ApiError("auth.invalid_key") from None',
          expect="auth.key_disabled")
def test_a_disabled_key_is_403_only_after_the_whole_hash_matched(db):
    async def scenario(env):
        off = await env.new_key(enabled=False)
        r = await env.client.get("/v1/whoami", headers=off.headers)
        assert (r.status_code, code_of(r)) == (403, "auth.key_disabled")
        forged = off.raw[:keys.LOOKUP_LEN] + (secrets.token_urlsafe(32) + "x" * 43)[:len(off.raw) - keys.LOOKUP_LEN]
        r = await env.client.get("/v1/whoami", headers={"Authorization": "Bearer " + forged})
        assert (r.status_code, code_of(r)) == (401, "auth.invalid_key"), "a forged key must not learn the prefix exists"

    run_app(db, scenario, routers=[routes()])


@mutation(DEPS, 'auth[:7].lower() == "bearer "', 'auth[:7] == "Bearer "', expect="BEARER")
@mutation(DEPS, 'or request.headers.get("x-goog-api-key") or ""', 'or ""', expect="x-goog-api-key")
def test_the_key_is_read_from_bearer_x_api_key_or_x_goog_api_key_and_never_from_the_query(db):
    async def scenario(env):
        key = await env.new_key()
        for headers in ({"Authorization": "BEARER " + key.raw}, {"X-API-Key": key.raw},
                        {"x-goog-api-key": key.raw}):
            r = await env.client.get("/v1/whoami", headers=headers)
            assert r.status_code == 200 and r.json() == {"id": str(key.id)}, (headers, r.text)
        r = await env.client.get("/v1/whoami", params={"key": key.raw})
        assert (r.status_code, code_of(r)) == (401, "auth.invalid_key")

    run_app(db, scenario, routers=[routes()])


KEYS_PY = "packages/relay-core/src/relay_core/keys.py"


def _last_used(db, key_id):
    return db.one("SELECT last_used_at, last_used_ip FROM api_keys WHERE id = %s", key_id)


@mutation(DEPS, "            await keys.touch(conn, key.id, client_ip(request))\n", "",
          expect="a key's first call is never recorded")
@mutation(KEYS_PY, "OR last_used_at <= clock_timestamp() - make_interval(secs => %s)",
          "OR last_used_at <= clock_timestamp() + make_interval(secs => %s)", expect="written again within the minute")
@mutation(KEYS_PY, "AND (last_used_at IS NULL ", "AND (last_used_at IS NOT NULL ",
          expect="a key's first call is never recorded")
def test_a_key_records_its_last_call_and_address_at_most_once_a_minute(db):
    async def scenario(env):
        key = await env.new_key()
        assert _last_used(db, key.id) == (None, None)
        assert (await env.client.get("/v1/whoami", headers=key.headers)).status_code == 200
        first, ip = _last_used(db, key.id)
        assert first is not None, "a key's first call is never recorded"
        assert ip == "203.0.113.7"
        assert (await env.client.get("/v1/whoami", headers=key.headers)).status_code == 200
        assert _last_used(db, key.id)[0] == first, "written again within the minute"
        db.run("UPDATE api_keys SET last_used_at = clock_timestamp() - interval '59 seconds' WHERE id = %s", key.id)
        held = _last_used(db, key.id)[0]
        await env.client.get("/v1/whoami", headers=key.headers)
        assert _last_used(db, key.id)[0] == held, "written again within the minute"
        db.run("UPDATE api_keys SET last_used_at = clock_timestamp() - interval '61 seconds', last_used_ip = 'old' "
               "WHERE id = %s", key.id)
        held = _last_used(db, key.id)[0]
        await env.client.get("/v1/whoami", headers=key.headers)
        later, ip = _last_used(db, key.id)
        assert later > held and ip == "203.0.113.7", "a minute-old time is not moved on"
        off = await env.new_key(enabled=False)
        await env.client.get("/v1/whoami", headers=off.headers)
        assert _last_used(db, off.id) == (None, None), "a refused key records nothing"

    run_app(db, scenario, routers=[routes()])


@mutation(DEPS, "await keys.touch(conn, key.id, client_ip(request))",
          "await keys.touch(conn, key.id, request.client.host)", expect="the forwarded client")
def test_the_address_is_the_forwarded_client_only_behind_a_trusted_local_proxy(db):
    forwarded = {"X-Forwarded-For": "198.51.100.24"}

    async def behind_proxy(env):
        key = await env.new_key()
        await env.client.get("/v1/whoami", headers={**key.headers, **forwarded})
        assert _last_used(db, key.id)[1] == "198.51.100.24", "the forwarded client is not the one recorded"

    async def direct(env):
        key = await env.new_key()
        await env.client.get("/v1/whoami", headers={**key.headers, **forwarded})
        assert _last_used(db, key.id)[1] == "203.0.113.7", "a direct caller can forge its address"

    run_app(db, behind_proxy, routers=[routes()], peer_ip="127.0.0.1", trusted_proxies="127.0.0.1/32")
    run_app(db, direct, routers=[routes()])


def _stale(db, key_id, when):
    db.run("UPDATE api_keys SET last_used_at = %s, last_used_ip = 'old' WHERE id = %s" % (when, "%s"), key_id)


@mutation(KEYS_PY, "    except psycopg.Error as e:\n", "    except ZeroDivisionError as e:\n",
          expect="a failed record failed the request")
def test_a_record_that_fails_never_fails_the_request(db, monkeypatch, caplog):
    monkeypatch.setattr(keys, "_TOUCH", "SELECT 1/0 WHERE %s::text IS NOT NULL AND %s::uuid IS NOT NULL "
                                        "AND %s::int IS NOT NULL")

    async def scenario(env):
        key = await env.new_key()
        r = await env.client.get("/v1/whoami", headers=key.headers)
        assert r.status_code == 200, "a failed record failed the request: %s" % r.text
        assert _last_used(db, key.id) == (None, None)

    run_app(db, scenario, routers=[routes()])
    assert [r.levelname for r in caplog.records if "last-used of key" in r.getMessage()] == ["WARNING"]


@mutation(KEYS_PY, "FOR UPDATE SKIP LOCKED)", "FOR UPDATE)", expect="waited for the row lock")
def test_a_request_never_waits_for_another_request_holding_the_key_row(db):
    async def scenario(env):
        key = await env.new_key()
        _stale(db, key.id, "clock_timestamp() - interval '2 minutes'")
        with db.connect() as holder, holder.transaction():   # another request's touch, still in its transaction
            holder.execute("SELECT 1 FROM api_keys WHERE id = %s FOR UPDATE", (key.id,))
            start = time.monotonic()
            r = await env.client.get("/v1/whoami", headers=key.headers)
            waited = time.monotonic() - start
            assert r.status_code == 200 and waited < 1.5, "waited for the row lock: %.1f s" % waited
            assert _last_used(db, key.id)[1] == "old", "a held row is skipped, not written"
        both = await asyncio.gather(*(env.client.get("/v1/whoami", headers=key.headers) for _ in range(2)))
        assert [r.status_code for r in both] == [200, 200]
        assert _last_used(db, key.id)[1] == "203.0.113.7", "a minute-old time is not moved on"

    run_app(db, scenario, routers=[routes()])


@mutation(KEYS_PY, " OR last_used_at > clock_timestamp()) ", ") ", expect="a time in the future is kept")
def test_a_time_a_minute_old_or_in_the_future_is_moved_on(db):
    async def scenario(env):
        key = await env.new_key()
        for when, why in (("clock_timestamp() - interval '60 seconds'", "a minute-old time is not moved on"),
                          ("clock_timestamp() + interval '1 day'", "a time in the future is kept")):
            _stale(db, key.id, when)
            await env.client.get("/v1/whoami", headers=key.headers)
            assert _last_used(db, key.id)[1] == "203.0.113.7", why

    run_app(db, scenario, routers=[routes()])
