"""Helpers for the account, check, command and title tests: a scriptable engine, an env wired to the worker role,
accounts, commands, a disposable object-store bucket and a runner that opens its pool as `relay_worker`."""

import asyncio
import contextlib
import dataclasses
import os
import uuid
from collections.abc import Awaitable, Callable

from lifecyclekit import MediaEngine

from relay_core import leases
from relay_core.budget import BrowserBudget
from relay_core.db import open_pool, tx
from relay_core.jobs.checks import Env
from relay_core.providers import AccountView, Health, Proxy, ProxyVerdict
from relay_core.storage import ObjectStore
from relay_core.testing.fake_provider import FakeSettings, FakeSpec

PROVIDER = "fake"


class KitSpec(FakeSpec):
    """The fake spec with the optional engine members the account work uses."""

    def __init__(self, *, execution: str = "http", titles: bool = True, catalog_refresh_s: int | None = 3600,
                 probe: bool = True):
        super().__init__(execution=execution)
        self.caps = dataclasses.replace(self.caps, titles=titles, catalog_refresh_s=catalog_refresh_s,
                                        has_probe_proxy=probe)


class KitEngine(MediaEngine):
    """A FakeEngine whose check, command and probe answers a test scripts, with the calls recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.health_by_account: dict[str, Health] = {}
        self.checked: list[str] = []
        self.tokens: list[object] = []
        self.on_check: Callable[[object], Awaitable[None]] | None = None
        self.results: dict[str, object] = {}
        self.commanded: list[tuple[str, dict]] = []
        self.on_command: Callable[[object, str, dict], Awaitable[None]] | None = None
        self.verdict = ProxyVerdict(True)
        self.probed: list[Proxy] = []

    async def check_account(self, ctx) -> Health:
        self.checked.append(ctx.account.id)
        self.tokens.append(ctx.browser_token)
        if self.on_check is not None:
            await self.on_check(ctx)
        return self.health_by_account.get(ctx.account.id, self.health)

    async def run_command(self, ctx, kind: str, args: dict):
        self.commanded.append((kind, dict(args)))
        if self.on_command is not None:
            await self.on_command(ctx, kind, args)
        got = self.results.get(kind, {"ok": True})
        return got(args) if callable(got) else got

    async def probe_proxy(self, proxy: Proxy) -> ProxyVerdict:
        self.probed.append(proxy)
        return self.verdict


def make_env(p, engine: KitEngine | None = None, *, spec: KitSpec | None = None, store: ObjectStore | None = None,
             budget: BrowserBudget | None = None, settings: FakeSettings | None = None, owner: str = "w1",
             **kw) -> Env:
    """An env for one provider on the pool `p`. Timings are overridable (`check_hard_s=0.6`, `retry_s=...`)."""
    spec = spec or KitSpec()
    keeper = leases.LeaseKeeper(p, owner, ttl_s=kw.get("ttl_s", leases.LEASE_TTL_S), heartbeat_s=0.1)

    async def load_account(account_id) -> AccountView:
        async with tx(p) as conn:
            row = await (await conn.execute(
                "SELECT label, proxy_host, proxy_port, provider_state, credential_gen FROM accounts WHERE id = %s",
                (account_id,))).fetchone()
        proxy = Proxy(row[1], row[2]) if row[1] else None
        return AccountView(str(account_id), row[0] or "", b"credential", row[4], proxy, row[3], {})

    return Env(db=p, spec=spec, engine=engine or KitEngine(), settings=settings or FakeSettings(spec), owner=owner,
               keeper=keeper, load_account=load_account, store=store, budget=budget, **kw)


@contextlib.asynccontextmanager
async def beating(env: Env):
    """Run the lease heartbeat of the env's keeper while the block runs."""
    stop = asyncio.Event()
    task = asyncio.create_task(env.keeper.run(stop))
    try:
        yield
    finally:
        stop.set()
        await task


def run_as(dsn: str, scenario, max_size: int = 10):
    """Run `scenario(pool)` on a pool opened with `dsn` (the worker role: the account work must run under it)."""
    async def main():
        p = await open_pool(dsn, max_size=max_size)
        try:
            return await scenario(p)
        finally:
            await p.close()

    return asyncio.run(main())


