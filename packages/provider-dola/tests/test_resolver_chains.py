"""The poll resolver on real and synthetic dola conversations (coverage RES*, RUN02/03/05/06).

The 16 recorded chains (relay/fixtures/dola/chains) plus the derived watermark-only chain are read through `poll()`
with an in-process transport; the outcome and the code per case equal what the earlier resolver gave on the same
data. The table `ANSWERS` was produced by running that resolver on each fixture with its clock at the fixture's
`fetched_at` and the job's `created_at` as the Re-check mark. The only allowed difference is the listing bug in
reconcile (test_dola_reconcile.py).
"""

import asyncio
import datetime as dt
import json

import pytest
from kit_b import (
    CONV,
    Ctx,
    FakeTransport,
    Resp,
    account,
    chain_files,
    chain_with,
    created_at,
    fetched_at,
    first_user_text,
    http_for,
    job,
    load_case,
    reply,
    strip_main_url,
    user,
)

from relay_core.providers import Done, Failed, Pending, Success
from relay_core.testing.mutation import mutation
from relay_dola import constants, errors
from relay_dola.engine import resolver
from relay_dola.engine.poll import SIGNED_OUT_NOTE, poll

POLL = "packages/provider-dola/src/relay_dola/engine/poll.py"
RESOLVER = "packages/provider-dola/src/relay_dola/engine/resolver.py"

NOW = 1_790_000_000.0
ACCEPT = ("The video will be generated using the **Dreamina Seedance 2.5 model**. It will use 2 credits and be ready "
          "in 35 minutes. I'll send it to you when it's done.")
NO_CHARGE = "I can’t generate the video and didn’t use any credit for this generation."
COPYRIGHT = ("For copyright protection, I can't show you the generated video. Use other references or edit the "
             "prompt and try again.")
LIKENESS = ("For likeness protection, Dreamina Seedance 2.5 only supports generating videos featuring yourself. "
            "Use another image, or create a video from text.")
DAILY = "You've reached the daily limit for video generation. Please try again tomorrow."
STORYBOARD = "I've generated the 30-panel storyboard sheet based on your reference image and script."
ASK = "Would you like me to generate Part 1 only, Part 2 only, or both parts now?"

# the recorded answer per chain: ("done", n outputs) | ("failed", code, state_patch without the healing keys)
ANSWERS = {
    "asked_question-e6d0abb9": ("failed", errors.NEEDS_CONFIRM, {}),
    "copyright-20900298": ("failed", errors.CONTENT_REFUSED, {"no_recheck": True}),
    "copyright-840aa471": ("failed", errors.CONTENT_REFUSED, {"no_recheck": True}),
    "image_done-cebde2e8": ("done", 1),
    "multi_video-32c6fe27": ("done", 2),
    "multi_video-76d1558b": ("done", 2),
    "no_charge-40b47fe1": ("failed", errors.CONTENT_REFUSED, {}),
    "no_charge-dc7e8c18": ("failed", errors.CONTENT_REFUSED, {}),
    "partial_cell_failed-36389eee": ("done", 1),
    "refuse_then_deliver-conv719633": ("done", 1),
    "split_30s_into_15s-1e73d195": ("done", 2),
    "split_30s_into_15s-35d578d7": ("failed", errors.NEEDS_CONFIRM, {}),
    "video_i2v-137e3085": ("done", 1),
    "video_i2v-9f1435d9": ("done", 1),
    "video_t2v-0369d0bd": ("done", 1),
    "video_t2v-32ffb8ef": ("done", 1),
}


def run(coro):
    return asyncio.run(coro)


def poll_chain(item, *, kind="video", state=None, now=NOW, since=None, prompt="a plain grey wall, static shot",
               marker="4100", duration=30, acc=None, transport=None):
    """One poll of CONV served by `item` (a body, a status int, a Resp). Returns (result, ctx, transport)."""
    transport = transport or FakeTransport({CONV: item})
    ctx = Ctx(acc or account())
    if isinstance(since, float):
        since = dt.datetime.fromtimestamp(since, dt.UTC)
    j = job(kind=kind, prompt=prompt, marker=marker, since=since, state=state, duration=duration)
    result = run(poll(ctx, j, ctx.settings, http=http_for(transport, ctx.account), clock=lambda: now))
    return result, ctx, transport


def poll_case(case: dict, body: dict | None = None):
    kind = (case.get("job") or {}).get("kind") or "video"
    duration = (case.get("job") or {}).get("duration")
    ctx = Ctx()
    j = job(kind=kind, prompt=first_user_text(case["chain"]), marker="", since=created_at(case),
            duration=int(duration) if duration else None)
    transport = FakeTransport({CONV: body or case["chain"]})
    return run(poll(ctx, j, ctx.settings, http=http_for(transport, ctx.account), clock=lambda: fetched_at(case)))


def cell(url=None, status=None, vid=None, duration=None, watermark_only=False):
    import base64
    video = {}
    if vid:
        video["vid"] = vid
    if duration is not None:
        video["duration"] = str(duration)
    if status is not None and url is None:
        video["status"] = status
    if url:
        video["download_url"] = "https://wm.example/%s.mp4" % (vid or "x")
        if not watermark_only:
            video["video_model"] = json.dumps({"video_list": {"720p": {
                "main_url": base64.b64encode(url.encode()).decode()}}})
    return {"type": 2, "video": video}


def video_msg(index, at, *cells):
    return {"index_in_conv": index, "content_type": 9999, "create_time": at, "content": json.dumps([
        {"block_type": 2074, "content": {"creation_block": {"creations": list(cells)}}}])}


def mine(text="a plain grey wall, static shot [4100]", at=NOW - 100):
    return user("Generated video: " + text, at)


