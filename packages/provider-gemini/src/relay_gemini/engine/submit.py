"""Submit: one image job to gemini (`StreamGenerate` with the image tool), and what gemini said as a `SubmitResult`.

The steps: load gemini's page (session rotated first when due); for an edit, upload the one input image (free); write
the job's request id to the job (`ctx.report_ref`); `ctx.mark_sent()`; the ONE send. gemini streams its answer until
the image is drawn (25-28 s measured), so a good answer is `Completed` with the image's `/gg-dl/` link (HOLD).

Money rules that shape this file (each has a test and a mutation):
- The job is on record as sent BEFORE its send leaves: `report_ref` then `mark_sent` immediately before the one POST.
  The write-ahead key of a lost send is the job's exact sent text and its send time (`sent_at`): gemini lists each chat
  with the time its send started and gives the sent text back (FINDINGS 4). No id we send is echoed, and a tracking
  code typed into the prompt is drawn into the image, so the text goes verbatim and the reconciler matches it.
- One send per job, never a second: nothing after `mark_sent` returns `Retry` or `NotTaken`.
- Only a complete answer is a verdict: clean (every line read) and closed by gemini's end item (`Answer.ended`).
  Complete with an image: `Completed`. Complete with a final text and no image: gemini answered for good, the job fails
  with its refund (`gemini.not_entitled` when the text is the "upgrade your subscription" answer, `gemini.refused`
  otherwise, the text kept in the detail). Anything else (no answer, a timeout, a cut or unreadable answer, a status
  that is not 200, an error item, an empty or image-promising final text with no image) is `Ambiguous`: the job becomes
  unknown and the reconciler looks for it; it is never sent again.
- After every send the account's pool rests `gemini.account_min_gap_s`.

Before `mark_sent` every failure is an ordinary retry: a signed-out page `pool.account_logged_out` with a spaced strike,
a proxy that cannot reach gemini `pool.proxy_failed`, an error answer `gemini.http_error`, an upload not taken
`gemini.upload_failed`.
"""

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import (
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
    MODE_AUTO,
    NETWORK_REST_S,
    NOT_ENTITLED_LINK,
)
from relay_gemini.engine.check import doubt_effects, signed_out_why
from relay_gemini.engine.http import Sessions, Site
from relay_gemini.engine.reply import Answer, Image, read_answer

REQUEST_KEY = "request"      # provider_state: the request id of the send (also the job's external_ref until the chat)
CHAT_KEY = "chat"            # provider_state: gemini's conversation id, once known
LOGGED_OUT = "pool.account_logged_out"
PROXY_FAILED = "pool.proxy_failed"
POOLS = ("image",)
IMAGE_PLACEHOLDER = "googleusercontent.com/image_generation_content/"   # the text of an answer that holds an image

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
    """After a send the account's pool rests `gemini.account_min_gap_s` (0: no rest)."""
    gap = int(settings.get("gemini.account_min_gap_s"))
    return tuple(Rest(pool, now + timedelta(seconds=gap), "gemini pacing between two sends")
                 for pool in POOLS) if gap > 0 else ()


def sent_text(job) -> str:
    """The core's framed text (verbatim: the customer's words); a job queued before gemini had frames carries none."""
    return job.prompt if job.frame_id is None or job.sent_text is None else job.sent_text


def request(text: str, ratio: str | None, image: tuple[str, str, str] | None, rid: str) -> tuple[list, dict]:
    """The send's `inner` and headers, as the web app builds them with the image tool chosen (captured 23/10,
    fixtures/gemini/live-261023/web_request_*.json). The anti-bot token the page puts at inner[3] is not needed. A
    ratio goes in the message (`[9][6]`) and as its option id (`[55]`); an edit names the uploaded file at `[3]`."""
    content: list = [text, 0, None, None, None, None, 0]
    if image is not None:
        path, mime, name = image
        content[3] = [[[path, 1, None, mime, str(uuid.uuid4())], name, None, None, None, None, None, None, [0]]]
    if ratio:
        content += [None, None, [None, None, None, None, None, None, [None, [None, ratio]]]]
    inner: list = [None] * 99
    for i, v in {0: content, 1: ["en"], 2: ["", "", "", None, None, None, None, None, None, ""], 4: uuid.uuid4().hex,
                 6: [0], 7: 1, 10: 1, 11: 0, 17: [[0]], 18: 0, 27: 1, 30: [4, 16, 19], 41: [1], 49: IMAGE_TOOL,
                 53: 0, 54: [], 55: [[C.RATIO_IDS[ratio]]] if ratio else [], 59: rid, 61: [], 67: 0, 68: 1, 79: 6,
                 80: 1, 91: 1, 96: 0, 98: 1}.items():
        inner[i] = v
    headers = {
        "x-goog-ext-525001261-jspb": json.dumps([1, None, None, None, MODE_AUTO, None, None, 0, CAPABILITIES, None,
                                                 None, 1, None, None, 6, 1, rid]),
        "x-goog-ext-525005358-jspb": json.dumps([rid, 1]),
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0,0,0]",
    }
    return inner, headers


