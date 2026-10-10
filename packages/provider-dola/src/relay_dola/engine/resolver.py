"""The result tier: read one dola conversation over HTTP (`POST /im/chain/single`, cmd 3100) and judge it.

Ported from dola_v4 `resolver.py` (the poll half). It needs no browser. HTTP is always 200 and the real status is in
the body (measured 260910):

| body `status_code` | meaning | retry helps? |
|---|---|---|
| 0 / absent | ok | |
| 712012001 | the session is dead | no (fresh cookies may still arrive: the job keeps waiting) |
| 712017001 | the conversation is not this account's, or does not exist | no |
| anything else, HTTP != 200, not JSON | network trouble or an unknown code | yes |

Pure helpers (`extract_videos`, `read_video`, ...) take the parsed body and a clock value: no network, no time of their
own, so every outcome is testable on a recorded chain. `fetch_chain` is the one network call.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from relay_browser.text import norm_for_match
from relay_core.providers import Output
from relay_dola import classify, constants, errors
from relay_dola.engine.http import DolaHttp

NOT_LOGGED_IN = 712012001
DATA_NOT_FOUND = 712017001

# platform codes (relay_core owns them; spelled here because the provider raises them)
ACCOUNT_DEAD_SESSION = "account.dead_session"
ACCOUNT_WRONG_CONVERSATION = "account.wrong_conversation"
ACCOUNT_PROMPT_MISMATCH = "account.prompt_mismatch"
RENDER_WATERMARKED_ONLY = "render.watermarked_only"

# `video.status` of a cell dola FAILED ("Couldn't generate video"): empty, never gets a file (measured 26/09).
VIDEO_FAILED = 4
# Longest prompt prefix compared when the conversation is judged to be the job's (v4 `_prompt_matches`).
PROMPT_PREFIX = 80


class ChainError(RuntimeError):
    """A conversation read that did not give a usable answer.

    `transient`: reading again may help. `terminal`: dola refused for good (copyright, no credit used, a question):
    the job ends, nothing is re-sent. The raiser names the `code` because only it knows the real reason; `raw` keeps
    dola's own words so the timeline holds the evidence, not just our conclusion; `state_patch` rides on the Failed."""

    def __init__(self, message: str, *, code: str, transient: bool = False, terminal: bool = False,
                 raw: str | None = None, state_patch: dict | None = None):
        super().__init__(message)
        self.code, self.transient, self.terminal = code, transient, terminal
        self.raw, self.state_patch = raw, state_patch or {}


@dataclass(frozen=True)
class VideoCell:
    url: str
    clean: bool            # True only for the decoded `main_url` master; `download_url` carries a watermark
    creation: dict = field(repr=False)
    at: float              # when the message holding this cell was created (seconds)


@dataclass(frozen=True)
class VideoRead:
    ready: tuple[VideoCell, ...]   # finished cells in message order; empty while a multi-video order still waits
    pending: int                   # cells with no file that are not failed
    text: str                      # dola's words this round, newest first, one paragraph per reply


# ---- body helpers -----------------------------------------------------------------------------------------------
def chain_messages(body: dict) -> list[dict]:
    chain = ((body.get("downlink_body") or {}).get("pull_singe_chain_downlink_body")) or {}
    return [m for m in (chain.get("messages") or []) if isinstance(m, dict)]


def message_time(msg: dict) -> float:
    """Creation time of a message in seconds (dola gives seconds, sometimes milliseconds). 0 when absent."""
    try:
        t = float(msg.get("create_time") or 0)
    except (TypeError, ValueError):
        return 0.0
    return t / 1000.0 if t > 1e12 else t


def last_message_time(body: dict) -> float:
    return max([message_time(m) for m in chain_messages(body)] or [0.0])


def _content(msg: dict) -> Any:
    content = msg.get("content")
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return None
    return content


def video_creations(body: dict) -> list[tuple[dict, float]]:
    """Every video cell of the conversation in message order: [(cell, time of its message)]."""
    out = []
    for msg in sorted(chain_messages(body), key=lambda m: int(m.get("index_in_conv") or 0)):
        content = _content(msg)
        if not isinstance(content, list):
            continue
        at = message_time(msg)
        for block in content:
            if not isinstance(block, dict) or block.get("block_type") != 2074:
                continue
            for creation in ((block.get("content") or {}).get("creation_block") or {}).get("creations", []):
                if isinstance(creation, dict) and creation.get("type") == 2:   # 2 == video
                    out.append((creation, at))
    return out


