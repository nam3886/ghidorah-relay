"""Submit: one job to flow, and what flow said as a `SubmitResult`. An image is `ogiZ0b` (BatchGenerateImages, HOLD);
a video is the VideoFx send of its operation (`VIDEO_RPCS`: `YhhmEf` from text, `eb1hJf` from a start frame, `nprQif`
from a first and a last frame, `MZZa6b` from references; DETACH: answered at once, then polled).

The steps: load the project page (session rotated first when due); for a video, the account's Flow credits (`nzlxg`,
free) must cover it and its plan (the tier the same read names) must have the option (`params.tiers`: Veo's 4 s and
6 s are Ultra's only, `flow.tier_required`); each input image is uploaded into the project (`maseQ`, free, no token;
one flow does not take is `flow.upload_failed`) and named by its media id; then, per try: a reCAPTCHA token for the
job's page action (IMAGE_GENERATION, VIDEO_GENERATION);
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
  token and a new batch id cannot make a second image. When the tries run out (or the next would not end inside the
  submit bound, or a token cannot be had after a refusal) the job goes back to the queue: `NotTaken`, nothing was made.
  A video gets ONE try. Its code 8 refusal (exactly that shape) is proven to make nothing (FINDINGS 30/10: four live
  sends, none ever listed) and is an account refusal like an image's; a 7 envelope on a video is still no such proof
  (Flow could take the video and still answer it): `Ambiguous`, reconciled by its batch, never re-sent (FINDINGS 24/10).
- Any other reply that is not a drawn image, or for a video not the recorded acceptance (our batch echoed, a media
  record of our project), is `Ambiguous`: the job becomes unknown and the reconciler looks for its batch ids; it is
  never sent again. A refusal or an out-of-credits answer to a video send was never captured, so it is ambiguous too:
  never listed, the render deadline ends it with its refund.
- After every generate (taken, refused or unclear) the account's pools rest `flow.account_min_gap_s`: the pacing
  between two generates of one account. A code 8 (RESOURCE_EXHAUSTED, `PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC`)
  makes it `flow.traffic_rest_s` instead.
- Account refusals (`relay_core.refusals.account_refused`): the exact refusal shape with code 8 (an image or a video),
  or an image's `PUBLIC_ERROR_MODEL_ACCESS_DENIED` (the account's plan may not use the model; the image pool also rests
  `flow.model_denied_rest_s`), refuses THIS account and made nothing: no more tries here, the job goes to another
  account (`flow.too_much_traffic` / `flow.model_access_denied`, no attempt used) until `flow.refusal_max_accounts`
  accounts refused it, and the last one fails it with the same code, refunded.

Before the first `mark_sent` every failure is an ordinary retry: a signed-out page `pool.account_logged_out` with a
spaced strike, too few Flow credits for a video `pool.account_out_of_points`, a proxy that cannot reach flow
`pool.proxy_failed`, an error answer `flow.http_error`, no token `flow.captcha_failed` (or the solver's own fault:
`flow.captcha_key_invalid`, `flow.captcha_no_balance`, `flow.captcha_timeout`; the account is never blamed).
"""

import asyncio
import base64
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import (
    REFUSED_BY,
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Facts,
    NotTaken,
    Output,
    ProxyBad,
    Rest,
    Retry,
    SubmitResult,
    Success,
    Tier,
)
from relay_core.refusals import account_refused, max_accounts_key
from relay_flow import errors
from relay_flow.constants import (
    API_TIMEOUT_S,
    BASE_URL,
    GENERATE_TIMEOUT_S,
    IMAGE_ACTION,
    MEDIA_HOSTS,
    NETWORK_REST_S,
    OUTPUT_MIME,
    SUBMIT_TIMEOUT_S,
    VIDEO_ACTION,
)
from relay_flow.engine.captcha import CaptchaError
from relay_flow.engine.check import (
    CREDITS,
    PROJECT_KEY,
    account_read,
    create_project,
    credits_of,
    doubt_effects,
    signed_out_why,
)
from relay_flow.engine.http import CLIENT_TOOL, Reply, Sessions, Site, request_context

GENERATE = "ogiZ0b"
GENERATE_VIDEO = "YhhmEf"
UPLOAD = "maseQ"             # FlowService.UploadImage: free, and taken without a reCAPTCHA token (25/10)
# the VideoFxService RPC of each video operation (flow's web app; each proven by one live job, 25/10)
VIDEO_RPCS = {"text_to_video": GENERATE_VIDEO, "image_to_video": "eb1hJf", "frames_to_video": "nprQif",
              "references_to_video": "MZZa6b"}
