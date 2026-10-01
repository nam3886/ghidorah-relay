"""SSRF-checked fetching and proxy strings (contracts §13, I16, D27). No real DNS and no real network: a fake
resolver and httpx's MockTransport stand in for both."""

import asyncio
import functools
import socket

import httpx
import pytest

from relay_core import net
from relay_core.db import open_pool, tx
from relay_core.net import FetchFailed, ProxyError, UnsafeUrl, parse_proxy
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/net.py"
PUBLIC_A, PUBLIC_B = "93.184.216.34", "151.101.1.1"


def sync(test):
    """Run an async test on its own loop (the repo has no async test plugin)."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


DNS = {
    "files.example.test": [PUBLIC_A],
    "cdn.example.test": [PUBLIC_B],
    "evil.test": ["127.0.0.1"],
    "mixed.test": [PUBLIC_A, "10.0.0.5"],
    "mapped.test": ["::ffff:192.168.1.10"],
    "v6.example.test": ["2606:4700::1111"],
}


async def fake_resolver(host: str, port: int) -> list[str]:
    if host not in DNS:
        raise socket.gaierror("no such host")
    return DNS[host]


class Site:
    """A MockTransport that records what the client really connected to and serves a script of answers."""

    def __init__(self, routes: dict[str, httpx.Response | list[httpx.Response]] | None = None):
        self.routes = routes or {}
        self.seen: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        answer = self.routes.get(request.headers["host"], httpx.Response(200, content=b"ok"))
        if isinstance(answer, list):
            return answer.pop(0)
        return answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


async def get(url: str, site: Site | None = None, **kw) -> net.SafeResponse:
    return await net.safe_get(url, resolver=fake_resolver, transport=(site or Site()).transport, **kw)


async def refused(url: str, reason: str | None = None, site: Site | None = None, **kw) -> UnsafeUrl:
    with pytest.raises(UnsafeUrl) as info:
        await get(url, site, **kw)
    if reason:
        assert info.value.reason == reason, info.value.reason
    return info.value


# ---- addresses ---------------------------------------------------------------------------------------------------
@mutation(SRC, "ip = _unwrap(ip)", "ip = ip")
def test_addresses_that_carry_an_ipv4_address_are_judged_by_it():
    for a in ("::ffff:192.168.1.1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.0.0.1",
              "64:ff9b::c0a8:101", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe"):
        assert not net.is_public_ip(a), a
    for a in ("::ffff:8.8.8.8", "64:ff9b::808:808"):
        assert net.is_public_ip(a), a


def test_public_and_non_public_addresses():
    for a in (PUBLIC_A, PUBLIC_B, "8.8.8.8", "2606:4700::1111"):
        assert net.is_public_ip(a), a
    for a in ("10.0.0.1", "172.16.5.5", "192.168.0.1", "127.0.0.1", "169.254.169.254", "100.64.0.1", "100.127.255.255",
              "0.0.0.0", "0.1.2.3", "255.255.255.255", "::", "::1", "fc00::1", "fd12:3456::1", "fe80::1",
              "2001:db8::1", "not an address", "", "fe80::1%eth0"):
        assert not net.is_public_ip(a), a


@pytest.mark.parametrize("host", ["10.0.0.1", "127.0.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "0.1.2.3",
                                  "192.168.1.1", "172.16.0.9", "[::1]", "[::]", "[fc00::1]", "[fd00::5]", "[fe80::1]",
                                  "[::ffff:192.168.1.1]", "[::ffff:7f00:1]", "[64:ff9b::c0a8:101]"])
@sync
async def test_private_literal_addresses_are_refused_before_any_request(host):
    site = Site()
    await refused("https://%s/x" % host, "private_address", site)
    assert site.seen == []


@sync
async def test_names_that_resolve_to_private_addresses_are_refused():
    site = Site()
    await refused("https://evil.test/x", "private_address", site)
    await refused("https://mapped.test/x", "private_address", site)
    assert site.seen == []


@mutation(SRC, "if not all(is_public_ip(a) for a in addresses):", "if not any(is_public_ip(a) for a in addresses):")
@sync
async def test_one_private_answer_among_public_ones_refuses_the_url():
    site = Site()
    await refused("https://mixed.test/x", "private_address", site)
    assert site.seen == []


@sync
async def test_localhost_through_the_real_resolver_is_refused():
    with pytest.raises(UnsafeUrl) as info:
        await net.check_url("https://localhost/x")
    assert info.value.reason == "private_address"


@sync
async def test_unresolvable_and_empty_answers_are_refused():
    await refused("https://nowhere.test/x", "unresolvable")

    async def nothing(host, port):
        return []

    with pytest.raises(UnsafeUrl, match="unresolvable"):
        await net.check_url("https://files.example.test/", nothing)


# ---- scheme and shape --------------------------------------------------------------------------------------------
@mutation(SRC, 'if parts.scheme != "https":', 'if parts.scheme not in ("http", "https"):')
@sync
async def test_http_is_refused_even_to_a_public_host():
    site = Site()
    await refused("http://files.example.test/a.png", "scheme", site)
    assert site.seen == []
    for url in ("ftp://files.example.test/a", "file:///etc/passwd", "gopher://files.example.test/",
                "//files.example.test/a", "files.example.test/a", "javascript:alert(1)", "data:text/plain,hi"):
        await refused(url, "scheme", site)


@sync
async def test_malformed_urls_and_credentials_are_refused():
    await refused("https://files.example.test:99999/x", "malformed")
    await refused("https://files.example.test/a b", "malformed")
    await refused("https://files.example.test/a\r\nHost: evil", "malformed")
    await refused("https://user:pass@files.example.test/x", "credentials")
    await refused("https://user@files.example.test/x", "credentials")
    await refused("https:///x", "host")
    assert (await net.check_url("https://files.example.test:8443/x", fake_resolver)).port == 8443


# ---- connecting to the checked IP ---------------------------------------------------------------------------------
PIN = 'url = "https://%s:%d%s" % (ip, target.port,'


PIN_BY_NAME = 'url = "https://%s:%d%s" % (target.host, target.port,'


@mutation(SRC, PIN, PIN_BY_NAME)
@sync
async def test_the_request_goes_to_the_checked_ip_with_the_original_name_for_tls():
    site = Site({"files.example.test": httpx.Response(200, content=b"png-bytes")})
    out = await get("https://files.example.test/a/b.png?x=1&y=2", site, headers={"Accept": "image/*"})
    assert out.status_code == 200 and out.content == b"png-bytes"
    (request,) = site.seen
    assert request.url.host == PUBLIC_A and request.url.port is None      # 443, the default
    assert request.url.path == "/a/b.png" and request.url.query == b"x=1&y=2"
    assert request.headers["host"] == "files.example.test" and request.headers["accept"] == "image/*"
    assert request.extensions["sni_hostname"] == "files.example.test"


@sync
async def test_ipv6_target_and_a_non_default_port():
    site = Site({"v6.example.test:8443": httpx.Response(200, content=b"v6")})
    out = await get("https://v6.example.test:8443/", site)
    (request,) = site.seen
    assert out.content == b"v6" and request.url.host == "2606:4700::1111" and request.url.port == 8443


# ---- redirects ---------------------------------------------------------------------------------------------------
@mutation(SRC, "target = await check_url(current, resolver)",
          "target = (await check_url(current, resolver) if hop == 0 else Target(current, urlsplit(current).hostname"
          " or \"\", 443, urlsplit(current).hostname or \"\"))")
@sync
async def test_a_redirect_into_a_private_address_is_refused_at_the_hop():
    for location in ("https://127.0.0.1/admin", "https://evil.test/x", "https://[::ffff:10.0.0.1]/"):
        site = Site({"files.example.test": redirect(location)})
        await refused("https://files.example.test/a", "private_address", site)
        assert [r.url.host for r in site.seen] == [PUBLIC_A], location       # the private hop was never contacted


@mutation(SRC, "if hop >= max_redirects:", "if hop > max_redirects + 99:")
@sync
async def test_at_most_four_redirects_are_followed():
    four = Site({"files.example.test": [redirect("/1"), redirect("/2"), redirect("/3"), redirect("/4"),
                                        httpx.Response(200, content=b"end")]})
    out = await get("https://files.example.test/0", four)
    assert out.content == b"end" and out.url == "https://files.example.test/4" and len(four.seen) == 5
    five = Site({"files.example.test": [redirect("/%d" % i) for i in range(1, 6)]})
    await refused("https://files.example.test/0", "redirects", five)
    assert len(five.seen) == 5


@sync
async def test_redirects_are_followed_across_hosts_relative_and_absolute():
    site = Site({"files.example.test": [redirect("https://cdn.example.test/v.mp4", 301)],
                 "cdn.example.test": [redirect("/final", 307), httpx.Response(200, content=b"video")]})
    out = await get("https://files.example.test/start", site, headers={"Authorization": "Bearer x"})
    assert out.content == b"video" and out.url == "https://cdn.example.test/final"
    assert [r.url.host for r in site.seen] == [PUBLIC_A, PUBLIC_B, PUBLIC_B]
    assert "authorization" in site.seen[0].headers and "authorization" not in site.seen[1].headers


@sync
async def test_a_redirect_to_http_is_refused():
    site = Site({"files.example.test": redirect("http://cdn.example.test/x")})
    await refused("https://files.example.test/a", "scheme", site)


@sync
async def test_a_webhook_follows_no_redirect_and_sends_its_body():
    site = Site({"files.example.test": redirect("https://127.0.0.1/steal")})
    out = await net.safe_post("https://files.example.test/hook", b'{"event": "done"}', resolver=fake_resolver,
                              transport=site.transport, headers={"Content-Type": "application/json"})
    assert out.status_code == 302 and out.headers["location"] == "https://127.0.0.1/steal"
    (request,) = site.seen
    assert request.method == "POST" and request.content == b'{"event": "done"}'
    unfollowed = await get("https://files.example.test/a", Site({"files.example.test": redirect("/x")}),
                           max_redirects=0)
    assert unfollowed.status_code == 302


# ---- transfer limits ---------------------------------------------------------------------------------------------
@sync
async def test_oversized_bodies_and_transport_failures_are_fetch_failures():
    big = Site({"files.example.test": httpx.Response(200, content=b"x" * 5000)})
    with pytest.raises(FetchFailed, match="too_large"):
        await get("https://files.example.test/a", big, max_bytes=1000)

    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(FetchFailed, match="transfer"):
        await net.safe_get("https://files.example.test/a", resolver=fake_resolver, transport=httpx.MockTransport(boom))


# ---- proxy strings ------------------------------------------------------------------------------------------------
def test_the_four_proxy_forms():
    expected = {
        "103.1.2.3:3129": ("http", "103.1.2.3", 3129, "", ""),
        "103.1.2.3:3129:user1:pass1": ("http", "103.1.2.3", 3129, "user1", "pass1"),
        "user1:pass1@103.1.2.3:3129": ("http", "103.1.2.3", 3129, "user1", "pass1"),
        "socks5://bob:hunter2@10.9.8.7:1080": ("socks5", "10.9.8.7", 1080, "bob", "hunter2"),
        "HTTPS://px.example.net:443": ("https", "px.example.net", 443, "", ""),
        "  1.2.3.4:8080  ": ("http", "1.2.3.4", 8080, "", ""),
    }
    for text, want in expected.items():
        p = parse_proxy(text)
        assert (p.scheme, p.host, p.port, p.username, p.password) == want, text


def test_proxy_superset_cases():
    assert parse_proxy("1.2.3.4:80:alice:p:a:ss").password == "p:a:ss"            # a colon in the password
    assert parse_proxy("alice:p%40ss%3Aw@1.2.3.4:80").password == "p@ss:w"        # percent-encoded userinfo
    assert parse_proxy("alice:p@ss@1.2.3.4:80").password == "p@ss"                # the last @ splits
    assert parse_proxy("bob@1.2.3.4:80").username == "bob"
    v6 = parse_proxy("socks5://u:p@[2001:db8::1]:1080")
    assert (v6.host, v6.port, v6.label, v6.server) == ("2001:db8::1", 1080, "[2001:db8::1]:1080",
                                                      "socks5://[2001:db8::1]:1080")
    assert parse_proxy("") is None and parse_proxy("   ") is None and parse_proxy(None) is None


def test_proxy_output_forms_and_no_password_leak():
    p = parse_proxy("103.1.2.3:3129:user1:pa ss/1")
    assert p.url == "http://user1:pa%20ss%2F1@103.1.2.3:3129"
    assert parse_proxy(p.url) == p                                        # the URL form round-trips
    assert p.playwright == {"server": "http://103.1.2.3:3129", "username": "user1", "password": "pa ss/1"}
    assert p.label == "103.1.2.3:3129" and "user1" not in p.label and "pa ss" not in p.label
    assert "pa ss" not in repr(p)
    plain = parse_proxy("1.2.3.4:8080")
    assert plain.url == "http://1.2.3.4:8080" and plain.playwright == {"server": "http://1.2.3.4:8080"}


@pytest.mark.parametrize("bad", ["linh tinh", "host", "host:notaport", "host:0", "host:70000", "1.2.3.4:80:onlyuser",
                                 "ftp://1.2.3.4:80", "://1.2.3.4:80", "1.2.3.4:", ":80", "a b:80", "[::1]", "user:pw@",
                                 "http://1.2.3.4:80/path"])
def test_malformed_proxies_are_refused(bad):
    with pytest.raises(ProxyError):
        parse_proxy(bad)


# ---- IPv6 outside global unicast, the total deadline, and the transaction guard -----------------------------------
@mutation(SRC, "if isinstance(ip, ipaddress.IPv6Address) and ip not in _GLOBAL_V6:", "if False:")
def test_ipv6_that_hides_an_ipv4_address_or_is_not_global_unicast_is_refused():
    for a in ("::7f00:1", "::a9fe:a9fe", "::c0a8:101", "::ffff:0:7f00:1", "fec0::1"):
        assert not net.is_public_ip(a), a
    assert net.is_public_ip("2606:4700::1111") and net.is_public_ip("2a00:1450:4001::200e")
    site = Site()
    for host in ("[::7f00:1]", "[::ffff:0:7f00:1]", "[fec0::1]"):
        asyncio.run(refused("https://%s/x" % host, "private_address", site))
    assert site.seen == []


@mutation(SRC, "async with asyncio.timeout(timeout_s):", "async with asyncio.timeout(None):")
def test_one_deadline_covers_every_redirect_hop():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.15)                        # each hop is well inside the limit; the chain is not
        return redirect("/next")

    async def main():
        with pytest.raises(FetchFailed) as info:
            await net.safe_get("https://files.example.test/0", resolver=fake_resolver,
                               transport=httpx.MockTransport(slow), timeout_s=0.4)
        assert info.value.reason == "timeout"

    asyncio.run(main())


@mutation(SRC, "assert_no_tx()\n    method = method.upper()", "method = method.upper()")
def test_a_request_inside_a_database_transaction_is_refused(db):
    async def main():
        pool = await open_pool(db.dsn)
        try:
            site = Site()
            async with tx(pool):
                with pytest.raises(RuntimeError, match="foreign I/O"):
                    await get("https://files.example.test/a", site)
            assert site.seen == []
            assert (await get("https://files.example.test/a", site)).status_code == 200     # fine once it is closed
        finally:
            await pool.close()

    asyncio.run(main())
