"""Pieces the admin route modules share: the strict body base, epoch-millisecond times, the command insert and the
proxy parser. Not a lane module: it has no `router`, so `create_app` never lists it.

`enqueue` is the only place the admin writes a `commands` row, and it refuses a kind outside `ADMIN_COMMANDS`
(: the database cannot tell which kinds the api may insert, the admin code does).
"""

import uuid
from datetime import datetime
from urllib.parse import quote, unquote, urlsplit

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from relay_core.api.errors_http import ApiError
from relay_core.pool import PoolConfig, PoolState
from relay_core.providers import ProviderRegistry, ProviderSpec

ADMIN_COMMANDS = ("delete_account", "wake", "check", "check_all", "proxy_recheck", "recover", "refresh_catalog",
                  "titles")
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
MAX_PAGE = 200


class Strict(BaseModel):
    """Admin bodies refuse a field they do not know."""

    model_config = ConfigDict(extra="forbid")


def ms(moment: datetime | None) -> int | None:
    """Admin times are epoch milliseconds for the page; None stays None."""
    return None if moment is None else int(moment.timestamp() * 1000)


def pool_budget(state: PoolState) -> int | None:
    """The units the account may spend in the pool's windows, for its tier (`pool.Standing.budget`: the windows that
    bind it added up, or under `all` the smallest): None when no window binds the tier: the figure is unknown."""
    return state.standing.budget


def pool_view(cfg: PoolConfig, state: PoolState) -> dict:
    """One pool of one account as the admin lists it, what the next claim will see: `left_today` is its effective
    balance (None = unknown), `source` says where it comes from (`reported`, `computed` or `unknown`), `left_n` is
    the balance the upstream last reported, `budget` the tier's units in the windows and `windows` each window's
    budget, spend and refill. `drift` is gone (spend is computed from the charges, so it cannot disagree with them)."""
    st = state.standing
    return {
        "pool": cfg.name, "unit": cfg.unit, "left_n": None if state.reported is None else state.reported.left,
        "left_today": st.left, "budget": pool_budget(state), "drift": None, "source": st.source, "day_anchor": None,
        "rest_until_ms": ms(state.rest_until), "rest_reason": state.rest_reason,
        "windows": [{"name": w.name, "kind": w.kind, "budget": w.budget, "spent": w.spent, "refill_ms": ms(w.refill_at)}
                    for w in st.windows]}


def spec_of(registry: ProviderRegistry, provider: str) -> ProviderSpec:
    """The registered provider, or 404: the admin never queues work for a provider nobody runs."""
    if provider not in registry.names():
        raise ApiError("resource.not_found", "provider")
    return registry.spec(provider)


async def enqueue(conn: AsyncConnection, provider: str, kind: str, *, account_id=None, job_id=None,
                  args: dict | None = None) -> uuid.UUID:
    """Insert one command row for the provider's worker and return its id. The api never runs a command."""
    if kind not in ADMIN_COMMANDS:
        raise ValueError("the admin does not queue %r" % kind)
    row = await (await conn.execute(
        "INSERT INTO commands (provider, kind, account_id, job_id, args) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (provider, kind, account_id, job_id, Jsonb(args or {})))).fetchone()
    return row[0]


def parse_proxy(text: str) -> tuple[str, int, str | None]:
    """`host:port`, `host:port:user:pass`, `user:pass@host:port` or a URL of those: (host, port, "user:pass" or None).
    ValueError for anything else; the message never repeats the text (it may hold a password)."""
    raw = text.strip()
    if "://" not in raw:
        parts = raw.split(":")
        if "@" in raw:
            raw = "http://" + raw
        elif len(parts) == 2:
            raw = "http://" + raw
        elif len(parts) == 4:
            host, port, user, secret = parts
            raw = "http://%s:%s@%s:%s" % (quote(user, safe=""), quote(secret, safe=""), host, port)
        else:
            raise ValueError("the proxy is not host:port[:user:pass]")
    url = urlsplit(raw)
    if url.scheme not in PROXY_SCHEMES:
        raise ValueError("the proxy scheme is not supported")
    host, port = url.hostname, url.port          # `.port` raises ValueError for a non-number or > 65535
    if not host or not port:
        raise ValueError("the proxy needs a host and a port")
    auth = None if url.username is None else "%s:%s" % (unquote(url.username), unquote(url.password or ""))
    return host, port, auth
