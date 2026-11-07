"""grok.com over the account's proxy (`engine/web.py`) and the account check (`engine/check.py`)."""

import asyncio
import base64
import contextlib
import logging
import socket
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import WSMessage, WSMsgType
from aiohttp import web as aiohttp_web
from grokkit import (
    EMAIL,
    NOW,
    PROXY,
    SSO,
    USER_ID,
    Ctx,
    Cut,
    FakeSession,
    FakeSocket,
    Resp,
    assert_no_secret,
    fixture,
    names,
    settings,
    ws_refused,
)

from relay_core.providers import Balance, Cooldown, Dead, Health, Proxy, ProxyBad, Success, Suspect, Tier
from relay_core.testing.mutation import mutation
from relay_grok.constants import (
    IMAGINE_WS,
    LIST_CONVERSATIONS_PATH,
    NEW_CONVERSATION_PATH,
    PROXY_REST_S,
    QUOTA_PATH,
    RESPONSES_PATH,
    SESSION_PATH,
    SITE_URL,
    UA,
    UPLOAD_PATH,
    UPLOAD_SOURCE,
)
from relay_grok.engine.check import check_account
from relay_grok.engine.web import NetError, Quota, Refused, SignedOut, Web, proxy_url, real_ws_session

WEB_FILE = "packages/provider-grok/src/relay_grok/engine/web.py"
CHECK_FILE = "packages/provider-grok/src/relay_grok/engine/check.py"
PROXIES = {"http": proxy_url(PROXY), "https": proxy_url(PROXY)}
ASSET = "https://assets.grok.com/users/u/generated/a/generated_video.mp4"
PUBLIC = "https://imagine-public.x.ai/imagine-public/images/a.jpg"
SPIKE_USER = "d0000000-0000-4000-8000-000000000001"


class ProxyError(Exception):
    """What curl_cffi raises when the proxy fails (matched by name); its text holds the proxy's login."""


def reply(name: str) -> Resp:
    """A fixture's answer: its status, headers and JSON body, or its lines (a stream)."""
    f = fixture(name)
    if "body" in f:
        return Resp(f["status"], f.get("headers"), json_body=f["body"])
    return Resp(f["status"], f.get("headers"), lines=f.get("lines"))


def cloudflare() -> Resp:
    return Resp(403, {"content-type": "text/html; charset=UTF-8", "server": "cloudflare"},
                text=fixture("rest_403_cloudflare.html"))


def signed_in(**more) -> Resp:
    body = fixture("session_signed_in")["body"]
    body["session"].update(more)
    return Resp(200, {"content-type": "application/json"}, json_body=body)


def fake(routes: dict | None = None, ctx: Ctx | None = None) -> FakeSession:
    base = {("GET", SESSION_PATH): reply("session_signed_in"), ("POST", QUOTA_PATH): reply("quota_before")}
    return FakeSession({**base, **(routes or {})}, calls=ctx.calls if ctx is not None else None)


def web(session: FakeSession, ctx: Ctx | None = None, **kw) -> Web:
    ctx = ctx or Ctx()
    return Web(ctx.account, ctx.settings, ctx, make_session=session, make_ws_session=session.ws_session, **kw)


async def drain(agen) -> tuple[list, BaseException | None]:
    got = []
    try:
        async for item in agen:
            got.append(item)
    except Exception as e:      # noqa: BLE001 - the test reads what was raised
        return got, e
    return got, None


def check(session: FakeSession, ctx: Ctx | None = None) -> Health:
    ctx = ctx or Ctx()
    return asyncio.run(check_account(ctx, ctx.settings, make_session=session, now=lambda: NOW))


# ---- the proxy, redirects, cookies -----------------------------------------------------------------------------------
PROXIES_LINE = '        self.proxies = {"http": url, "https": url} if url else None'


@mutation(WEB_FILE, PROXIES_LINE, "        self.proxies = None",
          expect="the session must use the account proxy")
