"""UI helpers (stage 1; coverage SESS01, SESS03, E06, E12): cookie conversion and injection with
read-back, the one-tab rule, typing and sending. The pure tests use stand-in objects; the browser tests drive a real
Chrome."""

import json

import pytest
from browserkit import Account, redact, site_for, sync
from fixtures_server import SiteServer

from relay_browser.session import open_session
from relay_browser.ui import (
    SessionError,
    UiError,
    cookies_for_playwright,
    inject_cookies,
    keep_single_tab,
    select_all_key,
    type_and_send,
)
from relay_core.testing.mutation import mutation

U = "packages/relay-browser/src/relay_browser/ui.py"
IDENTITY = ("sessionid", "uid_tt")
TAB_RULE = """    page = await ctx.new_page()
    for other in list(ctx.pages):
        if other is not page and (host in other.url or other.url == "about:blank"):
            try:
                await other.close()
            except Exception:      # noqa: BLE001 - a tab already gone is the goal
                pass
    return page"""
CLOSE_THEN_OPEN = """    for other in list(ctx.pages):
        if host in other.url:
            await other.close()
    return await ctx.new_page()"""


# ---- cookie conversion (SESS01) --------------------------------------------------------------------------------------
JAR = {"cookies": [
    {"name": "a", "value": "1", "domain": ".site.test", "sameSite": "no_restriction", "secure": True,
     "session": False, "expirationDate": 1794045504.5},
    {"name": "b", "value": "2", "domain": ".site.test", "session": True, "expirationDate": 1794045504.5},
    {"name": "c", "value": "3", "domain": ".site.test", "sameSite": "strict", "httpOnly": True, "path": "/x"},
]}


@mutation(U, 'if not c.get("session") and c.get("expirationDate"):', 'if c.get("expirationDate"):',
          expect="session cookie got expires")
@mutation(U, '"no_restriction": "None"', '"no_restriction": "Lax"', expect="sameSite is not kept")
def test_conversion_keeps_same_site_and_drops_expires_for_session_cookies():
    a, b, c = cookies_for_playwright(JAR)
    assert a["sameSite"] == "None" and a["expires"] == 1794045504 and a["secure"] is True, "sameSite is not kept"
    assert "expires" not in b and b["sameSite"] == "Lax", "a session cookie got expires: the browser drops it"
    assert c["sameSite"] == "Strict" and c["httpOnly"] is True and c["path"] == "/x"


def test_a_jar_is_read_as_data_a_bare_list_or_json_text():
    bare = [{"name": "c", "value": "3", "domain": ".site.test", "session": True}]
    assert cookies_for_playwright(bare)[0]["name"] == "c"
    assert cookies_for_playwright(json.dumps(JAR))[0]["name"] == "a"
    assert cookies_for_playwright(json.dumps(bare).encode())[0]["name"] == "c"


# ---- injection with read-back (E06) ----------------------------------------------------------------------------------
class CookieCtx:
    def __init__(self, readback):
        self.readback, self.calls = readback, []

    async def clear_cookies(self):
        self.calls.append("clear")

    async def add_cookies(self, cookies):
        self.calls.append("add")

    async def cookies(self, url):
        self.calls.append("read:" + url)
        return self.readback


IDENTITY_JAR = {"cookies": [{"name": "sessionid", "value": "SESS-A", "domain": ".site.test", "session": True},
                            {"name": "uid_tt", "value": "0123", "domain": ".site.test", "session": True},
                            {"name": "ttwid", "value": "x", "domain": ".site.test", "session": True}]}
GOOD = [{"name": "sessionid", "value": "SESS-A"}, {"name": "uid_tt", "value": "0123"}]


@mutation(U, "        await ctx.clear_cookies()", "        pass", expect="old cookies were not cleared")
@sync
async def test_injection_clears_the_old_cookies_adds_the_jar_and_reads_the_identity_cookies_back():
    ctx = CookieCtx(GOOD)
    assert await inject_cookies(ctx, IDENTITY_JAR, IDENTITY, "https://www.site.test") == ["sessionid", "uid_tt"]
    assert ctx.calls == ["clear", "add", "read:https://www.site.test"], "old cookies were not cleared before the add"


