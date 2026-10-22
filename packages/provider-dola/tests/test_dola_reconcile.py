"""Reconcile: did a send that lost contact reach dola? (I27; coverage
WORKER10/13/14/18/19/20/27/28, VERIFY02 for the probe the listing runs).

`NotSent` needs positive proof; the default is `StillUnknown`. The bug to avoid (a FULL listing holding
unknown conversations that do not carry the text was taken as proof, so a job whose conversation newer ones pushed out
of the 30 was sent twice) must never happen: `test_a_capped_listing_is_never_proof_of_not_sent` is that exact case.
"""

import asyncio
import datetime as dt
import json

import pytest
from kit_b import (
    FIXTURES,
    Ctx,
    FakeTransport,
    account,
    chain_with,
    http_for,
    job,
    reply,
    s1_capture,
    s1_reads,
    sent_of,
    user,
    user_block,
)

from relay_core.providers import Found, NotSent, StillUnknown
from relay_core.redact import redact_text
from relay_core.testing.mutation import mutation
from relay_dola.engine import reconcile as rc
from relay_dola.engine import resolver
from relay_dola.engine.reconcile import Listing, ListingError, reconcile
from relay_dola.engine.verify import CONV_LIST_LIMIT, LOGIN_PROBE_JS, LoginState, login_state, read_passport

RECONCILE = "packages/provider-dola/src/relay_dola/engine/reconcile.py"
RESOLVER = "packages/provider-dola/src/relay_dola/engine/resolver.py"

SENT = 1_790_700_000.0
SENT_AT = dt.datetime.fromtimestamp(SENT, dt.UTC)
PROMPT = "a cat riding a bicycle at dawn"
MARKER = "7788"
TEXT = sent_of(PROMPT, MARKER)          # the real renderer, the core frame: the words, then ` [7788]`
ACCEPT = ("The video will be generated using the **Dreamina Seedance 2.5 model**. It will use 2 credits and be ready "
          "in 35 minutes. I'll send it to you when it's done.")
UNCLEAR = "Hmm, let me think about that."
ASKED = "Would you like me to generate Part 1 only, or both parts? Please confirm."


def run(coro):
    return asyncio.run(coro)


def mine(text=TEXT, answer=ACCEPT, at=SENT + 30):
    """A conversation that carries `text` (dola prefixes its user messages with 'Generated video: ')."""
    msgs = [user("Generated video: " + text, at)]
    if answer:
        msgs.insert(0, reply(answer, at + 5))
    return chain_with(msgs)


def other(n=0, at=SENT + 1000):
    return chain_with([reply("Here are a few ideas.", at + 5), user("something else %d" % n, at)])


def shell():
    return chain_with([])


def lister_of(ids, *, cells=None, has_more=False):
    """A probe that saw `ids` (`cells` raw cells; default one per id) and dola's `has_more`."""
    calls = []

    async def lister(ctx, *a):
        calls.append(ctx)
        return Listing(list(ids), len(ids) if cells is None else cells, has_more)

    lister.calls = calls
    return lister


def go(conversations, ids=None, *, ref=None, prompt=PROMPT, marker=MARKER, frame=None, text=None, sent_at=SENT_AT,
       acc=None, lister=None, state=None, cells=None, has_more=False):
    """Reconcile a job whose own conversation is `ref` (or none) against a fake dola holding `conversations`. The job is
    in the production form: its `sent_text` is the real renderer's output for `prompt`, `marker` and `frame` (default:
    the core frame); `text` replaces it with another text (an empty one, for instance)."""
    transport = FakeTransport(dict(conversations))
    ctx = Ctx(acc or account(state=state))
    lister = lister or lister_of(list(conversations) if ids is None else ids, cells=cells, has_more=has_more)
    j = job(ref=ref, prompt=prompt, marker=marker, sent_text=text, sent_at=sent_at,
            **({} if frame is None else {"frame": frame}))
    result = run(reconcile(ctx, j, ctx.settings, http=http_for(transport, ctx.account), lister=lister))
    return result, transport, lister


