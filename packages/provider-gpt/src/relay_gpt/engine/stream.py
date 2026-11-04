"""The conversation stream of the one send (`POST /backend-api/f/conversation`, v1 delta encoding) and the
conversation read that replaces it when the stream breaks (`GET /backend-api/conversation/{id}`), both turned into one
`Reading`, and the verdict on it. Pure: no I/O, no secret kept (the resume token is never read).

A result is only an image pointer in a finished `role:"tool"` message: the input image of an edit shows as a
`sediment://` pointer in the user message (FINDINGS F5) and is never one. A refusal is judged as "complete, no image,
assistant text present" or a blocked moderation; `tool_invoked` is never read (it is false on real images)."""

import json
from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["image", "refused", "open"]


@dataclass
class Image:
    pointer: str                 # sediment://file_…
    file_id: str                 # the part after ://
    width: int | None
    height: int | None


@dataclass
class Reading:
    conversation: str | None
    user_message: str | None
    images: list[Image] = field(default_factory=list)
    assistant_text: str = ""
    blocked: bool = False
    complete: bool = False
    title: str | None = None


def _images(message: dict) -> list[Image]:
    role = (message.get("author") or {}).get("role")
    if role != "tool" or message.get("status") != "finished_successfully":
        return []
    out = []
    for part in (message.get("content") or {}).get("parts") or []:
        if isinstance(part, dict) and part.get("content_type") == "image_asset_pointer":
            pointer = part.get("asset_pointer")
            if isinstance(pointer, str) and "://" in pointer:
                out.append(Image(pointer, pointer.split("://", 1)[1], part.get("width"), part.get("height")))
    return out


def _text(message: dict) -> str:
    content = message.get("content") or {}
    if (message.get("author") or {}).get("role") != "assistant" or content.get("content_type") != "text":
        return ""
    return "".join(p for p in content.get("parts") or [] if isinstance(p, str))


def _gather(messages: list[dict], reading: Reading) -> Reading:
    for m in messages:
        reading.images += _images(m)
        reading.assistant_text += _text(m)
    return reading


def _apply(message: dict, path: str, op: str, value) -> None:
    """One JSON-patch op on the current message; only `/message/...` paths (the stream's only ones) are taken."""
    keys = path.split("/")[1:]
    if not keys or keys[0] != "message" or len(keys) < 2:
        return
    node = message
    for key in keys[1:-1]:
        node = node[int(key)] if isinstance(node, list) else node.setdefault(key, {})
    last = keys[-1]
    if isinstance(node, list):
        last = int(last)
    if op == "append":
        old = node[last] if isinstance(node, list) else node.get(last)
        if isinstance(old, str) and isinstance(value, str):
            value = old + value
        elif isinstance(old, list):
            value = old + (value if isinstance(value, list) else [value])
        elif isinstance(old, dict) and isinstance(value, dict):
            value = {**old, **value}
    elif op not in ("replace", "add"):
        return
    node[last] = value


class Reader:
    """Fed the stream line by line (`feed`); `reading()` at any point says what is known so far."""

    def __init__(self):
        self._conversation: str | None = None
        self._messages: dict[str, dict] = {}
        self._current: dict | None = None
        self._path = ""
        self._op = ""
        self._blocked = False
        self._complete = False
        self._title: str | None = None

    def conversation_named(self) -> str | None:
        return self._conversation

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("data:"):
            return                                   # event names, `: ping` comments, blank lines
        data = line[5:].strip()
        if data == "[DONE]":
            self._complete = True
            return
        try:
            obj = json.loads(data)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return                                   # the "v1" of delta_encoding, or anything new
        if not self._conversation and isinstance(obj.get("conversation_id"), str):
            self._conversation = obj["conversation_id"]
        kind = obj.get("type")
        if kind == "title_generation" and isinstance(obj.get("title"), str):
            self._title = obj["title"]
        elif kind == "moderation":
            if (obj.get("moderation_response") or {}).get("blocked") is True:
                self._blocked = True
        elif kind == "message_stream_complete":
            self._complete = True
        elif kind is None and "v" in obj:
            self._delta(obj)

    def _delta(self, obj: dict) -> None:
        value = obj["v"]
        if isinstance(value, dict) and isinstance(value.get("message"), dict):
            message = value["message"]
            if not self._conversation and isinstance(value.get("conversation_id"), str):
                self._conversation = value["conversation_id"]
            self._messages[str(message.get("id"))] = message
            self._current = message
            return
        # v1: a delta without p/o repeats the previous one's (a text streamed as one append, then bare values)
        self._path = obj.get("p", self._path)
        self._op = obj.get("o", self._op)
        ops = value if self._op == "patch" and isinstance(value, list) else [
            {"p": self._path, "o": self._op, "v": value}]
        for op in ops:
            if self._current is None or not isinstance(op, dict) or not isinstance(op.get("p"), str):
                continue
            try:
                _apply(self._current, op["p"], op.get("o", ""), op.get("v"))
            except (LookupError, ValueError, TypeError, AttributeError):
                continue                             # a path this message has no room for: skip, never break

    def reading(self) -> Reading:
        user = next((mid for mid, m in self._messages.items() if (m.get("author") or {}).get("role") == "user"),
                    None)
        return _gather(list(self._messages.values()), Reading(
            self._conversation, user, blocked=self._blocked, complete=self._complete, title=self._title))


def read_conversation(body: dict, message_id: str) -> Reading:
    """`GET /backend-api/conversation/{id}`: `user_message` is `message_id` only when a user message has that id."""
    messages = [n["message"] for n in (body.get("mapping") or {}).values()
                if isinstance(n, dict) and isinstance(n.get("message"), dict)]
    ours = any(m.get("id") == message_id and (m.get("author") or {}).get("role") == "user" for m in messages)
    blocked = any(isinstance(r, dict) and r.get("blocked") is True for r in body.get("moderation_results") or [])
    complete = any((m.get("author") or {}).get("role") == "assistant" and m.get("end_turn") is True
                   and m.get("status") == "finished_successfully" for m in messages)
    title = body.get("title") if isinstance(body.get("title"), str) else None
    return _gather(messages, Reading(body.get("conversation_id"), message_id if ours else None,
                                     blocked=blocked, complete=complete, title=title))


def verdict(r: Reading) -> Verdict:
    if r.images:
        return "image"
    if r.blocked:
        return "refused"
    if r.complete and not r.images and r.assistant_text.strip():
        return "refused"
    return "open"