@mutation(U, "if got.get(name) != value:", "if False:", expect="stale session was accepted")
@mutation(U, "    if not want:", "    if False:", expect="nothing to check was accepted")
@sync
async def test_a_stale_session_or_a_jar_without_identity_cookies_stops_the_run():
    stale = CookieCtx([{"name": "sessionid", "value": "SESS-OLD"}, {"name": "uid_tt", "value": "0123"}])
    with pytest.raises(SessionError, match="sessionid"):
        await inject_cookies(stale, IDENTITY_JAR, IDENTITY, "https://www.site.test")
        raise AssertionError("a stale session was accepted")
    missing = CookieCtx([{"name": "sessionid", "value": "SESS-A"}])
    with pytest.raises(SessionError, match="uid_tt"):
        await inject_cookies(missing, IDENTITY_JAR, IDENTITY, "https://www.site.test")
        raise AssertionError("a half-injected session was accepted")
    with pytest.raises(SessionError, match="none of the identity cookies"):
        await inject_cookies(CookieCtx([]), {"cookies": [{"name": "ttwid", "value": "x", "domain": "d"}]}, IDENTITY,
                             "https://www.site.test")
        raise AssertionError("a jar with nothing to check was accepted")


# ---- one tab (SESS03) ------------------------------------------------------------------------------------------------
class Tab:
    def __init__(self, url, log):
        self.url, self._log, self.closed = url, log, False

    async def close(self):
        self.closed = True
        self._log.append("close:" + self.url)


class TabCtx:
    def __init__(self):
        self.log = []
        self.pages = [Tab("https://www.site.test/chat", self.log), Tab("https://www.site.test/x", self.log),
                      Tab("https://other.example/", self.log)]

    async def new_page(self):
        self.log.append("open")
        tab = Tab("about:blank", self.log)
        self.pages.append(tab)
        return tab


@mutation(U, TAB_RULE, CLOSE_THEN_OPEN, expect="before any close")
@mutation(U, 'if other is not page and (host in other.url or other.url == "about:blank"):', "if other is not page:",
          expect="other site's tab")
@sync
async def test_a_new_tab_opens_before_the_old_ones_close_and_other_sites_are_left_alone():
    ctx = TabCtx()
    page = await keep_single_tab(ctx, "site.test")
    assert ctx.log[0] == "open", "the new tab must open before any close"
    assert sorted(ctx.log[1:]) == ["close:https://www.site.test/chat", "close:https://www.site.test/x"], \
        "the other site's tab was closed, or an old tab of the site was kept: %r" % ctx.log
    assert page.url == "about:blank"


@mutation(U, 'if other is not page and (host in other.url or other.url == "about:blank"):',
          "if other is not page and host in other.url:", expect="the leftover about:blank tab was kept")
@mutation(U, 'if other is not page and (host in other.url or other.url == "about:blank"):',
          'if host in other.url or other.url == "about:blank":', expect="the new tab was closed")
@sync
async def test_the_leftover_blank_tab_is_closed_and_the_new_tab_never_is():
    ctx = TabCtx()
    first = Tab("about:blank", ctx.log)               # the tab a fresh Chrome starts with
    ctx.pages.insert(0, first)
    page = await keep_single_tab(ctx, "site.test")
    assert ctx.log[0] == "open", "the new tab must open before any close"
    assert not page.closed, "the new tab was closed"
    assert first.closed, "the leftover about:blank tab was kept: %r" % ctx.log
    assert "close:https://other.example/" not in ctx.log, "another site's tab was closed"
    assert page is not first and page.url == "about:blank"


# ---- type and send in a real Chrome (E12) ----------------------------------------------------------------------------
COMPOSER = """<!doctype html><title>composer</title>
<textarea id="t" rows="3" style="width:300px"></textarea><ul id="log"></ul>
<button id="b" style="position:fixed;bottom:8px;left:8px;width:80px;height:30px">Send</button>
<script>
const mode = new URLSearchParams(location.search).get('mode');
const t = document.getElementById('t'), log = document.getElementById('log');
function send() {
  const v = t.value.trim(); if (!v) return;
  const li = document.createElement('li'); li.textContent = v; log.appendChild(li); t.value = '';
}
t.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  document.body.dataset.enters = (+document.body.dataset.enters || 0) + 1;
  if (mode === 'slow') setTimeout(send, 4000);       // a slow connection: the input empties 4 s after Enter
  else if (mode !== 'button' && mode !== 'stuck') send();
});
// the click count lives in the DOM: evaluate() runs in an isolated world, a window variable is invisible to it
document.getElementById('b').addEventListener('click', () => {
  document.body.dataset.clicks = (+document.body.dataset.clicks || 0) + 1;
  if (mode !== 'stuck') send(); });
if (mode === 'untrusted') {      // an app that ignores programmatic input events (a controlled input)
  t.addEventListener('input', e => { if (!e.isTrusted) t.value = ''; });
}
</script>"""
SENT = "[...document.querySelectorAll('#log li')].map(l => l.textContent)"
KEYS = "A prompt that is long enough to be sure of it: a paper lantern floating over a lake"


