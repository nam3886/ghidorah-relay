"""What the OpenAI and Gemini facades share (contracts 8): holding a request until its job ends, and its final text.

The job is created first (`doors.create_job_for`), so a refusal is a real HTTP error; only then does a facade hold
or stream. A held request never touches the job: when the client leaves or the hold limit passes, the job runs on
and stays charged. A non-stream hold also stops reading the job once its client has gone (the server cancels a
stream as soon as its client disconnects). A database error while waiting ends the wait like the hold limit
does: the job exists and is charged, so the poll hint is the honest answer, never a 500 or a cut stream.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from starlette.requests import ClientDisconnect

from relay_core import errors
from relay_core.api.doors import TERMINAL, job_state, job_status

log = logging.getLogger("relay.api")

HOLD_LIMIT_S = 1800.0       # a stream waits this long at most
NON_STREAM_HOLD_S = 540.0   # a non-stream request answers within this: below the OpenAI SDK's 600 s timeout, so the
#                             SDK never gives up and re-sends; past it the poll hint is the answer
HEARTBEAT_S = 5.0           # a stream says it is alive this often
POLL_S = 1.0                # how often the job row is read while waiting
SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


async def watch(pool: AsyncConnectionPool, job_id,
                gone: Callable[[], Awaitable[bool]] | None = None,
                limit: float | None = None) -> AsyncIterator[tuple[str, Any]]:
    """Yield `("beat", seconds waited)` every HEARTBEAT_S while the job is open, then one last
    `("end", (status, error_code))` when it is terminal, or `("end", None)` when `limit` (HOLD_LIMIT_S) passed first.
    `gone` (the request's `is_disconnected`) is asked before each wait: once the client has left, `ClientDisconnect`
    ends the wait (the request log records it as a hang-up); the job is not touched."""
    start, limit = time.monotonic(), HOLD_LIMIT_S if limit is None else limit
    next_beat = start + HEARTBEAT_S
    while True:
        try:
            state = await job_state(pool, job_id)
        except (psycopg.Error, PoolTimeout):
            log.warning("database error while waiting for job %s", job_id, exc_info=True)
            yield "end", None
            return
        now = time.monotonic()
        if state is not None and state[0] in TERMINAL:
            yield "end", state
            return
        if now - start >= limit or state is None:
            yield "end", None
            return
        if now >= next_beat:
            next_beat += HEARTBEAT_S
            yield "beat", now - start
        if gone is not None and await gone():
            raise ClientDisconnect()
        await asyncio.sleep(POLL_S)


async def hold(pool: AsyncConnectionPool, job_id,
               gone: Callable[[], Awaitable[bool]] | None = None) -> tuple[str, str | None] | None:
    """Wait for the job: its terminal (status, error code), or None when NON_STREAM_HOLD_S passed first or the
    database failed (see `watch`)."""
    async for kind, value in watch(pool, job_id, gone, limit=min(HOLD_LIMIT_S, NON_STREAM_HOLD_S)):
        if kind == "end":
            return value
    return None


def retries_of(header: str | None) -> int:
    """The SDK's `x-stainless-retry-count` (0 for a first send; absent or not a number counts as 0)."""
    try:
        return max(0, int((header or "0").strip()))
    except ValueError:
        return 0


def waiting_text(job_id) -> str:
    return "still rendering — poll job %s" % job_id


async def result_text(pool: AsyncConnectionPool, base_url: str, key_id, job_id, state) -> tuple[str, bool]:
    """(text, finished): the result URLs of a done job, else the poll hint. A failed job never gets here."""
    if state is None or state[0] != "done":
        return waiting_text(job_id), False
    status = await job_status(pool, base_url, key_id, str(job_id))
    return "\n".join(status["result_urls"] or status["images"]), True


def heartbeat_text(waited_s: float) -> str:
    return "Generating… (%ds elapsed)\n" % int(waited_s)


def sse(payload: Any) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def failure_code(state) -> str:
    """The code a failed job ends with (a failed job always has one; the net is the internal code)."""
    return (state[1] if state else None) or errors.INTERNAL
