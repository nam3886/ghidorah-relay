"""Submit: send one order through dola's page and map what dola said to a `SubmitResult`.

One `open_chat` preamble serves video and image: open the session, check the region, probe the login, take the
baseline, build the composer, attach the references, then `mark_sent`, Enter, and read the receipt and the replies from
the traffic the page itself produced (the `NetRecorder`). The UI is only the fallback.

Money rules that shape this file (each has a test and a mutation):
- `ctx.mark_sent()` runs IMMEDIATELY before Enter, never after: an order sent but not marked would be sent twice.
- After `mark_sent` the engine never returns `Retry` (it returns `Ambiguous`): a retry could be a second paid order.
- Every re-send into a gated conversation is preceded by `ctx.fence()`, and a lease lost before one means NO second
  send. It is judged on the new part of the conversation only, with the account's composer UI (`order_taken`).
- The conversation is the first `/im/chain/single` read whose body carries the job's WHOLE `sent_text` (the core
  composes it: prompt, hint, marker; the engine never builds its own).
- The pool that rests on "out of credit" is the job's kind, not dola's wording (dola says "video" for images too).
- There is no balance check before sending: the claim did it before any browser opened.

The page is driven through a `PageDriver` and the platform through `ctx`; a test passes a driver and a session factory
that need no Chrome (`Deps`).
"""

import asyncio
import contextlib
import functools
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from urllib.parse import urlsplit

from relay_browser.evidence import TEXT_MIME, Evidence
from relay_browser.session import BrowserLaunchError, BrowserRefused, open_session
from relay_browser.text import norm_for_match
from relay_browser.ui import inject_cookies
from relay_core.providers import (
    REFUSED_BY,
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Facts,
    Fail,
    NotTaken,
    ProxyBad,
    Retry,
    SubmitResult,
    Success,
    Warning,
)
from relay_core.redact import redact_text
from relay_dola import classify, constants, errors
from relay_dola.catalog import DEFAULT_RATIO
from relay_dola.credential import IDENTITY_COOKIE_NAMES, jar_from_blob
from relay_dola.engine import composer, resolver
from relay_dola.engine.http import DolaHttp, query_params
from relay_dola.engine.poll import image_outputs
from relay_dola.engine.site import (
    BASE,
    CHAT_URL,
    DOLA_SITE,
    REGION_BLOCK_MARK,
    duration_response_patch,
)
from relay_dola.engine.verify import (
    LOGIN_PROBE_JS,
    LoginState,
    cookies_lost_effects,
    login_doubt_effects,
    login_state,
    lost_identity_cookies,
)
from relay_dola.spec import SPEC

log = logging.getLogger("relay_dola.submit")
SINGLE_PATH = "/im/chain/single"
COMPLETION_PATH = "/chat/completion"

# dola's own throttle and region codes, carried in the `/chat/completion` SSE body (HTTP stays 200). Passive reading
# only: the request is never edited (dola signs the body).
THROTTLE_CODES = (710022004,)               # rate limit: the account rests, the job goes elsewhere (a refusal)
REGION_CODE = 710022003                     # blocked by region for this endpoint: the proxy is the wrong region
# dola will not take THIS order: measured 23/10, 9 of 18 tries in 3 h were 710022002, all on one prompt and on 7
# accounts, so it answers the prompt, not the account. Owner's ruling 25/10 (replacing 23/10's "end at once"): like
# every refusal, the account rests and the job goes to another one until `dola.refusal_max_accounts` refused it.
REFUSED_CODE = 710022002
REFUSAL_MAX_ACCOUNTS = "dola.refusal_max_accounts"
SSE_MESSAGE_CHARS = 200
# Events of a `/chat/completion` stream that deliver nothing: a heartbeat and the acknowledgement of the send.
QUIET_EVENTS = frozenset({"SSE_HEARTBEAT", "SSE_ACK"})
_CONV_ID = re.compile(r'"conversation_id"\s*:\s*"(\d+)"')
_ANCHOR = re.compile(r'"anchor_index"\s*:\s*(\d+)')
# The anchor of a FULL read of a conversation (the newest messages, whole): the one `CHAIN_FETCH_JS` asks for. The
# page's own incremental reads use a small anchor and carry the customer's message as text blocks.
FULL_READ_ANCHOR = 9007199254740991

# Levels dola offers by itself: any other duration needs the action-bar config patched (see site.py).
NATIVE_DURATIONS = frozenset({5, 10})
# The platform's pool codes (the platform owns them; the engine only names them).
POOL_LOGGED_OUT = "pool.account_logged_out"
POOL_THROTTLED = "pool.account_throttled"
POOL_REGION_BLOCKED = "pool.account_region_blocked"
POOL_OUT_OF_POINTS = "pool.account_out_of_points"
POOL_CANNOT_AFFORD = "pool.account_cannot_afford"
POOL_PROXY_FAILED = "pool.proxy_failed"
# What Playwright says when the page navigated under a running script.
PAGE_NAVIGATED = "execution context was destroyed"
# What marks a failure as a network one (the browser could not reach dola), not a UI one. A UI error says nothing about
# the proxy and is never blamed on it.
NETWORK_MARKS = ("err_timed_out", "err_connection", "err_proxy", "err_tunnel", "err_name_not_resolved",
                 "err_address_unreachable", "net::err")

# After `goto` the page may navigate again by itself (a reload shortly after /ttwid/check/ on some accounts). The run
# waits for a quiet page before it composes: at least SETTLE_MIN_S, then until no main-frame navigation for
# SETTLE_QUIET_S, and never longer than SETTLE_CAP_S.
SETTLE_MIN_S = 4.0
SETTLE_QUIET_S = 3.0
SETTLE_CAP_S = 15.0
SETTLE_STEP_MS = 250
COOKIES_TIMEOUT_S = 5.0      # a browser that does not answer a cookie read in this time is treated as unreadable

POLL_S = 2.5                 # between reads of the conversation while dola answers
REPLY_TRIES = 24             # about 60 s: with several profiles running dola answers much slower than a lone test
RECHECK_TRIES = 4            # re-reads before a re-send: the acceptance sentence can come a few seconds late
MAX_RESENDS = 2
# The session waits this long (from the start of the submit) for the images of an image order; the core cancels the
# submit at `SUBMIT_TIMEOUT_IMAGE_S`, so the wait ends well before it and falls back to the poll.
IMAGE_HOLD_S = constants.SUBMIT_TIMEOUT_IMAGE_S - 60
IMAGE_POLL_S = 5.0
REPLY_CHARS = constants.EVENT_TEXT_MAX      # the core stores an event over this as a bare "[omitted len=N]"

# Reads the conversation through dola's own API from INSIDE the page (same origin: cookies and proxy come along). It
# is the same data the poll uses, so submit and poll judge the same words; unlike `innerText` it holds no sidebar
# titles.
CHAIN_FETCH_JS = """
async ({cid, qp}) => {
  const params = new URLSearchParams(qp).toString();
  const payload = {cmd: 3100, uplink_body: {pull_singe_chain_uplink_body: {
      conversation_id: cid, anchor_index: 9007199254740991, conversation_type: 3,
      direction: 1, limit: 20, ext: {}, filter: {index_list: []}}},
      sequence_id: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
      channel: 2, version: "1"};
  try {
    const r = await fetch("/im/chain/single?" + params, {
      method: "POST", credentials: "include",
      headers: {"accept": "*/*", "agw-js-conv": "str",
                "content-type": "application/json; encoding=utf-8"},
      body: JSON.stringify(payload)});
    return await r.json();
  } catch (e) { return null; }
}
"""
# Who this cookie is. A GUEST tab also answers HTTP 200 with `message: "error"` and `data.name == "account_info_error"`:
# the caller reads `message` before it reads a name or an id.
PASSPORT_JS = """async (qp) => {
  try {
    const r = await fetch('/passport/account/info/v2/?' + new URLSearchParams(qp).toString(), {credentials: 'include'});
    const j = await r.json(); const d = j.data || {};
    return {message: String(j.message || ''), user_id: String(d.user_id_str || d.user_id || ''),
            name: String(d.name || d.screen_name || d.nickname || '')};
  } catch (e) { return null; }
}"""
# The account's plan, as the chat page itself asks it on load (POST `{}`, the plain query; measured 25/10): `data`
# carries `subs_status` ("free" on every account seen) and `has_active_subscription`.
# The fetch is aborted after `SUBSCRIPTION_TIMEOUT_S`, and the evaluate given up a little later as a backstop: a hung
# read must not hold the check's browser slot. Either way the read gives no tier and the check goes on.
SUBSCRIPTION_TIMEOUT_S = 8
SUBSCRIPTION_BACKSTOP_S = SUBSCRIPTION_TIMEOUT_S + 2
SUBSCRIPTION_JS = """async ([qp, ms]) => {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  try {
    const r = await fetch('/alice/commerce/sale/subscription/entry/config/?' + new URLSearchParams(qp).toString(),
      {method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'}, body: '{}',
       signal: ac.signal});
    if (!r.ok) return null;
    const j = await r.json();
    return j && j.code === 0 && j.data && typeof j.data === 'object' ? j.data : null;
  } catch (e) { return null; } finally { clearTimeout(timer); }
}"""
# The number of reference-image previews in the composer. The browser hands out a `blob:` URL for each local file
# picked, whatever CSS class dola uses (the class-based count read 0 for good after dola renamed its classes,
# 23/09). Counting
# `img` does not work either: each image now has two.
CARD_COUNT_JS = r"""() => [...document.querySelectorAll('img')]
  .filter(i => (i.src || '').startsWith('blob:')).length"""
ATTACH_TRIES = 45            # ~31 s: N images through a slow proxy
# The wait for the mode button on a cold profile: its first load fetches dola's whole script bundle (~10 MB) through
# the proxy, and the 9 s of a warm page ran out before the row was there (23/10). The success path does not wait it
# out: the button is polled.
COLD_MODE_WAIT_S = 30
# On a 710022002 the receipt keeps looking for the conversation this long (it may appear a moment after the stream),
# so it is kept as evidence (or named, when the job goes to another account); the refusal is decided right after.
REFUSAL_RECEIPT_S = 10.0
# The conversation reads of the page and of the engine (`CHAIN_FETCH_JS`, the login probe): POSTs that read, and the
# only POSTs the traffic watch does not count. The order itself goes elsewhere (`/chat/completion`).
READ_PATHS = frozenset({SINGLE_PATH, "/im/chain/recent_conv", "/passport/account/info/v2/"})


