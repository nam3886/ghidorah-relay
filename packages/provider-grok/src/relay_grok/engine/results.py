"""After the send: find a job whose answer was lost (reconcile), hand what it found to the fetcher (poll), and download
a result (fetch). Reads only, through the account's proxy: nothing here sends, uploads or opens the Imagine socket,
so nothing here can spend a use or make a second order.

- Reconcile (step 0, plans/grok-design-261106/step0.md; fixtures/grok/FINDINGS.md F9): every operation makes a
  conversation, read by `GET /rest/app-chat/conversations/{id}/responses`. With the conversation id the stream named
  (`provider_state.conversation_id`) that one is read. With only the request id (the run broke before the first line)
  the account's recent conversations are listed (an edit has no kind; a text-to-image or a video is
  `CONVERSATION_KIND_IMAGINE`), those made in `[sent_at - FIND_BEFORE_S, sent_at + grok.render_deadline_s]` are read,
  and the one whose human turn is exactly the sent text is the job's. Two such (a twin prompt) is no match: one of
  them is another job's; nor is one when another candidate could not be read (that one may be the job's). A
  conversation a refused send made (400 `code 3`) has no human turn, so it never matches.
  The answer is judged like the stream: the model's generated assets of the job's kind, a moderated one
  (`auxKeys.moderated` is the string "true") never delivered; a video with no `rRated` key, or done (human turn to
  answer) faster than `grok.decoy_min_s`, is a decoy: the account's video pool rests `grok.decoy_rest_s`. Something
  deliverable is `Found` (taken) with its outputs in `provider_state.outputs`; anything else is `StillUnknown`, NEVER
  `NotSent`: a re-send would charge twice. The render deadline fails such a job with its refund.
- Poll: the outputs a reconcile recorded, handed to the fetcher; it reads nothing upstream.
- Fetch: a text-to-image result is public (`imagine-public.x.ai`): `net.stream_download` with no cookie. An edit's
  image and a video are on `assets.grok.com`: `Web.get_media` with the account's cookie. Any other place (another host,
  http, a port, a login, `..`) is `OutputGone` before any request, so the cookie never leaves `assets.grok.com`.
"""

import contextlib
import logging
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

from relay_core.net import stream_download
from relay_core.providers import Done, Found, Output, OutputGone, Pending, PollResult, Reconciled, Rest, StillUnknown
from relay_grok.constants import (
    ASSETS_HOST,
    LIST_CONVERSATIONS_PATH,
    MEDIA_DEADLINE_S,
    MEDIA_MAX_BYTES,
    PUBLIC_HOST,
    RESPONSES_PATH,
    STATE_CONVERSATION,
    STATE_SENT_AT,
    STATE_SENT_TEXT,
    VIDEO_KIND,
    VIDEO_MODE_TAIL,
)
from relay_grok.engine.check import doubt_effects
from relay_grok.engine.web import Refused, Web, cookie_host, proxy_url, real_session
from relay_grok.engine.wire import asset_url, made_note

log = logging.getLogger("relay_grok.results")

FIND_BEFORE_S = 60           # a conversation made this long before the send may still be the job's (clock skew)
LIST_PAGE_SIZE = 30          # the page's own listing size; one job at a time per account keeps the window short
DOWNLOAD_FAILED = "render.download_failed"
PUBLIC_IMAGE = "https://%s/imagine-public/images/%%s.jpg" % PUBLIC_HOST   # a t2i asset id is its public image id
_KIND = {"text_to_image": "image", "image_to_image": "image", "text_to_video": "video"}
_MIME = {"image": "image/jpeg", "video": "video/mp4"}
_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def _now() -> datetime:
    return datetime.now(UTC)


def _id(value: Any) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _sent_at(job) -> datetime | None:
    if job.sent_at is not None:
        return job.sent_at
    at = (job.provider_state or {}).get(STATE_SENT_AT)
    return datetime.fromtimestamp(at, UTC) if isinstance(at, int | float) and not isinstance(at, bool) else None


# ---- judging a read-back -------------------------------------------------------------------------------------------
def _turns(body: Any) -> list[dict] | None:
    turns = body.get("responses") if isinstance(body, dict) else None
    return [t for t in turns if isinstance(t, dict)] if isinstance(turns, list) else None


def _human(turn: dict) -> bool:
    return str(turn.get("sender", "")).lower() == "human"