def decode_main_url(token: str) -> str:
    import base64
    try:
        return base64.b64decode(token).decode("utf-8", "ignore")
    except (ValueError, TypeError):
        return ""


def video_file(creation: dict) -> tuple[str, bool]:
    """(url, is the clean `main_url`) of one cell. The clean master (base64 `main_url`) wins; a cell with only the
    watermarked `download_url` gives that and False. An empty url means the cell has no file yet."""
    video = creation.get("video") or {}
    best, clean = video.get("download_url", "") or "", False
    try:
        video_list = json.loads(video.get("video_model", "") or "{}").get("video_list") or {}
        for entry in video_list.values():
            if isinstance(entry, dict) and entry.get("main_url"):
                decoded = decode_main_url(entry["main_url"])
                if decoded.startswith("http"):
                    best, clean = decoded, True
    except (ValueError, TypeError, AttributeError):
        pass
    return best, clean


def extract_videos(body: dict) -> tuple[list[VideoCell], int]:
    """(cells that have a file, in message order; number of cells still rendering). Scans EVERY cell: dola retries
    inside the same conversation (a failed 15 s cell leaves an empty one, the 10 s retry sits after it) and one order
    can come out as several videos (dola splits it); the customer gets all of them."""
    ready, pending = [], 0
    for creation, at in video_creations(body):
        url, clean = video_file(creation)
        if url:
            ready.append(VideoCell(url, clean, creation, at))
        elif (creation.get("video") or {}).get("status") != VIDEO_FAILED:
            pending += 1
    return ready, pending


def cell_duration(creation: dict) -> float | None:
    """The cell's own length in seconds from the chain (`video.duration`, else `video_model.video_duration`)."""
    video = creation.get("video") or {}
    for raw in (video.get("duration"), _model(video).get("video_duration")):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _model(video: dict) -> dict:
    try:
        model = json.loads(video.get("video_model", "") or "{}")
    except (ValueError, TypeError):
        return {}
    return model if isinstance(model, dict) else {}


def cell_id(creation: dict) -> str | None:
    vid = (creation.get("video") or {}).get("vid")
    return str(vid) if vid else None


def video_outputs(cells, conversation_id: str, start: int = 0) -> tuple[Output, ...]:
    """The clean cells as `Output`s. `upstream_id` is the cell's video id (stable across reads: Recover and a Re-check
    dedupe on it), `meta` holds the conversation (the fetch finds the cell again to ask for its master) and the cell's
    own length; never a link with a secret or a decode seed."""
    out = []
    for i, cell in enumerate(cells):
        meta = {"conversation_id": str(conversation_id), "source": "main_url"}
        if (length := cell_duration(cell.creation)) is not None:
            meta["duration"] = length
        out.append(Output(cell.url, "video/mp4", start + i,
                          cell_id(cell.creation) or "%s:%d" % (conversation_id, start + i), meta))
    return tuple(out)


USER_TYPE = 1       # `user_type` of a message the customer wrote (dola's own replies are 2)


def is_user_message(msg: dict) -> bool:
    return msg.get("user_type") in (USER_TYPE, str(USER_TYPE))


def _block_texts(content: list) -> list[str]:
    return [t for t in (((b.get("content") or {}).get("text_block") or {}).get("text", "")
                        for b in content if isinstance(b, dict)) if t]


def message_texts(msg: dict) -> list[str]:
    """The text blocks of ONE assistant message. A user message gives nothing, in either of its shapes (a dict
    `{"text": ...}`, or the block list the page's own reads carry): the customer's words are never dola's reply."""
    if is_user_message(msg):
        return []
    content = _content(msg)
    return _block_texts(content) if isinstance(content, list) else []


def conversation_text(body: dict) -> str:
    """Every assistant sentence of this one conversation (never the sidebar), newest first, one paragraph per reply,
    so a classifier that looks for phrases inside one sentence is not fooled by two replies run together."""
    replies = [" ".join(t) for t in map(message_texts, chain_messages(body)) if t]
    return "\n\n".join(replies)


