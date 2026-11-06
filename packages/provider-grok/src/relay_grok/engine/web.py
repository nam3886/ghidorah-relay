"""One run's HTTP and WebSocket to grok.com over curl_cffi (Chrome's fingerprint), bound to one account's cookies and
proxy.

Every call goes through the account's proxy; with no proxy on the account it goes direct, and a failed proxy is a failed
call, never a direct retry. No call follows a redirect. The account's cookies are passed with each call, and only to
`grok.com` and `assets.grok.com` over https on the default port (`cookie_host`); any other host gets none. Reads only
besides the two sends (`send_new`, and the Imagine socket the submit sends one frame on): nothing here calls a
refresh, rotate, logout or settings endpoint, and nothing writes a cookie back. A `Set-Cookie` of `sso` / `sso-rw`
during the run is told once as the event "grok set sso" (names only), so the soak shows whether grok rotates it
(design "Cookie quay vòng").

Nothing here logs or raises with a cookie, an id, an answer's body or the proxy's login: an exception keeps the
failure's type name or a refusal kind only.
"""

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit

from relay_core.providers import Proxy
from relay_grok.constants import (
    ASSETS_HOST,
    COOKIE_HOSTS,
    IMAGINE_WS,
    IMPERSONATE,
    NEW_CONVERSATION_PATH,
    QUOTA_IMAGE_KEY,
    QUOTA_PATH,
    QUOTA_VIDEO_KEY,
    READ_PATHS,
    REST_TIMEOUT_S,
    SESSION_PATH,
    SITE_URL,
    SSO_COOKIE,
    UA,
    UPLOAD_PATH,
    UPLOAD_SOURCE,
    UPLOAD_TIMEOUT_S,
    WS_OPEN_S,
)
from relay_grok.credential import jar_of
from relay_grok.engine.wire import classify_refusal

log = logging.getLogger("relay_grok.web")
ROTATING = (SSO_COOKIE, "sso-rw")         # a Set-Cookie of these is told (names only), never written back
ERROR_BODY_MAX = 65536                    # a refused send's body read for its kind, no more
_WS_STATUS = re.compile(r"WebSocket upgrade: (\d{3})")
_ID = "[A-Za-z0-9-]{1,64}"
_READ = re.compile("|".join(re.escape(p).replace("%s", _ID) for p in READ_PATHS))


class NetError(Exception):
    """A call that got no answer (or broke mid-answer). Its text is the failure's type name only: curl's own text may
    hold the proxy's login."""

    def __init__(self, kind: str, proxy: bool):
        super().__init__(kind)
        self.kind, self.proxy = kind, proxy


class SignedOut(Exception):
    """grok.com answered as signed out: the session read `unauthenticated`, a REST 401, or a WebSocket 401."""

    def __init__(self, where: str):
        super().__init__("grok.com answered the %s signed out" % where)
        self.where = where


class Refused(Exception):
    """grok.com refused a call: `kind` from `wire.classify_refusal` (or `input` for an upload's terminal error, `gone`
    for a media file that is not there), `status` the HTTP status. No body text is kept."""

    def __init__(self, kind: str, status: int):
        super().__init__("grok.com refused (%s, %d)" % (kind, status))
        self.kind, self.status = kind, status