def sent_here(body: Any, texts: tuple[str, ...], n: int | None) -> bool:
    """Does this conversation hold the job's send: a human turn whose message is exactly one of `texts` (and, for a
    text-to-image, asked the job's `n` images when it says)?"""
    for turn in _turns(body) or ():
        if not _human(turn) or turn.get("message") not in texts:
            continue
        asked = ((turn.get("mediaGenInput") or {}).get("textToImage") or {}).get("numOfImages")
        if n is None or type(asked) is not int or asked == n:
            return True
    return False


def outputs_of(body: Any, kind: str, op: str, decoy_min_s: float) -> tuple[list[dict], int]:
    """The deliverable outputs of a conversation read: the model's finished assets of `kind`, as output dicts (ref,
    mime, upstream_id), in answer order, and how many decoys it held. A moderated one is never an output, nor is a
    video with no `rRated` key or one done faster than `decoy_min_s` (0 skips the time rule, never the key rule): that
    is a decoy. None while grok still works on it (`inflightResponses`)."""
    turns = _turns(body)
    if turns is None or body.get("inflightResponses"):
        return [], 0
    asked = {t.get("responseId"): _when(t.get("createTime")) for t in turns if _human(t)}
    outs, decoys = [], 0
    for turn in turns:
        if _human(turn):
            continue
        took = None
        start, end = asked.get(turn.get("parentResponseId")), _when(turn.get("createTime"))
        if start is not None and end is not None:
            took = (end - start).total_seconds()
        for asset in turn.get("fileAttachmentAssetMetadata") or ():
            if not isinstance(asset, dict) or asset.get("isModelGenerated") is not True:
                continue
            if not str(asset.get("mimeType", "")).startswith(kind + "/"):
                continue
            if (asset.get("auxKeys") or {}).get("moderated") == "true":
                continue
            if kind == "video" and "rRated" not in asset:
                decoys += 1
                continue
            if kind == "video" and decoy_min_s and (took is None or took < decoy_min_s):
                decoys += 1
                continue
            aid = _id(asset.get("assetId"))
            if op == "text_to_image":
                ref = PUBLIC_IMAGE % aid if aid else None
            else:
                ref = asset_url(asset.get("key"))
            if ref is not None:
                outs.append({"ref": ref, "mime": _MIME[kind], "upstream_id": aid})
    return outs, decoys


# ---- reconcile -----------------------------------------------------------------------------------------------------
async def _read(web: Web, cid: str) -> tuple[int, Any]:
    return await web.read_json("GET", RESPONSES_PATH % quote(cid, safe=""))


class _SignedOut(Exception):
    pass


async def reconcile(ctx, job, settings, *, make_session: Callable[[Any], Any] = real_session,
                    now: Callable[[], datetime] = _now) -> Reconciled:
    """Did the send of an unknown job make something? See the module text."""
    kind = _KIND.get(job.operation)
    state = job.provider_state or {}
    cid, sent_at = _id(state.get(STATE_CONVERSATION)), _sent_at(job)
    if kind is None or (cid is None and sent_at is None):
        return StillUnknown()
    n = job.params.get("n") if job.operation == "text_to_image" else None
    text = state.get(STATE_SENT_TEXT) if isinstance(state.get(STATE_SENT_TEXT), str) else job.sent_text
    texts = (text, text + VIDEO_MODE_TAIL) if kind == "video" and text else (text,)
    read_ok = True
    try:
        async with Web(ctx.account, settings, ctx, make_session=make_session) as web:
            if cid is not None:
                status, body = await _read(web, cid)
                if status == 401:
                    raise _SignedOut
                if status != 200 or not isinstance(body, dict):
                    return StillUnknown()
                found = {cid: body}
            else:
                path = "%s?pageSize=%d" % (LIST_CONVERSATIONS_PATH, LIST_PAGE_SIZE)
                if job.operation != "image_to_image":
                    path += "&kind=" + VIDEO_KIND
                status, listing = await web.read_json("GET", path)
                if status == 401:
                    raise _SignedOut
                items = listing.get("conversations") if isinstance(listing, dict) else None
                if status != 200 or not isinstance(items, list):
                    return StillUnknown()
                low = sent_at - timedelta(seconds=FIND_BEFORE_S)
                high = sent_at + timedelta(seconds=int(settings.get("grok.render_deadline_s")))
                found = {}
                for item in items:
                    made_at = _when(item.get("createTime")) if isinstance(item, dict) else None
                    other = _id(item.get("conversationId")) if isinstance(item, dict) else None
                    if other is None or made_at is None or not low <= made_at <= high:
                        continue
                    status, body = await _read(web, other)
                    if status == 401:
                        raise _SignedOut
                    if status != 200 or not isinstance(body, dict):
                        read_ok = False
                        continue
                    if text and sent_here(body, texts, n):
                        found[other] = body
    except _SignedOut:
        return StillUnknown(effects=doubt_effects("grok.com answered the reconcile read signed out", settings, now()))
    except Exception as e:      # noqa: BLE001 - any failed read settles nothing; the type only (it may hold the proxy)
        log.warning("grok job %s: the reconcile read failed (%s)", job.id, type(e).__name__)
        return StillUnknown()
    if not read_ok:             # a candidate went unread: it may be the job's, so no other one is taken for it
        return StillUnknown(answered=False)
    if len(found) != 1:         # none, or a twin prompt: another job's conversation is never taken for this one
        return StillUnknown(answered=read_ok)
    cid, body = next(iter(found.items()))
    outs, decoys = outputs_of(body, kind, job.operation, int(settings.get("grok.decoy_min_s")))
    if not outs:                # a decoy rests the video pool as the stream's does (submit.py)
        rest = Rest("video", now() + timedelta(seconds=int(settings.get("grok.decoy_rest_s"))), "decoy")
        return StillUnknown(answered=read_ok, effects=(rest,) if decoys else ())
    note = made_note(len(outs), n, kind) if n else None
    if note is not None:
        log.warning("grok job %s: %s", job.id, note)
        try:
            await ctx.event(note, "reconcile", "engine")
        except Exception as e:      # noqa: BLE001 - diagnostics never fail the reconcile
            log.warning("grok job %s: the note was not recorded (%s)", job.id, type(e).__name__)
    return Found(cid, {STATE_CONVERSATION: cid, "outputs": outs[:n] if n else outs}, taken=True)