def recent_conversation_text(body: dict, limit: int) -> str:
    """The NEWEST replies of the conversation, newest first as `conversation_text` reads, cut at a paragraph boundary to
    fit `limit` characters (the event cap). `reaper` hands the stored text to `deadline_cause`, and a text over the cap
    is stored as a bare "[omitted len=N]": dola's last words must fit. A newest reply that alone exceeds the limit is
    cut at the limit."""
    out: list[str] = []
    size = 0
    for paragraph in conversation_text(body).split("\n\n"):
        added = len(paragraph) + (2 if out else 0)
        if size + added > limit:
            if not out:
                out.append(paragraph[:limit])
            break
        out.append(paragraph)
        size += added
    return "\n\n".join(out)


def user_text(body: dict) -> str:
    """The text of the USER's messages (the prompt), in both shapes dola sends: `content` a dict {"text": ...}, or a
    block list (i2v, and every read the page itself makes) marked `user_type == 1`."""
    parts = []
    for msg in chain_messages(body):
        content = _content(msg)
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif isinstance(content, list) and is_user_message(msg):
            parts.extend(_block_texts(content))
    return " ".join(parts)


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def prompt_matches(expect: str, text: str) -> bool:
    """Is this the job's conversation: the first 80 characters of `expect` (the marker, else the prompt) occur in the
    conversation text, folded the way `norm_for_match` does. Empty `expect` blocks nothing."""
    exp = norm_for_match(expect)
    return True if not exp else exp[:PROMPT_PREFIX] in norm_for_match(text)


def carries_text(text: str, sent_text: str, prompt: str) -> bool:
    """Does the text carry the FULL text we sent, and did the customer write something. Both sides are folded (NFKC,
    invisible characters dropped, quotes straightened, whitespace collapsed): dola stores the prompt with such changes.
    The second condition looks at the job's own `prompt`, not at the sent text: a frame may put the tracking code first,
    in the middle or last and may start with fixed words, so the sent text alone cannot tell the customer's words from
    the frame's. A job whose prompt folds to nothing matches nothing (its sent text would be frame words and a code that
    another job's conversation can share)."""
    folded = norm_for_match(sent_text)
    return bool(folded and norm_for_match(prompt)) and folded in norm_for_match(text)


def may_be_job(body: dict, marker: str, prompt: str) -> bool:
    """Could the conversation be the job's with its text altered: it carries the job's bare tracking code (the digits
    `JobView.marker` stores, with or without the brackets the frame puts around them) or the first 80 folded characters
    of the job's prompt, in the user's words or anywhere in the thread. Weaker than `carries_text` (it is true whenever
    that is), so it is "cannot tell", never "not ours". Nothing of the frame is used: its fixed words are the same for
    every job of the model and would match them all.

    The 80-character window is also tried without its first and last character: where the prompt meets the frame's
    words, those two can fold differently inside the sent text (a combining mark or a Hangul jamo composes with the
    letter before it, a final sigma changes with the letter after it) than when the prompt is folded alone."""
    hay = norm_for_match(user_text(body) + " " + conversation_text(body))
    code, words = norm_for_match(marker), norm_for_match(prompt)
    window = words[:PROMPT_PREFIX]
    inner = window[1:-1] if len(window) >= 10 else ""
    return bool((code and code in hay) or (window and window in hay) or (inner and inner in hay))


def adopted_wrong_conversation(body: dict, expect: str | None) -> bool:
    """Is this NOT the job's conversation. Judged only once the user's words are readable: a conversation still loading
    reads empty, and convicting it would drop a job that is running fine."""
    if not expect:
        return False
    mine = user_text(body)
    if not mine.strip():
        return False
    return not prompt_matches(expect, mine + " " + conversation_text(body))


