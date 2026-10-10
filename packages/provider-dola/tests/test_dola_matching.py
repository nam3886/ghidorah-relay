"""How dola recognises its own job when the prompt frame puts the tracking code first, in the middle or last, and starts
with fixed words (contracts 5.1, 5.4.1). Every text comes from the real renderer and every marker is
the bare digits `JobView.marker` stores.

Two directions, neither may cost money: a "not ours" ends in a second paid order, an "ours" adopts another job's
conversation. `carries_text` (the whole sent text, in a thread, for a customer who wrote something) is "ours";
`may_be_job` (the bare marker or the first 80 characters of the prompt) is "cannot tell" and keeps the job waiting.
"""

import asyncio

import pytest
from kit_b import FakeTransport, chain_with, http_for, reply, sent_of, user
from test_dola_reconcile import SENT, go, mine, other
from test_recovery import cell, conv

from relay_core import frames
from relay_core.providers import Found, NotSent, StillUnknown
from relay_core.testing.mutation import mutation
from relay_dola.engine import resolver
from relay_dola.engine.recovery import RecoveryError, recover_outputs
from relay_dola.engine.submit import body_carries
from relay_dola.spec import DEFAULT_VIDEO_FRAME, DEFAULT_VIDEO_HINT

RESOLVER = "packages/provider-dola/src/relay_dola/engine/resolver.py"
MAY_BE_JOB = ("    return bool((code and code in hay) or (window and window in hay) or (inner and inner in hay))")
BRACKETED = ("    return bool((code and '[' + code + ']' in hay) or (window and window in hay) "
             "or (inner and inner in hay))")
NO_MARKER = "    return bool((window and window in hay) or (inner and inner in hay))"
MARKER_ONLY = "    return bool(code and code in hay)"
NO_INNER = "    return bool((code and code in hay) or (window and window in hay))"
RECONCILE = "packages/provider-dola/src/relay_dola/engine/reconcile.py"
MAY_BE_CALL = "    if not mine and resolver.may_be_job(body, job.marker, prompt):"
MAY_BE_CALL_WITH_SENT = "    if not mine and resolver.may_be_job(body, job.marker, job.sent_text):"

PROMPT = "a cat riding a bicycle at dawn"
MARKER = "7788"
PRE = "Fixed preamble of this model. "
TAIL = " Keep it cinematic."
POSITIONS = {
    "last": "{{client_prompt}} {{tracking_code}}",
    "first": "{{tracking_code}} {{client_prompt}}",
    "middle": "{{client_prompt}} {{tracking_code}}" + TAIL,
    "preamble-last": PRE + "{{client_prompt}} {{tracking_code}}",
    "preamble-first": PRE + "{{tracking_code}} {{client_prompt}}",
    "preamble-middle": PRE + "{{client_prompt}} {{tracking_code}}" + TAIL,
    "long-preamble": DEFAULT_VIDEO_HINT + " {{client_prompt}} {{tracking_code}}",     # fixed words past 80 characters
    "shipped-video": DEFAULT_VIDEO_FRAME,
}
everywhere = pytest.mark.parametrize("frame", list(POSITIONS.values()), ids=list(POSITIONS))


def thread(text):
    return chain_with([user("Generated video: " + text)])


def flat(**kw):
    """The conversations of other jobs, made with the same frame."""
    return {str(100 + i): mine(sent_of("someone else's order number %d with other words" % i, "10%02d" % i, **kw))
            for i in range(3)}


# ---- the exact text is ours ----------------------------------------------------------------------------------
@everywhere
def test_the_exact_text_is_found_through_the_receipt_and_through_reconcile(frame):
    text = sent_of(PROMPT, MARKER, frame)
    assert body_carries(thread(text), text, PROMPT), "the receipt missed the job's own text"
    assert go({"1": mine(text)}, ref="1", frame=frame)[0] == Found("1", {}, True), "its own conversation was missed"
    assert go({"7": mine(text), **flat(frame=frame)}, frame=frame)[0] == Found("7", {}, True), "a listed one was missed"


# ---- other jobs made with the same frame are not ours --------------------------------------------------------
@mutation(RECONCILE, MAY_BE_CALL, MAY_BE_CALL_WITH_SENT,
          expect="another job's conversation made with the same frame was taken for ours")
@everywhere
def test_a_complete_listing_of_other_jobs_with_the_same_frame_proves_not_sent(frame):
    result = go(flat(frame=frame), frame=frame)[0]
    assert result == NotSent(True), "another job's conversation made with the same frame was taken for ours: %r" % (
        result,)
    assert go({"9": other()}, frame=frame)[0] == NotSent(True), "neither the words nor the code: not ours"


