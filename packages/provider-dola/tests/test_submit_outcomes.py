"""The submit outcome mapping and the money/lifecycle proofs, on a scripted page: no Chrome.

Every row of the submit outcome table lives here as the `SubmitResult` + account effects it became.
The order of the irreversible steps (`mark_sent`, Enter, `fence`) is read from one shared log, so a test can say "this
came before that"."""

import asyncio
import dataclasses
import json
from datetime import timedelta

import pytest
from kit_c import (
    ACCEPTED,
    CONV,
    COPYRIGHT,
    DAILY_LIMIT,
    GATE,
    GOOD_PROBE,
    LIKENESS,
    MARKER,
    NOT_ENOUGH,
    NOW,
    PROMPT,
    SENT,
    SOMETHING_WRONG,
    Scene,
    SendPage,
    account,
    chain_body,
    effects_of,
    fail_own_reads,
    lease_lost,
    make_job,
    media_ref,
    sync,
    types,
)

from relay_browser.session import BrowserLaunchError
from relay_core import frames
from relay_core.errors import rule
from relay_core.providers import (
    REFUSED_BY,
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Fail,
    NotTaken,
    ProxyBad,
    Rest,
    Retry,
    Success,
)
from relay_core.testing.mutation import mutation
from relay_dola import constants, errors
from relay_dola.engine import composer
from relay_dola.engine.submit import (
    REPLY_TRIES,
    OrderSpec,
    PageDriver,
    TrafficWatch,
    is_upload_commit,
    may_carry_order,
    order_spec,
    sse_message,
    stream_error,
    submit,
)

SUB = "packages/provider-dola/src/relay_dola/engine/submit.py"
LIKENESS_RIGHT = '                detail = "dola refused this order (likeness protection)"'
LIKENESS_WRONG = "                detail = verdict.detail"
CAPTURE_RIGHT = 'CAPTURE_PATHS = (r"^/im/chain/single$", r"^/chat/completion$")'
CAPTURE_WRONG = 'CAPTURE_PATHS = (r"^/im/chain/single$",)'
MIDNIGHT = NOW.replace(day=6, hour=0, minute=0)
COOLDOWN = NOW + timedelta(seconds=1800)
NET_ERROR = RuntimeError("Page.goto: net::ERR_TIMED_OUT at https://www.dola.com/chat")

OLD_GATE_AND_ACCEPT = ACCEPTED + " Would you like me to generate Part 1 only, Part 2 only, or both?"
GUEST = {"cells": 0, "ids": [], "name": "", "modal": False,
         "passport": {"message": "error", "uid": False, "name": "account_info_error"}}


def kinds(log):
    """The log without the steps that do not matter for ordering."""
    return [x for x in log if x in ("mark_sent", "send", "fence", "rebuild", "attach")]


# ---- the taken order ------------------------------------------------------------------------------------------------
@sync
async def test_a_taken_order_is_accepted_with_what_dola_stated():
    scene = Scene(replies={1: [ACCEPTED]})
    res = await scene.run()
    assert res.job == Accepted(CONV, facts=res.job.facts, taken=True)
    assert (res.job.facts.upstream_price, res.job.facts.upstream_model) == (2, "Dreamina Seedance 2.5")
    assert res.account == (Success(), Balance("video", 2))
    assert res.warnings == () and scene.ctx.refs == [(CONV, {})], "the conversation was not reported as the job's ref"
    assert scene.driver.sent == [SENT], "the engine must type the core's sent_text and nothing else"


@sync
async def test_an_order_sent_with_a_frame_that_moves_the_tracking_code_is_still_one_send_and_taken():
    """The receipt is judged on the text the frame produced, wherever the frame puts the code and whatever fixed words
    it starts with."""
    for frame in ("{{tracking_code}} {{client_prompt}}", "Fixed words first. {{tracking_code}} {{client_prompt}}",
                  "Generate it as one video: {{client_prompt}} {{tracking_code}} Keep it cinematic."):
        scene = Scene(job=make_job(frame=frame), replies={1: [ACCEPTED]})
        res = await scene.run()
        assert res.job == Accepted(CONV, facts=res.job.facts, taken=True), "frame %r: %r" % (frame, res.job)
        assert scene.driver.sent == [frames.render(frame, PROMPT, MARKER)], "the engine typed another text"
        assert scene.log.count("send") == 1, "frame %r sent more than once: %r" % (frame, kinds(scene.log))


SEND_CALL = "        await self.driver.send(self.page, self.spec.sent_text, before_enter=self._mark_sent)\n"
MARK_SENT_AFTER = ("        await self.driver.send(self.page, self.spec.sent_text, "
                   "before_enter=self._flush_steps)\n        await self._mark_sent()\n")
FROZEN_AT_SEND = ("        try:\n    " + SEND_CALL + "        finally:\n            self.traffic.freeze()\n")
FLUSH_FIRST = ("        await self._flush_steps()          # before mark_sent: nothing may come between mark_sent "
               "and the keypress\n"
               "        await self._core(self.ctx.mark_sent)\n        self.sent = True\n")
FLUSH_AFTER = ("        await self._core(self.ctx.mark_sent)\n        self.sent = True\n"
               "        await self._flush_steps()\n")


@mutation(SUB, SEND_CALL, MARK_SENT_AFTER, expect="mark_sent must come before the send")
@sync
async def test_mark_sent_is_recorded_immediately_before_enter_never_after():
    scene = Scene(replies={1: [ACCEPTED]})
    await scene.run()
    assert "mark_sent" in scene.log and scene.log.index("mark_sent") < scene.log.index("send"), (
        "mark_sent must come before the send (Enter)"
    )
    # everything that can still fail and retry cheaply happens BEFORE the marker
    assert scene.log[:scene.log.index("mark_sent")] == ["inject", "open_chat", "build", "flush"]
    assert scene.log[scene.log.index("mark_sent"):][:2] == ["mark_sent", "send"], (
        "something runs between marker and Enter"
    )


@mutation(SUB, FLUSH_FIRST, FLUSH_AFTER, expect="flush before mark_sent")
@sync
async def test_the_steps_are_flushed_before_mark_sent_so_nothing_runs_between_it_and_enter():
    scene = Scene(replies={1: [ACCEPTED]})
    await scene.run()
    at = scene.log.index("mark_sent")
    assert scene.log[at - 1] == "flush" and scene.log[at + 1] == "send", (
        "flush before mark_sent, then straight to Enter: %r" % scene.log[:at + 2]
    )


@sync
async def test_the_browser_is_opened_for_a_submit_with_the_cores_token_the_30s_patch_and_the_redactor():
    scene = Scene(replies={1: [ACCEPTED]})
    await scene.run()
    (site, kw), = scene.calls
    assert kw["purpose"] == "submit" and kw["token"] is scene.ctx.browser_token and kw["images"] is False
    assert [p.url_pattern for p in kw["response_patches"]] == ["*action_bar_v3/get_item_conf*"]
    assert callable(kw["redact"]) and site.name == "dola"
    assert site.viewport is None and site.locale == "en-US", "video keeps the browser's default viewport"


@sync
async def test_a_native_duration_needs_no_patch_and_an_image_gets_the_wide_viewport():
    scene = Scene(job=make_job(duration=10), replies={1: [ACCEPTED]}, patched=0)
    res = await scene.run()
    assert isinstance(res.job, Accepted), "a 10 s order needs no patch: 0 patches must not stop it"
    assert scene.calls[0][1]["response_patches"] == ()
    image = Scene(job=make_job("image"), replies={1: ["Done."]})
    await image.run()
    site, kw = image.calls[0]
    assert site.viewport == (1440, 900) and kw["response_patches"] == (), "the image chip row needs 1440x900"


@mutation(SUB, "        if spec.needs_patch and self.session.patched == 0:\n            # No 30 s level",
          "        if False:\n            # No 30 s level", expect="0 patches applied")
@sync
async def test_zero_patches_applied_stops_a_30_second_order_before_anything_is_sent():
    scene = Scene(patched=0, replies={1: [ACCEPTED]})
    res = await scene.run()
    assert res.job == Retry(errors.UI_DURATION_PATCH_FAILED), "0 patches applied must be a retry, never a send"
    assert "mark_sent" not in scene.log and "send" not in scene.log, "an order was sent with no 30 s level"


@sync
async def test_two_orders_in_a_row_until_the_account_is_exhausted():
    first = Scene(replies={1: [ACCEPTED]})
    assert (await first.run()).account == (Success(), Balance("video", 2))
    last = Scene(replies={1: [ACCEPTED.replace("2 video credits left", "0 video credits left")]})
    res = await last.run()
    assert isinstance(res.job, Accepted)
    assert res.account == (Success(), Balance("video", 0)), "the last credit: a reported 0, which the core rests"


@sync
async def test_a_price_not_stated_is_inferred_from_the_balance_dola_just_reported():
    said = ("The video will be generated using the Dreamina Seedance 2.5 model. "
            "You still have %d video credits left today.")
    for before, after in ((6, 4), (4, 2)):      # two jobs in a row on one account: each 2, never the stale 6
        scene = Scene(replies={1: [said % after]}, acct=account(video=before))
        res = await scene.run()
        assert res.job.facts.upstream_price == before - after, "the price was inferred from an old balance"


@sync
async def test_a_refused_order_records_no_price_even_though_the_refusal_names_one():
    res = await Scene(replies={1: [NOT_ENOUGH]}).run()
    assert isinstance(res.job, NotTaken) and not hasattr(res.job, "facts"), "a price was learned from a refusal"


@sync
async def test_another_model_named_is_a_warning_and_the_model_is_recorded():
    said = ("The video will be generated using the **Dreamina Seedance 2.0 Mini model**. It will use 3 credits and be "
            "ready in 10 minutes. You still have 1 video credits left today.")
    res = await Scene(replies={1: [said]}).run()
    assert res.job.facts.upstream_model == "Dreamina Seedance 2.0 Mini" and res.job.facts.upstream_price == 3
    assert [(w.code, "2.0 Mini" in w.text) for w in res.warnings] == [(errors.WARN_MODEL_SUBSTITUTED, True)]


# ---- out of credit --------------------------------------------------------------------------------------------------
@sync
async def test_the_daily_limit_reports_the_jobs_pool_empty_and_the_core_rests_it_until_its_refill():
    res = await Scene(replies={1: [DAILY_LIMIT]}).run()
    assert res.job == NotTaken("pool.account_out_of_points")
    assert effects_of(res) == (Balance("video", 0),), "out for the day: the pool reports 0, whatever a counter says"


@mutation(SUB, "        effects = self._balance(verdict)\n        # The admin hears the reply relay could not classify",
          "        effects = []\n        # The admin hears the reply relay could not classify",
          expect="rested in every reading")
@sync
async def test_zero_credits_left_with_nothing_else_recognised_is_unclear_and_rests_the_pool():
    scene = Scene(replies={1: ["You have 0 video credits left today."]})
    res = await scene.run()
    assert isinstance(res.job, Accepted) and res.job.taken is False, "an unknown reply is unclear: the poll decides"
    assert effects_of(res) == (Balance("video", 0),), (
        "an account at 0 must be rested in every reading: %r" % (effects_of(res),))
    assert scene.log.count("send") == 1, "no re-send, no move to another account"


