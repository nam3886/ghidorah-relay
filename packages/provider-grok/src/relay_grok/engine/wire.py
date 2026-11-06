"""grok's answers, read without any I/O: the Imagine WebSocket frames of a text-to-image run, the NDJSON lines of an
edit or a video, a refused answer's class, the assets URL of a relative path, the short-delivery note and the evidence
summary. Every field name here was seen in the 06/11 captures (fixtures/grok/FINDINGS.md F3, F5, F6, F8, F9).

Pure: no curl_cffi, no clock but the one handed in.
"""

import json
import re
import time
from collections.abc import Callable, Iterable
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from relay_grok.constants import (
    ANTIBOT_CODE,
    ASSETS_HOST,
    BAD_BODY_CODE,
    BLOCKED_PHRASES,
    CF_MARKS,
    LIMIT_PHRASES,
    SAFETY_PHRASES,
    SIGNED_OUT_CODE,
    STATE_CONVERSATION,
    STATE_POST,
    STATE_VIDEO,
)

ERROR_TEXT_MAX = 300      # an answer's error text kept for the timeline and the evidence


# ---- phrases and redaction -------------------------------------------------------------------------------------------
def _has(text: str, phrases: Iterable[str]) -> bool:
    folded = text.casefold()
    return any(p in folded for p in phrases)


def is_limit(text: str) -> bool:
    return _has(text, LIMIT_PHRASES)


def is_safety(text: str) -> bool:
    return _has(text, SAFETY_PHRASES)


_URL = re.compile(r"https?://[^\s\"'<>]+")
_USER_PATH = re.compile(r"users/[^\s\"'<>]+")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX = re.compile(r"[0-9a-f]{24,}", re.I)
# a Cookie / Authorization header's whole value; a cookie's or a JSON key's value named like a credential
_HEADER = re.compile(r"\b((?:set-)?cookie|authorization)([\"']?\s*[:=]\s*[\"']?)[^\r\n\"']+", re.I)
_PAIR = re.compile(r"\b(sso-rw|sso|x-userid|cf_clearance|[\w-]*(?:token|secret|password))([\"']?\s*[:=]\s*[\"']?)"
                   r"[^\s;,&\"'}]+", re.I)
_JWT = re.compile(r"\beyJ[\w-]*\.[\w-]+(?:\.[\w-]*)?")
_TOKEN = re.compile(r"(?<![\w+/-])(?=[\w+/-]*\d)[\w+/-]{32,}={0,2}")     # long base64 / opaque, a digit in it


def redact(text: str) -> str:
    """An answer's text with no link (its host kept), no `users/<uid>/…` path, no id, no credential (a Cookie or
    Authorization value, an `sso=`-style pair, a JWT, a long opaque token); at most ERROR_TEXT_MAX chars."""
    text = _HEADER.sub(r"\1\2<secret>", text)
    text = _PAIR.sub(r"\1\2<secret>", text)
    text = _JWT.sub("<secret>", text)
    text = _URL.sub(lambda m: "<%s>" % (urlsplit(m.group(0)).hostname or "link"), text)
    text = _HEX.sub("<id>", _UUID.sub("<id>", _USER_PATH.sub("<path>", text)))
    text = _TOKEN.sub("<secret>", text)
    return text[:ERROR_TEXT_MAX]


def _json(text) -> object:
    if isinstance(text, bytes | bytearray):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return None


def _error_message(err) -> str:
    """The text of an error object: `{"code", "message"}`, a plain string, or anything else as its JSON."""
    if isinstance(err, dict):
        msg = err.get("message") or err.get("err_msg")
        return msg if isinstance(msg, str) else json.dumps(err)
    return err if isinstance(err, str) else json.dumps(err)


# ---- Imagine WebSocket (text-to-image, F3) ---------------------------------------------------------------------------
class Final(NamedTuple):
    job_id: str
    url: str
    moderated: bool


