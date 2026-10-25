"""The composer's own page scripts in a real Chrome (browser tier): a row folded behind its "···" button, a hidden copy
of the button that must not be taken for it, and the request watch that proves a send never left the page. The dola
page itself is not loaded: the page below copies only the shape seen on 23/10 (a Blueprint overflow list whose button
holds an icon, its menu opening above it). Every other composer test answers these scripts from a scripted page."""

import asyncio
import functools
import os
from dataclasses import dataclass

import pytest

from relay_browser.session import SiteProfile, open_session
from relay_core.budget import BrowserBudget, Purpose
from relay_core.redact import redact_text
from relay_core.testing.mutation import mutation
from relay_dola.engine import composer
from relay_dola.engine.submit import PageDriver, TrafficWatch

CO = "packages/provider-dola/src/relay_dola/engine/composer.py"
SESSION = "packages/relay-browser/src/relay_browser/session.py"
FIND_VISIBLE = "  const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;})\"\"\"\n"

FOLDED = """<!doctype html><html><body style="margin:0;height:900px">
<span style="display:none">Create Images</span>
<div style="position:fixed;bottom:20px;left:360px;width:1000px;height:90px">
  <div class="flex"><span>Fast</span>
    <div class="bp5-overflow-list overflow-list-TcIun0"><div class="overflow-visible-item-SVWIz6"></div>
      <button id="more" style="width:32px;height:32px"><svg width="16" height="16"><circle cx="8" cy="8" r="2"/></svg>
      </button></div></div>
  <div id="chips"></div>
</div>
<div id="menu" style="display:none;position:fixed;bottom:120px;left:400px;background:#fff">
  %s
</div>
<script>
  const menu = document.getElementById('menu'), chips = document.getElementById('chips');
  document.getElementById('more').addEventListener('click', () => { menu.style.display = 'block'; });
  for (const item of menu.querySelectorAll('[role=menuitem]')) item.addEventListener('click', () => {
    menu.style.display = 'none';
    chips.innerHTML = '<button>Model Seedream 4.5</button>';
  });
</script></body></html>"""
ITEM = '<div role="menuitem" style="padding:8px"><span>%s</span></div>'


@dataclass(frozen=True)
class Account:
    id: str = "acct-1"
    credential_gen: int = 1
    proxy: object = None


def sync(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


async def with_page(tmp_path, body):
    token = BrowserBudget(1).try_acquire(Purpose.SUBMIT)
    site = SiteProfile(name="dola-test", host="dola.com", profile_root=str(tmp_path / "profiles"), headless=True,
                       viewport=(1440, 900))
    try:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact_text) as s:
            return await body(s.page)
    finally:
        token.release()


@pytest.mark.browser
@mutation(CO, FIND_VISIBLE, "  return true;})\"\"\"\n", expect="the folded row was not entered through its menu")
@sync
async def test_a_folded_row_is_entered_through_its_dots_menu_and_a_hidden_copy_is_never_the_button(tmp_path):
    async def body(page):
        await page.set_content(FOLDED % "".join(ITEM % t for t in ("Create Videos", "Create Images", "Writing")))
        try:
            got = await composer.enter_image_mode(page, settle_s=1)
        except composer.ComposerError as exc:
            got = "error: %s" % exc
        assert got == "Model Seedream 4.5", "the folded row was not entered through its menu: %r" % got
        await page.set_content(FOLDED % (ITEM % "Writing"))
        with pytest.raises(composer.ComposerError, match="nor the item"):
            await composer.enter_image_mode(page, settle_s=1)
        assert not await page.evaluate("() => document.getElementById('chips').textContent"), \
            "a menu without the item still changed the mode"
    await with_page(tmp_path, body)


RECORDED = os.path.join(os.path.dirname(__file__), "..", "..", "..", "fixtures", "dola", "composer-261025")
# The composer's action row as dola rendered it on 25/10 (recorded, stripped of layout classes, icons and ids), at the
# bottom of the screen where the composer is.
ROW_PAGE = """<!doctype html><html><body style="margin:0;height:900px">
<div style="position:fixed;bottom:0;left:360px;width:1000px">%s</div></body></html>"""
LONG_CHIP = "    return re.test(t) && t.length <= 44 &&"


def recorded_row(name: str) -> str:
    with open(os.path.join(RECORDED, name), encoding="utf-8") as f:
        return ROW_PAGE % f.read()


async def chip_texts(page, patterns: dict) -> dict:
    return {name: (await composer._wait_rect(page, pattern, timeout_s=1) or {}).get("text")
            for name, pattern in patterns.items()}