# ---- pure helpers ------------------------------------------------------------------------------------------------
def is_upload_commit(method: str, url: str) -> bool:
    """The request that COMMITS one reference-image upload: a POST to imagex, after the file went to the store.

    A thumbnail is NOT the signal (measured 23/09, two ~1.5 MB images over a proxy: thumbnails at 1.1 s, the file in the
    store at 2.4-4.8 s, the commit at 5.3 s; the DOM shows nothing in between). Sending in that gap sends an image dola
    does not have yet. The chain is: prepare_upload (POST, dola), imagex (GET, reserve), store (POST, the file), imagex
    (POST, commit). Only the last counts."""
    return method == "POST" and "imagex" in urlsplit(url).netloc


def keep_taken_order(verdict, taken: bool):
    """An order dola took stays taken whatever a later message says: "daily limit" answers an EXTRA send, "something
    went wrong" a different message, an aside says nothing. A refusal still wins: it carries the acceptance
    boilerplate and never delivers. Without this a taken (paid) order was dropped and its videos lost (25/09: 4
    conversations, 5 videos of 30 s)."""
    if taken and verdict.outcome in (None, classify.SubmitOutcome.NEEDS_CONFIRM, classify.SubmitOutcome.NO_POINTS,
                                     classify.SubmitOutcome.ERROR):
        return classify.ReplyVerdict(classify.SubmitOutcome.OK, "dola accepted the request", verdict.credits_left)
    return verdict


# ---- reading the conversation as MESSAGES ------------------------------------------------------------------------
# One dola reply is one assistant MESSAGE, which may hold several text blocks and blank lines of its own: it is judged
# whole, never split on blank lines, and never found again by diffing text.
class Msg(NamedTuple):
    id: str
    user: bool
    text: str              # an assistant message: all its text blocks, joined; a user message: the customer's words
    final: bool            # the MESSAGE's own `ext.is_finish == "1"`: dola's flag that the reply is complete


class Thread(NamedTuple):
    msgs: tuple[Msg, ...]       # oldest first, each assistant message id once
    sent_text: str

    @property
    def ours(self) -> list[int]:
        """The positions of the job's sends: the user messages whose text IS the sent text as typed (typographic quotes,
        invisible characters and NFKC folded as the receipt folds them), bar dola's own decoration of its bubble. A
        longer prompt of another job that merely contains it is not this job's send."""
        typed = _norm(self.sent_text)
        return [i for i, m in enumerate(self.msgs) if typed and m.user and typed in _bubble_forms(m.text)]

    @property
    def sends_seen(self) -> int:
        return len(self.ours)


_norm = functools.lru_cache(maxsize=64)(norm_for_match)      # a 20 000-character prompt: normalised once
_BUBBLE_HEAD = re.compile(r"^generate[d]? (?:video|image):\s*")
_BUBBLE_TAIL = re.compile(r"(?:,\s*\d{1,2}:\d{1,2})?(?:,\s*\d+\s*s)?\s*$")


@functools.lru_cache(maxsize=64)
def _bubble_forms(text: str) -> frozenset:
    """A stored bubble as it is, without dola's tail (", 16:9"), without its head ("Generated video: "), and without
    both. The SENT text is never stripped: a prompt that itself begins with "Generate image:" is compared as typed."""
    whole = _norm(text)
    tail = _BUBBLE_TAIL.sub("", whole, count=1).strip()
    return frozenset((whole, tail, _BUBBLE_HEAD.sub("", whole, count=1).strip(),
                      _BUBBLE_HEAD.sub("", tail, count=1).strip()))


def _blocks(msg: dict) -> list[dict]:
    content = msg.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return []
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _final(msg: dict) -> bool:
    """dola's completion flag is the MESSAGE's `ext.is_finish == "1"`: on all 48 finished assistant messages of the
    captures (44 in fixtures/dola/chains, 4 in fixtures/dola/s1-network). A reply still being written says
    `is_finish: true` on its text block (the 'Video' read of t2v.json and i2v.json, two seconds before the full text)
    and has NO `ext.is_finish`, so a block flag settles nothing. Anything else is "not final"."""
    ext = msg.get("ext")
    return (isinstance(ext, dict) and ext.get("is_finish") == "1"
            and all(b.get("is_finish") is not False for b in _blocks(msg)))


