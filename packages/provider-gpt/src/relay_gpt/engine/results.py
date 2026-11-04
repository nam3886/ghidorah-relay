"""After the send: read a job's conversation (poll), find a send whose answer was lost (reconcile), and download a
result (fetch). Reads only, through the account's proxy: nothing here sends, hides or deletes a conversation (hiding
is deleting, FINDINGS F7), so nothing here can spend or lose a result.

- Poll: `GET /backend-api/conversation/{id}` (the conversation the stream named) judged like the stream: an image in a
  tool message is the result, words with the answer finished are a refusal, still nothing `gpt.image_lost_after_s`
  after the send is a lost image. A 404 is a conversation gone with no result. A failed read is read again.
- Reconcile: the conversation the stream named, else the account's recent conversations made in
  [sent_at - FIND_BEFORE_S, sent_at + FIND_AFTER_S], oldest first, all of them read. A conversation is the job's when a
  user message has the job's own client-made message id (that wins over any other); the exact sent text decides only
  when no user message id can be read there, exactly one conversation qualifies, every read worked and the job is not
  an edit, so a conversation another job's message id claims is never taken for its twin prompt. Nothing found is
  `StillUnknown`, NEVER `NotSent`: a read may lag the send, and a requeue would send it again; the render deadline
  fails such a job with its refund.
- Fetch: the attachment's `download_url` (relative or absolute) must be `https://chatgpt.com` + ESTUARY_PATH; any
  other place ends the fetch before a request carries the bearer. The bytes come with the bearer only (no cookie).
"""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from relay_core.providers import (
    Done,
    Failed,
    Found,
    Output,
    OutputGone,
    Pending,
    PollResult,
    Reconciled,
    StillUnknown,
    Success,
)
from relay_gpt.constants import (
    BASE_URL,
    ESTUARY_PATH,
    MEDIA_MAX_BYTES,
    SITE_HOST,
    STATE_CONVERSATION,
    STATE_MESSAGE,
    STATE_PROMPT,
)
from relay_gpt.engine.check import doubt_effects
from relay_gpt.engine.stream import read_conversation, verdict
from relay_gpt.engine.web import Sessions, SignedOut, Web, real_session
from relay_gpt.errors import IMAGE_LOST, OUTPUT_MISSING, REFUSED

FIND_BEFORE_S = 60          # a conversation made this long before the send may still be the job's (clock skew)
FIND_AFTER_S = 600          # ... and this long after it
LIST_LIMIT = 28             # the page's own listing size
CONV_PATH = "/backend-api/conversation/"
DELETED = "Conversation has been deleted"
IMAGE_MIME = "image/png"


def _state(job, key: str) -> str | None:
    value = (job.provider_state or {}).get(key)
    return value if isinstance(value, str) and value else None


def _now() -> datetime:
    return datetime.now(UTC)


async def _read(web: Web, cid: str) -> tuple[int, Any]:
    return await web.get_json(CONV_PATH + quote(cid, safe=""))


# ---- poll ----------------------------------------------------------------------------------------------------------
async def poll(ctx, job, settings, sessions: Sessions, *, session=None,
               make_session: Callable[[dict[str, str] | None], Any] = real_session,
               now: Callable[[], datetime] = _now) -> PollResult:
    wait = int(settings.get("gpt.poll_interval_s"))
    cid = _state(job, STATE_CONVERSATION)
    if cid is None:
        return PollResult(Pending("no conversation to read", wait, answered=False))
    try:
        async with Web(ctx.account, settings, sessions, ctx, session=session, make_session=make_session) as web:
            await web.access_token()
            status, body = await _read(web, cid)
    except SignedOut:
        return PollResult(Pending("the session read signed out", wait, answered=False),
                          effects=doubt_effects("chatgpt.com answered the conversation read signed out", settings,
                                                now()))
    except Exception as e:      # noqa: BLE001 - read again; the type only (curl's text may hold the proxy)
        return PollResult(Pending("chatgpt.com could not be read (%s)" % type(e).__name__, wait, answered=False))
    if status == 404:
        return PollResult(Failed(IMAGE_LOST, "the conversation is gone (404) with no image", True))
    if status != 200 or not isinstance(body, dict):
        return PollResult(Pending("chatgpt.com answered %d to the conversation read" % status, wait, answered=False))
    reading = read_conversation(body, _state(job, STATE_MESSAGE) or "")
    said = verdict(reading)
    if said == "image":
        image = reading.images[0]
        return PollResult(Done((Output(image.pointer, IMAGE_MIME, 0, image.file_id, {
            STATE_CONVERSATION: cid, "width": image.width, "height": image.height}),)),
            effects=(Success(),), title=reading.title)
    if said == "refused":
        return PollResult(Failed(REFUSED, "chatgpt.com: %s" % (reading.assistant_text.strip() or "blocked")[:300],
                                 True), effects=(Success(),))
    lost_after = int(settings.get("gpt.image_lost_after_s"))
    if job.sent_at is not None and (now() - job.sent_at).total_seconds() >= lost_after:
        return PollResult(Failed(IMAGE_LOST, "the conversation shows no image %d s after the send" % lost_after, True))
    return PollResult(Pending("no image in the conversation yet", wait))


# ---- reconcile -----------------------------------------------------------------------------------------------------
def _user_messages(body: dict) -> list[dict]:
    return [n["message"] for n in (body.get("mapping") or {}).values()
            if isinstance(n, dict) and isinstance(n.get("message"), dict)
            and (n["message"].get("author") or {}).get("role") == "user"]


