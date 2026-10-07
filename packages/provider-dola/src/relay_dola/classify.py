"""Reading dola's words: classify a reply, read its credit statements.

Port of dola_v4 `services/reply.py` and `services/credits.py` (same function names). Pure.

WHY this exists: when dola refuses it STILL creates a conversation, so a refusal looks exactly like an accepted send
from outside. Real replies seen while verifying:

| dola says | really |
|---|---|
| "It will use 2 credits and be ready in 15 minutes..." | order accepted |
| "...will use 2 video credits. You only have 1 left today." | not enough credit |
| "Something went wrong. Please try again." | temporary error |

Patterns are WHOLE SENTENCES. The text compared is the page's `innerText`, which also holds the title of every old
conversation in the sidebar, and dola names conversations after their outcome ("Video Generation Limit"). A short
phrase would match them all.

The rule is a WHITE LIST: success only on a clear acceptance signal. A list of failure sentences is never finished:
dola changes a word and the failure slips through, and here that means a job waiting for a video that does not exist.
"""

import re
from enum import StrEnum
from typing import NamedTuple

from relay_dola import errors as codes
from relay_dola.catalog import MODEL_LABELS


class SubmitOutcome(StrEnum):
    OK = "ok"                                # the order was taken
    NO_POINTS = "no_points"                  # out of credit
    CONTENT_REFUSED = "content_refused"      # dola refused the content (copyright, safety, likeness, cannot generate)
    NEEDS_CONFIRM = "needs_confirm"          # dola asked a question or gated the order
    ERROR = "error"                          # a temporary error


# ================================= credits (v4 credits.py) =====================================================
# "You still have 1 video credits left today." / "You have 3 video credits left today."
_CREDITS_LEFT = re.compile(r"(\d+)\s+video\s+credits?\s+left", re.I)
# "...will use 2 video credits. You only have 1 left today."
_ONLY_HAVE_LEFT = re.compile(r"you\s+only\s+have\s+(\d+)\s+left", re.I)

# dola refuses because the account has not enough credit: nothing is spent and the account is not broken, so the job
# moves to another account.
#
# "0 video credits left" is NOT here: it is the balance AFTER the charge, and dola says it inside an acceptance too
# ("It will use 2 credits and be ready in 15 minutes... You still have 0 video credits left today."). Putting it here
# once reported an accepted order as out of credit and lost 2 credits.
# "no credits were used" is NOT here either: dola says it when it refuses CONTENT and has refunded; the account still
# has credit. Putting it here rested a good account until the reset AND re-sent the job elsewhere (paid twice for a
# refusal that cost nothing).
_OUT_OF_CREDIT_MARKERS = (
    "reached the daily limit for video generation",
    "run out of video credits",
    "don't have enough credits",
    "not enough credits",
    "change the parameters and try again",
    # The image ceiling has its OWN sentence (21/09, account Thu Ung718: 21 jobs = 63 images, job 22 got this).
    # Unmatched, the order read as ACCEPTED and four jobs polled the one-hour deadline into render.deadline.
    "reached today's image limit",
    "reached the daily limit for image generation",
    # The new UI (per account A/B) words it differently, for images AND video (24/09, after 5 jobs of 5.0 Pro).
    "reached the daily free usage limit",
)

_PRICE_STATED = re.compile(r"will use\s+(\d+)\s+(?:video\s+)?(?:credits?|points?)", re.I)


def read_credits_left(text: str) -> int | None:
    """The credit dola says it has left; None when the text says nothing about it."""
    text = text or ""
    match = _CREDITS_LEFT.search(text) or _ONLY_HAVE_LEFT.search(text)
    return int(match.group(1)) if match else None


def read_price_stated(text: str) -> int | None:
    """The price dola states for THIS generation ("It will use 2 credits" / "3 points"): the one truth about price.
    It is not uniform in length (2.5: 5 s = 2, 10 s = 4, 30 s = 2: the 30 s path is discounted)."""
    m = _PRICE_STATED.search(text or "")
    return int(m.group(1)) if m else None


