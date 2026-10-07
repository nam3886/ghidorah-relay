"""The gate (a re-send in the same conversation when dola asks instead of taking the order), built on the MESSAGES
of the engine's own chain read: ONE pure judgement, `judge()`, used at every decision point, and a scenario matrix.

The rules every row pins:
- a read is USABLE only if it holds every send made so far (fewer user messages = stale or partial = decides nothing);
- a reply is judged WHOLE (one dola reply holds blank lines of its own) and only when SETTLED (the chain's own flag);
- taken = some settled reply is an acceptance by the composer's rule; once taken nothing is sent again;
- the verdict is read from the NEWEST reply after the latest send; a failed read or a page text decides nothing;
- the balance of a taken order comes from the accepting reply; at most two re-sends."""

import json

from kit_c import (
    ACCEPTED,
    CONV,
    COPYRIGHT,
    DAILY_LIMIT,
    FIXTURES,
    LIKENESS,
    NOW,
    SENT,
    SOMETHING_WRONG,
    Scene,
    chain_body,
    fail_own_reads,
    make_job,
    real_reply,
    stale_own_reads,
    stale_page_read,
    sync,
)

from relay_core.providers import Accepted, Balance, Fail, NotTaken, Rest, Success
from relay_core.testing.mutation import mutation
from relay_dola import classify, errors
from relay_dola.engine import resolver
from relay_dola.engine.submit import REPLY_TRIES, _Run, judge, read_thread

SUB = "packages/provider-dola/src/relay_dola/engine/submit.py"
URL = "https://www.dola.com/chat/" + CONV
Q = ("Video generation currently supports durations from 4 to 15 seconds. "
     "Would you like to proceed with 15 seconds?")
ASK = "Would you like me to generate Part 1 only, Part 2 only, or both?"
DUR = "I'll generate a 15-second version for you."
UNCLEAR = "Okay, working on it."
ZERO = "You have 0 video credits left today."
ASIDE = "Anything else I can help with?"
HALF = "Video generation currently supports durations from 4 to 15 seconds."
BOILER = "The video will use 2 credits and be ready in 5 minutes."                       # job 912d715f
NOTICE_Q = "Video generation currently supports durations from 4 to 15 seconds. Should I proceed?"
PROCEED_SPLIT = [BOILER, "\n\n \n" + NOTICE_Q]            # ONE reply: the boilerplate, dola's blank line, the question
QUOTE_Q = ("You asked for a sign that says \"your video is ready\".\n\nVideo generation currently supports durations "
           "from 4 to 15 seconds. Would you like to proceed with 15 seconds?")
OLD_GATE_AND_ACCEPT = ACCEPTED + " " + ASK
BLANK_ASK = [ACCEPTED, "\n\n \n" + ASK]                   # the same, with a blank line inside the reply
NO_BALANCE = ("The video will be generated using the **Dreamina Seedance 2.5 model**. It will use 2 credits and be "
              "ready in 25 minutes. I'll send it to you when it's done.")
QUOTING_SENT = 'a sign that says "your video is ready" over a lake [7100]'
ECHO_Q = 'You asked for a sign saying \u201cyour video is ready\u201d. Would you like to proceed with 15 seconds?'
HOSTILE_SENT = "my notes: " + ACCEPTED + " [7100]"        # the prompt holds dola's own acceptance line
ASKED = ("Video generation currently supports durations from 4 to 15 seconds. Do you want me to proceed "
         "with 15 seconds? The video will be generated using the **Dreamina Seedance 2.5 model**.")
BOILER_HALF = ("The video will be generated using the **Dreamina Seedance 2.5 model**. It will use 2 credits and be "
               "ready in 25 minutes.")
DECOY = Q + "\n" + ACCEPTED + "\n7 video credits left today\nNot Enough Credits\nYou have 0 video credits left today."
MIDNIGHT = NOW.replace(day=6, hour=0, minute=0)

T2, TN, T0 = ("taken", 2), ("taken", None), ("taken", 0)     # Success, with the Balance dola reported (or none)
MAYBE, ZERO_LEFT, POINTS, TEMP, REFUSED = "maybe", "zero", "points", "temporary", "refused"
MAYBE2 = ("maybe", 2)         # not taken, with the balance dola reported in its question
OLD, NEW = False, True

