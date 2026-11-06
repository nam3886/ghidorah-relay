"""meta's gateway wire: frames, the Request builder, the Response reader and the link rules, on the 05/11 captures."""

import base64
import json
import re
import struct
from pathlib import Path

import pytest

from relay_core.testing.mutation import mutation
from relay_meta.constants import FINAL_PATH, TIME_ZONE
from relay_meta.engine.wire import (
    DATA,
    ESTAB,
    Attachment,
    FrameError,
    Reader,
    build_request,
    dec,
    enc,
    estab,
    estab_answer,
    estab_code,
    frame,
    is_final,
    link_class,
    media_outputs,
    otid,
    parse_frame,
    send_frame,
    summary,
    values,
)

WIRE = "packages/provider-meta/src/relay_meta/engine/wire.py"
CONSTANTS = "packages/provider-meta/src/relay_meta/constants.py"
FIX = Path(__file__).resolve().parents[3] / "fixtures" / "meta" / "http-261105"
SKELETON = json.loads((FIX / "request_skeleton.json").read_text())
CID = "0e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b"
VIEWER = "1234567890123456"
NOW = 1762336800000
CDN = "https://scontent.xx.fbcdn.net"


def captured(name: str, sent: bool = False) -> list[bytes]:
    """The gateway's frames of a capture in order (and the rebuilt sent ones with `sent`)."""
    rows = json.loads((FIX / ("frames_%s.json" % name)).read_text())
    return [base64.b64decode(r["b64"]) for r in rows if sent or "dir" not in r]


def read(raws) -> Reader:
    r = Reader()
    for raw in raws:
        r.feed(raw)
    return r


def response(fields) -> bytes:
    """A gateway data frame (seq 0) holding the protobuf `fields`."""
    return frame(DATA, 0, b"\x00\x00" + enc(fields))


def at(path, value) -> list:
    """`value` nested at `path`, as `enc` fields."""
    for f in reversed(path[1:]):
        value = [(f, value)]
    return [(path[0], value)]


def leaves(b: bytes, messages: set[str], path: tuple = ()):
    """(dotted path, wire, value) of every leaf, descending only into the paths in `messages` (some messages here
    read as text)."""
    for f, w, v in dec(b, 0):
        p = path + (f,)
        dotted = ".".join(map(str, p))
        if w == 2 and dotted in messages:
            yield from leaves(v, messages, p)
        else:
            yield dotted, w, v


SKELETON_MESSAGES = {".".join(r["path"].split(".")[:i]) for r in SKELETON for i in range(1, r["path"].count(".") + 1)}


def build(attachments=(), text="a red fox in the snow"):
    ids = iter("%08d-0000-4000-8000-000000000000" % i for i in range(10))
    return build_request(text=text, conversation=CID, viewer=VIEWER, attachments=attachments, now_ms=NOW,
                         ids=lambda: next(ids), rand22=12345)


# ---- frames -------------------------------------------------------------------------------------------------------
def test_a_frame_round_trips_and_a_short_or_long_length_is_refused():
    raw = frame(DATA, 3, b"abc")
    assert raw[:6] == b"\x0d\x03\x00\x03\x00\x00"
    assert parse_frame(raw) == (DATA, 3, b"abc")
    assert parse_frame(b"\x0a") == (0x0a, 0, b"")
    for bad in (raw + b"x", raw[:-1], b"", b"\x0d\x00\x00"):
        with pytest.raises(FrameError):
            parse_frame(bad)
    for raw in captured("t2i_n2", sent=True):           # every captured frame parses
        parse_frame(raw)