# ---- the job's own conversation ---------------------------------------------------------------------------------
@mutation(RECONCILE, "    if job.external_ref:", "    if False:",
          expect="must not open a browser for a listing")
def test_a_job_with_a_ref_reads_its_own_conversation_and_opens_no_browser():
    result, transport, lister = go({"111": mine()}, ref="111")
    assert result == Found("111", {}, True), "the job's own conversation was not read directly: %r" % (result,)
    assert not lister.calls, "a job with a conversation of its own must not open a browser for a listing"
    assert transport.chain_reads() == ["111"]


def test_the_own_conversation_without_a_reply_or_without_the_text_falls_back_to_the_listing():
    # carries the text but dola has not replied: not "Found" by the own-ref rule; the listing finds it all the same
    result, _, lister = go({"111": mine(answer=None)}, ref="111")
    assert result == Found("111", {}, False) and lister.calls, "a conversation with no reply is found, not yet taken"
    # the own conversation is an empty shell: the order is somewhere else in the listing
    result, _, lister = go({"111": shell(), "222": mine()}, ref="111")
    assert result == Found("222", {}, True) and lister.calls


def test_found_says_taken_only_when_its_reply_proves_the_order():
    assert go({"1": mine(answer=ACCEPT)})[0] == Found("1", {}, True)
    assert go({"1": mine(answer=UNCLEAR)})[0] == Found("1", {}, False), "an unclear reply must not count as taken"
    # the old composer asks and accepts together: not taken, the order may be only a question
    assert go({"1": mine(answer=ACCEPT + " " + ASKED)})[0] == Found("1", {}, False)
    # the new composer (read from the account's state) takes that same acceptance
    assert go({"1": mine(answer=ACCEPT + " " + ASKED)}, state={"composer": "new"})[0] == Found("1", {}, True)


def test_a_video_or_an_image_in_the_conversation_proves_the_order_whatever_the_words():
    import base64
    cell = {"type": 2, "video": {"vid": "v1", "video_model": json.dumps({"video_list": {"720p": {
        "main_url": base64.b64encode(b"https://x/1.mp4").decode()}}})}}
    video = chain_with([{"index_in_conv": 3, "create_time": SENT + 60, "content": json.dumps([
        {"block_type": 2074, "content": {"creation_block": {"creations": [cell]}}}])},
        reply(UNCLEAR, SENT + 40), user("Generated video: " + TEXT, SENT + 30)])
    assert go({"1": video})[0] == Found("1", {}, True)


# ---- matching the full sent text (WORKER10, WORKER13, WORKER14) -------------------------------------------------
@mutation(RESOLVER, "    return bool(folded and norm_for_match(prompt)) and folded in norm_for_match(text)",
          "    return bool(folded and norm_for_match(prompt)) and folded[:12] in norm_for_match(text)",
          expect="took another job's conversation")
def test_two_jobs_with_the_same_prompt_each_find_their_own_conversation():
    words = "same prompt for both jobs"
    both = {"c1": mine(sent_of(words, "1111")), "c2": mine(sent_of(words, "2222"))}
    a = go(both, prompt=words, marker="1111")[0]
    b = go(both, prompt=words, marker="2222")[0]
    assert a == Found("c1", {}, True) and b == Found("c2", {}, True), "took another job's conversation: %r %r" % (a, b)


def test_a_marker_collision_is_no_worse_than_prompt_matching():
    """WORKER14: two jobs sharing marker AND prompt cannot be told apart, exactly as when matching on the prompt."""
    same = {"c1": mine(sent_of("same prompt", "3333"))}
    assert go(same, prompt="same prompt", marker="3333")[0] == Found("c1", {}, True)
    assert go(same, prompt="same prompt", marker="4444")[0] != Found("c1", {}, True), "a different marker matched"


