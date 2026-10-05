"""The local proxy forwarder (contracts §6.1; coverage BSES04, BSES05, LSUB00 forwarder part, E02).

Pure tests drive it with sockets; the browser tests send a real Chrome through it to a stand-in upstream proxy."""

import asyncio
import base64
import time

import pytest
from browserkit import Account, Proxy, redact, site_for, sync

from relay_browser import forwarder as fwdmod
from relay_browser.forwarder import ProxyForwarder, _status, _with_auth, proxy_settings, target_host
from relay_browser.session import open_session
from relay_core.testing.mutation import mutation

F = "packages/relay-browser/src/relay_browser/forwarder.py"
AUTH = b"Proxy-Authorization: Basic eDp5"
UNREACHABLE = ("                cw.write(_BAD_GATEWAY)\n                await cw.drain()\n                return\n"
               "            self._writers.add(uw)")
CONNECT = b"CONNECT www.example.test:443 HTTP/1.1\r\nHost: www.example.test:443\r\n\r\n"


def upstream_settings(port: int) -> dict:
    return {"server": "http://127.0.0.1:%d" % port, "username": "u", "password": "p"}


# ---- the request head sent on to the real proxy ----------------------------------------------------------------------
@mutation(F, 'drop = (b"proxy-authorization:",) if tunnel else', 'drop = () if tunnel else',
          expect="exactly our password")
def test_the_tunnel_head_carries_exactly_our_password():
    head = _with_auth(b"CONNECT www.example.test:443 HTTP/1.1\r\nHost: www.example.test:443\r\n"
                      b"Proxy-Authorization: Basic b2xk\r\n\r\n", AUTH)
    assert head.startswith(b"CONNECT www.example.test:443 HTTP/1.1\r\n") and head.endswith(b"\r\n\r\n")
    assert head.count(b"Proxy-Authorization") == 1 and AUTH in head, "the tunnel does not carry exactly our password"


@mutation(F, "    if not tunnel:\n        rest.append(b\"Connection: close\")", "    if not tunnel:\n        pass",
          expect="plain HTTP connection is kept open")
def test_a_plain_http_connection_is_forced_to_close():
    plain = _with_auth(b"GET http://a.test/ HTTP/1.1\r\nHost: a.test\r\nProxy-Connection: keep-alive\r\n\r\n", AUTH)
    assert b"Connection: close" in plain and b"keep-alive" not in plain, \
        "a plain HTTP connection is kept open, and its next request would reach the proxy without a password"


# ---- a proxy as launch settings (LSUB00) -----------------------------------------------------------------------------
@mutation(F, 'user, _, password = proxy.auth.partition(":")', 'user, _, password = proxy.auth.rpartition(":")',
          expect="password with a colon")
def test_a_proxy_becomes_the_launch_settings():
    assert proxy_settings(None) is None
    assert proxy_settings(Proxy("1.2.3.4", 8080, "u:pw")) == {"server": "http://1.2.3.4:8080", "username": "u",
                                                              "password": "pw"}
    assert proxy_settings(Proxy("9.9.9.9", 3128)) == {"server": "http://9.9.9.9:3128"}
    assert proxy_settings(Proxy("1.2.3.4", 80, "u:pa:ss"))["password"] == "pa:ss", "password with a colon is cut"
    assert proxy_settings(Proxy("::1", 8080))["server"] == "http://[::1]:8080"


