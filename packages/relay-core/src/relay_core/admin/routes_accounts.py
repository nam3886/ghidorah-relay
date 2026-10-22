"""Admin: accounts and the commands that act on them.

The api holds the PUBLIC key only: an import seals each credential and each proxy password and nothing here can open
one. The api also cannot delete an account or write a worker-owned column (`check_due_at`, `cooldown_*`, `strikes`,
`strike_at`, `proxy_ok`, `proxy_checked_at`): delete, wake, check, check all and proxy re-check each insert ONE command
row and answer with its id; the provider's worker runs it and the page polls `GET /commands/{id}`. A proxy edit also
queues the proxy re-check, since the api cannot refresh `proxy_ok` itself.

An import never learns an account's identity (only the first check does), so a credential for a known identity is
inserted like any other; the worker's first check merges it into the existing row.
"""

import logging
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from psycopg_pool import AsyncConnectionPool
from pydantic import Field

from relay_core import crypto, storage
from relay_core.admin.routes_common import MAX_PAGE, Strict, enqueue, ms, parse_proxy, pool_point, spec_of
from relay_core.api.deps import get_pool, get_registry, get_settings, get_store, skip_body_log
from relay_core.api.errors_http import ApiError
from relay_core.db import lock_account, tx
from relay_core.db.pool import TX_HOLD_S
from relay_core.pool import pool_configs
from relay_core.providers import ProviderRegistry
from relay_core.settings import Settings
from relay_core.storage import ObjectStore

log = logging.getLogger("relay.admin")
router = APIRouter()

MAX_IMPORT = 200
MAX_CREDENTIAL_CHARS = 200_000
LABEL_WAIT_S = 12          # how long a label edit waits for a worker that holds the account row
IN_USE = "SELECT EXISTS (SELECT 1 FROM leases WHERE account_id = %s AND until > clock_timestamp())"
_LIST = (
    "SELECT a.id, a.provider, a.label, a.identity, a.display_name, a.state, a.state_reason, a.proxy_host, "
    "a.proxy_port, a.proxy_auth_enc IS NOT NULL, a.proxy_ok, a.proxy_checked_at, a.strikes, a.cooldown_until, "
    "a.cooldown_reason, a.last_error, a.last_used_at, a.last_checked_at, a.check_due_at, a.credential_gen, "
    "a.created_at, (SELECT count(*) FROM leases l WHERE l.account_id = a.id AND l.until > clock_timestamp()) > 0, "
    "EXISTS (SELECT 1 FROM account_shots s WHERE s.account_id = a.id), a.provider_state->>'composer' "
    "FROM accounts a WHERE (%(p)s::text IS NULL OR a.provider = %(p)s) "
    "ORDER BY a.created_at DESC, a.id LIMIT %(limit)s OFFSET %(offset)s")
_COLUMNS = ("id", "provider", "label", "identity", "display_name", "state", "state_reason", "proxy_host", "proxy_port",
            "has_proxy_auth", "proxy_ok", "proxy_checked_at", "strikes", "cooldown_until", "cooldown_reason",
            "last_error", "last_used_at", "last_checked_at", "check_due_at", "credential_gen", "created_at", "in_use",
            "has_screenshot", "composer")
_TIMES = ("proxy_checked_at", "cooldown_until", "last_used_at", "last_checked_at", "check_due_at", "created_at")


class ImportItem(Strict):
    credential: str = Field(min_length=1, max_length=MAX_CREDENTIAL_CHARS)
    label: str = Field(default="", max_length=100)
    proxy: str | None = Field(default=None, max_length=500)


class ImportBody(Strict):
    provider: str = Field(min_length=1, max_length=40)
    items: list[ImportItem] = Field(min_length=1, max_length=MAX_IMPORT)


class AccountPatch(Strict):
    """Only the fields present change. `proxy: null` (or empty) removes the proxy."""

    label: str | None = Field(default=None, max_length=100)
    proxy: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