# ---- dola altered the text: cannot tell, never "not ours" ---------------------------------------------------
@mutation(RESOLVER, MAY_BE_JOB, NO_MARKER, expect="the bare tracking code was not enough to doubt")
@everywhere
def test_one_word_changed_with_the_bare_marker_intact_is_cannot_tell(frame):
    text = sent_of(PROMPT.replace("cat", "dog"), MARKER, frame)
    result = go({"9": mine(text)}, frame=frame)[0]
    assert result == StillUnknown(), "the bare tracking code was not enough to doubt: %r" % (result,)


@mutation(RESOLVER, MAY_BE_JOB, BRACKETED,
          expect="dola took the brackets off the code")
@everywhere
def test_dola_stripping_the_brackets_and_altering_the_words_is_cannot_tell(frame):
    text = sent_of(PROMPT.replace("cat", "dog"), MARKER, frame).replace("[%s]" % MARKER, MARKER)
    result = go({"9": mine(text)}, frame=frame)[0]
    assert result == StillUnknown(), "dola took the brackets off the code: %r" % (result,)


@mutation(RESOLVER, MAY_BE_JOB, MARKER_ONLY,
          expect="the customer's words survived and were not enough to doubt")
@everywhere
def test_the_marker_removed_with_the_prompt_prefix_intact_is_cannot_tell(frame):
    text = sent_of(PROMPT, MARKER, frame).replace("[%s]" % MARKER, "")
    result = go({"9": mine(text)}, frame=frame)[0]
    assert result == StillUnknown(), "the customer's words survived and were not enough to doubt: %r" % (result,)


@everywhere
def test_only_the_customers_words_or_only_the_bare_marker_is_cannot_tell(frame):
    for text, what in ((PROMPT, "the customer's words alone"), ("take %s please" % MARKER, "the bare code alone")):
        result = go({"9": mine(text)}, frame=frame)[0]
        assert result == StillUnknown(), "%s were not enough to doubt: %r" % (what, result)


@everywhere
def test_a_capped_listing_holding_other_jobs_is_cannot_tell_but_one_holding_ours_is_found(frame):
    result = go(flat(frame=frame), frame=frame, has_more=True)[0]
    assert result == StillUnknown(), "a capped listing proves nothing: %r" % (result,)
    text = sent_of(PROMPT, MARKER, frame)
    assert go({"7": mine(text), **flat(frame=frame)}, frame=frame, has_more=True)[0] == Found("7", {}, True)


# ---- a prompt that shows nothing -----------------------------------------------------------------------------
@everywhere
def test_a_prompt_that_shows_nothing_matches_no_conversation_whatever_the_frame(frame):
    blank = "​ ⁠"
    sent = sent_of(blank, MARKER, frame)
    theirs = "Generated video: " + sent_of("someone else's order", MARKER, frame)
    assert not resolver.carries_text("Generated video: " + sent, sent, blank)
    assert not resolver.carries_text(theirs, sent, blank), "the frame's words and a shared code matched another order"
    for nothing in ("", None, " ", "\u200b"):
        assert not resolver.carries_text("anything at all", nothing, PROMPT), "a blank sent text matched %r" % nothing


# ---- the frame/prompt seam --------------------------------------------------------------------------------------
SEAMS = (
    ("Cafe{{client_prompt}} {{tracking_code}}", "\u0301 au lait with a few more words after it"),       # composes to é
    ("\uac00{{client_prompt}} {{tracking_code}}", "\u11a8 and then a few more words follow it"),          # Hangul jamo
    ("{{client_prompt}}\u0301 {{tracking_code}}", "a quiet lake at dusk, then e"),  # mark joins the e
    ("{{client_prompt}}x {{tracking_code}}", "the hero \u039f\u0394\u03a5\u03a3\u03a3\u0395\u03a5\u03a3"),  # sigma
    ("abc{{client_prompt}} {{tracking_code}}", "\u03a3 rest of the sentence goes here"),        # sigma after a letter
)


@mutation(RESOLVER, MAY_BE_JOB, NO_INNER, expect="the seam between the frame and the prompt changed how it folds")
def test_a_prompt_that_folds_differently_next_to_the_frames_words_is_cannot_tell_when_the_marker_is_lost():
    for frame, prompt in SEAMS:
        text = sent_of(prompt, MARKER, frame).replace("[%s]" % MARKER, "")
        window = resolver.norm_for_match(prompt)[:resolver.PROMPT_PREFIX]
        assert window not in resolver.norm_for_match(text), "premise: %r folds the same in the sent text" % prompt
        result = go({"9": mine(text)}, prompt=prompt, frame=frame)[0]
        assert result == StillUnknown(), "the seam between the frame and the prompt changed how it folds: %r %r" % (
            frame, result)


# ---- dola's own replies -----------------------------------------------------------------------------------------
@mutation(RESOLVER, '    hay = norm_for_match(user_text(body) + " " + conversation_text(body))',
          "    hay = norm_for_match(user_text(body))", expect="only dola's reply carries the tracking code")
