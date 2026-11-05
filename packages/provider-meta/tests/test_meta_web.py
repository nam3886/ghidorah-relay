"""One run's HTTP to meta.ai: the recorded pages read, the `rd` pass, the proxy on every call, the cookie-less upload,
the page gate per proxy IP, the token cache, and the cookie write-back guarded by the page's viewer."""

import asyncio
import logging
from dataclasses import replace

import pytest
from metakit import (
    NAME,
    PROXY,
    SESSION,
    TOKEN,
    VIEWER,
    Clock,
    Ctx,
    FakeSocket,
    Net,
    Resp,
    assert_no_secret,
    jar,
    page,
    settings,
)

from relay_core.testing.mutation import mutation
from relay_meta.constants import GATEWAY_QUERY, GATEWAY_URL, SITE_URL, UPLOAD_URL
from relay_meta.credential import jar_of, viewer_hash
from relay_meta.engine import web as W

WEB_FILE = "packages/provider-meta/src/relay_meta/engine/web.py"
HOME = (("GET", SITE_URL + "/"))
RD_POST = ("POST", SITE_URL + "/__rd_verify")
PROXIES = {"http": W.proxy_url(PROXY), "https": W.proxy_url(PROXY)}
OTHER_PROXY = replace(PROXY, host="10.0.0.10")


def run(coro):
    return asyncio.run(coro)


def signed_in(**cookies):
    return Resp(200, page("page_signed_in"), cookies=cookies)


def web(net, ctx=None, sessions=None, *, clock=None, account=None, **over):
    ctx = ctx or Ctx()
    sessions = sessions or W.Sessions(clock or Clock())
    return W.Web(account or ctx.account, settings(**over), sessions, ctx, make_session=net.make_session)


async def read(w, path="/"):
    async with w:
        return await w.page(path)


# ---- the pages ---------------------------------------------------------------------------------------------------
def test_the_saved_pages_read_as_signed_in_signed_out_rd_and_vercel():
    p = W.read_page(200, page("page_signed_in"))
    assert (p.state, p.token, p.viewer, p.name) == ("in", TOKEN, VIEWER, NAME)
    assert p.token.startswith("ecto1:") and len(p.viewer) == 16
    assert W.read_page(200, page("page_signed_out")).state == "out"
    assert W.read_page(403, page("page_rd_403")).state == "unreadable"
    assert W.read_page(429, page("page_vercel")).state == "vercel"
    assert W.read_page(500, page("page_signed_in")).state == "unreadable", "a signed-in page needs a 200"
    assert W.read_page(200, "").state == "unreadable"
    assert_no_secret(repr(p), str(p), repr(W.Held(TOKEN, VIEWER, 1.0)))


@mutation(WEB_FILE, '        if solve and resp.status_code == 403 and RD_MARK in (resp.text or ""):',
          '        if resp.status_code == 403 and RD_MARK in (resp.text or ""):', expect="never posts")
def test_the_rd_challenge_is_passed_once_then_the_page_is_read():
    rd = Resp(403, page("page_rd_403"))
    net = Net({HOME: [rd, signed_in()], RD_POST: Resp(200, cookies={"rd_challenge": "rd-1"})})
    p = run(read(web(net)))
    assert p.state == "in" and net.methods() == ["GET", "POST", "GET"]
    assert net.calls[1].url.startswith(SITE_URL + "/__rd_verify_") and "?challenge=" in net.calls[1].url
    assert net.calls[2].cookies.get("rd_challenge") == "rd-1", "the pass rides the second GET"

    net = Net({HOME: Resp(403, page("page_rd_403")), RD_POST: Resp(200)})
    assert run(read(web(net))).state == "unreadable"
    assert net.methods() == ["GET", "POST", "GET"], "the rd page is passed once, never twice"

    net = Net({HOME: [rd, signed_in()], RD_POST: Resp(200)})
    state = run(web(net).page(solve=False)).state
    assert (state, net.methods()) == ("unreadable", ["GET"]), "solve=False never posts the challenge"