# ---- the recorded chains ----------------------------------------------------------------------------------------
def test_every_recorded_chain_has_an_expectation():
    assert {p.stem for p in chain_files()} == set(ANSWERS), "a recorded chain without an answer (or the reverse)"


@pytest.mark.parametrize("name", sorted(ANSWERS))
def test_recorded_chain_gives_the_recorded_answer(name):
    case = load_case(name)
    result = poll_case(case)
    want = ANSWERS[name]
    out = result.outcome
    if want[0] == "done":
        assert isinstance(out, Done), "%s: the recorded answer delivered, got %r" % (name, out)
        assert len(out.outputs) == want[1], "%s: the recorded answer has %d outputs, got %d" % (
            name, want[1], len(out.outputs))
        assert [o.idx for o in out.outputs] == list(range(want[1])), "outputs are not numbered from 0 in order"
        assert len({o.upstream_id for o in out.outputs}) == want[1], "two outputs share an upstream id"
        assert any(isinstance(e, Success) for e in result.effects), "a good read must clear the strike counter"
    else:
        assert isinstance(out, Failed), "%s: the recorded answer ended the job (%s), got %r" % (name, want[1], out)
        assert out.code == want[1], "%s: the recorded code is %s, got %s" % (name, want[1], out.code)
        assert dict(out.state_patch) == want[2], "%s: state patch %r" % (name, dict(out.state_patch))
        assert out.taken, "a refusal after an accepted order is not 'not ours'"


def test_recorded_video_outputs_are_clean_mp4_with_a_stable_id():
    case = load_case("multi_video-32c6fe27")
    first = poll_case(case).outcome.outputs
    again = poll_case(case).outcome.outputs
    assert [o.upstream_id for o in first] == [o.upstream_id for o in again], "the cell id changes between reads"
    for o in first:
        assert o.mime == "video/mp4" and o.ref.startswith("http") and "wm" not in o.ref.split("/")[2]
        assert o.meta["conversation_id"] == CONV and o.meta["source"] == "main_url"
        assert o.meta["duration"] in (30.042, 30.08)
        assert "fallback" not in json.dumps(dict(o.meta)), "the decode seed's route must not ride in meta"


def test_recorded_image_chain_gives_the_clean_png():
    out = poll_case(load_case("image_done-cebde2e8")).outcome
    assert isinstance(out, Done) and out.outputs[0].mime == "image/png"
    assert "_wm_" not in out.outputs[0].ref and "image_raw" in out.outputs[0].ref


@mutation(RESOLVER, "    return node[\"url\"] if isinstance(node, dict) and node.get(\"url\") else None",
          "    return None", expect="a clean image was not taken")
def test_image_without_the_clean_rendition_is_not_done_yet():
    """RES21: only `image_ori_raw` counts; a grid with only watermarked renditions keeps polling."""
    body = chain_with([{"content_type": 9999, "index_in_conv": 2, "create_time": NOW, "content": json.dumps([
        {"content": {"creation_block": {"creations": [{"image": {"image_ori": {"url": "https://x/_wm_.png"}}}]}}}])},
        user("Generate image: a pebble [4100]", NOW - 10, 1)])
    result, _, _ = poll_chain(body, kind="image", prompt="a pebble")
    assert isinstance(result.outcome, Pending), "an image with only a watermarked rendition was delivered"
    clean = chain_with([{"content_type": 9999, "index_in_conv": 2, "create_time": NOW, "content": json.dumps([
        {"content": {"creation_block": {"creations": [{"image": {"image_ori_raw": {"url": "https://x/raw.jpeg"}}}]}}}])},
        user("Generate image: a pebble [4100]", NOW - 10, 1)])
    out = poll_chain(clean, kind="image", prompt="a pebble")[0].outcome
    got = (out.outputs[0].ref, out.outputs[0].mime) if isinstance(out, Done) else None
    assert got == ("https://x/raw.jpeg", "image/jpeg"), "a clean image was not taken: %r" % (out,)


# ---- watermarks -------------------------------------------------------------------------------------------------
@mutation(POLL, "    clean = [c for c in read.ready if c.clean]", "    clean = list(read.ready)",
          expect="a watermark-only video was delivered")
@pytest.mark.parametrize("name", ["video_t2v-0369d0bd", "multi_video-32c6fe27"])
def test_watermark_only_chain_keeps_polling_never_delivers(name):
    case = load_case(name)
    derived = strip_main_url(case["chain"])
    result = poll_case(case, derived)
    out = result.outcome
    assert isinstance(out, Pending), "a watermark-only video was delivered: %r" % (out,)
    assert out.state_patch["watermarked_only"] is True, "the deadline cannot name render.watermarked_only"
    assert out.next_in_s == constants.POLL_INTERVAL_S == 30


def test_partly_watermarked_delivers_the_clean_ones_with_a_warning():
    body = chain_with([video_msg(1, NOW - 90, cell("https://x/1.mp4", vid="a"),
                                 cell("https://x/2.mp4", vid="b", watermark_only=True)), mine()])
    result = poll_chain(body)[0]
    assert isinstance(result.outcome, Done) and [o.ref for o in result.outcome.outputs] == ["https://x/1.mp4"]
    marks = [w for w in result.warnings if w.code == errors.WARN_PARTLY_WATERMARKED]
    assert marks and "1 more video" in marks[0].text, \
        "a partly watermarked order delivered without saying so: %r" % (result.warnings,)


def test_a_cell_of_the_wrong_kind_is_not_a_video():
    body = chain_with([video_msg(2, NOW - 5, {"type": 1, "video": {"download_url": "http://x/y.mp4"}}), mine()])
    assert resolver.extract_videos(body) == ([], 0)


