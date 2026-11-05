"""meta's gateway wire: the frame codec, a small protobuf codec, the `Request` builder, the `Response` reader and the
link rules that submit, reconcile and poll share.

Pure: no I/O, no curl_cffi. Facts from the 05/11 HTTP spike (plans/meta-http-spike-261105/dgw.py) and the fixtures in
fixtures/meta/http-261105/.

A frame is type (1 byte), stream (LE16), payload length (LE24), payload. The client's one data frame is `00 80` and the
JSON `{"req-id", "payload": base64(Request)}`; the gateway's data frames are a 2-byte LE seq and a protobuf `Response`.
"""

import base64
import heapq
import json
import os
import re
import struct
from collections.abc import Callable, Iterator, Sequence
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from relay_core.net import host_allowed
from relay_core.providers import Output
from relay_meta.constants import (
    FINAL_CHUNK_FIELD,
    FINAL_IMAGE_MARK,
    FINAL_PATH,
    INPUT_MARK,
    MEDIA_HOSTS,
    PREVIEW_MARK,
    PRODUCT_CONTEXT,
    TEXT_PATH,
    TOOL_RESULT_PATHS,
    UNSETTLED_LEAVES,
)

KEEPALIVE, ACK, DATA, EOD, ESTAB = 0x0a, 0x0c, 0x0d, 0x0e, 0x0f


class FrameError(ValueError):
    """A frame, or a protobuf inside one, that does not decode."""


# ---- frames -------------------------------------------------------------------------------------------------------
def frame(t: int, stream: int, payload: bytes) -> bytes:
    return bytes([t]) + struct.pack("<H", stream) + len(payload).to_bytes(3, "little") + payload


def parse_frame(raw: bytes) -> tuple[int, int, bytes]:
    """-> (type, stream, payload). A 1-byte frame (the keepalive) is `(t, 0, b"")`."""
    if len(raw) == 1:
        return raw[0], 0, b""
    if len(raw) < 6 or int.from_bytes(raw[3:6], "little") != len(raw) - 6:
        raise FrameError("frame of %d bytes with a length that does not match" % len(raw))
    return raw[0], struct.unpack_from("<H", raw, 1)[0], raw[6:]


# ---- protobuf -----------------------------------------------------------------------------------------------------
def _varint(b: bytes, i: int) -> tuple[int, int]:
    x = s = 0
    while True:
        if i >= len(b) or s > 63:
            raise FrameError("bad varint")
        c = b[i]
        i += 1
        x |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return x, i


def _enc_varint(x: int) -> bytes:
    if x < 0:
        raise ValueError("negative varint")
    out = bytearray()
    while True:
        c, x = x & 0x7F, x >> 7
        out.append(c | (0x80 if x else 0))
        if not x:
            return bytes(out)


def enc(fields) -> bytes:
    """[(field, value)] -> protobuf: an int is a varint, a float a fixed32, bytes or str length-delimited, a list a
    nested message."""
    out = bytearray()
    for f, v in fields:
        if isinstance(v, int):
            out += _enc_varint(f << 3) + _enc_varint(int(v))
        elif isinstance(v, float):
            out += _enc_varint(f << 3 | 5) + struct.pack("<f", v)
        else:
            v = enc(v) if isinstance(v, list) else v.encode() if isinstance(v, str) else bytes(v)
            out += _enc_varint(f << 3 | 2) + _enc_varint(len(v)) + v
    return bytes(out)


def _texty(v: bytes) -> bool:
    try:
        s = v.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return sum(c.isprintable() for c in s) >= 0.95 * len(s)


def dec(b: bytes, depth: int = 6) -> list[tuple[int, int, object]]:
    """protobuf -> [(field, wire type, value)]. A varint is an int; a fixed32/64 its raw bytes; a length-delimited value
    is a nested list when `depth` allows, it is not text and it parses in full, else its bytes. Any other wire type, or
    a value cut short, raises `FrameError`."""
    out, i = [], 0
    while i < len(b):
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if f == 0:
            raise FrameError("field 0")
        if w == 0:
            v, i = _varint(b, i)
        elif w in (1, 5):
            n = 8 if w == 1 else 4
            v, i = b[i:i + n], i + n
            if len(v) != n:
                raise FrameError("short fixed value")
        elif w == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
            if len(v) != n:
                raise FrameError("short length-delimited value")
            if depth > 0 and v and not _texty(v):
                try:
                    v = dec(v, depth - 1)
                except FrameError:
                    pass
        else:
            raise FrameError("wire type %d" % w)
        out.append((f, w, v))
    return out