@mutation(WEB_FILE, "timeout=timeout, allow_redirects=False,", "timeout=timeout, allow_redirects=True,",
          expect="a call followed a redirect")
@mutation(WEB_FILE, 'proxy=self.proxies["https"] if self.proxies else None,', "proxy=None,",
          expect="the socket must use the account proxy")
def test_every_call_goes_through_the_account_proxy_and_follows_no_redirect():
    s = fake({("POST", UPLOAD_PATH): reply("upload_v2"), ("POST", NEW_CONVERSATION_PATH): Resp(200, lines=["{}"]),
              ("WS", IMAGINE_WS): FakeSocket(), ("GET", LIST_CONVERSATIONS_PATH): Resp(200, json_body={}),
              ("GET", "assets.grok.com/"): Resp(200, content=b"mp4")})

    async def run():
        async with web(s) as w:
            await w.session()
            await w.quota()
            await w.upload(b"img", "image/png", "0.png")
            await drain(w.send_new({"message": "x"}))
            await w.socket()
            await w.read_json("GET", LIST_CONVERSATIONS_PATH + "?pageSize=30")
            await drain(w.get_media(ASSET, 100, 10))
            await w.get_text(SITE_URL + "/")
    asyncio.run(run())
    assert len(s.requests) == 8
    assert s.made == [PROXIES], "the session must use the account proxy"
    for call in s.requests:
        if call.method == "WS":     # aiohttp's: one proxy URL; real_ws_session refuses its redirects
            assert call.proxies == PROXIES["https"], "the socket must use the account proxy"
            continue
        assert call.proxies == PROXIES, "the session must use the account proxy"
        assert call.allow_redirects is False, "a call followed a redirect"


def test_no_proxy_means_direct_and_never_a_fallback():
    s = fake()
    asyncio.run(web(s, Ctx(proxy=None)).session())
    assert s.made == [None] and s.requests[0].proxies is None
    s = fake({("WS", IMAGINE_WS): FakeSocket()})
    asyncio.run(web(s, Ctx(proxy=None)).socket())
    assert s.requests[0].proxies is None

    s = fake({("GET", SESSION_PATH): ProxyError("CONNECT to %s failed" % proxy_url(PROXY))})
    with pytest.raises(NetError) as e:
        asyncio.run(web(s).session())
    assert e.value.proxy is True and str(e.value) == "ProxyError"
    assert len(s.requests) == 1, "a failed proxy was retried"
    assert_no_secret(str(e.value))


@mutation(WEB_FILE, "        return self.jar if cookie_host(url) else None", "        return self.jar",
          expect="a cookie left grok.com")
def test_cookies_go_only_to_grok_and_assets():
    s = fake()

    async def run():
        async with web(s) as w:
            await w.session()
            await w.get_text(SITE_URL + "/")
            await w.get_text(SITE_URL + "/_next/static/chunks/a.js", cookies=False)
            for url in (PUBLIC, "http://grok.com/", "https://grok.com:8443/", "https://grok.com.evil.example/",
                        "https://x.grok.com/"):
                await w.get_text(url)
            await drain(w.get_media(ASSET, 100, 10))
    asyncio.run(run())
    on = [c for c in s.requests if c.has_cookie]
    assert [c.url for c in on] == [SITE_URL + SESSION_PATH, SITE_URL + "/", ASSET], "a cookie left grok.com"
    assert s.requests[0].cookies["sso"] == SSO


@mutation(WEB_FILE, "        if statsig_value:", "        if True:", expect="statsig off must send no header")
def test_statsig_header_only_when_a_generator_gives_a_value():
    seen = []

    def gives(value):
        def gen(method, path):
            seen.append((method, path))
            return value
        return gen

    async def later(method, path):
        return "async-value"

    def header(statsig):
        s = fake()
        asyncio.run(web(s, statsig=statsig).session())
        return s.requests[0].headers.get("x-statsig-id", "absent")

    assert header(None) == "absent", "statsig off must send no header"
    assert header(gives(None)) == "absent", "statsig off must send no header"
    assert header(gives("v1")) == "v1"
    assert header(later) == "async-value"
    assert seen == [("GET", SESSION_PATH), ("GET", SESSION_PATH)]


