"""`NetRecorder`: passive capture of what the page itself receives (contracts §6.2).

It listens (`page.on("response")`, `page.on("websocket")`) and never intercepts: no route, no CDP `Fetch`, so the
HTTP cache and request signatures are untouched. The site's profile declares what to keep: body paths (regular
expressions over the URL path) and WebSocket hosts. Everything else is ignored.

Events keep the order in which the page received them, also when a body takes longer to read than a later one.
"""

import asyncio
import collections
import contextlib
import itertools
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger("relay_browser.net")

MAX_BODY_CHARS = 2_000_000
# The dump prints at most DUMP_EVENT_CHARS per field, so evidence needs few events and little text: 500 events and 8 M
# characters hold four whole 2 M bodies, and bound the redaction work of one dump to a few seconds, off the loop.
MAX_EVENTS = 500                  # the oldest events go first; `dropped` counts them
MAX_TOTAL_CHARS = 8_000_000       # request and body text of all events held
DUMP_EVENT_CHARS = 20_000
# A field is cut to DUMP_EVENT_CHARS + this before redaction (one regex call holds the GIL for its whole run, so a
# 2 M-character field would stall the worker's loop), then redacted, then cut to DUMP_EVENT_CHARS. The margin covers
# the largest structure a redactor recognises whole (the core's cookie object is 4 000 characters; a provider may
# register a bigger pattern): a secret that starts inside the printed part and runs past it would stay unredacted.
REDACT_MARGIN_CHARS = 16384
READ_ERROR_CHARS = 200


@dataclass
class NetEvent:
    seq: int
    t: float                  # seconds since the recorder started
    kind: str                 # "http" or "ws"
    url: str
    path: str
    method: str = ""
    status: int = 0
    req: str = ""
    body: str = ""            # a binary WebSocket frame is read as latin-1: one character per byte
    binary: bool = False
    error: str = ""           # why the body could not be read ("" = read)
    ready: bool = False       # an http body is read in the background; ws frames are ready at once
    held: bool = True         # still in the recorder (False once the bounds pushed it out)

    def json(self):
        return json.loads(self.body)