# name -> replies by send number, `late` (one per tick), `late2` (appended after the SECOND send), the page text,
# the base (sends, result) for the old and the new composer, and which modes apply.
SCENARIOS = {
    "acceptance": dict(replies={1: [ACCEPTED]}, base=(1, T2)),
    "question > acceptance": dict(replies={1: [Q], 2: [ACCEPTED]}, base=(2, T2)),
    "question > acceptance + aside": dict(replies={1: [Q], 2: [ACCEPTED, ASIDE]}, base=(2, T2), tag="balance"),
    "question > unclear": dict(replies={1: [Q], 2: [UNCLEAR]}, base=(2, MAYBE)),
    "question > zero balance": dict(replies={1: [Q], 2: [ZERO]}, base=(2, ZERO_LEFT)),
    "question > refusal": dict(replies={1: [Q], 2: [DAILY_LIMIT]}, base=(2, POINTS)),
    "question > something went wrong": dict(replies={1: [Q], 2: [SOMETHING_WRONG]}, base=(2, TEMP)),
    "question > late acceptance": dict(replies={1: [Q]}, late=[ACCEPTED], base=(1, T2)),
    "question > late unclear": dict(replies={1: [Q]}, late=[UNCLEAR], base=(1, MAYBE)),
    "question > acceptance after the re-send": dict(replies={1: [Q]}, late2=[ACCEPTED], base=(2, T2)),
    "question > refusal after the re-send": dict(replies={1: [Q]}, late2=[DAILY_LIMIT], base=(2, POINTS)),
    "question > dola silent after the re-send": dict(replies={1: [Q]}, base=(2, MAYBE), tag="silent"),
    "refusal": dict(replies={1: [DAILY_LIMIT]}, base=(1, POINTS)),
    "something went wrong": dict(replies={1: [SOMETHING_WRONG]}, base=(1, TEMP)),
    "question + refusal in one send": dict(replies={1: [Q, DAILY_LIMIT]}, base=(1, POINTS), tag="newest"),
    "question > question + refusal": dict(replies={1: [Q], 2: [Q, DAILY_LIMIT]}, base=(2, POINTS), tag="newest"),
    "acceptance then a question": dict(replies={1: [ACCEPTED, Q]}, base=(1, T2), tag="taken"),
    "ask instead > other length + acceptance": dict(replies={1: [ASK], 2: [DUR + " " + ACCEPTED]}, base=(2, T2)),
    "ask instead > other length + acceptance + aside": dict(replies={1: [ASK], 2: [DUR + " " + ACCEPTED, ASIDE]},
                                                            base=(2, T2), tag="balance"),
    "other length > acceptance": dict(replies={1: [DUR], 2: [ACCEPTED]}, base=(2, T2)),
    "question x3": dict(replies={1: [Q], 2: [Q], 3: [Q]}, base=(3, MAYBE), three=True),
    "question x3 > acceptance": dict(replies={1: [Q], 2: [Q], 3: [ACCEPTED]}, base=(3, T2), three=True),
    "question x3 > unclear": dict(replies={1: [Q], 2: [Q], 3: [UNCLEAR]}, base=(3, MAYBE), three=True),
    "question x3 > zero balance": dict(replies={1: [Q], 2: [Q], 3: [ZERO]}, base=(3, ZERO_LEFT), three=True),
    "question x3 > something went wrong": dict(replies={1: [Q], 2: [Q], 3: [SOMETHING_WRONG]}, base=(3, TEMP),
                                               three=True),
    "question x3 > refusal": dict(replies={1: [Q], 2: [Q], 3: [DAILY_LIMIT]}, base=(3, POINTS), three=True),
    "silence": dict(replies={}, base=(1, MAYBE)),
    # one reply that holds blank lines of its own (7 of the 16 real captures do)
    "boilerplate + blank line + question, one reply": dict(replies={1: [PROCEED_SPLIT], 2: [ACCEPTED]},
                                                           base={OLD: (2, T2), NEW: (1, TN)}, tag="whole"),
    "acceptance + blank line + question, one reply": dict(replies={1: [BLANK_ASK], 2: [ACCEPTED]},
                                                          base={OLD: (2, T2), NEW: (1, T2)}, tag="whole",
                                                          unsure=MAYBE2),
    "quote + blank line + question, one reply": dict(replies={1: [QUOTE_Q], 2: [ACCEPTED]},
                                                     base={OLD: (2, T2), NEW: (1, MAYBE)}, sent=QUOTING_SENT,
                                                     tag="echo"),
    "dola echoes the customer's quoted words, then asks": dict(replies={1: [ECHO_Q], 2: [ACCEPTED]},
                                                              base={OLD: (2, T2), NEW: (1, MAYBE)},
                                                              sent=QUOTING_SENT, tag="echo"),
    "the prompt holds dola's acceptance line and dola answers with it": dict(
        replies={n: [[DUR, "\n" + ACCEPTED]] for n in (1, 2, 3)}, sent=HOSTILE_SENT, base=(1, T2), tag="own"),
    "the prompt holds the word generate, dola asks Reply generate": dict(
        replies={1: [BOILER + ' Reply "generate" to confirm.'], 2: [ACCEPTED]}, sent='Generate a red kite [7100]',
        base={OLD: (2, T2), NEW: (1, TN)}, tag="own"),
    "the prompt holds please confirm, dola quotes it beside unknown wording": dict(
        replies={1: ['You wrote "please confirm". Let me think about it.']},
        sent='say "please confirm" over a lake [7100]', base=(1, MAYBE), tag="own"),
    # a reply that says BOTH that dola took the order and that it refused or failed is contradictory: unclear, the job
    # is neither re-sent, nor moved, nor rested; the poll decides
    "a refusal and acceptance boilerplate in one reply": dict(
        replies={1: [DAILY_LIMIT + " Your video will be ready in 5 minutes."]}, base=(1, MAYBE), tag="contradiction"),
    "out of credit and acceptance boilerplate in one reply": dict(
        replies={1: ["You don't have enough credits. " + BOILER_HALF]}, base=(1, MAYBE), tag="contradiction"),
    "something went wrong and acceptance boilerplate in one reply": dict(
        replies={1: [SOMETHING_WRONG + " " + BOILER_HALF]}, base=(1, MAYBE), tag="transient"),
    "copyright and acceptance boilerplate in one reply": dict(
        replies={1: [COPYRIGHT + " " + BOILER_HALF]}, base=(1, MAYBE), tag="contradiction"),
    "likeness and acceptance boilerplate in one reply": dict(
        replies={1: [LIKENESS + " " + BOILER_HALF]}, base=(1, MAYBE), tag="contradiction"),
    "copyright alone": dict(replies={1: [COPYRIGHT]}, base=(1, REFUSED)),
    "likeness alone": dict(replies={1: [LIKENESS]}, base=(1, REFUSED)),
    # a prompt that itself begins with dola's own bubble words is still counted
    "prompt starting with Generate image:": dict(replies={1: [Q], 2: [ACCEPTED]}, sent="Generate image: a lake [7100]",
                                                 base=(2, T2), tag="prefix"),
    "prompt starting with Generate video:": dict(replies={1: [Q], 2: [ACCEPTED]}, sent="Generate video: a lake [7100]",
                                                 base=(2, T2), tag="prefix"),
    "prompt starting with Generated video:": dict(replies={1: [Q], 2: [ACCEPTED]},
                                                  sent="Generated video: a lake [7100]", base=(2, T2), tag="prefix"),
    "an acceptance and an unrecognised question in one reply": dict(
        replies={1: ["It will be ready in 5 minutes. Shall I proceed?"]}, base={OLD: (1, MAYBE), NEW: (1, TN)},
        tag="unknown"),
    "a question settled only by repetition is never re-sent": dict(
        replies={1: [("assistant_open", Q)]}, base=(1, MAYBE), tag="final", modes=("none",)),
    "accepted and asking in one reply": dict(replies={1: [OLD_GATE_AND_ACCEPT], 2: [ACCEPTED]},
                                             base={OLD: (2, T2), NEW: (1, T2)}, unsure=MAYBE2),
    # a reply still being written
    "question written in two reads": dict(replies={1: [("assistant_open", HALF)], 2: [ACCEPTED]},
                                          late=[("complete", Q)], base=(2, T2)),
    "boilerplate first, the question appended later": dict(
        replies={1: [("assistant_open", BOILER_HALF)], 2: [ACCEPTED]}, late=[("complete", ASKED)],
        base={OLD: (2, T2), NEW: (1, TN)}, tag="settled"),
    # a reply that is still being written is never judged on a partial; the newest unfinished reply hides an older
    # settled question for good, and the wait that ends it is bounded
    "a reply that grows at every read never settles": dict(
        replies={1: [("assistant_open", Q)]}, late=[("grow", Q + " " + "." * i) for i in range(1, 40)],
        base=(1, MAYBE), tag="settled", modes=("none",)),
    "a settled question, then a newer reply still being written": dict(
        replies={1: [Q, ("assistant_open", "Okay")]}, late=[("grow", "Okay" + "." * i) for i in range(1, 40)],
        base=(1, MAYBE), tag="hidden", modes=("none",)),
    "a settled question, then a newer unfinished reply that stays the same": dict(
        replies={1: [Q, ("assistant_open", "Okay")]}, base=(1, MAYBE), tag="hidden", modes=("none",)),
    # the balance belongs to the accepting reply
    "an old balance, then an acceptance without one": dict(
        replies={1: [Q + " You have 4 video credits left today."], 2: [NO_BALANCE]}, base=(2, TN), tag="balance",
        modes=("none", "outage in the wait after re-send 1", "stale page read")),
    # what the page shows beside the conversation decides nothing
    "page text with an acceptance, no usable read": dict(replies={}, base=(1, MAYBE), page=DECOY,
                                                        modes=("outage before re-send 1",)),
    "page widget + acceptance": dict(replies={1: [ACCEPTED]}, base=(1, T2), page=DECOY,
                                     modes=("first good read at the end of the wait",)),
    # an acceptance, then a LATER content refusal in a SEPARATE reply (copyright; "no credit used"): the job ends at
    # once with the refusal (D13), never a re-send, a rest or a move; the acceptance's balance is still reported
    "real: acceptance, then the no-charge refusal, both in the first read (no_charge-40b47fe1)": dict(
        replies={1: [real_reply("no_charge-40b47fe1", 0), real_reply("no_charge-40b47fe1", 1)]},
        base=(1, ("refused", 0)), real=True, tag="late-refusal"),
    "real: copyright capture, statements, acceptance and refusal in the first read": dict(
        replies={1: [real_reply("copyright-20900298", n) for n in range(4)]}, base=(1, ("refused", 2)), real=True,
        tag="late-refusal"),
    "real: copyright capture, acceptance and refusal in the first read": dict(
        replies={1: [real_reply("copyright-20900298", 2), real_reply("copyright-20900298", 3)]},
        base=(1, ("refused", 2)), real=True, tag="late-refusal"),
    "real: copyright capture, a re-send answered by the acceptance and the refusal": dict(
        replies={1: [real_reply("copyright-20900298", 0)],
                 2: [real_reply("copyright-20900298", 2), real_reply("copyright-20900298", 3)]},
        base=(2, ("refused", 2)), real=True, tag="late-refusal"),
    "real: a re-send answered by the no-charge acceptance and refusal": dict(
        replies={1: [real_reply("copyright-20900298", 0)],
                 2: [real_reply("no_charge-40b47fe1", 0), real_reply("no_charge-40b47fe1", 1)]},
        base=(2, ("refused", 0)), real=True, tag="late-refusal"),
    # real captured conversations (fixtures/dola/chains), replies in the order dola wrote them
    "real: question, then the multi-block acceptance (multi_video-32c6fe27)": dict(
        replies={1: [real_reply("multi_video-32c6fe27", 0)], 2: [real_reply("multi_video-32c6fe27", 1)]},
        base=(2, ("taken", 0)), real=True),
    "real: statements, a question, then the acceptance (partial_cell_failed-36389eee)": dict(
        replies={n + 1: [real_reply("partial_cell_failed-36389eee", n)] for n in range(3)}, base=(3, ("taken", 1)),
        real=True, three=True),
    "real: statement, statement, then the 2-block acceptance (copyright-840aa471)": dict(
        replies={n + 1: [real_reply("copyright-840aa471", n)] for n in range(3)}, base=(3, T0), real=True, three=True),
    "real: recommends splitting, blank lines inside (split_30s_into_15s-35d578d7)": dict(
        replies={1: [real_reply("split_30s_into_15s-35d578d7", 0)]}, base=(2, MAYBE), real=True),
    "real: refusal written by dola (no_charge-dc7e8c18, third reply)": dict(
        replies={1: [real_reply("no_charge-dc7e8c18", 2)]}, base=(1, REFUSED), real=True),
}
ALL_MODES = ("none", "outage before re-send 1", "outage at the read before re-send 1",
             "outage in the wait after re-send 1", "stale page read", "stale own reads after re-send 1",
             "first good read at the end of the wait")


