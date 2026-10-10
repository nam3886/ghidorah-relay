"""Recover (coverage RECOV00): the clean videos of one conversation, checked to be the job's, as `Output`s.

The v4 demo attached the videos in the database; here the engine only reads and returns outputs, and the core stores
them and dedupes by `upstream_id` (a second Recover of the same conversation returns the same ids, which is what makes
it idempotent). The guard test is the one that matters: a conversation of another job must never be attached.
"""

import asyncio
import base64
import json

import pytest
from kit_b import CONV, FakeTransport, chain_with, http_for, reply, sent_of, user

from relay_core.providers import Output
from relay_core.testing.mutation import mutation
from relay_dola.engine.commands import CommandRefused, recover
from relay_dola.engine.recovery import RecoveryError, recover_outputs

RECOVERY = "packages/provider-dola/src/relay_dola/engine/recovery.py"
NOW = 1_790_000_000.0
PROMPT = "a red kite over rice fields"
SENT_TEXT = sent_of(PROMPT, "4100")


def run(coro):
    return asyncio.run(coro)


def cell(url, vid, watermark_only=False, duration=30.08):
    video = {"vid": vid, "duration": str(duration), "download_url": "https://wm.example/%s.mp4" % vid}
    if not watermark_only:
        token = base64.b64encode(url.encode()).decode()
        video["video_model"] = json.dumps({"video_list": {"720p": {"main_url": token}}})
    return {"type": 2, "video": video}


def conv(*cells, text="Generated video: " + SENT_TEXT, answer="Your video is ready."):
    msgs = [{"index_in_conv": 3, "create_time": NOW - 10, "content_type": 9999, "content": json.dumps([
        {"block_type": 2074, "content": {"creation_block": {"creations": list(cells)}}}])}]
    if answer:
        msgs.append(reply(answer, NOW - 20, 2))
    msgs.append(user(text, NOW - 30))
    return chain_with(msgs)


def recovered(body, *, expect=SENT_TEXT[-6:], sent_text=None, prompt=PROMPT):
    http = http_for(FakeTransport({CONV: body}))
    return run(recover_outputs(http, CONV, expect=expect, sent_text=sent_text, now=NOW, prompt=prompt))


def test_recover_returns_every_clean_video_in_order_with_their_ids():
    body = conv(cell("https://x/1.mp4", "a"), cell("https://x/2.mp4", "b"), cell("https://x/3.mp4", "c"))
    outs = recovered(body)
    assert [(o.idx, o.ref, o.upstream_id, o.mime) for o in outs] == [
        (0, "https://x/1.mp4", "a", "video/mp4"), (1, "https://x/2.mp4", "b", "video/mp4"),
        (2, "https://x/3.mp4", "c", "video/mp4")]
    assert all(isinstance(o, Output) and o.meta["conversation_id"] == CONV for o in outs)


def test_recover_is_idempotent_the_same_conversation_gives_the_same_ids():
    body = conv(cell("https://x/1.mp4", "a"), cell("https://x/2.mp4", "b"))
    first, again = recovered(body), recovered(body)
    assert [o.upstream_id for o in first] == [o.upstream_id for o in again] == ["a", "b"], \
        "recovering twice must give the ids the core dedupes on"


def test_a_watermarked_video_is_not_recovered():
    outs = recovered(conv(cell("https://x/1.mp4", "a"), cell("https://x/2.mp4", "b", watermark_only=True)))
    assert [o.upstream_id for o in outs] == ["a"]
    with pytest.raises(RecoveryError, match="no clean video"):
        recovered(conv(cell("https://x/2.mp4", "b", watermark_only=True)))


CARRIES = "    if sent_text and not resolver.carries_text(resolver.user_text(body), sent_text, prompt or \"\"):"


@mutation(RECOVERY, CARRIES,
          "    if False:", expect="recovery attached another job's video")
