"""After the send: find the job's result in its project (reconcile, poll), and download it (fetch). Reads only:
nothing here sends a generate, so nothing here can spend.

- Reconcile: `Zzl0ze` lists the project's workflows with each one's batch id and primary media id (FINDINGS 6). A
  workflow with one of the job's batch ids is `Found`. An image is not listed while its reply is held, nor right after
  it, but about a minute later, so "not listed" is `StillUnknown` (answered), never "not sent": the render deadline
  ends such a job with its refund, and it is never sent again.
- Poll, an image (a job `Found` by the reconciler): the media record (`as29s`) gives a freshly signed link: `Done`.
- Poll, a video (accepted, or `Found`): the status read (`jwpduf`, the recorded args: the job's workflow, media and
  batch ids) gives the media record's status, `[6]` queued, `[2]` rendering, `[3]` done (fixtures video_status_*);
  at 3 the media record (`as29s`) carries the signed mp4 link: `Done`. Any other status was never captured: the job
  waits (an event names the status), and the render deadline ends it with its refund.
- Fetch: the signed `flow-content.google` link, through the account's proxy (never direct when it has one), no cookie.
"""

import contextlib
import random
from collections.abc import AsyncIterator, Callable

import httpx

from relay_core.net import stream_download, system_resolver
from relay_core.providers import Done, Found, Output, Pending, PollResult, Reconciled, StillUnknown, Success
from relay_flow.constants import MEDIA_HOSTS, OUTPUT_MIME, VIDEO_MIME
from relay_flow.engine.check import PROJECT_KEY
from relay_flow.engine.http import Sessions, Site, proxy_url
from relay_flow.engine.submit import BATCH_KEY, BATCHES_KEY, MEDIA_KEY, WORKFLOW_KEY, is_video, media_link

LIST_PROJECT = "Zzl0ze"
GET_MEDIA = "as29s"
VIDEO_STATUS = "jwpduf"
VIDEO_PREFIX = "https://%s/video/" % MEDIA_HOSTS[0]
QUEUED, RENDERING, DONE = 6, 2, 3     # a video media record's status (fixtures video_submit_try1, video_status_*)
ODD_STATUS_KEY = "odd_status"         # provider_state: a video status never recorded, already named in an event
MEDIA_MAX_BYTES = 64 * 2**20     # a 1376x768 JPEG is about 80 kB, an 8 s 720p mp4 about 8.5 MB
MEDIA_DEADLINE_S = 120


def batches_of(job) -> list[str]:
    state = job.provider_state or {}
    found = [b for b in state.get(BATCHES_KEY) or [] if isinstance(b, str) and b]
    return found or [b for b in (state.get(BATCH_KEY), job.external_ref) if isinstance(b, str) and b][:1]


def listed(payload, batches: list[str]) -> tuple[str, str, str | None] | None:
    """(batch id, primary media id, workflow id) of the first workflow of the listing whose batch id is one of
    `batches`. A workflow is `[id, _, _, [title, created, _, _, media id, batch id, updated], project]`."""
    workflows = payload[1] if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], list) else []
    for wf in workflows:
        record = wf[3] if isinstance(wf, list) and len(wf) > 3 and isinstance(wf[3], list) else []
        if len(record) > 5 and record[5] in batches and isinstance(record[4], str) and record[4]:
            return record[5], record[4], wf[0] if isinstance(wf[0], str) and wf[0] else None
    return None


async def _find(site: Site, project: str, batches: list[str]) -> tuple[str, str, str | None] | None | bool:
    """The listed (batch, media, workflow), None when not listed, False when the listing could not be read."""
    reply = await site.rpc(LIST_PROJECT, ["projects/%s" % project, None, None, None, [1]], "/project/%s" % project)
    if reply.status != 200 or not isinstance(reply.payload, list):
        return False
    return listed(reply.payload, batches)


def video_status(payload, media: str, project: str) -> int | None:
    """The status of OUR media record in a `jwpduf` answer `[_, _, [record...]]`: the record whose id is `media` and
    whose project is ours, its status `record[5][8][0]`. None when there is no such record (an id flow does not
    know answers `[id, null, ..., [.., [4, [null, "Media not found."]]]]`, with no project: never ours)."""
    records = payload[2] if isinstance(payload, list) and len(payload) > 2 and isinstance(payload[2], list) else []
    for rec in records:
        if not (isinstance(rec, list) and len(rec) > 5 and rec[0] == media and rec[1] == project):
            continue
        body = rec[5]
        status = body[8] if isinstance(body, list) and len(body) > 8 else None
        if isinstance(status, list) and len(status) == 1 and type(status[0]) is int:
            return status[0]
    return None


