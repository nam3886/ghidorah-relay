"""Network safety (I16): the SSRF-checked fetch and proxy-string parsing.

The server fetches URLs a client chose (input images, webhooks), so a URL must lead to the public internet only:
- https only, no user:pass in the URL;
- resolve the host and allow only addresses where `ipaddress.ip_address(a).is_global` holds after unwrapping the
  forms that carry an IPv4 address inside an IPv6 one (this excludes private, loopback, link-local, the metadata
  address 169.254.169.254, CGNAT 100.64/10, 0.0.0.0/8 and fc00::/7); one private answer among several refuses the
  URL;
- connect to the checked IP itself, never to the name again (no DNS rebinding between check and connect); the
  TLS server name and `Host` stay the original host so the certificate is still verified against it;
- follow redirects by hand and re-check every hop (at most 4); a webhook follows none.

A provider's media (`stream_download`) goes through the account's proxy when the account has one, never direct.

Proxy strings: `ip:port`, `ip:port:user:pass`, `user:pass@ip:port`, `scheme://user:pass@ip:port`.
"""

import asyncio
import contextlib
import functools
import ipaddress
import logging
import re
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from relay_core.db.pool import assert_no_tx

log = logging.getLogger("relay.net")
# httpx logs `HTTP Request: GET <full URL>` at INFO for every request: the pinned address, the path and the query of a
# signed link. Every process that fetches through this module (api, worker, CLI) imports it, so the cut is made here
# and does not depend on a log filter's patterns.
logging.getLogger("httpx").setLevel(logging.WARNING)

MAX_REDIRECTS = 4
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_GLOBAL_V6 = ipaddress.ip_network("2000::/3")
# global unicast only: rules out `::a.b.c.d`, `::ffff:0:a.b.c.d` and `fec0::/10`
_PROXY_SCHEMES = frozenset({"http", "https", "socks4", "socks5", "socks5h"})

Resolver = Callable[[str, int], Awaitable[list[str]]]


class NetError(Exception):
    """`reason` is a short fixed word; callers map it to their own error code and never echo upstream detail."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class UnsafeUrl(NetError):
    """The URL is refused before or between requests: scheme, credentials, unresolvable, private address,
    too many redirects."""


class FetchFailed(NetError):
    """The URL was allowed but the transfer failed (connect, timeout, too large)."""


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """The IPv4 address an IPv6 address carries: `::ffff:a.b.c.d` (IPv4-mapped) and NAT64 `64:ff9b::/96`.
    Python 3.13's `is_global` already handles the mapped form; NAT64 it reports as global."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def is_public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    ip = _unwrap(ip)
    if isinstance(ip, ipaddress.IPv6Address) and ip not in _GLOBAL_V6:
        return False
    return ip.is_global


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


@dataclass(frozen=True)
class Target:
    """A checked URL and the address to connect to."""

    url: str
    host: str
    port: int
    ip: str


async def check_url(url: str, resolver: Resolver = system_resolver, *, http_ok: bool = False) -> Target:
    """Refuse the URL unless it is https (http too with `http_ok`, which only changes the scheme and the default port;
    the caller decides which hosts may use it) and every address its host resolves to is public."""
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise UnsafeUrl("malformed")
    try:
        parts = urlsplit(url)
        port = parts.port or (80 if parts.scheme == "http" else 443)
    except ValueError:
        raise UnsafeUrl("malformed") from None
    if parts.scheme != "https" and not (http_ok and parts.scheme == "http"):
        raise UnsafeUrl("scheme")
    host = parts.hostname
    if not host:
        raise UnsafeUrl("host")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrl("credentials")
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            async with asyncio.timeout(10):
                addresses = await resolver(host, port)
        except (OSError, UnicodeError, TimeoutError):
            raise UnsafeUrl("unresolvable") from None
    if not addresses:
        raise UnsafeUrl("unresolvable")
    if not all(is_public_ip(a) for a in addresses):
        raise UnsafeUrl("private_address")
    return Target(url, host, port, addresses[0].split("%")[0])


@dataclass(frozen=True)
class SafeResponse:
    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str


def _pinned(target: Target) -> tuple[str, dict[str, str], dict]:
    """(URL with the IP in place of the name, the Host header, request extensions carrying the TLS server name)."""
    parts = urlsplit(target.url)
    ip = "[%s]" % target.ip if ":" in target.ip else target.ip
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    url = "%s://%s:%d%s" % (parts.scheme, ip, target.port, path)
    name = "[%s]" % target.host if ":" in target.host else target.host
    host_header = name if target.port == (80 if parts.scheme == "http" else 443) else "%s:%d" % (name, target.port)
    return url, {"Host": host_header}, {"sni_hostname": target.host}