# ---- the sentences that decide ----------------------------------------------------------------------------------
# dola says "no credit used" many ways; 21/09 it said "didn't use any credit" and the list had only "no credits were
# used", the job was dropped and dola delivered ~25 min later. Matched by meaning: dola says it did NOT charge, or that
# it will not show the generated video.
_NO_CHARGE = (r"no\s+credits?\s+(?:were|was|is|are)\s+used"
              r"|(?:did\s*n[’']?t|did\s+not|does\s*n[’']?t|do\s*n[’']?t)"
              r"\s+use\s+(?:any\s+)?credits?")
_MAY_STILL_DELIVER = re.compile(
    _NO_CHARGE + r"|(?:ca\s*n[’']?t|cannot|could\s*n[’']?t)\s+show\s+you\s+the\s+generated\s+video", re.I)
_NO_CHARGE_RE = re.compile(_NO_CHARGE, re.I)


def refusal_may_still_deliver(text: str) -> bool:
    """A refusal that says no credit was used, or that the video cannot be shown, is not final at poll time: dola has
    refused and then delivered (conv 38417876432719633: refusal at t+9 min, the master at ~t+25). Only "out of credit
    to START" ends a job here."""
    return bool(_MAY_STILL_DELIVER.search(text or ""))


def newest_reply(text: str) -> str:
    return (text or "").split("\n\n", 1)[0].strip()


def stopped_without_charge(text: str) -> str | None:
    """dola's NEWEST reply when it stopped the order without charging ("I can't generate the video and didn't use any
    credit"); the user decided 30/09 that this ends the job at once. An older one (before dola took the order) does
    not cancel the order it took afterwards, and a copyright block keeps its own path."""
    newest = newest_reply(text)
    if not _NO_CHARGE_RE.search(newest) or _is_copyright(newest):
        return None
    return newest


def _is_copyright(text: str) -> bool:
    plain = classify.plain(text)
    return any(m in plain for m in classify.COPYRIGHT_BLOCK_MARKERS)


def copyright_block_after(body: dict, since: float | None) -> str | None:
    """The newest dola reply when it is a copyright block written AFTER `since` (the send or a re-check). Older blocks
    do not count: a re-checked job keeps its rule. No `since`, or a message with no time, is not judged here."""
    if since is None:
        return None
    for msg in chain_messages(body):        # newest first, like `conversation_text`
        texts = message_texts(msg)
        if not texts:
            continue
        text, at = " ".join(texts), message_time(msg)
        if at and at >= since and _is_copyright(text):
            return text
        return None
    return None


def copyright_block_anywhere(body: dict, since: float | None) -> str | None:
    """Any dola message (not only the newest) that holds a copyright block and is not dated before `since`: v4's second
    path (`first_dola_saying`). The caller decides by when WE first saw it; a block with no time or a newer one is a
    candidate, a dated one from before the send or the Re-check is an older order's and never is."""
    for msg in chain_messages(body):
        text, at = " ".join(message_texts(msg)), message_time(msg)
        if text and _is_copyright(text) and not (at and since is not None and at < since):
            return text
    return None


def no_goods_detail(verdict) -> str:
    """The reason when dola says there is no result. "Something went wrong" at this step means the generation inside
    the conversation failed; the send-time wording "should retry" would be the opposite of the truth, the job ends."""
    if verdict.outcome is classify.SubmitOutcome.ERROR:
        return "dola said something went wrong in this conversation"
    return verdict.detail


def no_goods_code(verdict, default: str) -> str:
    """A content refusal keeps its own code (so the same refusal never gets two codes depending on whether dola said it
    at send or at poll time); anything else decided gets the `default`."""
    if verdict.outcome is classify.SubmitOutcome.CONTENT_REFUSED:
        return str(getattr(verdict.code, "value", verdict.code) or errors.CONTENT_REFUSED)
    return default