@sync
async def test_not_enough_for_this_order_with_a_positive_balance_is_not_out_of_credit():
    res = await Scene(replies={1: [NOT_ENOUGH]}).run()
    assert res.job == NotTaken("pool.account_cannot_afford")
    assert effects_of(res) == (Balance("video", 1),), \
        "a positive balance must not rest the pool until midnight"


@mutation(SUB, "        return _outcome(NotTaken(POOL_OUT_OF_POINTS), Balance(spec.kind, 0))",
          '        return _outcome(NotTaken(POOL_OUT_OF_POINTS), Balance("video", 0))', expect="the job's own pool")
@sync
async def test_the_pool_that_rests_is_the_jobs_kind_not_dolas_wording():
    # dola says "daily limit for VIDEO generation" for an IMAGE order too (measured 260920)
    scene = Scene(job=make_job("image"), replies={1: [DAILY_LIMIT]})
    res = await scene.run()
    assert res.job == NotTaken("pool.account_out_of_points")
    assert effects_of(res) == (Balance("image", 0),), "the job's own pool (image) must report empty"
    # a video balance dola reports in an image refusal is not an image balance
    scene = Scene(job=make_job("image"), replies={1: [DAILY_LIMIT + " You still have 3 video credits left today."]})
    assert effects_of(await scene.run()) == (Balance("image", 0),)


@mutation(SUB, "            if attempt >= 2:\n                # The page text is NOT consulted here",
          "            if attempt >= 1 and classify.looks_out_of_credit(await self.driver.page_text(self.page)):\n"
          "                return None\n            if attempt >= 2:\n"
          "                # The page text is NOT consulted here", expect="the receipt must win")
@sync
async def test_an_out_of_credit_page_does_not_end_the_receipt_wait_the_receipt_wins():
    # The page shows out-of-credit words (a sidebar title) while the conversation read only arrives later: dola took the
    # order, and the early page-text exit used to declare it not taken and rest the pool.
    arrived = asyncio.Event()

    async def late_read(world, n):
        async def go():
            await asyncio.sleep(0.05)                  # after several receipt polls (10 ms apart)
            await world.feeder.chain(world.body(), world.conv)
            arrived.set()
        asyncio.get_running_loop().create_task(go())
    scene = Scene(replies={1: [ACCEPTED]}, polls=False, on_send=late_read, page_text="Not Enough Credits")
    res = await scene.run()
    await asyncio.wait_for(arrived.wait(), 2)
    assert isinstance(res.job, Accepted) and res.job.taken is True, (
        "the receipt must win over the page text: %r %r" % (res.job, res.account)
    )
    assert not any(isinstance(e, Rest) for e in res.account), "the pool was rested on a sidebar title"


BUBBLE = "Generated video: %s " + "[7100]"


# ---- refusals --------------------------------------------------------------------------------------------------------
@sync
async def test_a_copyright_refusal_is_terminal_not_charged_and_keeps_the_account_good():
    res = await Scene(replies={1: [COPYRIGHT]}).run()
    assert isinstance(res.job, Fail) and res.job.code == errors.CONTENT_REFUSED
    assert Success() not in effects_of(res), "a refusal is not an accepted order"
    assert rule(errors.CONTENT_REFUSED).terminal and rule(errors.CONTENT_REFUSED).refund and \
        not rule(errors.CONTENT_REFUSED).counts_attempt, "a refusal must end the job, refunded, with no retry"


@mutation(SUB, LIKENESS_RIGHT, LIKENESS_WRONG, expect="guidance")
@sync
async def test_a_likeness_refusal_has_its_own_code_and_gives_no_guidance():
    res = await Scene(replies={1: [LIKENESS]}).run()
    assert isinstance(res.job, Fail) and res.job.code == errors.LIKENESS_REFUSED
    low = res.job.detail.lower()
    assert not any(word in low for word in ("owner", "only makes", "photo", "image", "yourself", "text")), \
        "the likeness refusal carries guidance text: %r" % res.job.detail


@sync
async def test_something_went_wrong_is_a_temporary_error_that_counts_an_attempt_and_drops_the_conversation():
    res = await Scene(replies={1: [SOMETHING_WRONG]}).run()
    assert res.job == NotTaken(errors.TEMPORARY_ERROR, {REFUSED_BY: ["acct-1"]}), "a refusal: counted (25/10)"
    assert rule(errors.TEMPORARY_ERROR).counts_attempt, "dola's own error must use an attempt"


@sync
async def test_an_unclear_reply_with_a_conversation_keeps_it_for_the_poll():
    scene = Scene(replies={1: ["Let me think about that."]})
    res = await scene.run()
    assert res.job == Accepted(CONV, taken=False, unclear="Let me think about that.") \
        and Success() not in effects_of(res), "an unclear reply must not be proven taken (and must not be thrown away)"
    assert scene.session.evidence.tags == ["unclear-reply"]
    assert any("UNCLEAR what dola replied" in text for text, _, _ in scene.ctx.events)


# ---- the pool codes never use an attempt, the UI codes do ------------------------------------------------------------
def test_pool_codes_do_not_count_an_attempt_and_composer_codes_do():
    for code in ("pool.account_out_of_points", "pool.account_cannot_afford", "pool.account_throttled",
                 "pool.account_region_blocked", "pool.account_logged_out", "pool.proxy_failed"):
        assert not rule(code).counts_attempt, "%s must not use an attempt" % code
    for code in (errors.UI_DURATION_PATCH_FAILED, errors.UI_ATTACH_FAILED, errors.UI_MODE_NOT_READY,
                 errors.UI_SEND_FAILED):
        assert rule(code).counts_attempt, "%s must use an attempt" % code


# ---- login, region, throttle -----------------------------------------------------------------------------------------
@mutation(SUB, "        if state.signed_out or state.cells >= 0 or state.modal_visible:\n"
               "            effects = login_doubt_effects(self.deps.now(), state.reason)",
          "        if False:\n            effects = login_doubt_effects(self.deps.now(), state.reason)",
          expect="a login doubt is a spaced strike with a rest")
@sync
async def test_a_cookie_that_belongs_to_no_one_is_a_spaced_strike_and_a_rest_and_nothing_is_sent():
    scene = Scene(probe=GUEST, replies={1: [ACCEPTED]})
    res = await scene.run()
    assert res.job == Retry("pool.account_logged_out"), res.job
    assert types(res) == ["Suspect", "Cooldown"], "a login doubt is a spaced strike with a rest: %r" % (types(res),)
    strike, rest = res.account
    assert strike.min_gap_s == 540 and rest.recheck and rest.until == NOW + timedelta(seconds=600)
    assert "mark_sent" not in scene.log and "send" not in scene.log


LOGIN_BRANCH = ("        else:\n"
                "            until = self.deps.now() + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S)\n"
                "            effects = (Cooldown(\"login read failed\", until),)")


@mutation(SUB, LOGIN_BRANCH, "        else:\n            effects = login_doubt_effects(self.deps.now(), state.reason)",
          expect="a failed read must not be a strike")
@sync
async def test_a_dialog_is_the_same_doubt_and_a_failed_read_is_a_short_rest_without_a_strike():
    res = await Scene(probe={**GOOD_PROBE, "modal": True}).run()
    assert res.job == Retry("pool.account_logged_out")
    assert types(res) == ["Suspect", "Cooldown"] and res.account[0].min_gap_s == 540 and res.account[1].recheck, \
        "a dialog must be a spaced strike with the same rest, never Dead"
    failed_read = {"cells": -1, "ids": [], "name": "", "modal": False, "passport": None}
    res = await Scene(probe=failed_read).run()
    assert res.job == Retry("pool.account_logged_out")
    assert types(res) == ["Cooldown"] and not res.account[0].recheck, "a failed read must not be a strike"


@mutation(SUB, '                        ProxyBad("region blocked: %s" % why), Cooldown("region blocked", until))',
          '                        Cooldown("region blocked", until))', expect="proxy")
@sync
async def test_a_region_page_is_the_proxys_fault_and_nothing_is_sent():
    scene = Scene(url="https://www.dola.com/security/region-restricted")
    res = await scene.run()
    assert res.job == NotTaken("pool.account_region_blocked")
    assert [type(e) for e in res.account] == [ProxyBad, Cooldown], "the proxy must be marked bad (not the account)"
    assert res.account[1] == Cooldown("region blocked", COOLDOWN)
    assert "mark_sent" not in scene.log and "send" not in scene.log


STREAMS = pytest.mark.parametrize("name,code", [("throttle-710022004.txt", 710022004)])


def stream_text(name: str) -> str:
    from kit_c import FIXTURES
    return (FIXTURES / "sse" / name).read_text(encoding="utf-8")


async def sse_on_send(name):
    async def feed(world, n):
        await world.feeder.sse(stream_text(name))
    return feed


@mutation(SUB, "        until = self.deps.now() + timedelta(seconds=constants.THROTTLE_COOLDOWN_S)\n"
               "        return await self._refusal_outcome(POOL_THROTTLED",
          "        until = self.deps.now()\n        return await self._refusal_outcome(POOL_THROTTLED", expect="1800")
@STREAMS
@sync
async def test_a_throttle_code_in_the_stream_cools_the_account_down_for_30_minutes(name, code):
    scene = Scene(polls=False, on_send=await sse_on_send(name))
    res = await scene.run()
    assert res.job == NotTaken("pool.account_throttled", {REFUSED_BY: ["acct-1"]})
    assert effects_of(res) == (Cooldown("dola throttled (%d)" % code, COOLDOWN),), "cooldown must be 1800 s"
    assert rule("pool.account_throttled").counts_attempt is False


# ---- refusals: at most `dola.refusal_max_accounts` accounts per job (owner's ruling 25/10) ---------------------------
REFUSED_ENDS = ("        if refused is not None:\n            return await self._refused_by_code(refused, "
                "conversation)\n")
REFUSED_KEPT = "                await self.ctx.report_ref(conversation, patch)\n"
REFUSED_REST = "        rest = (Cooldown(\"dola refused a prompt (%d)\" % REFUSED_CODE, until, recheck=True),)\n"
PLAIN_REFUSAL = ('data: {"error_code": 710022002, "error_msg": "The request   could not\\nbe completed"}\n\n'
                 'data: [DONE]\n\n')
# Another account refused this job before: this account's refusal is the last one the default (2) allows.
BEFORE = ["acct-0"]
CAP_REACHED = "        return refused, len(refused) >= cap\n"
CAP_PASSED = "        return refused, len(refused) > cap\n"
NOT_LAST = "        if not last:\n            await self._say(\"dola refused the order on %d account(s)"
COUNT_READ = "        refused = [*(before if isinstance(before, list) else ()), self.ctx.account.id]\n"
CAP_SETTING = "        cap = self.settings.get(REFUSAL_MAX_ACCOUNTS) if self.settings is not None else None\n"
DEFAULT_CAP = ('    _setting("refusal_max_accounts", int, 2,\n')
SPEC_FILE = "packages/provider-dola/src/relay_dola/spec.py"
THROTTLE_COUNTED = ("        return await self._refusal_outcome(POOL_THROTTLED, errors.REFUSED_ON_ACCOUNTS, "
                    "\"dola throttled (%d)\" % code,\n")
TEMPORARY_COUNTED = ("            return await self._refusal_outcome(errors.TEMPORARY_ERROR, "
                     "errors.REFUSED_ON_ACCOUNTS, verdict.detail,\n"
                     "                                               balance)\n")


