"""After the send: read a job's chat or task (reconcile, poll), and download a result (fetch). Reads only: nothing here
sends a generate, so nothing here can spend.

- Reconcile (a send whose answer was lost): read the job's chat (`GET /api/v2/chats/<id>`), made before the send and
  on the job since. No message of ours in it is `NotSent` (FINDINGS 4: qwen writes the user message when it takes
  the order; the core requeues only when this read started after the whole send window). A user message is `Found`:
  a video taken when its assistant message names the task (`extra.wanx.task_id`), an image always.
- Poll, a video with its task: `GET /api/v2/task/status/<task>` until `success` with the mp4 link; a status that says
  the task failed fails the job (refunded). A video without a task yet: read the chat for its task, for qwen's
  "Insufficient Credits", or for a reply in words with no task, a refusal after `qwen.refusal_grace_s`. An image:
  read the chat; the image is the assistant's `cdn.qwenlm.ai` link (in `content_list`); a finished answer with
  words and no image is qwen refusing; an image chat still empty `qwen.image_lost_after_s` after the send is a lost
  stream (it never delivered later, FINDINGS 6).
- Fetch: the `cdn.qwenlm.ai` link (its `key` holds no expiry), through the account's proxy, no cookie.
"""

import contextlib
import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from urllib.parse import quote

from relay_core.net import stream_download, system_resolver
from relay_core.providers import (
    Done,
    Failed,
    Found,
    NotSent,
    Output,
    Pending,
    PollResult,
    Reconciled,
    StillUnknown,
    Success,
)
from relay_qwen import errors
from relay_qwen.constants import MEDIA_DEADLINE_S, MEDIA_HOSTS, MEDIA_MAX_BYTES
from relay_qwen.engine.http import (
    Sessions,
    Site,
    assistant,
    has_user_message,
    links_of,
    media_link,
    proxy_url,
    task_of,
    text_of,
)
from relay_qwen.engine.submit import CHAT_KEY, IMAGE_MIME, TASK_KEY

VIDEO_MIME = "video/mp4"
TASK_FAILED = ("failed", "fail", "error", "cancelled", "canceled", "timeout")    # not seen yet: any of these ends it


def interval_s(settings, rand: Callable[[], float] = random.random) -> int:
    base = int(settings.get("qwen.poll_interval_s"))
    return base + int(rand() * base / 2)


def chat_of(job) -> str | None:
    state = job.provider_state or {}
    chat = state.get(CHAT_KEY)
    return chat if isinstance(chat, str) and chat else None


# ---- reconcile -----------------------------------------------------------------------------------------------------
async def reconcile(ctx, job, settings, sessions: Sessions, *, transport=None) -> Reconciled:
    """Did the paid send of an unknown job reach qwen? See the module text."""
    chat = chat_of(job)
    if chat is None:
        return StillUnknown()
    try:
        async with Site(ctx, sessions, transport=transport) as site:
            await site.token()
            record = await site.read_chat(chat)
    except Exception:      # noqa: BLE001 - any failed read settles nothing
        return StillUnknown()
    if record is None:
        return StillUnknown()
    if not has_user_message(record):
        return NotSent(covers_send_window=True)
    if job.params.get("kind") == "video":
        task = task_of(assistant(record))
        if task is None:
            return Found(chat, {CHAT_KEY: chat}, taken=False)
        return Found(task, {CHAT_KEY: chat, TASK_KEY: task}, taken=True)
    return Found(chat, {CHAT_KEY: chat}, taken=True)