def base_of(sc, new_composer: bool):
    return sc["base"][new_composer] if isinstance(sc["base"], dict) else sc["base"]


def expected(name: str, new_composer: bool, mode: str):
    sc = SCENARIOS[name]
    base = base_of(sc, new_composer)
    sends = base[0]
    if mode == "outage before re-send 1":
        return 1, MAYBE                         # nothing usable was ever read: left to the poll
    unsure = sc.get("unsure", MAYBE)            # what a question that was read but never answered ends as
    if mode == "outage at the read before re-send 1" and sends >= 2:
        return 1, unsure                        # the read that would have decided a re-send failed: none
    if mode == "stale page read" and sc.get("three"):
        return 2, unsure                        # the read before the third send fails
    if mode == "stale own reads after re-send 1" and sends >= 2:
        return 2, unsure                        # every later read lacks the new send: stale, undecided
    return base


def applies(name: str, mode: str) -> bool:
    sc = SCENARIOS[name]
    if "modes" in sc:
        return mode in sc["modes"]
    return not (mode == "stale page read" and name == "silence")


ROWS = [(nc, name, mode) for name in SCENARIOS for mode in ALL_MODES if applies(name, mode)
        for nc in (OLD, NEW)]


async def run_row(new_composer: bool, name: str, mode: str, user_kind: str | None = None):
    sc = SCENARIOS[name]
    kw = dict(replies=sc["replies"], new_composer=new_composer, url=URL,
              user_kind=user_kind or ("user_blocks" if new_composer else "user"))
    if sc.get("sent"):
        kw["job"] = make_job(sent_text=sc["sent"])
    if sc.get("late"):
        kw["late"] = list(sc["late"])
    feed = stale_page_read(HALF)
    late2 = list(sc.get("late2") or [])

    async def on_send(world, n):
        if n == 2:
            world.late.extend(late2)
        if mode in ("outage before re-send 1", "stale page read", "first good read at the end of the wait") and n == 1:
            await feed(world, n)

    kw["on_send"] = on_send
    fails = None
    if mode == "outage before re-send 1":
        fails = lambda sends, k: sends == 1                                          # noqa: E731
    elif mode == "outage at the read before re-send 1":
        fails = lambda sends, k: sends == 1 and k >= 6                               # noqa: E731
    elif mode == "outage in the wait after re-send 1":
        fails = lambda sends, k: sends == 2 and k <= REPLY_TRIES                     # noqa: E731
    elif mode == "stale page read":
        fails = lambda sends, k: sends == 2 and k >= REPLY_TRIES                     # noqa: E731
    elif mode == "first good read at the end of the wait":
        fails = lambda sends, k: sends == 1 and k <= REPLY_TRIES                     # noqa: E731
    if mode in ("outage before re-send 1", "stale page read", "first good read at the end of the wait"):
        kw["polls"] = False
        kw["page_text"] = sc.get("page", DECOY)
    scene = Scene(**kw)
    if fails is not None:
        fail_own_reads(scene, fails)
    if mode == "stale own reads after re-send 1":
        stale_own_reads(scene, lambda sends, k: True)
    res = await scene.run()
    return scene, res