# ---- the proxy, no redirect -------------------------------------------------------------------------------------
def test_every_call_goes_through_the_proxy_and_follows_no_redirect():
    sock = FakeSocket([b"\x0a"])
    net = Net({HOME: signed_in(), ("POST", UPLOAD_URL): Resp(200, json_body={"media_id": "1234567890123456"}),
               ("WS", GATEWAY_URL): sock})

    async def go(w):
        async with w:
            await w.page()
            await w.upload(TOKEN, b"img", "image/webp", "a.webp")
            s = await w.socket(TOKEN)
            await s.send(b"\x0f")
            assert await s.recv() == b"\x0a"
            await s.close()
    run(go(web(net)))
    assert net.methods() == ["GET", "POST", "WS"]
    for call in net.calls:
        assert call.proxies == PROXIES and call.allow_redirects is False, call.method
    assert sock.sent == [b"\x0f"] and sock.closed

    net = Net({HOME: ConnectionError("down")})
    with pytest.raises(W.NetError) as e:
        run(read(web(net, Ctx(proxy=None))))
    assert e.value.proxy is False and len(net.calls) == 1, "no retry"
    assert net.calls[0].proxies is None and net.calls[0].allow_redirects is False

    class ProxyError(Exception):
        pass
    net = Net({HOME: ProxyError("http://user:PLANTED-pw@10.0.0.9:3128 refused")})
    with pytest.raises(W.NetError) as e:
        run(read(web(net)))
    assert e.value.proxy is True and len(net.calls) == 1, "a failed proxy is never retried direct"


def test_the_socket_carries_the_token_and_the_gateway_query_on_the_jar_session():
    sock = FakeSocket()
    net = Net({("WS", GATEWAY_URL): sock})

    async def go(w):
        async with w:
            await w.socket(TOKEN)
    run(go(web(net)))
    from urllib.parse import parse_qs, urlsplit
    call = net.calls[0]
    q = {k: v[0] for k, v in parse_qs(urlsplit(call.url).query).items()}
    assert q["Authorization"] == TOKEN and {k: q[k] for k in GATEWAY_QUERY} == GATEWAY_QUERY
    assert len(q["x-dgw-app-clippy-request-id"]) == 36 and call.headers["Origin"] == SITE_URL
    assert call.cookies.get("ecto_1_sess") == SESSION, "the socket opens on the jar session (as measured)"


@mutation(WEB_FILE, "        bare = self.make_session(self.proxies)", "        bare = self.s",
          expect="the upload carried a cookie")
def test_the_upload_carries_no_cookie_and_returns_the_media_id():
    net = Net({("POST", UPLOAD_URL): Resp(200, json_body={"media_id": "1234567890123456"})})

    async def go(w):
        async with w:
            return await w.upload(TOKEN, b"img-bytes", "image/png", "in.png")
    assert run(go(web(net))) == "1234567890123456"
    call = net.calls[0]
    assert call.cookies == {} and "Cookie" not in call.headers, "the upload carried a cookie"
    assert call.session is not net.sessions[0] and call.url.startswith(UPLOAD_URL)
    assert call.headers["Authorization"] == "OAuth " + TOKEN and call.data == b"img-bytes"
    assert call.headers["Content-Type"] == "image/png" and call.headers["x-entity-length"] == "9"
    assert net.sessions[1].closed

    for answer in (Resp(500, "oops"), Resp(200, json_body={"media_id": "abc"}), Resp(200, "not json"),
                   Resp(200, json_body={"media_id": None}), Resp(200, json_body=[1])):
        with pytest.raises(W.UploadFailed):
            run(go(web(Net({("POST", UPLOAD_URL): answer}))))
    assert run(go(web(Net({("POST", UPLOAD_URL): Resp(200, json_body={"media_id": 42})})))) == "42"


# ---- the page gate per proxy IP -----------------------------------------------------------------------------------
@mutation(WEB_FILE, "        if last is not None and now - last < gap_s:",
          "        if last is not None and now - last <= 0:", expect="inside the gap")
def test_a_second_page_load_on_one_ip_inside_the_gap_is_refused_before_any_request():
    clock = Clock()
    sessions = W.Sessions(clock)
    net = Net({HOME: signed_in()})
    assert run(read(web(net, Ctx(), sessions))).state == "in"
    clock.advance(299)
    before = len(net.calls)
    try:
        run(read(web(net, Ctx(account_id="acc-2"), sessions)))
        closed = None
    except W.GateClosed as e:
        closed = e
    assert closed is not None and closed.marked is False and closed.until == clock.start + 300, "inside the gap"
    assert len(net.calls) == before, "refused before any request"
    assert run(read(web(net, Ctx(proxy=OTHER_PROXY), sessions))).state == "in", "another IP has its own gate"
    clock.advance(1)
    assert run(read(web(net, Ctx(account_id="acc-2"), sessions))).state == "in"