async def sent_through(page, server, mode, prompt, **kw):
    await page.goto(server.url("/?mode=" + mode))
    await type_and_send(page, prompt, **kw)
    return await page.evaluate(SENT)


@pytest.mark.browser
@mutation(U, "if len(got) >= min(len(prompt), 40) and got[:40] == prompt[:40]:", "if True:",
          expect="prompt was not sent")
@mutation(U, '    prompt = " ".join((prompt or "").split())', '    prompt = prompt or ""',
          expect="more than one message")
@sync
async def test_the_prompt_is_typed_and_sent_once_in_one_line(tmp_path, token):
    async with SiteServer() as server:
        server.add("GET", "/", COMPOSER)
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            assert await sent_through(s.page, server, "enter", KEYS) == [KEYS], "the prompt was not sent"
            multi = await sent_through(s.page, server, "enter", "first line\nsecond line\n\nthird")
            assert multi == ["first line second line third"], \
                "a multi-line prompt became more than one message: %r" % multi
            # an app that ignores programmatic input: the fast path is not taken, keys are typed instead
            assert await sent_through(s.page, server, "untrusted", KEYS) == [KEYS], "the prompt was not sent"


@pytest.mark.browser
@mutation(U, 'or "") >= max(1, min(10, len(prompt))):', 'or "") > min(10, len(prompt)):',
          expect="short prompt was not sent")
@sync
async def test_a_short_prompt_is_typed_and_sent_when_the_fast_path_does_not_take(tmp_path, token):
    async with SiteServer() as server:
        server.add("GET", "/", COMPOSER)
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            for short in ("a cat", "0123456789"):          # up to 10 characters: the length check once could not pass
                try:
                    got = await sent_through(s.page, server, "untrusted", short)
                except UiError as err:
                    got = "UiError: %s" % err
                assert got == [short], "a short prompt was not sent: %r" % got


# A rich-text composer that keeps a draft and ignores programmatic input events: the fast path does not take, so the
# keys are typed, and only a real select-all replaces the draft (a textarea would be cleared by the focus step).
DRAFT_COMPOSER = """<!doctype html><title>composer</title>
<div id="c" contenteditable="true" style="width:300px;height:60px">old draft</div><ul id="log"></ul>
<script>
const c = document.getElementById('c'), log = document.getElementById('log');
const draft = c.textContent;
c.addEventListener('input', e => { if (!e.isTrusted) c.textContent = draft; });
c.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  const v = c.textContent.trim(); if (!v) return;
  const li = document.createElement('li'); li.textContent = v; log.appendChild(li); c.textContent = '';
});
</script>"""


@mutation(U, 'return "Meta+A" if platform == "darwin" else "Control+A"', 'return "Control+A"',
          expect="select-all on macOS must be Command+A")
def test_select_all_is_command_on_macos_and_control_elsewhere():
    assert select_all_key("darwin") == "Meta+A", "select-all on macOS must be Command+A"
    assert select_all_key("linux") == "Control+A" and select_all_key("win32") == "Control+A"


@pytest.mark.browser
@mutation(U, "            await page.keyboard.press(select_all_key())", "            pass",
          expect="leftover draft was not replaced")
@sync
async def test_a_leftover_draft_is_replaced_not_appended_to_when_the_keys_are_typed(tmp_path, token):
    async with SiteServer() as server:
        server.add("GET", "/", DRAFT_COMPOSER)
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await type_and_send(s.page, KEYS)
            sent = await s.page.evaluate(SENT)
    assert sent == [KEYS], "the leftover draft was not replaced: %r" % sent


COUNTS = "[+document.body.dataset.enters || 0, +document.body.dataset.clicks || 0]"
RAISE_STILL_FULL = '        raise UiError("prompt still in the input")'
CLICK_INSTEAD = """        await page.evaluate("document.getElementById('b').click()")"""