def test_a_foreign_conversation_must_prove_it_belongs_to_the_job():
    """WORKER10: a conversation that is merely unknown (a hand-made one) is never adopted."""
    result, _, _ = go({"c1": other(1), "c2": other(2)})
    assert not isinstance(result, Found), "a foreign conversation was adopted: %r" % (result,)
    assert result == NotSent(True)


def test_whitespace_in_the_composer_is_folded_when_matching():
    flattened = "a cat riding a  bicycle\nat dawn [7788]"
    assert go({"1": mine(flattened)})[0] == Found("1", {}, True)


def test_a_found_conversation_wins_over_one_that_cannot_be_read():
    result, _, _ = go({"1": 503, "2": mine()}, ids=["1", "2"])
    assert result == Found("2", {}, True), "positive proof must beat an unreadable neighbour: %r" % (result,)


# ---- proof of NotSent (WORKER18, 19, 20, 27, 28) -------------------------------------------------------------------
def test_a_readable_list_below_the_cap_with_nothing_carrying_the_text_proves_not_sent():
    result, transport, _ = go({str(i): other(i) for i in range(5)})
    assert result == NotSent(covers_send_window=True)
    assert sorted(transport.chain_reads()) == sorted(str(i) for i in range(5)), "every candidate must be read"


@mutation("packages/relay-core/src/relay_core/providers.py", "    answered: bool = False\n",
          "    answered: bool = True\n", expect="a read that failed was taken for an answer")
@mutation(RECONCILE, "    return NotSent(covers_send_window=True) if covers else StillUnknown(answered=True)",
          "    return NotSent(covers_send_window=True) if covers else StillUnknown()",
          expect="a listing that was read in full was not taken for an answer")
def test_only_a_listing_that_was_read_in_full_is_an_answered_still_unknown():
    """After the render deadline the core waits for one reconcile that answered: a failed listing, an empty one or an
    unreadable conversation are not one; every conversation read with no proof either way is."""
    async def down(ctx, *a):
        raise ListingError("no browser")
    failed = {"a failed listing": go({"1": other()}, lister=down)[0], "an empty listing": go({}, ids=[])[0],
              "an unreadable conversation": go({"1": other()}, text="")[0]}
    for what, result in failed.items():
        assert result == StillUnknown() and result.answered is False, \
            "a read that failed was taken for an answer: " + what
    capped = go({str(i): other(i) for i in range(5)}, has_more=True)[0]
    assert capped.answered is True, "a listing that was read in full was not taken for an answer"


def test_an_empty_list_is_unreadable_not_proof():
    assert go({}, ids=[])[0] == StillUnknown(), "an empty list was taken as 'nothing was sent'"


def test_an_unreachable_listing_leaves_the_job_alone():
    async def down(ctx, *a):
        raise ListingError("no browser")
    assert go({"1": other()}, lister=down)[0] == StillUnknown()


def test_a_missing_account_is_not_guessed_about():
    ctx = Ctx(account())
    ctx.account = None
    mine_job = job(prompt=PROMPT, marker=MARKER, sent_at=SENT_AT)
    result = run(reconcile(ctx, mine_job, ctx.settings, http=http_for(FakeTransport()),
                           lister=lister_of(["1"])))
    assert result == StillUnknown()


def test_a_job_with_no_sent_text_proves_nothing():
    assert go({"1": other()}, text="")[0] == StillUnknown()


@mutation(RECONCILE, "    if unreadable:", "    if False:", expect="an unreadable conversation was ignored")
def test_any_unreadable_candidate_blocks_the_proof():
    for bad in (503, {"status_code": 712017001}, {"status_code": 712012001}, chain_with([{"khong_co_content": 1}])):
        result, _, _ = go({"1": other(1), "2": bad, "3": other(3)})
        assert result == StillUnknown(), "an unreadable conversation was ignored: %r with %r" % (result, bad)


