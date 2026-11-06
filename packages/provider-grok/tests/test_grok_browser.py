"""grok's text to image sent from a Chrome page (`engine/browser.py`, `grok.t2i_transport` = browser), driven through
the real submit with the browser kit's `open_session` replaced by a fake page whose CDP bridges to the kit's
`FakeSocket` (grok's side, fed from the 06/11 captures): no Chrome, no grok.com, no real cookie."""

import asyncio
import contextlib
import itertools
import json
import re
from types import SimpleNamespace

import pytest
from grokkit import SSO, Clock, Ctx, Cut, FakeSession, FakeSocket, Resp, assert_no_secret, fixture, job, names, settings

import relay_browser.session as kit
from relay_core.budget import Purpose
from relay_core.providers import Ambiguous, Completed, NotTaken, Rest, Retry, Success
from relay_core.testing.mutation import mutation
from relay_grok import errors
from relay_grok.constants import IMAGINE_WS, QUOTA_PATH
from relay_grok.engine import browser as B
from relay_grok.engine import submit as S

BROWSER_FILE = "packages/provider-grok/src/relay_grok/engine/browser.py"
WEB_FILE = "packages/provider-grok/src/relay_grok/engine/web.py"
SPEC_FILE = "packages/provider-grok/src/relay_grok/spec.py"
QUOTA = ("POST", QUOTA_PATH)
SPIKE_REQUEST = "00000000-0000-0000-0000-000000000001"      # -> img_000...01, the capture's request_id
SEND_TEXT = re.compile(r"^window\.__relayImagine\.send\((\".*\")\)$")


def frames_in(name: str) -> list[dict]:
    return [f["data"] for f in fixture(name)["frames"] if f["dir"] == "in"]


def quota(name: str) -> Resp:
    f = fixture(name)
    return Resp(f["status"], f.get("headers"), json_body=f["body"])


# ---- the fake page ---------------------------------------------------------------------------------------------------
class FakeCdp:
    """The page's CDP session with grok behind it. Runtime.evaluate of the open script answers `opened` (after a
    handshake of `status`); of a send, hands the text to `sock` (grok's side) and reports it sent, unless `silent`;
    `sock`'s answers are reported received and its end closed. `page_frames` are frames of a socket of the page's own,
    reported right after our first frame left: they are never ours. `ends_after_reset`: grok ends the socket right
    after the first frame."""

    def __init__(self, sock: FakeSocket, *, opened="open", status=101, page_frames=(), silent=False,
                 ends_after_reset=False):
        self.sock, self.opened, self.status, self.silent = sock, opened, status, silent
        self.ends_after_reset = ends_after_reset
        self.page_frames = list(page_frames)
        self.handlers: dict[str, list] = {}
        self.methods: list[str] = []
        self.open = False
        self.pump: asyncio.Task | None = None

    def on(self, event, cb):
        self.handlers.setdefault(event, []).append(cb)

    def emit(self, event, rid, response=None):
        for cb in self.handlers.get(event, []):
            cb({"requestId": rid, "response": response or {}})

    async def send(self, method, params=None):
        self.methods.append(method)
        if method != "Runtime.evaluate":
            return {}
        js = params["expression"]
        if js == B.OPEN_JS:
            self.emit("Network.webSocketHandshakeResponseReceived", "ours", {"status": self.status})
            self.open = self.opened == "open"
            return {"result": {"value": self.opened}}
        sent = SEND_TEXT.search(js)
        if sent:
            if not self.open:       # a socket that ended drops the frame without a word
                return {"result": {}}
            text = json.loads(sent.group(1))
            await self.sock.send(text)
            if not self.silent:
                self.emit("Network.webSocketFrameSent", "ours", {"opcode": 1, "payloadData": text})
            for f in self.page_frames:
                self.emit("Network.webSocketFrameReceived", "page", {"opcode": 1, "payloadData": json.dumps(f)})
            self.page_frames = []
            if self.ends_after_reset:
                self.open = False
                self.emit("Network.webSocketClosed", "ours")
            elif self.pump is None:
                self.pump = asyncio.ensure_future(self._pump())
            return {"result": {}}
        return {"result": {}}

    async def _pump(self):
        while True:
            try:
                data, _flags = await self.sock.recv()
            except BaseException:
                self.open = False
                self.emit("Network.webSocketClosed", "ours")
                return
            self.emit("Network.webSocketFrameReceived", "ours", {"opcode": 1, "payloadData": data.decode()})
            await asyncio.sleep(0)