def looks_out_of_credit(text: str) -> bool:
    """Is dola refusing because the ACCOUNT HAS NOT ENOUGH CREDIT. Two kinds: the daily limit is spent ("reached the
    daily limit...") and too little for this very order ("will use 2 video credits. You only have 1 left today").

    A content refusal with "no credits were used" is NOT one: dola refunded, the account still has credit."""
    low = (text or "").lower()
    if any(marker in low for marker in _OUT_OF_CREDIT_MARKERS):
        return True
    # dola mixes "will use 2 credits" | "will use 3 points" | "2 video credits": an older regex that required the
    # word "video" never matched, which made this branch dead code.
    need = re.search(r"will use\s+(\d+)\s+(?:video\s+)?(?:credits?|points?)", low)
    have = _ONLY_HAVE_LEFT.search(low)
    if need and have:
        return int(have.group(1)) < int(need.group(1))
    return False


# ================================= replies (v4 reply.py) ======================================================
# dola took the order and is rendering. WHITE LIST: only these sentences count.
_ACCEPTED_MARKERS = (
    "be ready in",                    # "...and be ready in 15 minutes"
    "will be generated using",        # "This video will be generated using the ... model"
    "let you know when it's ready",
    "send it to you when it's done",
    "your video is ready",
    "is being created",               # "Your 30-second video is being created now."
    "is being generated",
    "is being made",
)


def plain(text: str) -> str:
    """Lower case, markdown stripped, curly quotes made straight: the ONE form compared with the patterns. dola writes
    "Reply with **A** or **B**" and both "I can’t" and "I can't"."""
    return (text or "").lower().replace("*", "").replace("’", "'")


def order_accepted(text: str) -> bool:
    """Does the reply say the order was taken ("...will be generated using...", "...be ready in..."). Half of the
    question "is the order in": see `order_taken`."""
    low = plain(text)
    return any(marker in low for marker in _ACCEPTED_MARKERS)


# dola asks / offers options / asks for images instead of taking the order (spike 26/09, verbatim). Unmatched, the
# reply reads as "unclear", or worse, with the acceptance boilerplate of the old composer it reads as TAKEN: no
# re-send and a job waiting 2.5 hours for an order that does not exist. Vietnamese too: dola answers in the prompt's
# language.
_ASKS_INSTEAD = (
    "should i proceed",
    "which option do you want",
    "which version do you want",          # captured 28/09 (partial_cell_failed): the next re-send was accepted
    "reply with a or b",
    "please upload",
    "vui lòng tải lên",
    "please confirm",
    "if you'd like to proceed",
)

# dola ASKS rather than states. On the OLD composer the question comes with the acceptance boilerplate and one send
# still makes 0 videos, so a question means NOT taken.
_QUESTION_MARKERS = (
    "do you want me to",
    "would you like me to",
    "would you like to proceed",
    "please confirm",
    'reply "generate"',
    "reply 'generate'",
    "reply “generate”",
) + _ASKS_INSTEAD

def asks_question(text: str) -> bool:
    low = plain(text)
    return any(marker in low for marker in _QUESTION_MARKERS)


def order_taken(text: str, new_composer: bool) -> bool:
    """Has dola TAKEN the order in this conversation. A re-send after a taken order is a NEW order (paid twice) and
    the conversation must be KEPT to collect the result, whatever the next message says.

    An acceptance is enough, unless dola asked AND accepted on the old composer (`_QUESTION_MARKERS`). Measured 25/09
    over 35 conversations: 6 of 6 old-composer acceptances without a question made a video at the first send; a
    re-send only added a duplicate order or hit the daily limit and the conversation was dropped (5 paid videos
    lost). New composer: an acceptance is taken (24/09); it was never seen to ask and accept together."""
    return order_accepted(text) and (new_composer or not asks_question(text))


# A temporary error: retry; neither out of credit nor a broken account.
_TRANSIENT_MARKERS = (
    "something went wrong",
    "try again later",
    "temporarily unavailable",
)


def says_transient(text: str) -> bool:
    """Does the reply say dola failed for now ("Something went wrong"). `classify_reply` reads an acceptance FIRST, so a
    reply holding both is OK there; the gate has to see the contradiction itself."""
    low = plain(text)
    return any(marker in low for marker in _TRANSIENT_MARKERS)

