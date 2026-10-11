"""What dola hands the core to report (contracts 5.4.1 "unclear", "unclear_at_deadline"): at the render deadline the
newest paragraph of dola's last words when nothing explains them, and at submit the reply to the last send, from the
engine's own reads, with the lines that only echo the customer's words removed.
"""

from kit_c import (
    ACCEPTED,
    CONV,
    COPYRIGHT,
    GATE,
    LIKENESS,
    PROMPT,
    SOMETHING_WRONG,
    Scene,
    make_job,
    sync,
)

from relay_core.providers import Accepted, Balance
from relay_core.testing.mutation import mutation

SPEC_FILE = "packages/provider-dola/src/relay_dola/spec.py"
SUBMIT = "packages/provider-dola/src/relay_dola/engine/submit.py"
ACCEPTED_GUARD = "        if order_accepted(upstream_text):\n            return None"
ACCEPTED_NOT_GUARDED = "        if False:\n            return None"
CAUSE_GUARD = 'if not upstream_text.strip() or self.deadline_cause(upstream_text, provider_state) != "render.deadline":'
CAUSE_NOT_GUARDED = "if not upstream_text.strip():"
UNCLEAR_DROPPED = "return _outcome(Accepted(conversation, taken=False, unclear=reply), *effects)"
UNCLEAR_NOT_PASSED = "return _outcome(Accepted(conversation, taken=False), *effects)"
UNCLEAR_FROM_PAGE = ("return _outcome(Accepted(conversation, taken=False, "
                     "unclear=await self.driver.page_text(self.page)), *effects)")
BLANK_PARAGRAPH = '        return upstream_text.split("\\n\\n", 1)[0].strip() or None'
BLANK_REPORTED = '        return upstream_text.split("\\n\\n", 1)[0].strip()'
OPEN_QUESTION = "return _outcome(Accepted(self.conversation, taken=False), *balance)"
OPEN_QUESTION_REPORTED = "return _outcome(Accepted(self.conversation, taken=False, unclear=reply), *balance)"


def unclear_at_deadline(text, **state):
    from relay_dola.spec import SPEC
    return SPEC.unclear_at_deadline(text, state)


# ---- the deadline hook ------------------------------------------------------------------------------------------
@mutation(SPEC_FILE, CAUSE_GUARD, CAUSE_NOT_GUARDED, expect="reported a reply deadline_cause explains")
def test_the_hook_reports_nothing_when_deadline_cause_has_its_own_reason():
    for what, text, state in (("a refusal", COPYRIGHT, {}), ("a likeness refusal", LIKENESS, {}),
                              ("a question", GATE, {}), ("a dead session", "Hmm.", {"signed_out": True}),
                              ("watermarked links", "Hmm.", {"watermarked_only": True})):
        assert unclear_at_deadline(text, **state) is None, "reported a reply deadline_cause explains: " + what


@mutation(SPEC_FILE, ACCEPTED_GUARD, ACCEPTED_NOT_GUARDED, expect="reported an accepted last word")
def test_the_hook_reports_nothing_for_an_accepted_order_or_for_no_text():
    assert unclear_at_deadline(ACCEPTED) is None, "reported an accepted last word"
    assert unclear_at_deadline("") is None and unclear_at_deadline("  \n ") is None


@mutation(SPEC_FILE, BLANK_PARAGRAPH, BLANK_REPORTED, expect="reported a blank paragraph")
def test_the_hook_reports_nothing_when_the_newest_paragraph_is_blank():
    assert unclear_at_deadline(" \n\nolder words") is None, "reported a blank paragraph"


def test_the_hook_reports_the_newest_paragraph_of_dolas_last_words():
    assert unclear_at_deadline("Hmm, let me think about that.") == "Hmm, let me think about that."
    assert unclear_at_deadline("Newest words here.\n\nAn older reply.\n\nAnd older.") == "Newest words here."
    assert unclear_at_deadline("  Padded reply  \n\nolder") == "Padded reply"


# ---- the submit hand-off ----------------------------------------------------------------------------------------
@mutation(SUBMIT, UNCLEAR_DROPPED, UNCLEAR_NOT_PASSED, expect="the unclear reply was not handed to the core")
@sync
async def test_an_unclear_reply_hands_the_core_dolas_reply_raw_with_one_send():
    scene = Scene(replies={1: ["Hmm, let me think about that [4821]."]})
    res = await scene.run()
    assert res.job == Accepted(CONV, taken=False, unclear="Hmm, let me think about that [4821]."), \
        "the unclear reply was not handed to the core: %r" % (res.job,)
    assert scene.log.count("send") == 1


@sync
async def test_a_bare_zero_balance_is_reported_and_still_rests_the_account():
    scene = Scene(replies={1: ["You have 0 video credits left today."]})
    res = await scene.run()
    assert res.job == Accepted(CONV, taken=False, unclear="You have 0 video credits left today.")
    assert Balance("video", 0, "reported") in res.account and len(res.account) == 2


@mutation(SUBMIT, UNCLEAR_DROPPED, UNCLEAR_FROM_PAGE, expect="page text was reported as dola's reply")
@sync
async def test_page_text_is_never_reported_only_the_apis_reply_and_without_it_the_text_is_empty():
    page = "Other customer's chat: secret-title-7788\nDaily credits"
    scene = Scene(replies={}, page_text=page)                # the conversation exists, dola's API text stays empty
    res = await scene.run()
    assert res.job == Accepted(CONV, taken=False, unclear=""), "page text was reported as dola's reply: %r" % (res.job,)
    assert "secret-title" not in repr(res)


@sync
async def test_the_engine_hands_over_dolas_raw_reply_and_the_core_takes_the_customers_words_out():
    scene = Scene(replies={1: ["Hmm, let me think about that.\n" + PROMPT]})
    res = await scene.run()
    assert res.job.unclear == "Hmm, let me think about that.\n" + PROMPT, \
        "the engine must not strip anything itself: %r" % (res.job.unclear,)


@sync
async def test_after_a_resend_the_reported_text_is_the_reply_to_it_not_the_old_question():
    scene = Scene(replies={1: [GATE], 2: ["Hmm, not sure about that."]}, new_composer=False)
    res = await scene.run()
    assert scene.log.count("send") == 2, "premise: one re-send"
    assert res.job == Accepted(CONV, taken=False, unclear="Hmm, not sure about that."), res.job


@mutation(SUBMIT, OPEN_QUESTION, OPEN_QUESTION_REPORTED, expect="a question still open was reported")
@sync
async def test_a_question_still_open_after_the_resends_is_not_reported():
    scene = Scene(replies={1: [GATE], 2: [GATE], 3: [GATE]}, new_composer=False)
    res = await scene.run()
    assert res.job == Accepted(CONV, taken=False) and res.job.unclear is None, \
        "a question still open was reported: %r" % (res.job,)


@sync
async def test_an_acceptance_a_refusal_a_transient_error_and_an_image_order_report_nothing():
    for replies in ([ACCEPTED], [COPYRIGHT], [SOMETHING_WRONG]):
        res = await Scene(replies={1: replies}).run()
        assert getattr(res.job, "unclear", None) is None, "reported a decided reply: %r" % (res.job,)
    image = await Scene(job=make_job("image"), replies={1: ["Done."]}).run()
    assert isinstance(image.job, Accepted) and image.job.taken is True and image.job.unclear is None