def new_account(db, *, state="available", identity=None, checked=False, due_s: float | None = -1,
                proxy: tuple[str, int] | None = None, credential: bytes = b"\x01", provider: str = PROVIDER,
                label: str | None = None) -> uuid.UUID:
    """An account row. `due_s` sets `check_due_at` that many seconds from now (None = NULL); `checked` stamps
    `last_checked_at`."""
    account_id = uuid.uuid4()
    host, port = proxy if proxy else (None, None)
    db.run("INSERT INTO accounts (id, provider, label, identity, credential_enc, state, proxy_host, proxy_port, "
           "last_checked_at, check_due_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
           "CASE WHEN %s THEN clock_timestamp() END, "
           "CASE WHEN %s::float8 IS NULL THEN NULL ELSE clock_timestamp() + make_interval(secs => %s::float8) END)",
           account_id, provider, label or "acct", identity, credential, state, host, port, checked, due_s, due_s)
    return account_id


def new_command(db, kind: str, *, account_id=None, job_id=None, args: dict | None = None,
                provider: str = PROVIDER) -> uuid.UUID:
    import json
    cid = uuid.uuid4()
    db.run("INSERT INTO commands (id, provider, kind, account_id, job_id, args) VALUES (%s, %s, %s, %s, %s, %s)",
           cid, provider, kind, account_id, job_id, json.dumps(args or {}))
    return cid


def command(db, cid) -> dict:
    row = db.one("SELECT status, result, attempts, lease_id, account_id FROM commands WHERE id = %s", cid)
    names = ("status", "result", "attempts", "lease_id", "account_id")
    return None if row is None else dict(zip(names, row, strict=True))


def account_row(db, account_id) -> dict:
    cols = ("state", "state_reason", "strikes", "identity", "display_name", "last_error", "last_checked_at",
            "check_due_at", "proxy_ok", "proxy_checked_at", "cooldown_until", "provider_state", "credential_gen",
            "credential_enc", "proxy_host")
    row = db.one("SELECT %s FROM accounts WHERE id = %%s" % ", ".join(cols), account_id)
    return None if row is None else dict(zip(cols, row, strict=True))


def children(db, account_id) -> dict[str, int]:
    """Rows that reference the account, per table: all zero once it is gone."""
    return {t: db.one("SELECT count(*) FROM %s WHERE account_id = %%s" % t, account_id)[0]
            for t in ("leases", "account_shots", "commands", "account_pools", "account_charges", "jobs")}


def minio_settings() -> tuple[str, str, str]:
    endpoint = os.environ.get("RELAY_TEST_MINIO", "")
    if not endpoint:
        raise RuntimeError("RELAY_TEST_MINIO is not set: run the tests through scripts/check.sh")
    return endpoint, os.environ["RELAY_TEST_MINIO_ACCESS_KEY"], os.environ["RELAY_TEST_MINIO_SECRET_KEY"]


@contextlib.contextmanager
def object_store():
    """A store on a bucket of its own, emptied and removed afterwards."""
    s = ObjectStore(*minio_settings(), bucket="relay-test-" + uuid.uuid4().hex[:12])
    asyncio.run(s.ensure_bucket())
    try:
        yield s
    finally:
        for o in s._client.list_objects(s.bucket, recursive=True):
            s._client.remove_object(s.bucket, o.object_name)
        s._client.remove_bucket(s.bucket)


def keys(store: ObjectStore) -> list[str]:
    return sorted(o.object_name for o in store._client.list_objects(store.bucket, recursive=True))


def put_shot(db, store: ObjectStore, account_id, data: bytes = b"\x89PNG-shot") -> str:
    """A screenshot object and its row, as a check leaves them."""
    from relay_core.jobs.checks import shot_key
    key = shot_key(account_id)
    asyncio.run(store.put_bytes(key, data, "image/png"))
    db.run("INSERT INTO account_shots (account_id, object_key) VALUES (%s, %s)", account_id, key)
    return key


def done_job(db, key_id, account_id=None, *, ref: str = "r", upstream_id: str | None = None,
             status: str = "done", prompt: str = "a prompt") -> uuid.UUID:
    """A terminal job of the fake provider; a `done` one owns one output (invariant I11)."""
    job = db.job(key_id, provider=PROVIDER, prompt=prompt)
    db.run("UPDATE jobs SET account_id = %s, external_ref = %s, status = %s, sent_at = now() - interval '5 min', "
           "finished_at = now() WHERE id = %s", account_id, ref, status, job)
    if status == "done":
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, upstream_id) "
               "VALUES (%s, 'output', 0, %s, 'video/mp4', 1, %s)", job, "%s/output/0.mp4" % job,
               upstream_id or "up-" + ref)
    return job
