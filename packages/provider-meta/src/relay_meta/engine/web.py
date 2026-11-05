"""One run's HTTP to meta.ai over curl_cffi (Chrome's fingerprint), bound to one account's cookie jar and proxy, the
per-process token cache and the page gate per proxy IP.

Every call of a run (the www.meta.ai page, the rupload upload, the gateway WebSocket) goes through the account's
proxy; with no proxy on the account it goes direct, and a failed proxy is a failed call, never a direct retry. No
call follows a redirect: the cookies must not travel to wherever an answer points. The only token source is the
signed-in page itself (`\\"accessToken\\":\\"ecto1:…\\"`); nothing here calls a refresh or rotate endpoint.

Page loads are spaced per proxy IP (`meta.page_gap_s`; "direct" is one IP), and a Vercel page rests that IP
(`meta.checkpoint_rest_s`, growing on a repeat). The gate is in-process: one meta worker runs.

Cookies www.meta.ai set during a run are written back to the account (`keep_rotated`) only when every page the run
read was signed in as the account's own viewer (`viewer_hash(viewerId) == provider_state["viewer"]`). Nothing here
logs or raises with the token, the viewerId, a cookie value, the WebSocket URL or the proxy's login: an exception
keeps its type name only.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal, NamedTuple
from urllib.parse import quote, urlencode

from relay_core.pool import RepeatRest
from relay_core.providers import Cooldown, Proxy, Suspect
from relay_meta.constants import (
    COOKIE_DOMAIN,
    GATEWAY_QUERY,
    GATEWAY_URL,
    IMPERSONATE,
    LOCALE,
    LOGGED_IN_MARK,
    PAGE_TIMEOUT_S,
    RD_MARK,
    SIGNED_OUT_GAP_S,
    SIGNED_OUT_REST_S,
    SITE_HOST,
    SITE_URL,
    SOCKET_OPEN_S,
    UA,
    UPLOAD_TIMEOUT_S,
    UPLOAD_URL,
    VERCEL_MARK,
)
from relay_meta.credential import jar_of, on_site, renewable, viewer_hash, with_cookies

log = logging.getLogger("relay_meta.web")
RENEW_TIMEOUT_S = 5.0       # writing rotated cookies back: past it nothing is written

_TOKEN = re.compile(r'\\"accessToken\\":\\"(ecto1:[^"\\]+)\\"')
_VIEWER = re.compile(r'\\"viewerId\\":\\"(\d+)\\"')
_NAME = re.compile(r'\\"socialProfile\\":\{\\"username\\":\\"([^"\\]+)\\"')
_LOGGED_OUT = '\\"isLoggedIn\\":false'
_RD_PATH = re.compile(r"fetch\('(/[^'/][^']*\?challenge=[^']*)'")
_MEDIA_ID = re.compile(r"\d{1,32}")

_CH_UA = '"Google Chrome";v="%s", "Not.A/Brand";v="8", "Chromium";v="%s"' % ((IMPERSONATE[len("chrome"):],) * 2)
_LANG = LOCALE + ",en;q=0.9"
_NAV = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;"
        "q=0.8", "Accept-Language": _LANG, "Sec-Ch-Ua": _CH_UA, "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"', "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1"}


class NetError(Exception):
    """A call that got no answer. Only the error's kind is kept: curl's text may hold the proxy's login."""

    def __init__(self, kind: str, proxy: bool):
        super().__init__("meta.ai could not be reached (%s)" % kind)
        self.kind, self.proxy = kind, proxy


class GateClosed(Exception):
    """A page load the proxy IP's gate refused, before any request. `until` is on the `Sessions` clock; `marked`: the
    IP rests after a Vercel page (else the plain gap between two loads)."""

    def __init__(self, until: float, marked: bool):
        super().__init__("the proxy IP rests after a Vercel page" if marked else "a page was loaded on this IP lately")
        self.until, self.marked = until, marked


class UploadFailed(Exception):
    """rupload answered something else than a media id (the reason names the status, never a value)."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class Page(NamedTuple):
    state: Literal["in", "out", "vercel", "unreadable"]
    token: str | None
    viewer: str | None
    name: str | None
    html: str

    def __repr__(self) -> str:      # the token, the viewer and the page never go into a log or an error
        return "Page(state=%r)" % self.state


def read_page(status: int, html: str) -> Page:
    """What a www.meta.ai page says. `in` needs the signed-in mark, a token and a viewerId; `out` the signed-out mark;
    `vercel` Vercel's checkpoint page; anything else (the `rd` page included) is `unreadable`."""
    html = html or ""
    if VERCEL_MARK in html:
        return Page("vercel", None, None, None, html)
    token, viewer, name = _TOKEN.search(html), _VIEWER.search(html), _NAME.search(html)
    if status == 200 and LOGGED_IN_MARK in html and token and viewer:
        return Page("in", token.group(1), viewer.group(1), name.group(1) if name else None, html)
    if status == 200 and _LOGGED_OUT in html:
        return Page("out", None, None, None, html)
    return Page("unreadable", None, None, None, html)


