"""Reconcile: did a send that lost contact reach dola?

The question exists because a wrong answer costs money: "never sent" lets the core send the order again, and dola may
already have taken and charged the first one. So `NotSent` needs POSITIVE proof, `StillUnknown` is the default.

1. A job that has an `external_ref` reads that conversation directly: `Found` when it carries the full sent text and a
   reply.
2. Otherwise the account's conversations are listed (the browser probe `recent_conv`, limit 30, under a `reconcile`
   lease: it works only from inside a tab) and every one is READ; the one whose user text carries the whole sent text
   (the marker is in it) is the job's.
   Matching: the conversation must carry the whole sent text (folded with `norm_for_match`: invisible characters
   and typographic quotes do not matter) in the user's words or anywhere in the thread. A conversation that carries the
   job's bare marker or the first 80 characters of its prompt (the customer's words, not the sent text) but not the
   whole text may still be the job's with the text altered: it is "cannot tell", never "not ours".
   When several conversations match (an earlier attempt of the same job left one), the one with the newest message wins,
   and one whose newest message is older than `sent_at` by the clock margin is not this attempt's at all.
3. `NotSent(covers_send_window=True)` only when the listing provably covers the send window AND every conversation in it
   was read AND none carries the text. It covers the window when the probe saw FEWER than the cap (30) raw cells and
   dola said there is nothing more (`has_more` false), or when it holds a conversation whose newest message is older
   than `sent_at`: the job's conversation is at least as recent as that one, so a list of the most recent 30 that still
   reaches back past `sent_at` cannot have pushed it out. A list at the cap with nothing older than `sent_at`, one dola
   says continues (or does not say), cells with no id, an empty or unreadable list, a missing account, or any
   conversation that cannot be read: `StillUnknown`.

The bug this avoids: a full list holding unknown conversations that do not carry the
text was taken as proof; newer conversations could have pushed the job's own out of the 30, so one order could be sent
twice. Here a full list proves nothing unless it reaches back past `sent_at`.

A job sent over plain HTTP (`provider_state.send_path == "http"`) carries the `local_message_id` it was sent with
(written before `mark_sent`); dola keeps that id on the user message. Its conversations are listed over HTTP
(`/im/chain/recent_conv`, which answers with the `/im/*` content type) and matched by that exact id, not by text. The
proof standard is the same: `NotSent` only when every conversation of a window-covering list was read; a read that
does not reach a conversation's first message cannot rule it out.

An empty shell (a conversation with 0 messages: dola gives every opened `/chat` page an id, and a process that died
before typing leaves one) is read, is not ours, and is not "unreadable".
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from relay_browser import session as browser_session
from relay_browser import ui
from relay_core.providers import Found, NotSent, Reconciled, StillUnknown
from relay_core.redact import redact_text
from relay_dola import classify, credential
from relay_dola.engine import resolver
from relay_dola.engine.http import BASE, DolaHttp
from relay_dola.engine.site import DOLA_SITE, REGION_BLOCK_MARK
from relay_dola.engine.verify import CONV_LIST_LIMIT, LOGIN_PROBE_JS

log = logging.getLogger("relay.dola.reconcile")

# Clock difference we tolerate between dola's message times and the core's `sent_at`: a conversation counts as older
# than the send only when its newest message is this much before `sent_at`.
PREDATE_MARGIN_S = 600
PAGE_LOAD_MS = 60_000
PAGE_SETTLE_MS = 4_000


@dataclass(frozen=True)
class Listing:
    """What the probe saw: the ids it could read, how many cells dola returned (raw, with or without an id), and
    dola's own `has_more` (None when the answer did not say)."""
    ids: list[str]
    cells: int
    has_more: bool | None

    @property
    def complete(self) -> bool:
        """The list is every conversation of the account: shorter than the cap and dola says nothing more follows."""
        return self.cells < CONV_LIST_LIMIT and self.has_more is False


Lister = Callable[..., Awaitable[Listing]]


class ListingError(RuntimeError):
    """The conversation list could not be read (not logged in, region page, no browser, probe failed)."""


