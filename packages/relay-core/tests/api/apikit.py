"""Helpers for the HTTP tests: the real app on the per-run Postgres and MinIO, called through httpx over ASGI.

    def test_x(db):
        async def scenario(env):
            key = await env.new_key()
            r = await env.client.get("/v1/something", headers=key.headers)
            ...
        run_app(db, scenario)

`serving` makes the shared bucket on first use, uses a cheap argon2 meanwhile, and builds the app as `relay serve`
will: a pool as the `relay_api` role (so every statement is checked against the api's grants), the settings, the
object store, a registry holding the FakeProvider. Pass
`routers=[...]` / `admin_routers=[...]` to add routes of the test's own. The client's peer address is
`PEER_IP`. The invariant checker of the parent conftest still runs after every test that takes `db`.
"""

import asyncio
import atexit
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from accountkit import minio_settings
from argon2 import PasswordHasher
from fastapi import FastAPI

from relay_core import keys
from relay_core.admin import users
from relay_core.api.app import create_app
from relay_core.db import open_pool, tx
from relay_core.providers import ProviderRegistry
from relay_core.settings import REGISTRY, Registry, Settings
from relay_core.storage import ObjectStore
from relay_core.testing.fake_provider import FakeSpec

PEER_IP = "203.0.113.7"
ADMIN_USER = "operator"
ADMIN_PASSWORD = "correct horse battery"
_store: ObjectStore | None = None      # one bucket per test process, made on first use, removed at exit
FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)   # same argon2id format, minimal cost


def _drop_bucket() -> None:
    if _store is not None:
        for o in _store._client.list_objects(_store.bucket, recursive=True):
            _store._client.remove_object(_store.bucket, o.object_name)
        _store._client.remove_bucket(_store.bucket)


async def shared_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = ObjectStore(*minio_settings(), bucket="relay-test-api-" + uuid.uuid4().hex[:12])
        await _store.ensure_bucket()
        atexit.register(_drop_bucket)
    return _store


@dataclass
class NewApiKey:
    id: uuid.UUID
    raw: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.raw}


@dataclass
class Env:
    app: FastAPI
    client: httpx.AsyncClient
    pool: Any
    settings: Settings
    registry: ProviderRegistry
    store: ObjectStore
    _admin: dict[str, str] = field(default_factory=dict)

    async def new_key(self, *, enabled: bool = True, models=(keys.ANY_MODEL,), unlimited: bool = True) -> NewApiKey:
        """A key with a wallet per declared (provider, kind): unlimited by default so a test is not stopped by a
        limit it did not ask about."""
        declared = [(s.name, kind) for s in map(self.registry.spec, self.registry.names())
                    for kind in s.caps.wallet_kinds]
        async with tx(self.pool) as conn:
            made = await keys.create_key(conn, models=models, declared=declared,
                                         wallets={d: (None, None) for d in declared} if unlimited else None)
            if not enabled:
                await keys.set_enabled(conn, made.id, False)
        return NewApiKey(made.id, made.raw)

    async def admin_headers(self, *, fresh: bool = False) -> dict[str, str]:
        """`X-Admin-Session` for an admin user (created on first use) logged in over HTTP."""
        if fresh or not self._admin:
            with contextlib.suppress(users.UserExists):
                await users.create_user(self.pool, ADMIN_USER, ADMIN_PASSWORD)
            r = await self.client.post("/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
            assert r.status_code == 200, r.text
            self._admin = {"X-Admin-Session": r.json()["token"]}
        return self._admin


@contextlib.asynccontextmanager
async def serving(db, *, routers=(), admin_routers=(), peer_ip: str = PEER_IP, trusted_proxies: str = "",
                  public_base_url: str | None = None, env: dict[str, str] | None = None,
                  raise_app_exceptions: bool = False) -> AsyncIterator[Env]:
    store = await shared_store()
    pool = await open_pool(db.api_dsn, max_size=8, name="relay-api-test")
    saved = users._hasher, users._dummy
    users._hasher, users._dummy = FAST_HASHER, None       # the tests prove the flow, not the cost parameters
    try:
        spec = FakeSpec()
        # as `relay serve` builds it: the core settings plus every registered provider's own
        settings = Settings(pool, db.api_dsn, registry=Registry(*REGISTRY.specs.values(), *spec.settings().values()),
                            env=env or {})
        await settings.reload()
        registry = ProviderRegistry()
        registry.register(spec)
        app = create_app(pool=pool, settings=settings, store=store, registry=registry, routers=routers,
                         admin_routers=admin_routers, public_base_url=public_base_url,
                         trusted_proxies=trusted_proxies)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions, client=(peer_ip, 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://relay.test") as client:
            yield Env(app, client, pool, settings, registry, store)
    finally:
        users._hasher, users._dummy = saved
        await pool.close()


def run_app(db, scenario: Callable[[Env], Awaitable[Any]], **kw) -> Any:
    async def main():
        async with serving(db, **kw) as env:
            return await scenario(env)

    return asyncio.run(main())


def request_logs(db) -> list[dict]:
    """Every request_logs row, oldest first, as dicts."""
    cols = ("key_id", "provider", "route", "status_code", "job_id", "error_code", "body_redacted",
            "response_redacted")
    return [dict(zip(cols, row, strict=True)) for row in db.all(
        "SELECT %s FROM request_logs ORDER BY id" % ", ".join(cols))]