def result_kind(res):
    job, account = res.job, res.account
    if isinstance(job, NotTaken):
        if job.code == "pool.account_out_of_points" and account == (Rest("video", MIDNIGHT, "out of credit"),):
            return POINTS
        if job.code == errors.TEMPORARY_ERROR and account == ():
            return TEMP
        return "unexpected: %r %r" % (job, account)
    if isinstance(job, Fail):
        if job.code in (errors.CONTENT_REFUSED, errors.LIKENESS_REFUSED):
            if account == ():
                return REFUSED
            if len(account) == 1 and isinstance(account[0], Balance) and account[0].pool == "video":
                return ("refused", account[0].left_n)          # the balance the acceptance reported; no Rest
        return "unexpected: %r %r" % (job, account)
    if not isinstance(job, Accepted):
        return "unexpected: %r" % (job,)
    if job.taken and account[:1] == (Success(),):
        rest = account[1:]
        if not rest:
            return TN
        if rest[0] == Balance("video", 0, "reported") and len(rest) == 2 and isinstance(rest[1], Rest):
            return T0
        if len(rest) == 1 and isinstance(rest[0], Balance) and rest[0].pool == "video":
            return ("taken", rest[0].left_n)
    if not job.taken and account == ():
        return MAYBE
    if not job.taken and account == (Balance("video", 2, "reported"),):
        return MAYBE2
    if not job.taken and len(account) == 2 and account[0] == Balance("video", 0, "reported") \
            and isinstance(account[1], Rest) and account[1].pool == "video":
        return ZERO_LEFT
    return "unexpected: taken=%s %r" % (job.taken, account)


SYMPTOM = {
    "stale own reads after re-send 1": "a stale read was judged",
    "silent": "a re-send was judged on the old question",
    "newest": "the newest reply was not the one judged",
    "taken": "a taken order was sent again",
    "whole": "a reply was split on its blank lines",
    "settled": "a half-written reply was judged",
    "balance": "the balance was not the accepting reply's",
    "echo": "dola's echo of the customer's words was judged",
    "hidden": "an unfinished reply hid a settled question",
    "own": "the customer's own words decided",
    "refusal": "a refusal in the same reply was taken as an acceptance",
    "unknown": "an unrecognised question did not stop an acceptance",
    "final": "a reply without dola's flag was re-sent on",
    "late-refusal": "a content refusal after an acceptance was kept as taken",
    "contradiction": "a contradictory reply moved the job",
    "transient": "a temporary error with acceptance wording was taken",
    "prefix": "a prompt that starts like dola's bubble was not counted",
}


def symptom(name: str, mode: str) -> str:
    """One distinct tag per rule, chosen by the row, so a mutant is only killed by the rows that show ITS symptom."""
    if mode == "stale own reads after re-send 1":
        return SYMPTOM[mode]
    return SYMPTOM.get(SCENARIOS[name].get("tag"), "the gate decided wrongly")


SETTLE_ON = "        return m.final or (len(older) == 2 and all((m.id, m.text) in known for known in older))\n"
SETTLE_TWO = "        return m.final or (len(older) >= 1 and all((m.id, m.text) in known for known in older))\n"
FINAL_ON = '    return (isinstance(ext, dict) and ext.get("is_finish") == "1"\n'
FINAL_BLOCK = "    return (True\n"
CARRIES_ON = ("        typed = _norm(self.sent_text)\n"
              "        return [i for i, m in enumerate(self.msgs) if typed and m.user "
              "and typed in _bubble_forms(m.text)]\n")