def ip_key(proxy: Proxy | None) -> str:
    """The gate's key: the proxy's `host:port`, or "direct" for every account with no proxy."""
    return "direct" if proxy is None else "%s:%d" % (proxy.host, proxy.port)


class Gate:
    """Page loads per proxy IP: at most one per `gap_s`, none while the IP rests after a Vercel page."""

    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self._last: dict[str, float] = {}
        self._until: dict[str, float] = {}
        self._rest = RepeatRest(clock)

    def take(self, ip: str, gap_s: int) -> None:
        """Record a page load on `ip`, or raise GateClosed (nothing recorded). No await in between."""
        marked = self.until(ip)
        if marked is not None:
            raise GateClosed(marked, True)
        now, last = self.clock(), self._last.get(ip)
        if last is not None and now - last < gap_s:
            raise GateClosed(last + gap_s, False)
        self._last[ip] = now

    def mark(self, ip: str, base_s: int, max_s: int) -> float:
        """A Vercel page on `ip`: it rests `base_s`, doubled per repeat within a day, at most `max_s`. Its end."""
        end = self.clock() + self._rest.next_s(ip, base_s, max_s)
        self._until[ip] = end
        return end

    def until(self, ip: str) -> float | None:
        end = self._until.get(ip)
        return end if end is not None and end > self.clock() else None


class Held(NamedTuple):
    token: str
    viewer: str
    read_at: float

    def __repr__(self) -> str:
        return "Held(read_at=%r)" % self.read_at


class Sessions:
    """Per process: each account's token and viewer (by credential generation, never persisted), a lock per account,
    and the page gate."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._held: dict[str, tuple[int, Held]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.gate = Gate(clock)

    def token(self, account_id: str, gen: int) -> Held | None:
        """The account's token of any age (`stale` says whether to read the page again); None after a re-import."""
        held = self._held.get(account_id)
        return held[1] if held is not None and held[0] == gen else None

    def keep(self, account_id: str, gen: int, page: Page) -> None:
        if page.state == "in" and page.token and page.viewer:
            self._held[account_id] = (gen, Held(page.token, page.viewer, self.clock()))

    def forget(self, account_id: str) -> None:
        self._held.pop(account_id, None)

    def stale(self, held: Held, reread_s: float) -> bool:
        return self.clock() - held.read_at >= reread_s

    def lock(self, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())