@pytest.mark.browser
@mutation(CO, LONG_CHIP, "    return re.test(t) && t.length <= 30 &&", expect="the recorded video row")
@sync
async def test_the_recorded_25_10_rows_give_every_chip_the_composer_reads(tmp_path):
    """25/10 dola's default video model became 2.0 Fast: its chip `Model Dreamina Seedance 2.0 Fast` is 32 characters,
    over the old cap of 30, and every job failed `chip row did not appear after clicking Create Videos`."""
    async def body(page):
        await page.set_content(recorded_row("video_row.html"))
        got = await chip_texts(page, {"model": composer.MODEL_CHIP, "duration": composer.DURATION_CHIP,
                                      "combo": composer.COMBO_CHIP})
        assert got == {"model": "Model Dreamina Seedance 2.0 Fast", "duration": "Auto · 10s",
                       "combo": "Auto · 10s"}, "the recorded video row: %r" % got
        assert await composer.read_composer_kind(page) == "new"
        await page.set_content(recorded_row("image_row.html"))
        got = await chip_texts(page, {"model": composer.MODEL_CHIP, "ratio": composer.IMAGE_RATIO_CHIP,
                                      "style": composer.STYLE_CHIP})
        assert got == {"model": "Model Seedream 4.5", "ratio": "Aspect ratio Auto", "style": "Style"}, \
            "the recorded image row: %r" % got
    await with_page(tmp_path, body)


async def ws_server():
    """A WebSocket endpoint on 127.0.0.1 that answers the handshake and then only reads."""
    import base64
    import hashlib

    async def handle(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        key = next(line.split(":", 1)[1].strip() for line in head.split("\r\n")
                   if line.lower().startswith("sec-websocket-key:"))
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        await writer.drain()
        while await reader.read(1024):
            pass
    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def settle(watch, want=True):
    for _ in range(40):
        if watch.seen() == want:
            return
        await asyncio.sleep(0.05)


@pytest.mark.browser
@sync
async def test_the_traffic_watch_sees_every_send_of_the_context_even_when_it_fails(tmp_path):
    """The real context reports a query-routed send, a POST from a page opened after the watch, and a WebSocket frame;
    a plain read is not a send."""
    server = await ws_server()
    port = server.sockets[0].getsockname()[1]

    async def body(page):
        ctx = page.context
        watch = TrafficWatch(ctx)
        watch.arm()
        await page.evaluate("() => fetch('https://www.dola.invalid/im/chain/single').catch(() => 0)")
        await settle(watch, True)
        assert not watch.seen(), "a read of the conversation counted as a send"
        await page.evaluate("() => fetch('https://www.dola.invalid/api?route=completion').catch(() => 0)")
        await settle(watch)
        assert watch.seen(), "a send routed by its query was not seen"
        watch.stop()

        watch = TrafficWatch(ctx)
        watch.arm()
        popup = await ctx.new_page()
        await popup.evaluate("() => fetch('https://www.dola.invalid/x', {method: 'POST'}).catch(() => 0)")
        await settle(watch)
        assert watch.seen(), "a POST from a page opened after the watch was not seen"
        watch.stop()

        watch = TrafficWatch(ctx)
        watch.arm()
        await page.evaluate("""(port) => new Promise(done => {
            const ws = new WebSocket('ws://127.0.0.1:' + port + '/');
            ws.onopen = () => { ws.send('order'); setTimeout(done, 200); };
            ws.onerror = () => done();
        })""", port)
        await settle(watch)
        assert watch.seen(), "a WebSocket send was not seen"
        watch.stop()
    try:
        await with_page(tmp_path, body)
    finally:
        server.close()


CLOSED = ("                await asyncio.wait_for(self.ctx.close(), CLOSE_TIMEOUT_S)\n"
          "                self.closed = True\n")


@pytest.mark.browser
@mutation(SESSION, CLOSED, "                self.closed = True\n", expect="the browser still sent after the halt")
@sync
async def test_the_halt_closes_the_whole_browser_so_nothing_sends_after_it_and_the_session_exit_is_clean(tmp_path):
    """Before "provably not sent" the browser must be gone: a page that keeps POSTing (a queued send) and a second page
    die with the browser, the watch counts until the close has completed, and the session's own exit afterwards
    neither fails nor reuses the closed browser."""
    token = BrowserBudget(1).try_acquire(Purpose.SUBMIT)
    site = SiteProfile(name="dola-test", host="dola.com", profile_root=str(tmp_path / "profiles"), headless=True)
    try:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact_text) as s:
            ctx, page = s.ctx, s.page
            await page.set_content("<script>setInterval(() => fetch('https://www.dola.invalid/x', "
                                   "{method: 'POST'}).catch(() => 0), 50)</script>")
            other = await ctx.new_page()
            watch = TrafficWatch(ctx)
            watch.arm()
            await asyncio.sleep(0.5)
            assert watch.seen(), "the page's own sends were not seen"
            assert await PageDriver().halt(s), "the browser close could not be confirmed"
            assert page.is_closed() and other.is_closed(), "the browser still sent after the halt: a page survived"
            before = watch.count
            await asyncio.sleep(1)
            assert watch.count == before, "the browser still sent after the halt"
            assert await s.close_now(), "a second close of the closed browser failed"
            watch.stop()
    finally:
        token.release()