# ---- sending ------------------------------------------------------------------------------------------------------
@mutation(WIRE, '("1.2.2", vid)', '("1.2.2", 1)', expect="the viewer id twice")
def test_the_built_request_has_exactly_the_captured_browser_leaves():
    got = list(leaves(build(), SKELETON_MESSAGES))
    assert [(p, w) for p, w, _ in got] == [(r["path"], r["wire"]) for r in SKELETON]
    for (path, _, value), row in zip(got, SKELETON, strict=True):
        if row["kind"] == "const":
            want = row["value"].encode() if isinstance(row["value"], str) else row["value"]
            assert value == want, path
    by = {}
    for path, _, value in got:
        by.setdefault(path, []).append(value)
    assert by["1.2.1"] == by["1.2.2"] == [int(VIEWER)], "the viewer id twice"
    assert by["2.2"] == [b"a red fox in the snow"]
    assert by["1.1.5.5.1"] == by["2.1.2.1"] == by["1.10.4"] == [CID.encode()]
    assert by["2.1.2.3"] == [otid(NOW, 12345)] == [(NOW << 22) | 12345]
    assert by["2.1.5"] == [1]
    assert by["1.15.4"] == [TIME_ZONE.encode()] != [b"Asia/Saigon"]
    assert by["1.1.19.2"] == [struct.pack("<f", 1.0)]
    assert by["1.20"] == [b"\x03\x06"]
    assert by["1.5.1"] == by["1.5.3"] == by["2.1.2.2"] == [NOW]
    assert len({by[p][0] for p in ("1.6", "1.10.1", "1.10.3", "2.1.1")}) == 4   # a fresh id each


@mutation(WIRE, '(2, 1), (3, b"")', '(2, 2), (3, b"")', expect="an image attachment")
def test_attachments_go_in_order_after_the_text_with_their_media_ids():
    atts = (Attachment("1111111111111111", "image/jpeg", "a.jpg"), Attachment("2222222222222222", "image/png", "b.png"),
            Attachment("3333333333333333", "image/webp", "c.webp"))
    prompt = next(v for f, w, v in dec(build(atts), 0) if f == 2)
    fields = [f for f, _, _ in dec(prompt, 0)]
    assert fields == [1, 2, 3, 3, 3, 4]
    entries = [v for f, _, v in dec(prompt, 0) if f == 3]
    for raw, att in zip(entries, atts, strict=True):
        got = {f: v for f, _, v in dec(raw, 0)}
        assert list(values(raw, (1, 1))) == [int(att.media_id)]
        assert got[2] == 1 and got[3] == b"" and got[6] == att.mime.encode() and got[7] == att.name.encode(), \
            "an image attachment"


def test_the_send_frame_is_one_data_frame_with_the_00_80_prefix_and_base64_request():
    req = build()
    t, stream, payload = parse_frame(send_frame("rid-1", req))
    assert (t, stream) == (DATA, 0) and payload[:2] == b"\x00\x80"
    body = json.loads(payload[2:])
    assert list(body) == ["req-id", "payload"] and body["req-id"] == "rid-1"
    assert base64.b64decode(body["payload"]) == req
    captured_send = next(raw for raw in captured("t2i_n1", sent=True) if raw[0] == DATA)
    captured_payload = parse_frame(captured_send)[2]
    assert captured_payload[:2] == b"\x00\x80" and list(json.loads(captured_payload[2:])) == list(body)


def test_estab_answers_200_ok_401_unauthorized_anything_else_other():
    t, stream, payload = parse_frame(estab(CID))
    assert (t, stream) == (ESTAB, 0)
    assert json.loads(payload) == {"x-dgw-app-x-ecto-conversation-id": CID,
                                   "x-dgw-app-client-payload-type": "PROTO_INSIDE_JSON"}
    sent = json.loads(parse_frame(captured("t2i_n1", sent=True)[0])[2])
    assert list(sent) == list(json.loads(payload))
    answer = next(parse_frame(raw)[2] for raw in captured("t2i_n1") if raw[0] == ESTAB)
    assert estab_answer(answer) == "ok"
    assert estab_answer(b'{"code":401}') == "unauthorized"
    for other in (b'{"code":500}', b"nope", b"[]", b""):
        assert estab_answer(other) == "other"
    # the meaning is the submit path's, as before the cause was read: a 200 that JSON reads as a float is still ok
    assert estab_answer(b'{"code":200.0}') == "ok" and estab_answer(b'{"code":401.0}') == "unauthorized"
    assert estab_answer(b'{"code":"200"}') == "other"