def signed_out_effects(now: datetime) -> tuple[Suspect, Cooldown]:
    """What a signed-out read does to an account: one spaced strike, and a rest after which it is checked again."""
    return (Suspect("not signed in: meta.ai showed a signed-out sign or URL", min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port`; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


def _proxy_failure(exc: BaseException) -> bool:
    return any(c.__name__ == "ProxyError" for c in type(exc).__mro__)


def real_session(proxies: dict[str, str] | None):
    from curl_cffi import AsyncSession  # imported on first real use: importing the engine stays offline
    return AsyncSession(impersonate=IMPERSONATE, proxies=proxies)


async def _close(thing) -> None:
    try:
        await thing.close()
    except Exception:      # noqa: BLE001 - a session that will not close is dropped
        pass


class Socket:
    """curl_cffi's async WebSocket to the gateway: every send and the close are cut at `timeout`."""

    def __init__(self, ws, timeout: float):
        self.ws, self.timeout = ws, timeout

    async def send(self, data: bytes) -> None:
        await asyncio.wait_for(self.ws.send(data), self.timeout)

    async def recv(self) -> bytes:
        data, _flags = await self.ws.recv()
        return data

    async def close(self) -> None:
        await asyncio.wait_for(self.ws.close(), self.timeout)


class Web:
    """One run's HTTP to meta.ai. `async with`: on the way out, the cookies the site set are kept when the run's pages
    proved the account's own viewer (not when the run was cancelled)."""

    def __init__(self, account, settings, sessions: Sessions, ctx=None, *,
                 make_session: Callable[[dict[str, str] | None], Any] = real_session):
        self.account, self.settings, self.sessions, self.ctx = account, settings, sessions, ctx
        self.make_session = make_session
        self.start = jar_of(account.credential)
        url = proxy_url(account.proxy)
        self.proxies = {"http": url, "https": url} if url else None
        self.ip = ip_key(account.proxy)
        self.s = make_session(self.proxies)
        for name, value in self.start.items():
            self.s.cookies.set(name, value, domain=COOKIE_DOMAIN)
        # every page read so far was signed in as the account's own viewer (None: no page read)
        self.trusted: bool | None = None

    async def __aenter__(self) -> "Web":
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.ctx is not None and not isinstance(exc[1], asyncio.CancelledError):
                await keep_rotated(self.ctx, self)
        finally:
            await _close(self.s)

    async def _request(self, session, method: str, url: str, headers: dict[str, str], timeout: float, **kw):
        try:
            return await session.request(method, url, headers=headers, timeout=timeout, allow_redirects=False,
                                         proxies=self.proxies, **kw)
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None

    async def page(self, path: str = "/") -> Page:
        """`GET SITE_URL + path`, after the IP gate (GateClosed before any request). meta's `rd` 403 is passed once
        (POST its challenge, GET again); a second one is `unreadable`. A Vercel page rests the IP."""
        self.sessions.gate.take(self.ip, self.settings.get("meta.page_gap_s"))
        resp = await self._request(self.s, "GET", SITE_URL + path, _NAV, PAGE_TIMEOUT_S)
        if resp.status_code == 403 and RD_MARK in (resp.text or ""):
            rd = _RD_PATH.search(resp.text)
            if rd:
                await self._request(self.s, "POST", SITE_URL + rd.group(1),
                                    {"User-Agent": UA, "Origin": SITE_URL, "Referer": SITE_URL + "/"}, PAGE_TIMEOUT_S)
                resp = await self._request(self.s, "GET", SITE_URL + path, _NAV, PAGE_TIMEOUT_S)
        page = read_page(resp.status_code, resp.text)
        if page.state == "vercel":
            self.sessions.gate.mark(self.ip, self.settings.get("meta.checkpoint_rest_s"),
                                    self.settings.get("meta.checkpoint_rest_max_s"))
            log.warning("meta account %s: a Vercel page; proxy IP rests", self.account.id)
        own = page.state == "in" and viewer_hash(page.viewer) == self.account.provider_state.get("viewer")
        self.trusted = own if self.trusted is None else self.trusted and own
        return page

    async def upload(self, token: str, data: bytes, mime: str, name: str) -> str:
        """POST one input image to rupload on a cookie-less session through the same proxy: its `media_id`."""
        bare = self.make_session(self.proxies)
        try:
            resp = await self._request(bare, "POST", UPLOAD_URL + str(uuid.uuid4()), {
                "User-Agent": UA, "Origin": SITE_URL, "Referer": SITE_URL + "/", "Accept": "*/*",
                "Content-Type": mime, "x-entity-length": str(len(data)), "x-entity-name": name, "x-entity-type": mime,
                "Offset": "0", "Authorization": "OAuth " + token, "desired_upload_handler": "genai_document",
                "is_abra_user": "true", "ecto_auth_token": "true", "Sec-Fetch-Site": "same-site",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}, UPLOAD_TIMEOUT_S, data=data)
        finally:
            await _close(bare)
        try:
            media_id = json.loads(resp.text).get("media_id") if resp.status_code == 200 else None
        except (ValueError, TypeError, AttributeError):
            media_id = None
        if isinstance(media_id, int) and not isinstance(media_id, bool):
            media_id = str(media_id)
        if not isinstance(media_id, str) or not _MEDIA_ID.fullmatch(media_id):
            raise UploadFailed("rupload answered %d with no media id" % resp.status_code)
        return media_id

    async def socket(self, token: str) -> Socket:
        """The WebSocket to the gateway on the jar session, through the same proxy. Its URL carries the token."""
        url = GATEWAY_URL + "?" + urlencode({**GATEWAY_QUERY, "Authorization": token,
                                             "x-dgw-app-clippy-request-id": str(uuid.uuid4())})
        try:
            ws = await self.s.ws_connect(url, headers={"Origin": SITE_URL, "User-Agent": UA, "Accept-Language": _LANG},
                                         timeout=SOCKET_OPEN_S, allow_redirects=False, proxies=self.proxies,
                                         impersonate=IMPERSONATE)
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None
        return Socket(ws, SOCKET_OPEN_S)

    def cookies(self) -> dict[str, str]:
        """The meta.ai cookies the session holds now (what the site set included)."""
        return {c.name: c.value for c in self.s.cookies.jar if on_site(c.domain or SITE_HOST)}


async def keep_rotated(ctx, web: Web) -> bool:
    """Write the cookies meta.ai set during the run back into the account's sealed jar (`ctx.renew_credential`), each
    only while the stored value is still the one this run started from (another run's newer rotation is never
    overwritten). Only when the run read a page and every page was signed in as the account's own viewer. Cut at
    `RENEW_TIMEOUT_S`. True when written. Never raises; logs names only."""
    async def renew() -> bool:
        if not web.trusted:
            return False
        changes = {n: v for n, v in web.cookies().items() if web.start.get(n) != v and renewable(n, v)}
        if not changes:
            return False
        started = dict(web.start)

        def merge(current: bytes, _mine: bytes) -> bytes:
            stored = jar_of(current)
            mine = {n: v for n, v in changes.items() if stored.get(n) == started.get(n)}
            return with_cookies(current, mine) if mine else current
        written = await ctx.renew_credential(with_cookies(ctx.account.credential, changes), merge)
        if written is not None:
            log.info("meta account %s: rotated cookies written back (%s)", ctx.account.id, ", ".join(sorted(changes)))
        return written is not None

    try:
        return await asyncio.wait_for(renew(), RENEW_TIMEOUT_S)
    except Exception as e:      # noqa: BLE001 - the credential stays as it was; never a reason to fail the run
        log.warning("meta account %s: rotated cookies not written back (%s)",
                    getattr(getattr(ctx, "account", None), "id", "?"), type(e).__name__)
        return False
