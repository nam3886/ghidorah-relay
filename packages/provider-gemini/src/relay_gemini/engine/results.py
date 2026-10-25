"""After a lost answer: find the job's chat (reconcile), read what it holds (poll), and download a result (fetch).
Reads only: nothing here sends, so nothing here can spend.

- Reconcile: a job whose send answered its conversation id is read by that id. Otherwise the chat list (`MaZiqc`) is
  read: gemini lists each chat with the time its send started (measured 1 s after the POST) and shows it as soon as the
  answer is done; a chat started within `FIND_BEFORE_S`/`FIND_AFTER_S` of the job's `sent_at` whose first prompt is
  the job's exact sent text (`hNvQHb`) is the job's: `Found`. The account sends one job at a time (`account_slots` 1),
  so no other send of the account starts in that window. Not found is `StillUnknown(answered=True)`, never `NotSent`:
  a send cut before its answer ended left no chat in the spike (FINDINGS 4), but that is not proof, so the render
  deadline ends such a job with its refund and it is never sent again.
- Poll (a job `Found`, or a video job `Accepted`): the chat read, judged like a send's answer (`submit.judge`): the
  job's media (an image, or a video) is `Done`; a final text and no media is `Failed` (refunded; its pool is emptied as
  a send's refusal empties it, `submit.limit_effects`); a video still "generating" `gemini.silent_video_after_s`
  after its send on an account whose 5-hour window cannot hold a video is `Failed` as the limit (measured 25/10: such
  a video is never rendered), with the video pool empty until the window resets; anything else `Pending`.
- Fetch: the result link resolved to the file's bytes link with the session (`Site.resolve`: an image's original, a
  video's 720p MP4), then those bytes from lh3 (an image) or googlevideo.com (a video) with no cookie, through the
  account's proxy (never direct when it has one), with the User-Agent the link was resolved with: a googlevideo.com
  link is signed for that client (`eaua`) and answers any other User-Agent 403 (measured 25/10, live-261025). A song's
  MP4 is named in its send's answer by a `/gg-dl/` link that resolves only to its poster frame: its chat is read for
  the `/gg/` link of the same MP4 first.
"""

import contextlib
import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import httpx

from relay_core.net import stream_download, system_resolver
from relay_core.providers import (
    Done,
    Failed,
    Found,
    Output,
    Pending,
    PollResult,
    Reconciled,
    StillUnknown,
    Success,
)
from relay_gemini import errors
from relay_gemini.constants import (
    BYTES_HOST,
    FIND_AFTER_S,
    FIND_BEFORE_S,
    FULL_SIZE,
    LIST_PAGE,
    MEDIA_DEADLINE_S,
    MEDIA_MAX_BYTES,
    USER_AGENT,
    VIDEO_BYTES_HOST,
    VIDEO_SIZE,
)
from relay_gemini.engine.check import read_short_window
from relay_gemini.engine.http import Sessions, Site, proxy_url
from relay_gemini.engine.reply import Turn, chats, same_text, turns
from relay_gemini.engine.submit import (
    CHAT_KEY,
    REQUEST_KEY,
    is_music,
    is_video,
    job_pool,
    judge,
    limit_effects,
    limit_read_pool,
    outputs_of,
    sent_text,
    short_balance,
    usage_spent,
    video_room_short,
)

LIST_CHATS = "MaZiqc"
READ_CHAT = "hNvQHb"


async def read_chat(site: Site, chat: str) -> tuple[Turn | None, bool]:
    """The first turn of one chat, and whether the read was answered (200, clean, ended)."""
    status, body = await site.rpc(READ_CHAT, [chat, 10, None, 1, [0], [4], None, 1], "/app/%s" % chat[2:])
    if status != 200:
        return None, False
    found, ok = turns(body)
    return (found[0] if found else None), ok


async def _find(site: Site, job) -> tuple[str | None, bool]:
    """(the job's chat, answered): the chats started near the send, oldest first, the first whose prompt is the job's
    sent text."""
    if job.sent_at is None:
        return None, False
    status, body = await site.rpc(LIST_CHATS, [LIST_PAGE, None, [0, None, 1]])
    listed, ok = chats(body) if status == 200 else ([], False)
    if not ok:
        return None, False
    # ponytail: one page of LIST_PAGE chats, the newest; a job whose chat fell off it (more than 13 chats started on
    # the account since) is not found and the render deadline refunds it. Page on through MaZiqc if that ever bites.
    start = job.sent_at.timestamp()
    near = sorted((c for c in listed if start - FIND_BEFORE_S <= c.started <= start + FIND_AFTER_S),
                  key=lambda c: c.started)
    text, answered = sent_text(job), True
    for c in near:
        turn, read = await read_chat(site, c.cid)
        answered = answered and read
        if turn is not None and same_text(turn.prompt, text):
            return c.cid, True
    return None, answered


