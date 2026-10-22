"""API-key auth on the HTTP door."""

import secrets

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