# ---- poll ----------------------------------------------------------------------------------------------------------
async def poll(ctx, job, settings, sessions: Sessions, *, transport=None, rand: Callable[[], float] = random.random,
               now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> PollResult:
    state = job.provider_state or {}
    chat, task, wait = chat_of(job), state.get(TASK_KEY), interval_s(settings, rand)
    if chat is None:
        return PollResult(Pending("no chat to read", wait, answered=False))
    try:
        async with Site(ctx, sessions, transport=transport) as site:
            await site.token()
            if task:
                status, body = await site.call("GET", "/api/v2/task/status/%s" % quote(task, safe=""),
                                               referer="/c/" + chat)
                return _task(status, body, task, chat, wait)
            record = await site.read_chat(chat)
    except Exception as e:      # noqa: BLE001 - a failed read is read again; its text may carry the proxy URL
        return PollResult(Pending("qwen could not be read (%s)" % type(e).__name__, wait, answered=False))
    if record is None:
        return PollResult(Pending("the chat could not be read", wait, answered=False))
    if job.params.get("kind") == "video":
        return _video_chat(record, chat, wait, job, settings, now())
    return _image_chat(record, chat, wait, job, settings, now())


def _task(status: int, body: dict, task: str, chat: str, wait: int) -> PollResult:
    data = body.get("data") if isinstance(body.get("data"), dict) else None
    if status != 200 or body.get("success") is not True or data is None:
        return PollResult(Pending("qwen answered %d to the task read" % status, wait, answered=False))
    said = str(data.get("task_status") or "").lower()
    link = media_link(data.get("content"))
    if said == "success" and link:
        return PollResult(Done((Output(link, VIDEO_MIME, 0, task, {CHAT_KEY: chat}),)), effects=(Success(),))
    if said in TASK_FAILED:
        return PollResult(Failed(errors.RENDER_FAILED, "qwen's task ended %s: %s" % (
            said, str(data.get("message") or "no reason")[:200]), True), effects=(Success(),))
    return PollResult(Pending(said, wait), effects=(Success(),))


def _video_chat(record: dict, chat: str, wait: int, job, settings, now: datetime) -> PollResult:
    """A video found by the reconciler with no task yet: the task once the chat names it (followed), qwen's
    "Insufficient Credits", or an assistant reply in words with no task: a refusal once it has stood
    `qwen.refusal_grace_s` after the send (owner, 23/10), so a task written a little later is still followed."""
    message = assistant(record)
    task = task_of(message)
    if task:
        return PollResult(Pending("the task is %s" % task, 5, {TASK_KEY: task}))
    extra = (message or {}).get("extra")
    if isinstance(extra, dict) and extra.get("quota_limit") is True:
        return PollResult(Failed(errors.OUT_OF_CREDITS, "qwen: %s" % text_of(message)[:300], False))
    words = text_of(message)
    grace = int(settings.get("qwen.refusal_grace_s"))
    if words and job.sent_at is not None and (now - job.sent_at).total_seconds() >= grace:
        return PollResult(Failed(errors.REFUSED, "qwen: %s" % words[:300], False))
    return PollResult(Pending("qwen answered in words with no task yet" if words else "no task in the chat yet", wait))


def _image_chat(record: dict, chat: str, wait: int, job, settings, now: datetime) -> PollResult:
    message = assistant(record)
    links = links_of(message)
    if links:
        return PollResult(Done(tuple(Output(link, IMAGE_MIME, i, None, {CHAT_KEY: chat}) for i, link in
                                     enumerate(links))), effects=(Success(),))
    if (message or {}).get("done") is True:
        return PollResult(Failed(errors.REFUSED, "qwen: %s" % (text_of(message) or "no image")[:300], True))
    lost_after = int(settings.get("qwen.image_lost_after_s"))
    if job.sent_at is not None and (now - job.sent_at).total_seconds() >= lost_after:
        return PollResult(Failed(errors.IMAGE_LOST, "the chat shows no image %d s after the send" % lost_after, True))
    return PollResult(Pending("no image in the chat yet", wait))


# ---- fetch ---------------------------------------------------------------------------------------------------------
async def fetch(ctx, out: Output, *, media_transport=None, media_resolver=system_resolver) -> AsyncIterator[bytes]:
    """The bytes of `out`, streamed from its CDN link. The test seams are those of `stream_download`."""
    async with contextlib.aclosing(stream_download(
            out.ref, hosts=MEDIA_HOSTS, proxy=proxy_url(ctx.account.proxy), max_bytes=MEDIA_MAX_BYTES,
            deadline_s=MEDIA_DEADLINE_S, resolver=media_resolver, transport=media_transport)) as parts:
        async for part in parts:
            yield part

