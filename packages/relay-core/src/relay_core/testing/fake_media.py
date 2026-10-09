"""A scriptable media server for `net.stream_download` tests: no socket, no real body in memory.

`Body(n)` is N bytes generated as they are read, with their sha256, so a 200 MB download costs a few MiB.
`MediaSite` is the `transport` factory the helper takes (route proxy -> httpx transport). It records which route each
request used, what each request carried, and every stream close, and answers from a script per route.

A script is an `Answer`, bytes / `Body` (a 200 with that body), an int (that status, empty body), an exception (raised
by the transport: a connect error), a dict url -> script, or a
callable(request) -> script.
"""

import asyncio
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

PIECE = 1 << 16
PUBLIC_IP = "93.184.216.34"


class Body:
    """`size` bytes, never held whole: `pieces()` makes them again on every call."""

    def __init__(self, size: int, data: bytes | None = None):
        self.size, self._data = size, data

    @classmethod
    def of(cls, data: bytes) -> "Body":
        return cls(len(data), data)

    def pieces(self) -> Iterator[bytes]:
        if self._data is not None:
            yield from (self._data[i:i + PIECE] for i in range(0, self.size, PIECE))
            return
        for n, start in enumerate(range(0, self.size, PIECE)):
            block = hashlib.sha256(n.to_bytes(8, "big")).digest() * (PIECE // 32)
            yield block[:min(PIECE, self.size - start)]

    def sha256(self) -> str:
        h = hashlib.sha256()
        for piece in self.pieces():
            h.update(piece)
        return h.hexdigest()


@dataclass
class Answer:
    """One scripted response. `fail_after`/`stall_after`/`hang_after` count body bytes sent before the body breaks:
    a read error, a read timeout (the stall the client's own timeout would catch), a wait that never ends.
    `content_length` overrides the header (a body shorter than it is a truncated download). `delay` is a sleep before
    each piece (a slow trickle). `chunked` is a body with no Content-Length (chunked framing, which the client checks
    itself); `no_length` is one with neither, which no client can check; `hang_close` is a stream whose close never
    ends."""

    status: int = 200
    body: Body | bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    content_length: int | None = None
    fail_after: int | None = None
    stall_after: int | None = None
    hang_after: int | None = None
    delay: float = 0.0
    chunked: bool = False
    no_length: bool = False
    hang_close: bool = False


class _Stream(httpx.AsyncByteStream):
    def __init__(self, site: "MediaSite", answer: Answer, body: Body):
        self.site, self.answer, self.body = site, answer, body

    async def __aiter__(self):
        a, sent = self.answer, 0
        for piece in self.body.pieces():
            if a.delay:
                await asyncio.sleep(a.delay)
            for limit, error in ((a.fail_after, httpx.ReadError("reset")),
                                 (a.stall_after, httpx.ReadTimeout("stalled"))):
                if limit is not None and sent + len(piece) > limit:
                    if limit > sent:
                        yield piece[:limit - sent]
                    raise error
            if a.hang_after is not None and sent + len(piece) > a.hang_after:
                if a.hang_after > sent:
                    yield piece[:a.hang_after - sent]
                await asyncio.Event().wait()
            sent += len(piece)
            yield piece

    async def aclose(self) -> None:
        self.site.closed += 1
        if self.answer.hang_close:
            await asyncio.Event().wait()


class MediaSite:
    """`MediaSite(script_for_direct, script_for_proxy)`; `site(proxy)` is the transport factory.
    `routes` is the proxy URL (None = direct) of every request, in order."""

    def __init__(self, direct: Any = None, proxy: Any = None):
        self.scripts: dict[str | None, Any] = {}
        self._direct, self._proxy = direct, proxy
        self.requests: list[tuple[str | None, httpx.Request]] = []
        self.closed = 0

    def __call__(self, route: str | None) -> httpx.AsyncBaseTransport:
        self.scripts[route] = self._proxy if route is not None else self._direct
        return _Transport(self, route)

    @property
    def routes(self) -> list[str | None]:
        return [r for r, _ in self.requests]

    def _answer(self, route: str | None, request: httpx.Request) -> Answer:
        script = self.scripts[route]
        while True:
            if callable(script) and not isinstance(script, (Answer, type)):
                script = script(request)
            elif isinstance(script, dict):
                script = script[logical_url(request)]
            else:
                break
        if isinstance(script, Exception):
            raise script
        if isinstance(script, int):
            return Answer(script)
        if isinstance(script, (bytes, Body)):
            return Answer(body=script)
        return script


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, site: MediaSite, route: str | None):
        self.site, self.route = site, route

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.site.requests.append((self.route, request))
        answer = self.site._answer(self.route, request)
        body = answer.body if isinstance(answer.body, Body) else Body.of(answer.body)
        headers = dict(answer.headers)
        length = body.size if answer.content_length is None else answer.content_length
        if answer.chunked:
            headers["transfer-encoding"] = "chunked"
        elif not answer.no_length:
            headers["content-length"] = str(length)
        return httpx.Response(answer.status, headers=headers, stream=_Stream(self.site, answer, body),
                              request=request)


def logical_url(request: httpx.Request) -> str:
    """The link the client asked for: the direct route connects to a checked IP and names the host in `Host`."""
    host = request.headers.get("host", "").rsplit(":", 1)[0]
    return str(request.url.copy_with(host=host)) if host and not host.endswith("]") else str(request.url)


async def public_resolver(host: str, port: int) -> list[str]:
    """Every name resolves to one public address."""
    return [PUBLIC_IP]


def seen(site: MediaSite) -> list[dict[str, Any]]:
    """What each request carried: route, url (the logical one), header names (lower case)."""
    return [{"route": r, "url": logical_url(q), "headers": {k.lower() for k in q.headers}} for r, q in site.requests]

