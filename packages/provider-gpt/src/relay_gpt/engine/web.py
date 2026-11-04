"""HTTP to chatgpt.com over curl_cffi (Chrome's fingerprint), bound to one account's cookie jar and proxy.

Every call of a run goes through the account's proxy; with no proxy on the account it goes direct, and a failed proxy
is a failed call, never a direct retry. No call follows a redirect (the cookies and the bearer must not travel to
wherever an answer points). The only token source is `GET /api/auth/session` with the session cookie, the call the
page itself makes; nothing here calls an oauth, refresh or rotate endpoint. The access token (a 10-day JWT) is cached
per process until `exp - TOKEN_MARGIN_S`, and so is the home page's build id and anti-bot script address
(`gpt.page_max_age_s`).

chatgpt.com re-sets the session cookie on use (and may change how it is split): the run writes the new set back to the
account, whole (`keep_rotated`), unless a call of the run was answered as signed out. The session seam is curl_cffi's
`AsyncSession` (or a fake with the same `request`, `cookies` and `close`). Nothing here logs or raises with a cookie,
a token or the proxy's login: an exception keeps its type name only.
"""

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit

from relay_core.providers import Proxy
from relay_gpt.constants import (
    API_TIMEOUT_S,
    BASE_URL,
    ESTUARY_PATH,
    IMPERSONATE,
    LANGUAGE,
    MEDIA_DEADLINE_S,
    SEC_CH_UA,
    SEC_CH_UA_PLATFORM,
    SITE_HOST,
    TOKEN_MARGIN_S,
    UPLOAD_HOST_SUFFIX,
    UPLOAD_TIMEOUT_S,
    USER_AGENT,
)
from relay_gpt.credential import jar_of, merge_blobs, on_site, session_parts, with_session

log = logging.getLogger("relay_gpt.web")
AUTH_SESSION_PATH = "/api/auth/session"
AUTH_NAMESPACE = "https://api.openai.com/auth"
RENEW_TIMEOUT_S = 5.0       # writing the re-set session back: past it nothing is written
_BUILD = re.compile(r'data-build="([^"]+)"')
CF_CHALLENGE = "/cdn-cgi/challenge-platform/"
_SDK = re.compile(r'(?:https://chatgpt\.com)?(/sentinel/[A-Za-z0-9._-]+/sdk\.js)')


class NetError(Exception):
    """A call that got no answer. Only the error's kind is kept: curl's text may hold the proxy's login."""

    def __init__(self, kind: str, proxy: bool):
        super().__init__("chatgpt.com could not be reached (%s)" % kind)
        self.kind, self.proxy = kind, proxy


class SignedOut(Exception):
    """The session read gave no access token, or a call was answered 401: the account's session is gone."""


class BadAnswer(Exception):
    """chatgpt.com (or the upload host) answered a step with something else than its answer, or a URL was refused
    before any request (`status` 0)."""

    def __init__(self, status: int, where: str):
        super().__init__("chatgpt.com answered %d to %s" % (status, where) if status else "refused: %s" % where)
        self.status, self.where = status, where


class Challenge(Exception):
    """chatgpt.com asked for something the relay never does over HTTP: `arkose` or `force_login`."""

    def __init__(self, what: str):
        super().__init__("chatgpt.com asked for %s" % what)
        self.what = what