class Page:
    """What open_imagine sees of the kit: `opened` holds each open_session's account, token and cookies given."""

    def __init__(self, cdp: FakeCdp, *, load=200, load_error: BaseException | None = None, cookies_after=()):
        self.cdp, self.load, self.load_error = cdp, load, load_error
        self.cookies_after = list(cookies_after)
        self.log: list[tuple] = []

    @contextlib.asynccontextmanager
    async def open_session(self, site, account, *, purpose, token, redact, images=False, response_patches=()):
        kit.check_token(token, purpose)
        self.log.append(("open", site.host, account.id, str(purpose), callable(redact)))
        page = self

        class C:
            async def add_cookies(self, cookies):
                page.log.append(("cookies", sorted((c["domain"], c["name"], c["httpOnly"]) for c in cookies)))

            async def new_cdp_session(self, _page):
                return page.cdp

            async def cookies(self, url):
                return page.cookies_after

        class P:
            async def goto(self, url, **kw):
                page.log.append(("goto", url))
                if page.load_error is not None:
                    raise page.load_error

                async def text():
                    return "<html>Just a moment...</html>"
                return SimpleNamespace(status=page.load, headers={"content-type": "text/html"}, text=text)

        try:
            yield SimpleNamespace(ctx=C(), page=P())
        finally:
            self.log.append(("closed",))


def run_browser(monkeypatch, ctx, j, page: Page, *, q="quota_before", cap_s=10.0, hold_margin_s=15.0, **over):
    """One submit with the page. `hold_margin_s` 29 with `image_submit_timeout_s=30` gives the run a 1 s bound."""
    monkeypatch.setattr(kit, "open_session", page.open_session)
    ctx.settings = settings(t2i_transport="browser", **over)
    session = FakeSession({QUOTA: [quota(q), quota("quota_after_t2i")]}, calls=ctx.calls)
    clock = Clock()
    ids = itertools.repeat(SPIKE_REQUEST)
    deps = S.Deps(make_session=session, clock=clock, now=clock.now, uuid4=lambda: next(ids),
                  hold_margin_s=hold_margin_s)

    async def go():
        return await asyncio.wait_for(S.submit(ctx, j, ctx.settings, deps=deps), cap_s)
    return asyncio.run(go()), session


# ---- the send --------------------------------------------------------------------------------------------------------
@mutation(BROWSER_FILE, "        elif rid == self.mine:\n", "        else:\n", expect="a page socket's frame was read")
@mutation(WEB_FILE, '        if self.settings.get("grok.t2i_transport") == "browser":\n',
          '        if self.settings.get("grok.t2i_transport") == "never":\n', expect="the page sent the frame")
def test_t2i_from_the_page_marks_sends_the_curl_frame_and_reads_only_its_own_socket(monkeypatch):
    ctx = Ctx()
    sock = FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)
    # a socket of the page's own answers grok's rate limit (no request id): it must never reach the reader
    page = Page(FakeCdp(sock, page_frames=frames_in("imagine_rate_limit_frame")))
    result, session = run_browser(monkeypatch, ctx, job("t2i", n=2), page)
    assert not any(r.method == "WS" for r in session.requests), "the page sent the frame"
    seq = names(ctx.calls)
    assert seq.count("item.create") == 1 and seq.index("reset") < seq.index("report_ref") \
        < seq.index("mark_sent") < seq.index("item.create")
    assert sock.sent[1] == S.item_create("img_00000000000000000000000000000001", job("t2i").sent_text, "1:1", 2,
                                         sock.sent[1]["timestamp"])
    assert isinstance(result.job, Completed), "a page socket's frame was read"
    assert len(result.job.outputs) == 2 and Success() in result.account
    assert ctx.refs[-1][1]["conversation_id"] == "c0000000-0000-4000-8000-000000000008"
    assert page.log[0] == ("open", "grok.com", "acc-1", str(Purpose.SUBMIT), True)
    assert page.log[-1] == ("closed",)