# ---- the resolver by segment: extraction (RES00), refusals (RES01), guard (RES02, RES10, RES11, RES17) ----------
def test_extraction_prefers_the_decoded_master_and_scans_every_slot():
    master = "https://cdn.example.com/master.mp4"
    body = chain_with([video_msg(1, NOW, cell(master, vid="v1", duration=30.04))])
    (c,), pending = resolver.extract_videos(body)
    assert (c.url, c.clean) == (master, True) and pending == 0
    assert resolver.extract_videos(chain_with([])) == ([], 0)
    many = chain_with([video_msg(1, NOW, cell("https://x/1.mp4")), video_msg(2, NOW + 1, cell(status=4)),
                       video_msg(3, NOW + 2, cell("https://x/2.mp4"))])
    ready, pending = resolver.extract_videos(many)
    assert [c.url for c in ready] == ["https://x/1.mp4", "https://x/2.mp4"] and pending == 0, \
        "a conversation with n videos does not yield all n: %r" % [c.url for c in ready]
    ready, pending = resolver.extract_videos(chain_with([video_msg(1, NOW, cell("https://x/1.mp4")),
                                                         video_msg(2, NOW + 1, cell(status=1))]))
    assert pending == 1, "a video still rendering is not counted"
    only_wm = chain_with([video_msg(1, NOW, cell("https://x/w.mp4", watermark_only=True))])
    (w,), _ = resolver.extract_videos(only_wm)
    assert w.url.startswith("https://wm.example/") and w.clean is False


def test_refusals_that_may_still_deliver_are_not_final():
    assert resolver.refusal_may_still_deliver("I can't show you the generated video because it may contain "
                                              "copyrighted or policy-violating content. No credits were used.")
    assert not resolver.refusal_may_still_deliver(DAILY)
    assert not resolver.refusal_may_still_deliver("You only have 1 left today. Change the parameters and try again.")
    assert resolver.refusal_may_still_deliver("I can't generate the video and didn't use any credit.")


def test_refuse_then_deliver_keeps_waiting_until_the_video_comes():
    """A refusal with 'no credits were used' before any video, judged with no send mark (a manual read): polling goes
    on (conv 38417876432719633). With a mark, a copyright block written after it ends the job at once (below)."""
    body = chain_with([reply("I can't show you the generated video because it may contain copyrighted content. "
                             "No credits were used for this video.", NOW - 5), mine()])
    result, _, _ = poll_chain(body, since=None)
    assert isinstance(result.outcome, Pending), "refuse-then-deliver ended early: %r" % (result.outcome,)


def test_prompt_matching_uses_an_80_character_prefix_and_folds_blanks():
    assert resolver.prompt_matches("The two characters in a high-speed aerial collision",
                                  "user: The two  characters in a high-speed aerial collision. Your video is ready")
    assert not resolver.prompt_matches("aerial combat storyboard scene one two three four five six seven eight",
                                       "a red kite flying over green rice fields at sunset, cinematic")
    assert resolver.prompt_matches("", "anything")
    long = "x" * 80 + "TAIL"
    assert resolver.prompt_matches(long, "x" * 80), "the prefix is 80 characters, the tail is not required"


def test_the_guard_reads_the_user_text_not_the_assistant_text():
    """RES10: the prompt is in the user message (content is a dict), never in an assistant block."""
    prompt = "A calm ocean wave rolling onto a sandy beach at sunset, cinematic, photorealistic."
    body = chain_with([reply("Your video is ready.", 3), reply("I'll generate it at 5 seconds.", 2, 2),
                       user("Generated video: " + prompt + ", 16:9", 1)])
    assert "A calm ocean wave" not in resolver.conversation_text(body)
    assert "A calm ocean wave" in resolver.user_text(body)
    assert not resolver.prompt_matches(prompt, resolver.conversation_text(body))
    assert resolver.prompt_matches(prompt, resolver.user_text(body) + " " + resolver.conversation_text(body))


@mutation(RESOLVER, "    return not prompt_matches(expect, mine + \" \" + conversation_text(body))", "    return False",
          expect="a wrongly adopted conversation was delivered")
def test_a_wrong_conversation_is_caught_at_once_before_any_video():
    wrong = chain_with([reply("Done.", NOW - 1), user("Generate image: a single grey pebble on white paper",
                                                       NOW - 10)])
    result, _, _ = poll_chain(wrong, prompt="a plain grey wall, static shot", marker="")
    out = result.outcome
    assert isinstance(out, Failed) and out.code == resolver.ACCOUNT_PROMPT_MISMATCH, \
        "a wrongly adopted conversation was delivered or waited on: %r" % (out,)
    assert out.taken is False, "a conversation that is not ours proves nothing about our order"
    right = chain_with([reply("Done.", NOW - 1), user("Generated video: a plain grey wall, static shot", NOW - 10)])
    assert isinstance(poll_chain(right, marker="")[0].outcome, Pending)


def test_a_conversation_that_is_still_loading_is_not_convicted():
    """The guard judges only once the user's words are readable (RES11): empty or assistant-only reads wait."""
    for body in (chain_with([]), chain_with([reply("Working on it...", NOW)])):
        assert not resolver.adopted_wrong_conversation(body, "a plain grey wall"), \
            "a conversation still loading was called the wrong one"
        assert isinstance(poll_chain(body, marker="")[0].outcome, Pending)
    assert not resolver.adopted_wrong_conversation(chain_with([user("whatever", 1)]), None)


