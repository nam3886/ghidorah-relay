"""HTTP to dola over curl_cffi (Chrome fingerprint), bound to one account's cookies and proxy.

dola checks the TLS fingerprint, so the transport is curl_cffi `impersonate="chrome"`; it is blocking, so every call
here runs through `asyncio.to_thread`. The transport is a seam: tests pass an in-process fake, nothing here opens a
socket on import (curl_cffi is imported on first real use).

Every poll, reconcile read, title lookup and master lookup goes through `DolaHttp`; it never spends credits. The one
call that does is the plain-HTTP send (`send_http.py`), through `post_data` with a body it signed itself.
The media bytes do not: `relay_core.net.stream_download` fetches those (plain httpx, no cookie, through the same
account proxy when there is one, never direct then).
"""

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from relay_dola import credential

BASE = "https://www.dola.com"
API_TIMEOUT_S = 30


class Response(Protocol):
    status_code: int
    content: bytes

    def json(self) -> Any: ...


class Transport(Protocol):
    """The two calls the engine makes. `proxies` is curl_cffi's `{"http": url, "https": url}` or None. Both carry
    the account's cookie, so NEITHER follows a redirect: a 3xx comes back as the answer (callers treat any non-2xx
    as a failed read)."""

    def post(self, url: str, *, params: Mapping[str, str], headers: Mapping[str, str], json: Any, timeout: float,
             proxies: Mapping[str, str] | None) -> Response: ...

    def get(self, url: str, *, headers: Mapping[str, str] | None, timeout: float,
            proxies: Mapping[str, str] | None) -> Response:
        ...

    def post_data(self, url: str, *, data: bytes, headers: Mapping[str, str], timeout: float,
                  proxies: Mapping[str, str] | None, lines: list[str] | None = None) -> Response:
        """POST these exact bytes to this exact URL (query included: a signed query must not be re-encoded). With
        `lines` the answer is streamed: each line is appended AS IT ARRIVES, so a caller still holds what came before
        an exception (an `SSE_ACK` read before a drop is not lost)."""
        ...


class CurlTransport:
    """The real transport. curl_cffi is imported on first call so importing the engine stays cheap and offline."""

    def post(self, url, *, params, headers, json, timeout, proxies):
        from curl_cffi import requests as cffi
        return cffi.post(url, params=params, headers=headers, json=json, impersonate="chrome", timeout=timeout,
                         proxies=proxies, allow_redirects=False)

    def get(self, url, *, headers, timeout, proxies):
        from curl_cffi import requests as cffi
        return cffi.get(url, headers=headers, impersonate="chrome", timeout=timeout, proxies=proxies,
                        allow_redirects=False)

    def post_data(self, url, *, data, headers, timeout, proxies, lines=None):
        from curl_cffi import requests as cffi
        resp = cffi.post(url, data=data, headers=headers, impersonate="chrome", timeout=timeout, proxies=proxies,
                         allow_redirects=False, stream=lines is not None)
        if lines is None:
            return resp
        try:
            for raw in resp.iter_lines():
                lines.append(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))
        finally:
            resp.close()
        return resp


def query_params() -> dict[str, str]:
    """The fixed query dola's web client sends (aid 495671, region VN); `web_tab_id` is fresh per call."""
    return {
        "aid": "495671", "real_aid": "495671", "device_platform": "web",
        "doubao_device_platform": "web", "language": "en", "region": "VN",
        "sys_region": "VN", "pkg_type": "release_version", "version_code": "20800",
        "use-olympus-account": "1", "web_platform": "browser",
        "web_tab_id": str(uuid.uuid4()),
    }


def proxies_for(proxy) -> dict[str, str] | None:
    url = credential.proxy_url(proxy)
    return {"http": url, "https": url} if url else None


class DolaHttp:
    """Cookie + proxy bound client. `cookie` is a `Cookie:` header value; it is never logged."""

    def __init__(self, cookie: str, proxies: Mapping[str, str] | None = None, transport: Transport | None = None):
        self._cookie = cookie
        self.proxies = dict(proxies) if proxies else None
        self._transport = transport or CurlTransport()

    @classmethod
    def for_account(cls, account, transport: Transport | None = None) -> "DolaHttp":
        """Built from the run's `AccountView`: the sealed credential gives the cookies, its proxy the route."""
        cookies = credential.jar_from_blob(account.credential)
        return cls(credential.cookie_header(cookies), proxies_for(account.proxy), transport)

    async def post_chain(self, path: str, payload: Mapping[str, Any], referer: str, timeout: float = API_TIMEOUT_S):
        """POST a JSON command to dola's `im` or `samantha` API with the account's cookies and proxy. A redirect is
        returned (status 3xx), not followed: every caller reads a non-200 as a failed read."""
        headers = {"accept": "*/*", "agw-js-conv": "str", "content-type": "application/json; encoding=utf-8",
                   "origin": BASE, "referer": referer, "cookie": self._cookie}
        return await asyncio.to_thread(self._transport.post, BASE + path, params=query_params(), headers=headers,
                                       json=payload, timeout=timeout, proxies=self.proxies)

    async def post_data(self, path: str, query: str, data: bytes, headers: Mapping[str, str],
                        timeout: float = API_TIMEOUT_S, lines: list[str] | None = None):
        """POST exact bytes to `path?query` with the account's cookie and proxy (see `Transport.post_data`)."""
        return await asyncio.to_thread(self._transport.post_data, BASE + path + "?" + query, data=data,
                                       headers={**headers, "cookie": self._cookie}, timeout=timeout,
                                       proxies=self.proxies, lines=lines)

    async def get_with_cookie(self, url: str, timeout: float = 60):
        """A GET that carries the account's cookie (the `fallback_api` route), through the account proxy. A redirect
        is returned, not followed: the cookie must not travel to wherever the answer points."""
        return await asyncio.to_thread(self._transport.get, url, headers={"cookie": self._cookie}, timeout=timeout,
                                       proxies=self.proxies)
