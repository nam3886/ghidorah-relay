"""What every route gets from the app, and the API-key check (contracts §8, §13, D38).

The app holds its pool, settings, object store and provider registry in `app.state` (set by `create_app`; the
process that builds the app owns their lifecycle). Routes take them through the `get_*` dependencies.
"""

import ipaddress
import logging
from collections.abc import Iterable

from fastapi import Depends, Request
from psycopg_pool import AsyncConnectionPool

from relay_core import keys
from relay_core.api.errors_http import ApiError
from relay_core.billing import KeyDisabled
from relay_core.keys import ApiKey, KeyRefused
from relay_core.providers import ProviderRegistry
from relay_core.settings import Settings
from relay_core.storage import ObjectStore

log = logging.getLogger("relay.api")


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> ObjectStore:
    return request.app.state.store


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def credential(request: Request) -> str:
    """The raw key the caller sent: `Authorization: Bearer`, `X-API-Key` or `x-goog-api-key`, in that order. A
    `?key=` query value is never read (it would end up in proxy and access logs)."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (request.headers.get("x-api-key") or request.headers.get("x-goog-api-key") or "").strip()


async def require_key(request: Request, pool: AsyncConnectionPool = Depends(get_pool)) -> ApiKey:
    """The enabled key behind the request. Missing, unknown or malformed -> 401 `auth.invalid_key`; disabled ->
    403 `auth.key_disabled`, and only after the whole hash matched (D38). A database error propagates (500): no
    answer, no access."""
    try:
        async with pool.connection() as conn:
            key = await keys.lookup(conn, credential(request))
    except KeyRefused:
        raise ApiError("auth.invalid_key") from None
    except KeyDisabled as e:
        request.state.key_id = e.key_id      # the hash matched: the request log keeps the 403 under that key
        raise ApiError("auth.key_disabled") from None
    request.state.key_id = key.id
    return key


async def resolve_key_quietly(request: Request) -> ApiKey | None:
    """The key of a request that failed before `require_key` ran (a body that is not JSON). Never raises: the
    caller only wants to know whom to log the failure against."""
    if getattr(request.state, "key_id", None) is not None:
        return None
    try:
        return await require_key(request, request.app.state.pool)
    except Exception:
        return None


def skip_body_log(request: Request) -> None:
    """Dependency for a route whose body holds a credential: the request log keeps its line but not its body."""
    request.state.log_body = False


def trusted_networks(spec: str | Iterable[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """`TRUSTED_PROXIES` (comma separated addresses or CIDRs) as networks."""
    items = spec.split(",") if isinstance(spec, str) else spec
    return tuple(ipaddress.ip_network(i.strip(), strict=False) for i in items if i.strip())


def client_ip(request: Request) -> str:
    """The caller's address. `X-Forwarded-For` counts only when the direct peer is a trusted proxy, and then the
    rightmost address that is not itself a trusted proxy is the client; anything unparseable falls back to the peer."""
    peer = request.client.host if request.client else "0.0.0.0"
    trusted = request.app.state.trusted_proxies

    def ip_of(text: str):
        try:
            return ipaddress.ip_address(text.strip())
        except ValueError:
            return None

    def is_trusted(addr) -> bool:
        return any(addr in net for net in trusted)

    peer_ip = ip_of(peer)
    if peer_ip is None or not is_trusted(peer_ip):
        return str(peer_ip or "0.0.0.0")
    for hop in reversed(request.headers.get("x-forwarded-for", "").split(",")):
        addr = ip_of(hop)
        if addr is None:
            return str(peer_ip)
        if not is_trusted(addr):
            return str(addr)
    return str(peer_ip)