def test_headers_are_the_pages():
    s = fake()
    asyncio.run(web(s).session())
    h = s.requests[0].headers
    assert h["Origin"] == SITE_URL and h["Referer"] == SITE_URL + "/imagine" and h["Sec-Fetch-Site"] == "same-origin"
    assert len(h["x-xai-request-id"]) == 36


# ---- reads -----------------------------------------------------------------------------------------------------------
def test_quota_reads_both_pools_and_their_refill():
    assert asyncio.run(web(fake()).quota()) == Quota(4, None, 1, None)
    q = asyncio.run(web(fake({("POST", QUOTA_PATH): reply("quota_after_t2v")})).quota())
    assert q == Quota(1, None, 0, datetime(2026, 11, 7, 5, 30, 10, tzinfo=UTC))


def test_signed_out_shapes():
    for routes, call in (
            ({("GET", SESSION_PATH): reply("session_signed_out")}, "session"),
            ({("GET", SESSION_PATH): reply("rest_401_no_credentials")}, "session"),
            ({("POST", QUOTA_PATH): reply("rest_401_no_credentials")}, "quota"),
            ({("POST", QUOTA_PATH): reply("rest_401_bad_credentials")}, "quota"),
            ({("WS", IMAGINE_WS): ws_refused(401)}, "socket")):
        with pytest.raises(SignedOut):
            asyncio.run(getattr(web(fake(routes)), call)())
    with pytest.raises(Refused) as e:
        asyncio.run(web(fake({("WS", IMAGINE_WS): ws_refused(403)})).socket())
    assert (e.value.kind, e.value.status) == ("other", 403)


def test_a_refused_read_names_its_kind():
    for answer, kind in ((reply("rest_403_code7"), "antibot"), (cloudflare(), "cloudflare"),
                         (reply("rest_403_blocked"), "blocked"), (reply("rest_429"), "limit")):
        with pytest.raises(Refused) as e:
            asyncio.run(web(fake({("POST", QUOTA_PATH): answer})).quota())
        assert e.value.kind == kind


def test_read_json_takes_only_the_whole_read_back_paths():
    s = fake({("GET", "/rest/app-chat/"): Resp(200, json_body={"responses": []})})

    async def run():
        w = web(s)
        assert await w.read_json("GET", RESPONSES_PATH % "c0000000-0000-4000-8000-000000000007") == (
            200, {"responses": []})
        assert (await w.read_json("GET", LIST_CONVERSATIONS_PATH + "?pageSize=30&kind=CONVERSATION_KIND_IMAGINE"))[0] \
            == 200
        for method, path in (("GET", NEW_CONVERSATION_PATH), ("POST", LIST_CONVERSATIONS_PATH),
                             ("GET", RESPONSES_PATH % "../new"), ("GET", RESPONSES_PATH % "a/b"),
                             ("GET", LIST_CONVERSATIONS_PATH + "/new"), ("GET", QUOTA_PATH)):
            with pytest.raises(ValueError):
                await w.read_json(method, path)
    asyncio.run(run())
    assert len(s.requests) == 2


# ---- the send steps --------------------------------------------------------------------------------------------------
def test_a_non_200_send_raises_refused_before_any_line():
    for answer, kind in ((reply("send_out_of_quota"), "limit"), (cloudflare(), "cloudflare"),
                         (reply("rest_403_code7"), "antibot"), (reply("rest_401_bad_credentials"), "signed_out"),
                         (reply("rest_400_code3"), "bad_body")):
        got, err = asyncio.run(drain(web(fake({("POST", NEW_CONVERSATION_PATH): answer})).send_new({"m": 1})))
        assert got == [] and isinstance(err, Refused) and err.kind == kind, kind
    s = fake({("POST", NEW_CONVERSATION_PATH): Resp(200, lines=["a", "b", "c"], cut=2)})
    got, err = asyncio.run(drain(web(s).send_new({"m": 1})))
    assert got == ["a", "b"] and isinstance(err, NetError) and str(err) == Cut.__name__
    assert s.requests[0].stream is True and s.requests[0].data == '{"m": 1}'
    assert names(s.calls) == ["POST " + NEW_CONVERSATION_PATH]


