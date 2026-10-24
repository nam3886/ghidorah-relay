"""Catalog routes: the key's points, the models it may use, and a provider's options.

Every route needs a key. The model lists show a key only the models its allow-list admits (`keys.model_allowed`); a
model it may not use is as unknown to it as a model that does not exist. A model the admin paused
(`<p>.disabled_models`) is left out of every list the same way; a request for it is refused `model.disabled`.
"""

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from relay_core import billing, keys, pricing
from relay_core.api.deps import get_pool, get_registry, get_settings, require_key
from relay_core.api.doors import model_disabled
from relay_core.api.errors_http import ApiError
from relay_core.keys import ApiKey
from relay_core.providers import ModelSpec, ProviderRegistry, ProviderSpec, RegistryError
from relay_core.settings import Settings

router = APIRouter()

GEMINI_METHODS = ["generateContent", "streamGenerateContent"]
# used_day counts only for the wallet's own day: a stale anchor means the day counter starts again at 0.
_WALLETS = """
SELECT w.scope, w.day_limit, w.total_limit,
       CASE WHEN w.day_anchor IS DISTINCT FROM d.today THEN 0 ELSE w.used_day END, w.used_total
  FROM key_wallets w,
       (SELECT (clock_timestamp() AT TIME ZONE (SELECT tz FROM billing_pin))::date AS today) d
 WHERE w.key_id = %s ORDER BY (w.scope <> '*'), w.scope
"""
# What the key spent today from its points wallet, read from the ledger: the charges of today's billing day less the
# refunds of those same charges (a refund of an earlier day's charge gives back that day's spend, not today's).
_SPENT_TODAY = """
SELECT COALESCE(sum(c.amount), 0) - COALESCE(sum(r.amount), 0)
  FROM ledger c LEFT JOIN ledger r ON r.refund_of = c.id
 WHERE c.key_id = %s AND c.scope = '*' AND c.entry = 'charge'
   AND c.billing_day = (clock_timestamp() AT TIME ZONE (SELECT tz FROM billing_pin))::date
"""


def _left(limit: int | None, used: int) -> int | None:
    """What a limit leaves: None for no limit (unlimited), never below 0."""
    return None if limit is None else max(limit - used, 0)


def _allowed(registry: ProviderRegistry, key: ApiKey, settings: Settings) -> list[tuple[ProviderSpec, ModelSpec]]:
    """Every (provider, model) the key may use and the admin has not paused, in provider then catalog order."""
    specs = [registry.spec(n) for n in registry.names()]
    canonical = {name: m.id for s in specs for m in s.catalog() for name in m.names()}
    return [(s, m) for s in specs for m in s.catalog()
            if keys.model_allowed(key.models, m.id, canonical) and not model_disabled(settings, s, m)]


def model_entry(spec: ProviderSpec, m: ModelSpec, settings: Settings) -> dict:
    """A catalog entry; the admin's model list uses it too. `name` is never empty: the id stands in. `prices` are the
    live points of each option (`<p>.prices`), null for an option that has no price (a request for it is refused)."""
    return {"id": m.id, "name": m.display_name or m.id, "object": "model", "owned_by": spec.name, "kind": m.media_type,
            "operations": list(m.operations), "aliases": list(m.aliases),
            "limits": {**m.limits, "max_prompt_chars": spec.max_prompt_chars},
            "prices": pricing.option_prices(spec, m, settings), "is_alias": False}


def _gemini_resource(name: str, spec: ProviderSpec, m: ModelSpec) -> dict:
    return {"name": "models/" + name, "baseModelId": m.id, "version": "relay", "displayName": name,
            "description": "%s generation model (%s)" % (m.media_type, spec.name), "inputTokenLimit": 0,
            "outputTokenLimit": 0, "supportedGenerationMethods": GEMINI_METHODS}


@router.get("/v1/models")
async def list_models(key: ApiKey = Depends(require_key), registry: ProviderRegistry = Depends(get_registry),
                      settings: Settings = Depends(get_settings)):
    return {"object": "list", "data": [model_entry(s, m, settings) for s, m in _allowed(registry, key, settings)]}