def test_the_page_gets_the_accounts_grok_cookies_only_before_it_loads(monkeypatch):
    ctx = Ctx()
    page = Page(FakeCdp(FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)))
    run_browser(monkeypatch, ctx, job("t2i"), page)
    cookies = next(e for e in page.log if e[0] == "cookies")[1]
    assert {d for d, _n, _h in cookies} <= {"grok.com", ".grok.com"}
    assert {n for _d, n, _h in cookies} == {"sso", "sso-rw", "x-userid", "__cf_bm"}
    assert all(h == (n in ("sso", "sso-rw")) for _d, n, h in cookies)
    kinds = [e[0] for e in page.log]
    assert kinds.index("cookies") < kinds.index("goto") and ("goto", B.IMAGINE_PAGE) in page.log


def test_a_rate_limit_frame_on_the_page_socket_moves_the_job_like_curl(monkeypatch):
    ctx = Ctx()
    sock = FakeSocket(frames_in("imagine_rate_limit_frame"), calls=ctx.calls)
    result, _ = run_browser(monkeypatch, ctx, job("t2i"), Page(FakeCdp(sock)))
    assert isinstance(result.job, NotTaken) and result.job.code == S.POOL_THROTTLED
    assert any(isinstance(e, Rest) for e in result.account)
    assert len(sock.generations()) == 1


# ---- a socket that ends --------------------------------------------------------------------------------------------
@mutation(BROWSER_FILE, "        if self.status == 401:\n            raise SignedOut(\"ws\")\n", "",
          expect="a 401 handshake is signed out")
def test_a_socket_the_page_could_not_open_sends_nothing(monkeypatch):
    for status, want in ((None, Retry(errors.HTTP_ERROR)), (401, Retry(S.POOL_LOGGED_OUT))):
        ctx = Ctx()
        sock = FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)
        result, _ = run_browser(monkeypatch, ctx, job("t2i"), Page(FakeCdp(sock, opened="closed 1006",
                                                                               status=status)))
        assert result.job == want, "a 401 handshake is signed out"
        assert "mark_sent" not in names(ctx.calls) and not sock.sent


@mutation(BROWSER_FILE, "                await asyncio.wait_for(self.known.wait(), WS_OPEN_S)\n",
          "                pass\n", expect="marked sent with no frame seen leaving")
def test_nothing_is_marked_until_the_browser_shows_the_first_frame_leaving(monkeypatch):
    monkeypatch.setattr(B, "WS_OPEN_S", 0.05)
    ctx = Ctx()
    sock = FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)
    result, _ = run_browser(monkeypatch, ctx, job("t2i"), Page(FakeCdp(sock, silent=True)), hold_margin_s=29.0,
                            image_submit_timeout_s=30)
    assert "mark_sent" not in names(ctx.calls), "marked sent with no frame seen leaving"
    assert result.job == Retry(errors.HTTP_ERROR) and not sock.generations()


@mutation(BROWSER_FILE, '            raise NetError("WebSocketClosed", False)\n        return item\n',
          "            return '{}'\n        return item\n", expect="an ended socket was read on")
def test_a_socket_that_ends_after_the_send_is_unknown_and_never_sent_again(monkeypatch):
    ctx = Ctx()
    sock = FakeSocket(frames_in("imagine_t2i_n2")[:2], then=Cut("closed"), calls=ctx.calls)
    result, _ = run_browser(monkeypatch, ctx, job("t2i"), Page(FakeCdp(sock)), hold_margin_s=29.0,
                            image_submit_timeout_s=30)
    assert isinstance(result.job, Ambiguous) and "failed after the send" in result.job.note, \
        "an ended socket was read on"
    assert len(sock.generations()) == 1 and names(ctx.calls).count("mark_sent") == 1


def test_a_socket_that_ends_between_the_reset_and_the_send_is_unknown(monkeypatch):
    ctx = Ctx()
    sock = FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)
    result, _ = run_browser(monkeypatch, ctx, job("t2i"), Page(FakeCdp(sock, ends_after_reset=True)), cap_s=5.0)
    assert isinstance(result.job, Ambiguous)
    assert not sock.generations() and names(ctx.calls).count("mark_sent") == 1