def test_direct_accounts_share_one_gate():
    sessions = W.Sessions(Clock())
    net = Net({HOME: signed_in()})
    assert W.ip_key(None) == "direct" and W.ip_key(PROXY) == "10.0.0.9:3128"
    run(read(web(net, Ctx(proxy=None), sessions)))
    with pytest.raises(W.GateClosed):
        run(read(web(net, Ctx(proxy=None, account_id="acc-2"), sessions)))
    assert len(net.calls) == 1


def test_a_vercel_page_marks_the_ip_and_a_repeat_within_a_day_rests_longer():
    clock = Clock()
    sessions = W.Sessions(clock)
    net = Net({HOME: Resp(429, page("page_vercel"))})
    ip = W.ip_key(PROXY)
    rests = []
    for _ in range(3):
        assert run(read(web(net, Ctx(), sessions, checkpoint_rest_max_s=1500))).state == "vercel"
        rests.append(sessions.gate.until(ip) - clock())
        calls = len(net.calls)
        with pytest.raises(W.GateClosed) as e:
            run(read(web(net, Ctx(account_id="acc-2"), sessions)))
        assert e.value.marked is True and len(net.calls) == calls
        clock.advance(rests[-1])
        assert sessions.gate.until(ip) is None
    assert rests == [600, 1200, 1500]


# ---- the token cache --------------------------------------------------------------------------------------------
def test_a_cached_token_is_kept_per_credential_gen_and_forgotten_on_request():
    clock = Clock()
    s = W.Sessions(clock)
    p = W.read_page(200, page("page_signed_in"))
    assert s.token("acc-1", 1) is None
    s.keep("acc-1", 1, W.read_page(200, page("page_signed_out")))
    assert s.token("acc-1", 1) is None, "only a signed-in page gives a token"
    s.keep("acc-1", 1, p)
    held = s.token("acc-1", 1)
    assert (held.token, held.viewer, held.read_at) == (TOKEN, VIEWER, clock())
    assert s.token("acc-1", 2) is None, "a re-import drops the token"
    assert s.token("acc-2", 1) is None
    clock.advance(3599)
    assert not s.stale(held, 3600)
    clock.advance(1)
    assert s.stale(held, 3600) and s.token("acc-1", 1) == held, "an old token is still handed out"
    s.forget("acc-1")
    assert s.token("acc-1", 1) is None
    assert s.lock("acc-1") is s.lock("acc-1") and s.lock("acc-1") is not s.lock("acc-2")


# ---- the cookie write-back ---------------------------------------------------------------------------------------
def rotated():
    return {"ecto_1_sess": "ecto-new-2", "rd_challenge": "rd-new"}


@mutation(WEB_FILE,
          '        own = page.state == "in" and viewer_hash(page.viewer) == self.account.provider_state.get("viewer")',
          '        own = page.state == "in"', expect="another viewer")
def test_rotated_cookies_are_written_back_only_after_a_signed_in_page_of_the_same_viewer():
    ctx = Ctx()
    run(read(web(Net({HOME: signed_in(**rotated())}), ctx)))
    assert len(ctx.renewed) == 1
    stored = jar_of(ctx.stored)
    assert stored["ecto_1_sess"] == "ecto-new-2" and stored["rd_challenge"] == "rd-new"
    assert stored["_vcrcs"] == "vcrcs-1"

    ctx = Ctx(state={"viewer": viewer_hash("9999999999999999")})
    run(read(web(Net({HOME: signed_in(**rotated())}), ctx)))
    assert ctx.renewed == [], "another viewer"
    for state in ({}, {"viewer": None}):
        ctx = Ctx(state=state)
        run(read(web(Net({HOME: signed_in(**rotated())}), ctx)))
        assert ctx.renewed == [], "an account never checked over HTTP"
    for answer in (Resp(200, page("page_signed_out"), cookies=rotated()),
                   Resp(429, page("page_vercel"), cookies=rotated()),
                   Resp(500, "", cookies=rotated())):
        ctx = Ctx()
        run(read(web(Net({HOME: answer}), ctx)))
        assert ctx.renewed == [], answer.status_code

    # a signed-in page, then a signed-out one in the same run: nothing
    clock, ctx = Clock(), Ctx()
    net = Net({HOME: [signed_in(**rotated()), Resp(200, page("page_signed_out"))]})

    async def twice(w):
        async with w:
            await w.page()
            clock.advance(300)
            await w.page()
    run(twice(web(net, ctx, clock=clock)))
    assert ctx.renewed == []

    # no page read: nothing (the socket's own cookies are never proof)
    ctx = Ctx()
    net = Net({("WS", GATEWAY_URL): FakeSocket()})

    async def socket_only(w):
        async with w:
            w.s.cookies.set("ecto_1_sess", "ecto-new-2", ".meta.ai")
            await w.socket(TOKEN)
    run(socket_only(web(net, ctx)))
    assert ctx.renewed == []