def test_the_upload_returns_the_file_id_and_a_terminal_error_is_input():
    s = fake({("POST", UPLOAD_PATH): reply("upload_v2")})
    assert asyncio.run(web(s).upload(b"\x89PNG-bytes", "image/png", 'in"put.png')) == \
        "af000000-0000-4000-8000-000000000001"
    sent = s.requests[0]
    assert sent.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="in_put.png"' in sent.data and b"\x89PNG-bytes" in sent.data
    assert b'name="file_source"\r\n\r\n' + UPLOAD_SOURCE.encode() in sent.data
    with pytest.raises(Refused) as e:
        asyncio.run(web(fake({("POST", UPLOAD_PATH): Resp(200, json_body={"terminalError": {"code": 3}})}))
                    .upload(b"x", "image/png", "0.png"))
    assert (e.value.kind, e.value.status) == ("input", 200)
    with pytest.raises(SignedOut):
        asyncio.run(web(fake({("POST", UPLOAD_PATH): reply("rest_401_no_credentials")})).upload(b"x", "image/png", "a"))


@mutation(WEB_FILE, "        if cookies:\n", "        if False:\n", expect="the socket sent no cookie")
@mutation(WEB_FILE, "        if msg.type == WSMsgType.BINARY:\n", "        if False:\n",
          expect="a binary frame was lost")
@mutation(WEB_FILE, "            await _quiet_close(self.session)\n", "            pass\n",
          expect="the socket's aiohttp session was left open")
def test_the_socket_opens_with_the_cookie_and_wraps_the_frames():
    sock = FakeSocket([{"type": "session"}, WSMessage(WSMsgType.BINARY, b'{"type": "bin"}', None),
                       WSMessage(WSMsgType.CLOSE, 1000, "")])
    s = fake({("WS", IMAGINE_WS): sock})

    async def run():
        ws = await web(s).socket()
        await ws.send_json({"type": "conversation.item.create", "item": {"content": [{"type": "reset"}]}})
        got = [await ws.recv_text()]
        try:
            got.append(await ws.recv_text())
        except NetError:
            pytest.fail("a binary frame was lost")
        with pytest.raises(NetError) as e:
            await ws.recv_text()
        await ws.close()
        return got, e.value
    got, closed = asyncio.run(run())
    assert got == ['{"type": "session"}', '{"type": "bin"}'], "a binary frame was lost"
    assert (str(closed), closed.proxy) == ("WebSocketClose", False)
    call = s.requests[0]
    assert call.url == IMAGINE_WS and "sso=" + SSO in call.headers.get("Cookie", ""), "the socket sent no cookie"
    assert call.headers["Origin"] == SITE_URL and call.headers["User-Agent"] == UA
    assert call.headers["Accept-Language"] == "en-US,en;q=0.9"
    assert call.cookies == {}, "a cookie went to aiohttp's jar"
    assert call.kw["max_msg_size"] >= 64 * 1024 * 1024
    assert sock.closed and s.ws_closed == 1, "the socket's aiohttp session was left open"
    assert names(s.calls) == ["ws open", "reset"]


def test_a_broken_socket_or_an_error_frame_is_a_net_error():
    for frame, kind, proxy in ((ConnectionResetError("gone"), "ConnectionResetError", False),
                               (WSMessage(WSMsgType.ERROR, ProxyError("p"), None), "ProxyError", True),
                               (WSMessage(WSMsgType.CLOSED, None, None), "WebSocketClosed", False)):
        async def run(frame):
            ws = await web(fake({("WS", IMAGINE_WS): FakeSocket([frame])})).socket()
            with pytest.raises(NetError) as e:
                await ws.recv_text()
            return e.value
        err = asyncio.run(run(frame))
        assert (err.kind, err.proxy) == (kind, proxy)


