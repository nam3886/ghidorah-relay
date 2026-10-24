"""Admin: live settings.

The list shows each registered key with its value, where the value comes from (`db` or `default`), its default, its
description and the values it allows. A put validates before it writes (a refused value leaves the stored one as it
was); a delete restores the default. Each item names its `group` (the provider, or "core", as the `fixed` rows do) and
says whether it is `basic` (one the admin changes often) so the page can group them and fold the rest away. The list
also carries `fixed`, the limits that are code and not settings. The boot-only values (an upstream URL, the billing
timezone, the proxy list...) are configuration of the deployment: they are listed read-only under `env_only` and a put
or delete of one is refused. Every put that changes the stored value and every delete of one adds a row to the change
history (`GET /settings/history`); the list's `last_saved` is its newest row.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool

from relay_core import cgroup
from relay_core.admin.routes_common import MAX_PAGE, Strict, ms
from relay_core.admin.routes_stats import HEARTBEAT_STALE_S
from relay_core.api.deps import get_pool, get_settings
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.limits import fixed_limits
from relay_core.settings import Settings, SettingsError

router = APIRouter()

LOOKBACK_S = 24 * 3600      # a worker silent for longer than the stale threshold plus this is not listed
MAX_WORKER_ROWS = 50
WORKER_ROWS = (
    "SELECT worker_id, provider, started_at, at, limits, extract(epoch FROM clock_timestamp() - at) "
    "FROM worker_heartbeats WHERE at > clock_timestamp() - make_interval(secs => %s) "
    "ORDER BY at DESC, worker_id LIMIT %s")


def _containers(state: Any, workers: list) -> list[dict]:
    """The api's own container limits, then the workers that beat recently, newest first (at most MAX_WORKER_ROWS).
    A worker that stopped cleanly has no row; one that predates the limits has NULL and shows as not reported. `stale`
    is read on the server's clock against HEARTBEAT_STALE_S, as the admin stats do."""
    rows = [{"service": "api", "provider": None, "this_process": True, "reported": True,
             **cgroup.describe(state.container), "boot": None, "stale": False,
             "reported_ms": state.started_ms, "started_ms": state.started_ms}]
    for worker_id, provider, started_at, at, limits, age_s in workers:
        reported = isinstance(limits, dict)
        texts = cgroup.describe(limits) if reported else dict.fromkeys(("cpus", "memory", "pids", "shm"))
        rows.append({"service": worker_id, "provider": provider, "this_process": False, "reported": reported,
                     **texts, "boot": limits.get("boot") if reported else None,
                     "stale": age_s > HEARTBEAT_STALE_S, "reported_ms": ms(at), "started_ms": ms(started_at)})
    return rows


class SettingPut(Strict):
    value: Any


def _item(settings: Settings, key: str) -> dict:
    """`group`: the provider of a namespaced key (`<p>.<name>`), else "core", as in `fixed`. `basic`: shown first.
    `keys`: the keys a dict setting may hold (a provider's price options), null for any other setting."""
    spec = settings.specs()[key]
    return {"key": key, "group": key.partition(".")[0] if "." in key else "core", "basic": spec.basic,
            "type": spec.type.__name__, "value": settings.get(key), "source": settings.source(key),
            "default": spec.default, "description": spec.description, "allowed": spec.allowed, "read_only": False,
            "keys": None if spec.keys is None else list(spec.keys)}


def _known(settings: Settings, key: str) -> None:
    """A key the registry does not hold, or a boot-only one, is refused as a bad `key`."""
    try:
        settings.source(key)
    except SettingsError:
        raise ApiError("input.invalid", "key") from None


@router.get("/settings")
async def list_settings(request: Request, pool: AsyncConnectionPool = Depends(get_pool),
                        settings: Settings = Depends(get_settings)) -> dict:
    async with tx(pool) as conn:
        pin = await (await conn.execute("SELECT tz FROM billing_pin")).fetchone()
        workers = await (await conn.execute(WORKER_ROWS, (HEARTBEAT_STALE_S + LOOKBACK_S, MAX_WORKER_ROWS))
                         ).fetchall()
        saved = await (await conn.execute("SELECT at, by FROM settings_history ORDER BY id DESC LIMIT 1")).fetchone()
    state = request.app.state
    env_only = [
        {"name": "BILLING_TZ", "value": pin[0] if pin else None, "read_only": True},
        {"name": "PUBLIC_BASE_URL", "value": state.public_base_url, "read_only": True},
        {"name": "TRUSTED_PROXIES", "value": ",".join(str(n) for n in state.trusted_proxies), "read_only": True},
    ]
    return {"items": [_item(settings, k) for k in sorted(settings.specs())], "env_only": env_only,
            "fixed": fixed_limits(state.registry), "containers": _containers(state, workers),
            "last_saved": None if saved is None else {"at_ms": ms(saved[0]), "by": saved[1]}}


@router.get("/settings/history")
async def settings_history(limit: int = 100, before: int | None = None,
                           pool: AsyncConnectionPool = Depends(get_pool),
                           settings: Settings = Depends(get_settings)) -> dict:
    """The change history, newest first: who changed which key, from what stored value to what (null = none stored:
    the default), and when. A secret setting's row says only that it changed (`secret`, both values null): a row is
    masked when it was written for a secret, and also when the key is secret NOW or no longer known (a setting made
    secret later must not show the values it once kept). `before` (an id) pages back; `next` is the id to pass for the
    next page, null at the end."""
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError("input.invalid", "limit")
    async with tx(pool) as conn:
        rows = await (await conn.execute(
            "SELECT id, at, key, by, old_value, new_value, secret FROM settings_history "
            "WHERE (%(b)s::bigint IS NULL OR id < %(b)s) ORDER BY id DESC LIMIT %(n)s",
            {"b": before, "n": limit + 1})).fetchall()
    specs = settings.specs()
    items = []
    for row_id, at, key, by, old, new, secret in rows[:limit]:
        hidden = secret or key not in specs or specs[key].secret
        items.append({"id": row_id, "at_ms": ms(at), "key": key, "by": by, "old": None if hidden else old,
                      "new": None if hidden else new, "secret": hidden})
    return {"items": items, "next": items[-1]["id"] if len(rows) > limit else None}


@router.put("/settings/{key}")
async def put_setting(key: str, body: SettingPut, request: Request,
                      settings: Settings = Depends(get_settings)) -> dict:
    _known(settings, key)
    try:
        await settings.set(key, body.value, by=request.state.admin_user.username)
    except SettingsError:
        raise ApiError("input.invalid", "value") from None
    return _item(settings, key)


@router.delete("/settings/{key}")
async def delete_setting(key: str, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    _known(settings, key)
    user = request.state.admin_user.username
    stored = await settings.delete(key, by=user)
    return {**_item(settings, key), "deleted": stored}