def refusal_on_send(text):
    async def feed(world, n):
        await world.feeder.sse(text)
    return feed


def second_try(**kw):
    return make_job(provider_state={REFUSED_BY: list(BEFORE)}, **kw)


class Cap:
    """The settings view with `dola.refusal_max_accounts` set."""

    def __init__(self, n):
        self.n = n

    def get(self, key):
        return self.n if key == "dola.refusal_max_accounts" else None


# Each kind of refusal: the scene's keyword arguments, the code it goes back to the queue with, the code it ends with,
# and the account's rest.
REFUSALS = {
    "710022002": (lambda: dict(polls=False, on_send=refusal_on_send(stream_text("high-demand-710022002-nested.txt"))),
                  errors.REFUSED_710022002, errors.REFUSED_710022002,
                  (Cooldown("dola refused a prompt (710022002)", COOLDOWN, recheck=True),)),
    "throttle-710022004": (lambda: dict(polls=False, on_send=refusal_on_send(stream_text("throttle-710022004.txt"))),
                           "pool.account_throttled", errors.REFUSED_ON_ACCOUNTS,
                           (Cooldown("dola throttled (710022004)", COOLDOWN),)),
    "something-went-wrong": (lambda: dict(replies={1: [SOMETHING_WRONG]}), errors.TEMPORARY_ERROR,
                             errors.REFUSED_ON_ACCOUNTS, ()),
}
KINDS = pytest.mark.parametrize("kind", list(REFUSALS))


@mutation(SUB, NOT_LAST, "        if False:\n            await self._say(\"dola refused the order on %d account(s)",
          expect="the first refusal did not send the job to a second account")
@mutation(SUB, THROTTLE_COUNTED, "        return _outcome(NotTaken(POOL_THROTTLED), Cooldown(\"dola throttled (%d)\" % "
          "code, until))\n        (\"dola throttled (%d)\" % code,\n",
          expect="the first refusal did not send the job to a second account")
@mutation(SUB, TEMPORARY_COUNTED, "            return _outcome(NotTaken(errors.TEMPORARY_ERROR), *balance)\n",
          expect="the first refusal did not send the job to a second account")
@KINDS
@sync
async def test_the_first_refusal_rests_the_account_and_sends_the_job_to_a_second_account(kind):
    """Owner's ruling 25/10: whatever the refusal, a job is tried on at most `dola.refusal_max_accounts` (2) accounts;
    22-23/10 single jobs went round all 9 accounts within minutes and 6 were logged out by dola right after. The first
    refusal goes back to the queue with this account in the job's `refused_by`, which every requeue keeps."""
    scene_kw, not_taken, _, rest = REFUSALS[kind]
    scene = Scene(**scene_kw())
    res = await scene.run()
    assert res.job == NotTaken(not_taken, {REFUSED_BY: ["acct-1"]}), \
        "the first refusal did not send the job to a second account: %r" % (res.job,)
    assert tuple(e for e in effects_of(res) if isinstance(e, Cooldown)) == rest, "the account's rest changed"
    assert any("goes to another account" in t for t, _ in scene.ctx.said("submit"))
    assert scene.log.count("send") == 1


@mutation(SUB, CAP_REACHED, CAP_PASSED, expect="the refusal on the last account did not end the job")
@mutation(SUB, COUNT_READ, "        refused = [self.ctx.account.id]\n",
          expect="the refusal on the last account did not end the job")
@mutation(SPEC_FILE, DEFAULT_CAP, '    _setting("refusal_max_accounts", int, 3,\n',
          expect="the refusal on the last account did not end the job")
@KINDS
@sync
async def test_the_refusal_on_the_last_account_ends_the_job_failed_with_its_refund(kind):
    scene_kw, _, final, rest = REFUSALS[kind]
    scene = Scene(job=second_try(), **scene_kw())
    res = await scene.run()
    assert isinstance(res.job, Fail) and res.job.code == final, \
        "the refusal on the last account did not end the job: %r" % (res.job,)
    r = rule(final)
    assert (r.terminal, r.refund, r.counts_attempt, r.public) == (True, True, False, True), \
        "%s does not end the job with its full refund" % final
    assert res.job.state_patch[REFUSED_BY] == ["acct-0", "acct-1"]
    assert res.job.detail.endswith("(refused on 2 account(s), the most dola.refusal_max_accounts allows)")
    assert tuple(e for e in effects_of(res) if isinstance(e, Cooldown)) == rest, "the account's rest changed"
    assert scene.log.count("send") == 1


@mutation(SUB, CAP_SETTING, "        cap = None\n", expect="the setting at 1 did not end the job at the first refusal")
@KINDS
@sync
async def test_with_the_setting_at_1_the_first_refusal_ends_the_job(kind):
    scene_kw, _, final, _ = REFUSALS[kind]
    scene = Scene(**scene_kw())
    scene.ctx.settings = Cap(1)
    res = await scene.run()
    assert isinstance(res.job, Fail) and res.job.code == final, \
        "the setting at 1 did not end the job at the first refusal: %r" % (res.job,)
    assert res.job.state_patch[REFUSED_BY] == ["acct-1"]
    big = Scene(job=second_try(), **scene_kw())
    big.ctx.settings = Cap(3)
    assert (await big.run()).job == NotTaken(REFUSALS[kind][1], {REFUSED_BY: ["acct-0", "acct-1"]}), \
        "at 3 the second refusal must still go to a third account"


def test_the_setting_is_a_live_dola_setting_from_1_to_9_defaulting_to_2():
    from relay_dola.spec import SPEC
    s = SPEC.settings()["dola.refusal_max_accounts"]
    assert (s.type, s.default, s.min, s.max) == (int, 2, 1, 9)
    assert "refuses" in s.description and "refund" in s.description


@mutation(SUB, REFUSED_ENDS, "", expect="a 710022002 refusal did not end the job")
@mutation(SUB, REFUSED_REST, "        rest = ()\n", expect="the account that sent the refused prompt did not rest")
@mutation(SUB, REFUSED_KEPT, "                pass\n", expect="the refused conversation was not kept")
@pytest.mark.parametrize("conversation", [False, True], ids=["no-conversation", "a-conversation-appeared"])
@sync
async def test_a_710022002_refusal_on_the_last_account_ends_the_job_refunded_and_keeps_its_evidence(conversation):
    """23/10: 9 of 18 tries in 3 h were 710022002, all on one prompt, re-queued to 7 accounts in turn (5 of them were
    logged out by dola within the hour). The 25/10 ruling: like any refusal, the last allowed account's ends the job,
    with its refund."""
    scene = Scene(job=second_try(), polls=conversation,
                  on_send=refusal_on_send(stream_text("high-demand-710022002-nested.txt")))
    res = await scene.run()
    assert isinstance(res.job, Fail) and res.job.code == errors.REFUSED_710022002, \
        "a 710022002 refusal did not end the job (it would go to a third account): %r" % (res.job,)
    assert res.job.detail.startswith("dola refused the order with 710022002: high demand (refused on 2 account(s)")
    assert effects_of(res) == (Cooldown("dola refused a prompt (710022002)", COOLDOWN, recheck=True),), \
        "the account that sent the refused prompt did not rest: %r" % (res.account,)
    assert ("dola refused the order with 710022002: high demand", "upstream") in scene.ctx.said("submit")
    [(name, data, mime)] = [e for e in scene.ctx.evidence_items if e[0].startswith("refused-")]
    assert name == "refused-710022002-stream.txt" and b"710022002" in data and mime.startswith("text/plain")
    assert scene.log.count("send") == 1
    if conversation:
        # dola may refuse and still deliver ~25 min later: the conversation stays the job's reference for a Re-check
        assert scene.ctx.refs == [(CONV, {"refused_conversation": CONV})], \
            "the refused conversation was not kept: %r" % scene.ctx.refs
        assert res.job.state_patch == {"refused_conversation": CONV, REFUSED_BY: ["acct-0", "acct-1"]}
        assert any(CONV in t and "Re-check" in t for t, _ in scene.ctx.said("submit"))
    else:
        assert scene.ctx.refs == [] and res.job.state_patch == {REFUSED_BY: ["acct-0", "acct-1"]}


@sync
async def test_a_710022002_refusal_that_goes_to_another_account_names_the_conversation_and_keeps_no_reference():
    scene = Scene(on_send=refusal_on_send(stream_text("high-demand-710022002-nested.txt")))
    res = await scene.run()
    assert res.job == NotTaken(errors.REFUSED_710022002, {REFUSED_BY: ["acct-1"]})
    assert scene.ctx.refs == [], "a job that goes to another account keeps no reference of this one"
    assert any(CONV in t and "is left" in t for t, _ in scene.ctx.said("submit"))
    assert not rule(errors.REFUSED_710022002).counts_attempt, "a refusal's requeue uses no attempt"


@mutation(SUB, "        if code in THROTTLE_CODES:\n            return await self._throttled(code)\n        if code == "
          "REGION_CODE or", "        if code in THROTTLE_CODES or code == REGION_CODE:\n            return await "
          "self._throttled(code)\n        if code == REGION_CODE or", expect="a failure that is not a refusal")
@sync
async def test_failures_that_are_not_dolas_refusal_are_not_counted():
    """A proxy failure, a signed-out page, a page fault, out of credit and a region block are not dola refusing the
    order: none adds the account to `refused_by`, and none ends a job that one account already refused."""
    outcomes = [
        await Scene(job=second_try(), open_error=NET_ERROR).run(),                       # proxy
        await Scene(job=second_try(), patched=0, replies={1: [ACCEPTED]}).run(),         # page fault
        await Scene(job=second_try(), replies={1: [DAILY_LIMIT]}).run(),                 # out of credit
        await Scene(job=second_try(), probe=GUEST).run(),                                # signed out
        await Scene(job=second_try(), polls=False,
                    on_send=refusal_on_send('data: {"error_code": 710022003}\n\n')).run(),   # region
    ]
    for res in outcomes:
        assert not isinstance(res.job, Fail), "a failure that is not a refusal ended the job: %r" % (res.job,)
        assert REFUSED_BY not in getattr(res.job, "state_patch", {}), \
            "a failure that is not a refusal was counted: %r" % (res.job,)


STREAM_DELIVERED = "        return None\n    return error\n"
QUIET = "        if name in QUIET_EVENTS or obj == {}:\n            continue\n"
ERROR_THEN_REPLY = ('event: STREAM_ERROR\ndata: {"error_code": 710022002, "error_msg": "busy"}\n\n'
                    'event: CHUNK_DELTA\ndata: {"message": {"content": "{\\"text\\": \\"a lantern\\"}"}}\n\n')
CODE_IN_CONTENT = ('event: SSE_ACK\ndata: {"conversation_id": "1"}\n\n'
                   'event: CHUNK_DELTA\ndata: {"event_data": "{\\"message\\": {\\"content\\": '
                   '\\"error_code: 710022002\\", \\"error_code\\": 710022002}}"}\n\n')


@mutation(SUB, STREAM_DELIVERED, "        continue\n    return error\n",
          expect="a stream that also delivered was read as an error")
@mutation(SUB, QUIET, "        if True:\n            continue\n",
          expect="a stream that also delivered was read as an error")