@mutation(WEB_FILE, "                await _quiet_close(self.ws_session)\n", "                pass\n",
          expect="the run left the socket's aiohttp session open")
def test_the_run_closes_the_socket_session_whatever_happened(monkeypatch):
    monkeypatch.setattr("relay_grok.engine.web.WS_OPEN_S", 0.05)
    for answer in (FakeSocket(), ws_refused(500), ConnectionError("no")):
        s = fake({("WS", IMAGINE_WS): answer})

        async def run(s):
            async with web(s) as w:
                with contextlib.suppress(Refused, NetError):
                    await w.socket()       # opened and never closed by the caller, or refused, or no answer
        asyncio.run(run(s))
        assert s.ws_closed >= 1, "the run left the socket's aiohttp session open"


@mutation(WEB_FILE, "max_msg_size=WS_MAX_MSG), WS_OPEN_S)", "max_msg_size=WS_MAX_MSG), None)",
          expect="the socket's open was not cut")
def test_the_socket_open_is_cut_at_ws_open_s(monkeypatch):
    monkeypatch.setattr("relay_grok.engine.web.WS_OPEN_S", 0.05)
    s = fake()

    async def hang(url, **kw):
        await asyncio.Event().wait()
    s.ws_connect = hang

    async def run():
        async with asyncio.timeout(2):
            await web(s).socket()
    try:
        with pytest.raises(NetError) as e:
            asyncio.run(run())
    except TimeoutError:
        pytest.fail("the socket's open was not cut")
    assert (e.value.kind, e.value.proxy) == ("TimeoutError", False)


# ---- the socket on the real aiohttp session (local servers only) ----------------------------------------------------
class LocalWs:
    """`real_ws_session` (its jar, its redirect rule) with the Imagine URL turned to a local server: the cookies and
    headers the engine built for grok.com go there as they are."""

    def __init__(self, url: str):
        self.url, self.s = url, None

    def __call__(self):
        self.s = real_ws_session()
        return self

    async def ws_connect(self, url, **kw):
        assert url == IMAGINE_WS
        return await self.s.ws_connect(self.url, **kw)

    async def close(self):
        await self.s.close()

    def web(self) -> Web:
        return Web(Ctx(proxy=None).account, settings(), make_session=FakeSession(), make_ws_session=self)


@contextlib.asynccontextmanager
async def local_server(routes):
    app = aiohttp_web.Application()
    app.add_routes(routes)
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield runner.addresses[0][1]
    finally:
        await runner.cleanup()


BIG = "x" * (5 * 1024 * 1024)       # more than aiohttp's 4 MB default frame cap


@mutation(WEB_FILE, "                max_msg_size=WS_MAX_MSG), WS_OPEN_S)", "                ), WS_OPEN_S)",
          expect="a big frame was refused")