@router.get("/v1/models/aliases")
async def list_aliases(key: ApiKey = Depends(require_key), registry: ProviderRegistry = Depends(get_registry),
                       settings: Settings = Depends(get_settings)):
    """One entry per alias, carrying the model it runs as `base_model`."""
    return {"object": "list", "data": [{**model_entry(s, m, settings), "id": alias, "aliases": [], "base_model": m.id,
                                        "is_alias": True}
                                       for s, m in _allowed(registry, key, settings) for alias in m.aliases]}


@router.get("/v1beta/models")
@router.get("/models")
async def list_gemini_models(key: ApiKey = Depends(require_key), registry: ProviderRegistry = Depends(get_registry),
                             settings: Settings = Depends(get_settings)):
    return {"models": [_gemini_resource(m.id, s, m) for s, m in _allowed(registry, key, settings)]}


@router.get("/v1beta/models/{model}")
@router.get("/models/{model}")
async def get_gemini_model(model: str, key: ApiKey = Depends(require_key),
                           registry: ProviderRegistry = Depends(get_registry),
                           settings: Settings = Depends(get_settings)):
    """An id or an alias; unknown, not allowed for this key or paused: 404 (Gemini body, by path)."""
    for s, m in _allowed(registry, key, settings):
        if model in m.names():
            return _gemini_resource(model, s, m)
    raise ApiError("resource.not_found", "model")


@router.get("/v1/providers/{provider}/options")
async def provider_options(provider: str, key: ApiKey = Depends(require_key),
                           registry: ProviderRegistry = Depends(get_registry),
                           pool: AsyncConnectionPool = Depends(get_pool), settings: Settings = Depends(get_settings)):
    """`spec.extras(snapshots)` over the provider's stored snapshots. A provider the key may use no model of (or whose
    every model it may use is paused) is unknown to it."""
    try:
        spec = registry.spec(provider)
    except RegistryError:
        raise ApiError("resource.not_found", "provider") from None
    if not any(s.name == provider for s, _ in _allowed(registry, key, settings)):
        raise ApiError("resource.not_found", "provider")
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT name, data FROM provider_snapshots WHERE provider = %s",
                                         (provider,))).fetchall()
    return spec.extras({name: data for name, data in rows})


@router.get("/v1/points")
async def points(key: ApiKey = Depends(require_key), pool: AsyncConnectionPool = Depends(get_pool),
                 settings: Settings = Depends(get_settings)):
    """`points` (the key's one points wallet) and `providers` (`{provider: cap}`, each a provider's cap on top of it),
    both `{day_limit, used_day, total_limit, used_total}`, plus `models` (the key's allow-list), `unfinished_jobs` and
    `max_unfinished_jobs` (null = no cap). A limit of null is unlimited, 0 is closed. A job is charged to the points
    wallet and to its provider's cap, so it needs both open.

    Read-only additions for a person: `name` (the key's label, null when it has none), `points_left` (what the points
    wallet's day and total limits leave, `{day, total}`, each null when that limit is unlimited; null with no points
    wallet) and `spent_today` (points net of refunds charged today, from the ledger)."""
    async with pool.connection() as conn:
        wallets = await (await conn.execute(_WALLETS, (key.id,))).fetchall()
        unfinished = (await (await conn.execute(
            "SELECT count(*) FROM jobs WHERE key_id = %s AND status NOT IN ('done', 'failed')",
            (key.id,))).fetchone())[0]
        spent = (await (await conn.execute(_SPENT_TODAY, (key.id,))).fetchone())[0]
    cap = settings.get("max_unfinished_default") if key.max_unfinished is None else key.max_unfinished
    rows = {scope: {"day_limit": day_limit, "used_day": used_day, "total_limit": total_limit,
                    "used_total": used_total} for scope, day_limit, total_limit, used_day, used_total in wallets}
    star = rows.pop(billing.STAR, None)
    left = None if star is None else {"day": _left(star["day_limit"], star["used_day"]),
                                      "total": _left(star["total_limit"], star["used_total"])}
    return {"points": star, "providers": rows, "models": list(key.models),
            "unfinished_jobs": unfinished, "max_unfinished_jobs": cap or None,
            "name": key.label or None, "points_left": left, "spent_today": spent}