BATCH_KEY = "batch"          # provider_state: the batch id of the last try (also the job's external_ref)
BATCHES_KEY = "batches"      # provider_state: every batch id this job sent; the reconciler looks for any of them
MEDIA_KEY = "media"          # provider_state: flow's media id of the job's result, once known
WORKFLOW_KEY = "workflow"    # provider_state: flow's workflow id of a video, once known
LOGGED_OUT = "pool.account_logged_out"
OUT_OF_POINTS = "pool.account_out_of_points"
PROXY_FAILED = "pool.proxy_failed"
POOLS = ("image", "video", "video_pro", "video_ultra")
TOO_MUCH_TRAFFIC = 8         # RESOURCE_EXHAUSTED, PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC (FINDINGS 3)
MODEL_DENIED = "PUBLIC_ERROR_MODEL_ACCESS_DENIED"     # flow2api 16/10: the account may not use the model
MEDIA_PREFIX = "https://%s/image/" % MEDIA_HOSTS[0]


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


def is_video(job) -> bool:
    return job.operation in VIDEO_RPCS


def pacing(settings, now: datetime, code: int | None = None) -> tuple[Rest, ...]:
    """After a generate the account's pools rest `flow.account_min_gap_s` (0: no rest); after flow's code 8 (too much
    traffic) at least `flow.traffic_rest_s`."""
    gap, why = int(settings.get("flow.account_min_gap_s")), "flow pacing between two generates"
    if code == TOO_MUCH_TRAFFIC and int(settings.get("flow.traffic_rest_s")) > gap:
        gap, why = int(settings.get("flow.traffic_rest_s")), "flow said too much traffic (code 8)"
    return tuple(Rest(pool, now + timedelta(seconds=gap), why) for pool in POOLS) if gap > 0 else ()


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


def generate_args(params, prompt: str, project: str, token: str, batch: str, seed: int,
                  refs: tuple[str, ...] = ()) -> list:
    """`ogiZ0b` args (FINDINGS 4). The media and workflow ids we send are ignored by flow; the batch id is echoed. Each
    input image is a reference `[media id, null, null, null, 1]` in the entry's third slot (1: a reference, the web
    app's own; live 25/10)."""
    ctx = request_context(project, token)
    entry = [None, None, [[m, None, None, None, 1] for m in refs] or None, seed, params["aspect"], params["model"],
             None, ctx, [[[prompt]]], None, None, None, str(uuid.uuid4()), str(uuid.uuid4())]
    return [None, [entry], 1, ctx, [batch]]


def video_args(params, prompt: str, project: str, token: str, batch: str, operation: str = "text_to_video",
               media: tuple[str, ...] = ()) -> list:
    """The args of the operation's RPC (`VIDEO_RPCS`). Text (`YhhmEf`) exactly as recorded (FINDINGS 5, fixture
    video_submit_try1): the prompt, the model key, the field after it (2: 16:9), the media and workflow ids (ours,
    ignored), the context, and the batch id with 1. The other operations are the same request with the images placed
    where flow's web app puts them (live 25/10): a frame is `[null, <media id>]`; the start frame at field 5 and the
    request's own ids at 6 (`eb1hJf`), the first and the last frame at 5 and 6 and the ids at 7 (`nprQif`), the
    references a list at field 2 with the model at 3 (`MZZa6b`). Omni's 360p sends its resolution `[4]` where the web
    app does (text 8, start frame 10, references 12); the first-and-last request has no such field."""
    text, ids = [None, None, [[[prompt]]]], [None, None, None, None, str(uuid.uuid4()), str(uuid.uuid4())]
    res = [params["resolution"]] if params.get("resolution") else None
    frames = [[None, m] for m in media]
    if operation == "image_to_video":
        entry = [text, params["model"], params["aspect"], None, frames[0], ids, None, None, None, res]
    elif operation == "frames_to_video":
        entry = [text, params["model"], params["aspect"], None, frames[0], frames[1], ids]
    elif operation == "references_to_video":
        entry = [text, frames, params["model"], params["aspect"], None, ids, None, None, None, None, None, res]
    else:
        entry = [text, params["model"], params["aspect"], None, ids, None, None, res]
    while entry[-1] is None:
        entry.pop()
    return [[entry], request_context(project, token), [batch, 1]]