@mutation(POLL, "job.marker or job.prompt", "job.prompt", expect="a marker collision was read as a match")
def test_the_marker_decides_and_the_prompt_is_the_fallback():
    """Two jobs with the SAME prompt on one account tell their conversations apart by the marker."""
    body = chain_with([reply(ACCEPT, NOW - 1), user("Generated video: same prompt text [7777]", NOW - 10)])
    assert isinstance(poll_chain(body, prompt="same prompt text", marker="7777")[0].outcome, Pending)
    other = poll_chain(body, prompt="same prompt text", marker="8888")[0].outcome
    assert isinstance(other, Failed) and other.code == resolver.ACCOUNT_PROMPT_MISMATCH, \
        "a marker collision was read as a match on the prompt alone"
    assert isinstance(poll_chain(body, prompt="same prompt text", marker="")[0].outcome, Pending), \
        "without a marker the prompt prefix is the guard"


def test_the_guard_runs_for_images_too_even_with_no_image_yet():
    """RES18: a wrong image conversation is caught at once; a loading one is not."""
    other = chain_with([reply("You've reached today's image limit. Come back to create more tomorrow.", NOW),
                        {"index_in_conv": 1, "content_type": 1, "create_time": NOW - 5,
                         "content": json.dumps({"text": "a cat sleeping on a keyboard"})}])
    out = poll_chain(other, kind="image", prompt="a fishing pier at first light, wet stone", marker="")[0].outcome
    assert isinstance(out, Failed) and out.code == resolver.ACCOUNT_PROMPT_MISMATCH, \
        "the wrong conversation was read as the job's: %r" % (out,)
    loading = chain_with([reply("...", NOW)])
    assert isinstance(poll_chain(loading, kind="image", prompt="a fishing pier at first light", marker="")[0].outcome,
                      Pending)


# ---- status codes (RES03, RES04, RES05, RES06, RES22) --------------------------------------------------------------
@pytest.mark.parametrize("item,code,transient", [
    (503, errors.HTTP_ERROR, True),
    (Resp(200, bad_json=True), errors.BAD_RESPONSE, True),
    ({"status_code": resolver.NOT_LOGGED_IN}, resolver.ACCOUNT_DEAD_SESSION, False),
    ({"status_code": resolver.DATA_NOT_FOUND}, resolver.ACCOUNT_WRONG_CONVERSATION, False),
    ({"status_code": 999999, "status_desc": "?"}, errors.UNKNOWN_STATUS, True),
])
def test_the_four_chain_fetch_error_codes_keep_their_meaning(item, code, transient):
    http = http_for(FakeTransport({CONV: item}))
    with pytest.raises(resolver.ChainError) as caught:
        run(resolver.fetch_chain(http, CONV))
    assert caught.value.code == code and caught.value.transient is transient, \
        "%s: a chain error changed its code or its retry meaning" % code


def test_a_transport_failure_is_a_transient_read_error():
    def boom(_n):
        raise OSError("connection reset")
    transport = FakeTransport({CONV: boom})
    with pytest.raises(resolver.ChainError) as caught:
        run(resolver.fetch_chain(http_for(transport), CONV))
    assert caught.value.transient and caught.value.code == errors.HTTP_ERROR


@pytest.mark.parametrize("item", [503, Resp(200, bad_json=True), {"status_code": 999999}])
def test_transient_poll_errors_are_pending_until_the_deadline(item):
    result, _, _ = poll_chain(item)
    assert isinstance(result.outcome, Pending) and result.outcome.next_in_s == 30
    assert result.outcome.note, "the reason of a failed round must reach the timeline (RES08)"
    assert not result.effects, "a failed read must not count as a good poll"
    assert "signed_out" not in dict(result.outcome.state_patch)


@mutation(POLL, "    if exc.code == resolver.ACCOUNT_DEAD_SESSION:", "    if False:",
          expect="a dead session ended the job")
def test_a_dead_session_waits_for_fresh_cookies():
    result, _, _ = poll_chain({"status_code": resolver.NOT_LOGGED_IN})
    out = result.outcome
    assert isinstance(out, Pending), "a dead session ended the job: %r" % (out,)
    assert dict(out.state_patch) == {"signed_out": True} and out.note == SIGNED_OUT_NOTE


@mutation(POLL, "        return _pending(SIGNED_OUT_NOTE, {\"signed_out\": True}, answered=False)",
          "        return _pending(SIGNED_OUT_NOTE, {\"signed_out\": True}, answered=True)",
          expect="a signed-out read was taken for an answer")
@mutation(POLL, "        return _pending(str(exc), answered=False) ",
          "        return _pending(str(exc), answered=True) ", expect="a chain error was taken for an answer")
def test_only_a_read_that_worked_is_an_answered_pending():
    """After the render deadline the core waits for one read that answered. A chain error and a dead session are not
    one; a good read that finds the video still rendering is."""
    dead = poll_chain({"status_code": resolver.NOT_LOGGED_IN})
    for what, got in (("a chain error", poll_chain(503)), ("a signed-out read", dead)):
        assert got[0].outcome.answered is False, "%s was taken for an answer" % what
    waiting = chain_with([reply("It will use 2 credits and be ready in 15 minutes.", NOW - 5), mine()])
    assert poll_chain(waiting)[0].outcome.answered is True, "a read that worked was not taken for an answer"


@mutation(POLL, '        return _pending("no conversation to read yet", answered=False)',
          '        return _pending("no conversation to read yet", answered=True)',
          expect="no conversation was taken for an answer")
def test_a_job_with_no_conversation_to_read_is_not_an_answered_read():
    ctx = Ctx(account())
    result = run(poll(ctx, job(ref=None), ctx.settings, http=http_for(FakeTransport({}), ctx.account),
                      clock=lambda: NOW))
    assert result.outcome.answered is False, "no conversation was taken for an answer"


def test_the_wrong_conversation_code_is_terminal():
    out = poll_chain({"status_code": resolver.DATA_NOT_FOUND})[0].outcome
    assert isinstance(out, Failed) and out.code == "account.wrong_conversation" and out.taken is False