async def request(method: str, url: str, *, resolver: Resolver = system_resolver,
                  transport: httpx.AsyncBaseTransport | None = None, headers: Mapping[str, str] | None = None,
                  content: bytes | None = None, max_redirects: int = MAX_REDIRECTS, max_bytes: int = 32 * 2**20,
                  timeout_s: float = 20.0) -> SafeResponse:
    """One SSRF-checked request. Only GET follows redirects (each hop is checked again); with `max_redirects=0`
    a 3xx comes back as the answer, which is what a webhook wants. `timeout_s` is the per-phase httpx timeout AND
    the deadline of the whole call. `transport` is for tests."""
    assert_no_tx()
    method = method.upper()
    max_redirects = min(max_redirects, MAX_REDIRECTS) if method == "GET" else 0
    try:
        async with asyncio.timeout(timeout_s):                 # one deadline for every hop and the whole body
            return await _follow(method, url, resolver, transport, headers, content, max_redirects, max_bytes,
                                 timeout_s)
    except TimeoutError:
        raise FetchFailed("timeout") from None


async def _follow(method, url, resolver, transport, headers, content, max_redirects, max_bytes, timeout_s):
    current = url
    async with httpx.AsyncClient(transport=transport, timeout=timeout_s, follow_redirects=False,
                                 trust_env=False) as client:
        for hop in range(max_redirects + 1):
            target = await check_url(current, resolver)
            pinned_url, host_header, extensions = _pinned(target)
            try:
                async with client.stream(method, pinned_url, extensions=extensions, content=content,
                                         headers={**(headers or {}), **host_header} if hop == 0 else host_header) as r:
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise FetchFailed("too_large")
                    status, response_headers = r.status_code, r.headers
            except httpx.HTTPError:
                raise FetchFailed("transfer") from None
            location = response_headers.get("location")
            if status in _REDIRECT_STATUSES and location and max_redirects:
                if hop >= max_redirects:
                    raise UnsafeUrl("redirects")
                current = urljoin(current, location)
                continue
            return SafeResponse(status, response_headers, bytes(body), current)
    raise AssertionError("unreachable")     # the loop returns or raises on its last hop


async def safe_get(url: str, **kw) -> SafeResponse:
    return await request("GET", url, **kw)


async def safe_post(url: str, content: bytes, **kw) -> SafeResponse:
    """Never follows a redirect."""
    return await request("POST", url, content=content, max_redirects=0, **kw)


# ---- media download ---------------------------------------------------------------
CLOSE_TIMEOUT_S = 5.0      # closing a response or client is best effort and never hangs the helper
_HOST_NAME = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")      # letters, digits, "-" and "." only: no backslash, "%", IDN
# With no proxy the download resolves the name itself: a name that does not resolve, or an answer that is not public
# (a filtering resolver on this network answers 0.0.0.0 or a LAN address for a listed host), fails it as a transfer
# failure the fetcher retries later; nothing is connected.
_UNUSABLE = {"unresolvable": "dns", "private_address": "private"}