@pytest.mark.browser
@mutation(U, RAISE_STILL_FULL, CLICK_INSTEAD, expect="Send was clicked")
@sync
async def test_the_send_button_is_never_clicked_a_prompt_that_stays_is_one_enter_and_an_error(tmp_path, token):
    async with SiteServer() as server:
        server.add("GET", "/", COMPOSER)
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            for mode in ("button", "stuck"):          # the page ignores Enter; a Send click WOULD send it
                try:
                    await sent_through(s.page, server, mode, KEYS, clear_wait_s=1.0)
                    failure = None
                except UiError as err:
                    failure = str(err)
                counts = await s.page.evaluate(COUNTS)
                assert counts == [1, 0], "Send was clicked or Enter pressed again (%s): %r" % (mode, counts)
                assert await s.page.evaluate(SENT) == [], "Send was clicked: the page sent the prompt (%s)" % mode
                assert failure and "still in the input" in failure, "Send was clicked or wrong error: %r" % failure


@pytest.mark.browser
@sync
async def test_there_is_no_input_to_type_into(tmp_path, token):
    async with SiteServer() as server:
        server.add("GET", "/", "<title>no composer</title>")
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            with pytest.raises(UiError, match="could not type"):
                await type_and_send(s.page, KEYS, attempts=1)


@pytest.mark.browser
@sync
async def test_injected_cookies_replace_the_old_ones_in_a_real_context(tmp_path, token):
    jar = {"cookies": [{"name": "sessionid", "value": "SESS-NEW", "domain": "127.0.0.1", "session": True},
                       {"name": "uid_tt", "value": "UID-NEW", "domain": "127.0.0.1", "session": True}]}
    async with SiteServer() as server:
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            await s.ctx.add_cookies([{"name": "sessionid", "value": "SESS-OLD", "domain": "127.0.0.1", "path": "/"},
                                     {"name": "leftover", "value": "1", "domain": "127.0.0.1", "path": "/"}])
            assert await inject_cookies(s.ctx, jar, IDENTITY, server.url()) == ["sessionid", "uid_tt"]
            seen = {c["name"]: c["value"] for c in await s.ctx.cookies(server.url())}
    assert seen == {"sessionid": "SESS-NEW", "uid_tt": "UID-NEW"}, "an old cookie survived the injection: %r" % seen


@pytest.mark.browser
@mutation(U, "CLEAR_WAIT_S = 30.0 ", "CLEAR_WAIT_S = 0.0 ", expect="not exactly one message")
@sync
async def test_a_slow_connection_that_empties_the_input_late_gets_one_message(tmp_path, token):
    async with SiteServer() as server:
        server.add("GET", "/", COMPOSER)
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            try:
                got = await sent_through(s.page, server, "slow", KEYS)
            except UiError as err:
                got = "UiError: %s" % err
            counts = await s.page.evaluate(COUNTS)
    assert got == [KEYS] and counts == [1, 0], "not exactly one message from one Enter: %r %r" % (got, counts)


@pytest.mark.browser
@mutation(U, '    if before_enter is not None:\n        await before_enter()\n    await page.keyboard.press("Enter")\n',
          '    await page.keyboard.press("Enter")\n    if before_enter is not None:\n        await before_enter()\n',
          expect="after the typing and before Enter")
@mutation(U, "        await before_enter()\n", "        pass\n", expect="before_enter did not run")
@sync
async def test_before_enter_runs_after_the_typing_right_before_the_one_enter_and_can_stop_it(tmp_path, token):
    seen = []
    async with SiteServer() as server:
        server.add("GET", "/", COMPOSER)
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url("/?mode=enter"))

            async def hook():
                seen.append((await s.page.evaluate("document.getElementById('t').value"),
                             await s.page.evaluate(COUNTS)))

            async def refuse():
                raise RuntimeError("lease lost")

            await type_and_send(s.page, KEYS, before_enter=hook)
            assert seen == [(KEYS, [0, 0])], "before_enter did not run after the typing and before Enter: %r" % seen
            assert await s.page.evaluate(SENT) == [KEYS]
            with pytest.raises(RuntimeError, match="lease lost"):
                await type_and_send(s.page, "second", before_enter=refuse)
            assert await s.page.evaluate(COUNTS) == [1, 0] and await s.page.evaluate(SENT) == [KEYS], (
                "an Enter was pressed although before_enter refused")