def test_only_an_error_envelope_in_a_stream_that_delivered_nothing_is_dolas_error():
    """Codex 23/10: a regular expression over the raw stream could read the code out of a reply's text (an echo of the
    prompt). The stream is parsed event by event; the code counts only at the top level of an event (or of its
    `event_data`), and a stream that delivered anything else is no error at all."""
    assert stream_error(stream_text("high-demand-710022002-nested.txt")) == (710022002, "high demand")
    assert stream_error(stream_text("throttle-710022004.txt")) == (710022004, "rate limited")
    assert stream_error(stream_text("clean-ack.txt")) is None
    assert stream_error(PLAIN_REFUSAL).message == "The request could not be completed"
    for body in (ERROR_THEN_REPLY, CODE_IN_CONTENT, 'data: {"error_code": 710022002}\n\ndata: not json\n\n'):
        assert stream_error(body) is None, "a stream that also delivered was read as an error: %r" % body[:60]


REF_BEST_EFFORT = ("            except Exception as exc:      # noqa: BLE001 - keeping the evidence is best effort; "
                   "the refusal stands\n")


@mutation(SUB, REF_BEST_EFFORT, "            except ValueError as exc:\n",
          expect="a failed reference turned the refusal into an unknown order")
@sync
async def test_a_reference_that_cannot_be_stored_still_ends_the_refused_job_with_its_refund():
    scene = Scene(job=second_try(), on_send=refusal_on_send(stream_text("high-demand-710022002-nested.txt")),
                  report_error=RuntimeError("database unavailable"))
    res = await scene.run()
    assert isinstance(res.job, Fail) and res.job.code == errors.REFUSED_710022002, \
        "a failed reference turned the refusal into an unknown order: %r" % (res.job,)
    assert res.job.state_patch["refused_conversation"] == CONV, "the conversation was lost with the reference"
    assert any("could not be stored" in t for t, _ in scene.ctx.said("submit"))


@mutation(SUB, "REFUSAL_RECEIPT_S = 10.0", "REFUSAL_RECEIPT_S = 0.0",
          expect="a conversation that appeared after the refusal was lost")
@sync
async def test_a_conversation_that_appears_shortly_after_the_refusal_is_still_kept():
    """Codex 23/10: the receipt stopped at the first error, so a conversation that appeared a moment later was lost as
    evidence. On a refusal it is looked for REFUSAL_RECEIPT_S more; the refusal is decided right after."""
    scene = Scene(job=second_try(), polls=False,
                  on_send=refusal_on_send(stream_text("high-demand-710022002-nested.txt")))
    probes = {"n": 0}

    async def probe(page):
        probes["n"] += 1
        late = dict(GOOD_PROBE, ids=["111", "222", CONV])
        return late if probes["n"] >= 4 else GOOD_PROBE
    scene.driver.probe = probe
    res = await scene.run()
    assert isinstance(res.job, Fail) and res.job.code == errors.REFUSED_710022002
    assert res.job.state_patch.get("refused_conversation") == CONV, \
        "a conversation that appeared after the refusal was lost: %r" % (res.job,)
    assert scene.log.count("send") == 1


@sync
async def test_an_error_code_with_a_delivery_in_the_same_stream_never_ends_the_job():
    for body in (ERROR_THEN_REPLY, CODE_IN_CONTENT):
        res = await Scene(polls=False, on_send=refusal_on_send(body)).run()
        assert isinstance(res.job, Ambiguous), "a stream that also delivered ended the job: %r" % (res.job,)


GATE_REFUSAL = ("                if refused is not None:       # the re-send's own stream refused it, as the first "
                "send's could\n")


@mutation(SUB, GATE_REFUSAL, "                if False:\n", expect="a re-send's refusal did not end the job")
@sync
async def test_a_710022002_on_a_gated_re_send_is_a_refusal_too():
    """The gate presses Enter again in the same conversation; that send's own stream is read the same way: on the last
    account it ends the job, on the first it sends the job to another account (no third send here)."""
    async def refuse_resend(world, n):
        if n == 2:
            await world.feeder.sse(stream_text("high-demand-710022002-nested.txt"))
    scene = Scene(job=second_try(), replies={1: [GATE]}, on_send=refuse_resend)
    res = await scene.run()
    assert isinstance(res.job, Fail) and res.job.code == errors.REFUSED_710022002, \
        "a re-send's refusal did not end the job: %r" % (res.job,)
    assert scene.log.count("send") == 2, "sent again after dola refused: %r" % kinds(scene.log)
    assert res.job.state_patch == {"refused_conversation": CONV, REFUSED_BY: ["acct-0", "acct-1"]}
    first = Scene(replies={1: [GATE]}, on_send=refuse_resend)
    assert (await first.run()).job == NotTaken(errors.REFUSED_710022002, {REFUSED_BY: ["acct-1"]}), \
        "a re-send's refusal did not end the job (nor move it)"
    assert first.log.count("send") == 2


@sync
async def test_a_710022002_refusal_keeps_dolas_own_words_folded_and_cut():
    scene = Scene(job=second_try(), polls=False, on_send=refusal_on_send(PLAIN_REFUSAL))
    res = await scene.run()
    assert res.job.code == errors.REFUSED_710022002
    assert res.job.detail.startswith("dola refused the order with 710022002: The request could not be completed (")
    assert sse_message('data: {"error_code": 710022002, "error_msg": "%s"}' % ("x" * 999)) == "x" * 200
    assert sse_message('data: {"error_code": 710022002}') == ""
    silent = await Scene(job=second_try(), polls=False,
                         on_send=refusal_on_send('data: {"error_code": 710022002}\n\n')).run()
    assert silent.job.detail.startswith("dola refused the order with 710022002: (no message) (")


@sync
async def test_the_region_code_in_the_stream_marks_the_proxy():
    scene = Scene(polls=False, on_send=await sse_on_send("region-710022003.txt"))
    res = await scene.run()
    assert res.job == NotTaken("pool.account_region_blocked")
    assert [type(e) for e in effects_of(res)] == [ProxyBad, Cooldown] and effects_of(res)[1].until == COOLDOWN


@sync
async def test_a_stream_without_an_error_code_is_not_a_throttle():
    scene = Scene(polls=False, on_send=await sse_on_send("clean-ack.txt"))
    res = await scene.run()
    assert isinstance(res.job, Ambiguous), "no conversation and no code: unknown, never a throttle"


# ---- network and UI errors, before and after the send ----------------------------------------------------------------
@mutation(SUB, '        if network_failure(str(exc)):\n            return _outcome(Retry(POOL_PROXY_FAILED)',
          '        if True:\n            return _outcome(Retry(POOL_PROXY_FAILED)',
          expect="UI errors are never blamed on the proxy")
@sync
async def test_a_network_error_marks_the_proxy_and_a_ui_error_never_does():
    scene = Scene(open_error=NET_ERROR)
    res = await scene.run()
    assert res.job == Retry("pool.proxy_failed")
    assert [type(e) for e in res.account] == [ProxyBad, Cooldown] and res.account[1].until == COOLDOWN
    assert "mark_sent" not in scene.log
    ui = Scene(build_error=RuntimeError("chip row did not appear after clicking Create Videos"))
    res = await ui.run()
    assert res.job == Retry(errors.UI_MODE_NOT_READY) and res.account == (Success(),), \
        "UI errors are never blamed on the proxy (and the logged-in read clears the strike count)"


@sync
async def test_a_composer_error_is_a_retry_with_its_step_code_and_leaves_a_screenshot():
    err = composer.ComposerError("model selection did not take", errors.UI_MODEL_SELECT_FAILED)
    scene = Scene(build_error=err)
    res = await scene.run()
    assert res.job == Retry(errors.UI_MODEL_SELECT_FAILED) and res.account == (Success(),)
    assert scene.session.evidence.tags == ["video-composer"]
    assert [n for n, _, _ in scene.ctx.evidence_items] == ["ui-error-video-composer.png"]
    assert "mark_sent" not in scene.log


@sync
async def test_a_reload_that_times_out_while_building_is_an_ordinary_error_before_the_send():
    # job 21021d7e (24/09): a reload past 30 s during the build was treated as a lost contact and the job sat 13 minutes
    scene = Scene(build_error=TimeoutError("Page.reload: Timeout 30000ms exceeded"))
    res = await scene.run()
    assert res.job == Retry(errors.UI_MODE_NOT_READY) and "mark_sent" not in scene.log


@sync
async def test_a_browser_that_will_not_start_is_the_environments_not_the_orders_and_reaches_the_core():
    scene = Scene(open_error=BrowserLaunchError("Chrome would not start"))
    with pytest.raises(BrowserLaunchError):
        await scene.run()
    assert "mark_sent" not in scene.log
    assert [(t, s) for t, st, s in scene.ctx.events if st == "submit_error"] == [
        ("BrowserLaunchError: Chrome would not start", "engine")], "the cause of a browser error must be kept"


@sync
async def test_a_reference_image_that_cannot_be_read_stops_the_order_before_a_browser_opens():
    async def broken(ref):
        raise OSError("object store is down")
    scene = Scene(job=make_job(images=(media_ref(0),)), read_media=broken)
    res = await scene.run()
    assert res.job == Retry(errors.UI_MODE_NOT_READY) and scene.calls == [], "no browser may open without the images"


# ---- the references --------------------------------------------------------------------------------------------------
async def image_bytes(ref):
    return b"img-%d" % ref.idx


@mutation(SUB, "            if reason:\n                await self._ui_error_evidence(\"%s-attach\" % spec.kind)",
          "            if False:\n                await self._ui_error_evidence(\"%s-attach\" % spec.kind)",
          expect="attach all N")
@sync
async def test_an_order_with_missing_attachments_is_never_sent():
    reason = "only 1/3 thumbnails appeared, 1/3 uploads committed"
    job = make_job(images=(media_ref(0), media_ref(1), media_ref(2)))
    scene = Scene(job=job, read_media=image_bytes, attach_reason=reason, replies={1: [ACCEPTED]})
    res = await scene.run()
    assert res.job == Retry(errors.UI_ATTACH_FAILED), "attach all N: a short attachment must be a retry"
    assert "mark_sent" not in scene.log and "send" not in scene.log, "an i2v order went out with a missing reference"
    assert scene.calls[0][1]["images"] is True, "the browser must turn images on only when attaching"
    assert any(reason in text for text, _, _ in scene.ctx.events), "the reason it broke must reach the timeline"


@sync
async def test_the_references_are_attached_after_the_composer_and_before_the_marker():
    scene = Scene(job=make_job(images=(media_ref(0),)), read_media=image_bytes, replies={1: [ACCEPTED]})
    res = await scene.run()
    assert isinstance(res.job, Accepted)
    steps = [x for x in scene.log if x in ("build", "attach", "mark_sent", "send")]
    assert steps == ["build", "attach", "mark_sent", "send"]


# ---- the crash windows -----------------------------------------------------------------------------------------------
@mutation(SUB, "        if self.sent:\n            return await self._after_send_failure(",
          "        if False:\n            return await self._after_send_failure(", expect="never Retry after mark_sent")
@sync
async def test_a_crash_after_mark_sent_before_enter_is_ambiguous_never_a_retry():
    scene = Scene(send_error=RuntimeError("Target closed"), replies={1: [ACCEPTED]})
    res = await scene.run()
    assert isinstance(res.job, Ambiguous), "never Retry after mark_sent: it could be a second paid order"
    assert not isinstance(res.job, Retry)
    assert scene.log.index("mark_sent") < scene.log.index("send")
    # the steps up to the crash still reach the timeline
    assert any("video mode: chip" in text for text, st, _ in scene.ctx.events if st == "compose")


