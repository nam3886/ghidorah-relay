"""The customer's words are never dola's reply. dola stores the customer's message in two shapes: a dict
`{"text": ...}`, and a block list marked `user_type 1` (every i2v conversation and every incremental read the page makes
itself). A reply read that took the block-list shape as dola's words judged the customer's prompt as dola's: a
prompt quoting "daily limit" rested a pool, "please confirm" re-sent a taken order. Tested on the real S1 captures."""

import pytest
from kit_b import chain_with, reply, s1_capture, s1_reads, user, user_block

from relay_core.testing.mutation import mutation
from relay_dola import classify
from relay_dola.engine import resolver

RESOLVER = "packages/provider-dola/src/relay_dola/engine/resolver.py"

OUT_OF_CREDIT = "You've reached the daily limit for video generation. Please try again tomorrow."
CONFIRM = "Would you like me to proceed with part one only? Please confirm."
COPYRIGHT = "For copyright protection, I can't show you the generated video."
NAMES = ("t2v", "i2v", "image")


def user_words(body: dict) -> str:
    return resolver.user_text(body)


@mutation(RESOLVER, "    if is_user_message(msg):\n        return []", "    if False:\n        return []",
          expect="the customer's words were read as dola's")
@pytest.mark.parametrize("name", NAMES)
def test_the_reply_text_of_every_real_read_excludes_the_customers_prompt(name):
    marker = s1_capture(name)["marker"]
    reads = s1_reads(name)
    assert len(reads) >= 5
    block_reads = [b for _, anchor, b in reads if anchor < 9_000_000_000_000_000 and marker in user_words(b)]
    assert block_reads, "%s: no partial page read carries the prompt as a block list: the test would be vacuous" % name
    for t, anchor, body in reads:
        assert marker not in resolver.conversation_text(body), \
            "the customer's words were read as dola's (%s t=%s anchor=%s)" % (name, t, anchor)


@mutation(RESOLVER, "        elif isinstance(content, list) and is_user_message(msg):",
          "        elif False:", expect="a block-list user message was not read")
def test_user_text_reads_the_block_list_shape_of_the_i2v_conversation():
    first = [b for _, anchor, b in s1_reads("i2v") if anchor == 6][0]      # attachment, prompt as a block, dola
    assert s1_capture("i2v")["marker"] in resolver.user_text(first), "a block-list user message was not read"
    # and the dict shape the v4-era chains carry is still read
    dict_shape = chain_with([user("Generated video: red kite [1234]")])
    assert resolver.user_text(dict_shape) == "Generated video: red kite [1234]"


@pytest.mark.parametrize("phrase", [OUT_OF_CREDIT, CONFIRM, COPYRIGHT])
@pytest.mark.parametrize("name", NAMES)
def test_a_prompt_that_quotes_dolas_sentences_does_not_change_what_dola_said(name, phrase):
    """Every read of the S1 sessions with the customer's prompt swapped for a refusal / out-of-credit / question: the
    reply text is exactly what it was (dola's own words), so no verdict can come from the prompt."""
    plain, swapped = s1_reads(name), s1_reads(name, swap=phrase)
    assert len(plain) == len(swapped) >= 5
    for (t, anchor, a), (_, _, b) in zip(plain, swapped, strict=True):
        text = resolver.conversation_text(b)
        assert text == resolver.conversation_text(a), "the prompt was judged as dola's reply (%s t=%s anchor=%s)" % (
            name, t, anchor)
        assert phrase not in text
        assert resolver.copyright_block_after(b, 1.0) == resolver.copyright_block_after(a, 1.0)


def test_a_conversation_with_only_the_customers_message_has_no_reply_text():
    for message in (user("Generated video: " + OUT_OF_CREDIT + " [4100]", 10), user_block(OUT_OF_CREDIT, 10)):
        body = chain_with([message])
        assert resolver.conversation_text(body) == ""
        assert classify.classify_reply(resolver.conversation_text(body)).outcome is not classify.SubmitOutcome.NO_POINTS


def test_a_message_with_no_user_type_keeps_its_old_reading():
    """The v4-era chains carry no `user_type`: a block list there is dola's, a dict is the customer's."""
    body = chain_with([reply("Your video is ready.", 3, 2), user("Generated video: a kite", 1)])
    assert resolver.conversation_text(body) == "Your video is ready."
    assert resolver.user_text(body) == "Generated video: a kite"
    assert all("user_type" not in m for m in resolver.chain_messages(body))