# Content refusal (audio copyright). dola STILL charges and STILL adds the acceptance sentence ("will be generated...
# be ready in 15 minutes") but NEVER delivers a video (caught 13/09). Judged BEFORE the acceptance.
_CONTENT_REFUSED_MARKERS = (
    "for copyright protection",
    "can't show you the generated video",
    "cannot show you the generated video",
)

# The poll layer's public name: dola rendered and then blocked for copyright.
COPYRIGHT_BLOCK_MARKERS = _CONTENT_REFUSED_MARKERS

# dola REFUSES TO MAKE (unlike "can't SHOW" above): "I can't generate the video and didn't use any credit for this
# generation." It makes nothing, charges nothing, no video will come, and it still carries the acceptance boilerplate,
# so it must be judged BEFORE the acceptance or the poll never ends (caught 16/09, a meaningless i2v image). Terminal.
# The line "can't SHOW: wait, can't GENERATE: give up" was refuted by dola itself on 21/09: it said "can't generate ...
# didn't use any credit" and then DELIVERED a video. At the SUBMIT layer this is still a refusal (no conversation to
# wait on); at the POLL layer it is not final.
_CANNOT_GENERATE_MARKERS = (
    "can't generate the video",
    "cannot generate the video",
    "couldn't generate the video",
    "could not generate the video",
    # The second wording does not mention credit: "I can't generate the requested content. Try something else."
    # dola answers at once. Unmatched, the job polled nearly an hour into render.deadline (job 79c94a61, 24/09).
    "can't generate the requested content",
    "cannot generate the requested content",
    # The image version of that sentence (job 3312dcb5, 25/09): unmatched, an image job polled 2.5 hours.
    "can't generate the requested images",
    "cannot generate the requested images",
)

# One more kind of refusal where dola never says "cannot": the safety filter reads the prompt as self-harm risk and
# dola answers with advice and a help page link. No "can't", no "credit", no "proceed". Unmatched, the job polled an
# hour into render.deadline (job 14cba26b, 23/09: dola answered after 4 seconds). Caught by the LINK, not the wording:
# `help_resources` appears only in this kind of intervention, while advice wording is endless and would match an
# ordinary reminder. The video-extraction path still runs BEFORE this branch, so a poll that has a video wins.
_SAFETY_REFUSAL_MARKERS = (
    "help_resources",
)

# Portrait protection: a reference photo of a real person who is not the account owner is refused by Seedance 2.5 -
# "Use another image, or create a video from text". No generation, no charge, the same send is refused the same way.
# Unmatched before 24/09, the job polled an hour into render.deadline (job 21021d7e; dola answered on the first turn).
_LIKENESS_REFUSAL_MARKERS = (
    "likeness protection",
    "only supports generating videos featuring yourself",
)


def refusal_markers() -> tuple:
    """Every wording that makes a reply a CONTENT REFUSAL: the monitoring layer counts with the classifier's own
    vocabulary (a count query that copied two phrases once missed the "can't generate" branch)."""
    return (_CONTENT_REFUSED_MARKERS + _CANNOT_GENERATE_MARKERS + _SAFETY_REFUSAL_MARKERS
            + _LIKENESS_REFUSAL_MARKERS)


# A long or complex prompt makes dola GATE in words ("supports 4-15 seconds... proceed with 15s?"). But that is a LIE:
# **sending the prompt AGAIN in the SAME conversation makes dola take the order and render the real 30 s** (verified
# 14/09: gate -> re-send -> accept -> ffprobe 30.08 s; conversations sent once had 0 videos). So NEEDS_CONFIRM is NOT
# terminal: `video_possible=True`, the submit layer re-sends and the resolver polls for the video. dola words it many
# ways: "would"/"do", questions and statements that the length is over the cap.
_NEEDS_CONFIRM_MARKERS = (
    "do you want me to proceed",
    "would you like me to proceed",
    "would you like to proceed",
    "generation currently supports durations",
    "exceeds the current maximum supported duration",
    'reply "generate"',
    "reply 'generate'",
    "reply “generate”",
    # dola proposes instead of doing: "Would you like me to generate Part 1 only, Part 2 only, or both?" Unmatched,
    # the job took the conversation and waited 2.5 hours (job 35d578d7).
    "would you like me to generate",
) + _ASKS_INSTEAD