# ---- import --------------------------------------------------------------------------------------------------------
@router.post("/accounts/import", dependencies=[Depends(skip_body_log)])
async def import_accounts(body: ImportBody, request: Request, pool: AsyncConnectionPool = Depends(get_pool),
                          registry: ProviderRegistry = Depends(get_registry)) -> dict:
    """Seal and insert each credential: `available`, never checked (so not claimable), due for its first check at
    once. An item that cannot be parsed is rejected with a fixed reason and nothing of its text. The body never
    reaches a log: only the counts do."""
    spec = spec_of(registry, body.provider)
    public = crypto.public_key_from_env()
    ready: list[tuple[int, str, bytes, tuple[str, int, bytes | None] | None]] = []
    rejected: list[dict[str, Any]] = []
    for index, item in enumerate(body.items):
        try:
            parsed = spec.parse_credential(item.credential.encode())
        except ValueError:
            rejected.append({"index": index, "reason": "credential_unparseable"})
            continue
        proxy = None
        if item.proxy:
            try:
                host, port, auth = parse_proxy(item.proxy)
            except ValueError:
                rejected.append({"index": index, "reason": "proxy_invalid"})
                continue
            proxy = (host, port, None if auth is None else crypto.seal(public, auth.encode()))
        ready.append((index, item.label or parsed.hint, crypto.seal(public, parsed.blob), proxy))
    imported = []
    if ready:
        async with tx(pool) as conn:
            for index, label, sealed, proxy in ready:
                host, port, auth = proxy if proxy else (None, None, None)
                row = await (await conn.execute(
                    "INSERT INTO accounts (provider, label, credential_enc, proxy_host, proxy_port, proxy_auth_enc, "
                    "check_due_at) VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp()) RETURNING id",
                    (body.provider, label, sealed, host, port, auth))).fetchone()
                imported.append({"index": index, "id": str(row[0]), "label": label})
    log.info("account import: provider=%s count=%d rejected=%d", body.provider, len(imported), len(rejected))
    request.state.provider = body.provider
    return {"imported": imported, "rejected": rejected}


# ---- list, patch, screenshot -------------------------------------------------------------------------------------
@router.get("/accounts")
async def list_accounts(provider: str | None = None, limit: int = 100, offset: int = 0,
                        pool: AsyncConnectionPool = Depends(get_pool),
                        registry: ProviderRegistry = Depends(get_registry),
                        settings: Settings = Depends(get_settings)) -> dict:
    """A page of accounts, newest first. Each capacity row also shows `left_today`: what the next claim will see.
    A row whose pool day is over goes back to the pool's budget (unknown without one), as `pool.lock_capacity` rolls
    it; the stored `left_n` and `day_anchor` are shown as they are."""
    if not 1 <= limit <= MAX_PAGE or offset < 0:
        raise ApiError("input.invalid", "limit")
    async with tx(pool) as conn:
        rows = await (await conn.execute(_LIST, {"p": provider, "limit": limit, "offset": offset})).fetchall()
        total = (await (await conn.execute("SELECT count(*) FROM accounts WHERE (%(p)s::text IS NULL OR "
                                           "provider = %(p)s)", {"p": provider})).fetchone())[0]
        ids = [r[0] for r in rows]
        capacity = await (await conn.execute(
            "SELECT account_id, pool, left_n, source, day_anchor, rest_until, rest_reason FROM account_capacity "
            "WHERE account_id = ANY(%s) ORDER BY pool", (ids,))).fetchall()
        now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]   # the clock the claim uses
        made = await (await conn.execute(
            "SELECT account_id, media_type, count(*) FROM jobs WHERE account_id = ANY(%s) AND status = 'done' "
            "GROUP BY account_id, media_type", (ids,))).fetchall()
        spent = {(a, p, d): n for a, p, d, n in await (await conn.execute(
            "SELECT account_id, pool, account_day, sum(amount) FROM account_charges WHERE account_id = ANY(%s) "
            "AND refunded_at IS NULL GROUP BY account_id, pool, account_day", (ids,))).fetchall()}
    configs = {name: pool_configs(registry.spec(name).caps, settings.get) for name in registry.names()}
    provider_of = {r[0]: r[1] for r in rows}
    pools: dict[Any, list[dict]] = {}
    for account_id, pool_name, left_n, source, anchor, rest_until, rest_reason in capacity:
        cfg = configs.get(provider_of[account_id], {}).get(pool_name)
        paid = spent.get((account_id, pool_name, anchor), 0)
        _, left_today, drift = pool_point(cfg, left_n, source, anchor, now, paid)
        pools.setdefault(account_id, []).append({
            "pool": pool_name, "left_n": left_n, "left_today": left_today,
            "budget": None if cfg is None else cfg.budget, "drift": drift, "source": source,
            "day_anchor": None if anchor is None else str(anchor), "rest_until_ms": ms(rest_until),
            "rest_reason": rest_reason})
    items = []
    for row in rows:
        item = dict(zip(_COLUMNS, row, strict=True))
        for name in _TIMES:
            item[name.removesuffix("_at") + "_ms"] = ms(item.pop(name))
        item["id"] = str(item["id"])
        item["capacity"] = pools.get(row[0], [])
        item["made"] = {"video": 0, "image": 0}
        items.append(item)
    by_id = {item["id"]: item for item in items}
    for account_id, media_type, count in made:
        by_id[str(account_id)]["made"][media_type] = count
    return {"items": items, "total": total}