def test_a_conversation_where_only_dolas_reply_carries_the_marker_is_cannot_tell():
    body = chain_with([reply("Your video [%s] is on its way." % MARKER, SENT + 30), user("Generated video: unrelated")])
    result = go({"9": body})[0]
    assert result == StillUnknown(), "only dola's reply carries the tracking code: %r" % (result,)


# ---- a short prompt costs availability, never money ----------------------------------------------------------
@everywhere
def test_a_short_prompt_found_in_another_jobs_conversation_is_cannot_tell(frame):
    theirs = mine(sent_of("a cat on a sofa", "1234", frame))
    result = go({"5": theirs}, prompt="a cat", frame=frame)[0]
    assert result == StillUnknown(), "documented cost: the job waits for the deadline: %r" % (result,)


# ---- an earlier attempt of the same job ----------------------------------------------------------------------
@everywhere
def test_an_earlier_attempt_is_not_adopted_and_the_newest_match_wins(frame):
    text = sent_of(PROMPT, MARKER, frame)
    old, new = mine(text, at=SENT - 86_400), mine(text, at=SENT + 30)
    assert go({"900": old}, frame=frame)[0] == NotSent(True)
    for convs in ({"900": old, "100": new}, {"100": old, "900": new}):
        assert go(convs, frame=frame)[0] == Found(next(k for k, v in convs.items() if v is new), {}, True)


# ---- the invariant -----------------------------------------------------------------------------------------
def _frames():
    for pre in ("", PRE, "Cafe"):
        for first, second in (("{{client_prompt}}", "{{tracking_code}}"), ("{{tracking_code}}", "{{client_prompt}}")):
            for mid in ("", " ", " and "):
                for post in ("", TAIL, "e"):
                    yield pre + first + mid + second + post


PROMPTS = ("a cat", "x" * 120, "́ au lait", "He said “hello”  there", "ｆullｗidth text",
           "tab\tand\nnewline", "Zero​width in the middle of a sentence", "é")


@mutation(RESOLVER, MAY_BE_JOB, NO_MARKER, expect="a text we sent was not recognised as maybe ours")
def test_whatever_carries_the_whole_text_may_be_the_job_for_every_frame():
    prefix_alone_failed = 0
    for frame in _frames():
        for prompt in PROMPTS:
            for marker in ("7788", "0007"):
                sent = frames.render(frame, prompt, marker)
                for text in (sent, "Generated video: " + sent, "x " + sent + " y"):
                    if not resolver.carries_text(text, sent, prompt):
                        continue
                    body = chain_with([user(text)])
                    assert resolver.may_be_job(body, marker, prompt), \
                        "a text we sent was not recognised as maybe ours: %r from %r" % (text, frame)
                    hay = resolver.norm_for_match(resolver.user_text(body) + " " + resolver.conversation_text(body))
                    window = resolver.norm_for_match(prompt)[:resolver.PROMPT_PREFIX]
                    inner = window[1:-1] if len(window) >= 10 else ""
                    prefix_alone_failed += window not in hay and not (inner and inner in hay)
    assert prefix_alone_failed, "premise: some frame glues text to the prompt so that only the marker finds it"


# ---- the receipt ---------------------------------------------------------------------------------------------
@everywhere
def test_the_receipt_is_not_another_jobs_text_made_with_the_same_frame(frame):
    mine_ = sent_of(PROMPT, MARKER, frame)
    assert not body_carries(thread(sent_of("someone else's order", "1234", frame)), mine_, PROMPT)
    assert not body_carries(thread(PRE.strip()), mine_, PROMPT), "the preamble alone is not the receipt"
    assert not body_carries(thread(mine_), mine_, ""), "a blank prompt matches nothing"


# ---- the poll guard ------------------------------------------------------------------------------------------
@everywhere
def test_the_poll_guard_accepts_the_jobs_conversation_and_rejects_another_jobs(frame):
    ours, theirs = sent_of(PROMPT, MARKER, frame), sent_of("another order", "1234", frame)
    http = http_for(FakeTransport({"1": thread(ours), "2": thread(theirs)}))
    asyncio.run(resolver.fetch_chain(http, "1", MARKER))
    with pytest.raises(resolver.ChainError):
        asyncio.run(resolver.fetch_chain(http, "2", MARKER))


# ---- Recover -------------------------------------------------------------------------------------------------
@everywhere
def test_recover_refuses_another_conversation_unless_it_carries_the_whole_text(frame):
    sent = sent_of(PROMPT, MARKER, frame)
    video = cell("https://v.example/a.mp4", "v1")

    def recover(text):
        http = http_for(FakeTransport({"c": conv(video, text="Generated video: " + text)}))
        return asyncio.run(recover_outputs(http, "c", expect=None, sent_text=sent, now=1_790_000_000.0, prompt=PROMPT))

    assert len(recover(sent)) == 1
    for text in (sent_of("someone else's order", "1234", frame), PROMPT, MARKER):
        with pytest.raises(RecoveryError):
            recover(text)
