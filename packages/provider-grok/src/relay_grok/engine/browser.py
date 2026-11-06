"""grok's Imagine socket from a real Chrome page: how a text to image is sent while `grok.t2i_transport` is `browser`.

Why: on 06/11 grok.com refused the curl_cffi Imagine socket of 11 of 14 accounts with `rate_limit_exceeded` before
anything was made, whatever the headers, their order, the frames, fresh cookies, the timing or the TLS profile; the same
frame sent by page JS from Chrome, on the same account and proxy, delivered.

So the socket is opened by a page. `open_imagine` opens Chrome with the browser kit (`relay_browser.open_session`: the
image's pinned Chrome, the account's own profile under its flock, the account's proxy through the local forwarder, no
direct fallback), gives it the account's cookies of grok.com only, loads grok.com/imagine, and opens the socket from the
page's main world (CDP `Runtime.evaluate`). `BrowserSocket` is `web.Socket`'s three calls over it: the submit sends the
same frames, in the same order, and its reader (wire.py), its verdicts and its report_ref / mark_sent order are the
same. The frames come back through CDP `Network.webSocketFrameReceived` (no `Runtime.enable`: a page can tell it is on).
Our socket is told from any socket of the page's own by its first frame: the exact reset text the submit sent; until the
browser has shown that frame leaving, nothing is marked sent.

Nothing else uses the browser: the quota, the read-back, the downloads, an edit and a video stay on curl. Nothing here
calls a refresh, rotate or logout; a `sso` the page was given anew is told as an event (names only), never kept. At most
`grok.browsers` Chromes per worker at once (a job waits inside its submit bound). Nothing logged holds a cookie, the
proxy's login or a frame.
"""

import asyncio
import contextlib
import json
import logging

from relay_core.budget import BrowserBudget, Purpose
from relay_core.redact import redact_text
from relay_grok.constants import IMAGINE_WS, SITE_HOST, SITE_URL, SSO_COOKIE, WS_OPEN_S
from relay_grok.credential import cookies_of
from relay_grok.engine.web import NetError, Refused, SignedOut
from relay_grok.engine.wire import classify_refusal

log = logging.getLogger("relay_grok.browser")

IMAGINE_PAGE = SITE_URL + "/imagine"
LOAD_TIMEOUT_MS = 45_000          # one page load through the proxy (a cold profile fetches every script)
SLOT_POLL_S = 0.5                 # between two looks for a free browser
HTTP_ONLY = (SSO_COOKIE, "sso-rw")
CLOSED = object()                 # the socket's end, queued after its last frame
# The page's socket, kept on the page's window: open resolves "open", or "closed <code>" when it never opened.
OPEN_JS = """new Promise(done => {
  const ws = new WebSocket(%s);
  window.__relayImagine = ws;
  ws.onopen = () => done("open");
  ws.onclose = e => done("closed " + e.code);
})""" % json.dumps(IMAGINE_WS)
# One text frame. A socket that ended drops it without a word: the reset then never shows leaving (nothing is marked
# sent), the item.create reads the end (unknown, never sent again).
SEND_JS = "window.__relayImagine.send(%s)"
CLOSE_JS = "window.__relayImagine && window.__relayImagine.close()"