@sync
async def test_nothing_after_mark_sent_ever_comes_back_as_a_retry():
    failures = {
        "the send raises a composer error": dict(send_error=composer.ComposerError("prompt still in the input")),
        "the send raises anything": dict(send_error=ValueError("boom")),
        "the page dies with a network error": dict(send_error=NET_ERROR),
        "no conversation appears": dict(polls=False),
    }
    for label, kw in failures.items():
        res = await Scene(**kw).run()
        assert isinstance(res.job, Ambiguous), "%s: a retry after mark_sent is a possible second order" % label


# ---- after the send: proven not sent, and a guest page --------------------------------------------------------------
NOT_SENT_GUARD = "            if await self._proven_not_sent():\n                # Enter was pressed"
HALTED = "            halted = await self.driver.halt(self.session)\n"
FROZEN_BEFORE_CLOSE = "            self.traffic.freeze()\n" + HALTED
HALT_CHECKED = ("        if not halted:\n            await self._say(\"the browser could not be closed cleanly, so a "
                "late send cannot be ruled out\", \"submit\",\n")
COMPLETION_SEEN = ("        return any(ev.path == COMPLETION_PATH for ev in "
                   "self.session.recorder.events(self.since))")
STILL = composer.ComposerError("prompt still in the input")


@mutation(SUB, HALTED, "            halted = True\n", expect="decided before the browser was closed")
@mutation(SUB, HALT_CHECKED, "        if False:\n            await self._say(\"\", \"submit\",\n",
          expect="a browser that could not be closed")
@mutation(SUB, HALTED, FROZEN_BEFORE_CLOSE, expect="a late send was missed")
@mutation(SUB, SEND_CALL, FROZEN_AT_SEND, expect="a late send was missed")
@mutation(SUB, NOT_SENT_GUARD, "            if True:\n                # Enter was pressed",
          expect="not sent was decided before the browser was closed")
@mutation(SUB, NOT_SENT_GUARD, "            if False:\n                # Enter was pressed",
          expect="a send proven not to have left was left unknown")
@mutation(SUB, COMPLETION_SEEN, "        return False", expect="re-queued although dola answered the send")
@sync
async def test_a_prompt_left_in_the_input_with_no_request_to_dola_is_not_sent_and_goes_back_to_the_queue():
    """23/10: Enter did nothing (the prompt stayed in the input, the page made no request to dola) and the job sat in
    unknown for 11 min until reconcile proved it not sent. One Enter, then the proof, then the queue."""
    scene = Scene(send_error=STILL, order_request=False)
    res = await scene.run()
    assert res.job == NotTaken(errors.UI_SEND_FAILED), \
        "a send proven not to have left was left unknown: %r" % (res.job,)
    assert scene.log.count("send") == 1 and scene.log.index("mark_sent") < scene.log.index("send"), \
        "more than one Enter in one session"
    assert "halt" in scene.log and scene.log.index("halt") > scene.log.index("send"), \
        "not sent was decided before the browser was closed: %r" % scene.log
    watch = scene.driver.traffic_watches[0]
    assert watch.armed and watch.frozen and watch.stopped, "the traffic watch was not armed at the Enter, frozen "
    "after the close, and stopped with the run"
    res = await Scene(send_error=STILL, order_request=False, late_send=True).run()
    assert isinstance(res.job, Ambiguous), "a late send was missed: %r" % (res.job,)
    res = await Scene(send_error=STILL, order_request=False, halt_ok=False).run()
    assert isinstance(res.job, Ambiguous), "a browser that could not be closed proved not sent: %r" % (res.job,)
    res = await Scene(send_error=STILL, order_request=True).run()
    assert isinstance(res.job, Ambiguous), "re-queued although a request that may carry the order left the page"
    answered = Scene(send_error=STILL, order_request=False)
    real_send = answered.driver.send

    async def send(page, text, *, before_enter):
        async def enter():
            await before_enter()
            await answered.session.feeder.sse("data: {}\n\n")       # dola answered: the order left the page
        await real_send(page, text, before_enter=enter)
    answered.driver.send = send
    res = await answered.run()
    assert isinstance(res.job, Ambiguous), "re-queued although dola answered the send: %r" % (res.job,)


class Emitter:
    """A Playwright object's `on` / `remove_listener`, and `emit` for the test."""

    def __init__(self, pages=()):
        self.handlers, self.pages = {}, list(pages)

    def on(self, event, handler):
        self.handlers[event] = handler

    def remove_listener(self, event, handler):
        assert self.handlers.pop(event) == handler

    def emit(self, event, *args):
        if event in self.handlers:          # nobody listening: the event goes unseen, as in the browser
            self.handlers[event](*args)


def request(method, url):
    return dataclasses.make_dataclass("Request", ["method", "url"])(method, url)


CARRIES = ("            carries = ((request.method != \"GET\" and urlsplit(request.url).path not in READ_PATHS)"
           "\n")
CARRIES_GET_ONLY = "            carries = ((False)\n"
FULL_URL = "    return bool(_ORDER_REQUEST.search(url or \"\"))\n"
PATH_ONLY = "    return bool(_ORDER_REQUEST.search(urlsplit(url or \"\").path))\n"
BLIND_SEEN = "        return self.blind or not self.armed or self.count > 0\n"


@mutation(SUB, CARRIES, CARRIES_GET_ONLY, expect="a POST after the Enter was not seen")
@mutation(SUB, FULL_URL, PATH_ONLY, expect="a send routed by its query was not seen")
@mutation(SUB, '            ws.on("framesent", self._on_frame)\n', "            pass\n",
          expect="a WebSocket send on a page opened later was not seen")
@mutation(SUB, BLIND_SEEN, "        return self.count > 0\n", expect="a blind watch proved nothing left")
def test_the_traffic_watch_sees_every_send_of_the_context_and_a_blind_watch_proves_nothing():
    """Codex 23/10: "provably not sent" may rest only on a watch of the WHOLE browser context: every page and popup,
    a send routed by a query, any request that is not a GET, any WebSocket frame sent; a watch that could not see
    counts as traffic."""
    for url in ("https://www.dola.com/chat/completion?aid=1", "https://www.dola.com/chat/38418281883319313",
                "https://api.example/samantha/completion_v2", "https://www.dola.com/api?route=chat/completion"):
        assert may_carry_order(url), "a send routed by its query was not seen: %s" % url
    for url in ("https://www.dola.com/im/chain/single?x=1", "https://www.dola.com/passport/account/info/v2/",
                "https://www.dola.com/chat", ""):
        assert not may_carry_order(url), "a read of the page counted as a send: %s" % url

    old_page = Emitter()
    ctx = Emitter([old_page])
    watch = TrafficWatch(ctx)
    ctx.emit("request", request("POST", "https://www.dola.com/im/chain/single"))
    assert not watch.count, "traffic before the Enter was counted"
    watch.arm()
    ctx.emit("request", request("GET", "https://www.dola.com/im/chain/single?aid=1"))
    assert not watch.seen(), "a plain read counted as a send"
    ctx.emit("request", request("GET", "https://www.dola.com/api?route=chat/completion"))
    assert watch.seen(), "a send routed by its query was not seen"

    reads = TrafficWatch(Emitter())
    reads.arm()
    reads._ctx.emit("request", request("POST", "https://www.dola.com/im/chain/single?aid=1"))
    assert not reads.seen(), "a conversation read counted as a send"
    for method, url in (("POST", "https://mon.example/anything"), ("PUT", "https://www.dola.com/x"),
                        ("POST", "https://www.dola.com/im/chain/send")):
        other = TrafficWatch(Emitter())
        other.arm()
        other._ctx.emit("request", request(method, url))
        assert other.seen(), "a POST after the Enter was not seen: %s %s" % (method, url)

    ctx = Emitter()
    watch = TrafficWatch(ctx)
    watch.arm()
    popup, ws = Emitter(), Emitter()
    ctx.emit("page", popup)
    popup.emit("websocket", ws)
    ws.emit("framesent", "payload")
    assert watch.seen(), "a WebSocket send on a page opened later was not seen"
    watch.freeze()
    after = watch.count
    ctx.emit("request", request("POST", "https://www.dola.com/im/chain/single"))
    assert watch.count == after, "the engine's own reads after the send were counted"
    watch.stop()
    assert ctx.handlers == {} and popup.handlers == {} and ws.handlers == {}

    class Unwatchable:
        pages = ()

        def on(self, event, handler):
            raise RuntimeError("target closed")
    blind = TrafficWatch(Unwatchable())
    blind.arm()
    assert blind.seen(), "a blind watch proved nothing left"
    unarmed = TrafficWatch(Emitter())
    assert unarmed.seen(), "a watch never armed at an Enter proved nothing left"
    odd = TrafficWatch(Emitter())
    odd.arm()

    class Unreadable:
        @property
        def method(self):
            raise RuntimeError("target closed")
    odd._ctx.emit("request", Unreadable())
    assert odd.seen(), "a request that could not be read was taken as no order"


GUEST_READ = "        logged_out = await self._logged_out_after_send()\n        if logged_out is not None:\n"
GUEST_MAY_HAVE_LEFT = ("        if not await self._proven_not_sent():\n            await self._flush_steps()\n"
                       "            await self._say(\"after the send the page read")
GUEST_NEVER_LEFT = ("        if False:\n            await self._flush_steps()\n"
                    "            await self._say(\"after the send the page read")
GUEST_AFTER_SEND = {"cells": 0, "ids": [], "name": "", "modal": True,
                    "passport": {"message": "error", "uid": False, "name": "account_info_error"}}


@mutation(SUB, GUEST_READ, "        logged_out = None\n        if logged_out is not None:\n",
          expect="a guest page after the send was not read as logged out")
@mutation(SUB, GUEST_MAY_HAVE_LEFT, GUEST_NEVER_LEFT,
          expect="re-queued although a request that may carry the order left the page")
@mutation(SUB, "        self.logged_in = False\n        present = ", "        present = ",
          expect="the logged-in read at the open cleared the strike of the logout")
@sync
async def test_a_guest_page_after_the_send_is_a_logout_and_the_order_follows_the_not_sent_proof():
    """23/10: "Log in to unlock more features" over the composer after the send went to unknown with no word of the
    logout. Now the account gets the strike and the rest of a guest read; the order stays unknown when a request that
    may carry it left the page, and goes back to the queue when none did."""
    scene = Scene(polls=False, probe_after_send=GUEST_AFTER_SEND)
    res = await scene.run()
    assert isinstance(res.job, Ambiguous), \
        "re-queued although a request that may carry the order left the page: %r" % (res.job,)
    assert "Suspect" in types(res), "a guest page after the send was not read as logged out: %r" % (res.account,)
    assert "Success" not in types(res), "the logged-in read at the open cleared the strike of the logout"
    assert types(res) == ["Suspect", "Cooldown"], res.account
    assert res.account[1].recheck and res.account[1].until == NOW + timedelta(seconds=constants.LOGIN_DOUBT_REST_S)
    res = await Scene(polls=False, probe_after_send=GUEST_AFTER_SEND, order_request=False).run()
    assert res.job == NotTaken("pool.account_logged_out") and types(res) == ["Suspect", "Cooldown"], \
        "a guest page after the send was not read as logged out: %r" % (res,)
    lost = await Scene(polls=False, probe_after_send=GUEST_AFTER_SEND, order_request=False, cookie_names=set()).run()
    assert lost.job == NotTaken(errors.BROWSER_COOKIES_LOST) and types(lost) == ["Cooldown"], \
        "the browser's lost cookies were struck against the account: %r" % (lost,)