class Page(NamedTuple):
    build: str           # the home page's `data-build`
    sdk_url: str         # the sentinel loader's https://chatgpt.com/sentinel/<id>/sdk.js


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port`; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


def _cloudflare(resp, streamed: bool) -> bool:
    """Cloudflare answered instead of chatgpt.com: its challenge header, or its challenge page (not read when the
    answer is a stream)."""
    if (resp.headers or {}).get("cf-mitigated"):
        return True
    return not streamed and CF_CHALLENGE in (getattr(resp, "text", "") or "")


def _proxy_failure(exc: BaseException) -> bool:
    return any(c.__name__ == "ProxyError" for c in type(exc).__mro__)


def real_session(proxies: dict[str, str] | None):
    from curl_cffi import AsyncSession  # imported on first real use: importing the engine stays offline
    return AsyncSession(impersonate=IMPERSONATE, proxies=proxies)


def claims(token: str) -> dict:
    """The JWT's payload with the `https://api.openai.com/auth` namespace flattened in. Never verified, never logged;
    {} for anything that is not a JWT."""
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (AttributeError, IndexError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    auth = payload.get(AUTH_NAMESPACE)
    return {**payload, **auth} if isinstance(auth, dict) else payload


class Sessions:
    """Per process: each account's access token (by credential generation), a lock per account, and the home page."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._tokens: dict[str, tuple[int, str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._page: tuple[Page, float] | None = None

    def token(self, account_id: str, gen: int) -> str | None:
        held = self._tokens.get(account_id)
        if held is None or held[0] != gen or self.clock() >= held[2] - TOKEN_MARGIN_S:
            return None
        return held[1]

    def keep_token(self, account_id: str, gen: int, token: str) -> None:
        exp = claims(token).get("exp")
        if type(exp) is int:
            self._tokens[account_id] = (gen, token, float(exp))

    def forget(self, account_id: str) -> None:
        self._tokens.pop(account_id, None)

    def lock(self, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    def page(self, now: float, max_age_s: float) -> Page | None:
        if self._page is None or now - self._page[1] >= max_age_s:
            return None
        return self._page[0]

    def keep_page(self, page: Page, now: float) -> None:
        self._page = (page, now)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


class Web:
    """One run's HTTP to chatgpt.com. `async with`: on the way out the re-set session cookie is kept (not after a
    signed-out answer, nor when the run was cancelled)."""

    def __init__(self, account, settings, sessions: Sessions, ctx=None, session=None,
                 make_session: Callable[[dict[str, str] | None], Any] = real_session):
        self.account, self.settings, self.sessions, self.ctx = account, settings, sessions, ctx
        self.make_session = make_session
        self.start = jar_of(account.credential)
        self.start_parts = session_parts(self.start)
        url = proxy_url(account.proxy)
        self.proxies = {"http": url, "https": url} if url else None
        self.s = session if session is not None else make_session(self.proxies)
        for name, value in self.start.items():
            self.s.cookies.set(name, value, domain=SITE_HOST)
        self.device_id = self.start.get("oai-did") or str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())
        self.token: str | None = None
        self.signed_out = False
        self.blocked = False     # the last answer was a 403, a 429 or Cloudflare's: the account rests (submit)
        # the session parts the jar held after the last answer, and when an answer last changed them (the stamp of
        # the write-back: two overlapping runs keep the set chatgpt.com gave LAST, not the one whose run ended last)
        self.seen_parts = self.start_parts
        self.parts_at: float | None = None

    async def __aenter__(self) -> "Web":
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.ctx is not None and not isinstance(exc[1], asyncio.CancelledError):
                await keep_rotated(self.ctx, self)
        finally:
            await _close(self.s)

    # ---- the wire ----------------------------------------------------------------------------------------------
    async def _request(self, session, method: str, url: str, headers: dict[str, str], timeout: float, *,
                       signed_out_on_401: bool = True, **kw):
        try:
            resp = await session.request(method, url, headers=headers, timeout=timeout, allow_redirects=False, **kw)
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None
        if session is self.s:
            parts = self.session_now()
            if parts != self.seen_parts:
                self.seen_parts, self.parts_at = parts, self.sessions.clock()
        self.blocked = resp.status_code in (403, 429) or _cloudflare(resp, bool(kw.get("stream")))
        if resp.status_code == 401 and signed_out_on_401:
            self._signed_out()
        return resp

    def _signed_out(self):
        self.signed_out = True
        self.sessions.forget(self.account.id)
        raise SignedOut("chatgpt.com answered the session as signed out")

    def headers(self, path: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The page's fetch headers for a call to `path` (the spike's set; no client version or build number)."""
        h = {
            "User-Agent": USER_AGENT, "Origin": BASE_URL, "Referer": BASE_URL + "/",
            "Accept": "*/*", "Accept-Language": LANGUAGE + ",en;q=0.9",
            "Sec-Ch-Ua": SEC_CH_UA, "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": SEC_CH_UA_PLATFORM,
            "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
            "Priority": "u=1, i",
            "OAI-Device-Id": self.device_id, "OAI-Session-Id": self.session_id, "OAI-Language": LANGUAGE,
            "X-OpenAI-Target-Path": path, "X-OpenAI-Target-Route": path,
        }
        if self.token:
            h["Authorization"] = "Bearer " + self.token
            workspace = claims(self.token).get("chatgpt_account_id")
            if isinstance(workspace, str) and workspace:
                h["ChatGPT-Account-Id"] = workspace
        h.update(extra or {})
        return h

    async def _call(self, method: str, path: str, extra: dict[str, str] | None = None, *, reread: bool = True,
                    **kw):
        """One call to chatgpt.com. A 401 first forgets the bearer and reads the session again, then makes the call
        once more; a second 401 is signed out. `reread` False (the paid send: never made twice): a 401 is signed out
        at once."""
        timeout = kw.pop("timeout", API_TIMEOUT_S)

        async def once():
            return await self._request(self.s, method, BASE_URL + path, self.headers(path.split("?")[0], extra),
                                       timeout, signed_out_on_401=False, **kw)
        resp = await once()
        if resp.status_code == 401 and reread and path != AUTH_SESSION_PATH:
            self.token = None
            self.sessions.forget(self.account.id)
            await self.session()
            resp = await once()
        if resp.status_code == 401:
            self._signed_out()
        return resp

    # ---- the page and the token ---------------------------------------------------------------------------------
    async def home(self) -> Page:
        """`GET /` with navigate headers: the build id and the sentinel script address, cached for
        `gpt.page_max_age_s`."""
        now = self.sessions.clock()
        page = self.sessions.page(now, self.settings.get("gpt.page_max_age_s"))
        if page is not None:
            return page
        resp = await self._request(self.s, "GET", BASE_URL + "/", {
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": LANGUAGE + ",en;q=0.9", "Sec-Ch-Ua": SEC_CH_UA, "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": SEC_CH_UA_PLATFORM, "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1"}, API_TIMEOUT_S)
        html = resp.text if resp.status_code == 200 else ""
        build, sdk = _BUILD.search(html), _SDK.search(html)
        if not build or not sdk:
            raise BadAnswer(resp.status_code, "the home page (no build id or sentinel script)")
        page = Page(build.group(1), BASE_URL + sdk.group(1))
        self.sessions.keep_page(page, now)
        return page

    async def session(self) -> dict:
        """`GET /api/auth/session` (always read): the session body, with its `accessToken` kept for the run and the
        process. No `accessToken` (signed out answers 200 `{}`) or a 401 -> SignedOut."""
        resp = await self._call("GET", AUTH_SESSION_PATH)
        body = _json(resp.text) if resp.status_code == 200 else None
        if not isinstance(body, dict):
            raise BadAnswer(resp.status_code, "the session read")
        token = body.get("accessToken")
        if not isinstance(token, str) or not token:
            self._signed_out()
        self.token = token
        self.sessions.keep_token(self.account.id, self.account.credential_gen, token)
        return body

    async def access_token(self) -> str:
        """The account's access token: the process's cached one, else read from the session."""
        if self.token:
            return self.token
        async with self.sessions.lock(self.account.id):
            cached = self.sessions.token(self.account.id, self.account.credential_gen)
            if cached is None:
                await self.session()
            else:
                self.token = cached
        return self.token

    # ---- calls ----------------------------------------------------------------------------------------------
    async def get_json(self, path: str) -> tuple[int, Any]:
        resp = await self._call("GET", path)
        return resp.status_code, _json(resp.text)

    async def post_json(self, path: str, body: Any, extra_headers: dict[str, str] | None = None) -> tuple[int, Any]:
        resp = await self._call("POST", path, {"Content-Type": "application/json", **(extra_headers or {})},
                                data=json.dumps(body))
        return resp.status_code, _json(resp.text)

    async def stream(self, path: str, body: Any, extra_headers: dict[str, str] | None,
                     hold_s: float) -> AsyncIterator[str]:
        """POST and yield the answer's lines (an SSE stream). Not 200 -> BadAnswer, a 401 SignedOut (never made
        twice); a break in the middle -> NetError."""
        resp = await self._call("POST", path, {"Content-Type": "application/json", "Accept": "text/event-stream",
                                               **(extra_headers or {})}, data=json.dumps(body), timeout=hold_s,
                                stream=True, reread=False)
        try:
            if resp.status_code != 200:
                raise BadAnswer(resp.status_code, path)
            try:
                async for line in resp.aiter_lines():
                    yield line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
            except Exception as e:
                raise NetError(type(e).__name__, _proxy_failure(e)) from None
        finally:
            await _close(resp, "aclose")

    async def put_blob(self, url: str, data: bytes, mime: str) -> int:
        """PUT an input image to the upload host chatgpt.com named (https, `*.oaiusercontent.com` only)."""
        parts = urlsplit(url)
        if parts.scheme != "https" or not (parts.hostname or "").endswith(UPLOAD_HOST_SUFFIX):
            raise BadAnswer(0, "an upload host that is not chatgpt.com's")
        resp = await self._request(self.s, "PUT", url, {
            "User-Agent": USER_AGENT, "Origin": BASE_URL, "Referer": BASE_URL + "/", "Content-Type": mime,
            "x-ms-blob-type": "BlockBlob", "x-ms-version": "2020-04-08"}, UPLOAD_TIMEOUT_S, data=data)
        return resp.status_code

    async def get_media(self, url: str, max_bytes: int) -> tuple[int, str, bytes]:
        """A result's bytes from `https://chatgpt.com` + ESTUARY_PATH (relative or absolute), with the bearer and no
        cookie (a session of its own, through the same proxy). Any other place is refused before a request; more
        than `max_bytes` -> BadAnswer. A status other than 200 returns no bytes."""
        if url.startswith("/"):
            url = BASE_URL + url
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != SITE_HOST or parts.path != ESTUARY_PATH:
            raise BadAnswer(0, "a download that is not chatgpt.com's")
        token = await self.access_token()
        bare = self.make_session(self.proxies)
        try:
            resp = await self._request(bare, "GET", url, {
                "User-Agent": USER_AGENT, "Accept": "*/*", "Referer": BASE_URL + "/",
                "Authorization": "Bearer " + token}, MEDIA_DEADLINE_S, stream=True)
            try:
                if resp.status_code != 200:
                    return resp.status_code, "", b""
                data = bytearray()
                try:
                    async for chunk in resp.aiter_content():
                        data += chunk
                        if len(data) > max_bytes:
                            raise BadAnswer(resp.status_code, "a download larger than %d bytes" % max_bytes)
                except BadAnswer:
                    raise
                except Exception as e:
                    raise NetError(type(e).__name__, _proxy_failure(e)) from None
                return resp.status_code, resp.headers.get("content-type", ""), bytes(data)
            finally:
                await _close(resp, "aclose")
        finally:
            await _close(bare)

    def session_now(self) -> dict[str, str]:
        """The session parts the jar holds now (a part set empty counts as deleted)."""
        return {n: v for n, v in session_parts(self.cookies()).items() if v}

    def cookies(self) -> dict[str, str]:
        """The chatgpt.com cookies the session holds now (what chatgpt.com set included)."""
        return {c.name: c.value for c in self.s.cookies.jar if on_site(c.domain or SITE_HOST)}


async def _close(thing, method: str = "close") -> None:
    try:
        await getattr(thing, method)()
    except Exception:      # noqa: BLE001 - a session or an answer that will not close is dropped
        pass


async def keep_rotated(ctx, web: Web) -> bool:
    """Write the session cookie chatgpt.com re-set during the run back into the account's sealed jar, the whole set
    of its parts (`with_session`) stamped with when an answer re-set them (`Web.parts_at`, never the exit time),
    merged with what is stored now (`merge_blobs`: the set chatgpt.com gave later wins). Nothing
    after a signed-out answer, and nothing when the set did not change (a deleted part counts as gone). Cut at
    `RENEW_TIMEOUT_S`. True when written. Never raises; logs names only."""
    async def renew() -> bool:
        if web.signed_out:
            return False
        parts = web.session_now()
        if not parts or parts == web.start_parts or web.parts_at is None:
            return False
        written = await ctx.renew_credential(with_session(ctx.account.credential, parts, web.parts_at), merge_blobs)
        if written is not None:
            log.info("gpt account %s: the re-set session cookie written back (%s)", ctx.account.id,
                     ", ".join(sorted(parts)))
        return written is not None

    try:
        return await asyncio.wait_for(renew(), RENEW_TIMEOUT_S)
    except Exception as e:      # noqa: BLE001 - the credential stays as it was; never a reason to fail the run
        log.warning("gpt account %s: the re-set session cookie not written back (%s)",
                    getattr(getattr(ctx, "account", None), "id", "?"), type(e).__name__)
        return False