# ---- real forwarding over sockets ------------------------------------------------------------------------------------
async def connect_status(upstream_reply: bytes | None, silent: bool = False, refuse: tuple = (),
                         head: bytes = CONNECT) -> tuple[int, int]:
    """(the status Chrome would see, how many requests reached the real proxy) when the real proxy answers
    `upstream_reply` (None: it cannot be reached; `silent`: it reads the head and then says nothing at all)."""
    up, port, reached = None, 9, []              # nothing listens on the discard port: connection refused
    if upstream_reply is not None or silent:
        async def upstream(r, w):
            reached.append(await r.readuntil(b"\r\n\r\n"))
            if silent:
                await asyncio.sleep(30)
            w.write(upstream_reply)
            await w.drain()
            w.close()
        up = await asyncio.start_server(upstream, "127.0.0.1", 0)
        port = up.sockets[0].getsockname()[1]
    fwd = ProxyForwarder(upstream_settings(port), refuse)
    local = await fwd.start()
    r, w = await asyncio.open_connection("127.0.0.1", int(local["server"].rsplit(":", 1)[1]))
    w.write(head)
    try:
        got = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
    except (TimeoutError, asyncio.IncompleteReadError):
        got = b""
    finally:
        w.close()
        fwd.close()
        if up is not None:
            up.close()
    return _status(got), len(reached)


@sync
async def test_a_tunnel_round_trip_logs_in_to_the_upstream_and_carries_bytes_both_ways():
    seen = {}

    async def upstream(r, w):
        seen["head"] = await r.readuntil(b"\r\n\r\n")
        w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        w.write(await r.read(4))                    # echoes four bytes
        await w.drain()
        w.close()
    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    fwd = ProxyForwarder(upstream_settings(up.sockets[0].getsockname()[1]))
    local = await fwd.start()
    r, w = await asyncio.open_connection("127.0.0.1", int(local["server"].rsplit(":", 1)[1]))
    w.write(CONNECT)
    status = await r.readuntil(b"\r\n\r\n")
    w.write(b"ping")
    await w.drain()
    echo = await r.read(4)
    w.close()
    fwd.close()
    up.close()
    assert b"Proxy-Authorization: Basic " + base64.b64encode(b"u:p") in seen["head"], \
        "the forwarder does not log in to the account proxy"
    assert status.startswith(b"HTTP/1.1 200") and echo == b"ping", "the tunnel does not carry bytes both ways"


