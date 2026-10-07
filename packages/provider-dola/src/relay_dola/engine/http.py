"""HTTP to dola over curl_cffi (Chrome fingerprint), bound to one account's cookies and proxy.

dola checks the TLS fingerprint, so the transport is curl_cffi `impersonate="chrome"`; it is blocking, so every call
here runs through `asyncio.to_thread`. The transport is a seam: tests pass an in-process fake, nothing here opens a
socket on import (curl_cffi is imported on first real use).

Every poll, reconcile read, title lookup and fetch goes through `DolaHttp`; it never spends credits.
"""

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from relay_dola import credential

BASE = "https://www.dola.com"
MEDIA_TIMEOUT_S = 300   # a master of ~60 MB over a slow proxy
API_TIMEOUT_S = 30


class Response(Protocol):
    status_code: int
    content: bytes

    def json(self) -> Any: ...


class Transport(Protocol):
    """The two calls the engine makes. `proxies` is curl_cffi's `{"http": url, "https": url}` or None."""

    def post(self, url: str, *, params: Mapping[str, str], headers: Mapping[str, str], json: Any, timeout: float,
             proxies: Mapping[str, str] | None) -> Response: ...

    def get(self, url: str, *, headers: Mapping[str, str] | None, timeout: float,
            proxies: Mapping[str, str] | None) -> Response: ...


class CurlTransport:
    """The real transport. curl_cffi is imported on first call so importing the engine stays cheap and offline."""

    def post(self, url, *, params, headers, json, timeout, proxies):
        from curl_cffi import requests as cffi
        return cffi.post(url, params=params, headers=headers, json=json, impersonate="chrome", timeout=timeout,
                         proxies=proxies)

    def get(self, url, *, headers, timeout, proxies):
        from curl_cffi import requests as cffi
        return cffi.get(url, headers=headers, impersonate="chrome", timeout=timeout, proxies=proxies)


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
        """POST a JSON command to dola's `im` or `samantha` API with the account's cookies and proxy."""
        headers = {"accept": "*/*", "agw-js-conv": "str", "content-type": "application/json; encoding=utf-8",
                   "origin": BASE, "referer": referer, "cookie": self._cookie}
        return await asyncio.to_thread(self._transport.post, BASE + path, params=query_params(), headers=headers,
                                       json=payload, timeout=timeout, proxies=self.proxies)

    async def get_with_cookie(self, url: str, timeout: float = 60):
        """A GET that carries the account's cookie (the `fallback_api` route), through the account proxy."""
        return await asyncio.to_thread(self._transport.get, url, headers={"cookie": self._cookie}, timeout=timeout,
                                       proxies=self.proxies)

    async def download(self, url: str) -> bytes:
        """Media links are signed CDN links with no cookie: direct first, the account proxy only when direct fails
        (v4 measured a flaky proxy giving 7 MB in 300 s against 1.5 s direct). Raises `DownloadError`."""
        try:
            return await self._download_once(url, None)
        except DownloadError:
            if self.proxies is None:
                raise
            return await self._download_once(url, self.proxies)

    async def _download_once(self, url: str, proxies: Mapping[str, str] | None) -> bytes:
        try:
            resp = await asyncio.to_thread(self._transport.get, url, headers=None, timeout=MEDIA_TIMEOUT_S,
                                           proxies=proxies)
        except Exception as exc:   # noqa: BLE001 - any transport failure is a failed download attempt
            raise DownloadError("media download failed: %s" % str(exc)[:120]) from exc
        if resp.status_code // 100 != 2:
            raise DownloadError("media download returned HTTP %d" % resp.status_code)
        if not resp.content:
            raise DownloadError("media download returned an empty body")
        return resp.content


class DownloadError(RuntimeError):
    """A media download that did not give bytes; the core fetcher retries (`render.download_failed`)."""
