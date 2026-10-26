"""The tunnel to the account's muse VM: a Noise_XX handshake on a WebSocket, then HTTP-over-Noise calls on it.

One call at a time (the engine never runs two): `call` sends one request and reads frames until its stream ends,
keeping the events of the open subscription (`/chat/subscribe`, a stream that never ends) on `events`. The WebSocket
is a seam (`send`, `recv`, `close`): the real one is curl_cffi's (engine.web), tests pass an in-process server.
"""

import asyncio
import json
import os
import time
from collections.abc import Callable
from typing import Any, NamedTuple, Protocol

from relay_muse.engine import noise


class Socket(Protocol):
    async def send(self, data: bytes) -> None: ...
    async def recv(self) -> bytes: ...
    async def close(self) -> None: ...


class TunnelError(Exception):
    """The tunnel broke, a stream was reset, or a call got no whole answer in its time. Never holds a token."""


class Reply(NamedTuple):
    status: int
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body) if self.body else None
        except ValueError:
            return None


class Tunnel:
    def __init__(self, ws: Socket, send: noise.Cipher, receive: noise.Cipher,
                 clock: Callable[[], float] = time.monotonic):
        self.ws, self._tx, self._rx, self.clock = ws, send, receive, clock
        self._sid = 0
        self._parts = noise.Reassembler()
        self._status: dict[int, int] = {}
        self._bodies: dict[int, bytearray] = {}
        self._done: dict[int, Reply] = {}
        self._reset: dict[int, str] = {}
        self.stream: int | None = None          # the subscription's stream id
        self.events: list[dict] = []            # its events, oldest first

    @classmethod
    async def open(cls, ws: Socket, timeout: float, clock: Callable[[], float] = time.monotonic) -> "Tunnel":
        """The handshake: message 1 with the freshness nonce, the server's message 2, an empty message 3, then one
        empty transport frame (the web client's kick)."""
        hs = noise.Initiator()
        await ws.send(hs.message1(b"\x0a\x20" + os.urandom(32)))
        try:
            reply = await asyncio.wait_for(ws.recv(), timeout)
            hs.read2(reply)
        except TimeoutError:
            raise TunnelError("no handshake answer within %g s" % timeout) from None
        except Exception as e:
            raise TunnelError("the handshake failed (%s)" % type(e).__name__) from None
        last, send, receive = hs.message3(b"")
        await ws.send(last)
        await ws.send(send.encrypt(b"", b""))
        return cls(ws, send, receive, clock)

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:      # noqa: BLE001 - closing a dead socket says nothing
            pass

    async def _start(self, verb: str, path: str, body: Any = None, raw: bytes | None = None,
                     service: int = noise.DAEMON) -> int:
        self._sid += 1
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        headers = ([("content-type", "application/json")] if body is not None else []) + [("accept-language", "en-US")]
        payload = noise.request_payload(self._sid, verb, path, headers, data, service)
        for frame in noise.chunks(self._sid, payload):
            await self.ws.send(self._tx.encrypt(b"", frame))
        return self._sid

    async def _pump(self, timeout: float) -> bool:
        """Read and file one frame; False when none came within `timeout`. TunnelError when the socket broke."""
        if timeout <= 0:
            return False
        try:
            data = await asyncio.wait_for(self.ws.recv(), timeout)
        except TimeoutError:
            return False
        except Exception as e:
            raise TunnelError("the tunnel closed (%s)" % type(e).__name__) from None
        if not data:
            return True
        try:
            part = self._parts.feed(self._rx.decrypt(b"", data))
        except Exception as e:
            raise TunnelError("an unreadable frame (%s)" % type(e).__name__) from None
        if part is not None:
            self._file(part)
        return True

    def _file(self, part: noise.Part) -> None:
        sid = part.stream_id
        if part.kind == "reset":
            self._reset[sid] = "%d %s" % (part.status, part.reason[:200])
            return
        if part.kind == "response":
            self._status[sid] = part.status
        if sid == self.stream:
            if part.kind == "body" and part.data:
                try:
                    event = json.loads(part.data)
                except ValueError:
                    return
                if isinstance(event, dict):
                    self.events.append(event)
            return
        self._bodies.setdefault(sid, bytearray()).extend(part.data)
        if part.end:
            self._done[sid] = Reply(self._status.get(sid, 0), bytes(self._bodies.pop(sid, b"")))

    async def _wait(self, sid: int, timeout: float, until: Callable[[], bool]) -> None:
        end = self.clock() + timeout
        while not until():
            if sid in self._reset:
                raise TunnelError("the stream was reset: %s" % self._reset.pop(sid))
            left = end - self.clock()
            if left <= 0:
                raise TunnelError("no whole answer within %g s" % timeout)
            await self._pump(min(left, 1.0))

    async def call(self, verb: str, path: str, body: Any = None, *, raw: bytes | None = None,
                   service: int = noise.DAEMON, timeout: float = 30.0) -> Reply:
        """One request and its whole answer. TunnelError on a reset, a broken socket or `timeout`."""
        sid = await self._start(verb, path, body, raw, service)
        await self._wait(sid, timeout, lambda: sid in self._done)
        return self._done.pop(sid)

    async def subscribe(self, body: Any, timeout: float = 30.0) -> int:
        """Open the chat subscription (a stream that never ends); its status. Its events land on `events`."""
        sid = await self._start("POST", "/chat/subscribe", body)
        self.stream = sid
        await self._wait(sid, timeout, lambda: sid in self._status)
        return self._status[sid]

    async def listen(self, seconds: float) -> None:
        """Read whatever comes (subscription events) for `seconds`."""
        end = self.clock() + seconds
        while (left := end - self.clock()) > 0:
            await self._pump(left)
