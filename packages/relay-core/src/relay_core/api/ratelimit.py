"""Per-key request limits of the public API, in this process's memory (one api process; Cloudflare limits per IP).

- `RateLimiter`: two token buckets per known key, `read` (GET, HEAD) and `create` (any other method: the POST
  creates). `deps.require_key` takes a token once the key is known, so an unknown key gets its 401 and is never
  bucketed. An empty bucket answers 429 `quota.rate_limited` with `Retry-After`; the request does no other work.
- `BigBodyGate`: ASGI middleware. A public request whose body is large (`Content-Length` at least `api_big_body_mb`
  MiB, or a request other than GET/HEAD with no `Content-Length`) takes an upload slot before its body is read: at
  most `api_big_body_slots` in the process and `api_big_body_slots_per_key` per credential. It waits up to
  `api_big_body_wait_s` for one, then answers 429 `quota.upload_busy`. The slot is given back once the body is read
  (or the answer starts, or the request ends, whatever comes first), so a held or streamed chat keeps none.

Every number is a setting, read at each request: a change applies at once (a bucket keeps its tokens, clamped to the
new capacity, and refills at the new rate).
"""

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from relay_core.api.deps import credential
from relay_core.api.errors_http import error_response, retry_after
from relay_core.keys import hash_key

READS = frozenset({"GET", "HEAD"})
SWEEP_EVERY_S = 60.0
LOG_EVERY_S = 1.0       # a refused key writes at most one request-log row a second
RECHECK_S = 1.0         # a waiting upload looks again this often, so a raised slot setting lets it in


@dataclass
class _Bucket:
    tokens: float
    at: float
    logged: float = -math.inf


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.buckets: dict[tuple[object, str], _Bucket] = {}
        self.swept = clock()

    def take(self, key_id: object, method: str, settings) -> float:
        """0 when the request may go on (a token was taken), else the seconds until a token is back."""
        kind = "read" if method in READS else "create"
        rate, burst = settings.get("api_%s_rate" % kind), settings.get("api_%s_burst" % kind)
        now = self.clock()
        self._sweep(now, settings)
        b = self.buckets.get((key_id, kind))
        if b is None:
            b = self.buckets[(key_id, kind)] = _Bucket(float(burst), now)
        b.tokens, b.at = min(float(burst), b.tokens + (now - b.at) * rate), now
        if b.tokens >= 1:
            b.tokens -= 1
            return 0.0
        return (1 - b.tokens) / rate

    def log_due(self, key_id: object, method: str) -> bool:
        """Whether this refused request is the one of the second that the request log keeps."""
        b = self.buckets[(key_id, "read" if method in READS else "create")]
        if b.at - b.logged < LOG_EVERY_S:
            return False
        b.logged = b.at
        return True

    def _sweep(self, now: float, settings) -> None:
        # A bucket that has refilled to its capacity is the same as none: drop it. ponytail: an O(keys) scan once a
        # minute; only keys that passed the database lookup are bucketed, so the dict holds at most the live keys.
        if now - self.swept < SWEEP_EVERY_S:
            return
        self.swept = now
        full = {kind: (settings.get("api_%s_rate" % kind), settings.get("api_%s_burst" % kind))
                for kind in ("read", "create")}
        for k, b in list(self.buckets.items()):
            rate, burst = full[k[1]]
            if b.tokens + (now - b.at) * rate >= burst and now - b.logged >= LOG_EVERY_S:
                del self.buckets[k]


class BigBodyGate:
    """`is_gated(path)`: the paths whose big bodies take a slot (the public keyed ones). The per-key count is by the
    credential's hash, not a looked-up key: the body has not been read, and no database is asked. Requests with no
    credential share one count, so they hold at most `api_big_body_slots_per_key` slots together.
    ponytail: a client sending many made-up keys can still fill the process slots; Cloudflare's per-IP limit is the
    answer to that, a key lookup here if it is not enough."""

    def __init__(self, app: ASGIApp, *, is_gated: Callable[[str], bool]):
        self.app, self.is_gated = app, is_gated
        self.held: dict[str, int] = {}      # credential hash -> slots it holds
        self.total = 0
        self.waiters: set[asyncio.Future] = set()

    @staticmethod
    def _big(request: Request, limit_mb: int) -> bool:
        length = request.headers.get("content-length")
        if not length:
            return request.method not in READS
        try:
            return int(length) >= limit_mb << 20
        except ValueError:
            return True

    def _free(self, who: str, settings) -> bool:
        return (self.total < settings.get("api_big_body_slots")
                and self.held.get(who, 0) < settings.get("api_big_body_slots_per_key"))

    async def _take(self, who: str, settings, wait_s: float) -> bool:
        """Take a slot, waiting up to `wait_s`. The check and the take have no await between them, so no lock.
        ponytail: no queue order; a waiter may be overtaken by a newer one. Add a FIFO if that is ever unfair."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        while not self._free(who, settings):
            left = deadline - loop.time()
            if left <= 0:
                return False
            woken = loop.create_future()
            self.waiters.add(woken)
            try:
                await asyncio.wait({woken}, timeout=min(left, RECHECK_S))
            finally:
                self.waiters.discard(woken)
        self.total += 1
        self.held[who] = self.held.get(who, 0) + 1
        return True

    def _give_back(self, who: str) -> None:
        """Synchronous, so a cancelled request still frees its slot and wakes the waiters."""
        self.total -= 1
        self.held[who] -= 1
        if not self.held[who]:
            del self.held[who]
        for woken in self.waiters:
            if not woken.done():
                woken.set_result(None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.is_gated(scope["path"]):
            return await self.app(scope, receive, send)
        request = Request(scope)
        settings = scope["app"].state.settings
        if not self._big(request, settings.get("api_big_body_mb")):
            return await self.app(scope, receive, send)
        who = hash_key(credential(request))
        wait_s = settings.get("api_big_body_wait_s")
        if not await self._take(who, settings, wait_s):
            busy = error_response(scope["path"], "quota.upload_busy", headers=retry_after(wait_s))
            return await busy(scope, receive, send)
        holding = True

        def release() -> None:
            nonlocal holding
            if holding:
                holding = False
                self._give_back(who)

        async def body_receive() -> Message:
            msg = await receive()
            if msg["type"] != "http.request" or not msg.get("more_body", False):
                release()                   # the whole body is in (or the client left): the slot is done
            return msg

        async def answer_send(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                release()                   # answered without reading the body
            await send(msg)

        try:
            await self.app(scope, body_receive, answer_send)
        finally:
            release()
