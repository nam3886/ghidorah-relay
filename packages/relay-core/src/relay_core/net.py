"""Network safety (contracts §13, I16, D27): the SSRF-checked fetch and proxy-string parsing.

The server fetches URLs a client chose (input images, webhooks), so a URL must lead to the public internet only:
- https only (D27), no user:pass in the URL;
- resolve the host and allow only addresses where `ipaddress.ip_address(a).is_global` holds after unwrapping the
  forms that carry an IPv4 address inside an IPv6 one (this excludes private, loopback, link-local, the metadata
  address 169.254.169.254, CGNAT 100.64/10, 0.0.0.0/8 and fc00::/7); one private answer among several refuses the
  URL;
- connect to the checked IP itself, never to the name again (no DNS rebinding between check and connect); the
  TLS server name and `Host` stay the original host so the certificate is still verified against it;
- follow redirects by hand and re-check every hop (at most 4); a webhook follows none.

Proxy strings: `ip:port`, `ip:port:user:pass`, `user:pass@ip:port`, `scheme://user:pass@ip:port`.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from relay_core.db.pool import assert_no_tx

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


async def check_url(url: str, resolver: Resolver = system_resolver) -> Target:
    """Refuse the URL unless it is https and every address its host resolves to is public."""
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise UnsafeUrl("malformed")
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError:
        raise UnsafeUrl("malformed") from None
    if parts.scheme != "https":
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
    url = "https://%s:%d%s" % (ip, target.port, (parts.path or "/") + ("?" + parts.query if parts.query else ""))
    name = "[%s]" % target.host if ":" in target.host else target.host
    host_header = name if target.port == 443 else "%s:%d" % (name, target.port)
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