CARRIES_PLAIN = ('        typed = " ".join(self.sent_text.split())\n'
                 '        return [i for i, m in enumerate(self.msgs) if typed and m.user '
                 'and typed == " ".join(m.text.split())]\n')
CARRIES_ANY = ("        typed = _norm(self.sent_text)\n"
               "        return [i for i, m in enumerate(self.msgs) if typed and m.user]\n")
TYPED_ON = "        typed = _norm(self.sent_text)\n"
TYPED_STRIPPED = '        typed = _BUBBLE_HEAD.sub("", _norm(self.sent_text), count=1).strip()\n'
CONTRA_ON = "    if classify.order_accepted(text) and refused:\n"
CONTRA_OFF = "    if False:\n"
TRANS_ON = "    if classify.order_accepted(text) and classify.says_transient(text):\n"
TRANS_OFF = "    if False:\n"
FIRST_ON = "    mine = [m for m in thread.msgs[ours[0] + 1:] if not m.user]\n"
FIRST_OFF = "    mine = [m for m in thread.msgs if not m.user]\n"
DEDUPE_ON = "            if mid in seen:\n                continue\n"
DEDUPE_OFF = "            if False:\n                continue\n"
INDEX_ON = "    except (KeyError, TypeError, ValueError):\n        return None\n"
INDEX_OFF = "    except (KeyError, TypeError, ValueError):\n        raw = list(reversed(raw))\n"
MARK_ON = "        self.sends_made = 1               # counted AT the Enter\n"
FENCE_ON = "        self.sends_made += 1              # counted AT the Enter\n"
CLASSIFY = "packages/provider-dola/src/relay_dola/classify.py"
VERSION_ON = ('    "which version do you want",          # captured 28/09 (partial_cell_failed): the next re-send was '
              'accepted\n')
KEEP_ON = ("                                     classify.SubmitOutcome.ERROR):\n"
           "        return classify.ReplyVerdict(classify.SubmitOutcome.OK, \"dola accepted the request\", "
           "verdict.credits_left)\n")
KEEP_OFF = ("                                     classify.SubmitOutcome.ERROR, "
            "classify.SubmitOutcome.CONTENT_REFUSED):\n"
            "        return classify.ReplyVerdict(classify.SubmitOutcome.OK, \"dola accepted the request\", "
            "verdict.credits_left)\n")
DOUBLE_ON = "    if (raw.taken, raw.verdict.outcome) != (clean.taken, clean.verdict.outcome):\n"
DOUBLE_OFF = "    if False:\n"
WORDS_ON = '        return " " if " " in words and words in prompt else m.group(0)\n'
WORDS_OFF = '        return " " if words in prompt else m.group(0)\n'
OLD_Q_ON = ('    if not new_composer and verdict.outcome is classify.SubmitOutcome.OK '
            'and ("?" in text or "\\uff1f" in text):\n')
OLD_Q_OFF = "    if False:\n"
FLAG_ON = "        if verdict.outcome is classify.SubmitOutcome.NEEDS_CONFIRM and not newest.final:\n"
FLAG_OFF = "        if False:\n"
UNUSABLE_ON = "    if thread is None or len(ours) < sends_made:\n"
UNUSABLE_OFF = "    if thread is None:\n"
AFTER_ON = "    after = [m for m in thread.msgs[last_user + 1:] if not m.user]\n"
AFTER_OFF = "    after = [m for m in thread.msgs if not m.user]\n"
SETTLED_OFF = "        return True\n"
WHOLE_ON = "    accepting = next((m for m in reversed(mine) if settled(m) and read(m).taken), None)\n"
WHOLE_OFF = ("    accepting = next((m for m in reversed(mine) if settled(m) "
             "and classify.order_taken(m.text.split(\"\\n\\n\")[0], new_composer)), None)\n")
NEWEST_ON = "    newest = after[-1] if after else None\n"
NEWEST_OFF = "    newest = after[0] if after else None\n"
TAKEN_ON = "        return j.verdict.outcome is ask and not j.taken\n"
TAKEN_OFF = "        return j.verdict.outcome is ask\n"
CREDITS_ON = "        verdict = verdict._replace(credits_left=read(accepting).verdict.credits_left)\n"
CREDITS_OFF = ("        verdict = verdict._replace(\n"
               "            credits_left=classify.read_credits_left(newest.text if newest else \"\"))\n")


@mutation(SUB, KEEP_ON, KEEP_OFF, expect="a content refusal after an acceptance was kept as taken")
@mutation(SUB, DOUBLE_ON, DOUBLE_OFF, expect="dola's echo of the customer's words was judged")
@mutation(SUB, WORDS_ON, WORDS_OFF, expect="the customer's own words decided")
@mutation(SUB, OLD_Q_ON, OLD_Q_OFF, expect="an unrecognised question did not stop an acceptance")
@mutation(SUB, FLAG_ON, FLAG_OFF, expect="a reply without dola's flag was re-sent on")
@mutation(SUB, CONTRA_ON, CONTRA_OFF, expect="a contradictory reply moved the job")
@mutation(SUB, TRANS_ON, TRANS_OFF, expect="a temporary error with acceptance wording was taken")
@mutation(SUB, UNUSABLE_ON, UNUSABLE_OFF, expect="a stale read was judged")
@mutation(SUB, AFTER_ON, AFTER_OFF, expect="a re-send was judged on the old question")
@mutation(SUB, SETTLE_ON, SETTLED_OFF, expect="a half-written reply was judged")
@mutation(SUB, WHOLE_ON, WHOLE_OFF, expect="a reply was split on its blank lines")
@mutation(SUB, NEWEST_ON, NEWEST_OFF, expect="the newest reply was not the one judged")
@mutation(SUB, TAKEN_ON, TAKEN_OFF, expect="a taken order was sent again")
@mutation(SUB, CREDITS_ON, CREDITS_OFF, expect="the balance was not the accepting reply's")
@sync
async def test_the_gate_matrix_over_the_message_list():
    bad = []
    for nc, name, mode in ROWS:
        want = expected(name, nc, mode)
        scene, res = await run_row(nc, name, mode)
        got = (scene.log.count("send"), result_kind(res))
        if got != want:
            bad.append("[%s] %s composer | %s | %s: got %r, expected %r" % (
                symptom(name, mode), "new" if nc else "old", name, mode, got, want))
    assert not bad, "%d of %d rows:\n%s" % (len(bad), len(ROWS), "\n".join(bad))


