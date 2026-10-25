"""`net.stream_download` and `net.host_allowed`: memory, routes, bounds, hosts.
No real network except one local socket; the media comes from `relay_core.testing.fake_media`."""

import asyncio
import contextlib
import functools
import gzip
import hashlib
import logging
import secrets
import time
import tracemalloc

import httpx
import pytest

from relay_core import net
from relay_core.db.pool import _in_tx
from relay_core.net import FetchFailed, UnsafeUrl, host_allowed, stream_download
from relay_core.testing.fake_media import PUBLIC_IP, Answer, Body, MediaSite, public_resolver
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/net.py"
MIB = 1 << 20
HOSTS = ("media.test", "other.test")
URL = "https://cdn.media.test/v/clip.mp4?sig=x"
PROXY = "http://proxy.invalid:8080"


def sync(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


def options(site, **kw):
    return {"hosts": HOSTS, "proxy": None, "max_bytes": 1 << 30, "deadline_s": 30.0, "transport": site,
            "resolver": public_resolver} | kw


async def drain(site, url=URL, **kw) -> bytes:
    async with contextlib.aclosing(stream_download(url, **options(site, **kw))) as parts:
        return b"".join([c async for c in parts])


def data(n: int) -> bytes:
    return hashlib.sha256(b"seed").digest() * (n // 32 + 1)


def redirect(location: str) -> Answer:
    return Answer(302, headers={"location": location})


# ---- memory ------------------------------------------------------------------------------------------------------
@mutation(SRC, "it = resp.aiter_raw(chunk_size)", "await resp.aread()\n        it = resp.aiter_bytes(chunk_size)",
          expect="peak memory grew with the body")
@sync
async def test_a_200_mb_body_streams_in_bounded_memory_and_arrives_intact():
    body = Body(200 * MIB)
    site = MediaSite(body)
    digest, size, biggest = hashlib.sha256(), 0, 0
    tracemalloc.start()
    try:
        async with contextlib.aclosing(stream_download(URL, **options(site))) as parts:
            async for chunk in parts:
                digest.update(chunk)
                size, biggest = size + len(chunk), max(biggest, len(chunk))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert digest.hexdigest() == body.sha256() and size == body.size, "the bytes did not arrive intact"
    assert biggest <= MIB, "a chunk was larger than chunk_size: %d" % biggest
    assert peak < 8 * MIB, "peak memory grew with the body: %.1f MiB" % (peak / MIB)


# ---- routes ------------------------------------------------------------------------------------------------------
@mutation(SRC, "aclosing(_download(url, allowed, proxy,", "aclosing(_download(url, allowed, None,",
          expect="a direct connection was made although the account has a proxy")
@sync
async def test_with_a_proxy_every_request_goes_through_it_and_none_is_direct():
    """The owner's rule (23/10): the provider and its CDN never see this machine's address when the account has a
    proxy. Every hop of a redirect chain goes through the proxy too."""
    def hops(request):
        return redirect("/second") if request.url.path == "/v/clip.mp4" else b"video"

    site = MediaSite(b"direct", hops)
    got = await drain(site, proxy=PROXY)
    assert site.routes == [PROXY, PROXY], \
        "a direct connection was made although the account has a proxy: %r" % site.routes
    assert got == b"video"


@mutation(SRC, '**({"User-Agent": user_agent} if user_agent else {})})', "})",
          expect="a hop went without the caller's User-Agent")
@sync
async def test_a_caller_user_agent_goes_on_every_hop_and_none_keeps_the_default():
    """A link signed for the client that resolved it (gemini's googlevideo.com, 25/10) is refused to another
    User-Agent, so the caller's goes on every hop, direct and through the proxy."""
    def hops(request):
        return redirect("/second") if request.url.path == "/v/clip.mp4" else b"video"

    for proxy in (None, PROXY):
        site = MediaSite(hops, hops)
        assert await drain(site, proxy=proxy, user_agent="Browser/1.0") == b"video"
        assert [r.headers.get("user-agent") for _, r in site.requests] == ["Browser/1.0"] * 2, \
            "a hop went without the caller's User-Agent (proxy %s)" % proxy
    site = MediaSite(hops)
    await drain(site)
    assert all(r.headers["user-agent"].startswith("python-httpx/") for _, r in site.requests)


@mutation(SRC, "proxy=None if transport else proxy,", "proxy=None,",
          expect="the real client was built without the account proxy")
@sync
async def test_the_real_client_is_built_with_the_account_proxy(clients):
    with pytest.raises(FetchFailed):
        await drain(None, proxy=PROXY, transport=None)
    (_, kw), = clients
    assert kw["proxy"] == PROXY and kw["transport"] is None, "the real client was built without the account proxy"


@sync
async def test_without_a_proxy_the_download_goes_direct():
    site = MediaSite(b"video")
    assert await drain(site) == b"video" and site.routes == [None]
    with pytest.raises(FetchFailed):
        await drain(MediaSite(403))


GOOD = data(3 * MIB + 17)


@mutation(SRC, "aclosing(_download(url, allowed, proxy,", "aclosing(_download(url, allowed, None,",
          expect="a failed proxy went direct")
@pytest.mark.parametrize("why,via_proxy", [
    ("proxy refused", httpx.ProxyError("refused")),
    ("connect error", httpx.ConnectError("refused")),
    ("407 from the proxy", 407),
    ("403", 403),
    ("empty body", b""),
    ("stall past the read timeout", Answer(body=Body.of(GOOD), stall_after=0)),
    ("failure after 512 KiB", Answer(body=Body.of(GOOD), fail_after=512 * 1024)),
    ("shorter than its Content-Length", Answer(body=Body.of(GOOD[:MIB // 2]), content_length=MIB)),
])
@sync
async def test_a_proxy_failure_is_a_fetch_failure_and_never_goes_direct(why, via_proxy):
    """The fetcher retries the output later; nothing falls back to the direct route."""
    site = MediaSite(GOOD, via_proxy)
    try:
        await drain(site, proxy=PROXY)
    except FetchFailed:
        pass
    else:
        raise AssertionError("a failed proxy went direct (%s): %r" % (why, site.routes))
    assert site.routes and set(site.routes) == {PROXY}, "a failed proxy went direct (%s): %r" % (why, site.routes)


# ---- bounds ------------------------------------------------------------------------------------------------------
@mutation(SRC, "if total + len(pending) > max_bytes:", "if False:",
          expect="a body over the cap was accepted by its last chunk")
@sync
async def test_the_cap_covers_the_held_back_last_chunk_too():
    """The cap is checked on every chunk handed over, the held-back last one included."""
    try:
        await drain(MediaSite(Body(5000)), chunk_size=4096, max_bytes=4999, proxy=None)
    except FetchFailed as e:
        assert e.reason == "too_large"
    else:
        raise AssertionError("a body over the cap was accepted by its last chunk")


@mutation(SRC, "                if total > max_bytes:", "                if False:",
          expect="a body over the cap was accepted")
@sync
async def test_a_body_over_the_cap_is_refused_and_one_at_the_cap_passes():
    assert len(await drain(MediaSite(Body(10_000)), chunk_size=1000, max_bytes=10_000, proxy=None)) == 10_000
    for size in (10_001, 50_000):                    # never more than the cap is handed over, however big the body
        handed = 0
        try:
            async with contextlib.aclosing(stream_download(URL, **options(MediaSite(Body(size)), proxy=None,
                                                                          chunk_size=1000, max_bytes=10_000))) as parts:
                async for chunk in parts:
                    handed += len(chunk)
        except FetchFailed as e:
            assert e.reason == "too_large"
        assert handed <= 10_000, "a body over the cap was accepted: %d bytes handed over" % handed


@mutation(SRC, "if expected.isdigit() and resp.num_bytes_downloaded < int(expected):",
          "if expected.isdigit() and False:",
          expect="a body cut short of its Content-Length was delivered")
@sync
async def test_a_body_cut_short_of_its_content_length_is_a_failure_even_after_the_first_chunk():
    site = MediaSite(Answer(body=Body(3 * MIB), content_length=4 * MIB), Body(3 * MIB))
    got = []
    try:
        async with contextlib.aclosing(stream_download(URL, **options(site))) as parts:
            async for chunk in parts:
                got.append(chunk)
    except FetchFailed as e:
        assert e.reason == "truncated" and got and site.routes == [None], (e.reason, len(got), site.routes)
    else:
        raise AssertionError("a body cut short of its Content-Length was delivered")


@mutation(SRC, "deadline = asyncio.get_running_loop().time() + deadline_s",
          "deadline = asyncio.get_running_loop().time() + 1e9", expect="the deadline did not stop a slow body")
@sync
async def test_the_deadline_stops_a_slow_body_as_a_fetch_failure():
    site = MediaSite(Answer(body=Body(MIB), delay=0.05), Body(MIB))       # 16 pieces, 0.8 s in all
    start = time.monotonic()
    try:
        await drain(site, deadline_s=0.3, chunk_size=MIB // 4)
    except FetchFailed as e:
        assert e.reason == "timeout" and time.monotonic() - start < 1.3, (e.reason, time.monotonic() - start)
    else:
        raise AssertionError("the deadline did not stop a slow body")
    assert site.routes == [None]


@sync
async def test_a_hung_body_ends_at_the_deadline_and_a_slow_caller_gets_a_failure_never_a_cancel():
    start = time.monotonic()
    with pytest.raises(FetchFailed) as info:
        await drain(MediaSite(Answer(body=Body(3 * MIB), hang_after=512 * 1024)), deadline_s=0.3)
    assert info.value.reason == "timeout" and time.monotonic() - start < 1.3

    site = MediaSite(Body(4 * MIB))
    got = 0
    with pytest.raises(FetchFailed) as info:                 # the caller sleeps past the deadline between chunks
        async with contextlib.aclosing(stream_download(URL, **options(site, deadline_s=0.3))) as parts:
            async for _ in parts:
                got += 1
                await asyncio.sleep(0.5)
    assert info.value.reason == "timeout" and got == 1


# ---- hosts -------------------------------------------------------------------------------------------------------
@mutation(SRC, "if not allowed(current):",
          "if False and not allowed(current):",
          expect="a link outside the allowed hosts was fetched")
@sync
async def test_a_link_outside_the_list_is_refused_before_any_request():
    site = MediaSite(b"video", b"video")
    for url in ("https://evil.example/x.mp4", "https://media.test.evil.example/x", "https://notmedia.test/x",
                "https://media.test@evil.example/x", "https://user:pw@cdn.media.test/x"):
        for proxy in (None, PROXY):
            try:
                await drain(site, url, proxy=proxy)
            except UnsafeUrl as e:
                assert e.reason == "host", url
            else:
                raise AssertionError("a link outside the allowed hosts was fetched: %s" % url)
    assert site.requests == []


@sync
async def test_the_scheme_rule_and_the_host_forms():
    site = MediaSite(b"video")
    with pytest.raises(UnsafeUrl):
        await drain(site, "http://cdn.media.test/x", proxy=None)
    assert await drain(site, "http://cdn.media.test/x", proxy=None, https_only=False) == b"video"
    for url in ("https://media.test/x", "https://A.B.Media.TEST/x", "https://cdn.other.test:8443/x"):
        assert host_allowed(url, HOSTS), url
    for url in ("ftp://cdn.media.test/x", "", "https://", "https://media.test:99999/x", "https://cdn.media.test/a b",
                "https://media.test.\u0000/x"):
        assert not host_allowed(url, HOSTS, https_only=False), url


@mutation(SRC, "if not allowed(current):",
          "if hop == 0 and not allowed(current):",
          expect="a redirect off the allowed hosts was followed")
@sync
async def test_every_redirect_hop_is_checked_and_at_most_four_are_followed():
    """On both routes: through the proxy the host list is the only guard of a hop."""
    off = lambda r: redirect("https://evil.example/x") if r.url.path == "/v/clip.mp4" else b"video"  # noqa: E731
    for proxy in (None, PROXY):
        site = MediaSite(off, off)
        try:
            await drain(site, proxy=proxy)
        except UnsafeUrl as e:
            assert e.reason == "host" and site.routes == [proxy], (proxy, site.routes)
        else:
            raise AssertionError("a redirect off the allowed hosts was followed (proxy %s)" % proxy)

    def chain(request):                                       # /r0 -> /r1 -> ... within the list, then the body
        n = int(request.url.path.removeprefix("/r") or 0) if request.url.path.startswith("/r") else 0
        return redirect("/r%d" % (n + 1)) if n < limit else b"video"

    limit = 4
    assert await drain(MediaSite(chain, b"v"), "https://cdn.media.test/r0", proxy=None) == b"video"
    limit = 5
    with pytest.raises(UnsafeUrl) as info:
        await drain(MediaSite(chain, b"v"), "https://cdn.media.test/r0")
    assert info.value.reason == "redirects"
    cross = MediaSite(lambda r: redirect("https://x.other.test/y") if r.headers["host"] == "cdn.media.test"
                      else b"video")
    assert await drain(cross, proxy=None) == b"video" and len(cross.requests) == 2


# ---- headers, environment, close, transactions -------------------------------------------------------------------
@sync
async def test_no_cookie_or_authorization_is_sent():
    site = MediaSite(b"video", b"video")
    await drain(site, URL)
    await drain(site, URL, proxy=PROXY)
    names = {k.lower() for _, q in site.requests for k in q.headers}
    assert not names & {"cookie", "authorization", "proxy-authorization"}, names


@pytest.fixture
def clients(monkeypatch):
    """The real httpx clients the helper builds (no transport seam), kept with their arguments; none ever sends."""
    made = []

    class Spy(httpx.AsyncClient):
        def __init__(self, **kw):
            super().__init__(**kw)
            made.append((self, kw))

        async def send(self, *args, **kw):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(net.httpx, "AsyncClient", Spy)
    return made


@mutation(SRC, "connect=10.0), follow_redirects=False, trust_env=False)",
          "connect=10.0), follow_redirects=False, trust_env=True)", expect="the environment proxy was honoured")
@sync
async def test_the_environment_proxy_is_ignored(clients, monkeypatch):
    """With `trust_env` on, httpx mounts HTTP_PROXY and friends for the request; NO_PROXY could hide that."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    with pytest.raises(FetchFailed):
        await drain(None, proxy=None, transport=None)
    (client, kw), = clients
    assert not client._mounts and kw["trust_env"] is False, "the environment proxy was honoured"


@mutation(SRC, "timeout=httpx.Timeout(30.0, connect=10.0)", "timeout=None",
          expect="the client was built with no timeout")
@sync
async def test_the_client_is_built_with_a_finite_timeout_and_no_redirect_following(clients):
    with pytest.raises(FetchFailed):
        await drain(None, proxy=None, transport=None)
    (_, kw), = clients
    timeout = kw["timeout"]
    assert isinstance(timeout, httpx.Timeout) and None not in (timeout.connect, timeout.read, timeout.write,
                                                               timeout.pool), "the client was built with no timeout"
    assert kw["follow_redirects"] is False


@mutation(SRC, "await closing.close(*([resp.aclose] if resp is not None else []), client.aclose)", "pass",
          expect="the upstream stream was left open")
@sync
async def test_closing_the_generator_early_closes_the_upstream_stream():
    site = MediaSite(Body(8 * MIB))
    async with contextlib.aclosing(stream_download(URL, **options(site))) as parts:
        first = await anext(parts)
        assert len(first) == MIB and site.closed == 0
    assert site.closed == 1, "the upstream stream was left open: %d" % site.closed


@sync
async def test_a_download_inside_a_database_transaction_is_refused():
    site = MediaSite(b"video")
    token = _in_tx.set(True)
    try:
        with pytest.raises(RuntimeError, match="foreign I/O"):
            await drain(site)
    finally:
        _in_tx.reset(token)
    assert site.requests == [] and net.host_allowed(URL, HOSTS)


# ---- the direct route resolves, checks and pins every hop ---------------------------------------------------------
def resolving(*addresses):
    async def resolver(host, port):
        return list(addresses)
    return resolver


@mutation(SRC, "if not all(is_public_ip(a) for a in addresses):", "if False:",
          expect="a host that resolves to a private address was connected to")
@mutation(SRC, '_UNUSABLE = {"unresolvable": "dns", "private_address": "private"}',
          '_UNUSABLE = {"unresolvable": "dns"}', expect="a private answer was not a fetch failure")
@pytest.mark.parametrize("answer", [["10.0.0.5"], ["127.0.0.1"], ["0.0.0.0"], ["169.254.169.254"], ["::1"],
                                    ["fe80::1"], ["93.184.216.34", "10.0.0.5"]])
@sync
async def test_without_a_proxy_a_private_answer_fails_the_download_and_nothing_is_requested(answer):
    """A filtering resolver on the LAN answers exactly this for a listed host: a fetch failure (retried later), no
    connection."""
    site = MediaSite(b"video")
    try:
        await drain(site, resolver=resolving(*answer))
    except UnsafeUrl:
        raise AssertionError("a private answer was not a fetch failure: %r" % answer) from None
    except FetchFailed as e:
        assert e.reason == "private", e.reason
    else:
        raise AssertionError("a host that resolves to a private address was connected to: %r" % answer)
    assert site.requests == [], "a host that resolves to a private address was connected to: %r" % site.requests


@mutation(SRC, "            if proxy is None:\n                try:", "            if True:\n                try:",
          expect="the proxy route resolved the name here")
@sync
async def test_through_the_proxy_nothing_is_resolved_here_and_the_proxy_gets_the_name():
    """The proxy resolves in its own network: a local answer (private, or none at all) does not matter there."""
    asked = []

    async def private(host, port):
        asked.append(host)
        return ["10.0.0.5"]

    site = MediaSite(b"direct", b"video")
    try:
        got = await drain(site, proxy=PROXY, resolver=private)
    except net.NetError as e:
        raise AssertionError("the proxy route resolved the name here: %s" % e.reason) from None
    assert got == b"video" and asked == [], "the proxy route resolved the name here: %r" % asked
    (_, request), = site.requests
    assert request.url.host == "cdn.media.test", request.url


@sync
async def test_a_redirect_hop_that_resolves_to_a_private_address_fails_the_direct_download_the_same_way():
    async def resolver(host, port):
        return ["10.0.0.5"] if host == "cdn.other.test" else ["93.184.216.34"]

    def direct(r):
        return redirect("https://cdn.other.test/y") if r.headers["host"] == "cdn.media.test" else b"v"

    site = MediaSite(direct)
    with pytest.raises(FetchFailed) as info:
        await drain(site, resolver=resolver)
    assert info.value.reason == "private" and site.routes == [None]
    assert all(q.url.host != "10.0.0.5" for _, q in site.requests), site.requests


@mutation(SRC, "request_url, headers, extensions = _pinned(target)", "pass",
          expect="the direct route did not connect to the checked address")
@sync
async def test_the_direct_route_connects_to_the_checked_address():
    site = MediaSite(b"video")
    assert await drain(site, resolver=resolving("93.184.216.34")) == b"video"
    (_, direct), = site.requests
    assert direct.url.host == "93.184.216.34" and direct.headers["host"] == "cdn.media.test" \
        and direct.extensions["sni_hostname"] == "cdn.media.test" and direct.url.path == "/v/clip.mp4", \
        "the direct route did not connect to the checked address: %s" % direct.url
    plain = MediaSite(b"video")
    await drain(plain, "http://cdn.media.test/x?a=1", http_hosts=("cdn.media.test",),
                resolver=resolving("93.184.216.34"))
    (_, request), = plain.requests
    assert str(request.url) == "http://93.184.216.34/x?a=1" and request.headers["host"] == "cdn.media.test", \
        "the direct route did not connect to the checked address: %s" % request.url


@mutation(SRC, "if not _default_port(urlsplit(current)):", "if False:", expect="a non-default port was used")
@mutation(SRC, "if not _default_port(urlsplit(current)):", "if proxy is None and not _default_port(urlsplit(current)):",
          expect="a non-default port was used on the proxy route")
@sync
async def test_only_default_ports_are_used_on_both_routes_also_after_a_redirect():
    site = MediaSite(b"video", b"video")
    for url in ("https://cdn.media.test:8443/x", "http://cdn.media.test:443/x", "https://cdn.media.test:80/x"):
        for proxy in (None, PROXY):
            try:
                await drain(site, url, http_hosts=("cdn.media.test",), proxy=proxy)
            except UnsafeUrl as e:
                assert e.reason == "port", url
            else:
                raise AssertionError("a non-default port was used on the proxy route: %s %s" % (proxy, url))
    assert site.requests == []
    assert await drain(MediaSite(b"video"), "https://cdn.media.test:443/x") == b"video"
    to_8443 = lambda r: redirect("https://cdn.media.test:8443/y")  # noqa: E731
    for proxy, served_by in ((None, "direct"), (PROXY, "proxy")):
        site = MediaSite(to_8443, to_8443)
        try:
            await drain(site, proxy=proxy)
        except UnsafeUrl as e:
            assert e.reason == "port", "a non-default port was used on the proxy route: %s %s" % (served_by, e.reason)
        else:
            raise AssertionError("a non-default port was used on the proxy route: %s" % served_by)
        assert all(q.url.port in (None, 80, 443) for _, q in site.requests), "a non-default port was used"


@mutation(SRC, "if default_port_only and not _default_port(parts):", "if False:",
          expect="host_allowed accepted a non-default port")
def test_host_allowed_can_refuse_non_default_ports():
    assert host_allowed("https://cdn.media.test:8443/x", HOSTS)
    for url in ("https://cdn.media.test:8443/x", "https://cdn.media.test:80/x"):
        assert not host_allowed(url, HOSTS, default_port_only=True), "host_allowed accepted a non-default port"
    assert host_allowed("https://cdn.media.test:443/x", HOSTS, default_port_only=True)
    assert host_allowed("http://cdn.media.test:80/x?a=1", HOSTS, https_only=False, default_port_only=True)
    assert not host_allowed("http://cdn.media.test:443/x", HOSTS, https_only=False, default_port_only=True)


@mutation(SRC, '_UNUSABLE = {"unresolvable": "dns", "private_address": "private"}',
          '_UNUSABLE = {"private_address": "private"}', expect="an unresolvable name was not a fetch failure")
@sync
async def test_without_a_proxy_a_name_that_does_not_resolve_is_a_fetch_failure():
    async def nothing(host, port):
        raise OSError("no such host")

    site = MediaSite(b"x")
    try:
        await drain(site, resolver=nothing)
    except UnsafeUrl:
        raise AssertionError("an unresolvable name was not a fetch failure") from None
    except FetchFailed as e:
        assert e.reason == "dns", e.reason
    else:
        raise AssertionError("an unresolvable name was fetched")
    assert site.requests == []


@mutation(SRC, "except (httpx.InvalidURL, ValueError):", "except httpx.InvalidURL:",
          expect="a malformed URL escaped as")
@sync
async def test_a_malformed_link_is_refused_as_unsafe_url_never_as_a_value_error():
    for url in ("https://cdn.media.test/\ud800", "https://cdn.media.test:99999/x", "https://[::1/x", "https://",
                "https://cdn.media.test/a b", "https://evil.example\\.media.test/x", "https://cdn.media.test:ab/x"):
        for proxy in (None, PROXY):
            try:
                await drain(MediaSite(b"v", b"v"), url, proxy=proxy)
            except UnsafeUrl:
                continue
            except Exception as e:  # noqa: BLE001 - the point: nothing else may come out
                raise AssertionError("a malformed URL escaped as %s: %r" % (type(e).__name__, url)) from None
            raise AssertionError("a malformed URL escaped as a download: %r" % url)


@mutation(SRC, "except UnicodeError:", "except ZeroDivisionError:",
          expect="a malformed redirect target escaped as")
@sync
async def test_a_redirect_to_a_malformed_location_is_refused_as_unsafe_url_on_both_routes():
    """httpx builds the next request from the Location even when it is told not to follow: a broken punycode label
    comes out of `send` as a bare idna error."""
    for location in ("https://xn--a-.media.test/b", "https://xn--\u00e9.media.test/b"):
        for proxy in (None, PROXY):
            site = MediaSite(redirect(location), redirect(location))
            try:
                await drain(site, proxy=proxy)
            except UnsafeUrl as e:
                assert e.reason == "malformed", (location, e.reason)
            except Exception as e:  # noqa: BLE001 - the point: nothing else may come out
                raise AssertionError("a malformed redirect target escaped as %s: %r" % (type(e).__name__, location)) \
                    from None
            else:
                raise AssertionError("a malformed redirect target escaped as a download: %r" % location)


# ---- plain http, host characters ---------------------------------------------------------------------------------
@mutation(SRC, "(not https_only or _on(host, http_hosts))", "True",
          expect="plain http was fetched from a host outside http_hosts")
@sync
async def test_plain_http_only_on_the_http_hosts_and_never_by_a_downgrading_redirect():
    kw = {"http_hosts": ("cdn.media.test",), "proxy": None}
    assert await drain(MediaSite(b"video"), "http://cdn.media.test/x", **kw) == b"video"
    assert await drain(MediaSite(b"video"), "https://cdn.other.test/x", **kw) == b"video"
    for url in ("http://cdn.other.test/x", "http://media.test/x", "http://cdn.media.test.evil.example/x"):
        site = MediaSite(b"video")
        try:
            await drain(site, url, **kw)
        except UnsafeUrl as e:
            assert e.reason == "host" and site.requests == [], url
        else:
            raise AssertionError("plain http was fetched from a host outside http_hosts: %s" % url)
    down = MediaSite(lambda r: redirect("http://cdn.other.test/y") if r.url.path == "/x" else b"video")
    with pytest.raises(UnsafeUrl):
        await drain(down, "https://cdn.media.test/x", **kw)
    assert len(down.requests) == 1
    assert not host_allowed("http://cdn.media.test/x", HOSTS) and host_allowed("https://cdn.media.test/x", HOSTS)
    assert host_allowed("http://cdn.media.test/x", HOSTS, http_hosts=("media.test",))
    assert not host_allowed("http://cdn.other.test/x", HOSTS, http_hosts=("media.test",))


@mutation(SRC, "if not _HOST_NAME.fullmatch(host):", "if False:",
          expect="a host with a backslash or percent sign was accepted")
def test_host_names_are_plain_letters_digits_hyphens_and_dots():
    for url in ("https://evil.example\\.media.test/x", "https://evil.example%2f.media.test/x",
                "https://café.media.test/x", "https://a..media.test/x", "https://media.test./x",
                "https://evil.example;.media.test/x", "https://evil.example_.media.test/x"):
        assert not host_allowed(url, HOSTS), "a host with a backslash or percent sign was accepted: %r" % url
    assert host_allowed("https://a-b.media.test/x", HOSTS) and host_allowed("https://127.0.0.1/x", ("127.0.0.1",))


# ---- bodies: encoding, framing, the cap ---------------------------------------------------------------------------
@mutation(SRC, '{"Accept-Encoding": "identity", **headers}', "{**headers}",
          expect="the request did not ask for an identity body")
@sync
async def test_the_request_asks_for_an_identity_body_on_both_routes():
    site = MediaSite(b"video", b"video")
    await drain(site)
    await drain(site, proxy=PROXY)
    asked = [q.headers["accept-encoding"] for _, q in site.requests]
    assert asked == ["identity", "identity"], "the request did not ask for an identity body: %r" % asked


@mutation(SRC, 'not in ("", "identity"):', 'not in ("", "identity", "gzip"):',
          expect="a compressed response was accepted")
@sync
async def test_a_compressed_response_is_refused_so_the_wire_size_never_hides_the_memory_cost():
    squeezed = Answer(body=Body.of(gzip.compress(bytes(MIB))), headers={"content-encoding": "gzip"})
    for proxy in (None, PROXY):
        try:
            await drain(MediaSite(squeezed, squeezed), proxy=proxy)
        except FetchFailed as e:
            assert e.reason == "encoding"
        else:
            raise AssertionError("a compressed response was accepted")
    assert await drain(MediaSite(Answer(body=b"v", headers={"content-encoding": "identity"}))) == b"v"


@mutation(SRC, 'if not expected.isdigit() and "chunked" not in resp.headers.get("transfer-encoding", "").lower():',
          "if False:", expect="a body with no length framing was accepted")
@sync
async def test_a_body_with_neither_content_length_nor_chunked_framing_is_a_failed_route():
    bare = Answer(body=Body.of(GOOD), no_length=True)
    for proxy in (None, PROXY):
        try:
            await drain(MediaSite(bare, bare), proxy=proxy)
        except FetchFailed as e:
            assert e.reason == "no_length", e.reason
        else:
            raise AssertionError("a body with no length framing was accepted")
    chunked = MediaSite(Answer(body=Body.of(GOOD), chunked=True), b"x")
    assert await drain(chunked) == GOOD and chunked.routes == [None]


# ---- the deadline pre-check, logging, close -----------------------------------------------------------------------
@mutation(SRC, "if asyncio.get_running_loop().time() >= deadline:", "if False:",
          expect="a request was made after the deadline")
@sync
async def test_an_expired_deadline_stops_before_any_request():
    site = MediaSite(b"video", b"video")
    try:
        await drain(site, deadline_s=0.0)
    except FetchFailed as e:
        assert e.reason == "timeout"
    else:
        raise AssertionError("a request was made after the deadline")
    assert site.requests == [], "a request was made after the deadline"


@mutation(SRC, 'log.warning("media download failed (%s): %s", e.reason, url_host(url))',
          'log.debug("media download failed (%s): %s", e.reason, url_host(url))',
          expect="the final failure was not logged at WARNING")
@sync
async def test_the_final_failure_is_logged_with_its_reason_and_host_and_nothing_signed(caplog):
    sig, cred = secrets.token_hex(8), secrets.token_hex(6)
    url = "https://cdn.media.test/v/clip.mp4?sig=%s" % sig
    proxy = "http://u%s:p%s@proxy.invalid:8080" % (cred, cred)
    with caplog.at_level(logging.DEBUG, logger="relay.net"):
        with pytest.raises(FetchFailed):
            await drain(MediaSite(403, 500), url, proxy=proxy)
    text = " ".join(r.getMessage() for r in caplog.records if r.name == "relay.net" and r.levelno >= logging.WARNING)
    assert "status" in text and "cdn.media.test" in text, "the final failure was not logged at WARNING: %r" % text
    everything = " ".join(r.getMessage() for r in caplog.records if r.name == "relay.net")
    for secret in (sig, cred, "clip.mp4", "proxy.invalid", "/v/"):
        assert secret not in everything, "the log carries more than the reason and the host: %r" % everything


@mutation(SRC, 'logging.getLogger("httpx").setLevel(logging.WARNING)', "pass",
          expect="a log record carried the path, the query or the pinned address")
@sync
async def test_no_log_record_from_any_logger_carries_a_path_a_query_or_the_pinned_address(caplog):
    """Root logger at INFO and no redactor: the http client's own request line would print all three."""
    def hop(request):
        return redirect("/hop/part2?sig=hopsig") if request.url.path == "/v/clip.mp4" else b"video"

    async def nothing(host, port):
        raise OSError("no such host")

    with caplog.at_level(logging.INFO):
        await drain(MediaSite(hop, hop), proxy=None)                            # direct, with a redirect hop
        await drain(MediaSite(b"x", hop), proxy=PROXY, resolver=nothing)        # through the proxy route
        await net.request("GET", "https://cdn.media.test/p/secret?q=tok", resolver=public_resolver,
                          transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok")))
    text = " ".join(r.getMessage() for r in caplog.records)
    for leaked in ("clip.mp4", "sig=", "/hop/", "hopsig", PUBLIC_IP, "/p/secret", "q=tok", "proxy.invalid"):
        assert leaked not in text, "a log record carried the path, the query or the pinned address (%s): %r" % (
            leaked, text)


@mutation(SRC, "async with asyncio.timeout(max(self.left, 0)):", "async with asyncio.timeout(None):",
          expect="closing the response hung the helper")
@sync
async def test_a_close_that_never_ends_does_not_hang_the_helper(monkeypatch):
    monkeypatch.setattr(net, "CLOSE_TIMEOUT_S", 0.2)
    site = MediaSite(Answer(body=Body(4 * MIB), hang_close=True))
    start = time.monotonic()
    try:
        async with asyncio.timeout(5):
            async with contextlib.aclosing(stream_download(URL, **options(site, proxy=None))) as parts:
                await anext(parts)
    except TimeoutError:
        raise AssertionError("closing the response hung the helper") from None
    assert time.monotonic() - start < 3 and site.closed >= 1


@mutation(SRC, "self.left -= loop.time() - start", "pass", expect="closes were bounded one by one, not in total")
@sync
async def test_all_the_closes_of_one_download_share_one_budget(monkeypatch):
    """Three redirect hops and the final response all close slowly: together they get one budget, not one each."""
    def hop(request):
        n = int(request.url.path.removeprefix("/r") or 0) if request.url.path.startswith("/r") else 0
        if n < 3:
            return Answer(302, headers={"location": "/r%d" % (n + 1)}, hang_close=True)
        return Answer(body=Body(4 * MIB), hang_close=True)

    monkeypatch.setattr(net, "CLOSE_TIMEOUT_S", 0.3)
    site = MediaSite(hop)
    start = time.monotonic()
    try:
        async with asyncio.timeout(10):
            async with contextlib.aclosing(stream_download("https://cdn.media.test/r0",
                                                           **options(site, proxy=None))) as parts:
                await anext(parts)                  # stop early: the body's own end would close it by itself
    except TimeoutError:
        raise AssertionError("closes were bounded one by one, not in total: no end in 10 s") from None
    took = time.monotonic() - start
    assert site.closed >= 4 and took < 0.3 + 0.7, "closes were bounded one by one, not in total: %.2f s" % took
