"""The account check and the proxy probe (v4 `account._demo`, `alive._demo`, `session._demo`, `verify._demo`,
`proxy_manager._demo`: coverage ACC00, ALIVE00, SESS03, VERIFY02, PROXY02). No Chrome, no network: the page is a
scripted driver, the HTTP transport an in-process fake."""

import asyncio
import threading
from datetime import timedelta

import pytest
from kit_c import GOOD_PROBE, NOW, FakeCtx, FakeDriver, FakeSession, account, deps, sync

from relay_core.providers import Cooldown, Dead, Health, Proxy, ProxyBad, Suspect
from relay_core.testing.mutation import mutation
from relay_dola.engine import verify
from relay_dola.engine.check import check_account, identity_of, probe_proxy
from relay_dola.engine.verify import LoginState, login_state, read_passport

CK = "packages/provider-dola/src/relay_dola/engine/check.py"
USER_ID = "7676034022275056645"
PASSPORT = {"message": "success", "user_id": USER_ID, "name": "Nam Phuong339"}
GUEST = {"cells": 0, "ids": [], "name": "", "modal": False,
         "passport": {"message": "error", "uid": False, "name": "account_info_error"}}


async def check(**driver_kw):
    log = []
    session = FakeSession()
    driver = FakeDriver(log, None, **driver_kw)
    ctx = FakeCtx(log, account())
    calls = []
    d = deps(session, driver, calls=calls)
    return await check_account(ctx, ctx.settings, deps=d), log, calls


# ---- the identity (ACC00) ------------------------------------------------------------------------------------------
def test_the_identity_is_the_passport_user_id_as_a_string_and_the_name_is_read():
    assert identity_of(PASSPORT) == (USER_ID, "Nam Phuong339")
    assert identity_of({"message": "success", "user_id": 123, "name": "x"}) == ("123", "x"), \
        "a numeric id must come back as a string"


@mutation(CK, '    if not isinstance(passport, dict) or passport.get("message") != "success" or '
               'not passport.get("user_id"):',
          '    if not isinstance(passport, dict) or not passport.get("user_id"):', expect="guest")
def test_a_guest_or_a_failed_call_gives_no_identity_and_never_raises():
    # a guest tab answers HTTP 200 with message "error" and the NAME "account_info_error": an error text, not a name
    assert identity_of({"message": "error", "user_id": "", "name": "account_info_error"}) == (None, ""), "guest"
    assert identity_of({"message": "error", "user_id": "55", "name": "account_info_error"}) == (None, ""), "guest"
    assert identity_of(None) == (None, "") and identity_of({"err": "TypeError: Failed to fetch"}) == (None, "")
    assert identity_of({"message": "success", "user_id": "", "name": "x"}) == (None, "")


# ---- the login probe (VERIFY02, SESS03) ------------------------------------------------------------------------------
def test_the_login_probe_reports_signed_in_signed_out_or_unknown():
    assert LoginState(5, "Thu Ung718", False).logged_in
    assert not LoginState(5, "Thu Ung718", True).logged_in, "a login dialog means not in, whatever recent_conv returned"
    assert not LoginState(0, "", False).logged_in, "a guest: a valid but empty answer and no name"
    assert LoginState(0, "Nam Phuong339", False).logged_in, "a brand-new account with no conversation but a name"
    assert not LoginState(-1, "", False).logged_in, "a failed call is no proof of being in"


def test_dola_saying_outright_that_the_session_is_nobodys_is_a_signed_out_account():
    dead = LoginState(0, "", False, session="guest")
    assert not dead.logged_in and dead.signed_out and "signed out" in dead.reason, dead.reason
    # a call that failed midway, or conversations that exist, is a doubt: never a verdict
    assert not LoginState(0, "", False, session="unknown").signed_out
    assert not LoginState(-1, "", False, session="guest").signed_out
    assert not LoginState(5, "", False, session="guest").signed_out
    assert "could not read account name" in LoginState(0, "", False).reason