# ---- the page load -------------------------------------------------------------------------------------------------
def test_a_refused_or_failed_page_load_sends_nothing(monkeypatch):
    ctx = Ctx()
    sock = FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)
    result, _ = run_browser(monkeypatch, ctx, job("t2i"), Page(FakeCdp(sock), load=403))
    assert result.job.code == errors.CLOUDFLARE and not sock.sent

    ctx = Ctx()
    err = RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED at https://grok.com/imagine")
    result, _ = run_browser(monkeypatch, ctx, job("t2i"), Page(FakeCdp(sock), load_error=err))
    assert result.job == Retry(S.POOL_PROXY_FAILED) and "mark_sent" not in names(ctx.calls)


def test_a_sso_the_page_was_given_anew_is_told_by_name_only(monkeypatch):
    ctx = Ctx()
    after = [dict(name="sso", value=SSO + "-new"), dict(name="x-userid", value="other")]
    page = Page(FakeCdp(FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)), cookies_after=after)
    run_browser(monkeypatch, ctx, job("t2i"), page)
    assert [e[0] for e in ctx.events if "sso" in e[0]] == ["grok set sso (sso)"]
    assert_no_secret(ctx.events, ctx.refs)


# ---- the setting and the slots -------------------------------------------------------------------------------------
def test_the_transport_setting_defaults_to_the_browser_and_takes_only_its_two_values():
    from relay_grok.spec import SPEC
    spec = SPEC.settings()["grok.t2i_transport"]
    assert spec.default == "browser" and spec.rule.choices == ("browser", "http")
    assert spec.validate("http") == "http"
    for bad in ("chrome", "curl"):
        with pytest.raises(ValueError):
            spec.validate(bad)


@mutation(SPEC_FILE, '    execution_setting = PROVIDER + ".t2i_transport"', '    execution_setting = None',
          expect="the admin reads grok.t2i_transport")
@pytest.mark.parametrize("transport", ["browser", "http"])
def test_the_admin_shows_the_live_transport_as_grok_s_execution(transport):
    """The Today card shows `settings.get(spec.execution_setting)`: Browser or HTTP as the transport is set now. The
    registry accepts it (its rule lists only http and browser)."""
    from relay_core.providers import ProviderRegistry
    from relay_grok.spec import SPEC
    assert SPEC.execution_setting == "grok.t2i_transport", "the admin reads grok.t2i_transport"
    ProviderRegistry().register(SPEC)
    assert settings(t2i_transport=transport).get(SPEC.execution_setting) == transport


def test_the_curl_transport_opens_no_browser(monkeypatch):
    ctx = Ctx()
    opened = []
    monkeypatch.setattr(kit, "open_session", lambda *a, **k: opened.append(1))
    sock = FakeSocket(frames_in("imagine_t2i_n2"), calls=ctx.calls)
    session = FakeSession({QUOTA: [quota("quota_before"), quota("quota_after_t2i")], ("WS", IMAGINE_WS): sock},
                          calls=ctx.calls)
    clock = Clock()
    deps = S.Deps(make_session=session, clock=clock, now=clock.now, uuid4=lambda: SPIKE_REQUEST)
    result = asyncio.run(asyncio.wait_for(S.submit(ctx, job("t2i", n=2), ctx.settings, deps=deps), 10))
    assert isinstance(result.job, Completed) and not opened and "ws open" in names(ctx.calls)


@mutation(BROWSER_FILE, '    while _busy >= int(settings.get("grok.browsers")):\n',
          '    while _busy > int(settings.get("grok.browsers")):\n', expect="more browsers than grok.browsers")
def test_no_more_browsers_than_the_setting_run_at_once():
    s = settings(browsers=1)
    most = 0

    async def one():
        nonlocal most
        async with B.browser_slot(s) as token:
            assert not token.released and token.purpose == Purpose.SUBMIT
            most = max(most, B._busy)
            await asyncio.sleep(0.6)

    async def go():
        await asyncio.gather(one(), one())
    asyncio.run(go())
    assert most == 1, "more browsers than grok.browsers"
    assert B._busy == 0
