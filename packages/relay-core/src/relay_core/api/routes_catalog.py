"""Catalog routes (contracts §8): the key's points, the models it may use, and a provider's options.

Every route needs a key. The model lists show a key only the models its allow-list admits (`keys.model_allowed`); a
model it may not use is as unknown to it as a model that does not exist.
"""

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from relay_core import keys
from relay_core.api.deps import get_pool, get_registry, get_settings, require_key
from relay_core.api.errors_http import ApiError
from relay_core.keys import ApiKey
from relay_core.providers import ModelSpec, ProviderRegistry, ProviderSpec, RegistryError
from relay_core.settings import Settings

router = APIRouter()

GEMINI_METHODS = ["generateContent", "streamGenerateContent"]
# used_day counts only for the wallet's own day: a stale anchor means the day counter starts again at 0.
_WALLETS = """
SELECT w.provider, w.kind, w.day_limit, w.total_limit,
       CASE WHEN w.day_anchor IS DISTINCT FROM d.today THEN 0 ELSE w.used_day END, w.used_total
  FROM key_wallets w,
       (SELECT (clock_timestamp() AT TIME ZONE (SELECT tz FROM billing_pin))::date AS today) d
 WHERE w.key_id = %s ORDER BY w.provider, w.kind
"""


def _allowed(registry: ProviderRegistry, key: ApiKey) -> list[tuple[ProviderSpec, ModelSpec]]:
    """Every (provider, model) the key may use, in provider then catalog order."""
    specs = [registry.spec(n) for n in registry.names()]
    canonical = {name: m.id for s in specs for m in s.catalog() for name in m.names()}
    return [(s, m) for s in specs for m in s.catalog() if keys.model_allowed(key.models, m.id, canonical)]


def model_entry(spec: ProviderSpec, m: ModelSpec) -> dict:
    """A catalog entry (contracts 8); the admin's model list uses it too. `name` is never empty: the id stands in."""
    return {"id": m.id, "name": m.display_name or m.id, "object": "model", "owned_by": spec.name, "kind": m.media_type,
            "operations": list(m.operations), "aliases": list(m.aliases),
            "limits": {**m.limits, "max_prompt_chars": spec.max_prompt_chars},
            "prices": dict(m.prices), "is_alias": False}


def _gemini_resource(name: str, spec: ProviderSpec, m: ModelSpec) -> dict:
    return {"name": "models/" + name, "baseModelId": m.id, "version": "relay", "displayName": name,
            "description": "%s generation model (%s)" % (m.media_type, spec.name), "inputTokenLimit": 0,
            "outputTokenLimit": 0, "supportedGenerationMethods": GEMINI_METHODS}


@router.get("/v1/models")
async def list_models(key: ApiKey = Depends(require_key), registry: ProviderRegistry = Depends(get_registry)):
    return {"object": "list", "data": [model_entry(s, m) for s, m in _allowed(registry, key)]}


@router.get("/v1/models/aliases")
async def list_aliases(key: ApiKey = Depends(require_key), registry: ProviderRegistry = Depends(get_registry)):
    """One entry per alias, carrying the model it runs as `base_model`."""
    return {"object": "list", "data": [{**model_entry(s, m), "id": alias, "aliases": [], "base_model": m.id,
                                        "is_alias": True}
                                       for s, m in _allowed(registry, key) for alias in m.aliases]}


@router.get("/v1beta/models")
@router.get("/models")
async def list_gemini_models(key: ApiKey = Depends(require_key),
                             registry: ProviderRegistry = Depends(get_registry)):
    return {"models": [_gemini_resource(m.id, s, m) for s, m in _allowed(registry, key)]}


@router.get("/v1beta/models/{model}")
@router.get("/models/{model}")
async def get_gemini_model(model: str, key: ApiKey = Depends(require_key),
                           registry: ProviderRegistry = Depends(get_registry)):
    """An id or an alias; unknown, or not allowed for this key: 404 (Gemini body, by path)."""
    for s, m in _allowed(registry, key):
        if model in m.names():
            return _gemini_resource(model, s, m)
    raise ApiError("resource.not_found", "model")


@router.get("/v1/providers/{provider}/options")
async def provider_options(provider: str, key: ApiKey = Depends(require_key),
                           registry: ProviderRegistry = Depends(get_registry),
                           pool: AsyncConnectionPool = Depends(get_pool)):
    """`spec.extras(snapshots)` over the provider's stored snapshots. A provider the key may use no model of is
    unknown to it."""
    try:
        spec = registry.spec(provider)
    except RegistryError:
        raise ApiError("resource.not_found", "provider") from None
    if not any(s.name == provider for s, _ in _allowed(registry, key)):
        raise ApiError("resource.not_found", "provider")
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT name, data FROM provider_snapshots WHERE provider = %s",
                                         (provider,))).fetchall()
    return spec.extras({name: data for name, data in rows})


@router.get("/v1/points")
async def points(key: ApiKey = Depends(require_key), pool: AsyncConnectionPool = Depends(get_pool),
                 settings: Settings = Depends(get_settings)):
    """`{provider: {kind: {day_limit, used_day, total_limit, used_total}}}` plus `models` (the key's allow-list),
    `unfinished_jobs` and `max_unfinished_jobs` (null = no cap). A limit of null is unlimited, 0 is closed."""
    async with pool.connection() as conn:
        wallets = await (await conn.execute(_WALLETS, (key.id,))).fetchall()
        unfinished = (await (await conn.execute(
            "SELECT count(*) FROM jobs WHERE key_id = %s AND status NOT IN ('done', 'failed')",
            (key.id,))).fetchone())[0]
    cap = settings.get("max_unfinished_default") if key.max_unfinished is None else key.max_unfinished
    out: dict = {}
    for provider, kind, day_limit, total_limit, used_day, used_total in wallets:
        out.setdefault(provider, {})[kind] = {"day_limit": day_limit, "used_day": used_day,
                                              "total_limit": total_limit, "used_total": used_total}
    return {**out, "models": list(key.models), "unfinished_jobs": unfinished,
            "max_unfinished_jobs": cap or None}