@sync
async def test_a_network_failure_after_the_send_is_unknown_and_still_marks_the_proxy():
    res = await Scene(send_error=NET_ERROR).run()
    assert isinstance(res.job, Ambiguous)
    assert [type(e) for e in effects_of(res)] == [ProxyBad, Cooldown]


@mutation(SUB, "    except _CoreVerdict as verdict:\n        raise verdict.cause from None",
          "    except _CoreVerdict as verdict:\n        return _outcome(Retry(errors.UI_SEND_FAILED))",
          expect="DID NOT RAISE")
@sync
async def test_a_refused_mark_sent_reaches_the_core_unchanged_and_nothing_is_sent():
    scene = Scene(mark_error=lease_lost(), replies={1: [ACCEPTED]})
    with pytest.raises(type(lease_lost())) as err:
        await scene.run()      # the lease is lost: the core's own verdict, not to be turned into a Retry
    assert err.value.lease_id == 7, "the lease verdict must reach the core"
    assert "send" not in scene.log, "an order was sent although the marker was refused"
    stopping = Scene(mark_error=RuntimeError("the worker is stopping"))
    with pytest.raises(RuntimeError, match="stopping"):
        await stopping.run()
    assert "send" not in stopping.log


@sync
async def test_a_failure_to_report_the_conversation_after_the_send_is_unknown():
    scene = Scene(replies={1: [ACCEPTED]})

    async def broken(ref, state):
        raise OSError("database is down")
    scene.ctx.report_ref = broken
    res = await scene.run()
    assert isinstance(res.job, Ambiguous), "a failure after the send is never a retry"


# ---- the gate re-send ------------------------------------------------------------------------------------------------
async def gated(replies, *, composer_kind=None, new_composer=False, **kw):
    scene = Scene(replies=replies, new_composer=new_composer, composer_kind=composer_kind, **kw)
    return scene, await scene.run()


@mutation(SUB, "before_enter=self._fence_for_send)", "before_enter=self._flush_steps)",
          expect="fence() before every re-send")
@sync
async def test_every_resend_is_preceded_by_a_fence_and_there_are_at_most_two():
    scene, res = await gated({1: [GATE], 2: [GATE], 3: [GATE]})     # dola keeps asking: it never takes the order
    sends = [i for i, x in enumerate(scene.log) if x == "send"]
    assert len(sends) == 3, "the first send plus at most two re-sends: %r" % kinds(scene.log)
    for i in sends[1:]:
        assert scene.log[i - 1] == "fence", "fence() before every re-send: %r" % kinds(scene.log)
    assert scene.ctx.fences == 2
    assert res.job == Accepted(CONV, taken=False), "still gated after the pushes: the poll decides (R56)"
    # the re-send rebuilds the FULL composer first, in the same conversation
    assert [x for x in scene.log if x == "rebuild"] == ["rebuild", "rebuild"]
    assert set(scene.driver.sent) == {SENT}, "the re-send must carry the same text"
    assert scene.driver.world.sends == [SENT] * 3, "same conversation, same text"


@sync
async def test_a_lease_lost_between_the_gate_check_and_the_resend_means_no_second_send():
    scene, res = await gated({1: [GATE]}, fence_error=lease_lost())
    assert scene.log.count("send") == 1, "a second order was sent after the lease was lost: %r" % kinds(scene.log)
    assert isinstance(res.job, Ambiguous), "no second send: the outcome is unknown (reconcile finds the conversation)"
    assert scene.ctx.fences == 1


@sync
async def test_a_fence_that_fails_for_any_reason_also_stops_the_resend():
    scene, res = await gated({1: [GATE]}, fence_error=RuntimeError("database is down"))
    assert scene.log.count("send") == 1 and isinstance(res.job, Ambiguous)


@sync
async def test_a_gate_that_passes_after_one_resend_is_a_taken_order_not_a_failure():
    scene, res = await gated({1: [GATE], 2: [ACCEPTED]})
    assert scene.log.count("send") == 2 and scene.ctx.fences == 1
    assert isinstance(res.job, Accepted) and res.job.taken is True, "the re-send was accepted: the order is taken"
    assert res.job.facts.upstream_price == 2 and Success() in res.account


@sync
async def test_the_acceptance_that_comes_a_few_seconds_late_stops_the_resend():
    # re-read before re-sending: sending again would be a NEW paid order
    scene, res = await gated({1: [GATE]}, late=[ACCEPTED])
    assert scene.log.count("send") == 1 and scene.ctx.fences == 0, "the order was taken, and sent again"
    assert isinstance(res.job, Accepted) and res.job.taken is True


@mutation(SUB, "        return True if self.new_composer is None else self.new_composer", "        return False",
          expect="new composer")
@sync
async def test_the_accounts_composer_decides_what_counts_as_taken():
    # accepted AND asking in the same breath: on the new composer the order is in, on the old one it is not
    scene, res = await gated({1: [OLD_GATE_AND_ACCEPT]}, new_composer=True)
    assert scene.log.count("send") == 1 and isinstance(res.job, Accepted) and res.job.taken is True, \
        "new composer: accepted-and-asking is a taken order, a re-send would be a second order"
    scene, res = await gated({n: [OLD_GATE_AND_ACCEPT] for n in (1, 2, 3)}, new_composer=False)
    assert scene.log.count("send") == 3, "old composer: accepted-and-asking is not taken, the gate is pushed"
    assert res.job == Accepted(CONV, taken=False)


@sync
async def test_the_accounts_recorded_composer_fills_in_when_the_page_does_not_say():
    scene = Scene(new_composer=None, composer_kind="new", replies={1: [OLD_GATE_AND_ACCEPT]})
    res = await scene.run()
    assert scene.log.count("send") == 1 and res.job.taken is True, "the recorded composer UI was not used"


@sync
async def test_a_resend_attaches_the_references_again_and_a_failure_to_rebuild_ends_it_without_a_send():
    job = make_job(images=(media_ref(0),))
    scene, res = await gated({1: [GATE], 2: [ACCEPTED]}, job=job, read_media=image_bytes)
    assert [x for x in scene.log if x in ("rebuild", "attach", "fence", "send")] == [
        "attach", "send", "rebuild", "attach", "fence", "send"]
    broken, res = await gated({1: [GATE]}, rebuild_error=composer.ComposerError("chip row did not appear"))
    assert broken.log.count("send") == 1 and broken.ctx.fences == 0, "a re-send went out with an unbuilt composer"
    assert res.job == Accepted(CONV, taken=False)


@sync
async def test_an_order_dola_took_stays_taken_whatever_a_later_message_says():
    # 25/09: "daily limit" answered an EXTRA send and the taken order was thrown away (5 videos of 30 s lost)
    scene, res = await gated({1: [GATE, ACCEPTED], 2: [DAILY_LIMIT]}, new_composer=True)
    assert scene.log.count("send") == 1, "taken at once: nothing to re-send"
    late = Scene(replies={1: [ACCEPTED, DAILY_LIMIT]}, new_composer=True)
    out = await late.run()
    assert isinstance(out.job, Accepted) and out.job.taken is True and out.account[0] == Success(), \
        "a later 'daily limit' dropped a taken order"


# ---- the timeline and the debug files --------------------------------------------------------------------------------
@sync
async def test_the_timeline_keeps_dolas_words_apart_from_the_engines_own():
    scene = Scene(replies={1: [ACCEPTED]})
    await scene.run()
    assert [(text, src) for text, src in scene.ctx.said("submit")] == [(" ".join(ACCEPTED.split()), "upstream")]
    compose = scene.ctx.said("compose")
    assert len(compose) == 1 and compose[0][1] == "engine" and "login: 5 conversations" in compose[0][0]
    ui = Scene(build_error=composer.ComposerError("duration chip not found", errors.UI_DURATION_PATCH_FAILED))
    await ui.run()
    assert ui.ctx.said("submit") == [("composer: duration chip not found", "engine")], \
        "our own UI error was recorded as dola's words"


@sync
async def test_a_job_that_got_a_reply_leaves_the_after_send_picture_and_a_ui_error_leaves_its_files():
    scene = Scene(replies={1: [ACCEPTED]})
    await scene.run()
    assert scene.ctx.evidence_items == [("after-send-1.jpg", b"jpg", "image/jpeg")]
    failed = Scene(build_error=composer.ComposerError("x", errors.UI_MODE_NOT_READY))
    await failed.run()
    assert failed.ctx.evidence_items[0][2] == "image/png" and failed.ctx.evidence_items[0][0].endswith(".png")


# ---- no balance check before the send --------------------------------------------------------------------------------
BALANCE_CHECK = (
    "        spec, ctx = self.spec, self.ctx\n"
    "        if ctx.account.capacity.get(spec.kind) == 0:\n"
    "            return _outcome(NotTaken(POOL_CANNOT_AFFORD))\n")


RUN_HEAD = "        spec, ctx = self.spec, self.ctx\n"


@mutation(SUB, RUN_HEAD, BALANCE_CHECK, expect="no pre-send balance check")
@sync
async def test_there_is_no_pre_send_balance_check_the_claim_did_it():
    scene = Scene(acct=account(video=0), replies={1: [ACCEPTED]})
    res = await scene.run()
    assert scene.log.count("send") == 1 and isinstance(res.job, Accepted), "no pre-send balance check: the claim ran it"


# ---- images ----------------------------------------------------------------------------------------------------------
URLS = ["https://p.example.invalid/a.png", "https://p.example.invalid/b.png"]


@sync
async def test_an_image_order_holds_the_session_until_the_images_arrive_and_completes():
    scene = Scene(job=make_job("image"), replies={1: ["Done."]}, late=[("images", URLS)])
    res = await scene.run()
    assert isinstance(res.job, Completed) and [o.ref for o in res.job.outputs] == URLS
    assert [(o.idx, o.mime, o.upstream_id) for o in res.job.outputs] == [
        (0, "image/png", CONV + ":0"), (1, "image/png", CONV + ":1")]
    assert res.account == (Success(),) and scene.ctx.evidence_items[0][0] == "after-send-1.jpg"


@sync
async def test_an_image_that_is_not_there_before_the_hold_ends_is_accepted_for_the_poll():
    scene = Scene(job=make_job("image"), replies={1: ["Done."]}, hold_s=3)
    res = await scene.run()
    assert res.job == Accepted(CONV, taken=True), "the images are not yet there: take the receipt and poll"


@sync
async def test_an_image_refusal_is_a_refusal_and_a_stopped_order_fails_the_job():
    refused = await Scene(job=make_job("image"), replies={1: [COPYRIGHT]}).run()
    assert isinstance(refused.job, Fail) and refused.job.code == errors.CONTENT_REFUSED
    stopped = await Scene(job=make_job("image"), replies={1: ["Done."]},
                          late=["I couldn't make that image. No credits were used."]).run()
    assert isinstance(stopped.job, Fail) and stopped.job.code == errors.CONTENT_REFUSED