async def reconcile(ctx, job, settings, sessions: Sessions, *, transport=None) -> Reconciled:
    """Did a generate of an unknown job reach flow? Listed is `Found`; not listed is `StillUnknown(answered=True)`."""
    project, batches = (job.provider_state or {}).get(PROJECT_KEY), batches_of(job)
    if not project or not batches:
        return StillUnknown()
    try:
        async with Site(ctx, sessions, settings, transport=transport) as site:
            if not (await site.load("/project/%s" % project)).signed_in:
                return StillUnknown()
            hit = await _find(site, project, batches)
    except httpx.HTTPError:
        return StillUnknown()
    if hit is False:
        return StillUnknown()
    if hit is None:
        return StillUnknown(answered=True)
    batch, media, workflow = hit
    return Found(batch, {BATCH_KEY: batch, MEDIA_KEY: media, **({WORKFLOW_KEY: workflow} if workflow else {})}, True)


def interval_s(settings, rand: Callable[[], float] = random.random) -> int:
    base = int(settings.get("flow.poll_interval_s"))
    return base + int(rand() * base / 2)


async def poll(ctx, job, settings, sessions: Sessions, *, transport=None,
               rand: Callable[[], float] = random.random) -> PollResult:
    """The job's result: its media id (from the submit, the reconciler, or the listing); for a video its status;
    then a freshly signed link."""
    state = job.provider_state or {}
    project, media, wait = state.get(PROJECT_KEY), state.get(MEDIA_KEY), interval_s(settings, rand)
    batch, workflow, video = state.get(BATCH_KEY), state.get(WORKFLOW_KEY), is_video(job)
    if not project:
        return PollResult(Pending("no project to read", wait, answered=False))
    path = "/project/%s" % project
    try:
        async with Site(ctx, sessions, settings, transport=transport) as site:
            if not (await site.load(path)).signed_in:
                return PollResult(Pending("flow's page is signed out", wait, answered=False))
            if not media:
                hit = await _find(site, project, batches_of(job))
                if not hit:
                    return PollResult(Pending("not listed yet" if hit is None else "the listing could not be read",
                                              wait, answered=hit is None))
                batch, media, workflow = hit[0], hit[1], workflow or hit[2]
            if video:
                ids = [[i] for i in (workflow, media, batch) if isinstance(i, str) and i]
                read = await site.rpc(VIDEO_STATUS, [None, None, ids], path)
                if read.status != 200 or not isinstance(read.payload, list):
                    return PollResult(Pending("the video status could not be read", wait, {MEDIA_KEY: media},
                                              answered=False))
                status = video_status(read.payload, media, project)
                if status in (QUEUED, RENDERING):
                    return PollResult(Pending("queued" if status == QUEUED else "rendering", wait, {MEDIA_KEY: media}))
                if status != DONE:
                    # never captured (a failure, a refusal): waited on, named once, ended by the deadline (refund)
                    if ODD_STATUS_KEY not in state or state[ODD_STATUS_KEY] != status:
                        await ctx.event("flow's video status is %s, not one recorded (6, 2, 3); waiting" % status,
                                        "poll", "upstream")
                    return PollResult(Pending("status %s not recorded" % status, wait,
                                              {MEDIA_KEY: media, ODD_STATUS_KEY: status}))
            reply = await site.rpc(GET_MEDIA, [media], path)
    except httpx.HTTPError as e:
        return PollResult(Pending("flow could not be read (%s)" % type(e).__name__, wait, answered=False))
    link = None if reply.status != 200 else media_link(reply.payload, VIDEO_PREFIX) if video else media_link(
        reply.payload)
    if link is None:
        return PollResult(Pending("flow gave no link for the %s yet" % ("video" if video else "image"), wait,
                                  {MEDIA_KEY: media}))
    return PollResult(Done((Output(link, VIDEO_MIME if video else OUTPUT_MIME, 0, media, {"batch": batch or ""}),)),
                      effects=(Success(),))


async def fetch(ctx, out: Output, *, media_transport=None, media_resolver=system_resolver) -> AsyncIterator[bytes]:
    """The bytes of `out`, streamed from its signed link. The test seams are those of `stream_download`."""
    # ponytail: the link from the reply is used as is (it was fetched minutes after the reply, 23/10); if its signature
    # ever expires before the fetch, re-sign it with `as29s` by `out.upstream_id`.
    async with contextlib.aclosing(stream_download(
            out.ref, hosts=MEDIA_HOSTS, proxy=proxy_url(ctx.account.proxy), max_bytes=MEDIA_MAX_BYTES,
            deadline_s=MEDIA_DEADLINE_S, resolver=media_resolver, transport=media_transport)) as parts:
        async for part in parts:
            yield part
