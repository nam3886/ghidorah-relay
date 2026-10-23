"""Submit: one job to flow, and what flow said as a `SubmitResult`. An image is `ogiZ0b` (BatchGenerateImages, HOLD);
a video is `YhhmEf` (Veo text to video, DETACH: answered at once, then polled).

The steps: load the project page (session rotated first when due); for a video, the account's Flow credits (`nzlxg`,
free) must cover it; then, per try: a reCAPTCHA token for the job's page action (IMAGE_GENERATION, VIDEO_GENERATION);
a new batch id written to the job (`ctx.report_ref`, with every batch id tried and the project); `ctx.mark_sent()`; the
ONE generate POST of that try. flow holds an image's reply until it is drawn (13-17 s), so a good image reply is
`Completed` with the signed `flow-content.google` link of the JPEG. A video's reply comes in about 3 s with the new
credit balance and the job's media record (status 6, queued): `Accepted`, its media id kept for the poll.

Money rules that shape this file (each has a test and a mutation):
- The batch id and the project are on the job BEFORE its generate is sent: a reply that is lost is looked up by the
  batch id in the project's listing (FINDINGS 6).
- `ctx.mark_sent()` runs immediately before each generate POST, never after.
- One generate POST per try. A try is followed by another only when flow's reply was the immediate reCAPTCHA refusal:
  HTTP 200, the reply item there with NO payload and an envelope code 7 or 8 (`Reply.refused_before_anything`).
  Measured on images: such a refusal comes in 0.4 s, nothing is listed, nothing is charged, so a new try with a new
  token and a new batch id cannot make a second result. (A video refusal was never captured; the shape is the same
  transport's and is held to the same exact match: FINDINGS "i2i/t2v/i2v build 24/10".) When the tries run out (or the
  next would not end inside the submit bound, or a token cannot be had after a refusal) the job goes back to the queue:
  `NotTaken`, nothing was made.
- Any other reply that is not a drawn image, or for a video not the recorded acceptance (our batch echoed, a media
  record of our project), is `Ambiguous`: the job becomes unknown and the reconciler looks for its batch ids; it is
  never sent again. A refusal or an out-of-credits answer to a video send was never captured, so it is ambiguous too:
  never listed, the render deadline ends it with its refund.
- After every generate (taken, refused or unclear) the account's pools rest `flow.account_min_gap_s`: the pacing
  between two generates of one account.

Before the first `mark_sent` every failure is an ordinary retry: a signed-out page `pool.account_logged_out` with a
spaced strike, too few Flow credits for a video `pool.account_out_of_points`, a proxy that cannot reach flow
`pool.proxy_failed`, an error answer `flow.http_error`, no token `flow.captcha_failed`.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    NotTaken,
    Output,
    ProxyBad,
    Rest,
    Retry,
    SubmitResult,
    Success,
)
from relay_flow import errors
from relay_flow.constants import (
    API_TIMEOUT_S,
    BASE_URL,
    CAPTCHA_TIMEOUT_S,
    GENERATE_TIMEOUT_S,
    IMAGE_ACTION,
    MEDIA_HOSTS,
    NETWORK_REST_S,
    OUTPUT_MIME,
    SUBMIT_TIMEOUT_S,
    VIDEO_ACTION,
)
from relay_flow.engine.captcha import CaptchaError
from relay_flow.engine.check import PROJECT_KEY, create_project, doubt_effects, signed_out_why
from relay_flow.engine.http import Reply, Sessions, Site, request_context

GENERATE = "ogiZ0b"
GENERATE_VIDEO = "YhhmEf"
CREDITS = "nzlxg"
BATCH_KEY = "batch"          # provider_state: the batch id of the last try (also the job's external_ref)
BATCHES_KEY = "batches"      # provider_state: every batch id this job sent; the reconciler looks for any of them
MEDIA_KEY = "media"          # provider_state: flow's media id of the job's result, once known
WORKFLOW_KEY = "workflow"    # provider_state: flow's workflow id of a video, once known
LOGGED_OUT = "pool.account_logged_out"
OUT_OF_POINTS = "pool.account_out_of_points"
PROXY_FAILED = "pool.proxy_failed"
POOLS = ("image", "video")
MEDIA_PREFIX = "https://%s/image/" % MEDIA_HOSTS[0]


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


def is_video(job) -> bool:
    return job.operation == "text_to_video"


def pacing(settings, now: datetime) -> tuple[Rest, ...]:
    """After a generate the account's pools rest `flow.account_min_gap_s` (0: no rest)."""
    gap = int(settings.get("flow.account_min_gap_s"))
    return tuple(Rest(pool, now + timedelta(seconds=gap), "flow pacing between two generates")
                 for pool in POOLS) if gap > 0 else ()