def test_passport_shapes_measured_on_23_09_are_read_right():
    guest = {"message": "error", "uid": False, "name": "account_info_error"}
    joy = {"message": "success", "uid": True, "name": "Joy IELTS"}
    assert read_passport(guest) == ("guest", ""), "a guest tab was read as an account named account_info_error"
    assert read_passport(joy) == ("user", "Joy IELTS")
    assert read_passport(None) == ("unknown", "") and read_passport({"err": "x"}) == ("unknown", "")


def test_an_account_whose_name_does_not_end_in_three_digits_is_not_declared_dead():
    # the old name heuristic read an empty name for 6 of 13 accounts (Joy IELTS, Huy76...), and an account with no
    # conversation yet was declared dead; the passport answers who the cookie is
    state = login_state({"cells": 0, "ids": [], "name": "", "modal": False,
                         "passport": {"message": "success", "uid": True, "name": "Joy IELTS"}})
    assert state.logged_in and state.account_name == "Joy IELTS" and not state.signed_out
    guest = login_state({"cells": 0, "ids": [], "name": "", "modal": False, "passport": GUEST["passport"]})
    assert not guest.logged_in and guest.account_name == "" and guest.signed_out


def test_the_probe_script_asks_the_right_endpoints():
    for token in ("recent_conv", "phone_login_entry_title_cn", "credentials:'include'", "ids", "conversation_id",
                  "/passport/account/info/v2/", "passport}"):
        assert token in verify.LOGIN_PROBE_JS, token


# ---- check_account ---------------------------------------------------------------------------------------------------
@sync
async def test_a_live_account_is_alive_with_its_identity_name_picture_and_composer():
    health, log, calls = await check(passport=PASSPORT, kind="new")
    assert health == Health("alive", identity=USER_ID, display_name="Nam Phuong339", screenshot=b"png-bytes",
                            state_patch={"composer": "new"})
    assert calls[0][1]["purpose"] == "check" and "send" not in log and "mark_sent" not in log, "a check sends nothing"


@sync
async def test_the_composer_ui_is_recorded_old_or_new_and_left_out_when_unreadable():
    assert (await check(passport=PASSPORT, kind="old"))[0].state_patch == {"composer": "old"}
    assert (await check(passport=PASSPORT, kind=None))[0].state_patch == {}, (
        "an unreadable composer must not be guessed"
    )


@sync
async def test_a_new_account_with_no_conversation_but_a_name_is_alive():
    joy = {"message": "success", "uid": True, "name": "Joy"}
    probe = {"cells": 0, "ids": [], "name": "", "modal": False, "passport": joy}
    health = (await check(probe=probe, passport={"message": "success", "user_id": "9", "name": "Joy"}))[0]
    assert health.state == "alive" and health.identity == "9"


@mutation(CK, "            if state.signed_out:", "            if False:", expect="positive proof")
@sync
async def test_a_dead_cookie_is_signed_out_only_on_positive_proof():
    guest = {"message": "error", "user_id": "", "name": "account_info_error"}
    health, log, _ = await check(probe=GUEST, passport=guest)
    assert health.state == "signed_out", "positive proof (guest passport and no conversation) must say signed out"
    assert health.effects == (Dead("signed_out"),) and health.identity is None and health.screenshot == b"png-bytes"


@sync
async def test_a_doubt_is_unknown_with_a_strike_for_a_dialog_and_none_for_a_failed_read():
    dialog = (await check(probe={**GOOD_PROBE, "modal": True}))[0]
    assert dialog.state == "unknown" and [type(e) for e in dialog.effects] == [Suspect], (
        "a dialog is a strike, not death"
    )
    failed = (await check(probe={"cells": -1, "ids": [], "name": "", "modal": False, "passport": None}))[0]
    assert failed.state == "unknown" and failed.effects == (), "a read that did not answer is not even a strike"


@mutation(CK, "            if REGION_BLOCK_MARK in await driver.page_url(page):", "            if False:",
          expect="region")
@sync
async def test_a_region_page_is_the_proxys_trouble_and_the_account_is_not_judged():
    health, log, _ = await check(url="https://www.dola.com/security/region-restricted", passport=PASSPORT)
    assert health.state == "unknown" and [type(e) for e in health.effects] == [ProxyBad, Cooldown], "region: the proxy"
    assert health.effects[1].until == NOW + timedelta(seconds=1800)
    assert health.screenshot is None and health.identity is None