async def reconcile(ctx, job, settings, sessions: Sessions, *, transport=None) -> Reconciled:
    """Did the send of an unknown job reach gemini? Its chat is `Found`; none is `StillUnknown`."""
    state = job.provider_state or {}
    try:
        async with Site(ctx, sessions, settings, transport=transport) as site:
            if await site.open_account() is None:      # never a chat or a prompt of another Google account
                return StillUnknown()
            chat = state.get(CHAT_KEY)
            if chat:
                turn, answered = await read_chat(site, chat)
                if turn is None:
                    return StillUnknown(answered=answered)
            else:
                chat, answered = await _find(site, job)
                if chat is None:
                    return StillUnknown(answered=answered)
    except httpx.HTTPError:
        return StillUnknown()
    return Found(chat, {CHAT_KEY: chat, REQUEST_KEY: state.get(REQUEST_KEY)}, True)


def interval_s(settings, rand: Callable[[], float] = random.random) -> int:
    base = int(settings.get("gemini.poll_interval_s"))
    return base + int(rand() * base / 2)


async def poll(ctx, job, settings, sessions: Sessions, *, transport=None,
               rand: Callable[[], float] = random.random,
               now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> PollResult:
    """The found chat's answer, judged like a send's."""
    chat, wait = (job.provider_state or {}).get(CHAT_KEY), interval_s(settings, rand)
    if not chat:
        return PollResult(Pending("no chat to read", wait, answered=False))
    try:
        async with Site(ctx, sessions, settings, transport=transport) as site:
            if await site.open_account() is None:
                return PollResult(Pending("gemini's page is signed out or not the account's Google account", wait,
                                          answered=False))
            turn, answered = await read_chat(site, chat)
            if turn is None:
                return PollResult(Pending("the chat could not be read", wait, answered=answered))
            verdict, what = judge(200, turn.answer(chat), video=is_video(job), music=is_music(job))
            # the usage reads (they spend nothing) while the site is open: a limit's reset, a silent render's window
            pool = limit_read_pool(*what, job_pool(job)) if verdict == "refused" else None
            spent = await usage_spent(site, pool, settings) if pool else None
            room = await read_short_window(site) if verdict == "taken" and silent_due(job, settings, now()) else None
    except httpx.HTTPError as e:
        return PollResult(Pending("gemini could not be read (%s)" % type(e).__name__, wait, answered=False))
    if verdict == "done":
        return PollResult(Done(outputs_of(what, chat)), effects=(Success(),))
    if verdict == "refused":
        code, text = what
        return PollResult(Failed(code, "gemini answered: %s" % text[:300]),
                          effects=limit_effects(settings, now(), code, text, is_video(job), spent))
    if video_room_short(settings, room):
        # measured 25/10: a video sent into a used-up 5-hour window is taken ("generating") and never rendered
        return PollResult(Failed(errors.VIDEO_LIMIT, "gemini took the video but rendered nothing while the account's "
                                 "5-hour usage window could not hold one"), effects=(short_balance(room),))
    return PollResult(Pending("gemini is generating the video" if verdict == "taken" else what, wait))


def silent_due(job, settings, now: datetime) -> bool:
    """Has a taken video shown as generating for `gemini.silent_video_after_s` since its send?"""
    return job.sent_at is not None and (now - job.sent_at).total_seconds() >= int(
        settings.get("gemini.silent_video_after_s"))


async def fetch(ctx, out: Output, settings, sessions: Sessions, *, transport=None, media_transport=None,
                media_resolver=system_resolver) -> AsyncIterator[bytes]:
    """The bytes of `out`: its link resolved with the session (cookies only where their domain covers a hop), then
    streamed with no cookie from lh3 (an image) or googlevideo.com (a video). Both through the account's proxy. A link
    that does not resolve raises (the fetcher retries; the render deadline bounds it)."""
    video = out.mime.startswith("video/")
    async with Site(ctx, sessions, settings, transport=transport) as site:
        if await site.open_account() is None:
            raise RuntimeError("the account's Google account could not be verified")
        ref = out.ref
        if video and "/gg-dl/" in ref:
            # a song's MP4 comes in the send's own answer under a /gg-dl/ link, which serves only its poster frame
            # (25/10); the chat names the same MP4 by a /gg/ link that serves it
            chat = (out.meta or {}).get("chat")
            turn = (await read_chat(site, chat))[0] if chat else None
            if turn is None or len(turn.videos) <= out.idx:
                raise RuntimeError("the song's chat named no video to fetch")
            ref = turn.videos[out.idx].link
        link = await site.resolve(ref, VIDEO_SIZE if video else FULL_SIZE)
    if link is None:
        raise RuntimeError("the result link did not resolve to the file")
    async with contextlib.aclosing(stream_download(
            link, hosts=(VIDEO_BYTES_HOST,) if video else (BYTES_HOST,), proxy=proxy_url(ctx.account.proxy),
            max_bytes=MEDIA_MAX_BYTES,
            deadline_s=MEDIA_DEADLINE_S, resolver=media_resolver, transport=media_transport,
            user_agent=USER_AGENT)) as parts:
        async for part in parts:
            yield part