# ---- judging a conversation -------------------------------------------------------------------------------------
def read_video(body: dict, *, since: float | None, now: float) -> VideoRead:
    """Judge one read of a VIDEO conversation. Raises `ChainError` when dola ended the order for good; otherwise
    returns the finished cells (empty while still waiting). Pure: `now` is the caller's clock."""
    ready, pending = extract_videos(body)
    text = conversation_text(body)
    if ready and pending and now - min(c.at for c in ready) < constants.PENDING_GRACE_S:
        ready = []                    # more cells still render: wait to deliver all, but not forever
    if ready:
        return VideoRead(tuple(ready), pending, text)

    blocked = None if pending else copyright_block_after(body, since)
    if blocked:
        raise ChainError("dola blocked the video for copyright protection: %s" % blocked[:200],
                         code=errors.CONTENT_REFUSED, terminal=True, raw=text, state_patch={"no_recheck": True})
    stopped = None if pending else stopped_without_charge(text)
    if stopped:
        raise ChainError("dola stopped this order without charging for it: %s" % stopped[:300],
                         code=errors.CONTENT_REFUSED, terminal=True, raw=text)
    verdict = classify.classify_reply(text)
    # An order already taken is not cancelled by a limit/error line that came AFTER it (the answer to another send);
    # a content refusal still takes its own path.
    noise_after_order = (verdict.outcome in (classify.SubmitOutcome.NO_POINTS, classify.SubmitOutcome.ERROR)
                         and classify.order_accepted(text))
    if verdict.decided and not verdict.video_possible and not noise_after_order \
            and not refusal_may_still_deliver(text):
        raise ChainError("dola reports no video: %s" % no_goods_detail(verdict),
                         code=no_goods_code(verdict, errors.NO_VIDEO), terminal=True, raw=text)
    last = last_message_time(body)
    if not pending and last and now - last >= constants.QUIET_GRACE_S \
            and not classify.order_accepted(text) and not refusal_may_still_deliver(text):
        if extract_image_urls(body):         # job 2fabb502: "I've generated the 30-panel storyboard sheet"
            raise ChainError("dola made an image instead of a video", code=errors.NO_VIDEO, terminal=True, raw=text)
        if classify.asks_question(text) or verdict.outcome is classify.SubmitOutcome.NEEDS_CONFIRM:
            raise ChainError("dola asked a question instead of taking the order", code=errors.NEEDS_CONFIRM,
                             terminal=True, raw=text)
    return VideoRead((), pending, text)


def image_from_creation(img: dict) -> str | None:
    """The CLEAN image: `image_ori_raw` (2048 square, no watermark). The other renditions carry a watermark or may be
    half-made; without the clean one the image is not done yet, never "settle for the worse one"."""
    node = img.get("image_ori_raw")
    return node["url"] if isinstance(node, dict) and node.get("url") else None


def extract_image_urls(body: dict) -> list[str]:
    """Clean image URLs of the finished images; empty while the grid is still being made."""
    urls: list[str] = []
    for msg in chain_messages(body):
        if msg.get("content_type") != 9999:
            continue
        blocks = _content(msg)
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            crb = (block.get("content") or {}).get("creation_block") if isinstance(block, dict) else None
            if not crb:
                continue
            for cr in crb.get("creations", []):
                img = cr.get("image") if isinstance(cr, dict) else None
                url = image_from_creation(img) if isinstance(img, dict) else None
                if url:
                    urls.append(url)
    return urls


def read_images(body: dict) -> tuple[list[str], str]:
    """Judge one read of an IMAGE conversation: (urls, dola's words). Empty urls = not done. Raises `ChainError`."""
    text = conversation_text(body)
    urls = extract_image_urls(body)
    if urls:
        return urls, text
    stopped = stopped_without_charge(text)
    if stopped:
        raise ChainError("dola stopped this order without charging for it: %s" % stopped[:300],
                         code=errors.CONTENT_REFUSED, terminal=True, raw=text)
    verdict = classify.classify_reply(text)
    if verdict.decided and not verdict.video_possible and not refusal_may_still_deliver(text):
        raise ChainError("dola reports no image: %s" % no_goods_detail(verdict),
                         code=no_goods_code(verdict, errors.NO_IMAGE), terminal=True, raw=text)
    return [], text


# ---- the network call -------------------------------------------------------------------------------------------
def chain_payload(conversation_id: str) -> dict:
    return {
        "cmd": 3100,
        "uplink_body": {"pull_singe_chain_uplink_body": {
            "conversation_id": conversation_id, "anchor_index": 9007199254740991,
            "conversation_type": 3, "direction": 1, "limit": 20, "ext": {},
            "filter": {"index_list": []},
        }},
        "sequence_id": str(uuid.uuid4()), "channel": 2, "version": "1",
    }