def upload_args(project: str, data: bytes, mime: str) -> list:
    """`maseQ` args (flow's web app, live 25/10): the context with the project and no token, the image as base64, its
    type, then the web app's own flags (shown, not hidden)."""
    return [[None, CLIENT_TOOL, None, None, None, project], base64.b64encode(data).decode(), mime, True, None, None,
            None, False]


def uploaded(payload, project: str) -> str | None:
    """The media id of an upload's answer `[[<media id>, <project>, <workflow id>, ...], <workflow record>]`; None for
    anything else."""
    record = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else []
    if len(record) > 1 and isinstance(record[0], str) and record[0] and record[1] == project:
        return record[0]
    return None


def answer(reply: Reply, batch: str, settings, now: datetime) -> SubmitResult:
    """An image reply that is not the immediate refusal: a drawn image, or unknown. Never `Retry` or `NotTaken`."""
    rest = pacing(settings, now, reply.code)
    outs = outputs_of(reply.payload, batch) if reply.status == 200 and reply.found else ()
    if outs:
        return _result(Completed(outs), Success(), *rest)
    return _result(Ambiguous("flow answered %d to the generate (reply item %s, code %s)"
                             % (reply.status, "there" if reply.found else "missing", reply.code)), *rest)


def video_answer(reply: Reply, batch: str, batches: list[str], project: str, settings, now: datetime,
                 before: int) -> SubmitResult:
    """A video reply that is not the immediate refusal: the recorded acceptance, or unknown. Never `NotTaken`.
    flow states no price: the cost is `before` (the credits read right before this send) minus the balance the
    acceptance reports, which is the job's own only while an account runs one job at a time (`account_slots` 1)."""
    rest = pacing(settings, now, reply.code)
    taken = video_taken(reply.payload, batch, project) if reply.status == 200 and reply.clean else None
    if taken is None:
        return _result(Ambiguous("flow answered %d to the video send (reply item %s, code %s%s)"
                                 % (reply.status, "there" if reply.found else "missing", reply.code,
                                    ", " + reply.reason if reply.reason else "")), *rest)
    media, workflow, balance = taken
    state = {BATCH_KEY: batch, BATCHES_KEY: list(batches), PROJECT_KEY: project, MEDIA_KEY: media,
             WORKFLOW_KEY: workflow}
    left = (Balance("video", balance),) if balance is not None else ()
    spent = balance is not None and before > balance
    facts = Facts(before - balance, cost_source="balance_delta") if spent else Facts()
    return _result(Accepted(batch, state, facts, taken=True), Success(), *left, *rest)


def refused_here(ctx, job, settings, reply, batches: list[str], read: tuple, now: datetime) -> SubmitResult:
    """An account refusal (see the module docstring): the job goes to another account or, on the last one
    `flow.refusal_max_accounts` allows, fails refunded; this account rests (its pacing or traffic rest, and after a
    model refusal the image pool `flow.model_denied_rest_s`)."""
    rest = pacing(settings, now, reply.code)
    if reply.code == TOO_MUCH_TRAFFIC:
        code, detail = errors.TOO_MUCH_TRAFFIC, "flow said too much traffic (code 8)"
    else:
        code, detail = errors.MODEL_ACCESS_DENIED, "flow refused the model for the account (%s)" % MODEL_DENIED
        denied_s = int(settings.get("flow.model_denied_rest_s"))
        if denied_s > 0:
            rest = (*rest, Rest("image", now + timedelta(seconds=denied_s), detail))
    outcome = account_refused(job, ctx.account.id, settings, not_taken=code, final=code, detail=detail,
                              patch={"refused_batches": list(batches)})
    return _result(outcome, *read, *rest)