@mutation(RECONCILE, "    if summary.messages == 0:\n        return Reading(False)",
          "    if summary.messages == 0:\n        return Reading(True)", expect="an empty shell was taken")
def test_an_empty_shell_is_not_ours_and_not_unreadable():
    """dola gives a conversation id the moment /chat opens; a send killed before typing leaves a shell (0 messages)."""
    result, _, _ = go({"shell": shell(), "1": other()})
    assert result == NotSent(True), "an empty shell was taken or blocked the proof: %r" % (result,)
    assert not isinstance(go({"shell": shell()})[0], Found), "an empty shell was taken as the job's conversation"


# ---- the listing-coverage proof and the capped-listing bug ------------------------------------------------------
def capped_fixture():
    data = json.loads((FIXTURES / "listing-capped-30.json").read_text(encoding="utf-8"))
    sent_at = dt.datetime.fromtimestamp(data["sent_at"], dt.UTC)
    return data, sent_at


@mutation(RECONCILE, "    covers = listing.complete or predates", "    covers = True",
          expect="a capped listing was taken as proof of NotSent")
def test_a_capped_listing_is_never_proof_of_not_sent():
    """A full listing (30) holding one conversation the system does not know, none carrying the job's text: an
    older version requeued the job. Newer conversations may have pushed the job's own out of the 30, so the order
    could be sent twice. Here: StillUnknown. (The crash window: capped listing + one unknown non-matching.)"""
    data, sent_at = capped_fixture()
    assert len(data["listing"]["ids"]) == CONV_LIST_LIMIT == 30
    assert data["unknown_non_matching"] in data["conversations"]
    result, transport, _ = go(data["conversations"], ids=data["listing"]["ids"],
                              sent_at=sent_at)
    assert result == StillUnknown(answered=True), "a capped listing was taken as proof of NotSent: %r" % (result,)
    assert len(transport.chain_reads()) == 30, "every candidate must still be read before giving up"


@mutation(RECONCILE, " or len(ids) > CONV_LIST_LIMIT", "", expect="a listing longer than the cap proved NotSent")
def test_a_listing_longer_than_the_cap_never_proves_not_sent():
    """Only the first CONV_LIST_LIMIT ids are read; the job's own conversation can be among the others (probe: 35
    ids, the job's is the one sorted last, one other conversation older than the send)."""
    convs = {str(1000 + i): other(i) for i in range(34)}
    convs["5"] = mine(at=SENT + 30)
    convs["1033"] = other(33, at=SENT - 86_400)
    assert len(convs) > CONV_LIST_LIMIT
    result, transport, _ = go(convs, cells=len(convs), has_more=True)
    assert result == StillUnknown(), "a listing longer than the cap proved NotSent: %r" % (result,)
    assert "5" not in transport.chain_reads(), "premise: the job's conversation is never read"


def test_a_capped_listing_that_reaches_back_past_the_send_proves_not_sent():
    """The job's conversation is at least as recent as anything older than the send, so a top-30 that still holds such
    a conversation cannot have pushed the job's out."""
    data, sent_at = capped_fixture()
    convs = json.loads(json.dumps(data["conversations"]))
    oldest = data["listing"]["ids"][0]
    convs[oldest] = other(0, at=SENT - 86_400)             # last activity a day before the send
    result, _, _ = go(convs, ids=data["listing"]["ids"], sent_at=sent_at)
    assert result == NotSent(covers_send_window=True), "a listing that reaches back past the send was refused: %r" % (
        result,)


@mutation(RECONCILE, "summary.newest < sent_at - PREDATE_MARGIN_S", "summary.newest > sent_at - PREDATE_MARGIN_S",
          expect="a conversation barely older than the send was trusted")
