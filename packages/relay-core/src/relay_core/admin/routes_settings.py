"""Admin: live settings (contracts 10).

The list shows each registered key with its value, where the value comes from (`db`, `env` or `default`), its default
and its description. A put validates before it writes (a refused value leaves the stored one as it was); a delete
restores the env value or the default. The boot-only values (an upstream URL, the billing timezone, the proxy list...)
are configuration of the deployment: they are listed read-only under `env_only` and a put or delete of one is refused.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool

from relay_core.admin.routes_common import Strict
from relay_core.api.deps import get_pool, get_settings
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.settings import Settings, SettingsError

router = APIRouter()


class SettingPut(Strict):
    value: Any


def _item(settings: Settings, key: str) -> dict:
    spec = settings.specs()[key]
    return {"key": key, "type": spec.type.__name__, "value": settings.get(key), "source": settings.source(key),
            "default": spec.default, "description": spec.description, "env_name": spec.env_name, "read_only": False}


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
    state = request.app.state
    env_only = [
        {"name": "BILLING_TZ", "value": pin[0] if pin else None, "read_only": True},
        {"name": "PUBLIC_BASE_URL", "value": state.public_base_url, "read_only": True},
        {"name": "TRUSTED_PROXIES", "value": ",".join(str(n) for n in state.trusted_proxies), "read_only": True},
    ]
    return {"items": [_item(settings, k) for k in sorted(settings.specs())], "env_only": env_only}


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
async def delete_setting(key: str, settings: Settings = Depends(get_settings)) -> dict:
    _known(settings, key)
    stored = await settings.delete(key)
    return {**_item(settings, key), "deleted": stored}