def media_link(record, prefix: str = MEDIA_PREFIX) -> str | None:
    """The first signed `flow-content.google` link with `prefix` anywhere in a media record."""
    if isinstance(record, str):
        return record if record.startswith(prefix) else None
    if isinstance(record, list):
        for part in record:
            link = media_link(part, prefix)
            if link is not None:
                return link
    return None


def outputs_of(payload, batch: str) -> tuple[Output, ...]:
    """One output per drawn media record (`payload[0]`): the signed link, and flow's media id."""
    records = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else []
    out = []
    for record in records:
        link = media_link(record)
        if link is not None and isinstance(record, list) and record and isinstance(record[0], str):
            out.append(Output(link, OUTPUT_MIME, len(out), record[0], {"batch": batch}))
    return tuple(out)


def credits_of(payload) -> int | None:
    """The balance of a `nzlxg` answer `[balance, 3, 8, 1, null, balance, [refresh s, ns]]` (FINDINGS 2)."""
    if isinstance(payload, list) and payload and type(payload[0]) is int and payload[0] >= 0:
        return payload[0]
    return None


def video_taken(payload, batch: str, project: str) -> tuple[str, str, int | None] | None:
    """(media id, workflow id, new balance) of the recorded video acceptance (fixture video_submit_try1):
    `[null, <balance>, [[<workflow id>, _, _, [title, _, _, _, <media id>, <batch id>, _], <project>]],
    [[<media id>, <project>, <workflow id>, ...]]]`, with OUR batch id echoed and both records of our project.
    None for anything else."""
    if not isinstance(payload, list) or len(payload) < 4:
        return None
    flows, medias = payload[2], payload[3]
    if not (isinstance(flows, list) and len(flows) == 1 and isinstance(medias, list) and len(medias) == 1):
        return None
    wf, media = flows[0], medias[0]
    if not (isinstance(wf, list) and len(wf) > 4 and isinstance(wf[3], list) and len(wf[3]) > 5):
        return None
    if not (isinstance(media, list) and len(media) > 2 and isinstance(media[0], str) and media[0]):
        return None
    if wf[3][5] != batch or wf[4] != project or media[1] != project or wf[3][4] != media[0] or media[2] != wf[0]:
        return None
    balance = payload[1] if type(payload[1]) is int else None
    return media[0], wf[0], balance


def _text(job) -> str:
    """The core's framed text; a job queued before flow had frames carries the core frame's (the words and the
    tracking code) and goes as it went then: the bare words."""
    return job.prompt if job.frame_id is None else job.sent_text


def generate_args(params, prompt: str, project: str, token: str, batch: str, seed: int) -> list:
    """`ogiZ0b` args (FINDINGS 4). The media and workflow ids we send are ignored by flow; the batch id is echoed."""
    ctx = request_context(project, token)
    entry = [None, None, None, seed, params["aspect"], params["model"], None, ctx, [[[prompt]]], None, None, None,
             str(uuid.uuid4()), str(uuid.uuid4())]
    return [None, [entry], 1, ctx, [batch]]


def video_args(params, prompt: str, project: str, token: str, batch: str) -> list:
    """`YhhmEf` args exactly as recorded (FINDINGS 5, fixture video_submit_try1): the prompt, the model key, the field
    after it (2: 16:9), the media and workflow ids (ours, ignored), the context, and the batch id with 1."""
    entry = [[None, None, [[[prompt]]]], params["model"], params["aspect"], None,
             [None, None, None, None, str(uuid.uuid4()), str(uuid.uuid4())]]
    return [[entry], request_context(project, token), [batch, 1]]


def answer(reply: Reply, batch: str, settings, now: datetime) -> SubmitResult:
    """An image reply that is not the immediate refusal: a drawn image, or unknown. Never `Retry` or `NotTaken`."""
    rest = pacing(settings, now)
    outs = outputs_of(reply.payload, batch) if reply.status == 200 and reply.found else ()
    if outs:
        return _result(Completed(outs), Success(), *rest)
    return _result(Ambiguous("flow answered %d to the generate (reply item %s, code %s)"
                             % (reply.status, "there" if reply.found else "missing", reply.code)), *rest)