async def list_conversation_ids(ctx, purpose: str = "reconcile") -> Listing:
    """The account's 30 most recent conversations, read by the probe inside a tab. Needs the core's browser token
    (`ctx.browser_token`) and holds the browser only for this one read."""
    jar = credential.jar_from_blob(ctx.account.credential)
    try:
        async with browser_session.open_session(DOLA_SITE, ctx.account, purpose=purpose, token=ctx.browser_token,
                                                redact=redact_text) as session:
            await ui.inject_cookies(session.ctx, jar, credential.IDENTITY_COOKIE_NAMES, BASE)
            page = session.page
            await page.goto(BASE + "/chat", wait_until="domcontentloaded", timeout=PAGE_LOAD_MS)
            await page.wait_for_timeout(PAGE_SETTLE_MS)
            if REGION_BLOCK_MARK in page.url:
                raise ListingError("dola sent this proxy to its region page")
            probe = await page.evaluate(LOGIN_PROBE_JS)
    except ListingError:
        raise
    except Exception as exc:   # noqa: BLE001 - a browser that cannot open is "cannot read", never "nothing there"
        raise ListingError("the conversation list could not be read: %s" % str(exc)[:120]) from exc
    ids = [str(i) for i in (probe.get("ids") or [])]
    has_more = probe.get("hasMore")
    return Listing(ids, int(probe.get("cells", len(ids))), has_more if isinstance(has_more, bool) else None)


RECENT_PATH = "/im/chain/recent_conv"


def recent_payload() -> dict:
    """The page's own list request (`LOGIN_PROBE_JS`), the 30 most recent conversations."""
    return {"cmd": 3200, "uplink_body": {"pull_recent_conv_chain_uplink_body": {
        "limit": CONV_LIST_LIMIT, "message_count_per_conv": 1, "api_version": 1, "conv_version": 0, "direction": 3,
        "option": {"not_need_message": True, "need_complete_conversation": True}}},
        "sequence_id": str(uuid.uuid4()), "channel": 2, "version": "1"}


async def list_over_http(http: DolaHttp) -> Listing:
    """The same list as the browser probe, over HTTP. Anything but a readable `status_code 0` answer is a
    `ListingError` (never "no conversations")."""
    try:
        resp = await http.post_chain(RECENT_PATH, recent_payload(), BASE + "/chat")
        body = resp.json() if resp.status_code == 200 else None
    except Exception as exc:   # noqa: BLE001 - unreachable or unreadable is "cannot read"
        raise ListingError("the conversation list could not be read over HTTP: %s" % type(exc).__name__) from exc
    ok = isinstance(body, dict) and body.get("status_code") in (0, None)
    down = ((body.get("downlink_body") or {}).get("pull_recent_conv_chain_downlink_body")) if ok else None
    if not isinstance(down, dict):
        raise ListingError("the conversation list over HTTP gave no list")
    raw = down.get("cells") or []
    if not isinstance(raw, list):
        raise ListingError("the conversation list over HTTP gave no list")
    ids = []
    for cell in raw:        # a cell that cannot be read is still counted: the list is then not complete
        conv = cell.get("conversation") if isinstance(cell, dict) else None
        cid = (conv.get("conversation_id") or conv.get("id")) if isinstance(conv, dict) else None
        if cid:
            ids.append(str(cid))
    has_more = down.get("has_more")
    return Listing(ids, len(raw), has_more if isinstance(has_more, bool) else None)


@dataclass(frozen=True)
class Reading:
    """One conversation as reconcile saw it."""
    found: bool | None          # True: carries the sent text; False: readable and not ours; None: cannot tell yet
    taken: bool = False
    older: bool = False         # its newest message is older than the send
    newest: float = 0.0         # newest message time (seconds); 0 when none is dated


def read_conversation(body: dict, job, sent_at: float | None, new_composer: bool) -> Reading:
    """Judge one conversation read. Positive proof only: no message at all (an empty shell) is not ours; messages we
    cannot extract any text from may be a conversation still loading, and one that carries the job's marker or prompt
    prefix without the whole text may be the job's with the text altered: both are "cannot tell", never "not ours".
    A match whose newest message is older than the send is an earlier attempt's conversation, not this one."""
    summary = resolver.conversation_summary(body)
    if summary.messages == 0:
        return Reading(False)
    if not (summary.user + summary.assistant).strip():
        return Reading(None)
    older = bool(summary.newest and sent_at is not None and summary.newest < sent_at - PREDATE_MARGIN_S)
    sent_text, prompt = job.sent_text, job.prompt
    mine = resolver.carries_text(summary.user, sent_text, prompt) or resolver.carries_text(
        summary.assistant, sent_text, prompt)
    if mine and not older:
        videos, _ = resolver.extract_videos(body)
        proven = bool(videos) or bool(resolver.extract_image_urls(body)) \
            or classify.order_taken(summary.assistant, new_composer)
        return Reading(True, taken=proven, newest=summary.newest)
    if not mine and resolver.may_be_job(body, job.marker, prompt):
        return Reading(None)
    return Reading(False, older=older, newest=summary.newest)


