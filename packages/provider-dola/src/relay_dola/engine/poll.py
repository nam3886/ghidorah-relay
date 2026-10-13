"""Poll: one read of the job's conversation, mapped to a `PollResult` (contracts 5.4.1 "poll"). Reads only: it works
for a disabled account too and NEVER spends credits. The core calls it every `next_in_s` (30 s) until the job ends.

The v4 runner looped for 120 s per round and wrote its conclusions into the database; here one call is one read and the
conclusion is the return value.

Provider state this module writes through `state_patch` (the spec's `deadline_cause` reads it):
- `signed_out`: dola said the session is dead (712012001). The job keeps waiting (fresh cookies may still deliver); a
  later good read writes `False`.
- `no_recheck`: a copyright block first seen after the send: Re-check cannot change it.
- `watermarked_only`: only watermarked links so far; at the deadline the cause is `render.watermarked_only`.
"""

import time
from collections.abc import Callable
from datetime import datetime

from relay_core.providers import Done, Failed, Output, Pending, PollResult, Success, Warning
from relay_dola import constants, errors
from relay_dola.engine import resolver
from relay_dola.engine.http import DolaHttp
from relay_dola.engine.resolver import ChainError

SIGNED_OUT_NOTE = "the account holding this conversation is signed out: waiting for fresh cookies"
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
DRIFT_FMT = "delivered %.1fs but %ds was requested: dola may have capped or segmented it"


def is_image(job) -> bool:
    """An image job (its result is a grid of images): the plan's `params.kind`."""
    return job.params.get("kind") == "image"


def image_mime(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for ext, mime in IMAGE_MIME.items():
        if path.endswith(ext):
            return mime
    return "image/png"      # the clean master is a PNG (measured: image_ori_raw ends .png)


def _since(job) -> float | None:
    moment: datetime | None = job.render_since
    return moment.timestamp() if moment else None


def _pending(note: str = "", patch: dict | None = None, effects=(), *, answered: bool = True) -> PollResult:
    """`answered` False: the read itself did not work (a chain error, a dead session): the core must not take it for the
    read that says "not ready" when the render deadline is past."""
    return PollResult(Pending(note, constants.POLL_INTERVAL_S, patch or {}, answered), effects=tuple(effects))


def _chain_failure(exc: ChainError, healed: dict) -> PollResult:
    if exc.code == resolver.ACCOUNT_DEAD_SESSION:
        return _pending(SIGNED_OUT_NOTE, {"signed_out": True}, answered=False)
    if exc.transient:
        return _pending(str(exc), answered=False)               # HTTP error, not JSON, unknown code: read again
    # Not this job's conversation (wrong id / wrong text): the order is not proven ours, so nothing is "taken".
    taken = exc.code not in (resolver.ACCOUNT_WRONG_CONVERSATION, resolver.ACCOUNT_PROMPT_MISMATCH)
    return PollResult(Failed(exc.code, str(exc), taken, {**healed, **exc.state_patch}))


def _done_video(job, conversation: str, read: resolver.VideoRead, healed: dict) -> PollResult:
    clean = [c for c in read.ready if c.clean]
    if not clean:
        # Only watermarked links: poll again, never re-send (the video exists upstream; a new order could not make
        # `main_url` appear). The deadline turns this into `render.watermarked_only`.
        return _pending("no main_url yet, only the watermarked link", {**healed, "watermarked_only": True},
                        (Success(),))
    warnings = []
    wanted = job.params.get("duration")
    for i, cell in enumerate(clean):
        length = resolver.cell_duration(cell.creation)
        if wanted and length is not None and abs(length - float(wanted)) > constants.DURATION_TOLERANCE_S:
            drift = DRIFT_FMT % (length, int(wanted))
            warnings.append(Warning(errors.WARN_DURATION_DRIFT, drift if len(clean) == 1 else "video %d: %s"
                                    % (i + 1, drift)))
    if len(clean) < len(read.ready):
        warnings.append(Warning(errors.WARN_PARTLY_WATERMARKED,
                                "%d more video(s) in this conversation came only with a watermark and were not "
                                "delivered" % (len(read.ready) - len(clean))))
    return PollResult(Done(resolver.video_outputs(clean, conversation)), effects=(Success(),),
                      warnings=tuple(warnings))


def _copyright_seen(job, body: dict, read: resolver.VideoRead, healed: dict, now: float) -> PollResult:
    """v4's second copyright path: a block anywhere in the conversation (no time, or not the newest reply) that WE first
    saw at or after the send / Re-check ends the job at once, with nothing still rendering. The first sighting is kept
    in `copyright_seen_at`, so a Re-check later does not read the same old block as new."""
    since = _since(job)
    block = None if read.pending else resolver.copyright_block_anywhere(body, since)
    if not block:
        return _pending("", healed, (Success(),))
    seen_at = job.provider_state.get("copyright_seen_at")
    seen_at = now if seen_at is None else float(seen_at)
    patch = {**healed, "copyright_seen_at": seen_at}
    if since is not None and seen_at >= since:
        return PollResult(Failed(errors.CONTENT_REFUSED, "dola blocked the video for copyright protection: %s"
                                 % block[:200], True, {**patch, "no_recheck": True}))
    return _pending("", patch, (Success(),))


async def poll(ctx, job, settings, *, http: DolaHttp | None = None,
               clock: Callable[[], float] = time.time) -> PollResult:
    """One read. `http` and `clock` are seams for tests; the engine passes neither."""
    conversation = job.external_ref
    if not conversation:
        return _pending("no conversation to read yet", answered=False)
    http = http or DolaHttp.for_account(ctx.account)
    # a good read clears `signed_out`; the same patch rides on every outcome that follows a good read
    healed = {"signed_out": False} if job.provider_state.get("signed_out") else {}
    try:
        body = await resolver.fetch_chain(http, conversation, job.marker or job.prompt)
    except ChainError as exc:
        return _chain_failure(exc, healed)
    if job.provider_state.get("watermarked_only"):
        healed = {**healed, "watermarked_only": False}      # set again below when it still holds
    text = resolver.recent_conversation_text(body, constants.EVENT_TEXT_MAX)
    if text:
        await ctx.event(text, "resolve", "upstream")        # dola's newest words, within the event cap
    try:
        if is_image(job):
            urls, _ = resolver.read_images(body)
            if not urls:
                return _pending("", healed, (Success(),))
            return PollResult(Done(image_outputs(urls, conversation)), effects=(Success(),))
        read = resolver.read_video(body, since=_since(job), now=clock())
    except ChainError as exc:
        return _chain_failure(exc, healed)
    if not read.ready:
        return _copyright_seen(job, body, read, healed, clock())
    return _done_video(job, conversation, read, healed)


def image_outputs(urls: list[str], conversation: str) -> tuple[Output, ...]:
    """The clean images of one grid, numbered from 0; the id is the conversation and position (dola gives none)."""
    return tuple(Output(u, image_mime(u), i, "%s:%d" % (conversation, i), {"conversation_id": conversation})
                 for i, u in enumerate(urls))