class ReplyVerdict(NamedTuple):
    outcome: SubmitOutcome | None      # None = not enough to say yet, wait
    detail: str
    credits_left: int | None
    # False only when a sentence SAYS there is no video. Reading nothing must stay True: a video that was paid for
    # may still be rendering.
    video_possible: bool = True
    # A code of its own when the refusal has one (likeness); None = the code follows `outcome`.
    code: str | None = None

    @property
    def decided(self) -> bool:
        return self.outcome is not None


_LENGTH_RE = re.compile(r"(\d{1,3})[- ]?(?:second|giây)", re.I)


def durations_named(text: str) -> list[int]:
    """Every length (seconds) dola NAMES in a reply, in order, to compare with the length we ASKED. dola often does
    not ask, it states: "I'll generate a 15-second version for you" for a 5 s order, with the acceptance boilerplate,
    so it passed as taken. Measured 22/09: 3 of 6 jobs came out the wrong length this way."""
    return [int(m.group(1)) for m in _LENGTH_RE.finditer(text or "")]


def classify_reply(text: str, want_duration: int | None = None) -> ReplyVerdict:
    """Read dola's reply and decide. `outcome=None` means the reply is not complete yet: wait and read again, NEVER
    assume success."""
    text = text or ""
    credits = read_credits_left(text)

    # A clear refusal first: it contains "try again" and would read as a temporary error.
    if looks_out_of_credit(text):
        return ReplyVerdict(SubmitOutcome.NO_POINTS, "dola refused: not enough credits", credits, video_possible=False)

    low = plain(text)

    # Copyright refusal BEFORE the acceptance: the sentence holds "be ready in 15 minutes" too, and judged after it
    # reads as OK and the resolver waits for a video that never comes. Terminal, video_possible=False.
    if any(marker in low for marker in _CONTENT_REFUSED_MARKERS):
        return ReplyVerdict(SubmitOutcome.CONTENT_REFUSED, "dola refused content (copyright) - credit spent, no video",
                            credits, video_possible=False)

    # Safety intervention: dola never says "cannot", it advises. Also a content refusal, in other words.
    if any(marker in low for marker in _SAFETY_REFUSAL_MARKERS):
        return ReplyVerdict(SubmitOutcome.CONTENT_REFUSED,
                            "dola refused on safety grounds and pointed to its help resources",
                            credits, video_possible=False)

    # Portrait protection: final, at send time and at poll time (the resolver shares this function).
    if any(marker in low for marker in _LIKENESS_REFUSAL_MARKERS):
        return ReplyVerdict(SubmitOutcome.CONTENT_REFUSED,
                            "dola refused for likeness protection: with a photo of a person, Seedance 2.5 only makes "
                            "videos of the account owner", credits, video_possible=False, code=codes.LIKENESS_REFUSED)

    # dola refuses to MAKE (no generation, no charge): BEFORE the acceptance, since it carries that boilerplate too.
    if any(marker in low for marker in _CANNOT_GENERATE_MARKERS):
        return ReplyVerdict(SubmitOutcome.CONTENT_REFUSED, "dola declined to generate the video", credits,
                            video_possible=False)

    # GATE BEFORE the acceptance, for the same reason: dola's question comes with the acceptance boilerplate ("The
    # video will use 2 credits and be ready in 5 minutes"), so judging acceptance first lets the acceptance win over
    # the question. Caught 22/09: a 5 s job read as taken and polled the 2-hour deadline while dola waited for an
    # answer.
    if any(marker in low for marker in _NEEDS_CONFIRM_MARKERS):
        # video_possible=True: a re-send in the conversation makes the real 30 s.
        return ReplyVerdict(SubmitOutcome.NEEDS_CONFIRM,
                            "dola gated (asked to confirm) - re-send in the same conversation to proceed",
                            credits, video_possible=True)

    # dola does not always ask. It often just STATES another length: "I'll generate a 15-second version for you" for
    # a 5 s order, with the acceptance boilerplate. Wording does not catch it; MEANING does: it names a length other
    # than the one we asked. Also a gate: a re-send in the conversation made dola do the right number (14/09).
    # Wrongly gating costs at most 2 re-sends; letting it through delivers the wrong length, charged.
    if want_duration:
        named = durations_named(text)
        if named and want_duration not in named:
            return ReplyVerdict(
                SubmitOutcome.NEEDS_CONFIRM,
                "dola announced %ss for a %ss request - re-send to hold it to the asked length"
                % ("/".join(str(x) for x in named), want_duration),
                credits, video_possible=True)

    # Acceptance AFTER the two gates, but BEFORE the balance: an account's last accepted order still says "You still
    # have 0 video credits left today", the balance AFTER the charge and not a refusal.
    if any(marker in low for marker in _ACCEPTED_MARKERS):
        return ReplyVerdict(SubmitOutcome.OK, "dola accepted the request", credits)

    if any(marker in low for marker in _TRANSIENT_MARKERS):
        return ReplyVerdict(SubmitOutcome.ERROR, "dola reported a temporary error - should retry", credits,
                            video_possible=False)

    # A balance of 0 with nothing else recognised ("You have 0 video credits left today.", "You only have 0 left")
    # is NOT a verdict: only a KNOWN refusal moves an order to another account, and no real dola sample ties a bare
    # zero balance to a refusal (dola's not-enough sentence always names the price: "will use 2 video credits. You
    # only have 1 left today", handled above). `outcome=None` (unclear) carrying `credits_left=0`: the caller records
    # the balance and lets the poll decide.

    return ReplyVerdict(None, "no clear reply yet", credits)