@mutation(POLL, "    healed = {\"signed_out\": False} if job.provider_state.get(\"signed_out\") else {}",
          "    healed = {}", expect="the signed-out flag was not cleared")
def test_a_good_read_clears_the_signed_out_flag():
    waiting = chain_with([reply("It will use 2 credits and be ready in 15 minutes.", NOW - 5), mine()])
    out = poll_chain(waiting, state={"signed_out": True})[0].outcome
    assert isinstance(out, Pending) and dict(out.state_patch).get("signed_out") is False, \
        "the signed-out flag was not cleared by a good read: %r" % (dict(out.state_patch),)
    plain = poll_chain(waiting)[0].outcome
    assert "signed_out" not in dict(plain.state_patch), "a job that was never signed out gets no flag"
    # the same flag also clears when the good read ends the job in failure
    failed = poll_chain(chain_with([reply(DAILY, NOW - 5), mine()]), state={"signed_out": True})[0].outcome
    assert isinstance(failed, Failed) and dict(failed.state_patch).get("signed_out") is False


def test_not_ready_is_pending_with_no_note_and_a_good_read_effect():
    body = chain_with([reply("It will use 2 credits and be ready in 15 minutes.", NOW - 5), mine()])
    result, _, _ = poll_chain(body)
    assert isinstance(result.outcome, Pending) and result.outcome.note == ""
    assert result.outcome.next_in_s == 30 and any(isinstance(e, Success) for e in result.effects)


def test_a_job_with_no_conversation_yet_just_waits():
    ctx = Ctx()
    j = job(ref=None)
    out = run(poll(ctx, j, ctx.settings, http=http_for(FakeTransport()), clock=lambda: NOW)).outcome
    assert isinstance(out, Pending)


# ---- copyright and no-charge (RES13, RUN03) -----------------------------------------------------------------------
@mutation(RESOLVER, "        if at and at >= since and _is_copyright(text):", "        if at and _is_copyright(text):",
          expect="a copyright block from before the Re-check ended the re-checked job")
def test_copyright_block_after_the_mark_fails_at_once_and_older_ones_are_ignored():
    after = chain_with([reply(COPYRIGHT, NOW - 30), mine()])
    out = poll_chain(after, since=NOW - 600)[0].outcome
    assert isinstance(out, Failed) and out.code == errors.CONTENT_REFUSED and "copyright" in out.detail
    assert dict(out.state_patch) == {"no_recheck": True}, "a copyright block must say Re-check cannot help"
    before = chain_with([reply(COPYRIGHT, NOW - 600), mine()])
    out = poll_chain(before, since=NOW - 30)[0].outcome
    assert isinstance(out, Pending), "a copyright block from before the Re-check ended the re-checked job: %r" % (out,)


@mutation(POLL, "    if since is not None and seen_at >= since:", "    if since is not None:",
          expect="a block first seen before the Re-check ended the re-checked job")
@mutation(RESOLVER, "at < since)", "at < 0)", expect="an older dated block ended the re-checked job")
def test_a_block_that_is_not_the_newest_reply_or_has_no_time_fails_at_once_when_first_seen_after_the_mark():
    """The second path (`first_dola_saying`): any message, judged by when WE first saw it."""
    undated = chain_with([reply("Anything else I can help with?", NOW - 5, 3), reply(COPYRIGHT, 0, 2), mine()])
    result, _, _ = poll_chain(undated, since=NOW - 600)
    out = result.outcome
    assert isinstance(out, Failed) and out.code == errors.CONTENT_REFUSED and out.taken
    assert dict(out.state_patch) == {"copyright_seen_at": NOW, "no_recheck": True}, dict(out.state_patch)
    # a block we saw before the Re-check mark is an older order's: ignored, the first sighting is kept
    seen = poll_chain(undated, since=NOW - 600, state={"copyright_seen_at": NOW - 3600})[0].outcome
    assert isinstance(seen, Pending) and dict(seen.state_patch) == {"copyright_seen_at": NOW - 3600}, \
        "a block first seen before the Re-check ended the re-checked job: %r" % (seen,)
    # a dated block from before the mark is never a candidate
    old = chain_with([reply("Anything else I can help with?", NOW - 5, 3), reply(COPYRIGHT, NOW - 3600, 2), mine()])
    assert isinstance(poll_chain(old, since=NOW - 600)[0].outcome, Pending), \
        "an older dated block ended the re-checked job"


def test_the_first_sighting_of_a_block_is_recorded_even_with_no_mark_and_waits_for_a_cell_still_rendering():
    undated = chain_with([reply("Anything else?", NOW - 5, 3), reply(COPYRIGHT, 0, 2), mine()])
    out = poll_chain(undated, since=None)[0].outcome
    assert isinstance(out, Pending) and dict(out.state_patch) == {"copyright_seen_at": NOW}
    rendering = chain_with([reply(COPYRIGHT, 0, 4), video_msg(1, NOW - 100, cell(status=1)), mine()])
    waiting = poll_chain(rendering, since=NOW - 600)[0].outcome
    assert isinstance(waiting, Pending) and "copyright_seen_at" not in dict(waiting.state_patch)


def test_a_copyright_block_with_no_mark_is_not_judged_here():
    out = poll_chain(chain_with([reply(COPYRIGHT, NOW - 30), mine()]), since=None)[0].outcome
    assert isinstance(out, Pending)


@mutation(POLL, "job.render_since or job.sent_at", "job.render_since",
          expect="a reconciled order missed the copyright block")