@sync
async def test_a_network_error_is_the_proxys_and_any_other_crash_reaches_the_core():
    health, _, _ = await check(open_error=RuntimeError("Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED"))
    assert health.state == "unknown" and [type(e) for e in health.effects] == [ProxyBad, Cooldown]
    with pytest.raises(RuntimeError, match="boom"):          # a crashing check is recorded by the core, never swallowed
        await check(open_error=RuntimeError("boom"))


# ---- the proxy probe (PROXY02) ---------------------------------------------------------------------------------------
class Resp:
    def __init__(self, status=200, body=b""):
        self.status_code, self.content = status, body


class Transport:
    """Answers ipify and dola's chat page; remembers the proxies it was given and the thread it ran on."""

    def __init__(self, ip=b"103.1.2.3", chat=None, error=None, ip_error=None):
        self.ip, self.error, self.ip_error = ip, error, ip_error
        self.chat = chat or Resp(200, b"<html>Dola chat</html>")
        self.proxies, self.threads, self.urls = [], set(), []

    def get(self, url, *, headers, timeout, proxies):
        self.proxies.append(proxies)
        self.urls.append(url)
        self.threads.add(threading.get_ident())
        if "ipify" in url:
            if self.ip_error:
                raise self.ip_error
            return Resp(200, self.ip)
        if self.error:
            raise self.error
        return self.chat

    def post(self, *a, **k):
        raise AssertionError("a proxy probe makes no POST")


PROXY = Proxy("103.1.2.3", 3129, "user1:pass1")


@sync
async def test_a_proxy_that_reaches_dola_is_usable_and_the_exit_ip_is_reported():
    t = Transport()
    verdict = await probe_proxy(PROXY, transport=t)
    assert verdict.ok and "103.1.2.3" in verdict.reason
    assert t.urls == ["https://api.ipify.org", "https://www.dola.com/chat"]
    assert all(p == {"http": "http://user1:pass1@103.1.2.3:3129", "https": "http://user1:pass1@103.1.2.3:3129"}
               for p in t.proxies), "the probe did not go through the proxy"


@mutation(CK, "    if any(mark in low for mark in REGION_BLOCKED_MARKERS):", "    if False:", expect="blocked exit")
@sync
async def test_the_region_probe_rejects_a_blocked_exit():
    blocked = Transport(chat=Resp(302, b"Redirecting to /security/region-restricted"))
    verdict = await probe_proxy(PROXY, transport=blocked)
    assert not verdict.ok and "region" in verdict.reason, "a blocked exit was accepted: the proxy may not be used"


@mutation(CK, "    if status >= 500:", "    if False:", expect="5xx")
@sync
async def test_dola_answering_5xx_through_the_proxy_rejects_it():
    verdict = await probe_proxy(PROXY, transport=Transport(chat=Resp(503, b"")))
    assert not verdict.ok and "503" in verdict.reason, "a 5xx answer was accepted (5xx)"


@sync
async def test_a_proxy_that_cannot_connect_is_rejected_and_its_login_never_leaks():
    leaky = OSError("cannot reach http://user1:pass1@103.1.2.3:3129")
    verdict = await probe_proxy(PROXY, transport=Transport(error=leaky))
    assert not verdict.ok and "could not connect" in verdict.reason
    assert "pass1" not in verdict.reason, "the proxy password reached the verdict"


@sync
async def test_the_exit_ip_is_a_bonus_and_dolas_answer_decides():
    verdict = await probe_proxy(PROXY, transport=Transport(ip_error=OSError("ipify is down")))
    assert verdict.ok and "unknown" in verdict.reason


@sync
async def test_the_probe_runs_off_the_event_loop_thread():
    t = Transport()
    await probe_proxy(PROXY, transport=t)
    assert threading.get_ident() not in t.threads, "a blocking HTTP call ran on the event loop"
    assert asyncio.iscoroutinefunction(probe_proxy)