# ---- the reading of the order ----------------------------------------------------------------------------------------
def test_a_job_with_no_sent_text_is_refused_the_engine_builds_none_of_its_own():
    with pytest.raises(ValueError, match="sent_text"):
        order_spec(make_job(sent_text=None))
    with pytest.raises(ValueError, match="kind"):
        order_spec(dataclasses.replace(make_job(), params={}))


# ---- the attach itself and the composer net ----------------------------------------------
class AttachPage:
    """A page for `PageDriver.attach`: how many previews show, and how many upload commits happen on choosing files."""

    def __init__(self, cards, *, inputs=1, commits=None):
        self.cards, self.inputs = cards, inputs
        self.commits = cards if commits is None else commits
        self.listeners = []
        page = self

        class Loc:
            first = None

            async def count(self):
                return page.inputs

            async def set_input_files(self, paths):
                class Req:
                    def __init__(self, method, url):
                        self.method, self.url = method, url
                for i in range(page.commits):
                    for fn in list(page.listeners):     # the chain measured 23/09 for each image: only the last commits
                        fn(Req("POST", "https://www.dola.com/alice/resource/prepare_upload"))
                        fn(Req("GET", "https://imagex-ap-southeast-1.bytevcloudapi.com/"))
                        fn(Req("POST", "https://tos-myb2-share.vodupload.com/upload/v1/x/%d.png" % i))
                        fn(Req("POST", "https://imagex-ap-southeast-1.bytevcloudapi.com/"))
        loc = Loc()
        loc.first = loc
        self._loc = loc

    def locator(self, selector):
        return self._loc

    def on(self, event, fn):
        assert event == "requestfinished"
        self.listeners.append(fn)

    def remove_listener(self, event, fn):
        self.listeners.remove(fn)

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, js, *a):
        return self.cards


THREE = [(b"x", "image/png")] * 3


@sync
async def test_attach_reports_a_clean_result_only_when_every_image_is_committed_and_previewed():
    driver = PageDriver()
    assert await driver.attach(AttachPage(3), THREE) == "", "all there, reported broken"
    assert await driver.attach(AttachPage(0), []) == "", "a text-only order has nothing to attach"


@sync
async def test_attach_says_where_it_broke():
    driver = PageDriver()
    assert "1/3" in await driver.attach(AttachPage(1), THREE), "the reason must say how many of how many"
    assert await driver.attach(AttachPage(3, inputs=0), THREE) == "no file input in composer"
    # thumbnails are all there but only one upload committed: the 4-second gap measured 23/09 (the DOM looks done, the
    # file is not in dola's store yet): sending now sends an image dola does not have
    assert "1/3 uploads committed" in await driver.attach(AttachPage(3, commits=1), THREE)


def test_only_the_imagex_post_is_the_commit_of_an_upload():
    assert is_upload_commit("POST", "https://imagex-ap-southeast-1.bytevcloudapi.com/")
    for method, url in (("GET", "https://imagex-ap-southeast-1.bytevcloudapi.com/"),
                        ("POST", "https://tos-myb2-share.vodupload.com/upload/v1/x/a.png"),
                        ("POST", "https://www.dola.com/alice/resource/prepare_upload")):
        assert not is_upload_commit(method, url), (
            "%s %s counted as the commit: one image would count 2-3 times" % (method, url)
        )


@sync
async def test_attach_leaves_no_temp_file_behind(monkeypatch):
    import os

    from relay_dola.engine import submit as module
    made = []
    real = module._write_temp_files

    def spy(images, ext):
        made.extend(real(images, ext))
        return made
    monkeypatch.setattr(module, "_write_temp_files", spy)
    await PageDriver().attach(AttachPage(3), THREE)
    assert len(made) == 3 and not any(os.path.exists(p) for p in made), "temp image files were left on disk"


class ReloadPage:
    def __init__(self):
        self.reloads, self.kw = 0, []

    async def reload(self, **kw):
        self.reloads += 1
        self.kw.append(kw)

    async def wait_for_timeout(self, ms):
        return None


@sync
async def test_the_composer_is_rebuilt_after_a_reload_for_both_modes_and_gives_up_after_three_tries(monkeypatch):
    async def flaky_enter(counter):
        counter["n"] += 1
        if counter["n"] <= 2:
            raise composer.ComposerError("chip row did not appear")
        return "Model 2.5"

    for mode, enter in (("video", "enter_video_mode"), ("image", "enter_image_mode")):
        counter = {"n": 0}

        async def enter_fn(page, counter=counter):
            return await flaky_enter(counter)

        async def model(page, m):
            return "Model " + m

        async def dur(page, ratio, seconds):
            return ("30s", "16:9", True)

        async def ratio(page, r):
            return r
        monkeypatch.setattr(composer, enter, enter_fn)
        monkeypatch.setattr(composer, "select_video_model", model)
        monkeypatch.setattr(composer, "select_image_model", model)
        monkeypatch.setattr(composer, "select_video_ratio_duration", dur)
        monkeypatch.setattr(composer, "select_image_ratio", ratio)

        async def nothing(page):
            return None
        monkeypatch.setattr(composer, "dismiss_consent", nothing)
        page = ReloadPage()
        spec = make_job(mode).params
        order = OrderSpec(mode, spec["model"], spec.get("duration"), "16:9", None, SENT, PROMPT)
        built = await PageDriver().build(page, order)
        assert page.reloads == 2, "%s: no reload-and-retry when the chip row was not rendered" % mode
        assert all(kw.get("timeout") == 60000 for kw in page.kw), "the reload must wait as long as goto (60 s)"
        assert built.steps[0].startswith("%s mode: chip" % mode)
    # out of tries: raise, never go on to send into a page that was not built
    never = {"n": -99}

    async def always(page):
        return await flaky_enter(never)
    monkeypatch.setattr(composer, "enter_video_mode", always)
    with pytest.raises(composer.ComposerError):
        await PageDriver().build(ReloadPage(), OrderSpec("video", "seedance_v2.5", 30, "16:9", None, SENT, PROMPT))


COLD_WAIT = 'wait = {"settle_s": COLD_MODE_WAIT_S} if cold else {}'
COLD_PASSED = "built = await driver.build(page, spec, cold=self.session.cold)"


@mutation(SUB, COLD_WAIT, "wait = {}", expect="a cold profile got the warm wait for the mode button")
@mutation(SUB, COLD_PASSED, "built = await driver.build(page, spec)", expect="the run did not say its profile is cold")
@sync
async def test_a_cold_profile_waits_longer_for_the_mode_button(monkeypatch):
    """23/10: on the first run after a cookie refresh (a new, empty profile) the image composer was not there yet when
    the 9 s wait for its button ran out. A cold profile waits up to COLD_MODE_WAIT_S; a warm one keeps the 9 s."""
    from relay_dola.engine.submit import COLD_MODE_WAIT_S
    waits = []

    async def enter(page, **kw):
        waits.append(kw.get("settle_s"))
        return "Model x"

    async def model(page, m):
        return "Model " + m

    async def dur(page, ratio, seconds):
        return ("30s", "16:9", True)

    async def nothing(page):
        return None
    for name, fn in (("enter_image_mode", enter), ("enter_video_mode", enter), ("select_image_model", model),
                     ("select_video_model", model), ("select_video_ratio_duration", dur), ("dismiss_consent", nothing)):
        monkeypatch.setattr(composer, name, fn)
    image = OrderSpec("image", "seedream-4.5", None, None, None, SENT, PROMPT)
    video = OrderSpec("video", "seedance_v2.5", 30, "16:9", None, SENT, PROMPT)
    for order in (image, video):
        await PageDriver().build(ReloadPage(), order, cold=True)
        await PageDriver().build(ReloadPage(), order)
    assert waits == [COLD_MODE_WAIT_S, None] * 2 and COLD_MODE_WAIT_S == 30, \
        "a cold profile got the warm wait for the mode button: %r" % waits
    for cold in (True, False):
        scene = Scene(cold=cold, replies={1: [ACCEPTED]})
        await scene.run()
        assert scene.driver.builds_cold == [cold], "the run did not say its profile is cold"


@sync
async def test_the_resend_composer_enters_video_mode_only_when_the_chip_row_is_gone(monkeypatch):
    calls = []

    async def enter(page):
        calls.append("enter")
        return "x"

    async def model(page, m):
        calls.append("model")
        return "Model 2.5"

    async def dur(page, ratio, seconds):
        calls.append("duration")
        return ("30s", "16:9", False)
    monkeypatch.setattr(composer, "enter_video_mode", enter)
    monkeypatch.setattr(composer, "select_video_model", model)
    monkeypatch.setattr(composer, "select_video_ratio_duration", dur)
    spec = OrderSpec("video", "seedance_v2.5", 30, "16:9", None, SENT, PROMPT)
    for chip_row, expected in ((True, ["model", "duration"]), (False, ["enter", "model", "duration"])):
        async def wait_rect(page, pattern, timeout_s=25, js=None, chip_row=chip_row):
            return {"x": 0, "y": 0, "text": "Model 2.5"} if chip_row else None
        monkeypatch.setattr(composer, "_wait_rect", wait_rect)
        calls.clear()
        built = await PageDriver().rebuild(ReloadPage(), spec)
        assert calls == expected, "pressing 'Create Videos' with video mode on would turn it OFF: %r" % calls
        assert built.new_composer is False


# ---- the exact stored text -------------------------------------------------------------------------------------------
REPLY_CAP = "REPLY_CHARS = constants.EVENT_TEXT_MAX      # the core stores an event over this"
REPLY_UNCAPPED = "REPLY_CHARS = 8000      # the core stores an event over this"


@mutation(SUB, REPLY_CAP, REPLY_UNCAPPED, expect="the core would store a bare omitted marker")
def test_the_stored_reply_keeps_the_newest_part_and_cuts_the_oldest_within_the_event_cap():
    from relay_core.redact import redact_log
    from relay_dola.engine.submit import reply_text
    long = "Newest: order taken.  " + "old " * 5000
    out = reply_text(long)
    assert out.startswith("Newest: order taken. old") and reply_text(None) == ""
    assert not redact_log(out).startswith("[omitted"), "the core would store a bare omitted marker, not dola's words"
    assert len(out) == constants.EVENT_TEXT_MAX == 2000


@mutation(SUB, "    return \"\\n\\n\".join(p for p in paragraphs if p)[:REPLY_CHARS]",
          "    return \" \".join(p for p in paragraphs if p)[:REPLY_CHARS]", expect="the replies ran together")
def test_the_stored_reply_keeps_the_blank_lines_between_replies_and_folds_white_space_inside_each():
    from relay_dola.engine.submit import reply_text
    out = reply_text("Newest   reply\nhere.\n\nOlder  reply.\n\n\n\nOldest.")
    assert out == "Newest reply here.\n\nOlder reply.\n\nOldest.", "the replies ran together: %r" % (out,)


@sync
async def test_the_upstream_event_of_a_long_reply_survives_the_core_log_cap():
    from relay_core.redact import redact_log
    scene = Scene(replies={1: [ACCEPTED + " " + "more words. " * 800]})
    await scene.run()
    texts = [t for t, st, src in scene.ctx.events if st == "submit" and src == "upstream"]
    assert texts and all(not redact_log(t).startswith("[omitted") for t in texts), (
        "the core would store a bare omitted marker, not dola's words")