def test_estab_code_is_the_integer_code_or_none_never_the_text():
    assert estab_code(b'{"code":403,"msg":"x"}') == 403 and estab_code(b'{"code":200}') == 200
    for none in (b'{"code":"403 x"}', b'{"code":true}', b'{"code":403.5}', b'{"msg":"x"}', b"[]", b"nope", b""):
        assert estab_code(none) is None, none


# ---- reading ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,n", [("t2i_n1", 1), ("t2i_n2", 2), ("t2i_n4", 4)])
def test_the_captured_text_to_image_answers_read_done_with_n_finals_and_no_previews_left(name, n):
    a = read(captured(name)).answer("image")
    assert a.done and len(a.finals) == n and a.previews == 0 and a.other == 0 and a.error is None
    assert all(is_final(u, "image") and "&amp;" not in u for u in a.finals)
    outs = media_outputs(a.finals, CID, "image", n)
    assert [(o.ref, o.mime, o.idx) for o in outs] == [(u, "image/webp", i) for i, u in enumerate(a.finals)]


@mutation(WIRE, "cls, key = link_class(url), urlsplit(url).path", "cls, key = link_class(url), url",
          expect="one result")
def test_a_final_link_repeated_in_later_frames_is_one_result():
    r = read(captured("t2i_n1"))
    first = r.answer("image").finals
    repeats = sum(1 for raw in captured("t2i_n1") if raw[0] == DATA
                  for _ in values(parse_frame(raw)[2][2:], FINAL_PATH))
    assert repeats > 1                                  # meta repeats the final link in later frames
    url = first[0].split("?")[0]
    r.feed(response(at(FINAL_PATH, url + "?_nc_ohc=other&amp;oh=2")))
    assert r.answer("image").finals == first, "one result"


def test_the_captured_videos_read_as_one_mp4_and_an_image_in_a_video_job_is_other():
    for name in ("t2v", "i2v"):
        a = read(captured(name)).answer("video")
        assert a.done and len(a.finals) == 1 and a.previews == 0 and a.other == 0 and a.error is None
        assert a.finals[0].split("?")[0].endswith(".mp4")
    a = read(captured("t2i_n1")).answer("video")
    assert a.finals == () and a.other == 1


def test_the_edit_reads_one_final_image():
    a = read(captured("edit")).answer("image")
    assert a.done and len(a.finals) == 1 and a.error is None


def test_the_captured_refusal_reads_done_with_no_media_and_the_policy_error():
    a = read(captured("refusal")).answer("image")
    assert a.done and a.finals == () and a.previews == 0
    assert a.error["failure_kind"] == "policy_denied" and a.error["error_code"] == "integrity_check_failed"
    assert a.text.startswith("I wasn't able")


@mutation(WIRE, 'if isinstance(d, dict) and ("failure_kind" in d or "error_code" in d):', "if isinstance(d, dict):",
          expect="not an error")
def test_a_success_status_at_the_tool_result_path_is_not_an_error():
    ok = json.dumps({"file_path": "/mnt/data/gallery/x.webp", "status": "Generated 1 image(s)"})
    r = read([response(at((1, 4, 6, 4), ok)), response(at((1, 2, 4), ok))])
    assert r.answer("image").error is None, "not an error"
    r.feed(response(at((1, 2, 4), json.dumps({"error_code": "x"}))))
    assert r.answer("image").error == {"error_code": "x"}