def _text(message: dict) -> str:
    return "".join(p for p in (message.get("content") or {}).get("parts") or [] if isinstance(p, str))


def match(body: dict, message_id: str, prompt: str | None) -> str | None:
    """How the conversation holds the job's send: "id" (a user message with its message id), "text" (no user message
    id can be read and a user message has exactly its sent text), or None (claimed by another message id, or not it)."""
    users = _user_messages(body)
    ids = [m.get("id") for m in users]
    if message_id in ids:
        return "id"
    if any(isinstance(i, str) and i for i in ids):
        return None                  # claimed by another message id: never matched by its text
    return "text" if prompt and any(_text(m) == prompt for m in users) else None


def _when(value: Any) -> float | None:
    """A listing's `create_time`: epoch seconds or ISO 8601; None when unreadable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
        return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()
    return None


def candidates(listing: Any, sent_at: float) -> list[str] | None:
    """The listed conversation ids made in the send window, oldest first; None when the listing cannot be read. An
    item with no readable time is not provably in the window and is left out."""
    items = listing.get("items") if isinstance(listing, dict) else None
    if not isinstance(items, list):
        return None
    found = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            continue
        made = _when(item.get("create_time"))
        if made is not None and sent_at - FIND_BEFORE_S <= made <= sent_at + FIND_AFTER_S:
            found.append((made, item["id"]))
    return [cid for _, cid in sorted(found)]


async def reconcile(ctx, job, settings, sessions: Sessions, *, session=None,
                    make_session: Callable[[dict[str, str] | None], Any] = real_session,
                    now: Callable[[], datetime] = _now) -> Reconciled:
    """Did the send of an unknown job reach chatgpt.com? See the module text."""
    message, prompt, known = (_state(job, STATE_MESSAGE), _state(job, STATE_PROMPT),
                              _state(job, STATE_CONVERSATION))
    if message is None or (known is None and job.sent_at is None):
        return StillUnknown()
    read_ok, kinds = True, {}
    try:
        async with Web(ctx.account, settings, sessions, ctx, session=session, make_session=make_session) as web:
            await web.access_token()
            if known is not None:
                cids = [known]
            else:
                status, listing = await web.get_json("/backend-api/conversations?offset=0&limit=%d&order=updated"
                                                     % LIST_LIMIT)
                cids = candidates(listing, job.sent_at.timestamp()) if status == 200 else None
                if cids is None:
                    return StillUnknown()
            for cid in cids:             # every candidate is read: a later one may hold the job's own message id
                status, body = await _read(web, cid)
                if status != 200 or not isinstance(body, dict):
                    read_ok = False
                    continue
                kinds[cid] = match(body, message, prompt)
    except SignedOut:
        return StillUnknown(effects=doubt_effects("chatgpt.com answered the reconcile read signed out", settings,
                                                  now()))
    except Exception:      # noqa: BLE001 - any failed read settles nothing
        return StillUnknown()
    by_id = [cid for cid, kind in kinds.items() if kind == "id"]
    if by_id:
        return Found(by_id[0], {STATE_CONVERSATION: by_id[0]}, taken=True)
    # The sent text alone decides only when it is unique among conversations that were all read, and never for an
    # edit (the same words on another input image is another order).
    by_text = [cid for cid, kind in kinds.items() if kind == "text"]
    if len(by_text) == 1 and read_ok and job.operation != "image_to_image":
        return Found(by_text[0], {STATE_CONVERSATION: by_text[0]}, taken=True)
    return StillUnknown(answered=read_ok)


# ---- fetch ---------------------------------------------------------------------------------------------------------
def media_url(url: Any) -> str:
    """The download link as an absolute chatgpt.com estuary URL; OutputGone for anything else (before any request
    carries the bearer there)."""
    if not isinstance(url, str) or not url:
        raise OutputGone(OUTPUT_MISSING, "the download answer named no link")
    if url.startswith("/"):
        url = BASE_URL + url
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != SITE_HOST or parts.path != ESTUARY_PATH:
        raise OutputGone(OUTPUT_MISSING, "the download link is not chatgpt.com's")
    return url


async def fetch(ctx, out: Output, settings, sessions: Sessions, *, session=None,
                make_session: Callable[[dict[str, str] | None], Any] = real_session) -> AsyncIterator[bytes]:
    """The bytes of `out` (one image), yielded once. A conversation or file that is gone -> OutputGone; any other
    failure raises and the core downloads again."""
    cid, file_id = (out.meta or {}).get(STATE_CONVERSATION), out.upstream_id
    if not isinstance(cid, str) or not cid or not file_id:
        raise OutputGone(OUTPUT_MISSING, "the result names no conversation or no file")
    async with Web(ctx.account, settings, sessions, ctx, session=session, make_session=make_session) as web:
        await web.access_token()
        status, body = await web.get_json("%s%s/attachment/%s/download" % (
            CONV_PATH, quote(cid, safe=""), quote(file_id, safe="")))
        if status == 404 or (isinstance(body, dict) and body.get("detail") == DELETED):
            raise OutputGone(OUTPUT_MISSING, "the conversation is gone")
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError("chatgpt.com answered %d to the download link read" % status)
        status, mime, data = await web.get_media(media_url(body.get("download_url")), MEDIA_MAX_BYTES)
        if status == 404:
            raise OutputGone(OUTPUT_MISSING, "the image is gone")
        if status != 200 or not mime.startswith("image/"):
            raise RuntimeError("chatgpt.com answered %d (%s) to the download" % (status, mime.split(";")[0][:40]))
        yield data

