"""A local HTTP + WebSocket server for the browser suites, and the replay of a recorded network capture.

Nothing here touches a real site: a page served from 127.0.0.1 fetches the recorded bodies (and opens a WebSocket that
sends the recorded frames) so a real Chrome produces the real response and WebSocket events the recorder must read.
Hand-rolled on asyncio so the suites need no dependency beyond the kit's own.
"""

import asyncio
import base64
import hashlib
import json
import struct
from collections.abc import Callable
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "dola" / "s1-network"
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
REASONS = {200: "OK", 404: "Not Found", 502: "Bad Gateway"}


class Request:
    def __init__(self, method: str, target: str, headers: dict[str, str], body: bytes):
        self.method, self.target, self.headers, self.body = method, target, headers, body
        self.path, _, self.query = target.partition("?")


def ws_frame(payload: bytes, binary: bool) -> bytes:
    """One unmasked server frame."""
    n = len(payload)
    head = bytes([0x82 if binary else 0x81])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


class SiteServer:
    """Routes by (method, path). A route is bytes/str (a fixed 200 page) or a callable `Request -> (status, headers,
    body)`. `ws_frames` are sent, in order, to every WebSocket client that connects to `/__ws`, then the socket
    closes."""

    def __init__(self):
        self.routes: dict[tuple[str, str], object] = {}
        self.ws_frames: list[tuple[bool, bytes]] = []
        self.requests: list[Request] = []
        self._server = None
        self.port = 0

    def add(self, method: str, path: str, route, content_type: str = "text/html; charset=utf-8") -> None:
        self.routes[(method, path)] = (route, content_type)

    def url(self, path: str = "/") -> str:
        return "http://127.0.0.1:%d%s" % (self.port, path)

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()

    async def _handle(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            req = Request(method, target, headers, body)
            self.requests.append(req)
            if headers.get("upgrade", "").lower() == "websocket":
                await self._websocket(req, writer)
            else:
                writer.write(self._respond(req))
                await writer.drain()
        except Exception:      # noqa: BLE001 - a client that went away is not a test failure
            pass
        finally:
            writer.close()

    def _respond(self, req: Request) -> bytes:
        entry = self.routes.get((req.method, req.path))
        if entry is None:
            return b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        route, content_type = entry
        status, headers, body = (route(req) if isinstance(route, Callable) else (200, {}, route))
        body = body.encode("utf-8") if isinstance(body, str) else body
        headers = {"Content-Type": content_type, **headers, "Content-Length": str(len(body)), "Connection": "close"}
        head = "HTTP/1.1 %d %s\r\n%s\r\n\r\n" % (status, REASONS.get(status, "OK"),
                                                 "\r\n".join("%s: %s" % kv for kv in headers.items()))
        return head.encode("latin-1") + body

    async def _websocket(self, req: Request, writer) -> None:
        accept = base64.b64encode(hashlib.sha1(req.headers["sec-websocket-key"].encode() + WS_GUID).digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        for binary, payload in self.ws_frames:
            writer.write(ws_frame(payload, binary))
            await writer.drain()
            await asyncio.sleep(0.02)
        writer.write(b"\x88\x00")          # close
        await writer.drain()


REPLAY_PAGE = """<!doctype html><title>replay</title><script>
async function run() {
  const plan = await (await fetch('/__plan')).json();
  for (const [i, step] of plan.entries()) {
    await fetch(step.path + '?n=' + i, {method: 'POST', body: step.req});
  }
  const ws = new WebSocket('ws://' + location.host + '/__ws');
  ws.binaryType = 'arraybuffer';
  await new Promise(done => { ws.onclose = done; ws.onerror = done; });
  window.__replayDone = true;
}
run();
</script>"""


class ReplayServer(SiteServer):
    """Serves one recorded capture (`fixtures/dola/s1-network/<name>.json`): the page POSTs each recorded request
    to its recorded path, the server answers with the recorded body, and the WebSocket sends the recorded frames."""

    def __init__(self, name: str):
        super().__init__()
        self.capture = json.loads((FIXTURES / ("%s.json" % name)).read_text(encoding="utf-8"))
        # a body that could not be read at capture time ("read-failed", status 0) has nothing to replay
        self.steps = [h for h in self.capture["http"] if h["status"] == 200]
        self.ws_frames = [(w["binary"], w["text"].encode("latin-1")) for w in self.capture["ws"]]
        self.add("GET", "/", REPLAY_PAGE)
        self.add("GET", "/__plan", json.dumps([{"path": s["path"], "req": s["req"]} for s in self.steps]),
                 "application/json")
        for path in {s["path"] for s in self.steps}:
            self.add("POST", path, self._answer, "application/json")

    def _answer(self, req: Request):
        step = self.steps[int(req.query.split("n=")[1])]
        return step["status"], {}, step["body"]