@router.patch("/accounts/{account_id}")
async def patch_account(account_id: uuid.UUID, body: AccountPatch,
                        pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Label, proxy and enable. A proxy or enable change is refused (409) while a worker holds a live lease on the
    account; the account row is locked first, so a lease cannot appear between the check and the write. A proxy that
    was set is probed again by a queued `proxy_recheck` command (the api cannot write `proxy_ok`). Enabling a
    disabled account also queues a `wake` (its cooldown and pool rests are cleared by the worker), so that
    enabling wakes both pools."""
    fields = body.model_fields_set
    if not fields:
        raise ApiError("input.invalid")
    sets: list[str] = []
    params: list[Any] = []
    set_proxy = bool(body.proxy)
    if "label" in fields:
        if body.label is None:
            raise ApiError("input.invalid", "label")
        sets.append("label = %s")
        params.append(body.label)
    if "proxy" in fields:
        host = port = auth = None
        if set_proxy:
            try:
                host, port, plain = parse_proxy(body.proxy or "")
            except ValueError:
                raise ApiError("input.invalid", "proxy") from None
            auth = None if plain is None else crypto.seal(crypto.public_key_from_env(), plain.encode())
        sets.append("proxy_host = %s, proxy_port = %s, proxy_auth_enc = %s")
        params += [host, port, auth]
    if "enabled" in fields:
        if body.enabled is None:
            raise ApiError("input.invalid", "enabled")
        if body.enabled:
            sets.append("state = CASE WHEN state = 'disabled' THEN 'available' ELSE state END, "
                        "state_reason = CASE WHEN state = 'disabled' THEN NULL ELSE state_reason END")
        else:
            sets.append("state = 'disabled', state_reason = 'disabled by admin'")
    command_id = wake_id = None
    label_only = fields == {"label"}
    try:
        async with tx(pool, hold_s=LABEL_WAIT_S + 3 if label_only else TX_HOLD_S) as conn:
            if label_only:
                # A label edit is always allowed: it waits for a worker that holds the row instead of answering
                # 409. The UPDATE takes the row lock itself; a missing row updates nothing.
                await conn.execute("SELECT set_config('lock_timeout', %s, true), "
                                   "set_config('statement_timeout', %s, true)",
                                   ("%ds" % LABEL_WAIT_S, "%ds" % (LABEL_WAIT_S + 2)))
                was = None
            else:
                if not await lock_account(conn, account_id):
                    raise ApiError("resource.not_found")
                if fields & {"proxy", "enabled"} and (await (await conn.execute(IN_USE, (account_id,))).fetchone())[0]:
                    raise ApiError("pool.account_busy")
                was = (await (await conn.execute("SELECT state FROM accounts WHERE id = %s",
                                                 (account_id,))).fetchone())[0]
            row = await (await conn.execute(
                "UPDATE accounts SET " + ", ".join(sets) + ", updated_at = clock_timestamp() WHERE id = %s "
                "RETURNING provider, label, state, proxy_host, proxy_port", (*params, account_id))).fetchone()
            if row is None:
                raise ApiError("resource.not_found")
            if set_proxy:
                command_id = await enqueue(conn, row[0], "proxy_recheck", account_id=account_id,
                                           args={"account_id": str(account_id)})
            if body.enabled and was == "disabled":
                wake_id = await enqueue(conn, row[0], "wake", account_id=account_id,
                                        args={"account_id": str(account_id)})
    except psycopg.errors.LockNotAvailable:       # a worker holds the row right now: the account is in use
        raise ApiError("pool.account_busy") from None
    return {"id": str(account_id), "provider": row[0], "label": row[1], "state": row[2], "proxy_host": row[3],
            "proxy_port": row[4], "command_id": None if command_id is None else str(command_id),
            "wake_command_id": None if wake_id is None else str(wake_id)}


@router.get("/accounts/{account_id}/screenshot")
async def account_screenshot(account_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool),
                             store: ObjectStore = Depends(get_store)) -> Response:
    async with tx(pool) as conn:
        row = await (await conn.execute("SELECT object_key FROM account_shots WHERE account_id = %s",
                                        (account_id,))).fetchone()
    if row is None:
        raise ApiError("resource.not_found")
    try:
        data = await store.read(row[0])
    except storage.ObjectNotFound:
        raise ApiError("resource.not_found") from None
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


# ---- commands -------------------------------------------------------------------------------------------------------
async def _account_command(pool: AsyncConnectionPool, account_id: uuid.UUID, kind: str) -> dict:
    """One command row for an account of any state. A delete names its account in `args` only: its own row must not
    reference the account it deletes."""
    async with tx(pool) as conn:
        row = await (await conn.execute("SELECT provider FROM accounts WHERE id = %s", (account_id,))).fetchone()
        if row is None:
            raise ApiError("resource.not_found")
        command_id = await enqueue(conn, row[0], kind, account_id=None if kind == "delete_account" else account_id,
                                   args={"account_id": str(account_id)})
    return {"command_id": str(command_id)}


@router.delete("/accounts/{account_id}", status_code=202)
async def delete_account(account_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Queue the delete of an UNUSED account; the worker refuses (the command fails) when it has a job or a charge."""
    return await _account_command(pool, account_id, "delete_account")


@router.post("/accounts/{account_id}/wake", status_code=202)
async def wake_account(account_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    return await _account_command(pool, account_id, "wake")


@router.post("/accounts/{account_id}/check", status_code=202)
async def check_account(account_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    return await _account_command(pool, account_id, "check")


@router.post("/accounts/{account_id}/proxy-recheck", status_code=202)
async def proxy_recheck(account_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    return await _account_command(pool, account_id, "proxy_recheck")


async def _provider_command(pool: AsyncConnectionPool, registry: ProviderRegistry, provider: str, kind: str) -> dict:
    spec_of(registry, provider)
    async with tx(pool) as conn:
        return {"command_id": str(await enqueue(conn, provider, kind))}


@router.post("/providers/{provider}/check-all", status_code=202)
async def check_all(provider: str, pool: AsyncConnectionPool = Depends(get_pool),
                    registry: ProviderRegistry = Depends(get_registry)) -> dict:
    return await _provider_command(pool, registry, provider, "check_all")


@router.post("/providers/{provider}/refresh-catalog", status_code=202)
async def refresh_catalog(provider: str, pool: AsyncConnectionPool = Depends(get_pool),
                          registry: ProviderRegistry = Depends(get_registry)) -> dict:
    return await _provider_command(pool, registry, provider, "refresh_catalog")


@router.get("/commands/{command_id}")
async def command_status(command_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """What the page polls. The args are not returned (they may hold a sealed blob); the result is what the worker
    stored, already redacted."""
    async with tx(pool) as conn:
        row = await (await conn.execute(
            "SELECT id, provider, kind, account_id, job_id, status, result, attempts, created_at, finished_at "
            "FROM commands WHERE id = %s", (command_id,))).fetchone()
    if row is None:
        raise ApiError("resource.not_found")
    return {"id": str(row[0]), "provider": row[1], "kind": row[2],
            "account_id": None if row[3] is None else str(row[3]), "job_id": None if row[4] is None else str(row[4]),
            "status": row[5], "result": row[6], "attempts": row[7], "created_ms": ms(row[8]),
            "finished_ms": ms(row[9])}
