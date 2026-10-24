"""Admin: accounts and the commands that act on them.

The api holds the PUBLIC key only: an import seals each credential and each proxy password and nothing here can open
one. The api also cannot delete an account or write a worker-owned column (`check_due_at`, `cooldown_*`, `strikes`,
`strike_at`, `proxy_ok`, `proxy_checked_at`): delete, wake, check, check all and proxy re-check each insert ONE command
row and answer with its id; the provider's worker runs it and the page polls `GET /commands/{id}`. A proxy edit also
queues the proxy re-check, since the api cannot refresh `proxy_ok` itself.

An import learns an account's identity only when the operator gives it (`identity`, the gemini Google account email);
otherwise the first check does. A credential for a known identity is inserted like any other (with no identity when
another account already holds it); the worker's first check merges it into the existing row.

The detail's history (`GET /accounts/{id}/history`) is what the worker recorded from check and job outcomes: the
health per hour and the newest errors.
"""

import logging
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from psycopg_pool import AsyncConnectionPool
from pydantic import Field

from relay_core import crypto, storage
from relay_core.admin.routes_common import MAX_PAGE, Strict, enqueue, ms, parse_proxy, pool_view, spec_of
from relay_core.api.deps import get_pool, get_registry, get_settings, get_store, skip_body_log
from relay_core.api.errors_http import ApiError
from relay_core.db import lock_account, tx
from relay_core.db.pool import TX_HOLD_S
from relay_core.pool import pool_configs, pool_states
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
    "EXISTS (SELECT 1 FROM account_shots s WHERE s.account_id = a.id), a.provider_state->>'composer', "
    "(SELECT count(*) FROM leases l WHERE l.account_id = a.id AND l.purpose = 'job' AND l.until > clock_timestamp()), "
    "a.proxy_exit_ip, a.proxy_latency_ms, a.proxy_exit_at, a.check_failed_at, "
    "(SELECT taken_at FROM account_shots s WHERE s.account_id = a.id), "
    "a.tier, a.tier_read_at IS NOT NULL, a.cycle_start "
    "FROM accounts a WHERE (%(p)s::text IS NULL OR a.provider = %(p)s) "
    "ORDER BY a.created_at DESC, a.id LIMIT %(limit)s OFFSET %(offset)s")
_COLUMNS = ("id", "provider", "label", "identity", "display_name", "state", "state_reason", "proxy_host", "proxy_port",
            "has_proxy_auth", "proxy_ok", "proxy_checked_at", "strikes", "cooldown_until", "cooldown_reason",
            "last_error", "last_used_at", "last_checked_at", "check_due_at", "credential_gen", "created_at", "in_use",
            "has_screenshot", "composer", "jobs_in_use", "proxy_exit_ip", "proxy_latency_ms", "proxy_exit_at",
            "check_failed_at", "screenshot_at", "tier", "tier_read", "cycle_start")
_TIMES = ("proxy_checked_at", "cooldown_until", "last_used_at", "last_checked_at", "check_due_at", "created_at",
          "proxy_exit_at", "check_failed_at", "screenshot_at")
# the jobs each account of the page took since the start of the admin's day (`dashboard_tz`), and how many failed
_TODAY = ("SELECT account_id, count(*), count(*) FILTER (WHERE status = 'failed') FROM jobs "
          "WHERE account_id = ANY(%(ids)s) "
          "AND created_at >= date_trunc('day', clock_timestamp() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s "
          "GROUP BY account_id")
HISTORY_DAYS = 14
_INSERT = ("INSERT INTO accounts (provider, label, identity, credential_enc, proxy_host, proxy_port, proxy_auth_enc, "
           "check_due_at) VALUES (%(p)s, %(l)s, %(i)s, %(c)s, %(h)s, %(o)s, %(a)s, clock_timestamp()) RETURNING id")


