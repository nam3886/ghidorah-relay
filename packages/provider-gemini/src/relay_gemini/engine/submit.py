"""Submit: one job to gemini (`StreamGenerate` with the image or the video tool), and what gemini said as a
`SubmitResult`.

The steps: load gemini's page at the account's `/u/N` (session rotated first when due); check it is the Google account
the credential names; for an edit or an image to video, upload the one input image (free); write the job's request id
to the job (`ctx.report_ref`); `ctx.mark_sent()`; the ONE send. An image send streams its answer until the image is
drawn (25-28 s measured), so a good answer is `Completed` with the image's `/gg-dl/` link (HOLD). A video send answers
in 10-33 s that it is "generating your video" (FINDINGS 7): `Accepted` with the chat, which the poll then reads until
the video is there (1-2 min measured, DETACH).

Money rules that shape this file (each has a test and a mutation):
- The job is on record as sent BEFORE its send leaves: `report_ref` then `mark_sent` immediately before the one POST.
  The write-ahead key of a lost send is the job's exact sent text and its send time (`sent_at`): gemini lists each chat
  with the time its send started and gives the sent text back (FINDINGS 4). No id we send is echoed, and a tracking
  code typed into the prompt is drawn into the image, so the text goes verbatim and the reconciler matches it.
- One send per job, never a second: nothing after `mark_sent` returns `Retry` or `NotTaken`.
- Only a complete answer is a verdict: clean (every line read) and closed by gemini's end item (`Answer.ended`).
  Complete with the job's media: `Completed`. A video send complete with the "generating" item and a chat: `Accepted`.
  Complete with a final text and no media: gemini answered for good, the job fails with its refund
  (`gemini.not_entitled` for the "upgrade your subscription" answer, `gemini.video_limit` for "I can't create more
  videos for you today" with the account's video pool rested `gemini.video_limit_rest_s`, `gemini.refused` otherwise,
  the text kept in the detail). Anything else (no answer, a timeout, a cut or unreadable answer, a status that is not
  200, an error item, an empty or media-promising final text with no media, a "generating" answer with no chat) is
  `Ambiguous`: the job becomes unknown and the reconciler looks for it; it is never sent again.
- After every send the account's pools rest `gemini.account_min_gap_s`.

Before `mark_sent` every failure is an ordinary retry: a signed-out page `pool.account_logged_out` with a spaced strike,
a page of another Google account than the credential names (or an account the check has not resolved yet: no mode
known) `gemini.account_unresolved` with a rest and a recheck, a proxy that cannot reach gemini `pool.proxy_failed`, an
error answer `gemini.http_error`, an upload not taken `gemini.upload_failed`.
"""

import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Completed,
    Cooldown,
    Fail,
    Output,
    ProxyBad,
    Rest,
    Retry,
    SubmitResult,
    Success,
)
from relay_gemini import catalog as C
from relay_gemini import errors
from relay_gemini.constants import (
    CAPABILITIES,
    GENERATE_TIMEOUT_S,
    IMAGE_TOOL,
    NETWORK_REST_S,
    NOT_ENTITLED_LINK,
    QUOTA_WORDS,
    VIDEO_LIMIT_PHRASES,
    VIDEO_TOOL,
)
from relay_gemini.engine.check import doubt_effects, signed_out_why
from relay_gemini.engine.http import Sessions, Site
from relay_gemini.engine.reply import Answer, Image, Video, read_answer

REQUEST_KEY = "request"      # provider_state: the request id of the send (also the job's external_ref until the chat)
CHAT_KEY = "chat"            # provider_state: gemini's conversation id, once known
LOGGED_OUT = "pool.account_logged_out"
PROXY_FAILED = "pool.proxy_failed"
POOLS = ("image", "video")
IMAGE_PLACEHOLDER = "googleusercontent.com/image_generation_content/"   # the text of an answer that holds an image
VIDEO_PLACEHOLDER = "googleusercontent.com/generated_video_content/"    # ... and of one that holds a video
_MODE = re.compile(r"[0-9a-f]{16}")

_store = None


async def read_input_media(ref) -> bytes:
    """One job input image from the object store, the store created on first use (a text job needs none). The one
    place the engine reaches past `RunCtx` for a file: the protocol hands out only a `MediaRef`."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


def pacing(settings, now: datetime) -> tuple[Rest, ...]:
    """After a send the account's pools rest `gemini.account_min_gap_s` (0: no rest)."""
    gap = int(settings.get("gemini.account_min_gap_s"))
    return tuple(Rest(pool, now + timedelta(seconds=gap), "gemini pacing between two sends")
                 for pool in POOLS) if gap > 0 else ()


