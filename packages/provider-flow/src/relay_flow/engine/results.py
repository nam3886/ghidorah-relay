"""After a lost reply: find the job's image in its project (reconcile, poll), and download a result (fetch). Reads
only: nothing here sends a generate, so nothing here can spend.

- Reconcile: `Zzl0ze` lists the project's workflows with each one's batch id and primary media id (FINDINGS 6). A
  workflow with one of the job's batch ids is `Found`. An image is not listed while its reply is held, nor right after
  it, but about a minute later, so "not listed" is `StillUnknown` (answered), never "not sent": the render deadline
  ends such a job with its refund, and it is never sent again.
- Poll (a job `Found` by the reconciler): the media record (`as29s`) gives a freshly signed link: `Done`.
- Fetch: the signed `flow-content.google` link, through the account's proxy (never direct when it has one), no cookie.
"""

import contextlib
import random
from collections.abc import AsyncIterator, Callable

import httpx

from relay_core.net import stream_download, system_resolver
from relay_core.providers import Done, Found, Output, Pending, PollResult, Reconciled, StillUnknown, Success
from relay_flow.constants import MEDIA_HOSTS, OUTPUT_MIME
from relay_flow.engine.check import PROJECT_KEY
from relay_flow.engine.http import Sessions, Site, proxy_url
from relay_flow.engine.submit import BATCH_KEY, BATCHES_KEY, media_link

LIST_PROJECT = "Zzl0ze"
GET_MEDIA = "as29s"
MEDIA_KEY = "media"          # provider_state: flow's media id of the job's image, once found
MEDIA_MAX_BYTES = 64 * 2**20     # a 1376x768 JPEG is about 80 kB
MEDIA_DEADLINE_S = 120


def batches_of(job) -> list[str]:
    state = job.provider_state or {}
    found = [b for b in state.get(BATCHES_KEY) or [] if isinstance(b, str) and b]
    return found or [b for b in (state.get(BATCH_KEY), job.external_ref) if isinstance(b, str) and b][:1]


def listed(payload, batches: list[str]) -> tuple[str, str] | None:
    """(batch id, primary media id) of the first workflow of the listing whose batch id is one of `batches`.
    A workflow is `[id, _, _, [title, created, _, _, media id, batch id, updated], project]`."""
    workflows = payload[1] if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], list) else []
    for wf in workflows:
        record = wf[3] if isinstance(wf, list) and len(wf) > 3 and isinstance(wf[3], list) else []
        if len(record) > 5 and record[5] in batches and isinstance(record[4], str) and record[4]:
            return record[5], record[4]
    return None


async def _find(site: Site, project: str, batches: list[str]) -> tuple[str, str] | None | bool:
    """The listed (batch, media), None when not listed, False when the listing could not be read."""
    reply = await site.rpc(LIST_PROJECT, ["projects/%s" % project, None, None, None, [1]], "/project/%s" % project)
    if reply.status != 200 or not isinstance(reply.payload, list):
        return False
    return listed(reply.payload, batches)


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
    batch, media = hit
    return Found(batch, {BATCH_KEY: batch, MEDIA_KEY: media}, True)


def interval_s(settings, rand: Callable[[], float] = random.random) -> int:
    base = int(settings.get("flow.poll_interval_s"))
    return base + int(rand() * base / 2)


async def poll(ctx, job, settings, sessions: Sessions, *, transport=None,
               rand: Callable[[], float] = random.random) -> PollResult:
    """The job's image: its media id (from the reconciler, or the listing), then a freshly signed link."""
    state = job.provider_state or {}
    project, media, wait = state.get(PROJECT_KEY), state.get(MEDIA_KEY), interval_s(settings, rand)
    if not project:
        return PollResult(Pending("no project to read", wait, answered=False))
    try:
        async with Site(ctx, sessions, settings, transport=transport) as site:
            if not (await site.load("/project/%s" % project)).signed_in:
                return PollResult(Pending("flow's page is signed out", wait, answered=False))
            if not media:
                hit = await _find(site, project, batches_of(job))
                if not hit:
                    return PollResult(Pending("not listed yet" if hit is None else "the listing could not be read",
                                              wait, answered=hit is None))
                media = hit[1]
            reply = await site.rpc(GET_MEDIA, [media], "/project/%s" % project)
    except httpx.HTTPError as e:
        return PollResult(Pending("flow could not be read (%s)" % type(e).__name__, wait, answered=False))
    link = media_link(reply.payload) if reply.status == 200 else None
    if link is None:
        return PollResult(Pending("flow gave no link for the image yet", wait, {MEDIA_KEY: media}))
    batch = state.get(BATCH_KEY) or ""
    return PollResult(Done((Output(link, OUTPUT_MIME, 0, media, {"batch": batch}),)), effects=(Success(),))


async def fetch(ctx, out: Output, *, media_transport=None, media_resolver=system_resolver) -> AsyncIterator[bytes]:
    """The bytes of `out`, streamed from its signed link. The test seams are those of `stream_download`."""
    # ponytail: the link from the reply is used as is (it was fetched minutes after the reply, 23/10); if its signature
    # ever expires before the fetch, re-sign it with `as29s` by `out.upstream_id`.
    async with contextlib.aclosing(stream_download(
            out.ref, hosts=MEDIA_HOSTS, proxy=proxy_url(ctx.account.proxy), max_bytes=MEDIA_MAX_BYTES,
            deadline_s=MEDIA_DEADLINE_S, resolver=media_resolver, transport=media_transport)) as parts:
        async for part in parts:
            yield part
