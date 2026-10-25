"""The account check's verdict, the browser checkpoint and the engine's commands. No Chrome: the page is a scripted
stand-in (the real page is read in test_meta_check_browser.py)."""

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from relay_core.providers import Cooldown, Dead, Suspect
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_meta.engine import build_engine
from relay_meta.engine.check import check_account, read_signals, verdict
from relay_meta.spec import SPEC

CHECK_FILE = "packages/provider-meta/src/relay_meta/engine/check.py"
CHECKPOINT_CAPTURE = Path(__file__).resolve().parents[3] / "fixtures" / "meta" / "spike-261023" / "checkpoint-acc1"
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


def test_no_command_runs():
    engine = build_engine(SPEC, None)
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


def run_check(page, account_id=None):
    @contextlib.asynccontextmanager
    async def fake_open(site, account, **kw):
        assert kw["purpose"] == "check"
        yield SimpleNamespace(page=page, ctx=FakeCookies())

    account = SimpleNamespace(id=account_id or uuid.uuid4(), credential=JAR, provider_state={})
    ctx = SimpleNamespace(account=account, browser_token=object())
    settings = FakeSettings(SPEC, meta__checkpoint_rest_s=1500, meta__checkpoint_rest_max_s=5000)
    return asyncio.run(check_account(ctx, settings, open_session=fake_open, wait_s=0, now=lambda: NOW))


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


# ---- the browser checkpoint ------------------------------------------------------------------------------------------
CHALLENGE = {"menu": False, "out": False, "name": "", "title": "Vercel Security Checkpoint", "failed": False}


def checkpoint_read() -> dict:
    """What SIGNALS_JS reads on the captured checkpoint page: its title and its failure sentence."""
    loaded = json.loads((CHECKPOINT_CAPTURE / "loaded.state.json").read_text())
    return {"menu": loaded["signals"]["user_menu_visible"], "out": False, "name": "", "title": loaded["state"]["title"],
            "failed": "Failed to verify your browser" in loaded["page_text"]}


@mutation(CHECK_FILE, '    if seen.get("failed"):', "    if False:", expect="the checkpoint page")
@mutation(CHECK_FILE, '            return "checkpoint" if seen.get("title") == CHECKPOINT_TITLE else "unknown", ""',
          '            return "unknown", ""', expect="a challenge that never passed")
def test_the_captured_checkpoint_page_is_a_checkpoint_and_a_challenge_that_passes_is_not():
    read = checkpoint_read()
    assert read["failed"] and read["title"] == "Vercel Security Checkpoint" and not read["menu"]
    assert verdict(HOME, "meta.ai", read) == "checkpoint", "the checkpoint page is neither signed in nor signed out"
    assert verdict(HOME, "meta.ai", CHALLENGE) is None, "a challenge still running decides nothing"
    assert asyncio.run(read_signals(ScriptedPage([CHALLENGE, IN]), "meta.ai", 5)) == ("alive", "Nam")
    assert asyncio.run(read_signals(ScriptedPage([CHALLENGE]), "meta.ai", 0)) == ("checkpoint", ""), (
        "a challenge that never passed")


@mutation(CHECK_FILE, "    rest_s = CHECKPOINTS.next_s(account_id, settings.get(\"meta.checkpoint_rest_s\"),",
          "    rest_s = RepeatRest().next_s(account_id, settings.get(\"meta.checkpoint_rest_s\"),",
          expect="a repeat rests longer")
def test_a_checkpoint_read_rests_with_a_recheck_never_a_strike_or_death_and_a_repeat_rests_longer():
    account = uuid.uuid4()
    reads = [run_check(ScriptedPage([checkpoint_read()]), account) for _ in range(4)]
    rests = [h.effects for h in reads]
    assert rests[0] == (Cooldown("browser checkpoint (seen by a check), to be checked again in 1500 s",
                                 NOW + timedelta(seconds=1500), recheck=True),), rests[0]
    assert [r[0].until - NOW for r in rests] == [timedelta(seconds=s) for s in (1500, 3000, 5000, 5000)], (
        "a repeat rests longer, up to the max: %r" % ([r[0].until for r in rests],))
    assert all(h.state == "unknown" and h.screenshot == b"png" and len(h.effects) == 1 for h in reads)
    assert not any(isinstance(e, Suspect | Dead) for r in rests for e in r), "never a strike, never dead"
    other = run_check(ScriptedPage([checkpoint_read()]))
    assert other.effects[0].until == NOW + timedelta(seconds=1500), "another account starts at the base rest"


# ---- Vercel's challenge pass is kept, never the session ------------------------------------------------------------
class ChallengedCookies(FakeCookies):
    """The read-back after the injection sees the jar as given; later reads see what a load leaves (measured 25/10):
    a rotated `ecto_1_sess` and Vercel's `rd_challenge`."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    async def cookies(self, url):
        self.reads += 1
        if self.reads == 1:
            return self.jar
        return [{**c, "value": "v-rotated", "expires": 1_900_000_000} for c in self.jar] + [
            {"name": "rd_challenge", "value": "rc-pass", "domain": ".meta.ai", "path": "/", "expires": 1_900_000_000}]


def run_challenged(page):
    writes = []

    async def renew_credential(blob, merge=None):
        writes.append({c["name"]: c["value"] for c in json.loads(blob)["cookies"]})
        return blob

    @contextlib.asynccontextmanager
    async def fake_open(site, account, **kw):
        yield SimpleNamespace(page=page, ctx=ChallengedCookies())

    ctx = SimpleNamespace(account=SimpleNamespace(id=uuid.uuid4(), credential=JAR, provider_state={}),
                          browser_token=object(), renew_credential=renew_credential)
    health = asyncio.run(check_account(ctx, FakeSettings(SPEC, meta__checkpoint_rest_s=1500), open_session=fake_open,
                                       wait_s=0, now=lambda: NOW))
    return health, writes


KEEP_PASS = "            await keep_challenge_pass(session.ctx, ctx)\n"


@mutation(CHECK_FILE, KEEP_PASS, "", expect="challenge pass")
@mutation(CHECK_FILE, "only=CHALLENGE_COOKIES, domains=CHALLENGE_DOMAINS,", "domains=CHALLENGE_DOMAINS,",
          expect="session")
def test_a_signed_in_check_keeps_vercels_challenge_pass_and_never_the_rotated_session():
    health, writes = run_challenged(ScriptedPage([IN]))
    assert health.state == "alive"
    assert writes, "the challenge pass was not written back"
    assert writes == [{"ecto_1_sess": "v", "rd_challenge": "rc-pass"}], "the rotated session was written: %s" % writes
    _, writes = run_challenged(ScriptedPage([OUT]))
    assert writes == [], "a signed-out read was written back"
