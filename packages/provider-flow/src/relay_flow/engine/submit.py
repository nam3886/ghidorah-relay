"""Submit: one text-to-image job to flow (`ogiZ0b`, BatchGenerateImages), and what flow said as a `SubmitResult`.

The steps: load the project page (session rotated first when due); then, per try: a reCAPTCHA token for
IMAGE_GENERATION; a new batch id written to the job (`ctx.report_ref`, with every batch id tried and the project);
`ctx.mark_sent()`; the ONE generate POST of that try. flow holds the reply until the image is drawn (13-17 s), so a
good reply is `Completed` with the signed `flow-content.google` link of the JPEG (HOLD).

Money rules that shape this file (each has a test and a mutation):
- The batch id and the project are on the job BEFORE its generate is sent: a reply that is lost is looked up by the
  batch id in the project's listing (FINDINGS 6).
- `ctx.mark_sent()` runs immediately before each generate POST, never after.
- One generate POST per try. A try is followed by another only when flow's reply was the immediate reCAPTCHA refusal:
  HTTP 200, the reply item there with NO payload and an envelope code 7 or 8 (`Reply.refused_before_anything`).
  Measured: such a refusal comes in 0.4 s, nothing is listed, nothing is charged, so a new try with a new token and a
  new batch id cannot make a second image. When the tries run out (or the next would not end inside the submit bound,
  or a token cannot be had after a refusal) the job goes back to the queue: `NotTaken`, nothing was made.
- Any other reply that is not a drawn image (no answer, a timeout, a 5xx, another error code, a payload without a
  link) is `Ambiguous`: the job becomes unknown and the reconciler looks for its batch ids; it is never sent again.
- After every generate (taken, refused or unclear) the account's pools rest `flow.account_min_gap_s`: the pacing
  between two generates of one account.

Before the first `mark_sent` every failure is an ordinary retry: a signed-out page `pool.account_logged_out` with a
spaced strike, a proxy that cannot reach flow `pool.proxy_failed`, an error answer `flow.http_error`, no token
`flow.captcha_failed`.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import (
    Ambiguous,
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
    SUBMIT_TIMEOUT_IMAGE_S,
)
from relay_flow.engine.captcha import CaptchaError
from relay_flow.engine.check import PROJECT_KEY, create_project, doubt_effects, signed_out_why
from relay_flow.engine.http import Reply, Sessions, Site, request_context

GENERATE = "ogiZ0b"
BATCH_KEY = "batch"          # provider_state: the batch id of the last try (also the job's external_ref)
BATCHES_KEY = "batches"      # provider_state: every batch id this job sent; the reconciler looks for any of them
LOGGED_OUT = "pool.account_logged_out"
PROXY_FAILED = "pool.proxy_failed"
POOLS = ("image",)
MEDIA_PREFIX = "https://%s/image/" % MEDIA_HOSTS[0]


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


def pacing(settings, now: datetime) -> tuple[Rest, ...]:
    """After a generate the account's pools rest `flow.account_min_gap_s` (0: no rest)."""
    gap = int(settings.get("flow.account_min_gap_s"))
    return tuple(Rest(pool, now + timedelta(seconds=gap), "flow pacing between two generates")
                 for pool in POOLS) if gap > 0 else ()


def media_link(record) -> str | None:
    """The first signed `flow-content.google/image/` link anywhere in a media record."""
    if isinstance(record, str):
        return record if record.startswith(MEDIA_PREFIX) else None
    if isinstance(record, list):
        for part in record:
            link = media_link(part)
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


def generate_args(params, prompt: str, project: str, token: str, batch: str, seed: int) -> list:
    """`ogiZ0b` args (FINDINGS 4). The media and workflow ids we send are ignored by flow; the batch id is echoed."""
    ctx = request_context(project, token)
    entry = [None, None, None, seed, params["aspect"], params["model"], None, ctx, [[[prompt]]], None, None, None,
             str(uuid.uuid4()), str(uuid.uuid4())]
    return [None, [entry], 1, ctx, [batch]]


def answer(reply: Reply, batch: str, settings, now: datetime) -> SubmitResult:
    """A generate reply that is not the immediate refusal: a drawn image, or unknown. Never `Retry` or `NotTaken`."""
    rest = pacing(settings, now)
    outs = outputs_of(reply.payload, batch) if reply.status == 200 and reply.found else ()
    if outs:
        return _result(Completed(outs), Success(), *rest)
    return _result(Ambiguous("flow answered %d to the generate (reply item %s, code %s)"
                             % (reply.status, "there" if reply.found else "missing", reply.code)), *rest)


async def submit(ctx, job, settings, sessions: Sessions, tokens: Callable[..., Awaitable[str]], *,
                 transport=None, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 seed: Callable[[], int] = lambda: uuid.uuid4().int % 2147483646 + 1) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` and of the first `ctx.mark_sent` (a lost lease, a stopping worker)
    reach the core unchanged. The keyword arguments are seams for tests."""
    start = clock()
    project = (ctx.account.provider_state or {}).get(PROJECT_KEY)
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
            tries, gap = int(settings.get("flow.captcha_max_tries")), int(settings.get("flow.captcha_retry_gap_s"))
            batches: list[str] = []
            for attempt in range(tries):
                if attempt:
                    if clock() - start + gap + CAPTCHA_TIMEOUT_S + GENERATE_TIMEOUT_S + API_TIMEOUT_S \
                            > SUBMIT_TIMEOUT_IMAGE_S:
                        break                      # the next try would not end inside the submit bound
                    await sleep(gap)
                try:
                    token = await tokens(IMAGE_ACTION, BASE_URL + path, ctx.account.proxy)
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
                try:
                    reply = await site.rpc(GENERATE, generate_args(job.params, job.prompt, project, token, batch,
                                                                   seed()), path, GENERATE_TIMEOUT_S)
                except httpx.HTTPError as e:
                    return _result(Ambiguous("the generate got no answer (%s)" % type(e).__name__),
                                   *pacing(settings, now()))
                if not reply.refused_before_anything:
                    return answer(reply, batch, settings, now())
                await ctx.event("flow refused try %d at its reCAPTCHA check (code %s); nothing was made"
                                % (attempt + 1, reply.code), "submit", "upstream")
            return _result(NotTaken(errors.CAPTCHA_REFUSED, {"refused_batches": batches}), *pacing(settings, now()))
    except httpx.HTTPError as e:
        why = "flow could not be reached (%s)" % type(e).__name__     # the text may carry the proxy URL
        if sent:       # never reached: the generate's own errors are caught above. Kept as the money guard.
            return _result(Ambiguous(why))
        return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
