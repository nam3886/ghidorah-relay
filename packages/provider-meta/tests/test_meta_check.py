"""The account check's verdict and the engine's unbuilt parts. No Chrome: the page is a scripted stand-in (the real page
is read in test_meta_check_browser.py)."""

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from relay_core.providers import Cooldown, Dead, Suspect
from relay_core.testing.mutation import mutation
from relay_meta.engine import build_engine
from relay_meta.engine.check import check_account, read_signals, verdict
from relay_meta.spec import SPEC

CHECK_FILE = "packages/provider-meta/src/relay_meta/engine/check.py"
HOME = "https://www.meta.ai/"
IN = {"menu": True, "out": False, "name": "Nam"}
OUT = {"menu": False, "out": True, "name": ""}
NOTHING = {"menu": False, "out": False, "name": ""}


@mutation(CHECK_FILE, "        if any(mark in url for mark in LOGGED_OUT_URL_MARKS):", "        if False:",
          expect="a login URL")
@mutation(CHECK_FILE, '        if not (hostname == host or hostname.endswith("." + host)):', "        if False:",
          expect="a page off meta.ai")
def test_a_login_or_checkpoint_url_or_a_page_off_meta_ai_is_signed_out_whatever_the_page_shows():
    for url in ("https://www.meta.ai/login?next=/", "https://www.meta.ai/checkpoint/123"):
        assert verdict(url, "meta.ai", IN) == "signed_out", "a login URL: %s" % url
    for url in ("https://www.facebook.com/login.php", "https://evilmeta.ai/"):
        assert verdict(url, "meta.ai", IN) == "signed_out", "a page off meta.ai: %s" % url


@mutation(CHECK_FILE, '    if seen.get("menu") and not seen.get("out"):', '    if seen.get("menu"):',
          expect="both signs at once")
def test_the_signs_on_the_page_decide_and_both_or_neither_is_no_verdict():
    assert verdict(HOME, "meta.ai", IN) == "alive"
    assert verdict("https://meta.ai/", "meta.ai", IN) == "alive"
    assert verdict(HOME, "meta.ai", OUT) == "signed_out"
    assert verdict(HOME, "meta.ai", NOTHING) is None
    assert verdict(HOME, "meta.ai", {"menu": True, "out": True}) is None, "both signs at once"


@mutation(CHECK_FILE, '    if parts.scheme in ("http", "https"):', "    if True:", expect="a browser error page")
def test_a_browser_error_page_is_no_verdict_on_the_account():
    assert verdict("chrome-error://chromewebdata/", "meta.ai", NOTHING) is None, "a browser error page"
    assert verdict("about:blank", "meta.ai", NOTHING) is None, "a browser error page"


class ScriptedPage:
    def __init__(self, reads, url=HOME):
        self.reads, self.url = list(reads), url

    async def goto(self, url, **kw):
        pass

    async def screenshot(self, **kw):
        return b"png"

    async def evaluate(self, script, arg):
        read = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        if isinstance(read, Exception):
            raise read
        return read


def test_the_read_waits_through_a_navigation_for_a_sign_and_gives_up_as_unknown():
    page = ScriptedPage([RuntimeError("navigating"), NOTHING, IN])
    assert asyncio.run(read_signals(page, "meta.ai", 5)) == ("alive", "Nam")
    assert asyncio.run(read_signals(ScriptedPage([OUT]), "meta.ai", 5)) == ("signed_out", "")
    assert asyncio.run(read_signals(ScriptedPage([NOTHING]), "meta.ai", 0)) == ("unknown", "")


def test_submit_and_fetch_are_not_built_and_no_command_runs():
    engine = build_engine(SPEC, None)
    with pytest.raises(NotImplementedError, match="meta submit is not built yet"):
        asyncio.run(engine.submit(None, None))
    with pytest.raises(NotImplementedError, match="meta fetch is not built yet"):
        engine.fetch(None, None)
    for kind in ("titles", "recover", "refresh_catalog", "list_conversations"):
        with pytest.raises(ValueError, match="meta runs no command"):
            asyncio.run(engine.run_command(None, kind, {}))


# ---- check_account with a scripted page --------------------------------------------------------------------------
NOW = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)
JAR = json.dumps({"cookies": [{"name": "ecto_1_sess", "value": "v", "domain": ".meta.ai", "session": True}]}).encode()


class FakeCookies:
    def __init__(self):
        self.jar = []

    async def clear_cookies(self):
        self.jar = []

    async def add_cookies(self, cookies):
        self.jar = list(cookies)

    async def cookies(self, url):
        return self.jar


def run_check(page):
    @contextlib.asynccontextmanager
    async def fake_open(site, account, **kw):
        assert kw["purpose"] == "check"
        yield SimpleNamespace(page=page, ctx=FakeCookies())

    ctx = SimpleNamespace(account=SimpleNamespace(credential=JAR), browser_token=object())
    return asyncio.run(check_account(ctx, None, open_session=fake_open, wait_s=0, now=lambda: NOW))


def test_a_signed_in_page_is_alive_with_its_name_and_picture():
    health = run_check(ScriptedPage([IN]))
    assert (health.state, health.display_name, health.screenshot, health.effects) == ("alive", "Nam", b"png", ())


SIGNED_OUT_RETURN = '            return Health("unknown", screenshot=shot, effects=signed_out_effects(now()))'


@mutation(CHECK_FILE, SIGNED_OUT_RETURN, '            return Health("signed_out", screenshot=shot)',
          expect="one signed-out read must be a strike, not a verdict")
@mutation(CHECK_FILE, "now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))",
          "now + timedelta(seconds=SIGNED_OUT_REST_S)))", expect="the rest did not ask for a recheck")
def test_one_signed_out_read_is_a_spaced_strike_and_a_rest_never_a_dead_account():
    for page in (ScriptedPage([OUT]), ScriptedPage([NOTHING], url="https://www.meta.ai/checkpoint/1")):
        health = run_check(page)
        assert health.state == "unknown", "one signed-out read must be a strike, not a verdict: %s" % health.state
        assert [type(e) for e in health.effects] == [Suspect, Cooldown], health.effects
        strike, rest = health.effects
        assert strike.min_gap_s == 540 and rest.until == NOW + timedelta(seconds=600)
        assert rest.recheck, "the rest did not ask for a recheck"
        assert not any(isinstance(e, Dead) for e in health.effects) and health.screenshot == b"png"


def test_no_sign_is_an_unknown_read_with_no_strike():
    health = run_check(ScriptedPage([NOTHING]))
    assert (health.state, health.effects) == ("unknown", ())