@mutation(F, "if not reply or _status(reply) == 407:", "if not reply:", expect="login prompt")
@sync
async def test_an_upstream_407_reaches_chrome_as_502():
    assert (await connect_status(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n"))[0] == 502, \
        "a rejected proxy password reaches Chrome as 407: a login prompt and a page that hangs, not a dead proxy"


@mutation(F, UNREACHABLE, "                return\n            self._writers.add(uw)", expect="leaves Chrome waiting")
@sync
async def test_an_unreachable_upstream_reaches_chrome_as_502():
    t0 = time.monotonic()
    status, _ = await connect_status(None)
    assert status == 502, "an unreachable account proxy leaves Chrome waiting"
    assert time.monotonic() - t0 < 5


@mutation(F, "if not reply or _status(reply) == 407:", "if _status(reply) == 407:",
          expect="answers Chrome with nothing")
@sync
async def test_an_upstream_that_stays_silent_or_closes_without_a_reply_reaches_chrome_as_502(monkeypatch):
    monkeypatch.setattr(fwdmod, "REPLY_TIMEOUT_S", 0.3)
    t0 = time.monotonic()
    status, reached = await connect_status(None, silent=True)
    assert reached == 1 and status == 502, "a silent upstream answers Chrome with nothing (status %s)" % status
    assert time.monotonic() - t0 < 4, "the wait for a silent upstream is not bounded"
    status, reached = await connect_status(b"")            # reads the head, then closes
    assert reached == 1 and status == 502, "an upstream that closes without a reply answers Chrome with nothing"


@mutation(F, "            if _refused(target_host(head), self._refuse):", "            if False:",
          expect="refused host reached the account proxy")
@sync
async def test_a_refused_host_gets_a_502_and_never_reaches_the_account_proxy():
    ok = b"HTTP/1.1 200 Connection established\r\n\r\n"
    refuse = ("accounts.google.com", "update.googleapis.com")
    for host, refused in (("accounts.google.com:443", True), ("UPDATE.googleapis.com:443", True),
                          ("x.accounts.google.com:443", True), ("www.google.com:443", False),
                          ("notaccounts.google.com:443", False)):
        head = b"CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n" % (host.encode(), host.encode())
        status, reached = await connect_status(ok, refuse=refuse, head=head)
        assert (status, reached) == ((502, 0) if refused else (200, 1)), \
            "refused host reached the account proxy, or an ordinary host was refused: %s -> %s" % (
                host, (status, reached))
    plain = b"GET http://clients2.google.com/time/1/current?x=1 HTTP/1.1\r\nHost: clients2.google.com\r\n\r\n"
    assert await connect_status(ok, refuse=("clients2.google.com",), head=plain) == (502, 0), \
        "refused host reached the account proxy (plain HTTP)"


def test_the_host_a_request_head_asks_for():
    assert target_host(CONNECT) == "www.example.test"
    assert target_host(b"GET http://A.test:8080/x?y=1 HTTP/1.1\r\n\r\n") == "a.test"
    assert target_host(b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n") == "::1"
    assert target_host(b"garbage") == "" and target_host(b"") == ""


def test_a_proxy_without_a_login_gets_no_login_line():
    plain = b"CONNECT a.test:443 HTTP/1.1\r\nProxy-Authorization: Basic b2xk\r\n\r\n"
    assert b"Proxy-Authorization" not in _with_auth(plain, None), "the client's own login line was passed on"
    assert ProxyForwarder({"server": "http://127.0.0.1:1"})._auth is None


# ---- Chrome through the forwarder (browser tier) ---------------------------------------------------------------------
class FakeUpstreamProxy:
    """A plain-HTTP 'proxy': answers every request itself and records the head it was sent."""

    def __init__(self, status: int = 200):
        self.status, self.heads, self._server, self.port = status, [], None, 0

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()

    async def _serve(self, r, w):
        try:
            self.heads.append((await r.readuntil(b"\r\n\r\n")).decode("latin-1"))
            body = b"through-the-proxy"
            reason = {200: "OK", 407: "Proxy Authentication Required"}[self.status]
            w.write(b"HTTP/1.1 %d %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                    % (self.status, reason.encode(), len(body), body))
            await w.drain()
        finally:
            w.close()


@pytest.mark.browser
@sync
async def test_chrome_reaches_a_site_through_the_forwarder_and_the_upstream_sees_our_login(tmp_path, token):
    async with FakeUpstreamProxy() as up:
        account = Account(proxy=Proxy("127.0.0.1", up.port, "proxyuser:proxypass"))
        async with open_session(site_for(tmp_path), account, purpose="submit", token=token, redact=redact) as s:
            # a host name Chrome cannot resolve itself: only the proxy can answer it, and Chrome never bypasses it
            resp = await s.page.goto("http://relay-proxy-test.invalid/", timeout=30000)
            assert resp.status == 200 and "through-the-proxy" in await s.page.content()
    # Chrome may send a request of its own first (a connection to the search provider): ours is the one for the page
    head = next(h for h in up.heads if "relay-proxy-test.invalid" in h)
    expected = "Proxy-Authorization: Basic " + base64.b64encode(b"proxyuser:proxypass").decode()
    assert expected in head, "Chrome's request reached the upstream proxy without the account's login"
    assert "Connection: close" in head


@pytest.mark.browser
@sync
async def test_a_rejected_proxy_login_fails_the_page_at_once_instead_of_hanging(tmp_path, token):
    async with FakeUpstreamProxy(status=407) as up:
        account = Account(proxy=Proxy("127.0.0.1", up.port, "proxyuser:wrong"))
        async with open_session(site_for(tmp_path), account, purpose="submit", token=token, redact=redact) as s:
            t0 = time.monotonic()
            # an empty 502 is a failed navigation for Chrome; a passed-through 407 would hang until the timeout
            with pytest.raises(Exception, match="ERR_HTTP_RESPONSE_CODE_FAILURE"):
                await s.page.goto("http://relay-proxy-test.invalid/", timeout=30000)
            assert time.monotonic() - t0 < 20, "a rejected proxy login hung the page"
    assert up.heads, "the request never reached the upstream proxy"