def test_a_reconciled_order_with_only_a_send_time_fails_at_once_on_the_copyright_block_read_with_the_acceptance():
    """Job ce7bc6e8: found by reconcile (no render_since), every read held the block above the older acceptance."""
    sent = dt.datetime.fromtimestamp(NOW - 3000, dt.UTC)
    body = chain_with([reply(COPYRIGHT, NOW - 30, 3), reply(ACCEPT, NOW - 2900, 2), mine()])
    ctx = Ctx(account())
    j = job(since=None, sent_at=sent, prompt="a plain grey wall, static shot", marker="4100")
    out = run(poll(ctx, j, ctx.settings, http=http_for(FakeTransport({CONV: body}), ctx.account),
                   clock=lambda: NOW)).outcome
    assert isinstance(out, Failed) and out.code == errors.CONTENT_REFUSED and out.taken, \
        "a reconciled order missed the copyright block: %r" % (out,)
    assert dict(out.state_patch) == {"no_recheck": True}


def test_a_copyright_block_while_a_cell_still_renders_waits_for_it():
    body = chain_with([reply(COPYRIGHT, NOW - 30), video_msg(1, NOW - 100, cell(status=1)), mine()])
    assert isinstance(poll_chain(body, since=NOW - 600)[0].outcome, Pending)


@mutation(RESOLVER, "    return (text or \"\").split(\"\\n\\n\", 1)[0].strip()", "    return (text or \"\").strip()",
          expect="an old 'didn't use any credit'")
def test_newest_reply_saying_no_credit_was_used_ends_the_job_older_ones_do_not():
    stopped = poll_chain(chain_with([reply(NO_CHARGE, NOW - 5, 3), reply(ACCEPT, NOW - 50, 2), mine()]))[0].outcome
    assert isinstance(stopped, Failed) and stopped.code == errors.CONTENT_REFUSED and "didn" in stopped.detail
    assert dict(stopped.state_patch) == {}, "only a copyright block says no_recheck"
    old = poll_chain(chain_with([reply(ACCEPT, NOW - 5, 3), reply(NO_CHARGE, NOW - 50, 2), mine()]))[0].outcome
    assert isinstance(old, Pending), \
        "an old 'didn't use any credit' before dola took the order ended the job: %r" % (old,)
    assert resolver.stopped_without_charge("I can't generate the video. No credits were used.")
    assert resolver.stopped_without_charge("I can't generate the video and didn't use any credit for this "
                                           "generation.")


def test_no_charge_in_an_image_job_ends_it_too():
    out = poll_chain(chain_with([reply(NO_CHARGE, NOW - 5), mine()]), kind="image")[0].outcome
    assert isinstance(out, Failed) and out.code == errors.CONTENT_REFUSED


def test_a_likeness_refusal_ends_the_job_with_its_own_code():
    out = poll_chain(chain_with([reply(LIKENESS, NOW - 5), mine()]))[0].outcome
    assert isinstance(out, Failed) and out.code == errors.LIKENESS_REFUSED, "wrong code %r" % (out,)


def test_a_refusal_to_generate_with_no_credit_words_is_final():
    out = poll_chain(chain_with([reply("I can't generate the requested content. Try something else.", NOW - 5),
                                 mine()]))[0].outcome
    assert isinstance(out, Failed) and out.code == errors.CONTENT_REFUSED


def test_out_of_credit_with_no_order_ends_but_after_an_accepted_order_it_is_noise():
    final = poll_chain(chain_with([reply(DAILY, NOW - 5), mine()]))[0]
    assert isinstance(final.outcome, Failed) and final.outcome.code == errors.NO_VIDEO
    assert "daily limit" in final.outcome.detail or final.outcome.detail
    taken = chain_with([reply(DAILY, NOW - 5, 3), reply(
        "Only two videos can be generated at a time. I'll start by generating two 15-second segments for you. "
        + ACCEPT + " You still have 0 video credits left today.", NOW - 50, 2), mine()])
    assert isinstance(poll_chain(taken)[0].outcome, Pending), "a paid order was dropped by a later 'daily limit'"


def test_something_went_wrong_in_the_conversation_ends_it_without_saying_retry():
    out = poll_chain(chain_with([reply("Something went wrong. Please try again.", NOW - 5), mine()]))[0].outcome
    assert isinstance(out, Failed) and "went wrong" in out.detail and "retry" not in out.detail


def test_a_conversation_still_rendering_never_stops_early():
    """RES19."""
    body = chain_with([reply("It will use 2 credits and be ready in 15 minutes.", NOW - 5), mine()])
    assert isinstance(poll_chain(body)[0].outcome, Pending)


# ---- the quiet rule (RES15) -------------------------------------------------------------------------------------
def quiet(text, age_s, image=False):
    blocks = [{"content": {"text_block": {"text": text}}}]
    if image:
        blocks.append({"block_type": 2074, "content": {"creation_block": {"creations": [
            {"type": 1, "image": {"image_ori_raw": {"url": "https://x/board.png"}}}]}}})
    return chain_with([{"index_in_conv": 2, "content_type": 9999, "create_time": NOW - age_s,
                        "content": json.dumps(blocks)}, mine(at=NOW - age_s - 5)])


@mutation(RESOLVER, "now - last >= constants.QUIET_GRACE_S", "now - last >= 10 ** 9",
          expect="dola has no order and went quiet")
def test_quiet_conversation_with_no_order_stops_with_the_right_reason():
    long = constants.QUIET_GRACE_S + 60
    for body, code in ((quiet(STORYBOARD, long, image=True), errors.NO_VIDEO),
                       (quiet(ASK, long), errors.NEEDS_CONFIRM)):
        out = poll_chain(body)[0].outcome
        assert isinstance(out, Failed) and out.code == code, \
            "dola has no order and went quiet, yet the job waits or ends wrongly: %r" % (out,)
    for body, why in ((quiet(STORYBOARD, 600, image=True), "dola spoke 10 minutes ago"),
                      (quiet("Video generation currently supports durations from 4 to 15 seconds. I can generate "
                             "it at the nearest supported duration of 15 seconds. " + ACCEPT, long),
                       "dola took the order")):
        assert isinstance(poll_chain(body)[0].outcome, Pending), "gave up on a job too early: %s" % why


