"""The batch a job owns: reading it (poll, reconcile) and downloading its result (fetch). Reads only: none of this
ever sends a generate, so none of it can spend.

A job's batch id is ours (a UUID the submit made) and is written to the job before the generate is sent
(`provider_state.batch`, also the job's `external_ref`). `GET /api/generation-batches/<id>` reads it:

- Done is `isComplete` true AND every content item `isLoading` false. Nothing else counts: `videoUrl`,
  `thumbnailUrl` and the poster `imageUrl` appear, vanish and change path from one read to the next while the render
  runs, so no URL in a read is taken as a result. The download goes by the content item's id instead. A done batch
  whose item has an `error` or no media is vibes refusing it: the job fails at once, refunded (`render_error`).
- Completion is lazy: vibes finalizes a batch when it is READ (the finalizing GET carries `generationEndTime`), so the
  poll keeps reading every `vibes.poll_interval_s` (plus up to half again at random, never faster).
- Accepted (for reconcile) is `needsPolling` true, or an item with `data` or a `mediaEntId`, or a complete batch.
  The empty shell the create left (`needsPolling` false, `data` null) says nothing: it looks the same while a generate
  is in flight, after one that died on the way, and when none was ever sent. It is never "not sent": the job stays
  unknown until the render deadline fails it with its refund, and it is never sent again.
- A 403 `X-Vercel-Mitigated: challenge` is the checkpoint: the account makes no call for `vibes.checkpoint_rest_s`
  (doubled by each repeat within a day, up to `vibes.checkpoint_rest_max_s`) and the job keeps waiting.
  A 5xx (vibes says `Failed to fetch generation batch` now and then) is read again.
- A poll answered with a signed-out 401/403 that proves the session is gone (`session_gone`) strikes and rests the
  account, and the job keeps waiting (the render may still finish). A reconcile read answered so does the same to the
  account, and the job stays unknown: the batch it cannot read may hold a generate that a live session
  sent before. A generate that was itself answered so never gets here (the submit fails it with its refund).
"""