def test_recover_checks_the_conversation_carries_the_jobs_text():
    """It could otherwise attach another job's paid video (the guard v4 passed down as expect_prompt)."""
    foreign = conv(cell("https://x/9.mp4", "z"), text="Generated video: a cat sleeping [8888]")
    with pytest.raises(RecoveryError):
        recovered(foreign)                                         # the marker guard
    try:
        recovered(foreign, expect=None, sent_text=SENT_TEXT)       # the strict whole-text check
    except RecoveryError as exc:
        assert "not this job's" in str(exc)
    else:
        pytest.fail("recovery attached another job's video")
    mine = conv(cell("https://x/1.mp4", "a"))
    assert [o.upstream_id for o in recovered(mine, sent_text=SENT_TEXT)] == ["a"]
    with pytest.raises(RecoveryError, match="not this job's"):     # no customer words: the sent text proves nothing
        recovered(mine, sent_text=SENT_TEXT, prompt="\u200b ")


def test_the_strict_check_refuses_an_unreadable_conversation_but_the_marker_guard_waits():
    """The marker guard is silent while the user's words are unreadable (v4); with the whole sent text Recover
    needs positive proof, because it moves money."""
    loading = chain_with([reply("Your video is ready.", NOW - 20)] + [{
        "index_in_conv": 3, "create_time": NOW - 10, "content_type": 9999, "content": json.dumps([
            {"block_type": 2074, "content": {"creation_block": {"creations": [cell("https://x/1.mp4", "a")]}}}])}])
    assert [o.upstream_id for o in recovered(loading)] == ["a"]
    with pytest.raises(RecoveryError):
        recovered(loading, sent_text=SENT_TEXT)


def test_a_conversation_dola_ended_for_good_is_refused_with_its_reason():
    refused = chain_with([reply("You've reached the daily limit for video generation.", NOW - 20),
                          user("Generated video: " + SENT_TEXT, NOW - 30)])
    with pytest.raises(RecoveryError, match="dola.no_video"):
        recovered(refused)


def test_an_unreadable_or_foreign_conversation_is_a_clear_error():
    http = http_for(FakeTransport({CONV: {"status_code": 712017001}}))
    with pytest.raises(RecoveryError, match="account.wrong_conversation"):
        run(recover_outputs(http, CONV, expect=None, sent_text=None, now=NOW))


def test_recover_only_reads_it_posts_one_chain_read_and_downloads_nothing():
    transport = FakeTransport({CONV: conv(cell("https://x/1.mp4", "a"))})
    run(recover_outputs(http_for(transport), CONV, expect=None, sent_text=None, now=NOW))
    assert transport.chain_reads() == [CONV] and not transport.gets


# ---- the command's own guard: another conversation needs proof -----------------------------------------------------
@mutation("packages/provider-dola/src/relay_dola/engine/commands.py", "    if not own and not expect:", "    if False:",
          expect="another conversation was recovered with no proof")
def test_the_command_refuses_another_conversation_without_the_jobs_marker():
    transport = FakeTransport({CONV: conv(cell("https://x/1.mp4", "a"), text="Generated video: someone else's")})
    http = http_for(transport)
    for args in ({"conversation_id": CONV}, {"conversation_id": CONV, "external_ref": "999"}):
        try:
            run(recover(http, args, NOW))
        except CommandRefused:
            pass
        else:
            pytest.fail("another conversation was recovered with no proof")
    assert not transport.posts, "another conversation was read with no proof"
    with pytest.raises(CommandRefused, match="needs a conversation id"):
        run(recover(http, {}, NOW))


def test_the_command_recovers_the_jobs_own_conversation_and_checks_a_given_marker():
    http = http_for(FakeTransport({CONV: conv(cell("https://x/1.mp4", "a"))}))
    got = run(recover(http, {"external_ref": CONV, "job_id": "j", "account_id": "a"}, NOW))
    assert [o.upstream_id for o in got["outputs"]] == ["a"], "the job's own conversation is the job's by definition"
    again = run(recover(http, {"conversation_id": CONV, "marker": "[4100]"}, NOW))
    assert [o.upstream_id for o in again["outputs"]] == ["a"]
    with pytest.raises(RecoveryError):
        run(recover(http, {"conversation_id": CONV, "marker": "[9999]"}, NOW))
    full = run(recover(http, {"conversation_id": CONV, "sent_text": SENT_TEXT, "prompt": PROMPT}, NOW))
    assert len(full["outputs"]) == 1