def video_answer(reply: Reply, batch: str, batches: list[str], project: str, settings, now: datetime) -> SubmitResult:
    """A video reply that is not the immediate refusal: the recorded acceptance, or unknown. Never `NotTaken`."""
    rest = pacing(settings, now)
    taken = video_taken(reply.payload, batch, project) if reply.status == 200 and reply.clean else None
    if taken is None:
        return _result(Ambiguous("flow answered %d to the video send (reply item %s, code %s)"
                                 % (reply.status, "there" if reply.found else "missing", reply.code)), *rest)
    media, workflow, balance = taken
    state = {BATCH_KEY: batch, BATCHES_KEY: list(batches), PROJECT_KEY: project, MEDIA_KEY: media,
             WORKFLOW_KEY: workflow}
    left = (Balance("video", balance),) if balance is not None else ()
    return _result(Accepted(batch, state, taken=True), Success(), *left, *rest)


async def submit(ctx, job, settings, sessions: Sessions, tokens: Callable[..., Awaitable[str]], *,
                 transport=None, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 seed: Callable[[], int] = lambda: uuid.uuid4().int % 2147483646 + 1) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` and of the first `ctx.mark_sent` (a lost lease, a stopping worker)
    reach the core unchanged. The keyword arguments are seams for tests."""
    start = clock()
    project = (ctx.account.provider_state or {}).get(PROJECT_KEY)
    video = is_video(job)
    sent = False
    try:
        async with Site(ctx, sessions, settings, transport=transport, clock=wall) as site:
            page = await site.load("/project/%s" % project if project else "/")
            if page.status != 200:
                return _result(Retry(errors.HTTP_ERROR))
            if not page.signed_in:
                return _result(Retry(LOGGED_OUT), *doubt_effects(signed_out_why(site), now()))
            if not project:
                # the check makes the account's project; an account that has none yet gets one here (free)
                project = await create_project(site)
                if not project:
                    return _result(Retry(errors.HTTP_ERROR))
            path = "/project/%s" % project
            if video:
                credits = await site.rpc(CREDITS, [], path)
                balance = credits_of(credits.payload) if credits.status == 200 else None
                if balance is None:
                    return _result(Retry(errors.HTTP_ERROR))
                if balance < int(job.params["credits"]):
                    return _result(Retry(OUT_OF_POINTS), Balance("video", balance))
            tries, gap = int(settings.get("flow.captcha_max_tries")), int(settings.get("flow.captcha_retry_gap_s"))
            batches: list[str] = []
            for attempt in range(tries):
                if attempt:
                    if clock() - start + gap + CAPTCHA_TIMEOUT_S + GENERATE_TIMEOUT_S + API_TIMEOUT_S \
                            > SUBMIT_TIMEOUT_S:
                        break                      # the next try would not end inside the submit bound
                    await sleep(gap)
                try:
                    token = await tokens(VIDEO_ACTION if video else IMAGE_ACTION, BASE_URL + path, ctx.account.proxy)
                except CaptchaError as e:
                    if not sent:
                        await ctx.event("no reCAPTCHA token: %s" % e, "submit", "engine")
                        return _result(Retry(errors.CAPTCHA_FAILED))
                    break
                batch = str(uuid.uuid4())
                batches.append(batch)
                await ctx.report_ref(batch, {BATCH_KEY: batch, BATCHES_KEY: list(batches), PROJECT_KEY: project})
                if not sent:
                    await ctx.mark_sent()          # then marked sent, immediately before the POST
                    sent = True
                else:
                    try:
                        await ctx.mark_sent()      # fenced again before this try's POST
                    except Exception:              # every earlier try was refused before anything was made
                        return _result(NotTaken(errors.CAPTCHA_REFUSED, {"refused_batches": batches[:-1]}),
                                       *pacing(settings, now()))
                if video:
                    rpcid, args = GENERATE_VIDEO, video_args(job.params, _text(job), project, token, batch)
                else:
                    rpcid, args = GENERATE, generate_args(job.params, _text(job), project, token, batch, seed())
                try:
                    reply = await site.rpc(rpcid, args, path, GENERATE_TIMEOUT_S)
                except httpx.HTTPError as e:
                    return _result(Ambiguous("the generate got no answer (%s)" % type(e).__name__),
                                   *pacing(settings, now()))
                if not reply.refused_before_anything:
                    if video:
                        return video_answer(reply, batch, batches, project, settings, now())
                    return answer(reply, batch, settings, now())
                await ctx.event("flow refused try %d at its reCAPTCHA check (code %s); nothing was made"
                                % (attempt + 1, reply.code), "submit", "upstream")
            return _result(NotTaken(errors.CAPTCHA_REFUSED, {"refused_batches": batches}), *pacing(settings, now()))
    except httpx.HTTPError as e:
        why = "flow could not be reached (%s)" % type(e).__name__     # the text may carry the proxy URL
        if sent:       # never reached: the generate's own errors are caught above. Kept as the money guard.
            return _result(Ambiguous(why))
        return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