class ImagineAnswer(NamedTuple):
    conversation_id: str | None
    jobs: tuple[str, ...]               # from start_stage, in arrival order
    finals: tuple[Final, ...]           # one per job, arrival order; `moderated` from that job's completed frame
    completed: frozenset[str]
    moderated: frozenset[str]
    previews: int
    limit: bool
    safety: bool
    error_text: str | None
    unknown_frames: int

    def deliverable(self) -> tuple[Final, ...]:
        """The finals that may go to the customer: not moderated."""
        return tuple(f for f in self.finals if not f.moderated)


def _is_final(frame: dict) -> bool:
    """A final is an `image` frame with a `.jpg` URL at 100 %; a preview (`.png`, 50 %) never is (F3)."""
    url = frame.get("url")
    jpg = isinstance(url, str) and urlsplit(url).path.endswith(".jpg")
    return jpg and frame.get("percentage_complete") == 100


class ImagineReader:
    """The frames of one Imagine run, fed as they come. A frame naming another request (`request_id`, F3) is skipped
    and counted nowhere: a stray of another run never becomes an output nor settles this one."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        self._conversation: str | None = None
        self._jobs: list[str] = []
        self._finals: dict[str, str] = {}
        self._completed: set[str] = set()
        self._moderated: set[str] = set()
        self._previews = 0
        self._limit = self._safety = False
        self._error: str | None = None
        self._unknown = 0

    def feed(self, text) -> None:
        frame = _json(text)
        if not isinstance(frame, dict):
            self._unknown += 1
            return
        rid = frame.get("request_id", frame.get("requestId"))
        if rid is not None and rid != self.request_id:
            return
        kind, job = frame.get("type"), frame.get("job_id")
        if kind == "session":
            if isinstance(frame.get("conversation_id"), str):
                self._conversation = frame["conversation_id"]
        elif kind == "json" and isinstance(job, str):
            status = frame.get("current_status")
            if status == "start_stage" and job not in self._jobs:
                self._jobs.append(job)
            elif status == "completed":
                self._completed.add(job)
                if frame.get("moderated") is True:
                    self._moderated.add(job)
        elif kind == "image" and isinstance(job, str):
            if _is_final(frame):
                self._finals.setdefault(job, frame["url"])
            else:
                self._previews += 1
        elif kind == "error" or "err_code" in frame or "error" in frame:
            msg = _error_message(frame.get("error", frame))
            self._limit = self._limit or is_limit(msg)
            self._safety = self._safety or is_safety(msg)
            self._error = self._error or redact(msg)
        else:
            self._unknown += 1

    def answer(self) -> ImagineAnswer:
        finals = tuple(Final(j, u, j in self._moderated) for j, u in self._finals.items())
        return ImagineAnswer(self._conversation, tuple(self._jobs), finals, frozenset(self._completed),
                             frozenset(self._moderated), self._previews, self._limit, self._safety, self._error,
                             self._unknown)

    def settled(self) -> bool:
        """Every started job is completed (and at least one started)."""
        return bool(self._jobs) and set(self._jobs) <= self._completed


# ---- NDJSON of conversations/new (edit F5, text-to-video F6) ---------------------------------------------------------
class MediaAnswer(NamedTuple):
    conversation_id: str | None
    response_id: str | None
    video_id: str | None
    video_post_id: str | None
    progress: int
    final_url: str | None               # relative (`users/<uid>/…`); `asset_url` makes it fetchable
    asset_id: str | None
    thumbnail_url: str | None
    moderated: bool | None
    rrated_present: bool                # the decoy rule tests the key, not its value: `rRated: false` is a real video
    rrated: bool | None
    width: int | None
    height: int | None
    ended: bool                         # `modelResponse` seen
    error_text: str | None
    limit: bool
    safety: bool
    first_line_seen: bool
    progress_at: dict[int, float]       # progress -> seconds since the reader started (first time seen)


_STREAM_KEY = {"image": "streamingImageGenerationResponse", "video": "streamingVideoGenerationResponse"}
_IDS = ((STATE_CONVERSATION, "conversation_id"), (STATE_VIDEO, "video_id"), (STATE_POST, "video_post_id"))


class NdjsonReader:
    """The lines of one edit or video run, fed as they come; `answer()` reads what they hold so far."""

    def __init__(self, kind: Literal["image", "video"], clock: Callable[[], float] = time.monotonic):
        self.kind, self.clock = kind, clock
        self.start = clock()
        self.f: dict = dict(conversation_id=None, response_id=None, video_id=None, video_post_id=None, progress=0,
                            final_url=None, asset_id=None, thumbnail_url=None, moderated=None, rrated_present=False,
                            rrated=None, width=None, height=None, ended=False, error_text=None, limit=False,
                            safety=False, first_line_seen=False)
        self.progress_at: dict[int, float] = {}
        self._new: dict[str, str] = {}

    def _set_once(self, key: str, value) -> None:
        if isinstance(value, str) and value and self.f[key] is None:
            self.f[key] = value

    def feed(self, line) -> None:
        self._new = {}
        before = {state: self.f[key] for state, key in _IDS}
        if isinstance(line, bytes | bytearray):
            line = line.decode("utf-8", "replace")
        if not line.strip():
            return
        d = _json(line)
        if not isinstance(d, dict):
            return
        f = self.f
        f["first_line_seen"] = True
        if "error" in d:
            msg = _error_message(d["error"])
            f["limit"] = f["limit"] or is_limit(msg)
            f["safety"] = f["safety"] or is_safety(msg)
            f["error_text"] = f["error_text"] or redact(msg)
        result = d.get("result")
        result = result if isinstance(result, dict) else {}
        conv = result.get("conversation")
        if isinstance(conv, dict):
            self._set_once("conversation_id", conv.get("conversationId"))
        resp = result.get("response")
        if isinstance(resp, dict):
            self._response(resp)
        self._new = {state: f[key] for state, key in _IDS if f[key] is not None and before[state] is None}

    def _response(self, resp: dict) -> None:
        f = self.f
        self._set_once("response_id", resp.get("responseId"))
        if "modelResponse" in resp:
            f["ended"] = True
        g = resp.get(_STREAM_KEY[self.kind])
        if not isinstance(g, dict):
            return
        self._set_once("video_id", g.get("videoId"))
        self._set_once("video_post_id", g.get("videoPostId"))
        if isinstance(g.get("progress"), int | float):
            p = int(g["progress"])
            f["progress"] = max(f["progress"], p)
            self.progress_at.setdefault(p, self.clock() - self.start)
        if "moderated" in g:
            f["moderated"] = f["moderated"] is True or g["moderated"] is True
        for key in ("width", "height"):
            if isinstance(g.get(key), int):
                f[key] = g[key]
        if g.get("progress") != 100:
            return
        url = g.get("videoUrl" if self.kind == "video" else "imageUrl")
        if isinstance(url, str) and url:
            f["final_url"] = url
            self._set_once("asset_id", g.get("assetId"))
            if isinstance(g.get("thumbnailImageUrl"), str):
                f["thumbnail_url"] = g["thumbnailImageUrl"]
        if "rRated" in g:
            f["rrated_present"], f["rrated"] = True, g["rRated"] is True

    def answer(self) -> MediaAnswer:
        return MediaAnswer(**self.f, progress_at=dict(self.progress_at))

    def ids_new(self) -> dict[str, str] | None:
        """The ids (state keys) the last `feed` saw first, for an immediate `report_ref`; None when it saw none."""
        return dict(self._new) or None


# ---- a refused answer (F8, F9) ---------------------------------------------------------------------------------------
Refusal = Literal["signed_out", "cloudflare", "antibot", "blocked", "limit", "bad_body", "other"]


def classify_refusal(status: int, content_type: str, body: str, server: str = "") -> Refusal:
    """A non-200 answer by exact shapes only. A blocked account may carry `code 7` too, so the blocked phrases are read
    before the anti-bot code; a 429 (`code 8` "Too many requests", the out-of-quota send) is a limit, and so is another
    4xx with grok's usage-limit words: a refusal proven before acceptance. A 5xx (or any other status) with those words
    is `other`: it proves nothing was refused, so it never moves the job to another account."""
    d = _json(body)
    err = d.get("error") if isinstance(d, dict) else None
    code = (err if isinstance(err, dict) else d if isinstance(d, dict) else {}).get("code")
    html = "html" in content_type.lower() or body.lstrip().startswith("<")
    if status == 401 and code == SIGNED_OUT_CODE:
        return "signed_out"
    if status == 403 and ((html and _has(body, CF_MARKS)) or (not body.strip() and server.lower() == "cloudflare")):
        return "cloudflare"
    if _has(body, BLOCKED_PHRASES):
        return "blocked"
    if status == 403 and code == ANTIBOT_CODE:
        return "antibot"
    if status == 429 or (400 <= status < 500 and is_limit(body)):
        return "limit"
    if status == 400 and code == BAD_BODY_CODE:
        return "bad_body"
    return "other"


# ---- results ---------------------------------------------------------------------------------------------------------
def asset_url(relative) -> str | None:
    """`https://assets.grok.com/<relative>` for a relative path (F7), or None for anything that could send the
    account's cookie elsewhere: a scheme, a host, a leading `/`, `//`, `..`, a backslash."""
    if not isinstance(relative, str) or not relative:
        return None
    parts = urlsplit(relative)
    if (parts.scheme or parts.netloc or relative.startswith("/") or "//" in relative or ".." in relative
            or "\\" in relative):
        return None
    return "https://%s/%s" % (ASSETS_HOST, relative)