@mutation(WIRE, "max(0, len(self._previews) - len(finals))", "len(self._previews)", expect="previews left")
def test_previews_without_their_finals_are_counted():
    raws = captured("t2i_n2")
    cut = next(i for i, raw in enumerate(raws) if raw[0] == DATA and any(values(parse_frame(raw)[2][2:], FINAL_PATH)))
    r = read(raws[:cut])
    a = r.answer("image")
    assert not a.done and a.finals == () and a.previews == 2
    first = read(raws).answer("image").finals[0]
    r.feed(response(at(FINAL_PATH, first)))
    a = r.answer("image")
    assert len(a.finals) == 1 and a.previews == 1, "previews left"
    assert read(raws).answer("image").previews == 0


@mutation(CONSTANTS, "FINAL_CHUNK_FIELD = 3", "FINAL_CHUNK_FIELD = 2", expect="done is the final chunk only")
def test_done_is_the_final_chunk_only():
    raws = captured("t2v")
    last = max(i for i, raw in enumerate(raws) if raw[0] == DATA)
    assert not read(raws[:last]).answer("video").done, "done is the final chunk only"
    assert read(raws).answer("video").done, "done is the final chunk only"


def test_a_data_frame_that_does_not_decode_raises_frame_error():
    for payload in (b"\x00\x00\x0b\x01", b"\x00\x00\x0a\x05ab", b"\x00\x00\x08", b"\x00"):
        with pytest.raises(FrameError):
            Reader().feed(frame(DATA, 0, payload))
    with pytest.raises(FrameError):                     # a step on the answer's path that is not a message
        Reader().feed(response([(1, [(4, b"\x0b\x01")])]))
    Reader().feed(frame(0x0c, 0, b"\x00\x00"))           # an ack is no data


@mutation(WIRE, "    if not host_allowed(url, MEDIA_HOSTS, default_port_only=True):", "    if False:",
          expect="only the media hosts")
def test_link_classes():
    assert link_class(CDN + "/v/t39.105495-1/1_2_3_n.webp?_nc_ht=x&oh=y") == "image"
    assert link_class(CDN + "/v/t39.105495-1/1_2_3_n.mp4?_nc_ht=x") == "video"
    assert link_class(CDN + "/v/t58.90637-1/1_2_3_n.webp?x=1") == "preview"
    assert link_class(CDN + "/v/t58.90637-1/1_2_3_n.mp4") == "preview"
    assert link_class(CDN + "/m1/v/t0.71334-6/abc") == "input"
    assert link_class(CDN + "/v/t39.30808-6/1_2_3_n.jpg") == "other"            # the avatar
    assert link_class("https://evil.example/v/t39.105495-1/1_2_3_n.webp") == "other", "only the media hosts"
    assert link_class("https://scontent.fbcdn.net.evil.example/v/t39.105495-1/1_n.webp") == "other", \
        "only the media hosts"
    assert link_class("http://scontent.xx.fbcdn.net/v/t39.105495-1/1_2_3_n.webp") == "other", "only the media hosts"
    final = CDN + "/v/t39.105495-1/1_n.webp"
    assert is_final(final, "image") and not is_final(final, "video")


# ---- evidence -----------------------------------------------------------------------------------------------------
def test_the_summary_holds_no_token_viewer_query_or_uuid():
    uuid = "12345678-aaaa-4bbb-8ccc-1234567890ab"
    said = response(at((1, 4, 2), "Done: %s/v/t39.105495-1/1_n.webp?_nc_ohc=SECRETQ for %s" % (CDN, uuid)))
    raws = captured("i2v", sent=True) + [send_frame(uuid, build()), estab(CID), said, b"\x0d\x00"]
    text = summary(raws).decode()
    rows = json.loads(text)
    assert len(rows) == len(raws)
    for secret in (VIEWER, CID, uuid, "SECRETQ", "_nc_ohc", "ecto1:"):
        assert secret not in text
    assert not re.search(r"[0-9a-z]{8}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{12}", text)
    assert rows[-2]["text"] == "Done: <scontent.xx.fbcdn.net> for <id>"
    assert "scontent.xx.fbcdn.net" in rows[-2]["hosts"]
    assert any("1.4.6.2.3.1" in row.get("paths", []) for row in rows)