def test_the_matrix_is_as_big_as_it_claims():
    assert len(ROWS) == 828, "the matrix has %d rows" % len(ROWS)


@sync
async def test_the_user_message_shape_does_not_matter_both_shapes_give_the_same_rows():
    for name in ("question > acceptance", "question x3", "boilerplate + blank line + question, one reply",
                 "question > dola silent after the re-send"):
        for nc in (OLD, NEW):
            outcomes = set()
            for shape in ("user", "user_blocks"):
                scene, res = await run_row(nc, name, "none", user_kind=shape)
                outcomes.add((scene.log.count("send"), str(result_kind(res))))
            assert len(outcomes) == 1 and outcomes.pop() == (expected(name, nc, "none")[0],
                                                             str(expected(name, nc, "none")[1])), (
                "the shape of the user message changed the result: %s %s" % (name, nc))


# ---- judge(), pure, on message lists -------------------------------------------------------------------------------
def thread(*items, sent=SENT):
    """items: ("user", text) | ("user_blocks", text) | ("assistant", text or [blocks]) | ("assistant_open", text)"""
    return read_thread(chain_body(list(items), CONV), sent)


def judged(items, sends=1, nc=False, history=(), duration=30):
    return judge(thread(*items), sends, nc, duration, history)


def test_a_failed_read_and_a_stale_read_decide_nothing():
    for failed in (None, read_thread({}, SENT)):
        out = judge(failed, 1, False, 30)
        assert out.usable is False and out.taken is False and out.verdict.outcome is None
    stale = judged([("user", SENT), ("assistant", Q)], sends=2)
    assert stale.usable is False and stale.verdict.outcome is None, "a stale read was judged"
    assert judged([("user", SENT), ("assistant", Q), ("user", SENT)], sends=2).usable is True


@mutation(SUB, CARRIES_ON, CARRIES_ANY,
          expect="a user message without the sent text counted")
def test_only_a_user_message_that_carries_the_sent_text_counts_as_a_send():
    images = [("user_blocks", "[2 images attached]"), ("user", SENT), ("assistant", Q)]     # an i2v send: two messages
    assert thread(*images).sends_seen == 1, "a user message without the sent text counted as a send"
    assert judged(images, sends=2).usable is False


def test_nothing_after_the_latest_send_is_undecided_and_never_a_question():
    out = judged([("user", SENT), ("assistant", Q), ("user", SENT)], sends=2)
    assert out.usable and out.verdict.outcome is None and out.taken is False, "an old reply was judged for the new send"


def test_a_first_question_is_not_taken_and_gates():
    out = judged([("user", SENT), ("assistant", Q)])
    assert (out.taken, out.verdict.outcome) == (False, classify.SubmitOutcome.NEEDS_CONFIRM)


def test_a_reply_is_judged_whole_the_old_composer_reads_boilerplate_and_question_of_one_reply_as_not_taken():
    for blocks in (PROCEED_SPLIT, BLANK_ASK, OLD_GATE_AND_ACCEPT):
        out = judged([("user", SENT), ("assistant", blocks)], nc=False)
        assert out.taken is False and out.verdict.outcome is classify.SubmitOutcome.NEEDS_CONFIRM, (
            "a reply was split on its blank lines: %r" % (blocks,))
        assert judged([("user", SENT), ("assistant", blocks)], nc=True).taken is True
    separate = judged([("user", SENT), ("assistant", Q), ("assistant", ACCEPTED)], nc=False)
    assert separate.taken is True, "an acceptance in its own, later reply is taken"


def test_a_late_acceptance_in_its_own_reply_is_taken_on_both_composers_and_never_sent_again():
    items = [("user", SENT), ("assistant", Q), ("assistant", ACCEPTED)]
    for nc in (OLD, NEW):
        out = judged(items, nc=nc)
        assert out.taken is True and out.verdict.credits_left == 2


def test_the_verdict_is_the_newest_reply_after_the_latest_send_not_the_first():
    items = [("user", SENT), ("assistant", Q), ("user", SENT), ("assistant", Q), ("assistant", DAILY_LIMIT)]
    out = judged(items, sends=2)
    assert out.verdict.outcome is classify.SubmitOutcome.NO_POINTS, "the newest reply was not the one judged"
    assert out.newest == DAILY_LIMIT and out.text.startswith(DAILY_LIMIT)


@mutation(SUB, SETTLE_ON, SETTLE_TWO, expect="a reply was settled on two reads")
def test_a_reply_without_the_messages_flag_is_settled_only_when_the_same_text_stood_in_three_reads_in_a_row():
    first = thread(("user", SENT), ("assistant_open", Q))
    assert first.msgs[-1].final is False
    assert judge(first, 1, False, 30).verdict.outcome is None, "a reply with no flag was judged at its first sight"
    one_read = thread(("user", SENT), ("assistant_open", ACCEPTED))
    assert judge(one_read, 1, False, 30, (one_read,)).taken is False, "a reply was settled on two reads"
    out = judge(first, 1, False, 30, (first, first))
    assert out.verdict.outcome is None, "a reply without dola's flag was re-sent on"
    unclear = thread(("user", SENT), ("assistant_open", UNCLEAR))
    assert judge(unclear, 1, False, 30, (unclear, unclear)).newest == UNCLEAR
    accepted = thread(("user", SENT), ("assistant_open", ACCEPTED))
    assert judge(accepted, 1, False, 30).taken is False
    assert judge(accepted, 1, False, 30, (accepted, accepted)).taken is True, "three identical reads settle a reply"
    refusal = thread(("user", SENT), ("assistant_open", DAILY_LIMIT))
    got = judge(refusal, 1, False, 30, (refusal, refusal)).verdict.outcome
    assert got is classify.SubmitOutcome.NO_POINTS, "three identical reads settle a refusal"
    grown = thread(("user", SENT), ("assistant_open", Q + " Really?"))
    assert judge(grown, 1, False, 30, (first, first)).verdict.outcome is None, "a changed reply counted as settled"
    assert judge(first, 1, False, 30, (grown, first)).verdict.outcome is None, "a changed reply counted as settled"