# ---- every video, the pending grace (RES16, RUN05) -----------------------------------------------------------------
@mutation(RESOLVER, "< constants.PENDING_GRACE_S", "< 0", expect="delivered short while a cell was still rendering")
def test_pending_cells_hold_the_delivery_until_the_grace_ends():
    first_at = NOW - 100
    body = chain_with([video_msg(2, NOW - 90, cell(status=1)), video_msg(1, first_at, cell("https://x/1.mp4", vid="a")),
                       mine()])
    held = poll_chain(body, now=first_at + constants.PENDING_GRACE_S - 60)[0].outcome
    assert isinstance(held, Pending), "delivered short while a cell was still rendering: %r" % (held,)
    out = poll_chain(body, now=first_at + constants.PENDING_GRACE_S + 60)[0].outcome
    assert isinstance(out, Done) and [o.ref for o in out.outputs] == ["https://x/1.mp4"], \
        "after the grace the finished videos must be delivered: %r" % (out,)


def test_all_n_videos_are_delivered_numbered_continuously_and_failed_cells_skipped():
    body = chain_with([video_msg(1, NOW - 90, cell("https://x/1.mp4", vid="a"), cell(status=4),
                                 cell("https://x/2.mp4", vid="b"), cell("https://x/3.mp4", vid="c")), mine()])
    out = poll_chain(body)[0].outcome
    assert isinstance(out, Done)
    assert [(o.idx, o.ref, o.upstream_id) for o in out.outputs] == [(0, "https://x/1.mp4", "a"),
                                                                    (1, "https://x/2.mp4", "b"),
                                                                    (2, "https://x/3.mp4", "c")]


# ---- duration drift (RUN06) --------------------------------------------------------------------------------------
@mutation(POLL, "> constants.DURATION_TOLERANCE_S", "> 1000", expect="drifted video was delivered without a warning")
def test_a_wrong_duration_is_delivered_with_a_warning_naming_both_values():
    body = chain_with([video_msg(1, NOW - 90, cell("https://x/1.mp4", vid="a", duration=15.105)), mine()])
    result = poll_chain(body, duration=30)[0]
    assert isinstance(result.outcome, Done)
    drift = [w for w in result.warnings if w.code == errors.WARN_DURATION_DRIFT]
    assert drift and "15.1" in drift[0].text and "30" in drift[0].text, \
        "a drifted video was delivered without a warning naming both values: %r" % (result.warnings,)
    near = chain_with([video_msg(1, NOW - 90, cell("https://x/1.mp4", vid="a", duration=30.08)), mine()])
    assert not poll_chain(near, duration=30)[0].warnings, "30.08 s for 30 s is on target (tolerance 2 s)"
    edge = chain_with([video_msg(1, NOW - 90, cell("https://x/1.mp4", vid="a", duration=32.0)), mine()])
    assert not poll_chain(edge, duration=30)[0].warnings, "exactly 2 s off is inside the tolerance"
    many = chain_with([video_msg(1, NOW - 90, cell("https://x/1.mp4", vid="a", duration=15.1),
                                 cell("https://x/2.mp4", vid="b", duration=30.0)), mine()])
    texts = [w.text for w in poll_chain(many, duration=30)[0].warnings]
    assert len(texts) == 1 and texts[0].startswith("video 1: "), texts


def test_a_cell_with_no_length_in_the_chain_gives_no_drift_warning():
    body = chain_with([video_msg(1, NOW - 90, cell("https://x/1.mp4", vid="a")), mine()])
    assert not poll_chain(body, duration=30)[0].warnings


# ---- events, account, proxy ---------------------------------------------------------------------------------------
def test_every_polls_dola_text_goes_to_the_timeline_as_upstream_and_silence_writes_nothing():
    body = chain_with([reply("It will use 2 credits and be ready in 15 minutes.", NOW - 5), mine()])
    _, ctx, _ = poll_chain(body)
    assert ctx.events == [("It will use 2 credits and be ready in 15 minutes.", "resolve", "upstream")]
    _, silent, _ = poll_chain(chain_with([mine()]))
    assert silent.events == [], "a round with no dola text must not write an empty line"
    refused = poll_chain(chain_with([reply(NO_CHARGE, NOW - 5), mine()]))[1]
    assert refused.events and "didn" in refused.events[0][0], "the evidence of a conclusion is dola's own words"


def test_poll_reads_through_the_account_cookie_and_proxy_and_posts_cmd_3100():
    result, ctx, transport = poll_chain(chain_with([mine()]))
    (call,) = transport.posts
    assert call["json"]["cmd"] == 3100
    assert call["proxies"] == {"http": "http://user:pass@proxy.invalid:8080", "https": "http://user:pass@proxy.invalid:8080"}
    assert "sessionid=fake-sessionid" in call["headers"]["cookie"]
    assert call["headers"]["referer"].endswith("/chat/" + CONV)
    direct, _, direct_transport = poll_chain(chain_with([mine()]), acc=account(proxy=False))
    assert direct_transport.posts[0]["proxies"] is None


def test_poll_is_read_only_for_a_ctx_that_cannot_send():
    """A poll runs for a disabled account too: the ctx has no mark_sent/report_ref and poll never asks for them."""
    ctx = Ctx()
    assert not hasattr(ctx, "mark_sent") and not hasattr(ctx, "report_ref")
    j = job()
    transport = FakeTransport({CONV: chain_with([mine()])})
    run(poll(ctx, j, ctx.settings, http=http_for(transport), clock=lambda: NOW))
    assert len(transport.posts) == 1 and not transport.gets