class NetRecorder:
    def __init__(self, body_paths: Iterable[str] = (), ws_hosts: Iterable[str] = (), *,
                 max_body_chars: int = MAX_BODY_CHARS, max_events: int = MAX_EVENTS,
                 max_total_chars: int = MAX_TOTAL_CHARS, clock: Callable[[], float] = time.monotonic):
        self._paths = [re.compile(p) for p in body_paths]
        self._ws_hosts = {h.lower() for h in ws_hosts}
        self._max = max_body_chars
        self._max_events, self._max_total = max_events, max_total_chars
        self.dropped = 0                  # events thrown away to stay inside the bounds
        self._seq = itertools.count()
        self._clock = clock
        self._t0 = clock()
        self._events: collections.deque[NetEvent] = collections.deque()
        self._total = 0                   # request and body characters of the events held, kept running
        self._tasks: set[asyncio.Task] = set()
        self._changed = asyncio.Event()

    # ---- attach ------------------------------------------------------------------------------------------------
    def attach(self, page) -> None:
        page.on("response", self._on_response)
        page.on("websocket", self._on_websocket)

    def close(self) -> None:
        """Stop reading bodies that are still in flight."""
        for task in list(self._tasks):
            task.cancel()

    def now(self) -> float:
        """The recorder's clock, for `events(since)`: take it just before the action whose events you want."""
        return self._clock() - self._t0

    # ---- capture -----------------------------------------------------------------------------------------------
    def _add(self, **fields) -> NetEvent:
        ev = NetEvent(seq=next(self._seq), t=self.now(), **fields)
        self._events.append(ev)
        self._total += len(ev.req) + len(ev.body)
        self._trim()
        return ev

    def _trim(self) -> None:
        """Keep at most `max_events` events and `max_total_chars` of text: the oldest go first."""
        while self._events and (len(self._events) > self._max_events or self._total > self._max_total):
            gone = self._events.popleft()
            gone.held = False
            self._total -= len(gone.req) + len(gone.body)
            self.dropped += 1

    def _notify(self) -> None:
        self._changed.set()

    def _on_response(self, response) -> None:
        try:
            url = response.url
            path = urlsplit(url).path
            if not any(p.search(path) for p in self._paths):
                return
            ev = self._add(kind="http", url=url, path=path, status=response.status,
                           method=response.request.method)
        except Exception:      # noqa: BLE001 - a listener must never raise into the page's event loop
            return
        task = asyncio.ensure_future(self._read(ev, response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read(self, ev: NetEvent, response) -> None:
        try:
            try:
                ev.req = (response.request.post_data or "")[:self._max]
            except Exception:      # noqa: BLE001 - a binary request body has no text
                ev.req = ""
            try:
                ev.body = (await response.text())[:self._max]
            except asyncio.CancelledError:
                ev.error = "cancelled"          # `close` stopped it: not an empty reply
                raise
            except Exception as exc:      # noqa: BLE001 - a redirect or a closed page has no body to read
                ev.error = str(exc).splitlines()[0][:READ_ERROR_CHARS] if str(exc) else type(exc).__name__
        finally:
            ev.ready = True
            if ev.held:           # the body just read counts now (an event already pushed out never counted)
                self._total += len(ev.req) + len(ev.body)
            self._trim()
            self._notify()

    def _on_websocket(self, ws) -> None:
        try:
            parts = urlsplit(ws.url)
            if (parts.hostname or "").lower() not in self._ws_hosts:
                return
            ws.on("framereceived", lambda payload: self._on_frame(ws.url, parts.path, payload))
        except Exception:      # noqa: BLE001
            return

    def _on_frame(self, url: str, path: str, payload) -> None:
        binary = isinstance(payload, (bytes, bytearray))
        text = bytes(payload).decode("latin-1") if binary else str(payload)
        self._add(kind="ws", url=url, path=path, body=text[:self._max], binary=binary, ready=True)
        self._notify()

    # ---- read --------------------------------------------------------------------------------------------------
    def events(self, since: float = 0.0) -> list[NetEvent]:
        """Events whose body is read, in the order the page received them, from `since` (seconds on `now()`)."""
        return [e for e in self._events if e.ready and e.t >= since]

    def first(self, path_re: str, body_contains: str | None = None, since: float = 0.0) -> NetEvent | None:
        """The first event whose path matches and whose body (when asked) holds `body_contains`."""
        pattern = re.compile(path_re)
        for e in self.events(since):
            if pattern.search(e.path) and (body_contains is None or body_contains in e.body):
                return e
        return None

    async def wait_for(self, predicate: Callable[[NetEvent], bool], timeout: float,
                       since: float = 0.0) -> NetEvent | None:
        """The first event for which `predicate` holds, waiting up to `timeout` seconds; None when none arrived (the
        caller falls back to the UI and logs `source=engine`)."""
        deadline = self._clock() + timeout
        checked: set[int] = set()            # a ready event never changes: the predicate sees each one once
        while True:
            self._changed.clear()
            for e in self.events(since):
                if e.seq in checked:
                    continue
                checked.add(e.seq)
                try:
                    hit = predicate(e)
                except Exception as exc:      # noqa: BLE001 - a predicate bug (a body that is not JSON) is "no match"
                    log.warning("wait_for predicate raised %s on %s: treated as no match",
                                type(exc).__name__, e.path)
                    continue
                if hit:
                    return e
            remaining = deadline - self._clock()
            if remaining <= 0:
                return None
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), remaining)

    def dump(self, redact: Callable[[str], str], since: float = 0.0) -> str:
        """The network log as text, through the caller's redactor (the kit holds no secret patterns of its own).
        One block per event with its time on the recorder's clock. Each field is cut a margin past the 20 000 printed
        characters, redacted, then cut (a cut made first could split a secret into something the redactor no longer
        recognises); the whole block is redacted once more for the head line and the read error."""
        return _render(self.events(since), self.dropped, redact)

    async def dump_async(self, redact: Callable[[str], str], since: float = 0.0) -> str:
        """`dump` without blocking the event loop: the events are taken here, the redaction runs in a thread."""
        events, dropped = self.events(since), self.dropped
        return await asyncio.to_thread(_render, events, dropped, redact)


def redact_cut(text: str, limit: int, redact: Callable[[str], str]) -> str:
    """`text` redacted and cut to `limit` chars, without a cut ever splitting a secret the redactor would catch.

    Redacting the whole text can be slow, so only a margin past the cut is redacted. That alone is not enough:
    redaction shrinks the text (a long token becomes `[redacted]`), which pulls the slice's far end, where a secret
    may have been cut, into the first `limit` chars. So the text is also redacted up to the cut, and only the part on
    which both agree is kept: a secret cut at either end is exactly where the two differ."""
    if len(text) <= limit:
        return redact(text)
    wide = redact(text[:limit + REDACT_MARGIN_CHARS])
    near = redact(text[:limit])
    return os.path.commonprefix([wide, near])[:limit]


def _field(text: str, redact: Callable[[str], str]) -> str:
    return redact_cut(text, DUMP_EVENT_CHARS, redact)


def _render(events: list[NetEvent], dropped: int, redact: Callable[[str], str]) -> str:
    out = []
    if dropped:
        out.append("[%d earlier events were dropped to stay inside the recorder's bounds]" % dropped)
    for e in events:
        lines = ["%.3f %s %s %s %s" % (e.t, e.kind, e.method or "-", e.status or "-", e.url)]
        if e.req:
            lines.append("  req: " + _field(e.req, redact))
        if e.error:
            lines.append("  read-failed: " + e.error)
        if e.body:
            lines.append("  %s: %s" % ("frame(binary)" if e.binary else "body", _field(e.body, redact)))
        out.append(redact("\n".join(lines)))
    return "\n".join(out)