def read_thread(body: dict | None, sent_text: str) -> Thread | None:
    """The messages of the engine's OWN chain read, oldest first, each assistant message id once (the first listing of
    an id wins: a message that reappears later is the same message, not a new reply). None when the read gave nothing or
    its order or its replies cannot be told (a malformed `index_in_conv`, an assistant message without an
    id: never guessed)."""
    if not isinstance(body, dict):
        return None
    raw = resolver.chain_messages(body)
    try:
        raw = sorted(raw, key=lambda m: int(m["index_in_conv"]))
    except (KeyError, TypeError, ValueError):
        return None
    out, seen = [], set()
    for m in raw:
        mid = str(m.get("message_id") or "")
        if resolver.is_user_message(m):
            text = resolver.user_text({"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [m]}}})
            out.append(Msg(mid, True, text, False))
        elif texts := resolver.message_texts(m):
            if not mid:
                return None                  # an id-less reply cannot be told from a repeat of an older one
            if mid in seen:
                continue
            seen.add(mid)
            out.append(Msg(mid, False, " ".join(texts), _final(m)))
    return Thread(tuple(out), sent_text)


_OPEN_QUOTES, _CLOSE_QUOTES = '"\u201c\u201d\u201e\u201f\u00ab', '"\u201c\u201d\u201e\u201f\u00bb'
_QUOTED = re.compile("[%s]([^%s]{3,}?)[%s]" % (_OPEN_QUOTES, _OPEN_QUOTES + "\u00bb", _CLOSE_QUOTES))


def without_echo(reply: str, sent_text: str) -> str:
    """The reply minus dola's ECHO of the customer's own words: a quoted fragment of at least TWO words that sits inside
    the sent text ("You asked for a sign saying "your video is ready""). One quoted word proves nothing (the prompt
    can say "Generate" and dola can say Reply "generate"), and dola's own lines are never dropped."""
    prompt = _norm(sent_text)
    if not prompt:
        return reply

    def echo(m):
        words = _norm(m.group(1))
        return " " if " " in words and words in prompt else m.group(0)
    return _QUOTED.sub(echo, reply)


class _Read(NamedTuple):
    taken: bool
    verdict: classify.ReplyVerdict


def _evaluate(text: str, new_composer: bool, want_duration: int | None) -> _Read:
    """What ONE reply, taken whole, says: is it an acceptance, and its verdict.
    - A refusal, an out-of-credit or a temporary error AND acceptance wording in the SAME reply contradict each other:
      the reply is undecided (not taken, no re-send, no refusal result, nothing rested); the poll decides. Without
      acceptance wording a refusal keeps its verdict.
    - Old composer: an acceptance with a question mark in the same reply is not taken, and is not a re-send either
      (a question we do not recognise is taught in code, from a real capture, never guessed)."""
    verdict = classify.classify_reply(text, want_duration=want_duration)
    taken = classify.order_taken(text, new_composer)
    refused = verdict.outcome in (classify.SubmitOutcome.NO_POINTS, classify.SubmitOutcome.CONTENT_REFUSED,
                                  classify.SubmitOutcome.ERROR)
    if classify.order_accepted(text) and refused:
        return _Read(False, classify.ReplyVerdict(None, "a refusal and an acceptance in one reply", None))
    if classify.order_accepted(text) and classify.says_transient(text):
        return _Read(False, classify.ReplyVerdict(None, "a temporary error and an acceptance in one reply", None))
    if refused:
        return _Read(False, verdict)
    if not new_composer and verdict.outcome is classify.SubmitOutcome.OK and ("?" in text or "\uff1f" in text):
        unknown = classify.ReplyVerdict(None, "an acceptance with a question we do not know", verdict.credits_left)
        return _Read(False, unknown)
    return _Read(taken, verdict)


def _read_reply(text: str, sent_text: str, new_composer: bool, want_duration: int | None) -> _Read:
    """A reply judged TWICE, raw and with the customer's quoted words removed. The same answer is used; a different
    one means the customer's own words decide it, so it decides nothing."""
    raw = _evaluate(text, new_composer, want_duration)
    clean_text = without_echo(text, sent_text)
    if clean_text == text:
        return raw
    clean = _evaluate(clean_text, new_composer, want_duration)
    if (raw.taken, raw.verdict.outcome) != (clean.taken, clean.verdict.outcome):
        return _Read(False, classify.ReplyVerdict(None, "the customer's own words decide it", None))
    return clean


class Judged(NamedTuple):
    usable: bool                       # the read held every send made so far (else it is stale or partial)
    taken: bool                        # dola took the order: some settled reply is an acceptance
    verdict: classify.ReplyVerdict     # of the newest settled reply after the latest send
    newest: str                        # that reply ("" when none)
    accepting: str                     # the newest accepting reply ("" when none)
    text: str                          # every reply, newest first (for the timeline)


UNREAD = Judged(False, False, classify.ReplyVerdict(None, "nothing usable read", None), "", "", "")


def judge(thread: Thread | None, sends_made: int, new_composer: bool, want_duration: int | None,
          history: tuple[Thread, ...] = ()) -> Judged:
    """The ONE judgement of what dola said, used at every decision point of the gate. Pure: no I/O, no page text.

    - USABLE only when the read holds at least `sends_made` of the job's user messages: fewer is a stale or partial
      read, which decides nothing (never a re-send, never a refusal). A failed read (None) is the same.
    - A reply is judged only when SETTLED: its message says `ext.is_finish == "1"`, or (no such flag) the same text
      under the same id stood in each of the last TWO usable reads in `history` too (three reads in a row). A reply
      settled only by repetition can be taken, a refusal or unclear, NEVER a question that earns a re-send.
    - The customer's own words never decide (`_read_reply`).
    - TAKEN: some settled reply AFTER the job's first send, taken WHOLE, is an acceptance by the composer's rule (old
      composer: acceptance boilerplate and a question in one reply is not taken; an acceptance in its own, later reply
      is). A refusal in the same reply wins. Once taken it stays taken, and nothing sends again.
    - VERDICT: that of the NEWEST reply after the newest user message (the latest send), when settled; a newer
      unfinished reply leaves the verdict undecided (a bounded wait, never a send).
      A re-send happens only when not taken and that verdict is NEEDS_CONFIRM (a question, or a stated other length
      without acceptance wording).
    - The balance of a taken order is read from the accepting reply and from nothing else."""
    ours = thread.ours if thread is not None else []
    if thread is None or len(ours) < sends_made:
        return UNREAD
    older = [{(m.id, m.text) for m in h.msgs if not m.user} for h in history[-2:]]

    def settled(m: Msg) -> bool:
        return m.final or (len(older) == 2 and all((m.id, m.text) in known for known in older))

    reads: dict[str, _Read] = {}

    def read(m: Msg) -> _Read:
        if m.id not in reads:
            reads[m.id] = _read_reply(m.text, thread.sent_text, new_composer, want_duration)
        return reads[m.id]

    mine = [m for m in thread.msgs[ours[0] + 1:] if not m.user]
    accepting = next((m for m in reversed(mine) if settled(m) and read(m).taken), None)
    last_user = max(i for i, m in enumerate(thread.msgs) if m.user)
    after = [m for m in thread.msgs[last_user + 1:] if not m.user]
    newest = after[-1] if after else None
    verdict = classify.ReplyVerdict(None, "no settled reply since the last send", None)
    if newest is not None and settled(newest):
        verdict = read(newest).verdict
        if verdict.outcome is classify.SubmitOutcome.NEEDS_CONFIRM and not newest.final:
            verdict = classify.ReplyVerdict(None, "a question dola did not mark finished", verdict.credits_left)
    if accepting is not None:
        verdict = verdict._replace(credits_left=read(accepting).verdict.credits_left)
    return Judged(True, accepting is not None, verdict, newest.text if newest else "",
                  accepting.text if accepting else "", "\n\n".join(m.text for m in reversed(mine)))


def reply_text(reply: str | None) -> str:
    """The dola reply with the white space inside each paragraph folded and the blank lines between replies kept (the
    deadline report reads the newest paragraph), the FIRST `constants.EVENT_TEXT_MAX` (2000) characters: the newest
    message comes first, so cutting the tail keeps this send's answer, and the core stores a longer event as a bare
    omitted marker."""
    paragraphs = (" ".join(p.split()) for p in (reply or "").split("\n\n"))
    return "\n\n".join(p for p in paragraphs if p)[:REPLY_CHARS]


def body_carries(body, sent_text: str, prompt: str) -> bool:
    """Does this conversation read hold the job's WHOLE sent text, in the user's words or anywhere in the thread (an i2v
    prompt travels as a text block, not as the plain user text). Whitespace is folded, as the composer flattens it."""
    if not isinstance(body, dict):
        return False
    texts = (resolver.user_text(body), resolver.conversation_text(body))
    return any(resolver.carries_text(t, sent_text, prompt) for t in texts)


def body_of(event) -> dict | None:
    """The JSON object a recorded read holds, None for anything else (an error page, a body that was cut)."""
    try:
        body = event.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def is_full_read(request: str) -> bool:
    """Was this chain read the full one (anchor at the end), as `CHAIN_FETCH_JS` asks?"""
    m = _ANCHOR.search(request or "")
    return bool(m) and int(m.group(1)) == FULL_READ_ANCHOR


def conversation_id_of(request: str) -> str | None:
    """The conversation a chain read asked for, from its request body."""
    m = _CONV_ID.search(request or "")
    return m.group(1) if m else None


def network_failure(text: str) -> bool:
    low = (text or "").lower()
    return any(mark in low for mark in NETWORK_MARKS)


class StreamError(NamedTuple):
    code: int
    message: str                  # dola's own words, white space folded, at most SSE_MESSAGE_CHARS ("" when none)


def sse_events(body: str) -> list[tuple[str, str]]:
    """The events of a server-sent-events body as (event name, data), the data lines of one event joined; comment
    lines (`:`) are skipped."""
    out, name, data = [], "", []
    for line in [*(body or "").splitlines(), ""]:
        if not line.strip():
            if data:
                out.append((name, "\n".join(data)))
            name, data = "", []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    return out


def _error_envelope(obj) -> dict | None:
    """dola's error envelope: an object with an integer `error_code` at its TOP level, or in its `event_data` (an
    object, or a JSON string of one: the nested shape). A code anywhere deeper (inside a message's content, an echo of
    the prompt) is never one."""
    if not isinstance(obj, dict):
        return None
    inner = obj.get("event_data")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            inner = None
    for env in (obj, inner):
        code = env.get("error_code") if isinstance(env, dict) else None
        if (isinstance(code, int) and not isinstance(code, bool)) or (isinstance(code, str) and code.isdigit()):
            return env
    return None


def stream_error(body: str) -> StreamError | None:
    """dola's error for a send, read from its `/chat/completion` stream, parsed event by event: the first error
    envelope, and only when the stream delivered NOTHING else. An event that is not a heartbeat, an acknowledgement, an
    empty object or `[DONE]` is a delivery (unreadable data too), and a stream with one is no error: an error code
    then a reply, or a code inside the reply's text, never ends a job."""
    error = None
    for name, data in sse_events(body):
        if data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except ValueError:
            return None                       # unreadable data: it may have been a delivery
        env = _error_envelope(obj)
        if env is not None:
            if error is None:
                said = env.get("error_msg")
                said = " ".join(said.split())[:SSE_MESSAGE_CHARS] if isinstance(said, str) else ""
                error = StreamError(int(env["error_code"]), said)
            continue
        if name in QUIET_EVENTS or obj == {}:
            continue
        return None
    return error


def sse_message(body: str) -> str:
    """dola's words next to its error code in a stream ("" when none, or when the stream is no error)."""
    error = stream_error(body)
    return error.message if error else ""


_ORDER_REQUEST = re.compile(r"/chat/|completion")


def may_carry_order(url: str) -> bool:
    """Could this GET carry the order? Any URL naming a completion or with `/chat/` in it, path or query (a send
    routed by a query parameter), on any host. Any request that is not a GET counts whatever its URL
    (`TrafficWatch`)."""
    return bool(_ORDER_REQUEST.search(url or ""))


# ---- what the engine reads off the job -----------------------------------------------------------------------------
@dataclass(frozen=True)
class OrderSpec:
    kind: str                      # "video" | "image": also the pool of the job
    model: str
    duration: int | None
    ratio: str | None
    style: str | None
    sent_text: str
    prompt: str

    @property
    def needs_patch(self) -> bool:
        return self.kind == "video" and self.duration not in NATIVE_DURATIONS


def order_spec(job) -> OrderSpec:
    """Read the plan's params (`kind`, `model`, `duration`, `ratio`, `style`) and the core's `sent_text`. The engine
    never composes the text it sends: a job without one is a core bug and is refused before anything is sent."""
    params = job.params
    kind = params.get("kind")
    if kind not in ("video", "image"):
        raise ValueError("the job's plan names no kind (video or image)")
    if not job.sent_text:
        raise ValueError("the job has no sent_text: the core composes it")
    if kind == "video":
        return OrderSpec("video", params["model"], int(params["duration"]), params.get("ratio") or DEFAULT_RATIO,
                         None, job.sent_text, job.prompt)
    return OrderSpec("image", params["model"], None, params.get("ratio") or None, params.get("style") or None,
                     job.sent_text, job.prompt)


# ---- the page driver (the seam a test replaces) ------------------------------------------------------------------
@dataclass(frozen=True)
class Built:
    """What building the composer gave: its steps (for the timeline) and whether the account has the NEW composer (the
    answer `order_taken` needs), None when the page did not say (image mode)."""
    steps: tuple[str, ...]
    new_composer: bool | None


class NavWatch:
    """Counts document loads of the main frame (a reload, a navigation) since it was made. A same-document move (the
    page changing its URL itself) is not one."""

    def __init__(self, page) -> None:
        self._page, self._loads = page, 0
        page.on("domcontentloaded", self._on_load)

    def _on_load(self, *_args) -> None:
        self._loads += 1

    def moved(self) -> bool:
        return self._loads > 0

    def stop(self) -> None:
        self._page.remove_listener("domcontentloaded", self._on_load)


class TrafficWatch:
    """Everything the browser context sends that could carry an order, from `arm()` (right before the Enter) until
    `freeze()` (after the browser was closed, see `_proven_not_sent`): any request that is not a GET, on any host, from
    any page, popup or worker the context reports (the context's own `request` event), bar the conversation reads
    (`READ_PATHS`); a GET whose full URL names a send (`may_carry_order`); any WebSocket frame sent by any page, also
    a page opened later. Watched from the moment the session opens, so a
    WebSocket opened before the send is seen too. Fail-safe: a context that could not be watched, a page or socket it
    could not attach to, or a listener that failed is a BLIND watch, which counts as traffic. Only a watch that saw
    nothing proves the order never left (`seen()` False).

    ponytail: a WebSocket of a dedicated worker is not reported by Playwright, so it is not seen; dola sends over
    `POST /chat/completion` (measured), which the context does report."""

    def __init__(self, ctx) -> None:
        self._ctx, self.count, self.blind = ctx, 0, False
        self.armed = self.frozen = False
        self._pages: list = []
        self._sockets: list = []
        try:
            ctx.on("request", self._on_request)
            ctx.on("page", self._on_page)
            for page in list(ctx.pages):
                self._on_page(page)
        except Exception:      # noqa: BLE001 - not watched is not "nothing left"
            self.blind = True

    def _hit(self) -> None:
        if self.armed and not self.frozen:
            self.count += 1

    def _on_request(self, request) -> None:
        try:
            carries = ((request.method != "GET" and urlsplit(request.url).path not in READ_PATHS)
                       or may_carry_order(request.url))
        except Exception:      # noqa: BLE001 - an unreadable request may have been the order
            carries = True
        if carries:
            self._hit()

    def _on_page(self, page) -> None:
        try:
            page.on("websocket", self._on_socket)
            self._pages.append(page)
        except Exception:      # noqa: BLE001
            self.blind = True

    def _on_socket(self, ws) -> None:
        try:
            ws.on("framesent", self._on_frame)
            self._sockets.append(ws)
        except Exception:      # noqa: BLE001
            self.blind = True

    def _on_frame(self, *_args) -> None:
        self._hit()

    def arm(self) -> None:
        self.armed, self.frozen = True, False

    def freeze(self) -> None:
        self.frozen = True

    def seen(self) -> bool:
        return self.blind or not self.armed or self.count > 0

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._ctx.remove_listener("request", self._on_request)
            self._ctx.remove_listener("page", self._on_page)
        for page in self._pages:
            with contextlib.suppress(Exception):
                page.remove_listener("websocket", self._on_socket)
        for ws in self._sockets:
            with contextlib.suppress(Exception):
                ws.remove_listener("framesent", self._on_frame)


class PageDriver:
    """The real page actions. A test subclasses it and answers from a script, so no Chrome is needed."""

    def watch_navigation(self, page) -> NavWatch:
        return NavWatch(page)

    def watch_traffic(self, session) -> TrafficWatch:
        return TrafficWatch(session.ctx)

    async def halt(self, session) -> bool:
        """Make every further send of this browser impossible: close the whole browser (its context and process), so
        no page, service worker, keep-alive request or beacon of dola's is left. The session is closed for good; its
        own exit does not touch it again. True only when the close itself completed."""
        return await session.close_now()

    async def inject(self, session, account) -> list[str]:
        """Put the account's cookies into the context and read the identity cookies back (a half-done injection would
        send as one account and charge another). Returns the identity names that were verified in this browser."""
        return await inject_cookies(session.ctx, jar_from_blob(account.credential), IDENTITY_COOKIE_NAMES, BASE)

    async def identity_cookie_names(self, session) -> set[str] | None:
        """The names of the identity cookies the browser holds now; None when it could not be asked."""
        try:
            cookies = await asyncio.wait_for(session.ctx.cookies(BASE), COOKIES_TIMEOUT_S)
            return {c["name"] for c in cookies} & set(IDENTITY_COOKIE_NAMES)
        except Exception:      # noqa: BLE001 - no answer (or none in time) is no conclusion
            return None

    async def open_chat(self, page) -> None:
        await self.settle(page, goto=True)

    async def settle(self, page, *, goto: bool = False) -> None:
        """Wait for a quiet page (see SETTLE_*): after `goto` on the first load, or alone after a page reloaded under
        the run."""
        moved: list[bool] = []

        def on_navigated(frame) -> None:
            if frame == page.main_frame:
                moved.append(True)

        page.on("framenavigated", on_navigated)
        try:
            if goto:
                await page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
            moved.clear()
            elapsed = quiet = 0.0
            while elapsed < SETTLE_CAP_S and (elapsed < SETTLE_MIN_S or quiet < SETTLE_QUIET_S):
                await page.wait_for_timeout(SETTLE_STEP_MS)
                elapsed += SETTLE_STEP_MS / 1000
                quiet += SETTLE_STEP_MS / 1000
                if moved:
                    moved.clear()
                    quiet = 0.0
        finally:
            page.remove_listener("framenavigated", on_navigated)

    async def probe(self, page) -> dict:
        return await page.evaluate(LOGIN_PROBE_JS)

    async def page_url(self, page) -> str:
        return page.url or ""

    async def read_chain(self, page, conversation_id: str) -> dict | None:
        """One read of a conversation through dola's API from inside the page; None when it gave nothing."""
        try:
            body = await page.evaluate(CHAIN_FETCH_JS, {"cid": conversation_id, "qp": query_params()})
        except Exception:      # noqa: BLE001 - the page is busy or closing: the caller reads again
            return None
        return body if isinstance(body, dict) else None

    async def build(self, page, spec: OrderSpec, *, cold: bool = False) -> Built:
        """The whole composer, with a reload and a retry when the chip row has not rendered (it misses ~50% of first
        tries: late hydration). The net wraps the WHOLE group (mode, model, duration, ratio): all steps read one
        chip row and fail for one reason; wrapping only the first step moved the failure to `duration chip not
        found` (22/09). A `cold` profile (no HTTP cache: the first run after a new credential) waits up to
        COLD_MODE_WAIT_S for the mode button before it gives up on a try (23/10: the button was not there yet)."""
        steps: list[str] = []
        wait = {"settle_s": COLD_MODE_WAIT_S} if cold else {}
        if spec.kind == "image":
            async def group(p):
                chip = await composer.enter_image_mode(p, **wait)
                # The cookies popup blocks the first chip click (measured 260918): close it after EVERY reload.
                await composer.dismiss_consent(p)
                return (chip, await composer.select_image_model(p, spec.model),
                        await composer.select_image_ratio(p, spec.ratio) if spec.ratio else None,
                        await composer.select_image_style(p, spec.style) if spec.style else None)
            chip, model, ratio, style = await self._robust(page, group, "image composer")
            steps += ["image mode: chip %s" % chip, "model: %s" % model]
            steps += ["ratio: %s" % ratio] if ratio else []
            steps += ["style: %s" % style] if style else []
            return Built(tuple(steps), None)

        async def group(p):
            return (await composer.enter_video_mode(p, **wait), await composer.select_video_model(p, spec.model),
                    *await composer.select_video_ratio_duration(p, spec.ratio, spec.duration))
        chip, model, duration, ratio, new_composer = await self._robust(page, group, "video composer")
        steps += ["video mode: chip %s" % chip, "model: %s" % model, "duration: %s" % duration, "ratio: %s" % ratio]
        return Built(tuple(steps), new_composer)

    async def rebuild(self, page, spec: OrderSpec) -> Built:
        """The full parameters again inside the conversation already sent, for a re-send. Video mode may still be on
        after the first send (pressing "Create Videos" then TURNS IT OFF), so enter it only when the chip row is
        missing."""
        async def group(p):
            if not await composer._wait_rect(p, composer.MODEL_CHIP, timeout_s=3):
                await composer.enter_video_mode(p)
            return (await composer.select_video_model(p, spec.model),
                    *await composer.select_video_ratio_duration(p, spec.ratio, spec.duration))
        model, duration, ratio, new_composer = await self._robust(page, group, "video composer (re-send)")
        return Built(("re-send composer: %s | %s | %s" % (model, duration, ratio),), new_composer)

    async def _robust(self, page, build, label: str, attempts: int = 3):
        """Run `build`; on a composer error reload and try again. Each step is "set if not already right", so running it
        again on a freshly loaded page gives the same state and adds nothing. The reload waits 60 s like `goto`: the
        default 30 s failed a whole batch when dola was slow for everybody (24/09)."""
        for k in range(attempts):
            try:
                return await build(page)
            except composer.ComposerError:
                if k == attempts - 1:
                    raise
                log.info("%s not rendered (try %d): reload and retry", label, k + 1)
                await page.reload(wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(4000)

    async def attach(self, page, images, *, raise_navigation: bool = True) -> str:
        """Attach N reference images (`[(bytes, mime)]`). Returns "" when exactly N were attached and COMMITTED, else
        the reason. dola takes several images (verified 260917). The files are chosen in ONE `set_input_files`
        (the trusted
        CDP event the Semi upload listens to; a native-setter script does not fire its onChange)."""
        if not images:
            return ""
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}
        paths: list[str] = []
        try:
            paths = await asyncio.to_thread(_write_temp_files, images, ext)
            loc = page.locator('input[type="file"]')
            if not await loc.count():
                return "no file input in composer"
            n = len(images)
            commits: list[str] = []          # listen BEFORE choosing the files: no commit is missed

            def listen(req):
                if is_upload_commit(req.method, req.url):
                    commits.append(req.url)
            page.on("requestfinished", listen)
            try:
                await loc.first.set_input_files(paths)
                for _ in range(ATTACH_TRIES):
                    await page.wait_for_timeout(700)
                    if len(commits) >= n and (await page.evaluate(CARD_COUNT_JS)) >= n:
                        return ""
                got = await page.evaluate(CARD_COUNT_JS)
            finally:
                page.remove_listener("requestfinished", listen)
            # A missing image is NOT sent: the old "at least one" rule delivered a video made from other material than
            # the client's, charged, and the difference reached no data and no API.
            return "only %d/%d thumbnails appeared, %d/%d uploads committed" % (got, n, len(commits), n)
        except Exception as exc:      # noqa: BLE001 - a failed attach is a reason, not a crash
            if raise_navigation and PAGE_NAVIGATED in str(exc).lower():
                raise          # the page moved under the run: the in-open recompose handles it, not an attach reason
            return "attach error: %s" % str(exc)[:120]
        finally:
            await asyncio.to_thread(_remove_files, paths)

    async def send(self, page, text: str, *, before_enter) -> None:
        """Type the text, `before_enter()`, press Enter once (see `composer.send_prompt`): the Send button is never
        clicked."""
        await composer.send_prompt(page, text, before_enter=before_enter)

    # ---- what the account check reads (check.py) ----
    async def passport(self, page) -> dict | None:
        """Who the cookie is, from dola's passport API called inside the page: `{message, user_id, name}`."""
        try:
            return await page.evaluate(PASSPORT_JS, query_params())
        except Exception:      # noqa: BLE001 - nothing is concluded from a failed call
            return None

    async def subscription(self, page) -> dict | None:
        """The account's plan from dola's subscription config, called inside the page (`data`); None when unread."""
        try:
            read = page.evaluate(SUBSCRIPTION_JS, [query_params(), SUBSCRIPTION_TIMEOUT_S * 1000])
            return await asyncio.wait_for(read, SUBSCRIPTION_BACKSTOP_S)
        except Exception:      # noqa: BLE001 - nothing is concluded from a failed call
            return None

    async def screenshot(self, page) -> bytes | None:
        try:
            return await page.screenshot(full_page=False)
        except Exception:      # noqa: BLE001 - a picture is a bonus, never a reason to fail the check
            return None

    async def composer_kind(self, page) -> str | None:
        """`old` or `new`, from the chips of video mode (pressing "Create Videos" sends nothing); None if unreadable."""
        try:
            await composer.enter_video_mode(page)
            return await composer.read_composer_kind(page)
        except composer.ComposerError:
            return None


def _write_temp_files(images, ext: dict) -> list[str]:
    paths = []
    for data, mime in images:
        fd, path = tempfile.mkstemp(suffix=ext.get(mime, ".png"))
        paths.append(path)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    return paths


def _remove_files(paths: list[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


_store = None


async def read_input_media(ref) -> bytes:
    """One job input image from the object store, the store created on first use (a run without references needs none).

    The one place the engine reaches past `RunCtx` for a file: the protocol hands out only a `MediaRef`. A v2 protocol
    would give the engine `ctx.read_input(ref)` and this function would go."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


@dataclass
class Deps:
    """What a run needs from outside; every field has the real default."""
    open_session: Callable = open_session
    driver: PageDriver = field(default_factory=PageDriver)
    read_media: Callable[..., Awaitable[bytes]] = read_input_media
    sleep: Callable[[float], Awaitable] = asyncio.sleep
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    clock: Callable[[], float] = time.monotonic
    poll_s: float = POLL_S
    image_poll_s: float = IMAGE_POLL_S
    image_hold_s: float = IMAGE_HOLD_S
    http: Callable[..., DolaHttp] = DolaHttp.for_account     # the HTTP send path: the account's client
    signer: Callable[[str], object] | None = None             # the HTTP send path: a signer for a User-Agent


class _CoreVerdict(Exception):
    """An exception from `ctx.mark_sent` (a lost lease, a stopping worker): it is the core's own verdict and must reach
    the core unchanged, not be turned into a `Retry`."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


class _SendBlocked(Exception):
    """`ctx.fence` refused right before an irreversible send action: that action did not run."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


def _outcome(job, *effects, warnings=()) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects), warnings=tuple(warnings))


# ---- the run -----------------------------------------------------------------------------------------------------
class _Run:
    def __init__(self, ctx, job, settings, deps: Deps):
        self.ctx, self.job, self.settings, self.deps = ctx, job, settings, deps
        self.driver = deps.driver
        self.spec = order_spec(job)
        self.started = deps.clock()
        self.watch: NavWatch | None = None     # sees the page load again between the compose and the send
        self.traffic: TrafficWatch | None = None    # what the browser sent from the Enter on
        self.logged_in = False            # True once the login read said logged in: the strike count is then cleared
        self.injected: list[str] = []     # the identity cookie names `inject` verified in this browser
        self.sent = False                 # True from the moment `mark_sent` returned: the order may be at dola
        self.steps: list[str] = []
        self.conversation: str | None = None
        self.session = None
        self.page = None
        self.sends_made = 0               # Enter presses so far for this job (a read must hold this many sends)
        self.history: list[Thread] = []   # the last two usable reads, for a reply that carries no completion flag
        self.since = 0.0                  # recorder time just before the send
        self.new_composer: bool | None = None

    # ---- platform helpers (best effort: diagnostics never fail the order) ----
    async def _say(self, text: str, stage: str, source: str) -> None:
        try:
            await self.ctx.event(text, stage, source)
        except Exception as exc:      # noqa: BLE001
            log.warning("event %s not recorded: %s", stage, str(exc)[:80])

    async def _evidence(self, items) -> None:
        for item in items:
            try:
                await self.ctx.evidence(item.name, item.data, item.mime)
            except Exception as exc:      # noqa: BLE001
                log.warning("evidence %s not stored: %s", item.name, str(exc)[:80])

    async def _ui_error_evidence(self, tag: str) -> None:
        if self.session is None or self.page is None:
            return
        try:
            await self._evidence(await self.session.evidence.ui_error(self.page, tag))
        except Exception as exc:      # noqa: BLE001
            log.warning("ui-error evidence not taken: %s", str(exc)[:80])

    async def _flush_steps(self) -> None:
        if self.steps:
            await self._say("\n".join(self.steps), "compose", "engine")
            self.steps = []

    # ---- reading the conversation ----
    def _latest_chain(self, conversation: str) -> dict | None:
        """The newest FULL read of THIS conversation the page itself made, from the recorder. The page's incremental
        reads (a small anchor) carry the customer's own message as text blocks: no decision rests on them."""
        for ev in reversed(self.session.recorder.events(self.since)):
            if (ev.path == SINGLE_PATH and ev.status == 200 and is_full_read(ev.req)
                    and conversation_id_of(ev.req) == conversation):
                if (body := body_of(ev)) is not None:
                    return body
        return None

    async def _chain(self, conversation: str, *, fresh: bool) -> dict | None:
        """A read of the conversation. The page's own traffic first (the same data, no extra call); `fresh` asks dola
        directly first, for the reads a decision rests on."""
        if fresh:
            return await self.driver.read_chain(self.page, conversation) or self._latest_chain(conversation)
        return self._latest_chain(conversation) or await self.driver.read_chain(self.page, conversation)

    async def _reply(self, conversation: str, *, fresh: bool = False) -> str:
        body = await self._chain(conversation, fresh=fresh)
        return resolver.conversation_text(body) if body else ""

    async def _judged(self, conversation: str) -> Judged:
        """The engine's OWN read of the conversation, judged. No fallback to what the page last read (that can be older
        than what was judged already), and no page text: a failed read judges as nothing, which never earns a send."""
        body = await self.driver.read_chain(self.page, conversation)
        thread = read_thread(body, self.spec.sent_text)
        out = judge(thread, self.sends_made, self._composer_is_new(), self.spec.duration, tuple(self.history))
        if out.usable:
            self.history = [*self.history, thread][-2:]
        return out

    async def _refresh(self, conversation: str, current: Judged) -> Judged:
        """A new judgement, or `current` when the read was not usable (a stale or failed read keeps what is known)."""
        latest = await self._judged(conversation)
        return latest if latest.usable else current

    def _composer_is_new(self) -> bool:
        # Which composer the account has decides what counts as "taken"; unknown reads as the new one: taken means NO
        # re-send, the money-safe side.
        return True if self.new_composer is None else self.new_composer

    def _stream_errors(self) -> list[tuple[StreamError, str]]:
        """dola's error of every `/chat/completion` stream since the first send that is an error (`stream_error`), with
        that stream's body, oldest first."""
        out = []
        for ev in self.session.recorder.events(self.since):
            if ev.path == COMPLETION_PATH and (error := stream_error(ev.body)) is not None:
                out.append((error, ev.body))
        return out

    def _sse_error(self) -> int | None:
        errors_seen = self._stream_errors()
        return errors_seen[0][0].code if errors_seen else None

    def _refusal(self) -> str | None:
        """The body of a stream of this job's sends that refused the order with REFUSED_CODE; None when none did. Every
        stream is read, the first send's and each re-send's."""
        return next((body for error, body in self._stream_errors() if error.code == REFUSED_CODE), None)

    def _order_may_have_left(self) -> bool:
        """May the order have reached dola? Yes when the browser sent anything that could carry it since the Enter
        (`TrafficWatch`, blind or unarmed included), or the recorder holds a `/chat/completion` answer since the send.
        No only when both saw nothing (decided for good only by `_proven_not_sent`)."""
        if self.traffic is None or self.traffic.seen():
            return True
        return any(ev.path == COMPLETION_PATH for ev in self.session.recorder.events(self.since))

    # ---- the order ----
    async def run(self) -> SubmitResult:
        spec, ctx = self.spec, self.ctx
        images = await self._load_images()
        patches = (duration_response_patch(spec.duration),) if spec.needs_patch else ()
        site = DOLA_SITE if spec.kind == "image" else replace(DOLA_SITE, viewport=None)
        async with self.deps.open_session(site, ctx.account, purpose="submit", token=ctx.browser_token,
                                          images=bool(images), response_patches=patches, redact=redact_text) as session:
            self.session, self.page = session, session.page
            return await self._order(images)

    async def _load_images(self) -> list[tuple[bytes, str]]:
        """The reference images, read BEFORE the browser opens: a store that is down means nothing was sent."""
        refs = await self.job.inputs()
        return [(await self.deps.read_media(ref), ref.mime) for ref in refs]

    async def _order(self, images) -> SubmitResult:
        # Watched from the open, before any page loads: a WebSocket the page opens early is seen when it sends.
        self.traffic = self.driver.watch_traffic(self.session)
        try:
            return await self._order_steps(images)
        finally:
            self.traffic.stop()

    async def _order_steps(self, images) -> SubmitResult:
        ctx, driver, page = self.ctx, self.driver, self.page
        self.injected = list(await driver.inject(self.session, ctx.account))
        await driver.open_chat(page)

        # dola sends an account whose proxy is in the wrong region to a region-restricted URL (the app never loads
        # there; the recent-conversations API still answers, so the login probe cannot see it). It is the PROXY's fault.
        if REGION_BLOCK_MARK in await driver.page_url(page):
            return self._region_blocked("the proxy IP is the wrong region for this account")

        try:
            return await self._compose(images)
        except Exception as exc:      # noqa: BLE001 - only a navigation before the send is absorbed; the rest goes on
            if self.sent or PAGE_NAVIGATED not in str(exc).lower():
                raise
            # The page navigated under the run before anything was sent (a reload that landed after the settle wait):
            # load the chat page afresh (a settle alone would keep the images already attached) and compose once more
            # in this same open. A second navigation is not caught here and ends as `dola.page_reloaded`; nothing is
            # sent twice because nothing was sent.
            await self._say("the page reloaded while composing: waiting for it, then composing again", "submit",
                            "engine")
            await driver.open_chat(page)
            if REGION_BLOCK_MARK in await driver.page_url(page):
                return self._region_blocked("the page moved to dola's region page while composing")
            return await self._compose(images)

    async def _compose(self, images) -> SubmitResult:
        """The order from the login read to the end. Before `mark_sent` it can be run a second time in the same open.
        A document load of the page from the end of the build until the send is a navigation under the run (see
        `_mark_sent`)."""
        self.watch, self.logged_in = None, False
        try:
            return await self._compose_steps(images)
        finally:
            if self.watch is not None:
                self.watch.stop()

    async def _compose_steps(self, images) -> SubmitResult:
        spec, ctx, driver, page = self.spec, self.ctx, self.driver, self.page
        probe = await driver.probe(page)
        state = login_state(probe)
        self.steps.append("login: %s" % state.reason)
        if not state.logged_in:
            present = await driver.identity_cookie_names(self.session)
            if present is None:
                return self._cookies_fault("cookies not readable")
            lost = lost_identity_cookies(present, self.injected)
            if lost:
                return self._cookies_lost(lost)
            return self._not_logged_in(state)
        self.logged_in = True
        baseline = set(probe.get("ids") or [])

        try:
            built = await driver.build(page, spec, cold=self.session.cold)
            self.watch = driver.watch_navigation(page)     # not before: the build reloads the page itself when it must
        except composer.ComposerError as exc:
            await self._ui_error_evidence("%s-composer" % spec.kind)
            if spec.needs_patch and self.session.patched == 0:
                # The level is missing because the patch never reached the page: name the cause, not the chip.
                return await self._unsent(errors.UI_DURATION_PATCH_FAILED,
                                          "composer: %s (the action-bar config was not patched)" % exc)
            return await self._unsent(exc.code, "composer: %s" % exc)
        self.steps += built.steps
        # The composer the page SHOWED this time decides what "taken" means; the account's recorded composer (set by the
        # account check) only fills in when the page did not say.
        self.new_composer = (built.new_composer if built.new_composer is not None
                             else ctx.account.provider_state.get("composer") == "new")
        if spec.needs_patch and self.session.patched == 0:
            # No 30 s level means the patch never reached the page: stop, never send on and deliver a 10 s video.
            return await self._unsent(errors.UI_DURATION_PATCH_FAILED,
                                      "the action-bar config was not patched: the %d s level is not there"
                                      % spec.duration)

        if images:
            reason = await driver.attach(page, images)
            if reason:
                await self._ui_error_evidence("%s-attach" % spec.kind)
                return await self._unsent(errors.UI_ATTACH_FAILED,
                                          "could not attach the reference images: %s (not sent: a wrong-mode charge)"
                                          % reason)

        # `mark_sent` runs inside the send, right before Enter: an error after it is "unknown", before it (the prompt
        # would not go into the input) an ordinary retry.
        self.since = self.session.recorder.now()
        try:
            await self._send_once()
        except composer.ComposerError as exc:
            await self._ui_error_evidence("%s-send" % spec.kind)
            if not self.sent:
                return await self._unsent(exc.code, "the prompt did not go into the input: %s" % exc)
            if await self._proven_not_sent():
                # Enter was pressed, the prompt stayed in the input, and the page asked dola nothing: provably not
                # sent (23/10: reconcile took 11 min to prove it). Nothing more is pressed in this session.
                return await self._not_sent("the prompt stayed in the input and the browser sent nothing that could "
                                            "carry the order: not sent (%s)" % exc)
            return await self._after_send_failure("the send did not complete: %s" % exc)

        conversation = await self._receipt(baseline)
        refused = self._refusal()
        if refused is not None:
            return await self._refused_by_code(refused, conversation)
        if conversation is None:
            return await self._no_conversation()
        self.conversation = conversation
        await ctx.report_ref(conversation, {})
        return await self._answer(conversation, images)

    async def _send_once(self) -> None:
        """The first send. The traffic watch keeps counting after it: a send the page fires late (the input is given
        30 s to clear) is still seen (`_proven_not_sent`)."""
        await self.driver.send(self.page, self.spec.sent_text, before_enter=self._mark_sent)

    async def _proven_not_sent(self) -> bool:
        """Is it PROVEN that the order never left? Only on a browser that no longer exists: the whole browser is closed
        first (`driver.halt`: every page, service worker, keep-alive request and beacon dies with it), the watch keeps
        counting until the close has completed, and only then is it read and frozen. Traffic seen at any point, a close
        that fails or cannot be confirmed: not proven (the order may have left; the job stays unknown). The session is
        gone afterwards, which is fine: this run ends here either way."""
        if self._order_may_have_left():
            return False
        try:
            halted = await self.driver.halt(self.session)
        except Exception as exc:      # noqa: BLE001 - not confirmed is not proven
            log.info("could not close the browser before the not-sent proof: %s", str(exc)[:80])
            halted = False
        if not halted:
            await self._say("the browser could not be closed cleanly, so a late send cannot be ruled out", "submit",
                            "engine")
            return False
        if self.traffic is not None:
            self.traffic.freeze()
        return not self._order_may_have_left()

    async def _mark_sent(self) -> None:
        """Called by the driver immediately before Enter: from here dola MAY have the order."""
        if self.watch is not None and self.watch.moved():
            # The page loaded again while the prompt was typed (a reload that threw nothing): the composer the run built
            # is gone, and nothing was sent. Treated like any navigation before the send.
            raise RuntimeError("Execution context was destroyed: the page loaded again before the send")
        if self.traffic is not None:
            self.traffic.arm()             # from here everything the browser sends may be the order
        await self._flush_steps()          # before mark_sent: nothing may come between mark_sent and the keypress
        await self._core(self.ctx.mark_sent)
        self.sent = True
        self.sends_made = 1               # counted AT the Enter

    async def _fence_for_send(self) -> None:
        """Called by the driver before a re-send's Enter: a lost
        lease, a stopping worker or a DB error all mean NO further send."""
        try:
            await self.ctx.fence()
        except Exception as exc:      # noqa: BLE001
            raise _SendBlocked(exc) from exc
        self.sends_made += 1              # counted AT the Enter

    async def _core(self, fn) -> None:
        """Call a `ctx` irreversible-action gate; whatever it raises is the core's verdict and is passed on as it is."""
        try:
            await fn()
        except Exception as exc:
            raise _CoreVerdict(exc) from exc

    # ---- results before the send ----
    async def _unsent(self, code: str, why: str) -> SubmitResult:
        """Nothing was sent: the order goes back to the queue (`Retry` is legal only here)."""
        await self._flush_steps()
        await self._say(why, "submit", "engine")
        return _outcome(Retry(code))

    def _region_blocked(self, why: str) -> SubmitResult:
        until = self.deps.now() + timedelta(seconds=constants.THROTTLE_COOLDOWN_S)
        return _outcome(NotTaken(POOL_REGION_BLOCKED),
                        ProxyBad("region blocked: %s" % why), Cooldown("region blocked", until))

    def _cookies_lost(self, lost: list[str]) -> SubmitResult:
        # The cookies were injected and read back, and now they are gone: the browser lost them (a browser fault). The
        # page reads as a guest, but dola said nothing about the account: no strike, no verdict. The run goes back to
        # the queue without using an attempt, and the account rests like after a login doubt.
        self.steps.append("cookies lost in the browser: %s" % ", ".join(lost))
        return _outcome(Retry(errors.BROWSER_COOKIES_LOST),
                        *cookies_lost_effects(self.deps.now()))

    def _cookies_fault(self, why: str) -> SubmitResult:
        # The browser could not be asked for its cookies: a browser fault like a failed read, no strike, a short rest.
        self.steps.append(why)
        until = self.deps.now() + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S)
        return _outcome(Retry(errors.BROWSER_COOKIES_LOST), Cooldown(why, until))

    def _not_logged_in(self, state: LoginState) -> SubmitResult:
        # A cookie that was just injected and read back, and dola says it belongs to nobody (a guest read), or shows a
        # dialog, or shows no conversation and no name: every one is a doubt and never a verdict: one spaced
        # strike and a rest. A failed read is no strike, but the account rests briefly so the run does not spin on it.
        if state.signed_out or state.cells >= 0 or state.modal_visible:
            effects = login_doubt_effects(self.deps.now(), state.reason)
        else:
            until = self.deps.now() + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S)
            effects = (Cooldown("login read failed", until),)
        return _outcome(Retry(POOL_LOGGED_OUT), *effects)

    # ---- the receipt ----
    async def _receipt(self, baseline: set[str]) -> str | None:
        """The conversation of this send: the first `/im/chain/single` read whose body carries the WHOLE sent text, its
        id taken from the request. Not the URL, not the biggest id (dola's ids do not grow with time: 14 of 35
        consecutive pairs fell). The URL and the account's conversation list are the fallback when the page's own
        traffic gives nothing, and the fallback is logged as the engine's."""
        spec, refused_at = self.spec, None
        for attempt in range(REPLY_TRIES):
            ev = await self.session.recorder.wait_for(
                lambda e: e.path == SINGLE_PATH and e.status == 200
                and body_carries(body_of(e), spec.sent_text, spec.prompt),
                self.deps.poll_s, since=self.since)
            if ev is not None:
                conversation = conversation_id_of(ev.req)
                if conversation:
                    return conversation
            if attempt >= 2:
                # The page text is NOT consulted here: a sidebar title or the echo of the order can read as "out of
                # credit" before the receipt exists. Only dola's own code for THIS send ends the wait early; on a
                # refusal the conversation is still looked for REFUSAL_RECEIPT_S, as evidence for a late delivery.
                if self._refusal() is not None:
                    refused_at = self.deps.clock() if refused_at is None else refused_at
                    if self.deps.clock() - refused_at >= REFUSAL_RECEIPT_S:
                        return None
                elif self._sse_error() is not None:
                    return None              # dola answered with a throttle or region code: nothing will come
                try:
                    found = await self._fallback_receipt(baseline)
                except Exception as exc:      # noqa: BLE001 - the page is busy: the network read may still come
                    log.info("receipt fallback failed: %s", str(exc)[:80])
                    found = None
                if found:
                    await self._say("conversation %s found by the page URL or the list, not by the network read"
                                    % found, "submit", "engine")
                    return found
        return None

    async def _fallback_receipt(self, baseline: set[str]) -> str | None:
        url = await self.driver.page_url(self.page)
        if "/chat/" in url:
            cand = url.split("/chat/")[-1].split("?")[0].split("#")[0].strip("/")
            if (cand.isdigit() and cand not in baseline
                    and body_carries(await self.driver.read_chain(self.page, cand), self.spec.sent_text,
                                     self.spec.prompt)):
                return cand
        fresh = [i for i in (await self.driver.probe(self.page)).get("ids") or [] if i not in baseline]
        for conversation in sorted(fresh, key=lambda x: -int(x) if x.isdigit() else 0)[:10]:
            if body_carries(await self.driver.read_chain(self.page, conversation), self.spec.sent_text,
                            self.spec.prompt):
                return conversation
        return None

    async def _no_conversation(self) -> SubmitResult:
        """Sent, and no conversation of ours appeared. Only dola's own signals decide: a throttle or region code in
        its stream, its region page, or the login read of the page (a guest page: logged out). The page TEXT never
        does (sidebar titles of the account's other chats read as "not enough credits"): anything else is unknown,
        and reconcile reads the account's own conversation list (proven not sent: send again; otherwise the deadline
        refunds)."""
        code = self._sse_error()
        if code in THROTTLE_CODES:
            return await self._throttled(code)
        if code == REGION_CODE or REGION_BLOCK_MARK in await self.driver.page_url(self.page):
            return self._region_blocked("dola answered %s" % (code or "with its region page"))
        logged_out = await self._logged_out_after_send()
        if logged_out is not None:
            return logged_out
        await self._ui_error_evidence("no-conversation")
        return await self._after_send_failure("no new conversation appeared after sending (%s)"
                                              % errors.UI_NO_CONVERSATION)

    async def _logged_out_after_send(self) -> SubmitResult | None:
        """The page read as a guest after the send (23/10: "Log in to unlock more features" over the composer, labelled
        unknown with no word of the logout). The account gets what a guest read at the open gets (one spaced strike and
        a rest, or no strike when the browser itself lost the cookies), and this run clears no strike. The order
        follows the money rule: a request that may carry it left the page, so it stays unknown; none did, so it was
        not sent and goes back to the queue. None when the read says logged in or says nothing."""
        try:
            state = login_state(await self.driver.probe(self.page))
        except Exception as exc:      # noqa: BLE001 - a page that cannot be read proves nothing
            log.info("login read after the send failed: %s", str(exc)[:80])
            return None
        if state.logged_in or not (state.signed_out or state.cells >= 0 or state.modal_visible):
            return None
        self.logged_in = False
        present = await self.driver.identity_cookie_names(self.session)
        lost = lost_identity_cookies(present, self.injected)
        if present is None or lost:         # the browser's fault, not dola's verdict: no strike
            why, code, effects = ("the browser lost the account's cookies: %s" % (", ".join(lost) or "not readable"),
                                  errors.BROWSER_COOKIES_LOST, cookies_lost_effects(self.deps.now()))
        else:
            why, code, effects = ("logged out: %s" % state.reason, POOL_LOGGED_OUT,
                                  login_doubt_effects(self.deps.now(), "after the send: %s" % state.reason))
        await self._ui_error_evidence("logged-out-after-send")
        if not await self._proven_not_sent():
            await self._flush_steps()
            await self._say("after the send the page read %s; the order may have reached dola" % why,
                            "submit_error", "engine")
            return _outcome(Ambiguous(("after the send: %s" % why)[:500]), *effects)
        return await self._not_sent("after the send the page read %s, and it sent no request that could carry the "
                                    "order: not sent" % why, code, effects)

    async def _not_sent(self, why: str, code: str = errors.UI_SEND_FAILED, effects=()) -> SubmitResult:
        """After `mark_sent`, PROVEN not sent (`_order_may_have_left` is False): the order goes back to the queue as a
        new attempt (`NotTaken`; the core clears `sent_at`). Nothing more is pressed in this session."""
        await self._flush_steps()
        await self._say(why, "submit_error", "engine")
        return _outcome(NotTaken(code), *effects)

    def _refused_on_account(self) -> tuple[list, bool]:
        """dola refused this order on this account (710022002, a throttle code, or its "something went wrong" reply).
        Owner's ruling 25/10: a job may be tried on at most `dola.refusal_max_accounts` accounts when dola refuses it;
        22-23/10 single jobs went round all 9 accounts within minutes and 6 were logged out by dola's risk system right
        after. The count is the job's `REFUSED_BY` list, which the core keeps across every requeue, so neither a fault
        requeue in between nor a restart resets it. Returns the list with this account appended and whether this
        refusal ends the job (the cap is reached)."""
        before = self.job.provider_state.get(REFUSED_BY)
        refused = [*(before if isinstance(before, list) else ()), self.ctx.account.id]
        cap = self.settings.get(REFUSAL_MAX_ACCOUNTS) if self.settings is not None else None
        if not isinstance(cap, int):          # the spec's own default, not a copy of it
            cap = SPEC.settings()[REFUSAL_MAX_ACCOUNTS].default
        return refused, len(refused) >= cap

    async def _refusal_outcome(self, not_taken: str, final: str, detail: str, effects, patch=None) -> SubmitResult:
        """A refusal on this account: the job goes to another one (`not_taken`), or, when this was the last account
        `dola.refusal_max_accounts` allows, fails with `final` (a refusal rule: terminal, refunded). The account
        `effects` (its rest) are the same either way."""
        refused, last = self._refused_on_account()
        if not last:
            await self._say("dola refused the order on %d account(s); the job goes to another account (%s)"
                            % (len(refused), REFUSAL_MAX_ACCOUNTS), "submit", "engine")
            return _outcome(NotTaken(not_taken, {REFUSED_BY: refused}), *effects)
        return _outcome(Fail(final, "%s (refused on %d account(s), the most %s allows)"
                             % (detail, len(refused), REFUSAL_MAX_ACCOUNTS), {**(patch or {}), REFUSED_BY: refused}),
                        *effects)

    async def _refused_by_code(self, stream: str, conversation: str | None) -> SubmitResult:
        """dola's stream answered the send with REFUSED_CODE: dola will not take this order, and took nothing. A refusal
        like any other (`_refused_on_account`): the job goes to another account, and the last account's refusal ends
        it with `dola.refused_710022002`, refunded. The stream is kept as evidence and dola's own words go to the
        timeline. The account rests THROTTLE_COOLDOWN_S and is checked again then: the refusal is about the prompt,
        but an account that just sent such a prompt is at risk, and the check finds a logout before a job does.

        On the refusal that ends the job, a conversation that already appeared is KEPT before the job fails: its id
        becomes the job's reference and is named in the timeline and in `provider_state.refused_conversation`. dola is
        known to refuse and still deliver about 25 min later; such a delivery is not fetched by itself (the job is
        failed and refunded), but an admin Re-check sends the job back to waiting (the key is charged again) and the
        poll reads that conversation. A job that goes to another account names the conversation in the timeline
        only (the requeue drops it as the reference)."""
        said = sse_message(stream)
        detail = "dola refused the order with %d: %s" % (REFUSED_CODE, said or "(no message)")
        await self._flush_steps()
        await self._say(detail, "submit", "upstream")
        await self._evidence([Evidence("refused-%d-stream.txt" % REFUSED_CODE, stream.encode("utf-8"), TEXT_MIME)])
        until = self.deps.now() + timedelta(seconds=constants.THROTTLE_COOLDOWN_S)
        rest = (Cooldown("dola refused a prompt (%d)" % REFUSED_CODE, until, recheck=True),)
        patch = {}
        if conversation is not None and not self._refused_on_account()[1]:
            await self._say("conversation %s is left: dola refused the order there" % conversation, "submit", "engine")
        elif conversation is not None:
            self.conversation = conversation
            patch = {"refused_conversation": conversation}      # kept by the Fail itself, whatever comes next
            try:
                await self.ctx.report_ref(conversation, patch)
                await self._say("conversation %s is kept as the job's reference: a late delivery there is found by "
                                "a Re-check" % conversation, "submit", "engine")
            except Exception as exc:      # noqa: BLE001 - keeping the evidence is best effort; the refusal stands
                log.warning("refused conversation %s not stored as the reference: %s", conversation, str(exc)[:80])
                await self._say("conversation %s could not be stored as the job's reference (%s); it is named in "
                                "provider_state.refused_conversation" % (conversation, type(exc).__name__),
                                "submit", "engine")
        return await self._refusal_outcome(errors.REFUSED_710022002, errors.REFUSED_710022002, detail, rest, patch)

    async def _throttled(self, code: int) -> SubmitResult:
        """dola's throttle code: a refusal (`_refused_on_account`). The account rests; the job goes to another one, or
        ends with `dola.refused_on_accounts` on the last."""
        until = self.deps.now() + timedelta(seconds=constants.THROTTLE_COOLDOWN_S)
        return await self._refusal_outcome(POOL_THROTTLED, errors.REFUSED_ON_ACCOUNTS, "dola throttled (%d)" % code,
                                           (Cooldown("dola throttled (%d)" % code, until),))

    # ---- the answer ----
    async def _answer(self, conversation: str, images) -> SubmitResult:
        spec = self.spec
        if spec.kind == "image":
            reply = await self._settled_reply(conversation)
            verdict = classify.classify_reply(reply)
            await self._after_send_picture()
            await self._say(reply_text(reply), "submit", "upstream")
            if verdict.decided and not verdict.video_possible:
                return await self._refused(verdict, reply)
            return await self._image_result(conversation, reply, verdict)

        # Only the engine's OWN reads of the conversation, as MESSAGES, judged by `judge()`. A page's innerText is never
        # read here: it holds sidebar titles of other chats, old bubbles and the balance widget.
        judged = await self._refresh(conversation, UNREAD)
        for _ in range(REPLY_TRIES):
            if judged.taken or judged.verdict.decided:
                break
            await self.deps.sleep(self.deps.poll_s)
            judged = await self._refresh(conversation, judged)

        judged, interrupted = await self._gate(conversation, images, judged)
        if interrupted is not None:
            await self._say_upstream(judged.text)
            return interrupted
        verdict = keep_taken_order(judged.verdict, judged.taken)
        if verdict is not judged.verdict:
            await self._say("dola accepted the order: keeping this conversation (dola also said: %s)"
                            % judged.verdict.detail, "submit", "engine")
        if verdict.decided or verdict.outcome is classify.SubmitOutcome.OK:
            await self._after_send_picture()
        await self._say_upstream(judged.text)
        return await self._video_result(conversation, judged.accepting or judged.newest, verdict, judged.text)

    async def _say_upstream(self, text: str) -> None:
        if text:
            await self._say(reply_text(text), "submit", "upstream")

    async def _gate(self, conversation: str, images, judged: Judged) -> tuple[Judged, SubmitResult | None]:
        """dola's "supports 4-15 s ... proceed with 15 s?" LIES: sending the prompt AGAIN in the same conversation makes
        it take the order and render the real 30 s (verified 260914). At most two re-sends, each with the FULL
        composer as the first send (a bare re-type once let dola pick Seedance 2.0 Mini / 15 s / 3 credits itself),
        each after `fence()`. But dola may say a different length and ACCEPT in the same breath: the order is in,
        and a re-send is a NEW paid order. The acceptance can come a few seconds late, so re-read before re-sending.

        Every decision (the recheck, the read before a re-send, the wait after a send) is `judge()` on the engine's OWN
        read of the conversation's messages: a failed or stale read decides nothing and never earns a send.

        Returns (judged, result): a non-None `result` ends the submit (a lost lease)."""
        spec = self.spec
        ask = classify.SubmitOutcome.NEEDS_CONFIRM

        def asking(j: Judged) -> bool:
            return j.verdict.outcome is ask and not j.taken

        if asking(judged):
            for _ in range(RECHECK_TRIES):
                await self.deps.sleep(self.deps.poll_s)
                judged = await self._refresh(conversation, judged)
                if judged.taken or (judged.verdict.decided and judged.verdict.outcome is not ask):
                    break
        resends = 0
        while asking(judged) and resends < MAX_RESENDS:
            latest = await self._judged(conversation)
            if not latest.usable:
                self.steps.append("no re-send: nothing usable was read of dola's latest reply")
                break
            judged = latest
            if not asking(judged):
                self.steps.append("no re-send: dola's latest reply is %s"
                                  % ("an acceptance" if judged.taken else "no question to answer"))
                break
            resends += 1
            self.steps.append("gated: re-send with the SAME parameters in the same conversation (push %d)" % resends)
            try:
                built = await self.driver.rebuild(self.page, spec)
                self.steps += built.steps
                if images:
                    reason = await self.driver.attach(self.page, images, raise_navigation=False)
                    if reason:
                        raise composer.ComposerError("could not re-attach the reference images: %s" % reason,
                                                     errors.UI_ATTACH_FAILED)
            except composer.ComposerError as exc:
                self.steps.append("re-send failed: %s" % str(exc)[:80])
                break
            try:
                # `fence()` runs right before Enter: a lost lease, a stopping worker or a DB error means NO second
                # order.
                await self.driver.send(self.page, spec.sent_text, before_enter=self._fence_for_send)
            except _SendBlocked as blocked:
                await self._flush_steps()
                return judged, await self._after_send_failure(
                    "no re-send: the lease check failed (%s)" % type(blocked.cause).__name__)
            except composer.ComposerError as exc:
                self.steps.append("re-send failed: %s" % str(exc)[:80])
                break
            for _ in range(REPLY_TRIES):
                await self.deps.sleep(self.deps.poll_s)
                refused = self._refusal()
                if refused is not None:       # the re-send's own stream refused it, as the first send's could
                    await self._flush_steps()
                    return judged, await self._refused_by_code(refused, conversation)
                latest = await self._judged(conversation)
                if not latest.usable:
                    continue              # a failed or stale read says nothing: keep waiting, keep what was read
                judged = latest
                if judged.taken or (judged.verdict.decided and judged.verdict.outcome is not ask):
                    break
        return judged, None

    async def _settled_reply(self, conversation: str) -> str:
        """Wait until dola has said something. One read right after the conversation appears is always empty: the
        refusal (out of credit, policy) did not exist yet and an already-refused conversation was adopted."""
        reply = ""
        for _ in range(12):
            reply = await self._reply(conversation)
            if reply.strip():
                return reply
            await self.deps.sleep(self.deps.poll_s)
        return reply

    async def _after_send_picture(self) -> None:
        item = await self.session.evidence.after_send(self.page)
        if item is not None:
            await self._evidence([item])

    # ---- turning what dola said into the result ----
    def _facts(self, reply: str, verdict) -> tuple[Facts, list[Warning]]:
        """What dola stated about the taken order: its price and model (video only), never from a refused order: a
        refusal also says "will use N credits" and learning from it invented a price for a level never rendered."""
        if self.spec.kind != "video":
            return Facts(), []
        price = classify.read_price_stated(reply)
        if price is None:
            # dola does not always state a price (a 5 s clip of 2.0 / 1.0 only reports the balance): infer it from the
            # balance before this send (the claim's fresh snapshot of the account) and the one dola reports after.
            before = self.ctx.account.capacity.get("video")
            after = verdict.credits_left
            if before is not None and after is not None and before > after:
                price = before - after
        model = classify.named_model(reply)
        warnings = []
        swap = classify.model_substitution(reply, self.spec.model)
        if swap:
            warnings.append(Warning(errors.WARN_MODEL_SUBSTITUTED, swap))
        return Facts(price, model), warnings

    def _balance(self, verdict) -> list:
        """The balance dola reported. Only for video: `credits_left` counts VIDEO credits, never image ones."""
        if self.spec.kind != "video" or verdict.credits_left is None:
            return []
        return [Balance("video", verdict.credits_left)]

    async def _video_result(self, conversation: str, reply: str, verdict, said: str = "") -> SubmitResult:
        outcome = verdict.outcome
        if outcome is classify.SubmitOutcome.OK:
            facts, warnings = self._facts(reply, verdict)
            effects = [Success(), *self._balance(verdict)]     # a reported 0 rests the pool until its refill (the core)
            return _outcome(Accepted(conversation, facts=facts, taken=True), *effects, warnings=warnings)
        if verdict.decided:
            return await self._refused(verdict, reply)
        # Waited the whole time and nothing clear was said: keep the conversation, the poll decides (never "success" by
        # default, never thrown away: dola may well be rendering). The tail is kept for the timeline.
        if said:
            tail = " ".join(said.split())[-1200:]
            await self._say("UNCLEAR what dola replied, tail: %s" % tail, "submit", "engine")
        else:
            await self._say("no usable read of dola's reply (a blip, a stale read, a changed shape): nothing is "
                            "known of it, the poll decides", "submit", "engine")
        await self._ui_error_evidence("unclear-reply")
        effects = self._balance(verdict)
        # The admin hears the reply relay could not classify: dola's own reply to the last send, as the engine read it
        # (`reply` is API text only, never the page's). The core takes the customer's echoed words out of it.
        return _outcome(Accepted(conversation, taken=False, unclear=reply), *effects)

    async def _refused(self, verdict, reply: str) -> SubmitResult:
        """dola said something decisive that is not "taken"."""
        outcome = verdict.outcome
        balance = self._balance(verdict)
        if outcome is classify.SubmitOutcome.NO_POINTS:
            return await self._out_of_points(verdict.credits_left, reply)
        if outcome is classify.SubmitOutcome.CONTENT_REFUSED:
            if verdict.code == errors.LIKENESS_REFUSED:
                # No guidance on what to change: the refusal is final, and a hint would teach how to get round it.
                detail = "dola refused this order (likeness protection)"
                return _outcome(Fail(errors.LIKENESS_REFUSED, detail), *balance)
            return _outcome(Fail(errors.CONTENT_REFUSED, verdict.detail), *balance)
        if outcome is classify.SubmitOutcome.ERROR:      # "something went wrong": a refusal like a throttle
            return await self._refusal_outcome(errors.TEMPORARY_ERROR, errors.REFUSED_ON_ACCOUNTS, verdict.detail,
                                               balance)
        # NEEDS_CONFIRM after the re-sends: dola is still gated but the video may come (R56): the poll decides.
        return _outcome(Accepted(self.conversation, taken=False), *balance)

    async def _out_of_points(self, credits_left: int | None, text: str) -> SubmitResult:
        """Out of credit. A positive balance with a video order that costs more is "not enough for THIS order", not
        "out" (resting it until midnight would lock points a cheaper job can still use). The pool that rests is the
        job's KIND: dola says "daily limit for VIDEO generation" for images too (measured 260920)."""
        spec = self.spec
        if spec.kind == "video" and (credits_left or 0) > 0:
            return _outcome(NotTaken(POOL_CANNOT_AFFORD), Balance("video", credits_left))
        # out: the pool of this job's kind reports 0, and the core rests it until its next refill whatever its counter
        # says (dola's "reached today's image limit" is as final as a zero video balance)
        return _outcome(NotTaken(POOL_OUT_OF_POINTS), Balance(spec.kind, 0))

    # ---- images: the session holds until the images are there ----
    async def _image_result(self, conversation: str, reply: str, verdict) -> SubmitResult:
        deadline = self.started + self.deps.image_hold_s
        while True:
            body = await self._chain(conversation, fresh=True)
            if body is not None:
                try:
                    urls, _ = resolver.read_images(body)
                except resolver.ChainError as exc:
                    if exc.terminal:
                        await self._say(str(exc), "submit", "upstream")
                        return _outcome(Fail(exc.code, str(exc)[:300]))
                    urls = []
                if urls:
                    return _outcome(Completed(image_outputs(urls, conversation)), Success())
            if self.deps.clock() >= deadline:
                break
            await self.deps.sleep(self.deps.image_poll_s)
        # The images are not there yet: the order is taken (dola made a conversation and did not refuse); the poll
        # fetches them.
        return _outcome(Accepted(conversation, taken=True), Success())

    # ---- failures after the send ----
    async def _after_send_failure(self, why: str) -> SubmitResult:
        """Anything that goes wrong after `mark_sent` and is not listed above: the outcome is unknown. Never `Retry`."""
        await self._flush_steps()
        await self._say(why, "submit_error", "engine")
        effects = []
        if network_failure(why):
            effects = self._proxy_effects(why)
        return _outcome(Ambiguous(why[:500]), *effects)

    def _proxy_effects(self, why: str) -> list:
        until = self.deps.now() + timedelta(seconds=constants.THROTTLE_COOLDOWN_S)
        return [ProxyBad("browser could not reach dola: %s" % why[:100]), Cooldown("proxy failed", until)]

    def with_login_read(self, outcome: SubmitResult) -> SubmitResult:
        """A run that read the account logged in clears the strike count and the time of its last strike, whatever it
        ends as (a refusal, a throttle, an unknown order, a retry), as a check does: a doubt followed by a logged-in
        read is not a streak. An outcome that already carries a `Success` is left alone."""
        if not self.logged_in or any(isinstance(e, Success) for e in outcome.account):
            return outcome
        return replace(outcome, account=(Success(), *outcome.account))

    async def failed(self, exc: BaseException) -> SubmitResult:
        """The run raised. Before `mark_sent`: an ordinary retry (a network error is the proxy's, a UI error never is).
        After it: unknown."""
        cause = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        if self.sent:
            return await self._after_send_failure("the browser failed after sending: %s" % cause)
        await self._flush_steps()
        await self._say(cause, "submit_error", "engine")
        if network_failure(str(exc)):
            return _outcome(Retry(POOL_PROXY_FAILED), *self._proxy_effects(cause))
        if PAGE_NAVIGATED in str(exc).lower():
            until = self.deps.now() + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S)
            return _outcome(Retry(errors.PAGE_RELOADED),
                            Cooldown("page reloaded while composing", until))
        code = getattr(exc, "code", None)
        ui = isinstance(code, str) and code.startswith("dola.ui_")
        return _outcome(Retry(code if ui else errors.UI_MODE_NOT_READY))


async def submit(ctx, job, settings, *, deps: Deps | None = None) -> SubmitResult:
    """Send the job's order and return what happened. Exceptions the core must see (`mark_sent` refused: a lost lease,
    a stopping worker) propagate unchanged; every other failure becomes a result."""
    deps = deps or Deps()
    from relay_dola.engine import send_http  # it imports this module: loaded here, not at the top
    http = send_http.wanted_path(settings) == "http"
    why_browser = send_http.browser_only(job) if http else None
    if http and why_browser is None:
        signed = deps if deps.signer else replace(deps, signer=send_http.make_signer)
        run = send_http.HttpRun(ctx, job, settings, signed)
    else:
        run = _Run(ctx, job, settings, deps)
        if why_browser:
            await run._say("send path: browser (%s)" % why_browser, "submit", "engine")
    outcome = None
    try:
        outcome = run.with_login_read(await run.run())
    except _CoreVerdict as verdict:
        raise verdict.cause from None
    except (BrowserLaunchError, BrowserRefused) as exc:
        # The environment, not the order: Chrome would not start, or the core gave no token. Nothing was sent, and the
        # core puts the job back with no attempt used and logs this as an error (a broken box must be loud).
        await run._say("%s: %s" % (type(exc).__name__, str(exc)[:300]), "submit_error", "engine")
        raise
    except Exception as exc:      # noqa: BLE001 - mapped below; cancellation is not an Exception and passes
        log.warning("submit failed (sent=%s): %s", run.sent, str(exc)[:200])
        return run.with_login_read(await run.failed(exc))
    return outcome
