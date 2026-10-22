"""Admin: API keys and their per-provider wallets.

A key is made with one wallet per declared (provider, kind) pair: a pair the admin does not name is CLOSED, a named one
is `closed`, a `limit` (a day and/or a total number) or `unlimited` (set on purpose, never by omission). The raw key is
in the create answer once and nowhere else; no route returns a hash. `max_unfinished`: NULL = the
`max_unfinished_default` setting, 0 = unlimited.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool
from pydantic import Field, field_validator, model_validator

from relay_core import keys
from relay_core.admin.routes_common import Strict, ms
from relay_core.api.deps import get_pool, get_registry, get_settings
from relay_core.api.errors_http import ApiError
from relay_core.api.routes_catalog import model_entry
from relay_core.db import tx
from relay_core.providers import ProviderRegistry
from relay_core.settings import Settings

router = APIRouter()


@router.get("/models")
async def list_models(registry: ProviderRegistry = Depends(get_registry)) -> dict:
    """Every model of every provider, unfiltered: the choices of a key's allow-list (the admin has no API key to
    read `/v1/models` with). Same entries as the public catalog."""
    specs = [registry.spec(name) for name in registry.names()]
    return {"object": "list", "data": [model_entry(s, m) for s in specs for m in s.catalog()]}

Limits = tuple[int | None, int | None]       # (day_limit, total_limit)


class WalletIn(Strict):
    provider: str
    kind: str
    mode: Literal["closed", "limit", "unlimited"]
    day_limit: int | None = Field(default=None, ge=1)
    total_limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> "WalletIn":
        limited = self.day_limit is not None or self.total_limit is not None
        if self.mode == "limit" and not limited:
            raise ValueError("a limit needs day_limit or total_limit")
        if self.mode != "limit" and limited:
            raise ValueError("only mode 'limit' takes a limit")
        return self

    def limits(self) -> Limits:
        return {"closed": (0, None), "unlimited": (None, None)}.get(self.mode, (self.day_limit, self.total_limit))


class KeyCreate(Strict):
    label: str = Field(default="", max_length=100)
    models: list[str] = Field(default_factory=lambda: [keys.ANY_MODEL], min_length=1, max_length=200)
    max_unfinished: int | None = Field(default=None, ge=0)
    wallets: list[WalletIn] = Field(default_factory=list, max_length=100)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        # the 100-character limit is counted before this strip; a label of spaces is no label
        return value.strip()


class KeyPatch(Strict):
    """Only the fields present change. `max_unfinished: null` goes back to the default."""

    label: str | None = Field(default=None, max_length=100)
    models: list[str] | None = Field(default=None, min_length=1, max_length=200)
    max_unfinished: int | None = Field(default=None, ge=0)
    wallets: list[WalletIn] | None = Field(default=None, max_length=100)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str | None) -> str | None:
        # `None` (not sent) stays None: the route refuses an explicit null
        return None if value is None else value.strip()


def declared_pairs(registry: ProviderRegistry) -> list[tuple[str, str]]:
    return [(name, kind) for name in registry.names() for kind in registry.spec(name).caps.wallet_kinds]


def _wallet_map(wallets: list[WalletIn], declared: list[tuple[str, str]]) -> dict[tuple[str, str], Limits]:
    chosen: dict[tuple[str, str], Limits] = {}
    for i, w in enumerate(wallets):
        pair = (w.provider, w.kind)
        if pair not in declared or pair in chosen:
            raise ApiError("input.invalid", "wallets.%d" % i)
        chosen[pair] = w.limits()
    return chosen


def _models(models: list[str]) -> list[str]:
    clean = [m.strip() for m in models]
    if not all(clean):
        raise ApiError("input.invalid", "models")
    return clean


_LIST = (
    "SELECT k.id, k.key_prefix, k.label, k.enabled, k.models, k.max_unfinished, k.created_at, k.updated_at, "
    "(SELECT count(*) FROM jobs j WHERE j.key_id = k.id), "
    "(SELECT count(*) FROM jobs j WHERE j.key_id = k.id AND j.status NOT IN ('done', 'failed')) "
    "FROM api_keys k WHERE (%(id)s::uuid IS NULL OR k.id = %(id)s) ORDER BY k.created_at DESC, k.id")


async def _views(conn, settings: Settings, key_id=None) -> list[dict]:
    rows = await (await conn.execute(_LIST, {"id": key_id})).fetchall()
    wallets: dict[Any, list[dict]] = {}
    for kid, provider, kind, day, total, used_day, used_total, anchor in await (await conn.execute(
            "SELECT key_id, provider, kind, day_limit, total_limit, used_day, used_total, day_anchor FROM key_wallets "
            "WHERE (%(id)s::uuid IS NULL OR key_id = %(id)s) ORDER BY provider, kind", {"id": key_id})).fetchall():
        wallets.setdefault(kid, []).append({
            "provider": provider, "kind": kind, "day_limit": day, "total_limit": total, "used_day": used_day,
            "used_total": used_total, "day_anchor": None if anchor is None else str(anchor),
            "mode": "closed" if 0 in (day, total) else "unlimited" if day is None and total is None else "limit"})
    default = settings.get("max_unfinished_default")
    return [{"id": str(r[0]), "prefix": r[1], "label": r[2], "enabled": r[3], "models": list(r[4]),
             "max_unfinished": r[5], "max_unfinished_effective": default if r[5] is None else r[5],
             "created_ms": ms(r[6]), "updated_ms": ms(r[7]), "jobs": r[8], "unfinished": r[9],
             "wallets": wallets.get(r[0], [])} for r in rows]


@router.post("/keys")
async def create_key(body: KeyCreate, pool: AsyncConnectionPool = Depends(get_pool),
                     registry: ProviderRegistry = Depends(get_registry),
                     settings: Settings = Depends(get_settings)) -> dict:
    """The raw key is in this answer and never again. Every declared wallet exists afterwards (unnamed: closed)."""
    declared = declared_pairs(registry)
    wallets = _wallet_map(body.wallets, declared)
    async with tx(pool) as conn:
        made = await keys.create_key(conn, label=body.label, models=_models(body.models),
                                     max_unfinished=body.max_unfinished, declared=declared, wallets=wallets)
        view = (await _views(conn, settings, made.id))[0]
    return {**view, "key": made.raw}


@router.get("/keys")
async def list_keys(pool: AsyncConnectionPool = Depends(get_pool), settings: Settings = Depends(get_settings)) -> dict:
    async with tx(pool) as conn:
        return {"items": await _views(conn, settings)}


@router.get("/keys/{key_id}")
async def get_key(key_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool),
                  settings: Settings = Depends(get_settings)) -> dict:
    async with tx(pool) as conn:
        views = await _views(conn, settings, key_id)
    if not views:
        raise ApiError("resource.not_found")
    return views[0]


@router.patch("/keys/{key_id}")
async def patch_key(key_id: uuid.UUID, body: KeyPatch, pool: AsyncConnectionPool = Depends(get_pool),
                    registry: ProviderRegistry = Depends(get_registry),
                    settings: Settings = Depends(get_settings)) -> dict:
    fields = body.model_fields_set
    if not fields:
        raise ApiError("input.invalid")
    if "label" in fields and body.label is None:
        raise ApiError("input.invalid", "label")
    if "models" in fields and body.models is None:
        raise ApiError("input.invalid", "models")
    wallets = {}
    if "wallets" in fields:
        if body.wallets is None:
            raise ApiError("input.invalid", "wallets")
        wallets = _wallet_map(body.wallets, declared_pairs(registry))
    async with tx(pool) as conn:
        if (await (await conn.execute("SELECT 1 FROM api_keys WHERE id = %s FOR NO KEY UPDATE", (key_id,))
                   ).fetchone()) is None:
            raise ApiError("resource.not_found")
        if "label" in fields:
            await conn.execute("UPDATE api_keys SET label = %s, updated_at = clock_timestamp() WHERE id = %s",
                               (body.label, key_id))
        if "models" in fields:
            await keys.set_models(conn, key_id, _models(body.models or []))
        if "max_unfinished" in fields:
            await keys.set_max_unfinished(conn, key_id, body.max_unfinished)
        for (provider, kind), (day, total) in wallets.items():
            cur = await conn.execute(
                "UPDATE key_wallets SET day_limit = %s, total_limit = %s WHERE key_id = %s AND provider = %s "
                "AND kind = %s", (day, total, key_id, provider, kind))
            if cur.rowcount == 0:                     # a declared pair with no row: a bug, not an admin choice
                raise ApiError("input.invalid", "wallets")
        return (await _views(conn, settings, key_id))[0]


async def _set_enabled(pool: AsyncConnectionPool, key_id: uuid.UUID, enabled: bool) -> dict:
    async with tx(pool) as conn:
        if not await keys.set_enabled(conn, key_id, enabled):
            raise ApiError("resource.not_found")
    return {"id": str(key_id), "enabled": enabled}


@router.post("/keys/{key_id}/enable")
async def enable_key(key_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    return await _set_enabled(pool, key_id, True)


@router.post("/keys/{key_id}/disable")
async def disable_key(key_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    return await _set_enabled(pool, key_id, False)


@router.delete("/keys/{key_id}")
async def delete_key(key_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """A key with no job and no ledger row is deleted; any other key is only disabled (the foreign keys decide)."""
    async with tx(pool) as conn:
        result = await keys.delete_key(conn, key_id)
    if result == "missing":
        raise ApiError("resource.not_found")
    return {"id": str(key_id), "result": result}
