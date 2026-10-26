"""After the submit: the poll of a taken job, the reconcile of a send whose answer was lost, and the fetch.

All three read only. The job's side chat id was persisted before the send (`external_ref`, `provider_state.session_id`):
- reconcile reads that chat: any event in it (the job's own message first) proves the order is there, `Found`, and
  the poll follows it. An empty or unknown chat proves nothing (muse may still be writing it): `StillUnknown`,
  answered; `NotSent` is never returned, so a lost send is never sent twice; the render deadline refunds it;
- the poll reads the chat with the submit's rules (`chat.verdict`); "settled" is the same reading on two polls at
  least `muse.settle_s` apart (kept in `provider_state.seen`);
- the fetch reads `GET /fs/raw/<ref>` in the tunnel (there is no public URL): 404 is gone for good.
"""

import asyncio
import logging
from datetime import datetime

from relay_core.providers import Done, Failed, Found, OutputGone, Pending, PollResult, StillUnknown
from relay_muse import errors
from relay_muse.engine.chat import (
    history_events,
    history_path,
    is_media_ref,
    is_session,
    made_note,
    meter_effects,
    outputs,
    paid_of,
    read_history,
    signature,
    verdict,
    want_of,
)
from relay_muse.engine.link import Deps, opened

log = logging.getLogger("relay_muse.results")


def _session_of(job) -> str | None:
    sid = job.external_ref or (job.provider_state or {}).get("session_id")
    return sid if is_session(sid) else None


async def _events(ctx, settings, deps: Deps, session: str) -> list[dict] | None:
    """The chat's events; [] when muse does not know the chat (404, as for a chat never written); None when the
    answer is no history."""
    async with opened(ctx, settings, deps, chat=False) as (_web, tunnel):
        reply = await tunnel.call("GET", history_path(session), timeout=settings.get("muse.call_timeout_s"))
    return [] if reply.status == 404 else history_events(reply)


async def _say(ctx, text: str) -> None:
    try:
        await ctx.event(text, "poll", "engine")
    except Exception as e:      # noqa: BLE001 - diagnostics never fail the read
        log.warning("event not recorded: %s", type(e).__name__)


def _settled(state, seen: list, now: datetime, settle_s: float) -> bool:
    if state.get("seen") != seen:
        return False
    try:
        since = datetime.fromisoformat(str(state.get("seen_at")))
    except ValueError:
        return False
    return since.tzinfo is not None and (now - since).total_seconds() >= settle_s


async def poll(ctx, job, settings, *, deps: Deps | None = None) -> PollResult:
    deps = deps or Deps()
    interval = settings.get("muse.poll_interval_s")
    session = _session_of(job)
    if session is None:
        return PollResult(Pending("the job names no muse chat", next_in_s=interval, answered=False))
    try:
        events = await _events(ctx, settings, deps, session)
    except Exception as e:      # noqa: BLE001 - a failed read: polled again, never a verdict
        return PollResult(Pending("the chat could not be read (%s)" % type(e).__name__, next_in_s=interval,
                                  answered=False))
    if events is None:
        return PollResult(Pending("muse gave no history for the chat", next_in_s=interval, answered=False))
    kind, state, now = job.params.get("kind"), job.provider_state or {}, deps.now()
    reading = read_history(events, kind)
    seen = signature(reading)
    found = verdict(reading, want_of(job.params), _settled(state, seen, now, settings.get("muse.settle_s")))
    if found == "done":
        await _say(ctx, made_note(reading, want_of(job.params), kind, job.id))
        return PollResult(Done(outputs(reading, want_of(job.params), session, **paid_of(job))),
                          effects=meter_effects(ctx.account, settings, kind, len(reading.media)))
    if found == "refused":
        return PollResult(Failed(errors.REFUSED, "muse answered with no %s: %s"
                                 % (kind, " ".join(reading.text.split())[:300])))
    since = state.get("seen_at") if state.get("seen") == seen else now.isoformat()
    return PollResult(Pending("muse is still working", next_in_s=interval,
                              state_patch={"seen": seen, "seen_at": since}))


async def reconcile(ctx, job, settings, *, deps: Deps | None = None):
    session = _session_of(job)
    if session is None:
        return StillUnknown()
    try:
        events = await _events(ctx, settings, deps or Deps(), session)
    except Exception as e:      # noqa: BLE001 - the read failed: it proves nothing
        log.info("muse reconcile of %s: the chat could not be read (%s)", session, type(e).__name__)
        return StillUnknown()
    if events:
        return Found(session, {"session_id": session})
    return StillUnknown(answered=events is not None)


async def fetch(ctx, out, settings, *, deps: Deps | None = None):
    """The bytes of one result, read through the tunnel. An async generator, as the core expects."""
    if not is_media_ref(out.ref):
        raise OutputGone(errors.OUTPUT_MISSING, "the result is not a file of muse's media folder")
    async with opened(ctx, settings, deps or Deps(), chat=False) as (_web, tunnel):
        reply = await tunnel.call("GET", "/fs/raw/" + out.ref, timeout=settings.get("muse.fetch_timeout_s"))
    if reply.status == 404:
        raise OutputGone(errors.OUTPUT_MISSING, "muse has no file for this result")
    if reply.status != 200 or not reply.body or len(reply.body) > settings.get("muse.media_max_mb") * 2**20:
        raise RuntimeError("muse answered %d with %d bytes to the file read" % (reply.status, len(reply.body)))
    if out.mime.startswith("video/") and out.meta.get("duration"):
        off = video_off(await probe(reply.body), out.meta)
        if off:     # delivered all the same (any output is a full charge); measured here
            log.warning("muse job %s output %d: %s", out.meta.get("job_id"), out.idx, off)
            await _say(ctx, "the video is not what was paid: %s; delivered all the same" % off)
    yield reply.body


LENGTH_SLACK_S = 1.0      # a video this much longer or shorter than paid is noted (measured: to the frame)
RATIO_SLACK = 0.05        # and one whose width/height is this far (relative) from the paid ratio


async def probe(data: bytes) -> dict:
    """ffprobe's streams and format of a file; {} when it cannot be read (the fetcher's own probe decides)."""
    import json
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data)
        f.flush()
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", f.name,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 30)
            return json.loads(out or b"{}")
        except (OSError, ValueError, TimeoutError):
            return {}


def video_off(info: dict, paid) -> str:
    """How a probed video differs from its paid length and ratio ("" when it does not, or cannot be told)."""
    video = next((s for s in info.get("streams") or () if s.get("codec_type") == "video"), None)
    if not video:
        return ""
    found = []
    try:
        length = float((info.get("format") or {}).get("duration") or video.get("duration"))
        if abs(length - float(paid["duration"])) > LENGTH_SLACK_S:
            found.append("%.2f s, %s s paid" % (length, paid["duration"]))
    except (TypeError, ValueError, KeyError):
        pass
    ratio, width, height = paid.get("ratio"), video.get("width"), video.get("height")
    if ratio and width and height:
        w, h = (int(x) for x in ratio.split(":"))
        if abs(width / height - w / h) > RATIO_SLACK * (w / h):
            found.append("%dx%d, %s paid" % (width, height, ratio))
    return "; ".join(found)