def values(b: bytes, path: Sequence[int]) -> Iterator[object]:
    """Every value at `path` in the message `b`, each step on the way read as a message (a step that is not one raises
    `FrameError`)."""
    for f, w, v in dec(b, 0):
        if f != path[0]:
            continue
        if len(path) == 1:
            yield v
        elif w == 2:
            yield from values(v, path[1:])


# ---- sending ------------------------------------------------------------------------------------------------------
def estab(conversation: str) -> bytes:
    """The EstabStream frame that opens the job's stream on its conversation."""
    head = {"x-dgw-app-x-ecto-conversation-id": conversation, "x-dgw-app-client-payload-type": "PROTO_INSIDE_JSON"}
    return frame(ESTAB, 0, json.dumps(head, separators=(",", ":")).encode())


def estab_answer(payload: bytes) -> Literal["ok", "unauthorized", "other"]:
    try:
        code = json.loads(payload).get("code")
    except (ValueError, AttributeError):
        return "other"
    return "ok" if code == 200 else "unauthorized" if code == 401 else "other"


def otid(now_ms: int, rand22: int) -> int:
    return (now_ms << 22) | rand22


class Attachment(NamedTuple):
    media_id: str
    mime: str
    name: str


def _path(p: str) -> tuple[int, ...]:
    return tuple(int(x) for x in p.split("."))


def _tree(rows) -> list:
    """Ordered (dotted path, value) leaves -> nested [field, value] lists. Each step goes into the last entry of its
    field; a leaf whose field that entry already holds starts a new entry of its parent (the repeated 1.18 pairs)."""
    root: list = []
    for dotted, value in rows:
        path, node, parent = _path(dotted), root, None
        for f in path[:-1]:
            last = next((c for c in reversed(node) if c[0] == f), None)
            if last is None:
                last = [f, []]
                node.append(last)
            parent, node = (node, f), last[1]
        if any(c[0] == path[-1] for c in node):
            node = []
            parent[0].append([parent[1], node])
        node.append([path[-1], value])
    return root


def build_request(*, text: str, conversation: str, viewer: str, attachments: Sequence[Attachment], now_ms: int,
                  ids: Callable[[], str], rand22: int) -> bytes:
    """The `Request` protobuf of one send: the browser's fixed leaves (`PRODUCT_CONTEXT`, `UNSETTLED_LEAVES`) and this
    job's conversation, viewer, times, ids and text, in the browser's order; the attachments after the text."""
    vid = int(viewer)
    own = (("1.1.5.5.1", conversation), ("1.2.1", vid), ("1.2.2", vid), ("1.5.1", now_ms), ("1.5.3", now_ms),
           ("1.6", ids()), ("1.10.1", ids()), ("1.10.3", ids()), ("1.10.4", conversation), ("2.1.1", ids()),
           ("2.1.2.1", conversation), ("2.1.2.2", now_ms), ("2.1.2.3", otid(now_ms, rand22)), ("2.2", text))
    tree = _tree(heapq.merge(PRODUCT_CONTEXT, UNSETTLED_LEAVES, own, key=lambda r: _path(r[0])))
    prompt = next(c[1] for c in tree if c[0] == 2)
    at = next(i for i, c in enumerate(prompt) if c[0] == 2) + 1
    prompt[at:at] = [[3, [(1, [(1, int(a.media_id))]), (2, 1), (3, b""), (6, a.mime), (7, a.name)]]
                     for a in attachments]
    return enc(tree)


def send_frame(request_id: str, request: bytes) -> bytes:
    """The one data frame of a job."""
    body = {"req-id": request_id, "payload": base64.b64encode(request).decode()}
    return frame(DATA, 0, b"\x00\x80" + json.dumps(body, separators=(",", ":")).encode())


# ---- link rules ---------------------------------------------------------------------------------------------------
LinkClass = Literal["image", "video", "preview", "input", "other"]
_MIMES = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_LINK = re.compile(rb"https://[A-Za-z0-9.-]+/[A-Za-z0-9._~%/-]+")


def link_class(url: str) -> LinkClass:
    """What an fbcdn link is, by its path class. Anything not https on an fbcdn host is "other"."""
    if not host_allowed(url, MEDIA_HOSTS, default_port_only=True):
        return "other"
    path = urlsplit(url).path
    if PREVIEW_MARK in path:
        return "preview"
    if INPUT_MARK in path:
        return "input"
    if FINAL_IMAGE_MARK in path:
        return "video" if path.lower().endswith(".mp4") else "image"
    return "other"


def is_final(url: str, kind: str) -> bool:
    return link_class(url) == kind