class ImportItem(Strict):
    credential: str = Field(min_length=1, max_length=MAX_CREDENTIAL_CHARS)
    label: str = Field(default="", max_length=100)
    proxy: str | None = Field(default=None, max_length=500)
    # who the credential belongs to, when the operator knows it (gemini: the Google account email)
    identity: str | None = Field(default=None, max_length=320)


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
    ready: list[tuple[int, str, bytes, tuple[str, int, bytes | None] | None, str | None]] = []
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
        ready.append((index, item.label or parsed.hint, crypto.seal(public, parsed.blob), proxy,
                      (item.identity or "").strip() or None))
    imported = []
    if ready:
        async with tx(pool) as conn:
            for index, label, sealed, proxy, identity in ready:
                host, port, auth = proxy if proxy else (None, None, None)
                values = {"p": body.provider, "l": label, "i": identity, "c": sealed, "h": host, "o": port, "a": auth}
                try:
                    async with conn.transaction():          # a savepoint: a taken identity undoes this row only
                        row = await (await conn.execute(_INSERT, values)).fetchone()
                except psycopg.errors.UniqueViolation:
                    # another account of the provider has the identity (an earlier import, or one committed while
                    # this one ran): the row goes in without it, and the first check merges it into that account
                    row = await (await conn.execute(_INSERT, {**values, "i": None})).fetchone()
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
    """A page of accounts, newest first. Each pool in `capacity` shows `left_today`: what the next claim will see (the
    balance the upstream reported while its refill has not come, else the tier's budget less the unrefunded charges of
    the window, as `pool.lock_capacity` reads it), its `source`, its windows and its rest. The account's `tier` (null =
    free), whether the upstream `tier_read` it and its `cycle_start` ride along."""
    if not 1 <= limit <= MAX_PAGE or offset < 0:
        raise ApiError("input.invalid", "limit")
    async with tx(pool) as conn:
        rows = await (await conn.execute(_LIST, {"p": provider, "limit": limit, "offset": offset})).fetchall()
        total = (await (await conn.execute("SELECT count(*) FROM accounts WHERE (%(p)s::text IS NULL OR "
                                           "provider = %(p)s)", {"p": provider})).fetchone())[0]
        ids = [r[0] for r in rows]
        configs = {name: pool_configs(name, registry.spec(name).caps, settings.get) for name in registry.names()}
        _, states = await pool_states(conn, {r[0]: (r[-3], configs.get(r[1], {})) for r in rows})
        made = await (await conn.execute(
            "SELECT account_id, media_type, count(*) FROM jobs WHERE account_id = ANY(%s) AND status = 'done' "
            "GROUP BY account_id, media_type", (ids,))).fetchall()
        today = await (await conn.execute(_TODAY, {"ids": ids, "tz": settings.get("dashboard_tz")})).fetchall()
    items = []
    for row in rows:
        item = dict(zip(_COLUMNS, row, strict=True))
        for name in _TIMES:
            item[name.removesuffix("_at") + "_ms"] = ms(item.pop(name))
        item["id"] = str(item["id"])
        item["cycle_start"] = None if item["cycle_start"] is None else str(item["cycle_start"])
        item["capacity"] = [pool_view(cfg, states[(row[0], name)]) for name, cfg in configs.get(row[1], {}).items()]
        item["made"] = {"video": 0, "image": 0}
        item["jobs_today"] = {"total": 0, "failed": 0}
        items.append(item)
    by_id = {item["id"]: item for item in items}
    for account_id, media_type, count in made:
        by_id[str(account_id)]["made"][media_type] = count
    for account_id, count, failed in today:
        by_id[str(account_id)]["jobs_today"] = {"total": count, "failed": failed}
    return {"items": items, "total": total}


@router.get("/accounts/{account_id}/history")
async def account_history(account_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """What the detail draws beyond the list: `hours`, the account's health per UTC hour of the last HISTORY_DAYS
    days as [hour_ms, worst] (0 usable, 1 asleep, 2 dead or proxy down; an hour with no check or job outcome is
    absent), oldest first; and `errors`, its newest errors with their time, the job they came from (null for a check)
    and that job's own error code and text."""
    async with tx(pool) as conn:
        if await (await conn.execute("SELECT 1 FROM accounts WHERE id = %s", (account_id,))).fetchone() is None:
            raise ApiError("resource.not_found")
        hours = await (await conn.execute(
            "SELECT hour, worst FROM account_health WHERE account_id = %s "
            "AND hour >= date_trunc('hour', clock_timestamp(), 'UTC') - make_interval(days => %s) ORDER BY hour",
            (account_id, HISTORY_DAYS))).fetchall()
        errors = await (await conn.execute(
            "SELECT e.at, e.code, e.job_id, j.error_code, j.error FROM account_errors e "
            "LEFT JOIN jobs j ON j.id = e.job_id WHERE e.account_id = %s ORDER BY e.at DESC, e.id DESC", (account_id,))
        ).fetchall()
    return {"hours": [[ms(h), w] for h, w in hours],
            "errors": [{"at_ms": ms(at), "code": code, "job_id": None if job is None else str(job),
                        "job_error_code": job_code, "job_error": job_text}
                       for at, code, job, job_code, job_text in errors]}


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