@mutation(WEB_FILE, "cookie_jar=aiohttp.DummyCookieJar(), ", "", expect="aiohttp kept a cookie")
@mutation(WEB_FILE, "middlewares=(_no_redirect,), ", "", expect="the socket followed a redirect")
def test_the_real_session_takes_big_frames_keeps_no_cookie_and_follows_no_redirect():
    seen = {}

    async def imagine(request):
        seen["cookie"] = request.headers.get("Cookie", "")
        ws = aiohttp_web.WebSocketResponse(max_msg_size=0)
        ws.cookies["sso"] = "rotated"
        await ws.prepare(request)
        await ws.send_str(BIG)
        await ws.receive()
        return ws

    async def moved(request):
        raise aiohttp_web.HTTPFound("/ws/imagine/listen")

    async def signed_out(request):
        return aiohttp_web.Response(status=401)

    async def run():
        async with local_server([aiohttp_web.get("/ws/imagine/listen", imagine),
                                 aiohttp_web.get("/moved", moved), aiohttp_web.get("/out", signed_out)]) as port:
            local = LocalWs("ws://localhost:%d/ws/imagine/listen" % port)
            async with local.web() as w:
                ws = await w.socket()
                try:
                    got = await ws.recv_text()
                except NetError:
                    pytest.fail("a big frame was refused")
                kept = len(local.s.cookie_jar)
                await ws.close()
            assert local.s.closed
            out = []
            for path in ("/moved", "/out"):
                local = LocalWs("ws://localhost:%d%s" % (port, path))
                async with local.web() as w:
                    try:
                        await w.socket()
                        out.append("opened")
                    except (Refused, SignedOut) as e:
                        out.append(e)
            return got, kept, out
    got, kept, (moved_, out) = asyncio.run(run())
    assert len(got) == len(BIG), "a big frame was refused"
    assert "sso=" + SSO in seen["cookie"]
    assert kept == 0, "aiohttp kept a cookie"
    assert isinstance(moved_, Refused) and moved_.status == 302, "the socket followed a redirect"
    assert isinstance(out, SignedOut)


@mutation(WEB_FILE, '_PROXY_ERRORS = ("ProxyError", "ClientProxyConnectionError", "ClientHttpProxyError")',
          '_PROXY_ERRORS = ("ProxyError",)', expect="a failed proxy must be told as the proxy's")
def test_the_real_socket_goes_through_the_account_proxy_with_its_login_and_a_failed_proxy_is_told(caplog):
    connects = []

    async def proxy(reader, writer):
        connects.append((await reader.readuntil(b"\r\n\r\n")).decode())
        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    async def run():
        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        closed_port = free.getsockname()[1]
        free.close()                                            # nothing listens there
        errors = []
        try:
            for p in (port, closed_port):
                ctx = Ctx(proxy=Proxy("127.0.0.1", p, PROXY.auth))
                async with Web(ctx.account, settings(), ctx, make_session=FakeSession()) as w:     # the real WS session
                    with pytest.raises(NetError) as e:
                        await w.socket()
                    errors.append(e.value)
        finally:
            server.close()
            await server.wait_closed()
        return errors
    errors = asyncio.run(run())
    assert len(connects) == 1, "a failed proxy was retried or went direct"
    assert connects[0].startswith("CONNECT grok.com:443 ")
    login = base64.b64encode(PROXY.auth.encode()).decode()
    assert "Proxy-Authorization: Basic " + login in connects[0]
    assert [(e.kind, e.proxy) for e in errors] == [("ClientHttpProxyError", True),
                                                   ("ClientProxyConnectionError", True)], \
        "a failed proxy must be told as the proxy's"
    assert_no_secret(caplog.text, *(str(e) for e in errors))


def test_get_media_refuses_a_foreign_host_before_any_request():
    s = fake({("GET", "assets.grok.com/"): Resp(200, content=b"x" * 50)})

    async def run():
        w = web(s)
        for url in ("https://evil.example/a.mp4", "http://assets.grok.com/a", "https://grok.com/a", PUBLIC,
                    "https://assets.grok.com:444/a", "https://u@assets.grok.com/a"):
            _, err = await drain(w.get_media(url, 100, 10))
            assert isinstance(err, ValueError), url
        assert s.requests == []
        assert await drain(w.get_media(ASSET, 100, 10)) == ([b"x" * 50], None)
        _, err = await drain(w.get_media(ASSET, 10, 10))
        assert isinstance(err, ValueError)
    asyncio.run(run())
    assert s.requests[0].has_cookie and s.requests[0].timeout == 10
    for status in (403, 404):
        _, err = asyncio.run(drain(web(fake({("GET", "assets.grok.com/"): Resp(status)})).get_media(ASSET, 9, 9)))
        assert isinstance(err, Refused) and (err.kind, err.status) == ("gone", status)
    _, err = asyncio.run(drain(web(fake({("GET", "assets.grok.com/"): Resp(500)})).get_media(ASSET, 9, 9)))
    assert isinstance(err, Refused) and err.kind == "other"