def test_only_a_conversation_clearly_older_than_the_send_counts_as_reaching_back():
    data, sent_at = capped_fixture()
    convs = json.loads(json.dumps(data["conversations"]))
    first = data["listing"]["ids"][0]
    # older than the send, but only by half the clock margin: dola's clock could be that far off, so it proves nothing
    convs[first] = other(0, at=SENT - rc.PREDATE_MARGIN_S / 2)
    close = go(convs, ids=data["listing"]["ids"], sent_at=sent_at)[0]
    assert close == StillUnknown(answered=True), "a conversation barely older than the send was trusted: %r" % (close,)
    convs[first] = other(0, at=SENT - rc.PREDATE_MARGIN_S * 2)
    far = go(convs, ids=data["listing"]["ids"], sent_at=sent_at)[0]
    assert far == NotSent(True), "a conversation older than the send did not count: %r" % (far,)
    # an undated conversation and a job with no sent_at prove nothing about the window
    convs[first] = chain_with([reply("hi", 0), user("old", 0)])
    assert go(convs, ids=data["listing"]["ids"], sent_at=sent_at)[0] == StillUnknown(answered=True)
    convs[first] = other(0, at=SENT - 86_400)
    assert go(convs, ids=data["listing"]["ids"], sent_at=None)[0] == StillUnknown(answered=True)


def test_a_capped_listing_that_holds_the_text_is_found_whatever_its_size():
    data, sent_at = capped_fixture()
    convs = dict(data["conversations"])
    convs[data["listing"]["ids"][3]] = mine(data["job"]["sent_text"])
    result, _, _ = go(convs, ids=data["listing"]["ids"], sent_at=sent_at)
    assert result == Found(data["listing"]["ids"][3], {}, True)


def test_one_dead_session_while_reading_ends_the_round_unknown():
    result, _, _ = go({"1": other(1), "2": {"status_code": 712012001}}, ids=["1", "2"])
    assert result == StillUnknown()


# ---- matching the way dola stores the prompt (block-list user messages, invisible characters, quotes) --------------
def test_the_i2v_conversation_in_its_real_block_list_shape_is_found():
    """S1 capture: an i2v order is stored with the prompt as a block-list user message; reading only the dict shape
    called the job's own conversation 'not ours' and a job that was taken got sent again."""
    marker = s1_capture("i2v")["marker"]                        # `[7103]`: the capture was made with the bracketed form
    first = next(b for _, anchor, b in s1_reads("i2v") if anchor == 6)
    stored = resolver_user_text(first)
    sent = stored.split("Generated video: ", 1)[1]
    prompt = sent[:sent.index(marker)].strip()            # the composer adds ", 16:9, 30s" after the marker
    newest = rc.resolver.last_message_time(first)
    result, _, _ = go({"38417993818525713": first}, prompt=prompt, marker=marker.strip("[]"),
                      sent_at=dt.datetime.fromtimestamp(newest - 20, dt.UTC))
    assert result == Found("38417993818525713", {}, False), "the job's own i2v conversation was denied: %r" % (result,)


def resolver_user_text(body):
    return rc.resolver.user_text(body)


def test_a_block_list_user_message_in_a_made_up_chain_is_found_and_matched_like_submit_does():
    conv = chain_with([reply(ACCEPT, SENT + 35), user_block("Generated video: " + TEXT, SENT + 30)])
    assert go({"1": conv})[0] == Found("1", {}, True)


@mutation(RESOLVER, "    return bool(folded and norm_for_match(prompt)) and folded in norm_for_match(text)",
          "    return bool(folded and norm_for_match(prompt)) and _flat(sent_text) in _flat(text)",
          expect="dola altered the text and the job was declared not sent")