def no_result_cause(text: str) -> tuple[str, str] | None:
    """Why dola delivered nothing, read from its LAST words as (code, detail); None = nothing better than "expired".

    Before 26/09 every expired job carried `render.deadline`: wrong for 6 of 6 production jobs (3 refusals, 1 question
    instead of an order, 1 image instead of a video, 1 image refusal). A caller reading that code thinks "too slow"
    and sends the same order again while dola said exactly why."""
    if not text:
        return None
    verdict = classify_reply(text)
    # Refusals first: they carry the acceptance boilerplate and never produce a video.
    if verdict.outcome is SubmitOutcome.CONTENT_REFUSED:
        return verdict.code or codes.CONTENT_REFUSED, "dola refused and never delivered: %s" % verdict.detail
    # An order that went in and did not come out: a real render timeout.
    if order_accepted(text):
        return None
    if asks_question(text) or verdict.outcome is SubmitOutcome.NEEDS_CONFIRM:
        return codes.NEEDS_CONFIRM, "dola asked a question instead of taking the order, and never delivered"
    return None


# "The video will be generated using the **Dreamina Seedance 2.0 Mini model**." dola names its own model.
_MODEL_STATED = re.compile(r"generated using the\s*\**\s*([^*\n]{1,80}?)\s+model\b", re.I)


def named_model(text: str) -> str | None:
    """The model dola NAMES in a reply; None when it does not."""
    m = _MODEL_STATED.search(text or "")
    return m.group(1).strip() if m else None


def same_model(said: str, model_id: str) -> bool:
    """Is the name dola gave the model we asked for. Compared as WORD SETS without "Dreamina": "Seedance 2.0" and
    "Seedance 2.0 Fast" are one model, "2.0 Mini" and "2.0 Fast" are not. A model with no label counts as the same:
    there is not enough ground to complain."""
    want = MODEL_LABELS.get(model_id)
    if not said or not want:
        return True

    def words(x: str) -> set:
        return set(re.sub(r"[^a-z0-9.]+", " ", x.lower().replace("dreamina", "")).split())

    return words(said) <= words(want) or words(want) <= words(said)


def model_substitution(text: str, model_id: str) -> str | None:
    """A warning when dola STATES another model than the one asked; None when it matches or says nothing. Job
    36389eee asked for Seedance 2.5 and dola said "Dreamina Seedance 2.0 Mini": delivered, with a warning (user,
    24/09)."""
    said = named_model(text)
    if not said or same_model(said, model_id):
        return None
    return "dola used %s instead of %s" % (said, MODEL_LABELS.get(model_id, model_id))