import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from relay_core.providers import (
    Cooldown,
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
from relay_vibes import errors
from relay_vibes.constants import BATCHES_PATH, DOWNLOAD_PATH, DOWNLOAD_TIMEOUT_S, OUTPUT_MIME
from relay_vibes.engine.check import doubt_effects
from relay_vibes.engine.http import Resting, Rests, client_for, is_checkpoint, json_of, session_gone

BATCH_KEY = "batch"          # provider_state key of the job's batch id
CHECKPOINT_NOTE = "vibes' security checkpoint: the account makes no call for a while, the job keeps waiting"
RESTING_NOTE = "the account rests after vibes' security checkpoint"


def batch_id(job) -> str | None:
    return (job.provider_state or {}).get(BATCH_KEY) or job.external_ref


def interval_s(settings, rand: Callable[[], float] = random.random) -> int:
    """The wait before the next read: the setting plus up to half of it at random (never less than the setting)."""
    base = int(settings.get("vibes.poll_interval_s"))
    return base + int(rand() * base / 2)


def checkpoint_rest(ctx, settings, rests: Rests, why: str) -> tuple[int, Cooldown]:
    """The account's checkpoint rest, started here unless the call that met the checkpoint already started it
    (`Rests.call`, so one checkpoint is one repeat): (its length left, the Cooldown the core records)."""
    if not rests.left_s(ctx.account.id):
        rests.trip(ctx.account.id, settings)
    return rests.left_s(ctx.account.id), Cooldown(why, rests.start(ctx.account.id, 0), recheck=True)


def items(batch: dict) -> list[dict]:
    content = batch.get("content")
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []


def is_done(batch: dict) -> bool:
    """Complete AND no item still loading: the one done rule."""
    content = items(batch)
    return batch.get("isComplete") is True and bool(content) and all(c.get("isLoading") is False for c in content)


def is_accepted(batch: dict) -> bool:
    """vibes took the generate: the batch moved past the empty shell the create left."""
    return (batch.get("needsPolling") is True or batch.get("isComplete") is True
            or any(c.get("data") or c.get("mediaEntId") for c in items(batch)))


def has_media(item: dict) -> bool:
    """The item holds a picture or a video. `data` is not media: a refused item keeps `{"requestId": ...}` there."""
    return bool(item.get("mediaEntId") or item.get("imageUrl") or item.get("videoUrl"))


def render_error(batch: dict) -> tuple[str, str] | None:
    """Why a finished batch delivers nothing, as (code, detail); None when every item has its media. An item with an
    `error`, or with no media, is vibes refusing it (live 23/10: `isComplete`, `hasError: false`, and one item with
    `error: "This content could not be generated. ..."`, `data` `{"requestId"}`, no `imageUrl`; its download 404s
    forever). The job fails at once with its refund. A batch holds one item (the create makes one), so there is no
    partial result to deliver; were there several, one refused item still fails the whole job: the client is
    refunded rather than charged in full for part of the order."""
    if batch.get("hasError"):
        return errors.RENDER_FAILED, "vibes ended the batch with an error: %s" % str(batch.get("error") or
                                                                                     "no detail")[:200]
    for c in items(batch):
        if c.get("error") or not has_media(c):
            return errors.CONTENT_REFUSED, "vibes refused the item: %s" % str(c.get("error") or
                                                                              "it ended with no media")[:200]
    return None


def outputs(batch: dict, kind: str) -> tuple[Output, ...]:
    """One output per content item, downloaded by the item's id (never by a URL a read showed)."""
    rows = sorted((c for c in items(batch) if c.get("id")), key=lambda c: c.get("orderIndex") or 0)
    return tuple(Output(str(c["id"]), OUTPUT_MIME[kind], i, str(c.get("mediaEntId") or "") or None,
                        {"batch": str(batch.get("id") or "")}) for i, c in enumerate(rows))


async def read_batch(ctx, batch: str, rests: Rests, transport=None) -> httpx.Response:
    async with client_for(ctx.account, transport, rests, ctx=ctx) as client:
        return await client.get("%s/%s" % (BATCHES_PATH, quote(batch, safe="")))


def batch_of(resp: httpx.Response) -> dict | None:
    batch = json_of(resp).get("batch") if resp.status_code == 200 else None
    return batch if isinstance(batch, dict) else None


# ---- poll ----------------------------------------------------------------------------------------------------------
async def poll(ctx, job, settings, rests: Rests, *, transport=None,
               rand: Callable[[], float] = random.random,
               now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> PollResult:
    """One read of the job's batch. `transport`, `rand` and `now` are seams for tests."""
    batch = batch_id(job)
    wait = interval_s(settings, rand)
    if not batch:
        return PollResult(Pending("no batch to read", wait, answered=False))
    try:
        resp = await read_batch(ctx, batch, rests, transport)
    except Resting as e:
        return PollResult(Pending(RESTING_NOTE, e.left_s, answered=False))
    except httpx.HTTPError as e:
        return PollResult(Pending("vibes could not be read (%s)" % type(e).__name__, wait, answered=False))
    if is_checkpoint(resp):
        seconds, cooldown = checkpoint_rest(ctx, settings, rests, "vibes security checkpoint on a poll")
        return PollResult(Pending(CHECKPOINT_NOTE, seconds, answered=False), effects=(cooldown,))
    if session_gone(resp):      # the job keeps waiting; the account is doubted at once
        said = "vibes answered %d to the batch read" % resp.status_code
        return PollResult(Pending(said, wait, answered=False), effects=doubt_effects("not signed in: " + said, now()))
    found = batch_of(resp)
    if found is None:
        return PollResult(Pending("vibes answered %d to the batch read" % resp.status_code, wait, answered=False))
    if not is_done(found):
        return PollResult(Pending("", wait), effects=(Success(),))
    why = render_error(found)
    if why is not None:
        return PollResult(Failed(*why, True), effects=(Success(),))
    return PollResult(Done(outputs(found, job.params["kind"])), effects=(Success(),))


# ---- reconcile -----------------------------------------------------------------------------------------------------
async def reconcile(ctx, job, settings, rests: Rests, *, transport=None,
                    now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Reconciled:
    """Did the generate of an unknown job reach vibes? Read its batch: accepted is `Found`; the empty shell is
    `StillUnknown(answered=True)` (never `NotSent`: it cannot prove the send did not happen). `transport` and `now`
    are seams for tests."""
    batch = batch_id(job)
    if not batch:
        return StillUnknown()
    try:
        resp = await read_batch(ctx, batch, rests, transport)
    except (Resting, httpx.HTTPError):
        return StillUnknown()
    if is_checkpoint(resp):
        checkpoint_rest(ctx, settings, rests, "vibes security checkpoint on a reconcile")
        return StillUnknown()
    if session_gone(resp):      # never NotSent: the generate may have gone through before the session ended
        return StillUnknown(effects=doubt_effects("not signed in: vibes answered %d to the batch read"
                                                  % resp.status_code, now()))
    found = batch_of(resp)
    if found is None:
        return StillUnknown()
    if is_accepted(found):
        return Found(batch, {BATCH_KEY: batch}, True)
    return StillUnknown(answered=True)


# ---- fetch ---------------------------------------------------------------------------------------------------------
class DownloadError(RuntimeError):
    """The download did not give the file; the fetcher tries again. Only vibes' own JSON 404 `{"error": ...}` (the
    file does not exist) fails the job at once (`OutputGone`)."""


async def fetch(ctx, out: Output, settings, rests: Rests, *, transport=None) -> AsyncIterator[bytes]:
    """The result file, streamed: `GET /api/download/png|video?id=<content item id>` over the account's proxy. The PNG
    is larger (810x1440) than the size the API reports (720x1280); the file is delivered as downloaded."""
    kind = "image" if out.mime.startswith("image/") else "video"
    async with client_for(ctx.account, transport, rests, ctx=ctx) as client:
        try:
            async with client.stream("GET", DOWNLOAD_PATH[kind] % quote(out.ref, safe=""),
                                     timeout=DOWNLOAD_TIMEOUT_S, headers={"Accept": "*/*"}) as resp:
                if is_checkpoint(resp):
                    checkpoint_rest(ctx, settings, rests, "vibes security checkpoint on a download")
                    raise DownloadError(CHECKPOINT_NOTE)
                # Only the file itself is delivered: an HTML or JSON page (whatever type it claims) would be probed
                # as unreadable and end the job, so it raises here and the fetcher tries again.
                mime = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if resp.status_code == 404 and mime == "application/json":
                    # vibes' app says the file does not exist (live 23/10, a refused item: 404 `{"error": ...}` on
                    # every try for an hour): trying again cannot help, the job fails now with its refund
                    await resp.aread()
                    said = json_of(resp).get("error")
                    if said:
                        raise OutputGone(errors.OUTPUT_MISSING, "vibes answered 404 to the download: %s"
                                         % str(said)[:200])
                if resp.status_code != 200 or mime != out.mime:
                    raise DownloadError("vibes answered %d (%s) to the download" % (resp.status_code, mime[:40]))
                chunks = resp.aiter_bytes()
                head = b""
                async for chunk in chunks:
                    head += chunk
                    if len(head) >= SIGNATURE_BYTES:
                        break
                if not has_signature(kind, head):
                    raise DownloadError("the download is not a %s file" % out.mime)
                yield head
                async for chunk in chunks:
                    yield chunk
        except Resting:
            raise DownloadError(RESTING_NOTE) from None


SIGNATURE_BYTES = 12
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def has_signature(kind: str, head: bytes) -> bool:
    """The file's first bytes: the PNG signature, or an MP4 whose first box is `ftyp`."""
    return head.startswith(PNG_MAGIC) if kind == "image" else head[4:8] == b"ftyp"
