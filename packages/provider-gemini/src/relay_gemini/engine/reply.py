"""Reading gemini's answers: the StreamGenerate stream of a send and the batchexecute reads. Pure (no I/O).

Both answers are `)]}'` then length lines and JSON lines; every `["wrb.fr", <rpc id or null>, "<payload json>"]` item
carries one payload. A send streams several payloads (FINDINGS 2): the first `[null, [cid, rid], {...}]` names the
conversation, the next ones repeat it with the candidate at `[4][0]`: `[rcid, [text], ..., [12]: the generated media]`.
A drawn image is a record `[null, 1, <file name>, "https://lh3.googleusercontent.com/gg-dl/...", null, <token>, ...,
"image/png", ..., [width, height, bytes]]` under the candidate's `[12]` (a chat read carries the same record with a
`/gg/` link). The record is found by its shape anywhere in the payload, not by a fixed path (the paths drift).

A video send answers only that it was taken: a payload `{"7": ["I'm generating your video...", ["video_generation",
...]]}` and no candidate (FINDINGS 7). The video shows up later in the chat read, a record `[null, 2, "video.mp4", null,
null, <token>, null, [<lh3 /gg/ link>, <contribution link>, <poster link>], ..., "video/mp4", ..., [[secs, nanos],
width, height], ..., bytes]` under the candidate.
"""

import json
import re
from dataclasses import dataclass

from relay_gemini.constants import GENERATING_MARK

_CID = re.compile(r"c_[0-9a-f]{8,}")
_MODE = re.compile(r"[0-9a-f]{16}")
LINK_PREFIX = "https://lh3.googleusercontent.com/gg"     # /gg-dl/ (a send's answer) or /gg/ (a chat read)


@dataclass(frozen=True)
class Image:
    link: str
    mime: str
    name: str | None
    width: int | None
    height: int | None
    size: int | None


@dataclass(frozen=True)
class Video:
    link: str
    mime: str
    name: str | None
    width: int | None
    height: int | None
    duration_s: float | None
    size: int | None


@dataclass(frozen=True)
class Answer:
    """What one answer said. `clean`: every line was the guard, a length or a JSON frame (nothing cut or unreadable).
    `cid`: the conversation it names (None: none). `texts`: every candidate text, in order (the last is the final
    one). `images`, `videos`: the media of the LAST payload that has a candidate (each payload repeats the whole answer
    so far). `generating`: some payload carries the "generating your video" item. `errors`: error items (`["er", ...]`)
    or error codes a payload carried."""
    clean: bool
    ended: bool
    payloads: tuple
    cid: str | None
    texts: tuple[str, ...]
    images: tuple[Image, ...]
    errors: tuple
    videos: tuple[Video, ...] = ()
    generating: bool = False


def _items(text: str) -> tuple[list, list, bool, bool]:
    """(payloads, errors, clean, ended): `ended` when the LAST frame is the end item `["e", ...]` every complete answer
    closes with (measured on every send and read, 23/10)."""
    payloads, errors, clean, ended = [], [], True, False
    for line in text.splitlines():
        line = line.strip()
        if not line or line == ")]}'" or line.isdigit():
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            clean = False
            continue
        if not isinstance(frame, list):
            clean = False
            continue
        ended = bool(frame) and all(isinstance(i, list) and i and i[0] == "e" for i in frame)
        for item in frame:
            if not isinstance(item, list) or not item:
                continue
            if item[0] == "wrb.fr" and len(item) > 2:
                if isinstance(item[2], str):
                    try:
                        payloads.append(json.loads(item[2]))
                    except ValueError:
                        clean = False
                if len(item) > 5 and item[5] is not None:
                    errors.append(item[5])
            elif item[0] == "er":
                errors.append(item)
    return payloads, errors, clean, ended


def _walk(node):
    yield node
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        for x in node.values():
            yield from _walk(x)


def image_of(record) -> Image | None:
    """One image record, by its shape: a list whose [3] is an lh3 `/gg` link, with an `image/...` type and a final
    `[width, height, bytes]`."""
    if not isinstance(record, list) or len(record) < 4 or not isinstance(record[3], str) \
            or not record[3].startswith(LINK_PREFIX):
        return None
    mime = next((x for x in record if isinstance(x, str) and x.startswith("image/")), None)
    if mime is None:
        return None
    dims = record[-1] if isinstance(record[-1], list) and len(record[-1]) == 3 \
        and all(type(v) is int for v in record[-1]) else [None, None, None]
    name = record[2] if isinstance(record[2], str) else None
    return Image(record[3], mime, name, *dims)