def test_a_set_sso_is_an_event_with_no_value_and_nothing_written_back():
    ctx = Ctx()
    rotated = Resp(200, json_body=fixture("session_signed_in")["body"], cookies={"sso": "sso-NEW-1", "__cf_bm": "b"})
    s = fake({("GET", SESSION_PATH): rotated,
              ("POST", QUOTA_PATH): Resp(200, json_body=fixture("quota_before")["body"], cookies={"sso-rw": "rw-2"})},
             ctx)

    async def run():
        async with web(s, ctx) as w:
            await w.session()
            await w.quota()
    asyncio.run(run())
    assert [e[0] for e in ctx.events] == ["grok set sso (sso)"]
    assert "renew" not in names(ctx.calls)
    assert all("NEW" not in str(c) for c in ctx.calls)

    # grok echoing the account's own value is no rotation
    ctx = Ctx()
    asyncio.run(web(fake({("GET", SESSION_PATH): Resp(200, json_body={"status": "authenticated", "session": {}},
                                                      cookies={"sso": SSO})}, ctx), ctx).session())
    assert ctx.events == []


# ---- the account check -----------------------------------------------------------------------------------------------
def test_a_signed_in_check_is_alive_with_the_user_id_free_tier_and_two_balances():
    health = check(fake())
    assert health.state == "alive" and health.identity == SPIKE_USER
    assert health.display_name == "grok.test@example.invalid"
    assert health.effects == (Tier("free"), Balance("image", 4, None, window="quota"),
                              Balance("video", 1, None, window="quota"), Success())
    health = check(fake({("POST", QUOTA_PATH): reply("quota_after_t2v")}))
    assert health.effects[2] == Balance("video", 0, datetime(2026, 11, 7, 5, 30, 10, tzinfo=UTC), window="quota")


def test_an_empty_pool_with_no_refill_rests_limit_rest_s():
    body = fixture("quota_after_t2v")["body"]
    del body["video720p"]["nextAvailableAt"]
    body["imagePro"] = None
    health = check(fake({("POST", QUOTA_PATH): Resp(200, json_body=body)}))
    assert health.effects == (Tier("free"), Balance("video", 0, NOW + timedelta(seconds=86400), window="quota"),
                              Success())


def test_a_check_without_email_shows_the_given_name():
    assert check(fake({("GET", SESSION_PATH): signed_in(email="")})).display_name == "Test"


@mutation(CHECK_FILE, '    return (Suspect(why, min_gap_s=settings.get("grok.signed_out_gap_s")),',
          "    return (Suspect(why),", expect="a signed-out read must be a spaced strike")
def test_a_signed_out_check_is_a_spaced_strike_never_dead_at_once():
    for routes in ({("GET", SESSION_PATH): reply("session_signed_out")},
                   {("POST", QUOTA_PATH): reply("rest_401_bad_credentials")}):
        health = check(fake(routes))
        assert health.state == "unknown" and not any(isinstance(e, Dead) for e in health.effects)
        strike, rest = health.effects
        assert isinstance(strike, Suspect) and strike.min_gap_s > 0, "a signed-out read must be a spaced strike"
        assert isinstance(rest, Cooldown) and rest.recheck and rest.until > NOW


@mutation(CHECK_FILE, '    return (Suspect(why, min_gap_s=settings.get("grok.signed_out_gap_s")),',
          "    return (Suspect(why, min_gap_s=540),", expect="the strike spacing is grok.signed_out_gap_s")
@mutation(CHECK_FILE, 'now + timedelta(seconds=settings.get("grok.signed_out_rest_s"))',
          "now + timedelta(seconds=600)", expect="the rest is grok.signed_out_rest_s")