def test_a_cookie_another_run_rotated_meanwhile_is_not_overwritten():
    ctx = Ctx()
    ctx.stored = jar(("rd_challenge", "rd-old")).replace(SESSION.encode(), b"ecto-by-another-run")
    ctx.account = replace(ctx.account, credential=jar(("rd_challenge", "rd-old")))
    run(read(web(Net({HOME: signed_in(**rotated())}), ctx)))
    stored = jar_of(ctx.stored)
    assert stored["ecto_1_sess"] == "ecto-by-another-run", "another run's newer value stays"
    assert stored["rd_challenge"] == "rd-new"


def test_a_failed_or_slow_write_back_never_fails_the_run(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    ctx = Ctx(fail="renew")
    assert run(read(web(Net({HOME: signed_in(**rotated())}), ctx))).state == "in"
    monkeypatch.setattr(W, "RENEW_TIMEOUT_S", 0.05)
    ctx = Ctx(renew_delay_s=5)
    assert run(read(web(Net({HOME: signed_in(**rotated())}), ctx))).state == "in"
    assert ctx.renewed == [] and ctx.calls == [("renew",)]
    assert "not written back" in caplog.text
    assert_no_secret(caplog.text)


def test_a_cancelled_run_writes_nothing_back():
    ctx = Ctx()

    async def go(w):
        async with w:
            await w.page()
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        run(go(web(Net({HOME: signed_in(**rotated())}), ctx)))
    assert ctx.renewed == []


# ---- no secret anywhere --------------------------------------------------------------------------------------------
def test_no_token_viewer_cookie_or_proxy_login_reaches_a_log_or_an_error(caplog):
    caplog.set_level(logging.DEBUG)
    texts = []
    clock = Clock()
    sessions = W.Sessions(clock)
    ctx = Ctx()
    run(read(web(Net({HOME: signed_in(**rotated())}), ctx, sessions)))
    texts.append(repr(sessions.token("acc-1", 1)))
    sessions.keep("acc-1", 1, W.read_page(200, page("page_signed_in")))
    texts.append(repr(sessions.token("acc-1", 1)))
    with pytest.raises(W.GateClosed) as e:
        run(read(web(Net({HOME: signed_in()}), Ctx(), sessions)))
    texts.append(str(e.value))
    clock.advance(300)
    run(read(web(Net({HOME: Resp(429, page("page_vercel"))}), Ctx(), sessions)))

    leak = "token %s viewer %s via %s" % (TOKEN, VIEWER, W.proxy_url(PROXY))
    for net, call in ((Net({("POST", UPLOAD_URL): RuntimeError(leak)}), "upload"),
                      (Net({("WS", GATEWAY_URL): RuntimeError(leak)}), "socket"),
                      (Net({("POST", UPLOAD_URL): Resp(400, leak)}), "upload")):
        async def go(w, call=call):
            async with w:
                if call == "upload":
                    await w.upload(TOKEN, b"x", "image/png", "a.png")
                else:
                    await w.socket(TOKEN)
        with pytest.raises(Exception) as e:
            run(go(web(net)))
        texts += [str(e.value), repr(e.value)]
    assert "rotated cookies written back" in caplog.text and "Vercel page" in caplog.text
    assert_no_secret(caplog.text, *texts)
    assert "rd-new" not in caplog.text and "ecto-new-2" not in caplog.text
