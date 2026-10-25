"""HTTP to vibes.ai over httpx, bound to one account's session cookie and proxy.

vibes.ai is a plain JSON API that does not check the client's TLS fingerprint, so the async httpx client that
relay-core already carries is enough (no Chrome impersonation, no thread hop). The transport is a seam: tests pass an
`httpx.MockTransport`, nothing here opens a socket on import. No call follows a redirect: the cookie must not travel to
wherever an answer points, and a 3xx is read as a failed read.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from relay_core.pool import RepeatRest
from relay_core.providers import Proxy
from relay_vibes.constants import (
    ACK_COOKIE,
    API_TIMEOUT_S,
    BASE_URL,
    CHECKPOINT_HEADER,
    CHECKPOINT_VALUE,
    SESSION_COOKIE,
    USER_AGENT,
)
from relay_vibes.credential import session_of


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port` for httpx; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


def headers_for(session: str) -> dict[str, str]:
    """What the web app sends with every API call. The cookie value is never logged."""
    return {"Cookie": "%s; %s=%s" % (ACK_COOKIE, SESSION_COOKIE, session), "User-Agent": USER_AGENT,
            "Origin": BASE_URL, "Referer": BASE_URL + "/", "Accept": "application/json"}


def is_checkpoint(resp: httpx.Response) -> bool:
    """Vercel's security checkpoint (a 403 `X-Vercel-Mitigated: challenge`): the account must pause, nothing failed."""
    return resp.status_code == 403 and resp.headers.get(CHECKPOINT_HEADER, "").lower() == CHECKPOINT_VALUE


def json_of(resp: httpx.Response) -> dict:
    """The answer's JSON object; {} when it is not one."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class Resting(Exception):
    """The account rests after vibes' checkpoint: the call was not made."""

    def __init__(self, left_s: int):
        super().__init__("the account rests %d s more after vibes' security checkpoint" % left_s)
        self.left_s = left_s


class Rests:
    """Every vibes call of one account, in this worker process, passes here (`client_for(..., rests=)`): the calls of
    one account queue on its lock, each starts at least `vibes.account_min_gap_s` after the one before (several jobs
    of one account must not add up to the ~45 calls in 5.5 min that brought the checkpoint), and while the account
    rests after a checkpoint no call is made (`Resting`). A call that meets the checkpoint starts the rest before the
    lock is released, so the calls queued behind it never reach vibes. The submit and the poll that met it also
    report a `Cooldown`, so the core stops claims and checks of the account."""
    # ponytail: per process, one worker process per provider. A second worker process would not share the locks, the
    # spacing or the rests (a shared limiter would live in the database); a restarted worker forgets a rest, which
    # costs one more 403 per account.

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC), *, settings=None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable] = asyncio.sleep):
        self._until: dict[str, datetime] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self.repeats = RepeatRest(clock)
        self.now, self.settings, self.clock, self.sleep = now, settings, clock, sleep

    def left_s(self, account_id: str) -> int:
        """Whole seconds the account still rests (0: it may call). An ended rest is dropped."""
        until = self._until.get(account_id)
        if until is None:
            return 0
        left = math.ceil((until - self.now()).total_seconds())
        if left <= 0:
            del self._until[account_id]
            return 0
        return left

    def start(self, account_id: str, seconds: int) -> datetime:
        """Rest the account `seconds` from now (never shortens a longer rest); returns its end."""
        until = max(self._until.get(account_id) or self.now(), self.now() + timedelta(seconds=seconds))
        self._until[account_id] = until
        return until

    def trip(self, account_id: str, settings) -> datetime:
        """The account met the checkpoint: rest `vibes.checkpoint_rest_s`, doubled by each repeat within a day up to
        `vibes.checkpoint_rest_max_s`; returns the rest's end."""
        return self.start(account_id, self.repeats.next_s(account_id, int(settings.get("vibes.checkpoint_rest_s")),
                                                          int(settings.get("vibes.checkpoint_rest_max_s"))))

    async def call(self, account_id: str, send: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        """One call of the account: in its turn, spaced, never during a rest. The lock is held until the answer's
        headers are in, so a checkpoint starts the rest before the next queued call looks."""
        async with self._locks.setdefault(account_id, asyncio.Lock()):
            left = self.left_s(account_id)
            if left:
                raise Resting(left)
            gap = self.settings.get("vibes.account_min_gap_s") if self.settings is not None else 0
            wait = self._last.get(account_id, -math.inf) + gap - self.clock()
            if wait > 0:
                await self.sleep(wait)
            self._last[account_id] = self.clock()
            resp = await send()
            if is_checkpoint(resp) and self.settings is not None:
                self.trip(account_id, self.settings)
            return resp

    def gated(self, account_id: str, inner: httpx.AsyncBaseTransport) -> httpx.AsyncBaseTransport:
        return _Gated(self, account_id, inner)


class _Gated(httpx.AsyncBaseTransport):
    """A transport whose every request goes through `Rests.call` of one account."""

    def __init__(self, rests: Rests, account_id: str, inner: httpx.AsyncBaseTransport):
        self.rests, self.account_id, self.inner = rests, account_id, inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.rests.call(self.account_id, lambda: self.inner.handle_async_request(request))

    async def aclose(self) -> None:
        await self.inner.aclose()


def client_for(account, transport: httpx.AsyncBaseTransport | None = None,
               rests: Rests | None = None) -> httpx.AsyncClient:
    """A client for one run of one account: its session cookie, its proxy, no redirects, no environment proxies; with
    `rests`, every call is spaced and refused during a checkpoint rest (`Resting`)."""
    inner = transport or httpx.AsyncHTTPTransport(proxy=proxy_url(account.proxy), trust_env=False)
    if rests is not None:
        inner = rests.gated(account.id, inner)
    return httpx.AsyncClient(base_url=BASE_URL, headers=headers_for(session_of(account.credential)),
                             transport=inner, timeout=API_TIMEOUT_S, follow_redirects=False, trust_env=False)