async def fetch_chain(http: DolaHttp, conversation_id: str, expect: str | None = None) -> dict:
    """One read of a conversation: POST, parse, check the status codes, then the prompt guard (before any result is
    looked at, so a wrongly adopted conversation that never yields a result is still caught at once, not at the
    deadline). Raises `ChainError`, also when HTTP is 200."""
    try:
        resp = await http.post_chain("/im/chain/single", chain_payload(conversation_id),
                                     "https://www.dola.com/chat/%s" % conversation_id)
    except Exception as exc:   # noqa: BLE001 - any transport failure is a failed read, read again later
        raise ChainError("could not reach dola: %s" % str(exc)[:100], code=errors.HTTP_ERROR, transient=True) from exc
    if resp.status_code != 200:
        raise ChainError("HTTP %s from dola" % resp.status_code, code=errors.HTTP_ERROR, transient=True)
    try:
        body = resp.json()
    except (ValueError, TypeError) as exc:
        raise ChainError("response is not JSON: %s" % str(exc)[:80], code=errors.BAD_RESPONSE, transient=True) from exc
    if not isinstance(body, dict):
        raise ChainError("response is not a JSON object", code=errors.BAD_RESPONSE, transient=True)
    code = body.get("status_code")
    if code == NOT_LOGGED_IN:
        raise ChainError("the session is dead (%s): the job keeps waiting for fresh cookies" % code,
                         code=ACCOUNT_DEAD_SESSION)
    if code == DATA_NOT_FOUND:
        raise ChainError("the conversation does not belong to this account or does not exist (%s)" % code,
                         code=ACCOUNT_WRONG_CONVERSATION)
    if code not in (0, None):
        raise ChainError("dola returned unknown code %s: %s" % (code, str(body.get("status_desc"))[:60]),
                         code=errors.UNKNOWN_STATUS, transient=True)
    if adopted_wrong_conversation(body, expect):
        raise ChainError("conversation does not contain the job's prompt: the wrong conversation was adopted, "
                         "its result is refused", code=ACCOUNT_PROMPT_MISMATCH, terminal=True)
    return body


# Names dola shows before the real one is set: skipped, read again next round.
PLACEHOLDER_NAMES = ("new chat", "new conversation", "main chat", "新对话", "新会话")


async def conversation_names(http: DolaHttp, conversation_ids: list[str]) -> dict[str, str]:
    """dola's own name for each conversation (the sidebar name): {conversation_id: name}; one without a name is absent.
    `/im/conversation/batch_get` cmd 1111 answers over plain HTTP (measured 27/09, unlike `recent_conv`); it costs no
    credit. Any failure gives {}: a title is only convenience."""
    if not conversation_ids:
        return {}
    payload = {
        "cmd": 1111,
        "uplink_body": {"batch_get_conv_info_uplink_body": {
            "conversation_id": [str(c) for c in conversation_ids],
            "option": {"recent_message_count_per_conv": 1}, "ext": {}}},
        "sequence_id": str(uuid.uuid4()), "channel": 2, "version": "1",
    }
    try:
        resp = await http.post_chain("/im/conversation/batch_get", payload, "https://www.dola.com/chat", timeout=20)
        body = resp.json() if resp.status_code == 200 else {}
    except Exception:   # noqa: BLE001
        return {}
    if not isinstance(body, dict) or body.get("status_code") not in (0, None):
        return {}
    items = (((body.get("downlink_body") or {}).get("batch_get_conv_info_downlink_body") or {})
             .get("conversation_info_list")) or []
    out = {}
    for it in items:
        name = _flat(str((it or {}).get("name") or ""))
        if name and name.lower() not in PLACEHOLDER_NAMES:
            out[str(it.get("conversation_id"))] = name
    return out


def conversation_summary(body: dict) -> "ConversationSummary":
    """What reconcile needs from one read: how many messages, the newest message time, and the user's words."""
    messages = chain_messages(body)
    return ConversationSummary(len(messages), last_message_time(body), user_text(body), conversation_text(body))


@dataclass(frozen=True)
class ConversationSummary:
    messages: int
    newest: float          # newest message time in seconds; 0 when none is dated
    user: str
    assistant: str