def _on(host: str, hosts: tuple[str, ...]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def _default_port(parts) -> bool:
    return parts.port in (None, 80 if parts.scheme == "http" else 443)


def host_allowed(url: str, hosts: tuple[str, ...], *, https_only: bool = True,
                 http_hosts: tuple[str, ...] = (), default_port_only: bool = False) -> bool:
    """Is `url` an https link on one of `hosts` (the name itself or a subdomain), with no user:pass and a plain
    host name. Plain http only for a host in `http_hosts` (or for any host with `https_only=False`); a port other
    than 80/443 only without `default_port_only`.
    `https://www.dola.com@evil.example` is a link to evil.example; a host with a backslash or "%" is no link at all."""
    if any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port                  # raises on a malformed port
    except ValueError:
        return False
    if not _HOST_NAME.fullmatch(host):
        return False
    if parts.scheme != "https" and not (parts.scheme == "http" and (not https_only or _on(host, http_hosts))):
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if default_port_only and not _default_port(parts):
        return False
    return _on(host, hosts)


def url_host(url: str) -> str:
    """The host name only: the rest of a media link is signed."""
    try:
        return urlsplit(url).hostname or "?"
    except ValueError:
        return "?"


def _refuse(url: str, reason: str):
    log.warning("media link refused (%s): %s", reason, url_host(url))
    raise UnsafeUrl(reason)


async def _wait(deadline: float, fn, *args, **kw):
    """`await fn(*args)` inside the download's one absolute deadline. A bound on this one wait only, never on a yield:
    a timeout that spanned a yield would cancel the caller's own wait."""
    if asyncio.get_running_loop().time() >= deadline:
        raise FetchFailed("timeout")
    try:
        async with asyncio.timeout_at(deadline) as bound:
            return await fn(*args, **kw)
    except TimeoutError:
        raise FetchFailed("timeout" if bound.expired() else "transfer") from None


class _CloseBudget:
    """Best-effort closing with ONE time budget for the whole download (CLOSE_TIMEOUT_S, shared by every response and
    client it closes, redirect hops included): a peer that never finishes closing cannot hang the helper."""

    def __init__(self):
        self.left = CLOSE_TIMEOUT_S

    async def close(self, *closers) -> None:
        loop = asyncio.get_running_loop()
        for close in closers:
            start = loop.time()
            try:
                async with asyncio.timeout(max(self.left, 0)):
                    await close()
            except Exception as e:      # noqa: BLE001 - a failed close must not hide the download's own outcome
                log.debug("media close: %s", type(e).__name__)
            self.left -= loop.time() - start


async def _download(url, allowed, proxy, deadline, max_bytes, chunk_size, transport,
                    resolver) -> AsyncIterator[bytes]:
    """The download over its one route. One chunk is held back so that a body that turns out empty or truncated fails
    before its last chunk was handed over. Without a proxy every hop is resolved and checked here and the connection
    goes to the checked address; through the proxy nothing is resolved here (the proxy resolves the name in its own
    network, so there is no address to check or pin), and the host list on every hop is the guard."""
    client = httpx.AsyncClient(transport=transport(proxy) if transport else None, proxy=None if transport else proxy,
                               timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False, trust_env=False)
    closing, resp = _CloseBudget(), None
    try:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            if not allowed(current):
                _refuse(current, "host")
            if not _default_port(urlsplit(current)):
                _refuse(current, "port")
            request_url, headers, extensions = current, {}, {}
            if proxy is None:
                try:
                    target = await _wait(deadline, check_url, current, resolver, http_ok=True)
                except UnsafeUrl as e:
                    if e.reason in _UNUSABLE:
                        raise FetchFailed(_UNUSABLE[e.reason]) from None
                    _refuse(current, e.reason)
                request_url, headers, extensions = _pinned(target)
            try:
                request = client.build_request("GET", request_url, extensions=extensions,
                                               headers={"Accept-Encoding": "identity", **headers})
            except (httpx.InvalidURL, ValueError):
                _refuse(current, "malformed")
            try:
                resp = await _wait(deadline, client.send, request, stream=True)
            except UnicodeError:        # httpx builds a redirect's next request even unfollowed: broken punycode label
                _refuse(current, "malformed")
            location = resp.headers.get("location")
            if resp.status_code in _REDIRECT_STATUSES and location:
                if hop >= MAX_REDIRECTS:
                    _refuse(current, "redirects")
                try:
                    current = urljoin(current, location)
                except ValueError:
                    _refuse(current, "malformed")
                await closing.close(resp.aclose)
                continue
            break
        if resp.status_code // 100 != 2:
            raise FetchFailed("status")
        if resp.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
            raise FetchFailed("encoding")       # a compressed body would break the memory bound
        expected = resp.headers.get("content-length", "")
        if not expected.isdigit() and "chunked" not in resp.headers.get("transfer-encoding", "").lower():
            raise FetchFailed("no_length")      # its end could not be told from a cut connection
        it = resp.aiter_raw(chunk_size)
        total, pending = 0, None
        while (part := await _wait(deadline, anext, it, None)) is not None:
            if pending is not None:
                total += len(pending)
                if total > max_bytes:
                    raise FetchFailed("too_large")
                yield pending
            pending = part
        if expected.isdigit() and resp.num_bytes_downloaded < int(expected):
            raise FetchFailed("truncated")
        if pending is None:
            raise FetchFailed("empty")
        if total + len(pending) > max_bytes:
            raise FetchFailed("too_large")
        yield pending
    except httpx.HTTPError:
        raise FetchFailed("transfer") from None
    finally:
        await closing.close(*([resp.aclose] if resp is not None else []), client.aclose)


async def stream_download(url: str, *, hosts: tuple[str, ...], proxy: str | None, max_bytes: int, deadline_s: float,
                          https_only: bool = True, http_hosts: tuple[str, ...] = (), chunk_size: int = 1 << 20,
                          resolver: Resolver = system_resolver,
                          transport: Callable[[str | None], httpx.AsyncBaseTransport] | None = None
                          ) -> AsyncIterator[bytes]:
    """`chunk_size` chunks of a media file on one of `hosts`, with no cookie and no `Authorization`, uncompressed
    (`Accept-Encoding: identity`; a response that is compressed anyway is refused). https only, except for the hosts in
    `http_hosts` (or all of them with `https_only=False`, tests). Only default ports (80, 443).
    ONE route, never a second: with a `proxy` (the account's) every request goes through it and nothing is ever
    connected from here, so the provider and its CDN never see this machine's address; a proxy that fails is a
    FetchFailed like any transfer failure (the caller retries later), never a reason to go direct. The proxy resolves
    the name, so no address is checked or pinned on that route: the host list, checked on every redirect hop, is its
    guard. Without a proxy the request goes direct: every hop is resolved with `resolver` and the connection goes to
    the checked address; a name that does not resolve, or any answer that is not public, fails the download
    (FetchFailed "dns"/"private", nothing is connected). All the closes of one download share one CLOSE_TIMEOUT_S
    budget. More than `max_bytes` -> FetchFailed("too_large"); a body shorter than its Content-Length -> "truncated";
    a 2xx with neither Content-Length nor chunked framing -> "no_length"; past `deadline_s` (one deadline over every
    redirect and the body) -> "timeout", never CancelledError. A refused link (host, scheme, port, malformed, also on
    a redirect hop) -> UnsafeUrl, raised before that link is requested. `transport` (tests) builds the transport of
    the route from its proxy URL (None = direct). Close it early with `contextlib.aclosing`: the upstream stream is
    closed at once. A failure is logged with its reason and host name only."""
    assert_no_tx()
    deadline = asyncio.get_running_loop().time() + deadline_s
    allowed = functools.partial(host_allowed, hosts=hosts, https_only=https_only, http_hosts=http_hosts)
    try:
        async with contextlib.aclosing(_download(url, allowed, proxy, deadline, max_bytes, chunk_size, transport,
                                                 resolver)) as parts:
            async for part in parts:
                yield part
    except FetchFailed as e:
        log.warning("media download failed (%s): %s", e.reason, url_host(url))
        raise


# ---- proxy strings ----------------------------------------------------------------------------------------------
class ProxyError(ValueError):
    pass


@dataclass(frozen=True)
class Proxy:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = field(default="", repr=False)

    @property
    def label(self) -> str:
        """Host and port only: the one form fit for logs and the admin list."""
        return "%s:%d" % ("[%s]" % self.host if ":" in self.host else self.host, self.port)

    @property
    def server(self) -> str:
        return "%s://%s" % (self.scheme, self.label)

    @property
    def url(self) -> str:
        """For httpx and curl style clients; credentials are percent-encoded."""
        if not self.username:
            return self.server
        return "%s://%s:%s@%s" % (self.scheme, quote(self.username, safe=""), quote(self.password, safe=""),
                                  self.label)

    @property
    def playwright(self) -> dict[str, str]:
        out = {"server": self.server}
        if self.username:
            out |= {"username": self.username, "password": self.password}
        return out


def _hostport(text: str) -> tuple[str, int]:
    if text.startswith("["):                                   # [v6]:port
        host, _, rest = text[1:].partition("]")
        port = rest.removeprefix(":")
    else:
        host, _, port = text.rpartition(":")
    if not host or not port.isascii() or not port.isdigit() or not 0 < int(port) < 65536:
        raise ProxyError("a proxy needs a host and a port")
    if any(c.isspace() or c in "/?#@" for c in host):
        raise ProxyError("the proxy host is not valid")
    return host, int(port)


def parse_proxy(text: str | None) -> Proxy | None:
    """None for an empty string; `ProxyError` for anything that is not one of the four forms."""
    p = (text or "").strip()
    if not p:
        return None
    scheme = "http"
    if "://" in p:
        scheme, p = p.split("://", 1)
        scheme = scheme.lower()
    if scheme not in _PROXY_SCHEMES:
        raise ProxyError("unsupported proxy scheme")
    user = pwd = ""
    if "@" in p:
        cred, p = p.rsplit("@", 1)
        user, _, pwd = cred.partition(":")
        user, pwd = unquote(user), unquote(pwd)
    elif p.count(":") >= 3 and not p.startswith("["):          # ip:port:user:pass, the password may hold colons
        host, port, user, pwd = p.split(":", 3)
        p = host + ":" + port
    elif p.count(":") == 2 and not p.startswith("["):
        raise ProxyError("a proxy with a user also needs a password")
    host, port = _hostport(p)
    return Proxy(scheme, host, port, user, pwd)