def made_note(made: int, n: int, kind: str) -> str | None:
    """The timeline line of a done job grok made more or fewer results for than the paid `n` (None: as paid). Fewer is
    still a full charge (any output is a full charge); more: the first n go out."""
    if made == n:
        return None
    if made > n:
        return "grok made %d %ss, %d were paid: the first %d are delivered, the rest are not" % (made, kind, n, n)
    return "grok made %d of the %d %ss asked; all are charged (any output is a full charge)" % (made, n, kind)


# ---- evidence --------------------------------------------------------------------------------------------------------
_NOISE = ("responseId", "isThinking", "isSoftStop")


def _walk(tree, path: str = ""):
    """(leaf path, value) of a JSON tree; a list adds `[]`, a key that looks like an id becomes `<id>`."""
    if isinstance(tree, dict):
        for k, v in tree.items():
            k = redact(str(k))
            yield from _walk(v, "%s.%s" % (path, k) if path else k)
    elif isinstance(tree, list):
        for v in tree:
            yield from _walk(v, path + "[]")
    else:
        yield path, tree


def _type(d: dict) -> str:
    if isinstance(d.get("type"), str):
        status = d.get("current_status")
        return d["type"] + (":" + status if isinstance(status, str) else "")
    result = d.get("result")
    if isinstance(result, dict) and result:
        top = next(iter(result))
        sub = result[top]
        if top == "response" and isinstance(sub, dict):
            inner = [k for k in sub if k not in _NOISE]
            return "response." + (inner[0] if inner else "")
        return top
    return "error" if "error" in d else "?"


def summary(items: Iterable) -> bytes:
    """Frames or lines as evidence: each one's type, progress, key paths and the link hosts it names, plus a redacted
    error text. No value otherwise: no id, URL path, blob, prompt or user path."""
    out = []
    for item in items:
        d = _json(item)
        if not isinstance(d, dict):
            out.append({"bad": len(item) if isinstance(item, str | bytes | bytearray) else None})
            continue
        leaves = list(_walk(d))
        progress = next((v for p, v in leaves if p.rsplit(".", 1)[-1] in ("progress", "percentage_complete")
                         and isinstance(v, int | float)), None)
        hosts = sorted({urlsplit(m).hostname or "" for _, v in leaves if isinstance(v, str) for m in _URL.findall(v)})
        row = {"type": _type(d), "progress": progress, "keys": list(dict.fromkeys(p for p, _ in leaves)),
               "hosts": hosts}
        if "error" in d or "err_code" in d:
            row["error"] = redact(_error_message(d.get("error", d)))
        out.append(row)
    return json.dumps(out).encode()