def images_in(node) -> tuple[Image, ...]:
    """Every distinct image record anywhere in `node`, in order (a link once)."""
    out, seen = [], set()
    for n in _walk(node):
        img = image_of(n)
        if img is not None and img.link not in seen:
            seen.add(img.link)
            out.append(img)
    return tuple(out)


def video_of(record) -> Video | None:
    """One video record, by its shape: a list with a `video/...` type whose [7] is a list of links led by an lh3 `/gg`
    link; its size `[[secs, nanos], width, height]` and its byte count (the last item) when they are there."""
    if not isinstance(record, list) or len(record) < 8 or not isinstance(record[7], list) or not record[7] \
            or not isinstance(record[7][0], str) or not record[7][0].startswith(LINK_PREFIX):
        return None
    mime = next((x for x in record if isinstance(x, str) and x.startswith("video/")), None)
    if mime is None:
        return None
    size = next((x for x in record if isinstance(x, list) and len(x) == 3 and isinstance(x[0], list)
                 and len(x[0]) == 2 and all(type(v) is int for v in (*x[0], x[1], x[2]))), None)
    width, height, duration = (size[1], size[2], size[0][0] + size[0][1] / 1e9) if size else (None, None, None)
    name = record[2] if isinstance(record[2], str) else None
    return Video(record[7][0], mime, name, width, height, duration, record[-1] if type(record[-1]) is int else None)


def videos_in(node) -> tuple[Video, ...]:
    """Every distinct video record anywhere in `node`, in order (a link once)."""
    out, seen = [], set()
    for n in _walk(node):
        vid = video_of(n)
        if vid is not None and vid.link not in seen:
            seen.add(vid.link)
            out.append(vid)
    return tuple(out)


def generating(node) -> bool:
    """Does `node` carry the "I'm generating your video" item (`["video_generation", ...]`)?"""
    return any(n == GENERATING_MARK for n in _walk(node))


GENERATING_FAILED = 4    # the item's status once gemini gave the video up (the upgrade answer, 23/10: 1, then 4)


def generating_status(node) -> int | None:
    """The status of the LAST "generating" item in `node` (`["video_generation", [...], status, ...]`, in answer
    order): 1 while it renders, `GENERATING_FAILED` once gemini gave it up; None: no item with a status. `node` may be
    `Answer.payloads`, a tuple (`_walk` descends lists and dicts only)."""
    last = None
    for n in _walk(list(node) if isinstance(node, tuple) else node):
        if isinstance(n, list) and len(n) > 2 and n[0] == GENERATING_MARK and type(n[2]) is int:
            last = n[2]
    return last


def _candidate_texts(node) -> list[str]:
    """The text of every candidate `[rcid, [text, ...], ...]` in `node`."""
    out = []
    for n in _walk(node):
        if isinstance(n, list) and len(n) > 1 and isinstance(n[0], str) and n[0].startswith("rc_") \
                and isinstance(n[1], list) and n[1] and isinstance(n[1][0], str):
            out.append(n[1][0])
    return out


def read_answer(text: str) -> Answer:
    payloads, errors, clean, ended = _items(text)
    cid = None
    for p in payloads:
        if isinstance(p, list) and len(p) > 1 and isinstance(p[1], list) and p[1] and isinstance(p[1][0], str) \
                and _CID.fullmatch(p[1][0]):
            cid = p[1][0]
            break
    last = None
    for p in payloads:
        if isinstance(p, list) and len(p) > 4 and isinstance(p[4], list) and p[4]:
            last = p
    texts = tuple(_candidate_texts(last)) if last is not None else ()
    return Answer(clean, ended, tuple(payloads), cid, texts, images_in(last) if last is not None else (),
                  tuple(errors), videos_in(last) if last is not None else (), generating(payloads))


# ---- the chat list and a chat read ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Chat:
    cid: str
    started: float           # unix s: the time its first send started (measured: 1 s after our POST)


def chats(text: str) -> tuple[list[Chat], bool]:
    """The chats of a MaZiqc answer `[null, null, [[cid, title, null, null, null, [secs, nanos], ...], ...]]`, and
    whether the answer was read (clean, one payload with a list of chats, possibly empty)."""
    payloads, _, clean, ended = _items(text)
    if not clean or not ended or len(payloads) != 1:
        return [], False
    p = payloads[0]
    rows = p[2] if isinstance(p, list) and len(p) > 2 else None
    if rows is None:
        return [], isinstance(p, list)          # no chat at all: an empty account answers a payload without the list
    if not isinstance(rows, list):
        return [], False
    out = []
    for row in rows:
        if isinstance(row, list) and len(row) > 5 and isinstance(row[0], str) and _CID.fullmatch(row[0]) \
                and isinstance(row[5], list) and len(row[5]) == 2 and all(type(v) is int for v in row[5]):
            out.append(Chat(row[0], row[5][0] + row[5][1] / 1e9))
    return out, True