class Quota(NamedTuple):
    image_left: int | None
    image_refill: datetime | None
    video_left: int | None
    video_refill: datetime | None


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port`; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


def real_session(proxies: dict[str, str] | None):
    from curl_cffi import AsyncSession  # imported on first real use: importing the engine stays offline
    return AsyncSession(impersonate=IMPERSONATE, proxies=proxies)


def cookie_host(url: str) -> bool:
    """May the account's cookies go to `url`? Only grok.com and assets.grok.com, https (wss), default port, no login."""
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return False
    return p.scheme in ("https", "wss") and port is None and not p.username and (p.hostname or "") in COOKIE_HOSTS


def _proxy_failure(exc: BaseException) -> bool:
    return any(c.__name__ == "ProxyError" for c in type(exc).__mro__)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _left(body: Any, key: str) -> tuple[int | None, datetime | None]:
    """One `quota_info` pool: the uses left and `nextAvailableAt` (to the second: its sub-second part moves)."""
    pool = body.get(key) if isinstance(body, dict) else None
    if not isinstance(pool, dict):
        return None, None
    left = pool.get("remainingQueries")
    left = left if type(left) is int and left >= 0 else None
    refill = None
    if isinstance(pool.get("nextAvailableAt"), str):
        try:
            refill = datetime.fromisoformat(pool["nextAvailableAt"])
        except ValueError:
            refill = None
    if refill is not None and refill.tzinfo is None:
        refill = None
    return left, refill.replace(microsecond=0) if refill is not None else None


async def _quiet_close(thing, method: str = "close") -> None:
    try:
        await getattr(thing, method)()
    except Exception:      # noqa: BLE001 - a session, an answer or a socket that will not close is dropped
        pass


class Socket:
    """The Imagine WebSocket: `send_json` cut at `WS_OPEN_S`, `recv_text` uncut (the submit cuts its read), `close`.
    A broken socket raises NetError."""

    def __init__(self, ws):
        self.ws = ws

    async def send_json(self, obj: Any) -> None:
        try:
            await asyncio.wait_for(self.ws.send_json(obj), WS_OPEN_S)
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None

    async def recv_text(self) -> str:
        try:
            data, _flags = await self.ws.recv()
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None
        return data.decode("utf-8", errors="replace") if isinstance(data, bytes | bytearray) else str(data)

    async def close(self) -> None:
        try:
            await asyncio.wait_for(self.ws.close(), WS_OPEN_S)
        except Exception:      # noqa: BLE001 - a socket that will not close is dropped
            pass


class Web:
    """One run's calls to grok.com. `statsig(method, path)` (sync or async) gives the `x-statsig-id` value, None for
    none; without it no header is sent. `clock` is the run's monotonic clock (kept for the caller's timings)."""

    def __init__(self, account, settings, ctx=None, *, make_session: Callable[[Any], Any] = real_session,
                 statsig: Callable[[str, str], Any] | None = None, clock: Callable[[], float] = time.monotonic):
        self.account, self.settings, self.ctx = account, settings, ctx
        self.statsig, self.clock = statsig, clock
        self.jar = jar_of(account.credential)
        url = proxy_url(account.proxy)
        self.proxies = {"http": url, "https": url} if url else None
        self.s = make_session(self.proxies)
        self.sso_told = False

    async def __aenter__(self) -> "Web":
        return self

    async def __aexit__(self, *exc) -> None:
        await _quiet_close(self.s)

    # ---- the wire ----------------------------------------------------------------------------------------------
    def _cookies(self, url: str) -> dict[str, str] | None:
        return self.jar if cookie_host(url) else None

    async def headers(self, method: str, path: str, referer: str = "/imagine") -> dict[str, str]:
        """The page's fetch headers for a call to `path`; `x-statsig-id` only when the generator gives a value."""
        h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Origin": SITE_URL,
             "Referer": SITE_URL + referer, "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
             "Sec-Fetch-Site": "same-origin", "x-xai-request-id": str(uuid.uuid4())}
        statsig_value = self.statsig(method, path) if self.statsig is not None else None
        if inspect.isawaitable(statsig_value):
            statsig_value = await statsig_value
        if statsig_value:
            h["x-statsig-id"] = statsig_value
        return h

    async def _request(self, method: str, url: str, headers: dict[str, str], timeout: float | None, *,
                       cookies: bool = True, **kw):
        try:
            resp = await self.s.request(method, url, headers=headers, timeout=timeout, allow_redirects=False,
                                        proxies=self.proxies, cookies=self._cookies(url) if cookies else None, **kw)
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None
        await self._watch_sso()
        return resp

    async def _watch_sso(self) -> None:
        """grok re-set `sso` / `sso-rw` (a value other than the account's): one event per run, names only."""
        if self.sso_told:
            return
        names = sorted({c.name for c in self.s.cookies.jar
                        if c.name in ROTATING and c.value and c.value != self.jar.get(c.name)})
        if not names:
            return
        self.sso_told = True
        log.info("grok account %s: grok set %s", self.account.id, ", ".join(names))
        event = getattr(self.ctx, "event", None)
        if event is not None:
            try:
                await event("grok set sso (%s)" % ", ".join(names), "account", "engine")
            except Exception as e:      # noqa: BLE001 - telling it is never a reason to fail the run
                log.warning("grok account %s: the sso event not written (%s)", self.account.id, type(e).__name__)

    async def _rest(self, method: str, path: str, *, body: Any = None, timeout: float | None = REST_TIMEOUT_S, **kw):
        headers = await self.headers(method, path.split("?")[0])
        if body is not None:
            headers["Content-Type"] = "application/json"
            kw["data"] = json.dumps(body)
        return await self._request(method, SITE_URL + path, headers, timeout, **kw)

    @staticmethod
    def _refused(resp, where: str) -> Exception:
        """What a non-200 read answer raises: a 401 is signed out, anything else a refusal of its kind."""
        if resp.status_code == 401:
            return SignedOut(where)
        return Refused(classify_refusal(resp.status_code, resp.headers.get("content-type", ""), resp.text or ""),
                       resp.status_code)

    # ---- reads ---------------------------------------------------------------------------------------------------
    async def session(self) -> dict:
        """`GET /api/auth/session`: the `session` object. `unauthenticated` or a 401 -> SignedOut."""
        resp = await self._rest("GET", SESSION_PATH)
        if resp.status_code != 200:
            raise self._refused(resp, "session read")
        body = _json(resp.text)
        if isinstance(body, dict) and body.get("status") == "unauthenticated":
            raise SignedOut("session read")
        if not isinstance(body, dict) or body.get("status") != "authenticated" or not isinstance(
                body.get("session"), dict):
            raise Refused("other", resp.status_code)
        return body["session"]

    async def quota(self) -> Quota:
        """`POST /rest/media/imagine/quota_info {}` (spends nothing): both pools' uses left and their refill."""
        resp = await self._rest("POST", QUOTA_PATH, body={})
        if resp.status_code != 200:
            raise self._refused(resp, "quota read")
        body = _json(resp.text)
        if not isinstance(body, dict):
            raise Refused("other", resp.status_code)
        return Quota(*_left(body, QUOTA_IMAGE_KEY), *_left(body, QUOTA_VIDEO_KEY))

    async def read_json(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """A reconcile read: GET of a read-back path (the whole path, one id filled in; a query is allowed), never
        anything else (ValueError before any request)."""
        if method != "GET" or body is not None or not _READ.fullmatch(urlsplit(path).path):
            raise ValueError("not a grok read-back path")
        resp = await self._rest("GET", path)
        return resp.status_code, _json(resp.text)

    async def get_text(self, url: str, *, cookies: bool = True, timeout: float = REST_TIMEOUT_S) -> tuple[int, str]:
        """A page or script GET (the statsig seed, the drift check). The account's cookies only when `cookies` and the
        host is a cookie host."""
        resp = await self._request("GET", url, {"User-Agent": UA, "Accept": "*/*",
                                                "Accept-Language": "en-US,en;q=0.9"}, timeout, cookies=cookies)
        return resp.status_code, resp.text or ""

    # ---- the one-send steps (called by the submit only) -----------------------------------------------------------
    async def upload(self, data: bytes, mime: str, name: str) -> str:
        """One input image (multipart `file` + `file_source`): its `fileMetadataId`. A `terminalError` ->
        Refused("input", 200)."""
        boundary = "----relay" + uuid.uuid4().hex
        safe = re.sub(r'[^A-Za-z0-9._-]', "_", name)[:100] or "image"
        form = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                 % (boundary, safe, mime)).encode() + data
                + ("\r\n--%s\r\nContent-Disposition: form-data; name=\"file_source\"\r\n\r\n%s\r\n--%s--\r\n"
                   % (boundary, UPLOAD_SOURCE, boundary)).encode())
        headers = await self.headers("POST", UPLOAD_PATH)
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        resp = await self._request("POST", SITE_URL + UPLOAD_PATH, headers, UPLOAD_TIMEOUT_S, data=form)
        if resp.status_code != 200:
            raise self._refused(resp, "upload")
        body = _json(resp.text)
        if isinstance(body, dict) and body.get("terminalError"):
            raise Refused("input", resp.status_code)
        meta = body.get("fileMetadata") if isinstance(body, dict) else None
        file_id = meta.get("fileMetadataId") if isinstance(meta, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise Refused("other", resp.status_code)
        return file_id

    async def send_new(self, body: Any, *, timeout_s: float | None = None) -> AsyncIterator[str]:
        """THE send of an edit or a video: one `POST /rest/app-chat/conversations/new`, streamed. A status other than
        200 raises Refused(kind, status) before any line (a 401 too: `signed_out`); then the answer's lines as they
        come. A break mid-answer -> NetError. The caller cuts the read; `timeout_s` is curl's own cut."""
        resp = await self._rest("POST", NEW_CONVERSATION_PATH, body=body, timeout=timeout_s, stream=True)
        try:
            if resp.status_code != 200:
                text = ""
                try:
                    async for line in resp.aiter_lines():
                        text += (line.decode("utf-8", "replace") if isinstance(line, bytes) else line) + "\n"
                        if len(text) > ERROR_BODY_MAX:
                            break
                except Exception:      # noqa: BLE001 - a refusal whose body breaks is still that refusal
                    pass
                raise Refused(classify_refusal(resp.status_code, resp.headers.get("content-type", ""), text),
                              resp.status_code)
            try:
                async for line in resp.aiter_lines():
                    yield line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
            except Exception as e:
                raise NetError(type(e).__name__, _proxy_failure(e)) from None
        finally:
            await _quiet_close(resp, "aclose")

    async def socket(self) -> Socket:
        """The Imagine WebSocket on this run's session, through the same proxy. A 401 handshake -> SignedOut("ws"),
        any other refused handshake -> Refused; no answer -> NetError."""
        try:
            ws = await self.s.ws_connect(IMAGINE_WS, headers={"Origin": SITE_URL, "User-Agent": UA,
                                                              "Accept-Language": "en-US,en;q=0.9"},
                                         cookies=self._cookies(IMAGINE_WS), timeout=WS_OPEN_S, proxies=self.proxies,
                                         allow_redirects=False)
        except Exception as e:
            status = _WS_STATUS.search(str(e))
            if status and status.group(1) == "401":
                raise SignedOut("ws") from None
            if status:
                raise Refused(classify_refusal(int(status.group(1)), "", ""), int(status.group(1))) from None
            raise NetError(type(e).__name__, _proxy_failure(e)) from None
        await self._watch_sso()
        return Socket(ws)

    async def get_media(self, url: str, max_bytes: int, deadline_s: float) -> AsyncIterator[bytes]:
        """A result file from `https://assets.grok.com/...` with the account's cookies. Any other place -> ValueError
        before a request; 403 / 404 -> Refused("gone", status); another status -> Refused("other", status); more than
        `max_bytes` -> ValueError; a break -> NetError. `deadline_s` is curl's cut of the whole download."""
        p = urlsplit(url)
        if not cookie_host(url) or p.scheme != "https" or p.hostname != ASSETS_HOST:
            raise ValueError("not a grok media file")
        resp = await self._request("GET", url, {"User-Agent": UA, "Accept": "*/*", "Referer": SITE_URL + "/"},
                                   deadline_s, stream=True)
        try:
            if resp.status_code in (403, 404):
                raise Refused("gone", resp.status_code)
            if resp.status_code != 200:
                raise Refused("other", resp.status_code)
            got = 0
            try:
                async for chunk in resp.aiter_content():
                    got += len(chunk)
                    if got > max_bytes:
                        raise ValueError("a grok media file larger than %d bytes" % max_bytes)
                    yield chunk
            except ValueError:
                raise
            except Exception as e:
                raise NetError(type(e).__name__, _proxy_failure(e)) from None
        finally:
            await _quiet_close(resp, "aclose")