@mutation(SUB, FINAL_ON, FINAL_BLOCK, expect="a block flag settled a reply")
def test_a_text_block_that_says_finished_does_not_settle_a_reply_only_the_messages_flag_does():
    # the S1 capture (t2v.json read 3): the reply reads 'Video', its block says is_finish true, its ext says nothing
    cap = json.loads((FIXTURES / "s1-network" / "t2v.json").read_text())
    reads = [json.loads(r["body"]) for r in cap["http"] if "chain/single" in r.get("path", r.get("url", ""))]
    partial = next(b for b in reads if any(
        resolver.message_texts(m) == ["Video"] for m in resolver.chain_messages(b)))
    user = resolver.user_text(partial)
    sent = user.replace("Generated video: ", "", 1).strip()
    t = read_thread(partial, sent)
    reply = [m for m in t.msgs if not m.user][-1]
    assert reply.text == "Video" and reply.final is False, "a block flag settled a reply"
    assert judge(t, 1, False, 5).verdict.outcome is None
    done = read_thread(reads[-1], sent)
    assert [m for m in done.msgs if not m.user][-1].final is True


def test_a_reply_the_chain_flags_unfinished_is_not_judged_a_finished_one_is():
    open_ = judged([("user", SENT), ("assistant_open", BOILER_HALF)], nc=True)
    assert open_.taken is False and open_.verdict.outcome is None, "a half-written reply was judged"
    assert judged([("user", SENT), ("assistant", BOILER_HALF)], nc=True).taken is True


def test_the_balance_comes_from_the_accepting_reply_only():
    old = Q + " You have 4 video credits left today."
    out = judged([("user", SENT), ("assistant", old), ("user", SENT), ("assistant", NO_BALANCE)], sends=2, nc=True)
    assert out.taken is True and out.verdict.credits_left is None, "an old balance beat the new acceptance"
    out = judged([("user", SENT), ("assistant", ACCEPTED), ("assistant", ASIDE)], nc=True)
    assert out.verdict.credits_left == 2, "the balance was not the accepting reply's"


def test_a_stated_other_length_is_a_question_without_acceptance_wording_and_taken_with_it():
    bare = judged([("user", SENT), ("assistant", DUR)])
    assert (bare.taken, bare.verdict.outcome) == (False, classify.SubmitOutcome.NEEDS_CONFIRM)
    assert judged([("user", SENT), ("assistant", DUR + " " + ACCEPTED)]).taken is True


def test_a_zero_balance_alone_is_unclear_and_keeps_its_balance():
    out = judged([("user", SENT), ("assistant", ZERO)])
    assert out.taken is False and out.verdict.outcome is None and out.verdict.credits_left == 0


def test_the_messages_are_ordered_by_their_index_whatever_order_dola_lists_them_in():
    body = chain_body([("user", SENT), ("assistant", Q), ("user", SENT), ("assistant", UNCLEAR)], CONV)
    msgs = body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]
    for order in (msgs, list(reversed(msgs))):
        body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"] = order
        out = judge(read_thread(body, SENT), 2, False, 30)
        assert out.newest == UNCLEAR and out.verdict.outcome is None, "the messages were not ordered by their index"


def test_a_re_asked_question_is_a_new_message_and_a_re_rendered_old_one_is_not():
    # a re-rendered OLD question (markdown, curly quotes, a dash, a zero-width character) is the same message: nothing
    # after the latest send, so nothing to judge and nothing to send; a re-ASKED identical question is a new message.
    md = Q.replace("supports", "**supports**").replace("15 seconds?", "15‑seconds​?")
    same = [("user", SENT), ("assistant", md), ("user", SENT)]
    assert judged(same, sends=2).verdict.outcome is None
    asked_again = [("user", SENT), ("assistant", Q), ("user", SENT), ("assistant", Q)]
    assert judged(asked_again, sends=2).verdict.outcome is classify.SubmitOutcome.NEEDS_CONFIRM


# ---- round 9 ---------------------------------------------------------------------------------------------------------
@mutation(SUB, CARRIES_ON, CARRIES_PLAIN, expect="the gate counted sends more strictly than the receipt")
def test_the_sends_are_counted_with_the_matcher_of_the_receipt():
    for sent, stored in (("it\u2019s a lake [7100]", "it's a lake [7100]"), ("a lake [7100]", "a la\u200bke [7100]"),
                         ("lake 123 [7100]", "lake \uff11\uff12\uff13 [7100]")):
        t = read_thread(chain_body([("user", "Generated video: " + stored), ("assistant", Q)], CONV), sent)
        assert t.sends_seen == 1, "the gate counted sends more strictly than the receipt: %r" % stored


@mutation(SUB, FIRST_ON, FIRST_OFF, expect="an acceptance older than the job's first send was taken")
def test_an_acceptance_older_than_the_jobs_first_send_is_not_the_jobs():
    out = judged([("assistant", ACCEPTED), ("user", SENT), ("assistant", Q)], nc=True)
    assert out.taken is False, "an acceptance older than the job's first send was taken for the job's"
    assert out.verdict.outcome is classify.SubmitOutcome.NEEDS_CONFIRM


