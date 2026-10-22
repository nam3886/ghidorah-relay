"""Pieces the admin route modules share: the strict body base, epoch-millisecond times, the command insert and the
proxy parser. Not a lane module: it has no `router`, so `create_app` never lists it.

`enqueue` is the only place the admin writes a `commands` row, and it refuses a kind outside `ADMIN_COMMANDS`
(: the database cannot tell which kinds the api may insert, the admin code does).
"""

import uuid
from datetime import date, datetime
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from relay_core.api.errors_http import ApiError
from relay_core.pool import PoolConfig
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


def pool_point(cfg: PoolConfig | None, left_n: int | None, source: str, anchor: date | None, now: datetime,
               spent: int) -> tuple[bool, int | None, bool | None]:
    """One capacity row as the next claim sees it: `(stale, left_today, drift)`. `now` is the database clock
    (`clock_timestamp()`, the one `pool.lock_capacity` rolls the day with), `spent` the unrefunded charges of the
    row's own day. A row whose pool day is over goes back to the pool's budget (unknown without one); drift is a
    counted value of today that disagrees with the budget less `spent` (the pool's 5-minute pass corrects it)."""
    stale = cfg is not None and anchor != now.astimezone(ZoneInfo(cfg.tz)).date()
    counted = cfg is not None and cfg.budget is not None and not stale and source in ("counted", "reset") \
        and left_n is not None
    drift = left_n != cfg.budget - spent if counted else None
    return stale, cfg.budget if stale and cfg else left_n, drift


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