# ---- conversation names (RES25) -------------------------------------------------------------------------------------
def test_conversation_names_come_from_batch_get_and_placeholders_are_skipped():
    transport = FakeTransport(names={"38418045224341265": "Larva Battle Royale", "38417974042485009": "  ",
                                     "38417937625688337": "New chat"})
    http = http_for(transport)
    got = run(resolver.conversation_names(http, ["38418045224341265", "38417974042485009", "38417937625688337"]))
    assert got == {"38418045224341265": "Larva Battle Royale"}, "conversation names are not read from batch_get"
    (call,) = transport.posts
    assert call["url"].endswith("/im/conversation/batch_get") and call["json"]["cmd"] == 1111
    assert run(resolver.conversation_names(http, [])) == {}
    assert len(transport.posts) == 1, "no ids, no call"


def test_a_failed_names_call_gives_nothing_not_an_error():
    class Down(FakeTransport):
        def post(self, *a, **k):
            return Resp(503, {})
    assert run(resolver.conversation_names(http_for(Down()), ["1"])) == {}


# ---- text of a conversation (RES09) and the codes every chain error declares (RES04) -------------------------------
def test_conversation_text_is_the_assistant_replies_only_newest_first_one_paragraph_each():
    body = chain_with([reply("Something went wrong. Please try again.", 3, 3), reply("Your video: sóng biển", 2, 2),
                       user("Generated video: sidebar title of another conversation", 1)])
    want = "Something went wrong. Please try again.\n\nYour video: sóng biển"
    assert resolver.conversation_text(body) == want, "two dola replies run together, or the user's words leaked in"
    assert resolver.conversation_text(chain_with([])) == ""


def test_every_chain_error_declares_its_own_code():
    """Only the raiser knows the real reason; a catcher that guesses merges a dead session, a wrong conversation, a
    broken file and 'dola did not deliver' into one code."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(resolver))
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ChainError"]
    assert len(raises) >= 12
    assert all(any(k.arg == "code" for k in call.keywords) for call in raises), \
        "a ChainError was raised without a code: the catcher would have to guess"


# ---- the upstream text a poll reports fits the event cap and leads with dola's newest words ---------------
LONG_OLD = " ".join("Here is another idea for your scene number %d, with a lot of descriptive detail." % i
                   for i in range(40))
LAST_WORDS = (
    ("a refusal", "I can't generate the requested images. Try something else.", "dola.content_refused"),
    ("a likeness refusal", "For likeness protection, Dreamina Seedance 2.5 only supports generating videos featuring "
                           "yourself. Use another image, or create a video from text.", "dola.likeness_refused"),
    ("a question", "To proceed, please upload the 3 reference images of the 3 people and the stage reference image.",
     "dola.needs_confirm"),
)


@mutation(POLL, "resolver.recent_conversation_text(body, constants.EVENT_TEXT_MAX)", "resolver.conversation_text(body)",
          expect="the whole conversation was reported")
@pytest.mark.parametrize("name, words, code", LAST_WORDS, ids=[w[0] for w in LAST_WORDS])
def test_a_long_conversations_newest_words_survive_the_event_cap_and_set_the_deadline_cause(name, words, code):
    from relay_core.redact import redact_log
    from relay_dola.spec import SPEC
    body = chain_with([reply(words, NOW - 5, 8)]
                      + [reply(LONG_OLD, NOW - 50 - i, 7 - i) for i in range(5)] + [mine()])
    assert len(resolver.conversation_text(body)) > constants.EVENT_TEXT_MAX, "premise: the conversation is long"
    _, ctx, _ = poll_chain(body)
    stored = [redact_log(text) for text, _stage, source in ctx.events if source == "upstream"]
    assert len(stored) == 1 and not stored[0].startswith("[omitted"), (
        "the whole conversation was reported and the core stored only a marker: %r" % stored[:1])
    assert stored[0].startswith(words), "dola's newest words must come first"
    assert SPEC.deadline_cause(stored[0], {}) == code, "the deadline cause lost dola's last words (%s)" % name


def test_the_newest_reply_alone_over_the_cap_is_cut_and_older_paragraphs_never_displace_it():
    body = chain_with([reply("x" * 5000, NOW - 5, 3), reply("older reply", NOW - 50, 2), mine()])
    assert resolver.recent_conversation_text(body, 100) == "x" * 100
    body = chain_with([reply("newest reply", NOW - 5, 3), reply("older reply", NOW - 50, 2), mine()])
    assert resolver.recent_conversation_text(body, 25) == "newest reply\n\nolder reply"
    assert resolver.recent_conversation_text(body, 24) == "newest reply"


@mutation("packages/provider-dola/src/relay_dola/constants.py", "EVENT_TEXT_MAX = LOG_VALUE_LIMIT",
          "EVENT_TEXT_MAX = LOG_VALUE_LIMIT + 1",
          expect="the event text cap is above the core's")
def test_the_event_text_cap_never_exceeds_what_the_core_stores():
    """The pure constant mirrors `relay_core.redact`'s limit (the core turns a longer string into a bare marker)."""
    from relay_core.redact import LOG_VALUE_LIMIT, trim_log_value
    assert constants.EVENT_TEXT_MAX <= LOG_VALUE_LIMIT, (
        "the event text cap is above the core's: %d > %d" % (constants.EVENT_TEXT_MAX, LOG_VALUE_LIMIT))
    assert trim_log_value("x" * constants.EVENT_TEXT_MAX) == "x" * constants.EVENT_TEXT_MAX