def limit_rest(settings, now: datetime) -> Rest:
    """gemini said the account makes no more videos for now: its video pool rests `gemini.video_limit_rest_s`."""
    until = now + timedelta(seconds=int(settings.get("gemini.video_limit_rest_s")))
    return Rest("video", until, "gemini answered the account's video limit is reached")


def is_video(job) -> bool:
    return job.operation in ("text_to_video", "image_to_video")


def mode_of(account) -> str | None:
    """The account's mode id the check read (otAQ7b); None until a check has."""
    mode = (account.provider_state or {}).get("mode")
    return mode if isinstance(mode, str) and _MODE.fullmatch(mode) else None


def sent_text(job) -> str:
    """The core's framed text (verbatim: the customer's words); a job queued before gemini had frames carries none."""
    return job.prompt if job.frame_id is None or job.sent_text is None else job.sent_text


def request(text: str, ratio: str | None, image: tuple[str, str, str] | None, rid: str, mode: str, *,
            video: bool = False) -> tuple[list, dict]:
    """The send's `inner` and headers, as the web app builds them with the image tool chosen (captured 23/10,
    fixtures/gemini/live-261023/web_request_*.json) or the video tool (24/10, live-261024/web_request_i2v.json). The
    anti-bot token the page puts at inner[3] is not needed. An image ratio goes in the message (`[9][6]`) and as its
    option id (`[55]`); an edit names the uploaded file at `[3]`. A video always names its ratio option (`[55]`); a text
    to video carries the ratio in the message too, an image to video the web app's `[[null, null, null, 1]]` there and
    the file at `[3]` in the shorter entry the video tool sends."""
    content: list = [text, 0, None, None, None, None, 0]
    if image is not None:
        path, mime, name = image
        entry = [path, 1, None, mime, str(uuid.uuid4())]
        content[3] = [[entry, name]] if video else [[entry, name, None, None, None, None, None, None, [0]]]
    if video:
        content += [None, None, [None, None, None, None, None, None,
                                 [[None, None, None, 1]] if image is not None else [None, [None, ratio]]]]
    elif ratio:
        content += [None, None, [None, None, None, None, None, None, [None, [None, ratio]]]]
    option = [[C.VIDEO_RATIO_IDS[ratio]]] if video else [[C.RATIO_IDS[ratio]]] if ratio else []
    inner: list = [None] * 99
    for i, v in {0: content, 1: ["en"], 2: ["", "", "", None, None, None, None, None, None, ""], 4: uuid.uuid4().hex,
                 6: [0], 7: 1, 10: 1, 11: 0, 17: [[0]], 18: 0, 27: 1, 30: [4, 16, 19], 41: [1],
                 49: VIDEO_TOOL if video else IMAGE_TOOL, 53: 0, 54: [], 55: option, 59: rid, 61: [], 67: 0, 68: 1,
                 79: 6, 80: 1, 91: 1, 96: 0, 98: 1}.items():
        inner[i] = v
    headers = {
        "x-goog-ext-525001261-jspb": json.dumps([1, None, None, None, mode, None, None, 0, CAPABILITIES, None,
                                                 None, 1, None, None, 6, 1, rid]),
        "x-goog-ext-525005358-jspb": json.dumps([rid, 1]),
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0,0,0]",
    }
    return inner, headers


def outputs_of(media: tuple[Image | Video, ...], chat: str | None) -> tuple[Output, ...]:
    def meta(m):
        extra = {"duration_s": m.duration_s} if isinstance(m, Video) else {}
        return {"chat": chat, "width": m.width, "height": m.height, **extra}
    return tuple(Output(m.link, m.mime, i, m.name, meta(m)) for i, m in enumerate(media))


def judge(status: int, answer: Answer | None, *, video: bool = False) -> tuple[str, object]:
    """What a send's (or a chat read's) answer means: ("done", media) / ("taken", None) / ("refused", (code, text)) /
    ("unknown", why). Only a complete answer (200, clean, ended) is ever "done", "taken" or "refused". `video`: the
    job's media is a video, and a "generating your video" answer is "taken" (sent and accepted, not done)."""
    if status != 200 or answer is None:
        return "unknown", "gemini answered %d" % status
    if not answer.clean or not answer.ended:
        return "unknown", "the answer was cut or could not be read"
    if answer.errors:
        return "unknown", "the answer carried an error item"
    media = answer.videos if video else answer.images
    if media:
        return "done", media
    final = answer.texts[-1].strip() if answer.texts else ""
    # the proven refusals first: the free plan's "upgrade" answer carries the "generating" item too (measured 23/10)
    if NOT_ENTITLED_LINK in final:
        return "refused", (errors.NOT_ENTITLED, final)
    if video and is_video_limit(final):
        return "refused", (errors.VIDEO_LIMIT, final)
    if video and answer.generating:     # taken, whatever other text comes with it: never a refusal while it renders
        return "taken", None
    if not final or IMAGE_PLACEHOLDER in final or VIDEO_PLACEHOLDER in final:
        return "unknown", "the answer named no %s record" % ("video" if video else "image")
    return "refused", (errors.REFUSED, final)