class BrowserSocket:
    """The Imagine socket of a page, over its CDP session: `send_json` (cut at WS_OPEN_S), `recv_text` (uncut: the
    submit cuts its read), `close`. A socket that ends or a frame the page could not hand over raises NetError."""

    def __init__(self, cdp):
        self.cdp = cdp
        self.mine: str | None = None        # the CDP request id of our socket, known once our first frame left
        self.first: str | None = None       # the text of our first frame
        self.known = asyncio.Event()
        self.frames: asyncio.Queue = asyncio.Queue()
        self.early: dict[str, list] = {}    # frames (and ends) of sockets seen before ours was known
        self.status: int | None = None      # the last WebSocket handshake status Chrome reported

    async def listen(self) -> None:
        """The socket events, before anything opens one."""
        self.cdp.on("Network.webSocketHandshakeResponseReceived", self._handshake)
        self.cdp.on("Network.webSocketFrameSent", self._sent)
        self.cdp.on("Network.webSocketFrameReceived", self._received)
        self.cdp.on("Network.webSocketFrameError", self._ended)
        self.cdp.on("Network.webSocketClosed", self._ended)
        await self.cdp.send("Network.enable")

    def _handshake(self, ev: dict) -> None:
        status = (ev.get("response") or {}).get("status")
        self.status = status if isinstance(status, int) else self.status

    def _sent(self, ev: dict) -> None:
        if self.mine is not None or self.first is None or (ev.get("response") or {}).get("payloadData") != self.first:
            return
        self.mine = ev.get("requestId")
        for item in self.early.pop(self.mine, []):
            self.frames.put_nowait(item)
        self.early.clear()
        self.known.set()

    def _push(self, rid, item) -> None:
        if self.mine is None:
            self.early.setdefault(rid, []).append(item)
        elif rid == self.mine:
            self.frames.put_nowait(item)

    def _received(self, ev: dict) -> None:
        self._push(ev.get("requestId"), (ev.get("response") or {}).get("payloadData") or "")

    def _ended(self, ev: dict) -> None:
        self._push(ev.get("requestId"), CLOSED)

    async def _eval(self, js: str, *, wait: bool = False):
        got = await asyncio.wait_for(self.cdp.send("Runtime.evaluate", {
            "expression": js, "returnByValue": True, "awaitPromise": wait}), WS_OPEN_S)
        if got.get("exceptionDetails"):
            raise NetError("PageScriptError", False)
        return (got.get("result") or {}).get("value")

    async def open(self) -> None:
        """The page opens the socket. Not open: a 401 handshake is SignedOut("ws"), another status a refusal of its
        kind, no answer a NetError."""
        state = await self._eval(OPEN_JS, wait=True)
        if state == "open":
            return
        if self.status == 401:
            raise SignedOut("ws")
        if self.status is not None and self.status != 101:
            raise Refused(classify_refusal(self.status, "", ""), self.status)
        raise NetError("WebSocketClosed", False)

    async def send_json(self, obj) -> None:
        text = json.dumps(obj)
        first = self.first is None
        if first:
            self.first = text
        await self._eval(SEND_JS % json.dumps(text))
        if first:       # our socket is known only once the browser shows this frame leaving
            try:
                await asyncio.wait_for(self.known.wait(), WS_OPEN_S)
            except TimeoutError:
                raise NetError("NoFrameEvent", False) from None

    async def recv_text(self) -> str:
        item = await self.frames.get()
        if item is CLOSED:
            raise NetError("WebSocketClosed", False)
        return item

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._eval(CLOSE_JS)


def chrome_cookies(blob: bytes) -> list[dict]:
    """The credential's grok.com cookies as the browser takes them (`add_cookies`); `sso` and `sso-rw` out of reach of
    the page's scripts, as grok sets them."""
    return [{"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"], "secure": True,
             "httpOnly": c["name"] in HTTP_ONLY} for c in cookies_of(blob)]


_busy = 0


@contextlib.asynccontextmanager
async def browser_slot(settings):
    """A browser token, once fewer than `grok.browsers` run in this worker (read live). grok is an HTTP provider: the
    core gives it no browser budget, so the bound is kept here (as qwen's mint does)."""
    global _busy
    while _busy >= int(settings.get("grok.browsers")):
        await asyncio.sleep(SLOT_POLL_S)
    _busy += 1          # no await between the look and the count: two waiters never take one slot
    token = BrowserBudget(1).try_acquire(Purpose.SUBMIT)
    try:
        yield token
    finally:
        token.release()
        _busy -= 1


def _proxy_failed(exc: BaseException) -> bool:
    text = str(exc)
    return "ERR_TUNNEL_CONNECTION_FAILED" in text or "ERR_PROXY_CONNECTION_FAILED" in text


async def _page_refusal(resp) -> Exception:
    """A grok.com/imagine load answered with another status than 200."""
    if resp.status == 401:
        return SignedOut("page")
    body = ""
    with contextlib.suppress(Exception):
        body = await resp.text()
    headers = resp.headers or {}
    return Refused(classify_refusal(resp.status, headers.get("content-type", ""), body, headers.get("server", "")),
                   resp.status)


@contextlib.asynccontextmanager
async def open_imagine(web):
    """Chrome on `web`'s account with grok.com/imagine loaded and its Imagine socket open: a `BrowserSocket`. Every
    failure before the yield sends nothing (the submit maps it); a proxy that failed is a NetError of the proxy."""
    from relay_browser.session import SiteProfile, open_session  # late: only a page send starts a browser

    site = SiteProfile(name="grok", host=SITE_HOST, timezone="UTC")
    async with browser_slot(web.settings) as token, \
            open_session(site, web.account, purpose=Purpose.SUBMIT, token=token, redact=redact_text) as session:
        await session.ctx.add_cookies(chrome_cookies(web.account.credential))
        cdp = await session.ctx.new_cdp_session(session.page)
        sock = BrowserSocket(cdp)
        await sock.listen()
        try:
            resp = await session.page.goto(IMAGINE_PAGE, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        except Exception as e:      # its text may hold a URL: only the kind goes on
            raise NetError(type(e).__name__, _proxy_failed(e)) from None
        if resp is not None and resp.status != 200:
            raise await _page_refusal(resp)
        await sock.open()
        try:
            yield sock
        finally:
            with contextlib.suppress(Exception):
                await web.tell_sso([(c.get("name"), c.get("value")) for c in await session.ctx.cookies(SITE_URL)])
