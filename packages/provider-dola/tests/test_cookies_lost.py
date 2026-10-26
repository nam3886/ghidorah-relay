"""Cookies the browser lost are a browser fault, not a guest verdict: no strike, no `signed_out`, the same rest as
a login doubt, and a send goes back to the queue without using an attempt. Only the identity cookies that were injected
into this browser can be lost. A browser that cannot be asked is a fault too. Scripted page, no Chrome."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from kit_c import NOW, FakeCtx, FakeDriver, FakeSession, Scene, account, deps, sync, types

from relay_core.providers import Cooldown, Dead, Retry, Success, Suspect
from relay_core.testing.mutation import mutation
from relay_dola import constants, errors
from relay_dola.engine import submit as submit_mod
from relay_dola.engine.check import check_account
from relay_dola.engine.submit import PageDriver
from relay_dola.engine.verify import lost_identity_cookies

CK = "packages/provider-dola/src/relay_dola/engine/check.py"
SUB = "packages/provider-dola/src/relay_dola/engine/submit.py"
VER = "packages/provider-dola/src/relay_dola/engine/verify.py"
ERR = "packages/provider-dola/src/relay_dola/errors.py"
GUEST = {"cells": 0, "ids": [], "name": "", "modal": False,
         "passport": {"message": "error", "uid": False, "name": "account_info_error"}}
LOST = "cookies lost in the browser"
BOTH = ["sessionid", "uid_tt"]
REST = timedelta(seconds=constants.LOGIN_DOUBT_REST_S)


async def check(**driver_kw):
    log = []
    driver = FakeDriver(log, None, **driver_kw)
    ctx = FakeCtx(log, account())
    return await check_account(ctx, ctx.settings, deps=deps(FakeSession(), driver))


@mutation(VER, "    return sorted(name for name in injected if name not in have)",
          "    return sorted(name for name in ('sessionid', 'uid_tt') if name not in have)",
          expect="a name that was never injected was reported lost")
@mutation(VER, "    return sorted(name for name in injected if name not in have)", "    return []",
          expect="a missing identity cookie was not reported")
@mutation(VER, "    if present is None:\n        return []", "    if present is None:\n        return sorted(injected)",
          expect="an unreadable browser was called a cookie loss")
def test_only_the_injected_identity_cookies_can_be_lost_and_an_unreadable_browser_loses_none():
    assert lost_identity_cookies({"sessionid", "uid_tt", "ttwid"}, BOTH) == []
    assert lost_identity_cookies({"uid_tt"}, BOTH) == ["sessionid"], "a missing identity cookie was not reported"
    assert lost_identity_cookies(set(), BOTH) == BOTH, "a missing identity cookie was not reported"
    assert lost_identity_cookies({"sessionid"}, ["sessionid"]) == [], \
        "a name that was never injected was reported lost"
    assert lost_identity_cookies(set(), []) == []
    assert lost_identity_cookies(None, BOTH) == [], "an unreadable browser was called a cookie loss"


@mutation(CK, "                if lost := lost_identity_cookies(present, injected):",
          "                if False and (lost := lost_identity_cookies(present, injected)):",
          expect="a guest read with lost cookies was judged")
@mutation(VER, 'return (Cooldown("cookies lost in the browser", now + timedelta(seconds=LOGIN_DOUBT_REST_S), '
               'recheck=True),)',
          'return (Cooldown("cookies lost in the browser", now + timedelta(seconds=LOGIN_DOUBT_REST_S)),)',
          expect="the cookie-loss rest did not ask for a recheck")
@sync
async def test_a_check_that_finds_the_cookies_gone_is_a_browser_fault_with_the_login_doubt_rest_and_no_strike():
    health = await check(probe=GUEST, cookie_names=set())
    assert health.state == "unknown", "a guest read with lost cookies was judged: %s" % health.state
    assert [type(e) for e in health.effects] == [Cooldown], "a guest read with lost cookies was judged: %r" % (
        health.effects,)
    rest = health.effects[0]
    assert rest.reason == LOST and rest.until == NOW + REST, "the cookie-loss rest is not the login doubt rest"
    assert rest.recheck, "the cookie-loss rest did not ask for a recheck"
    assert not any(isinstance(e, (Dead, Suspect)) for e in health.effects)
    part = await check(probe=GUEST, cookie_names={"uid_tt"})
    assert part.state == "unknown" and [type(e) for e in part.effects] == [Cooldown], \
        "a guest read with one identity cookie missing was judged"


@sync
async def test_a_jar_with_only_a_session_cookie_whose_session_reads_guest_is_a_login_doubt_not_a_cookie_loss():
    # `sessionid` alone is a legal jar: `uid_tt` was never injected, so its absence says nothing
    health = await check(probe=GUEST, injected=["sessionid"], cookie_names={"sessionid"})
    assert [type(e) for e in health.effects] == [Suspect, Cooldown], "a never-injected cookie was called lost"
    assert health.effects[1].reason != LOST
    lost = await check(probe=GUEST, injected=["sessionid"], cookie_names=set())
    assert [type(e) for e in lost.effects] == [Cooldown] and lost.effects[0].reason == LOST


UNREADABLE = ('                    return Health("unknown", screenshot=shot, '
              'effects=(_browser_fault("cookies not readable", deps),))')


@mutation(CK, UNREADABLE,
          '                    return Health("unknown", screenshot=shot,\n'
          '                                  effects=login_doubt_effects(deps.now(), state.reason))',
          expect="an unreadable cookie jar was struck")
@sync
async def test_a_browser_that_cannot_be_asked_for_its_cookies_is_a_short_rest_and_no_strike():
    health = await check(probe=GUEST, cookie_names=None)
    assert health.state == "unknown" and [type(e) for e in health.effects] == [Cooldown], \
        "an unreadable cookie jar was struck: %r" % (health.effects,)
    assert health.effects[0].until == NOW + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S)
    assert not health.effects[0].recheck


@mutation(SUB, "            if lost:\n                return self._cookies_lost(lost)\n", "",
          expect="a guest read with lost cookies killed or struck the account")
@mutation(ERR, "    _ui_retry(BROWSER_COOKIES_LOST,", "    _ui(BROWSER_COOKIES_LOST,",
          expect="a cookie loss used an attempt")
@sync
async def test_a_send_that_finds_the_cookies_gone_goes_back_to_the_queue_with_no_attempt_and_no_strike():
    scene = Scene(probe=GUEST, cookie_names=set())
    res = await scene.run()
    assert res.job == Retry(errors.BROWSER_COOKIES_LOST), \
        "a guest read with lost cookies killed or struck the account: %r %r" % (res.job, types(res))
    assert types(res) == ["Cooldown"] and res.account[0].reason == LOST
    assert res.account[0].until == NOW + REST and res.account[0].recheck
    assert "send" not in scene.log and "build" not in scene.log, "nothing may be sent"
    assert errors.CODES[errors.BROWSER_COOKIES_LOST].counts_attempt is False, "a cookie loss used an attempt"


@sync
async def test_a_send_with_the_cookies_in_place_keeps_the_login_doubt_handling():
    res = await Scene(probe=GUEST, cookie_names={"sessionid", "uid_tt"}).run()
    assert res.job == Retry("pool.account_logged_out")
    assert types(res) == ["Suspect", "Cooldown"] and res.account[1].reason != LOST


@sync
async def test_a_send_cannot_ask_the_browser_for_its_cookies_gets_a_short_rest_and_no_strike():
    res = await Scene(probe=GUEST, cookie_names=None).run()
    assert res.job == Retry(errors.BROWSER_COOKIES_LOST) and types(res) == ["Cooldown"]
    assert res.account[0].until == NOW + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S)
    assert not res.account[0].recheck


class HangingCtx:
    """A browser context whose cookie read never answers, or raises."""

    def __init__(self, mode):
        self.mode = mode

    async def cookies(self, url):
        if self.mode == "raises":
            raise RuntimeError("target closed")
        await asyncio.sleep(30)


@mutation(SUB, "            cookies = await asyncio.wait_for(session.ctx.cookies(BASE), COOKIES_TIMEOUT_S)",
          "            cookies = await session.ctx.cookies(BASE)", expect="a hung browser held the run")
@sync
async def test_a_cookie_read_that_hangs_or_raises_is_unreadable_not_a_conclusion(monkeypatch):
    monkeypatch.setattr(submit_mod, "COOKIES_TIMEOUT_S", 0.05)
    for mode in ("raises", "hangs"):
        try:
            got = await asyncio.wait_for(PageDriver().identity_cookie_names(SimpleNamespace(ctx=HangingCtx(mode))), 5)
        except TimeoutError:
            raise AssertionError("a hung browser held the run") from None
        assert got is None, "an unreadable browser gave a conclusion: %r" % (got,)


RUN_LINE = "        outcome = run.with_login_read(await run.run())"
FAIL_LINE = "        return run.with_login_read(await run.failed(exc))"


CLEARS = "a logged-in read did not clear the strike count"


@mutation(SUB, RUN_LINE, "        outcome = await run.run()", expect=CLEARS)
@mutation(SUB, FAIL_LINE, "        return await run.failed(exc)", expect=CLEARS)
@sync
async def test_a_send_that_read_the_account_logged_in_clears_the_strike_count_even_when_it_goes_back_to_the_queue():
    from relay_dola.engine import composer
    composer_error = composer.ComposerError("model selection did not take", errors.UI_MODEL_SELECT_FAILED)
    for scene in (Scene(build_error=composer_error), Scene(build_error=RuntimeError("chip row did not appear"))):
        res = await scene.run()
        assert isinstance(res.job, Retry) and types(res) == ["Success"], \
            "a logged-in read did not clear the strike count: %r %r" % (res.job, types(res))


@sync
async def test_a_login_doubt_is_never_cleared_and_a_taken_order_carries_one_success():
    from kit_c import ACCEPTED
    res = await Scene(probe=GUEST).run()
    assert "Success" not in types(res), "a guest read cleared the strike count"
    res = await Scene(replies={1: [ACCEPTED]}).run()
    assert types(res).count("Success") == 1, types(res)


FREE_RULE = "free_retries=BROWSER_FAULT_FREE_RETRIES)"
@mutation(ERR, FREE_RULE, "free_retries=0)", expect="a browser-fault code lost its free retries")
def test_both_browser_fault_codes_use_no_attempt_for_the_first_three_retries_of_a_job():
    for code in (errors.BROWSER_COOKIES_LOST, errors.PAGE_RELOADED):
        rule = errors.CODES[code]
        assert rule.counts_attempt is False and not rule.terminal and not rule.refund, code
        assert rule.free_retries == constants.BROWSER_FAULT_FREE_RETRIES == 3, \
            "a browser-fault code lost its free retries: %s" % code


CLEAR_GUARD = "        if not self.logged_in or any(isinstance(e, Success) for e in outcome.account):"


@mutation(SUB, CLEAR_GUARD, "        if True:", expect="a logged-in read did not clear the strike count")
@sync
async def test_a_logged_in_read_clears_the_strike_count_whatever_the_send_ends_as():
    from kit_c import COPYRIGHT, DAILY_LIMIT
    cases = {
        "a refusal (Fail)": Scene(replies={1: [COPYRIGHT]}),
        "an unknown order (Ambiguous)": Scene(send_error=RuntimeError("Page.goto: net::ERR_TIMED_OUT")),
        "a daily limit (NotTaken)": Scene(replies={1: [DAILY_LIMIT]}),
    }
    for what, scene in cases.items():
        res = await scene.run()
        assert Success() in res.account, \
            "a logged-in read did not clear the strike count on %s: %r" % (what, res.account)