def plain(text: str) -> str:
    """The text lowercased, its apostrophes made straight, "cannot" made "can't", every other punctuation a space."""
    t = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")
    t = t.replace("cannot", "can't").replace("can not", "can't")
    return " ".join(re.sub(r"[^\w']+", " ", t).split())


def is_video_limit(text: str) -> bool:
    """Is it gemini's "no more videos for now" answer (measured: "I can't create more videos for you today, ...")?"""
    t = plain(text)
    return any(p in t for p in VIDEO_LIMIT_PHRASES)


def refusal_rests(settings, now: datetime, code: str, text: str, video: bool) -> tuple[Rest, ...]:
    """A video refusal that is the limit answer, or reads like a quota (in any of the languages listed), rests the
    account's video pool: the account is not sent another video until the rest ends."""
    quota = video and code == errors.REFUSED and any(w in plain(text) for w in QUOTA_WORDS)
    return (limit_rest(settings, now),) if code == errors.VIDEO_LIMIT or quota else ()


def answer_result(status: int, answer: Answer | None, settings, now: datetime, *, video: bool = False,
                  rid: str | None = None) -> SubmitResult:
    """A send's verdict. Never `Retry` or `NotTaken`: the job was sent."""
    rest = pacing(settings, now)
    verdict, what = judge(status, answer, video=video)
    if verdict == "done":
        return _result(Completed(outputs_of(what, answer.cid)), Success(), *rest)
    if verdict == "taken":
        if not answer.cid:
            return _result(Ambiguous("gemini took the video but named no chat"), *rest)
        return _result(Accepted(answer.cid, {REQUEST_KEY: rid, CHAT_KEY: answer.cid}), Success(), *rest)
    if verdict == "refused":
        code, text = what
        return _result(Fail(code, "gemini answered: %s" % text[:300], {CHAT_KEY: answer.cid}), Success(), *rest,
                       *refusal_rests(settings, now, code, text, video))
    return _result(Ambiguous(what), *rest)


async def submit(ctx, job, settings, sessions: Sessions, *, transport=None,
                 read_media: Callable[..., Awaitable[bytes]] = read_input_media,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 wall: Callable[[], float] = time.time) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` and `ctx.mark_sent` (a lost lease, a stopping worker) reach the
    core unchanged. The keyword arguments are seams for tests."""
    sent, video = False, is_video(job)
    try:
        async with Site(ctx, sessions, settings, transport=transport, clock=wall) as site:
            page = await site.load()
            if page.status != 200:
                return _result(Retry(errors.HTTP_ERROR))
            if not page.signed_in:
                return _result(Retry(LOGGED_OUT), *doubt_effects(signed_out_why(site), now()))
            mode = mode_of(ctx.account)
            if mode is None or (site.email and (page.email or "").lower() != site.email):
                why = "gemini's page is not the account's Google account, or the account was not checked yet"
                return _result(Retry(errors.UNRESOLVED),
                               Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S), recheck=True))
            image = None
            if job.operation in ("image_to_image", "image_to_video"):
                ref = (await job.inputs())[0]
                name = "input.%s" % (ref.mime.split("/")[-1] or "png")
                path = await site.upload(name, await read_media(ref))
                if path is None:
                    return _result(Retry(errors.UPLOAD_FAILED))
                image = (path, ref.mime, name)
            rid = str(uuid.uuid4()).upper()
            ratio = job.params.get("ratio") or (C.VIDEO_RATIO if video else None)
            inner, headers = request(sent_text(job), ratio, image, rid, mode, video=video)
            await ctx.report_ref(rid, {REQUEST_KEY: rid})
            await ctx.mark_sent()
            sent = True
            try:
                status, body = await site.send(inner, headers, GENERATE_TIMEOUT_S)
            except httpx.HTTPError as e:
                return _result(Ambiguous("the send got no full answer (%s)" % type(e).__name__),
                               *pacing(settings, now()))
            answer = read_answer(body)          # judged with its status, never instead of it
            if status == 200 and answer.cid:
                await ctx.report_ref(answer.cid, {REQUEST_KEY: rid, CHAT_KEY: answer.cid})
            return answer_result(status, answer, settings, now(), video=video, rid=rid)
    except httpx.HTTPError as e:
        why = "gemini could not be reached (%s)" % type(e).__name__     # the text may carry the proxy URL
        if sent:       # never reached: the send's own errors are caught above. Kept as the money guard.
            return _result(Ambiguous(why))
        return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