# ---- poll ----------------------------------------------------------------------------------------------------------
async def poll(ctx, job, settings) -> PollResult:
    """The outputs a reconcile found, handed to the fetcher; reads nothing upstream. None recorded (or one that is not
    a grok media link) is a poll that did not answer: the render deadline bounds it."""
    state = job.provider_state or {}
    outs = state.get("outputs")
    if not isinstance(outs, list) or not outs or not all(
            isinstance(o, dict) and _where(o.get("ref")) is not None for o in outs):
        return PollResult(Pending("no grok result was recorded for this job", answered=False))
    return PollResult(Done(tuple(
        Output(o["ref"], str(o.get("mime") or ""), i, o.get("upstream_id") if isinstance(o.get("upstream_id"), str)
               else None) for i, o in enumerate(outs))))


# ---- fetch ---------------------------------------------------------------------------------------------------------
def _where(url: Any) -> str | None:
    """`public` for an https link on imagine-public.x.ai, `assets` for one the account's cookie may go to on
    assets.grok.com, None for anything else (another host, http, a port, a login, `..`)."""
    if not isinstance(url, str) or ".." in url or "\\" in url:
        return None
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    if p.scheme != "https" or port is not None or p.username or p.password:
        return None
    if p.hostname == PUBLIC_HOST:
        return "public"
    if p.hostname == ASSETS_HOST and cookie_host(url):
        return "assets"
    return None


async def fetch(ctx, out: Output, settings, *, make_session: Callable[[Any], Any] = real_session,
                download=stream_download) -> AsyncIterator[bytes]:
    """The bytes of `out`, streamed. A file grok says is not there (403 / 404 on assets.grok.com) or a link that is
    not grok's -> OutputGone; any other failure raises and the core downloads again. `make_session` and `download`
    are the test seams."""
    where = _where(out.ref)
    if where == "public":
        async with contextlib.aclosing(download(
                out.ref, hosts=(PUBLIC_HOST,), proxy=proxy_url(ctx.account.proxy), max_bytes=MEDIA_MAX_BYTES,
                deadline_s=MEDIA_DEADLINE_S)) as parts:
            async for part in parts:
                yield part
        return
    if where != "assets":
        raise OutputGone(DOWNLOAD_FAILED, "not a grok media host")
    try:
        async with Web(ctx.account, settings, ctx, make_session=make_session) as web:
            async with contextlib.aclosing(web.get_media(out.ref, MEDIA_MAX_BYTES, MEDIA_DEADLINE_S)) as parts:
                async for part in parts:
                    yield part
    except Refused as e:
        if e.kind == "gone":
            raise OutputGone(DOWNLOAD_FAILED, "assets.grok.com answered %d: the file is gone" % e.status) from None
        raise