def media_outputs(urls, conversation: str, kind: str = "image", n: int | None = None) -> tuple[Output, ...]:
    """The first `n` results (what the job asked and paid for: meta may draw more than asked), in page order; every
    result for a job with no `n` (a video, or an image job queued before n existed)."""
    def mime(url: str) -> str:
        if kind == "video":
            return "video/mp4"
        return _MIMES.get(os.path.splitext(urlsplit(url).path)[1].lower(), "image/webp")
    return tuple(Output(url, mime(url), i, meta={"conversation_id": conversation})
                 for i, url in enumerate(tuple(urls)[:n]))


# ---- reading ------------------------------------------------------------------------------------------------------
class Answer(NamedTuple):
    done: bool
    finals: tuple[str, ...]
    previews: int           # distinct previews seen less the finals: previews whose final never came
    other: int              # finals of the other kind
    error: dict | None      # the tool result's structured error
    text: str


def _str(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else ""


def _tool_error(v) -> dict | None:
    try:
        d = json.loads(_str(v))
    except ValueError:
        return None
    if isinstance(d, dict) and ("failure_kind" in d or "error_code" in d):
        return d
    return None


class Reader:
    """The gateway's frames of one job, fed as they come; `answer(kind)` reads what they hold so far."""

    def __init__(self):
        self.frames: list[bytes] = []
        self._finals: list[str] = []
        self._previews: set[str] = set()
        self._error: dict | None = None
        self._text = ""
        self._done = False

    def feed(self, raw: bytes) -> None:
        self.frames.append(raw)
        t, _, payload = parse_frame(raw)
        if t != DATA:
            return
        if len(payload) < 2:
            raise FrameError("data frame with no seq")
        body = payload[2:]
        finals = [_str(v).replace("&amp;", "&") for v in values(body, FINAL_PATH)]
        errors = [e for p in TOOL_RESULT_PATHS for e in map(_tool_error, values(body, p)) if e]
        texts = [_str(v) for v in values(body, TEXT_PATH)]
        done = any(f == FINAL_CHUNK_FIELD and w == 0 and v == 1 for f, w, v in dec(body, 0))
        previews = {urlsplit(u).path for u in (m.decode() for m in _LINK.findall(body))
                    if link_class(u) == "preview"}
        self._finals += finals
        self._previews |= previews
        if self._error is None and errors:
            self._error = errors[0]
        if texts:
            self._text = texts[-1]
        self._done = self._done or done

    def answer(self, kind: str) -> Answer:
        seen: set[str] = set()
        finals: list[str] = []
        other: set[str] = set()
        for url in self._finals:
            cls, key = link_class(url), urlsplit(url).path
            if cls == kind and key not in seen:
                seen.add(key)
                finals.append(url)
            elif cls in ("image", "video") and cls != kind:
                other.add(key)
        return Answer(self._done, tuple(finals), max(0, len(self._previews) - len(finals)), len(other),
                      self._error, self._text)


# ---- evidence -----------------------------------------------------------------------------------------------------
_UUID = re.compile(r"[0-9a-z]{8}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{12}", re.I)
_URL = re.compile(r"https?://[^\s\"'<>]+")


def _leaves(tree, path=()) -> Iterator[str]:
    for f, _, v in tree:
        if isinstance(v, list):
            yield from _leaves(v, path + (f,))
        else:
            yield ".".join(map(str, path + (f,)))


def summary(frames: Sequence[bytes]) -> bytes:
    """The frames as evidence: type, seq, leaf paths, the answer text and the link hosts. No value but the answer text
    (its links cut to their host, its uuids dropped), so no token, viewer, query or id."""
    out = []
    for raw in frames:
        try:
            t, _, payload = parse_frame(raw)
        except FrameError:
            out.append({"t": raw[0] if raw else None, "bytes": len(raw)})
            continue
        row = {"t": t, "seq": None, "paths": [], "text": None,
               "hosts": sorted({urlsplit(m.decode()).hostname or "" for m in _LINK.findall(payload)})}
        if t == DATA and len(payload) >= 2:
            row["seq"] = int.from_bytes(payload[:2], "little")
            try:
                row["paths"] = list(dict.fromkeys(_leaves(dec(payload[2:]))))
                texts = [_str(v) for v in values(payload[2:], TEXT_PATH)]
            except FrameError:
                row["paths"], texts = [], []
                row["bytes"] = len(payload)
            if texts:
                text = _URL.sub(lambda m: "<%s>" % (urlsplit(m.group(0)).hostname or "link"), texts[-1])
                row["text"] = _UUID.sub("<id>", text)
        out.append(row)
    return json.dumps(out).encode()