async def submit(ctx, job, settings, sessions: Sessions, tokens: Callable[..., Awaitable[str]], *,
                 transport=None, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 seed: Callable[[], int] = lambda: uuid.uuid4().int % 2147483646 + 1,
                 read_media: Callable[..., Awaitable[bytes]] = read_input_media) -> SubmitResult:
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
            # the pre-send read (balance, refill, plan) rides on every result that is known to have spent nothing;
            # an accepted video carries the newer balance of its own reply, an unknown one none (it may have spent)
            read: tuple = ()
            if video:
                credits = await site.rpc(CREDITS, [], path)
                balance = credits_of(credits.payload) if credits.status == 200 else None
                if balance is None:
                    return _result(Retry(errors.HTTP_ERROR))
                read = account_read(credits.payload, now())
                tier = next((e.tier for e in read if isinstance(e, Tier)), None)
                if job.params.get("tiers") and tier not in job.params["tiers"]:
                    await ctx.event("the account's plan (%s) has no %s at %s s: the job goes to another account"
                                    % (tier, job.model, job.params.get("duration")), "submit", "engine")
                    return _result(Retry(errors.TIER_REQUIRED), *read)
                if balance < int(job.params["credits"]):
                    return _result(Retry(OUT_OF_POINTS), *read)
            # the input images, uploaded into the project (free, nothing generated): one not taken is a retry
            media: list[str] = []
            if job.operation not in ("text_to_image", "text_to_video"):
                for ref in await job.inputs():
                    up = await site.rpc(UPLOAD, upload_args(project, await read_media(ref), ref.mime), path,
                                        GENERATE_TIMEOUT_S)
                    mid = uploaded(up.payload, project) if up.status == 200 else None
                    if mid is None:
                        await ctx.event("flow did not take input image %d (answer %d, code %s)"
                                        % (ref.idx, up.status, up.code), "submit", "upstream")
                        return _result(Retry(errors.UPLOAD_FAILED), *read)
                    media.append(mid)
            tries, gap = int(settings.get("flow.captcha_max_tries")), int(settings.get("flow.captcha_retry_gap_s"))
            captcha_s = int(settings.get("flow.captcha_timeout_s"))
            batches: list[str] = []
            for attempt in range(tries):
                if attempt:
                    if clock() - start + gap + captcha_s + GENERATE_TIMEOUT_S + API_TIMEOUT_S > SUBMIT_TIMEOUT_S:
                        break                      # the next try would not end inside the submit bound
                    await sleep(gap)
                try:
                    token = await tokens(VIDEO_ACTION if video else IMAGE_ACTION, BASE_URL + path, ctx.account.proxy)
                except CaptchaError as e:
                    if not sent:
                        await ctx.event("no reCAPTCHA token: %s" % e, "submit", "engine")
                        return _result(Retry(e.code), *read)
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
                                       *read, *pacing(settings, now()))
                if video:
                    rpcid = VIDEO_RPCS[job.operation]
                    args = video_args(job.params, _text(job), project, token, batch, job.operation, tuple(media))
                else:
                    rpcid = GENERATE
                    args = generate_args(job.params, _text(job), project, token, batch, seed(), tuple(media))
                try:
                    reply = await site.rpc(rpcid, args, path, GENERATE_TIMEOUT_S,
                                           user_agent=getattr(token, "user_agent", None))
                except httpx.HTTPError as e:
                    return _result(Ambiguous("the generate got no answer (%s)" % type(e).__name__),
                                   *pacing(settings, now()))
                code8 = reply.refused_before_anything and reply.code == TOO_MUCH_TRAFFIC
                if video and not code8:     # only a video's code 8 is proven to make nothing; never re-sent
                    return video_answer(reply, batch, batches, project, settings, now(), balance)
                if not reply.refused_before_anything:
                    return answer(reply, batch, settings, now())
                await ctx.event("flow refused try %d at its reCAPTCHA check (code %s, %s); nothing was made"
                                % (attempt + 1, reply.code, reply.reason or "no reason"), "submit", "upstream")
                if code8 or reply.reason == MODEL_DENIED:      # no more tries here: the account refused the job
                    result = refused_here(ctx, job, settings, reply, batches, read, now())
                    if isinstance(result.job, NotTaken):
                        await ctx.event("flow refused the job on %d account(s); it goes to another account (%s)"
                                        % (len(result.job.state_patch[REFUSED_BY]), max_accounts_key("flow")),
                                        "submit", "engine")
                    return result
            return _result(NotTaken(errors.CAPTCHA_REFUSED, {"refused_batches": batches}), *read,
                           *pacing(settings, now()))
    except httpx.HTTPError as e:
        why = "flow could not be reached (%s)" % type(e).__name__     # the text may carry the proxy URL
        if sent:       # never reached: the generate's own errors are caught above. Kept as the money guard.
            return _result(Ambiguous(why))
        return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
