"""The gpt stream reader: the v1 SSE deltas of the one send, the verdict on them, and the conversation read that is
the fallback when the stream breaks. Recorded streams are the spike's (fixtures/gpt/spike-261104); the refusal and
moderation shapes were never seen live and are synthetic (fixtures/README.md "Gaps")."""

import copy
import json

from gptkit import fixture

from relay_core.testing.mutation import mutation
from relay_gpt.engine.stream import Image, Reader, read_conversation, verdict

STREAM_FILE = "packages/provider-gpt/src/relay_gpt/engine/stream.py"
ROLE_TOOL = "    if role != \"tool\" or message.get(\"status\") != \"finished_successfully\":"
ANY_ROLE = "    if message.get(\"status\") != \"finished_successfully\":"
COMPLETE_AND = "    if r.complete and not r.images and r.assistant_text.strip():"
TRUE_AND = "    if True and not r.images and r.assistant_text.strip():"
DONE_AND_FINISHED = "complete=self._complete and _finished(messages)"
DONE_ONLY = "complete=self._complete"
CID = "6aeb5d18-6638-83ec-a053-487d9b537d06"
OUR_MESSAGE = "360bfd87-efe9-4516-993b-1f006fb68a6b"     # the edit's user message id (conv_edit)


def read(lines):
    reader = Reader()
    for line in lines:
        reader.feed(line)
    return reader.reading()


def lines_of(name: str) -> list[str]:
    return fixture(name)["stream_lines"]


def delta(obj) -> list[str]:
    return ["event: delta", "data: " + json.dumps(obj), ""]


def message(mid, role, parts, *, status="finished_successfully", end_turn=None, content_type="text") -> dict:
    return {"v": {"message": {"id": mid, "author": {"role": role}, "content": {"content_type": content_type,
            "parts": parts}, "status": status, "end_turn": end_turn}, "conversation_id": "c-1"}}


HEAD = ["event: delta_encoding", 'data: "v1"', "",
        'data: {"type":"resume_conversation_token","kind":"topic","token":"<redacted>","conversation_id":"c-1"}', ""]
DONE = ['data: {"type":"message_stream_complete","conversation_id":"c-1"}', "", "data: [DONE]", ""]


def refusal_text() -> list[str]:
    """An assistant text streamed the v1 way: the message, then appends that name the path once and then omit it."""
    return (delta(message("a-1", "assistant", [""], status="in_progress"))
            + delta({"p": "/message/content/parts/0", "o": "append", "v": "I can't help "})
            + delta({"v": "with that request."})
            + delta({"p": "", "o": "patch", "v": [
                {"p": "/message/status", "o": "replace", "v": "finished_successfully"},
                {"p": "/message/end_turn", "o": "replace", "v": True}]}))


def test_t2i_stream_gives_one_image_and_the_conversation():
    r = read(lines_of("sse_t2i_1x1"))
    assert r.images == [Image("sediment://file_0000000087cc820797dbca5224902b9c",
                              "file_0000000087cc820797dbca5224902b9c", 1254, 1254)]
    assert r.conversation == "6aeb5d96-8c24-83ec-a04c-92c10e5e0702"
    assert r.complete and not r.blocked and r.title == "Generate Ramen Image"
    assert r.user_message == "1b4326f5-8fe9-409d-8f62-4906eb968b28"      # t5A echoes the user message
    assert verdict(r) == "image"
    wide = read(lines_of("sse_t2i_16x9"))
    assert [(i.width, i.height) for i in wide.images] == [(1672, 941)] and verdict(wide) == "image"


@mutation(STREAM_FILE, ROLE_TOOL, ANY_ROLE, expect="the input pointer was taken for a result")
def test_the_input_pointer_of_an_edit_is_never_a_result():
    result = "sediment://file_00000000f8988207b5be52138be217d0"
    body = fixture("conv_edit")["body"]
    user = body["mapping"][OUR_MESSAGE]["message"]
    the_input = user["content"]["parts"][0]["asset_pointer"]
    assert the_input.startswith("sediment://") and the_input != result     # the recorded fact this guards (F5)

    conv = read_conversation(body, OUR_MESSAGE)
    assert [i.pointer for i in conv.images] == [result], "the input pointer was taken for a result"
    stream = read(lines_of("sse_edit"))
    assert [i.pointer for i in stream.images] == [result], "the input pointer was taken for a result"
    # a stream that echoes the user message (t5A does for text) carries the input pointer there: still not a result
    echo = copy.deepcopy({"v": {"message": user, "conversation_id": CID}})
    lines = lines_of("sse_edit")
    echoed = read(lines[:4] + delta(echo) + lines[4:])
    assert echoed.user_message == OUR_MESSAGE
    assert [i.pointer for i in echoed.images] == [result], "the input pointer was taken for a result"


@mutation(STREAM_FILE, COMPLETE_AND, TRUE_AND, expect="an unfinished stream was judged refused")
def test_text_without_end_is_open_not_refused():
    r = read(HEAD + refusal_text())                   # the stream broke: no message_stream_complete, no [DONE]
    assert r.assistant_text == "I can't help with that request." and not r.complete
    assert verdict(r) == "open", "an unfinished stream was judged refused"