def test_the_json_a_chain_read_returns_is_what_the_kit_feeds():
    # guards the test kit itself: the conversation body it builds must be the shape dola's own reads have
    from kit_c import chain_body
    body = chain_body([("user", "hello"), ("assistant", "hi")])
    messages = body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]
    assert [json.loads(m["content"]).get("text") if m["user_type"] == 1 else "assistant" for m in messages] == [
        "assistant", "hello"], "dola lists the newest message first"
    assert asyncio.iscoroutinefunction(submit)


# ---- the site profile (site.py) --------------------------------------------------------------------------------------
SITE = "packages/provider-dola/src/relay_dola/engine/site.py"


@mutation(SITE, CAPTURE_RIGHT, CAPTURE_WRONG, expect="throttle stream")
def test_the_site_profile_declares_what_the_recorder_keeps_and_what_the_browser_needs():
    import re

    from relay_browser.session import CHROME_BACKGROUND_HOSTS
    from relay_dola.engine import site
    profile = site.DOLA_SITE
    assert (profile.name, profile.host, profile.locale, profile.viewport) == ("dola", "dola.com", "en-US", (1440, 900))
    keeps = lambda path: any(re.search(p, path) for p in profile.capture_paths)      # noqa: E731
    assert keeps("/im/chain/single") and keeps("/chat/completion"), "the throttle stream is not captured"
    assert not keeps("/im/chain/recent_conv") and not keeps("/im/conversation/info"), "an undeclared body is captured"
    assert profile.capture_ws_hosts == () and profile.block_images and profile.block_fonts
    assert "*mcs.snssdk.com*" in profile.telemetry_url_patterns and len(profile.telemetry_url_patterns) == 5
    # the measured values are asserted in test_site_profile.py
    assert profile.local_storage_token_keys == site.LOCAL_STORAGE_TOKEN_KEYS
    assert set(CHROME_BACKGROUND_HOSTS) <= set(profile.refuse_hosts), "Chrome's background hosts must stay refused"
    assert site.REGION_BLOCK_MARK == "region-restricted"


# ---- the composer fails because the patch never reached the page ---
@mutation(SUB, "            if spec.needs_patch and self.session.patched == 0:\n                # The level is missing",
          "            if False:\n                # The level is missing", expect="no patch must be")
@sync
async def test_a_composer_failure_with_zero_patches_is_blamed_on_the_patch_not_on_the_chip():
    boom = composer.ComposerError("duration chip not found", code=errors.UI_RATIO_SELECT_FAILED)
    scene = Scene(patched=0, build_error=boom)
    res = await scene.run()
    assert res.job == Retry(errors.UI_DURATION_PATCH_FAILED), "no patch must be the code: %r" % (res.job,)
    assert "mark_sent" not in scene.log
    other = Scene(patched=1, build_error=boom)
    assert (await other.run()).job == Retry(errors.UI_RATIO_SELECT_FAILED), "a patched page keeps the step's code"
    short = Scene(patched=0, build_error=boom, job=make_job(duration=10))
    assert (await short.run()).job == Retry(errors.UI_RATIO_SELECT_FAILED), "a native level needs no patch"


# ---- a prompt that never went into the input is not a sent order ---
@mutation(SUB, "            if not self.sent:\n                return await self._unsent(exc.code,",
          "            if False:\n                return await self._unsent(exc.code,", expect="never typed")
@sync
async def test_a_prompt_that_never_went_into_the_input_is_a_retry_and_was_never_marked_sent():
    scene = Scene(type_error=composer.ComposerError("could not type the prompt", code=errors.UI_SEND_FAILED))
    res = await scene.run()
    assert res.job == Retry(errors.UI_SEND_FAILED) and "mark_sent" not in scene.log, (
        "an order never typed was marked sent (a retry became impossible): %r %r" % (res.job, scene.log)
    )


# ---- the Send button is never clicked: one Enter, an input that stays full is unknown ---
CO_SEND = "        await type_and_send(page, prompt, before_enter=before_enter)"
CO_SEND_NO_HOOK = "        await type_and_send(page, prompt)"


@mutation("packages/provider-dola/src/relay_dola/engine/composer.py", CO_SEND, CO_SEND_NO_HOOK,
          expect="mark_sent must run right before the one Enter")
@sync
async def test_an_input_that_stays_full_after_the_one_enter_ends_ambiguous_with_no_second_action():
    scene = Scene(replies={1: [ACCEPTED]})
    page = SendPage(scene.log, clear_after=-1)         # Enter does nothing; any other script (a click) raises

    async def real_send(_page, text, *, before_enter):
        await PageDriver.send(scene.driver, page, text, before_enter=before_enter)
    scene.driver.send = real_send
    res = await scene.run()
    tail = scene.log[scene.log.index("mark_sent"):][:2] if "mark_sent" in scene.log else []
    assert tail == ["mark_sent", "Enter"], "mark_sent must run right before the one Enter: %r" % scene.log
    assert scene.log.count("Enter") == 1 and "fence" not in scene.log, "a second action followed Enter"
    assert isinstance(res.job, Ambiguous), "marked sent, input still full: unknown, reconcile decides: %r" % (res.job,)


# ---- the composer flag when nothing says (money-safe default) ---


# ---- the first reply read from the page text, the API back later ---
GATE_Q = ("Video generation currently supports durations from 4 to 15 seconds. "
          "Would you like to proceed with 15 seconds?")
PAGE = "New chat\nCreate Videos\n" + GATE_Q + "\nSend"


QUESTION = "Would you like to proceed with 15 seconds?"


# ---- page text never gives an order up while a conversation exists ---


@sync
async def test_refusal_wording_in_the_page_text_with_a_conversation_leaves_the_poll_to_decide():
    zero_widget = "Daily credits\nYou have 0 video credits left today."
    for page_text in ("Not Enough Credits", DAILY_LIMIT, COPYRIGHT, SOMETHING_WRONG, ACCEPTED, zero_widget):
        scene = Scene(replies={}, page_text=page_text)       # the conversation exists, dola's API text stays empty
        res = await scene.run()
        assert res.job == Accepted(CONV, taken=False, unclear=""), \
            "page text %r gave the order up or was reported as dola's reply: %r" % (page_text, res.job)
        assert effects_of(res) == (), "page wording marked taken, rested or priced the account: %r" % (res.account,)


# ---- the URL's conversation is adopted only when it carries our text ---
URL_ADOPT = ("            if (cand.isdigit() and cand not in baseline\n"
             "                    and body_carries(await self.driver.read_chain(self.page, cand), "
             "self.spec.sent_text,\n"
             "                                     self.spec.prompt)):\n")


@mutation(SUB, URL_ADOPT, "            if cand.isdigit() and cand not in baseline:\n", expect="carries our text")
@sync
async def test_a_conversation_in_the_url_is_adopted_only_when_its_read_carries_the_sent_text():
    url = "https://www.dola.com/chat/" + CONV
    mine = Scene(replies={1: [ACCEPTED]}, polls=False, url=url)
    assert isinstance((await mine.run()).job, Accepted)
    assert mine.ctx.refs == [(CONV, {})], "our own conversation in the URL was not adopted"
    other = Scene(replies={1: [ACCEPTED]}, polls=False, url=url)
    other.driver.bodies[CONV] = chain_body([("user", "somebody else's order")])
    res = await other.run()
    assert other.ctx.refs == [] and isinstance(res.job, Ambiguous), (
        "the URL's conversation was adopted without carrying our text (carries our text): %r" % (res.job,)
    )


ZERO_LEFT = "You have 0 video credits left today."


# ---- the unclear note does not claim dola said something when only the API blipped ---
UNCLEAR_API = '        if said:\n            tail'
UNCLEAR_ALWAYS = '        if True:\n            tail'


@mutation(SUB, UNCLEAR_API, UNCLEAR_ALWAYS, expect="claims dola said something")
@sync
async def test_an_api_blip_is_not_logged_as_something_dola_said():
    blip = Scene(replies={}, page_text="Create Videos\nSend")
    await blip.run()
    notes = [t for t, st, src in blip.ctx.events if src == "engine" and st == "submit"]
    assert any("no usable read" in t for t in notes) and not any("UNCLEAR what dola" in t for t in notes), (
        "the note claims dola said something when only the API blipped: %r" % notes)
    said = Scene(replies={1: ["Okay, working on it."]})
    await said.run()
    assert any("UNCLEAR what dola replied" in t for t, st, src in said.ctx.events), "an unclear API reply lost its tail"


HALF_WRITTEN = "Video generation currently supports durations from 4 to 15 seconds."
UNCLEAR_REPLY = "Okay, working on it."
CHAT = "https://www.dola.com/chat/" + CONV


# ---- the reply that is stored and logged is dola's, never the page's ---
WAIT_KEEP = "        return latest if latest.usable else current\n"
WAIT_OVERWRITE = "        return latest\n"


@mutation(SUB, WAIT_KEEP, WAIT_OVERWRITE, expect="dola's last words were dropped")
@sync
async def test_a_failed_last_read_keeps_dolas_last_words_not_the_pages_sidebar():
    sidebar = "Sidebar: Other chat title"
    scene = Scene(replies={1: [UNCLEAR_REPLY]}, polls=False, page_text=sidebar, url=CHAT)
    fail_own_reads(scene, lambda sends, k: sends == 1 and k == REPLY_TRIES + 1)     # the last read of the wait
    await scene.run()
    texts = [t for t, st, src in scene.ctx.events if st == "submit"]
    assert any(UNCLEAR_REPLY in t for t in texts), "dola's last words were dropped: %r" % texts
    assert not any("Sidebar" in t for t in texts), "the page text was stored as dola's reply: %r" % texts


# ---- no conversation: page text never decides ---
NO_CONV_PAGE_OFF = ("        code = self._sse_error()\n        if code in THROTTLE_CODES:\n"
                    "            return await self._throttled(code)\n")
NO_CONV_PAGE_ON = ("        text = await self.driver.page_text(self.page)\n"
                   "        if classify.looks_out_of_credit(text):\n"
                   "            return await self._out_of_points(classify.read_credits_left(text), text)\n"
                   "        code = self._sse_error()\n        if code in THROTTLE_CODES:\n"
                   "            return await self._throttled(code)\n")


@mutation(SUB, NO_CONV_PAGE_OFF, NO_CONV_PAGE_ON, expect="page text decided an order with no conversation")
@sync
async def test_with_no_conversation_page_wording_never_decides_the_order():
    prompt = "a sign that says: you don't have enough credits, change the parameters and try again"
    echo = "\n".join([BUBBLE % prompt, "Send"])
    pages = ("Not Enough Credits", DAILY_LIMIT, SOMETHING_WRONG, COPYRIGHT, NOT_ENOUGH, echo,
             "\n".join([BUBBLE % "a lantern", DAILY_LIMIT]))
    for page in pages:
        job = make_job(prompt=prompt) if page == echo else make_job()
        scene = Scene(job=job, polls=False, page_text=page)
        res = await scene.run()
        assert isinstance(res.job, Ambiguous) and effects_of(res) == (), (
            "page text decided an order with no conversation (%r): %r %r" % (page[:30], res.job, res.account))
        assert scene.log.count("send") == 1