def test_the_signed_out_rest_and_strike_spacing_are_settings():
    ctx = Ctx(settings_=settings(signed_out_rest_s=1200, signed_out_gap_s=100))
    strike, rest = check(fake({("GET", SESSION_PATH): reply("session_signed_out")}), ctx).effects
    assert strike.min_gap_s == 100, "the strike spacing is grok.signed_out_gap_s"
    assert rest.until == NOW + timedelta(seconds=1200), "the rest is grok.signed_out_rest_s"
    strike, rest = check(fake({("GET", SESSION_PATH): reply("session_signed_out")})).effects
    assert (strike.min_gap_s, rest.until) == (540, NOW + timedelta(seconds=600)), "the defaults are 540 s and 600 s"


def test_a_blocked_account_is_a_strike_named_blocked():
    health = check(fake({("GET", SESSION_PATH): reply("rest_403_blocked")}))
    strike, rest = health.effects
    assert isinstance(strike, Suspect) and strike.reason.startswith("blocked") and strike.min_gap_s > 0
    assert rest.recheck


def test_cloudflare_or_antibot_in_the_check_rests_without_a_strike():
    for answer, rest_s in ((cloudflare(), 600), (reply("rest_403_code7"), 1800)):
        health = check(fake({("POST", QUOTA_PATH): answer}))
        assert health.state == "unknown" and len(health.effects) == 1
        (rest,) = health.effects
        assert isinstance(rest, Cooldown) and rest.recheck and rest.until == NOW + timedelta(seconds=rest_s)


def test_a_failed_proxy_in_the_check_marks_the_proxy_and_rests():
    health = check(fake({("GET", SESSION_PATH): ProxyError("x")}))
    bad, rest = health.effects
    assert isinstance(bad, ProxyBad) and rest == Cooldown("the proxy failed", NOW + timedelta(seconds=PROXY_REST_S))
    assert check(fake({("GET", SESSION_PATH): TimeoutError()})) == Health("unknown")


def test_a_paid_subscription_still_checks_as_free(caplog):
    caplog.set_level(logging.INFO)
    health = check(fake({("GET", SESSION_PATH): signed_in(xSubscriptionType="SuperGrok")}))
    assert health.state == "alive" and health.effects[0] == Tier("free")
    assert "'SuperGrok' checked as free" in caplog.text


def test_the_check_spends_nothing():
    ctx = Ctx()
    check(fake(ctx=ctx), ctx)
    assert names(ctx.calls) == ["GET " + SESSION_PATH, "POST " + QUOTA_PATH]


def test_no_cookie_user_id_email_or_proxy_login_reaches_a_log_event_or_error(caplog):
    caplog.set_level(logging.DEBUG)
    texts = []
    planted = signed_in(userId="other-" + USER_ID, email=EMAIL, xSubscriptionType="SuperGrok")
    leak = "sso=%s proxy %s user %s" % (SSO, proxy_url(PROXY), USER_ID)
    for routes in ({("GET", SESSION_PATH): planted},
                   {("GET", SESSION_PATH): ProxyError(leak)},
                   {("GET", SESSION_PATH): RuntimeError(leak)},
                   {("GET", SESSION_PATH): Resp(401, json_body={"code": 16, "message": leak})},
                   {("POST", QUOTA_PATH): Resp(403, json_body={"error": {"code": 7, "message": leak}})},
                   {("POST", QUOTA_PATH): Resp(403, json_body={"error": {"code": 13,
                                                                         "message": "blocked-user " + leak}})}):
        ctx = Ctx()
        health = check(fake(routes, ctx), ctx)
        texts += [repr(health.effects), *(e[0] for e in ctx.events)]
    for routes, call in (({("GET", SESSION_PATH): ProxyError(leak)}, "session"),
                         ({("POST", QUOTA_PATH): Resp(429, json_body={"error": {"message": leak}})}, "quota"),
                         ({("WS", IMAGINE_WS): Exception(leak)}, "socket")):
        with pytest.raises(Exception) as e:
            asyncio.run(getattr(web(fake(routes)), call)())
        texts.append(str(e.value))
    assert_no_secret(caplog.text, *texts)