def test_a_dropped_invisible_character_or_a_straightened_quote_still_finds_the_conversation():
    zwsp = "a cat\u200b riding a bicycle at dawn"
    plain = "a cat riding a bicycle at dawn"
    straight, curly = "a cat's bicycle at dawn", "a cat\u2019s bicycle at dawn"
    for sent, stored in ((zwsp, plain), (plain, zwsp), (curly, straight), (straight, curly)):
        result, _, _ = go({"9": mine(sent_of(stored, MARKER))}, prompt=sent)
        assert result == Found("9", {}, True), "dola altered the text and the job was declared not sent: %r" % (
            result,)


@mutation(RESOLVER, "    return bool(folded and norm_for_match(prompt)) and folded in norm_for_match(text)",
          "    return bool(folded) and folded in norm_for_match(text)",
          expect="the frame's words and a shared code matched another order")
def test_a_job_whose_prompt_shows_nothing_matches_nothing_whatever_its_frame_says():
    """The prompt folded to nothing, so the sent text is only the frame's words and the tracking code: another job's
    conversation made with the same frame and the same four digits carries all of it (about 1 pair in 7,000)."""
    frame = "Fixed preamble of this model. {{client_prompt}} {{tracking_code}}"
    blank = "\u200b\u200b"
    sent = sent_of(blank, MARKER, frame)
    theirs = "Generated video: " + sent_of("someone else's order", MARKER, frame)
    assert not resolver.carries_text("Generated video: " + sent, sent, blank), \
        "the frame's words and a shared code matched another order"
    assert not resolver.carries_text(theirs, sent, blank), "the frame's words and a shared code matched another order"
    real = sent_of("someone else's order", MARKER, frame)
    assert resolver.carries_text(theirs, real, "someone else's order"), "premise: a real prompt still matches"


@mutation(RECONCILE, "    if not mine and resolver.may_be_job(body, job.marker, prompt):",
          "    if False:", expect="carries the job's marker was called not ours")
def test_a_conversation_with_the_jobs_marker_but_other_words_is_cannot_tell_never_not_sent():
    altered = "a cat riding a bicycle at the break of day [7788]"        # dola rewrote it; the marker survived
    result, _, _ = go({"9": mine(altered)})
    assert result == StillUnknown(), "a conversation that carries the job's marker was called not ours: %r" % (result,)
    # only the first 80 characters survive (the tail was cut): the prefix alone is enough to doubt
    result, _, _ = go({"9": mine("x" * 90 + " the")}, prompt="x" * 90 + " the end")
    assert result == StillUnknown()
    # a conversation that shares neither marker nor prefix stays "not ours"
    assert go({"9": mine("something else entirely [1111]")})[0] == NotSent(True)


def test_the_unrelated_conversation_next_to_a_match_does_not_hide_it():
    result, _, _ = go({"1": other(1), "2": mine(), "3": other(3)})
    assert result == Found("2", {}, True)


# ---- an earlier attempt of the same job left a conversation with the same text -------------------------------------
@mutation(RECONCILE, "got.newest > best[0]", "got.newest < best[0]",
          expect="adopted an earlier attempt's conversation")
def test_the_newest_matching_conversation_wins_over_an_earlier_attempts():
    refused = chain_with([reply("Something went wrong. Please try again.", SENT - 100), user("Generated video: " + TEXT,
                                                                                         SENT - 120)])
    taken = mine(at=SENT + 30)
    for convs in ({"900": refused, "100": taken}, {"100": refused, "900": taken}):
        result, _, _ = go(convs)
        assert result == Found(next(k for k, v in convs.items() if v is taken), {}, True), \
            "adopted an earlier attempt's conversation: %r" % (result,)


@mutation(RECONCILE, "    if mine and not older:", "    if mine:", expect="an old attempt's conversation was adopted")
def test_a_match_older_than_the_send_is_not_this_attempts_and_proves_nothing_alone():
    old = mine(at=SENT - 86_400)
    result, _, _ = go({"900": old})
    assert not isinstance(result, Found), "an old attempt's conversation was adopted: %r" % (result,)
    assert result == NotSent(True), "an older conversation is readable and not ours: it is the usual 'not sent' proof"
    assert go({"900": old, "100": mine(at=SENT + 30)})[0] == Found("100", {}, True)