@mutation(SUB, DEDUPE_ON, DEDUPE_OFF, expect="a duplicated message id counted as a reply")
def test_a_message_id_that_reappears_after_the_second_send_is_not_a_reply_to_it():
    first = chain_body([("user", SENT), ("assistant", Q), ("user", SENT)], CONV)
    msgs = first["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]
    again = dict(next(m for m in msgs if m["user_type"] == 2))
    again["index_in_conv"] = "9"                                    # the same message (same id), listed again later
    msgs.append(again)
    out = judge(read_thread(first, SENT), 2, False, 30)
    assert out.verdict.outcome is None, "a duplicated message id counted as a reply to the second send"


@mutation(SUB, INDEX_ON, INDEX_OFF, expect="a read with a malformed index was ordered by guess")
def test_a_read_with_a_malformed_index_is_unusable_never_guessed():
    body = chain_body([("user", SENT), ("assistant", Q)], CONV)
    body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"][0]["index_in_conv"] = "x"
    assert read_thread(body, SENT) is None, "a read with a malformed index was ordered by guess"


@mutation(SUB, DOUBLE_ON, DOUBLE_OFF, expect="the customer's own words decided")
def test_dolas_echo_of_the_customers_quoted_words_is_not_dolas_acceptance():
    for nc in (OLD, NEW):
        out = judge(thread(("user", QUOTING_SENT), ("assistant", ECHO_Q), sent=QUOTING_SENT), 1, nc, 30)
        assert out.taken is False, "dola's echo of the customer's words was judged as an acceptance"
        want = classify.SubmitOutcome.NEEDS_CONFIRM if nc is OLD else None      # undecided: the words decide it
        assert out.verdict.outcome is want, "the customer's own words decided"


@mutation(CLASSIFY, VERSION_ON, "", expect="a captured question was not a question")
def test_the_which_version_capture_is_a_question_dola_asks():
    asked = " ".join(real_reply("partial_cell_failed-36389eee", 1))
    out = judged([("user", SENT), ("assistant", asked)])
    assert out.verdict.outcome is classify.SubmitOutcome.NEEDS_CONFIRM, "a captured question was not a question"
    assert classify.asks_question(asked)


@mutation(SUB, MARK_ON, "", expect="the first send was counted only after it returned")
@mutation(SUB, FENCE_ON, "", expect="a re-send was counted only after it returned")
@sync
async def test_the_send_is_counted_at_the_enter_not_after_the_send_returns():
    scene = Scene(replies={1: [Q]})
    run = _Run(scene.ctx, scene.job, scene.ctx.settings, scene.deps)
    await run._mark_sent()
    assert run.sends_made == 1, "the first send was counted only after it returned"
    await run._fence_for_send()
    assert run.sends_made == 2, "a re-send was counted only after it returned"


# ---- round 10 ------------------------------------------------------------------------------------------------
MISSING_ON = ("            if not mid:\n"
              "                return None                  # an id-less reply cannot be told from a repeat of "
              "an older one\n")
MISSING_OFF = "            if False:\n                return None\n"
CACHE_ON = "_norm = functools.lru_cache(maxsize=64)(norm_for_match)"
CACHE_OFF = "_norm = norm_for_match"
EXACT_ON = ("        return [i for i, m in enumerate(self.msgs) if typed and m.user "
            "and typed in _bubble_forms(m.text)]\n")
EXACT_OFF = ("        return [i for i, m in enumerate(self.msgs) if typed and m.user "
             "and any(typed in f for f in _bubble_forms(m.text))]\n")


@mutation(SUB, MISSING_ON, MISSING_OFF, expect="a message without an id bypassed the id-once rule")
def test_an_assistant_message_without_an_id_makes_the_read_unusable():
    body = chain_body([("user", SENT), ("assistant", Q)], CONV)
    for msg in body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]:
        if msg["user_type"] == 2:
            msg["message_id"] = ""
    assert read_thread(body, SENT) is None, "a message without an id bypassed the id-once rule"


@mutation(SUB, EXACT_ON, EXACT_OFF, expect="another job's longer prompt counted as this job's send")
def test_a_user_message_counts_only_when_it_equals_the_sent_text_bar_dolas_own_decoration():
    sent = "cat [7100]"
    other = thread(("user", "cat [7100] extra"), ("assistant", Q), sent=sent)
    assert other.sends_seen == 0, "another job's longer prompt counted as this job's send"
    for stored in ("Generated video: cat [7100], 16:9", "Generated video: cat [7100], 16:9, 30s", "cat [7100]",
                   "Generate image: cat [7100]", "Generated video: cat [7100], 21:9"):
        assert thread(("user", stored), ("assistant", Q), sent=sent).sends_seen == 1, stored
    assert thread(("user", "[2 images attached]"), ("assistant", Q), sent=sent).sends_seen == 0


@mutation(SUB, CACHE_ON, CACHE_OFF, expect="the prompt was normalised again and again")
def test_a_judgement_normalises_the_customers_prompt_once_not_again_and_again():
    import time

    from relay_dola.engine import submit
    prompt = ("a paper lantern over a lake, slow camera tilt, \u201csoft light\u201d " * 300) + "[7100]"     # ~20 000
    t = thread(("user", prompt), ("assistant", Q), ("user", prompt), ("assistant", Q), sent=prompt)
    assert hasattr(submit._norm, "cache_info"), "the prompt was normalised again and again"
    submit._norm.cache_clear()
    started = time.perf_counter()
    for _ in range(40):
        assert judge(t, 2, False, 30).usable
    took = time.perf_counter() - started
    info = submit._norm.cache_info()
    assert info.misses <= 4 and info.hits >= 40, "the prompt was normalised again and again: %s" % (info,)
    assert took < 1.0, "a judgement of a 20 000-character prompt took %.2f s for 40 calls" % took


@mutation(SUB, TYPED_ON, TYPED_STRIPPED, expect="a prompt that starts like dola's bubble was not counted")
def test_a_prompt_that_starts_with_dolas_own_bubble_words_is_still_a_send():
    for sent in ("Generate image: a lake [7100]", "Generate video: a lake [7100]", "Generated video: a lake [7100]"):
        for stored in (sent, "Generated video: " + sent + ", 16:9", "Generated video: " + sent + ", 16:9, 30s",
                       sent + ", 16:9", "Generate image: " + sent):
            t = thread(("user", stored), ("assistant", Q), sent=sent)
            assert t.sends_seen == 1, "a prompt that starts like dola's bubble was not counted: %r" % stored
    assert thread(("user", "Generate image: a lake [7100] extra"), sent="Generate image: a lake [7100]").sends_seen == 0