@dataclass(frozen=True)
class Turn:
    """One user turn of a chat read (hNvQHb): what was sent, the answer's final text, images and videos, whether it is
    still "generating your video", and the error items the read carried (any one makes the read prove nothing). Only
    the answer (`turn[3]`) is searched: an input image sits in what was sent (`turn[2]`)."""
    prompt: str | None
    texts: tuple[str, ...]
    images: tuple[Image, ...]
    errors: tuple = ()
    videos: tuple[Video, ...] = ()
    generating: bool = False

    def answer(self, cid: str) -> Answer:
        """The turn as a complete answer, judged like a send's."""
        return Answer(True, True, (), cid, self.texts, self.images, self.errors, self.videos, self.generating)


def turns(text: str) -> tuple[list[Turn], bool]:
    """The turns of a chat read `[[[ [cid, rid], null, [[prompt], ...], [[candidate...]], ... ], ...]]`, and whether
    the read was clean, ended, with one payload."""
    payloads, errors, clean, ended = _items(text)
    if not clean or not ended or len(payloads) != 1 or not isinstance(payloads[0], list):
        return [], False
    rows = payloads[0][0] if payloads[0] and isinstance(payloads[0][0], list) else []
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 4:
            continue
        sent = row[2]
        prompt = sent[0][0] if isinstance(sent, list) and sent and isinstance(sent[0], list) and sent[0] \
            and isinstance(sent[0][0], str) else None
        out.append(Turn(prompt, tuple(_candidate_texts(row[3])), images_in(row[3]), tuple(errors),
                        videos_in(row[3]), generating(row[3])))
    return out, True


def modes(text: str) -> list[tuple[str, str | None]]:
    """The account's modes `(id, name, kind)` in the menu's order from an otAQ7b answer: payload[15] lists them `[id,
    name, ..., [17]: kind]`; the first is the default ("Auto" on a free plan, "Flash-Lite" on AI Pro, measured 24/10).
    The kind is the number a send names beside the mode id (header `x-goog-ext-525001261-jspb`[14]: Auto and
    Flash-Lite 6, Flash 1, Pro 3, Argon 8); None when the row has none. Empty when the list cannot be read or its first
    entry is not a mode."""
    payloads, _, clean, ended = _items(text)
    if not clean or not ended or len(payloads) != 1:
        return []
    p = payloads[0]
    rows = p[15] if isinstance(p, list) and len(p) > 15 and isinstance(p[15], list) else []
    out = []
    for r in rows:
        if not (isinstance(r, list) and r and isinstance(r[0], str) and _MODE.fullmatch(r[0])):
            if not out:
                return []                   # no default mode
            continue
        name = r[1] if len(r) > 1 and isinstance(r[1], str) else None
        kind = r[17] if len(r) > 17 and type(r[17]) is int else None
        out.append((r[0], name, kind))
    return out


def usage_of(text: str) -> tuple[int, list[tuple[int, int | None, int | None]]] | None:
    """(plan code, [(units left, reset epoch s, window code) per window]) from a jSf9Qc answer (FINDINGS 5):
    `[plan, [[left, used share, window, [[reset s, ns]]], ...], ...]`. None when it cannot be read."""
    payloads, _, clean, ended = _items(text)
    if not clean or not ended or len(payloads) != 1:
        return None
    p = payloads[0]
    if not (isinstance(p, list) and len(p) > 1 and type(p[0]) is int and isinstance(p[1], list)):
        return None
    windows = []
    for w in p[1]:
        if not (isinstance(w, list) and len(w) > 3 and type(w[0]) in (int, float)):
            return None
        reset = w[3][0][0] if isinstance(w[3], list) and w[3] and isinstance(w[3][0], list) and w[3][0] else None
        windows.append((int(w[0]), reset if type(reset) is int and reset > 0 else None,
                        w[2] if type(w[2]) is int else None))
    return p[0], windows


def same_text(a: str | None, b: str) -> bool:
    """The chat's prompt is the job's sent text, character for character (measured: gemini gives it back as sent).
    Never folded: two jobs whose texts differ only in white space are two jobs."""
    return a is not None and a == b