def read_by_local_id(body: dict, local_message_id: str, sent_at: float | None) -> Reading:
    """Judge one conversation read by the job's `local_message_id`: a user message carrying it = the job's
    conversation (dola took the send: it made this conversation for it). Not found counts as "not ours" only when the
    read reaches the conversation's first message (the send opens a new conversation, so it is message 1)."""
    messages = resolver.chain_messages(body)
    if not messages:
        return Reading(False)
    newest = resolver.last_message_time(body)
    if any(str(m.get("local_message_id") or "") == local_message_id for m in messages):
        return Reading(True, taken=True, newest=newest)
    try:
        first = min(int(m.get("index_in_conv")) for m in messages)
    except (TypeError, ValueError):
        return Reading(None)
    if first > 1:
        return Reading(None)       # older messages were not read: ours could be among them
    return Reading(False, older=bool(newest and sent_at is not None and newest < sent_at - PREDATE_MARGIN_S),
                   newest=newest)


async def _read(http: DolaHttp, conversation_id: str) -> dict | None:
    """The body of one conversation, or None when it cannot be read now (including a dead session)."""
    try:
        return await resolver.fetch_chain(http, conversation_id)
    except resolver.ChainError as exc:
        log.info("reconcile: conversation %s unreadable (%s)", conversation_id, exc.code)
        return None


async def reconcile(ctx, job, settings, *, http: DolaHttp | None = None, lister: Lister | None = None) -> Reconciled:
    """See the module text. `http` and `lister` are seams for tests; the engine passes neither."""
    sent_text = job.sent_text
    local_id = (job.provider_state or {}).get("local_message_id")
    if not (sent_text or local_id) or ctx.account is None:
        return StillUnknown()
    try:
        http = http or DolaHttp.for_account(ctx.account)
    except ValueError:
        return StillUnknown()       # a credential that cannot be read gives no proof
    sent_at = job.sent_at.timestamp() if job.sent_at else None
    new_composer = (ctx.account.provider_state or {}).get("composer") == "new"

    if local_id:            # sent over HTTP: the exact id decides, and the list is read over HTTP
        def judge(body):
            return read_by_local_id(body, str(local_id), sent_at)

        async def default_lister(_ctx):
            return await list_over_http(http)
    else:
        def judge(body):
            return read_conversation(body, job, sent_at, new_composer)
        default_lister = list_conversation_ids

    if job.external_ref:
        body = await _read(http, job.external_ref)
        if body is not None:
            mine = judge(body)
            if mine.found and (local_id or resolver.conversation_summary(body).assistant.strip()):
                return Found(str(job.external_ref), {}, mine.taken)

    try:
        listing = await (lister or default_lister)(ctx)
    except ListingError as exc:
        log.info("reconcile: %s", exc)
        return StillUnknown()
    ids = listing.ids
    if not ids:
        return StillUnknown()       # empty = the probe failed or the account is signed out, never "no conversations"

    # A cell with no id cannot be read, and ids beyond the cap are never read: either may be the job's, so neither
    # listing is a completeness proof.
    unreadable, predates = listing.cells != len(ids) or len(ids) > CONV_LIST_LIMIT, False
    best: tuple[float, str, bool] | None = None                  # (newest message, id, taken) of the best match
    # newest id first only to save reads; correctness does not rest on the order (dola ids do not follow time)
    for cid in sorted(ids, key=lambda x: int(x) if x.isdigit() else 0, reverse=True)[:CONV_LIST_LIMIT]:
        body = await _read(http, cid)
        if body is None:
            unreadable = True
            continue
        got = judge(body)
        if got.found:
            if best is None or got.newest > best[0]:
                best = (got.newest, cid, got.taken)
        elif got.found is None:
            unreadable = True
        predates = predates or got.older

    if best:
        return Found(best[1], {}, best[2])
    if unreadable:
        return StillUnknown()       # one conversation we could not read may be the job's
    covers = listing.complete or predates
    return NotSent(covers_send_window=True) if covers else StillUnknown(answered=True)   # every conversation was read