def test_complete_text_without_image_is_refused():
    title = ['data: {"type":"title_generation","title":"Request Denied","conversation_id":"c-1"}', ""]
    r = read(HEAD + title + refusal_text() + DONE)
    assert r.complete and r.images == [] and r.title == "Request Denied"
    assert verdict(r) == "refused"
    # complete with neither image nor text is not a refusal either: nothing to judge on
    assert verdict(read(HEAD + delta(message("a-2", "assistant", [""], end_turn=True)) + DONE)) == "open"
    only_done = read(HEAD + refusal_text() + ["data: [DONE]"])
    assert only_done.complete and verdict(only_done) == "refused"


@mutation(STREAM_FILE, DONE_AND_FINISHED, DONE_ONLY, expect="a [DONE] without a finished answer was judged refused")
def test_an_ended_stream_whose_answer_never_finished_is_open():
    unfinished = (delta(message("a-1", "assistant", [""], status="in_progress"))
                  + delta({"p": "/message/content/parts/0", "o": "append", "v": "Let me draw that"}))
    r = read(HEAD + unfinished + DONE)
    assert not r.complete and verdict(r) == "open", "a [DONE] without a finished answer was judged refused"
    # the conversation read judges the same way: complete only with an assistant message that ended its turn
    body = {"conversation_id": "c-1", "mapping": {"a-1": {"message": {
        "id": "a-1", "author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["x"]},
        "status": "in_progress", "end_turn": None}}}}
    assert verdict(read_conversation(body, "u-1")) == "open"


def test_moderation_blocked_is_refused():
    blocked = ['data: {"type":"moderation","conversation_id":"c-1","moderation_response":{"blocked":true}}', ""]
    r = read(HEAD + blocked)
    assert r.blocked and not r.complete and verdict(r) == "refused"
    flagged = ['data: {"type":"moderation","moderation_response":{"blocked":false,"flagged":true}}', ""]
    assert verdict(read(HEAD + flagged)) == "open"
    body = copy.deepcopy(fixture("conv_t2i")["body"])
    assert verdict(read_conversation(body, "x")) == "image"
    for node in list(body["mapping"].values()):
        if (node.get("message") or {}).get("author", {}).get("role") == "tool":
            node["message"] = None
    body["moderation_results"] = [{"blocked": True}]
    assert verdict(read_conversation(body, "x")) == "refused"


def test_tool_invoked_false_with_an_image_is_an_image():
    lines = lines_of("sse_edit")
    assert any('"tool_invoked":false' in line for line in lines)          # recorded: t3 made an image with false
    assert verdict(read(lines)) == "image"
    no_image = HEAD + ['data: {"type":"server_ste_metadata","metadata":{"tool_invoked":true}}', ""] + DONE
    assert verdict(read(no_image)) == "open"


def test_conversation_is_named_by_the_first_event():
    reader = Reader()
    assert reader.conversation_named() is None
    for line in lines_of("sse_edit")[:4]:
        reader.feed(line)
    assert reader.conversation_named() == CID
    late = Reader()
    for line in ["event: delta_encoding", 'data: "v1"', ""] + delta(message("u", "assistant", ["x"])):
        late.feed(line)
    assert late.conversation_named() == "c-1"              # a delta's own conversation_id names it too


def test_read_conversation_matches_only_our_message_id():
    body = fixture("conv_edit")["body"]
    ours = read_conversation(body, OUR_MESSAGE)
    assert ours.user_message == OUR_MESSAGE and ours.conversation == CID
    assert ours.complete and ours.title == "Edit Image Weather" and verdict(ours) == "image"
    other = read_conversation(body, "7817cd23-0a75-416c-83e4-503580afae04")   # an assistant id, not a user one
    assert other.user_message is None
    assert read_conversation(body, "not-there").user_message is None
    t2i = read_conversation(fixture("conv_t2i")["body"], "1b4326f5-8fe9-409d-8f62-4906eb968b28")
    assert [(i.file_id, i.width) for i in t2i.images] == [("file_0000000087cc820797dbca5224902b9c", 1254)]
    # an image whose tool message has not finished is not a result yet
    body2 = copy.deepcopy(body)
    body2["mapping"]["e6d0bd2a-7c3f-485d-840a-67d4dbe8a7c5"]["message"]["status"] = "in_progress"
    assert read_conversation(body2, OUR_MESSAGE).images == []


def test_unknown_event_types_and_bad_json_lines_are_skipped():
    noise = [": ping - 2026-11-04", "event: something_new", "data: {not json", 'data: {"type":"new_thing","x":1}',
             "data: 42", 'data: ["a"]', 'data: {"p":"/message/content/parts/0","o":"append","v":"orphan"}',
             'data: {"p":"/nowhere/else","o":"replace","v":1}', "id: 7", "", "garbage"]
    lines = lines_of("sse_t2i_16x9")
    r = read(noise + lines[:6] + noise + lines[6:] + noise)
    assert [i.width for i in r.images] == [1672] and r.complete and verdict(r) == "image"
    assert read(noise).conversation is None and verdict(read(noise)) == "open"