def outputs_of(images: tuple[Image, ...], chat: str | None) -> tuple[Output, ...]:
    return tuple(Output(img.link, img.mime, i, img.name, {"chat": chat, "width": img.width, "height": img.height})
                 for i, img in enumerate(images))


def judge(status: int, answer: Answer | None) -> tuple[str, object]:
    """What a send's (or a chat read's) answer means: ("done", images) / ("refused", (code, text)) / ("unknown", why).
    Only a complete answer (200, clean, ended) is ever "done" or "refused"."""
    if status != 200 or answer is None:
        return "unknown", "gemini answered %d" % status
    if not answer.clean or not answer.ended:
        return "unknown", "the answer was cut or could not be read"
    if answer.images:
        return "done", answer.images
    if answer.errors:
        return "unknown", "the answer carried an error item"
    final = answer.texts[-1].strip() if answer.texts else ""
    if not final or IMAGE_PLACEHOLDER in final:
        return "unknown", "the answer named no image record"
    if NOT_ENTITLED_LINK in final:
        return "refused", (errors.NOT_ENTITLED, final)
    return "refused", (errors.REFUSED, final)


def answer_result(status: int, answer: Answer | None, settings, now: datetime) -> SubmitResult:
    """A send's verdict. Never `Retry` or `NotTaken`: the job was sent."""
    rest = pacing(settings, now)
    verdict, what = judge(status, answer)
    if verdict == "done":
        return _result(Completed(outputs_of(what, answer.cid)), Success(), *rest)
    if verdict == "refused":
        code, text = what
        return _result(Fail(code, "gemini answered: %s" % text[:300], {CHAT_KEY: answer.cid}), Success(), *rest)
    return _result(Ambiguous(what), *rest)


async def submit(ctx, job, settings, sessions: Sessions, *, transport=None,
                 read_media: Callable[..., Awaitable[bytes]] = read_input_media,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 wall: Callable[[], float] = time.time) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` and `ctx.mark_sent` (a lost lease, a stopping worker) reach the
    core unchanged. The keyword arguments are seams for tests."""
    sent = False
    try:
        async with Site(ctx, sessions, settings, transport=transport, clock=wall) as site:
            page = await site.load()
            if page.status != 200:
                return _result(Retry(errors.HTTP_ERROR))
            if not page.signed_in:
                return _result(Retry(LOGGED_OUT), *doubt_effects(signed_out_why(site), now()))
            image = None
            if job.operation == "image_to_image":
                ref = (await job.inputs())[0]
                name = "input.%s" % (ref.mime.split("/")[-1] or "png")
                path = await site.upload(name, await read_media(ref))
                if path is None:
                    return _result(Retry(errors.UPLOAD_FAILED))
                image = (path, ref.mime, name)
            rid = str(uuid.uuid4()).upper()
            inner, headers = request(sent_text(job), job.params.get("ratio"), image, rid)
            await ctx.report_ref(rid, {REQUEST_KEY: rid})
            await ctx.mark_sent()
            sent = True
            try:
                status, body = await site.send(inner, headers, GENERATE_TIMEOUT_S)
            except httpx.HTTPError as e:
                return _result(Ambiguous("the send got no full answer (%s)" % type(e).__name__),
                               *pacing(settings, now()))
            answer = read_answer(body) if status == 200 else None
            if answer is not None and answer.cid:
                await ctx.report_ref(answer.cid, {REQUEST_KEY: rid, CHAT_KEY: answer.cid})
            return answer_result(status, answer, settings, now())
    except httpx.HTTPError as e:
        why = "gemini could not be reached (%s)" % type(e).__name__     # the text may carry the proxy URL
        if sent:       # never reached: the send's own errors are caught above. Kept as the money guard.
            return _result(Ambiguous(why))
        return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