# ---- the listing proof counts what dola returned (B2/N4) -------------------------------------------------------
@mutation(RECONCILE, "self.has_more is False", "True",
          expect="dola says there is more")
def test_a_short_listing_proves_nothing_when_dola_says_there_is_more_or_says_nothing():
    convs = {str(i): other(i) for i in range(5)}
    assert go(convs, has_more=False)[0] == NotSent(True)
    for more in (True, None):
        assert go(convs, has_more=more)[0] == StillUnknown(answered=True), \
            "dola says there is more (%r): no proof" % (more,)


@mutation(RECONCILE, "listing.cells != len(ids)", "False",
          expect="a cell with no id was ignored")
def test_cells_without_an_id_are_counted_and_block_the_proof():
    convs = {str(i): other(i) for i in range(5)}
    assert go(convs, cells=6)[0] == StillUnknown(), "a cell with no id was ignored"
    # 29 ids but dola returned 30 cells: the list is at the cap, and one cell is unreadable
    many = {str(i): other(i) for i in range(29)}
    assert go(many, cells=30)[0] == StillUnknown()


def test_the_probe_returns_dolas_has_more_and_the_raw_cell_count(monkeypatch):
    seen = {}
    page = FakePage({"cells": 3, "ids": ["1", "2"], "hasMore": True, "name": "", "modal": False, "passport": None})
    install_browser(monkeypatch, page, seen)
    assert run(rc.list_conversation_ids(Ctx(token=object()))) == Listing(["1", "2"], 3, True)
    page = FakePage({"cells": 1, "ids": ["1"], "hasMore": None, "name": "", "modal": False, "passport": None})
    install_browser(monkeypatch, page, seen)
    assert run(rc.list_conversation_ids(Ctx(token=object()))).has_more is None
    assert "has_more" in LOGIN_PROBE_JS and "hasMore" in LOGIN_PROBE_JS


# ---- the browser listing (the probe runs inside a tab) ----------------------------------------------------------
class FakePage:
    def __init__(self, probe, url="https://www.dola.com/chat"):
        self.probe, self.url, self.goto_url, self.evaluated = probe, url, None, None

    async def goto(self, url, **kw):
        self.goto_url = url

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, js):
        self.evaluated = js
        if isinstance(self.probe, Exception):
            raise self.probe
        return self.probe


def install_browser(monkeypatch, page, seen):
    import contextlib

    from relay_browser import session as kit_session
    from relay_browser import ui

    @contextlib.asynccontextmanager
    async def open_session(site, acct, *, purpose, token, images=False, response_patches=(), redact):
        seen.update(site=site, purpose=purpose, token=token, redact=redact, account=acct)

        class Session:
            ctx = "browser-context"

        Session.page = page
        yield Session

    async def inject(ctx, jar, names, read_url):
        seen.update(injected=(ctx, [c["name"] for c in jar], tuple(names), read_url))
        return list(names)

    monkeypatch.setattr(kit_session, "open_session", open_session)
    monkeypatch.setattr(ui, "inject_cookies", inject)


def test_the_listing_probe_runs_in_a_reconcile_session_with_the_cores_token_and_redactor(monkeypatch):
    seen = {}
    page = FakePage({"cells": 2, "ids": ["38417999999999991", "38417999999999992"], "hasMore": False, "name": "",
                     "modal": False, "passport": None})
    install_browser(monkeypatch, page, seen)
    token = object()
    ctx = Ctx(token=token)
    listing = run(rc.list_conversation_ids(ctx))
    assert listing == Listing(["38417999999999991", "38417999999999992"], 2, False)
    assert seen["purpose"] == "reconcile" and seen["token"] is token, "the browser opens under the core's token"
    assert seen["redact"] is redact_text, "evidence must go through the core's redactor"
    assert seen["site"].name == "dola" and seen["account"] is ctx.account
    assert seen["injected"][0] == "browser-context" and seen["injected"][3] == "https://www.dola.com"
    assert set(seen["injected"][1]) == {"sessionid", "uid_tt"}
    assert page.goto_url == "https://www.dola.com/chat" and page.evaluated == LOGIN_PROBE_JS


def test_a_region_page_or_a_browser_failure_is_a_listing_error_never_an_empty_list(monkeypatch):
    seen = {}
    install_browser(monkeypatch, FakePage({"ids": []}, url="https://www.dola.com/region-restricted"), seen)
    with pytest.raises(ListingError):
        run(rc.list_conversation_ids(Ctx(token=object())))
    install_browser(monkeypatch, FakePage(RuntimeError("page crashed")), seen)
    with pytest.raises(ListingError):
        run(rc.list_conversation_ids(Ctx(token=object())))


def test_a_listing_that_cannot_be_read_leaves_the_job_unknown_through_the_real_lister(monkeypatch):
    seen = {}
    install_browser(monkeypatch, FakePage(RuntimeError("boom")), seen)
    ctx = Ctx(token=object())
    transport = FakeTransport()
    result = run(reconcile(ctx, job(ref=None, prompt=PROMPT, marker=MARKER, sent_at=SENT_AT), ctx.settings,
                           http=http_for(transport, ctx.account)))
    assert result == StillUnknown() and not transport.posts


# ---- verify.py: the login probe's reading (VERIFY02) ------------------------------------------------------------
def test_the_probe_lists_up_to_the_cap_and_reads_the_three_signals():
    assert CONV_LIST_LIMIT == 30 and "limit:30" in LOGIN_PROBE_JS
    for token in ("recent_conv", "phone_login_entry_title_cn", "credentials:'include'", "ids", "conversation_id",
                  "/passport/account/info/v2/", "passport}"):
        assert token in LOGIN_PROBE_JS, token


def test_login_state_signed_in_signed_out_or_unknown():
    assert LoginState(5, "Thu Ung718", False).logged_in
    assert not LoginState(5, "Thu Ung718", True).logged_in, "a login dialog means not in, whatever recent_conv says"
    assert not LoginState(0, "", False).logged_in
    assert LoginState(0, "Nam Phuong339", False).logged_in, "a new account with a name is in"
    assert not LoginState(-1, "", False).logged_in


def test_passport_reads_three_measured_shapes_and_a_guest_is_not_an_account():
    guest = {"message": "error", "uid": False, "name": "account_info_error"}
    joy = {"message": "success", "uid": True, "name": "Joy IELTS"}
    assert read_passport(guest) == ("guest", ""), "a guest tab was read as an account"
    assert read_passport(joy) == ("user", "Joy IELTS")
    assert read_passport(None) == ("unknown", "")
    assert read_passport({"err": "TypeError: Failed to fetch"}) == ("unknown", "")


def test_signed_out_is_only_the_measured_shape_and_the_reason_says_it():
    dead = LoginState(0, "", False, "guest")
    assert not dead.logged_in and dead.signed_out and "signed out" in dead.reason
    assert not LoginState(0, "", False, "unknown").signed_out, "a failed passport call is a doubt, not a verdict"
    assert not LoginState(-1, "", False, "guest").signed_out
    assert not LoginState(5, "", False, "guest").signed_out, "a conversation list means the cookie works"
    state = login_state({"cells": 0, "ids": [], "name": "", "modal": False,
                         "passport": {"message": "error", "uid": False, "name": "account_info_error"}})
    assert state.signed_out and state.session == "guest"
    named = login_state({"cells": 0, "ids": [], "name": "", "modal": False,
                         "passport": {"message": "success", "uid": True, "name": "Joy IELTS"}})
    assert named.logged_in and named.account_name == "Joy IELTS" and not named.signed_out
