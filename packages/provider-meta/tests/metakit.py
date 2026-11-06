"""Test kit for meta over HTTP: planted secrets, a sealed jar, the settings, a recording `RunCtx`, planned jobs, a fake
curl_cffi `AsyncSession` (and its WebSocket) fed from a route table, a fake clock, and the 05/11 captures
(fixtures/meta/http-261105, read at call time).

Every request any fake session makes goes, in order, into `Net.calls`; every platform step the ctx takes goes into
`Ctx.calls`. The fixture's token and viewerId (redacted fakes) and the planted values stand for the secrets:
`assert_no_secret` proves none of them reached a text the run produced."""

import asyncio
import base64
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from relay_core.providers import AccountView, JobView, Proxy
from relay_core.testing.fake_provider import FakeSettings
from relay_meta.catalog import IMAGE_MODEL, VIDEO_MODEL
from relay_meta.credential import parse_credential, viewer_hash
from relay_meta.spec import SPEC

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "meta" / "http-261105"
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def page(name: str) -> str:
    """A recorded page by its file stem (`page_signed_in`, `prompt_page_t2i_n2`, …)."""
    return (FIX / (name.removesuffix(".html") + ".html")).read_text(encoding="utf-8")


def frames(name: str, out: bool = False) -> list[bytes]:
    """The raw frames of a recorded job (`t2i_n2`, `frames_refusal`, …) in order: the gateway's (default), or the
    two the client sent (`out=True`)."""
    stem = name if name.startswith("frames_") else "frames_" + name
    rows = json.loads((FIX / (stem + ".json")).read_text(encoding="utf-8"))
    return [base64.b64decode(r["b64"]) for r in rows if bool(r.get("dir") == "out") == out]


# ---- secrets: never in a log, an error, an event, a state or evidence --------------------------------------------
_SIGNED_IN = page("page_signed_in")
TOKEN = re.search(r'\\"accessToken\\":\\"(ecto1:[^"\\]+)\\"', _SIGNED_IN).group(1)
VIEWER = re.search(r'\\"viewerId\\":\\"(\d+)\\"', _SIGNED_IN).group(1)
NAME = re.search(r'\\"username\\":\\"([^"\\]+)\\"', _SIGNED_IN).group(1)
SESSION = "ecto-PLANTED-session-0001"
PROXY = Proxy("10.0.0.9", 3128, "user:PLANTED-pw")
# the gateway closing the socket as seen live on 06/11 (curl flags 8 = CURLWS_CLOSE): close code 4003, then a
# 16-byte reason (synthetic here). Read as a frame, its first 6 bytes look like an 18-byte 0x0f frame that lies.
CLOSE_4003 = ((4003).to_bytes(2, "big") + b"Unauthorized....", 8)
PLANTED: tuple[str, ...] = (TOKEN, VIEWER, SESSION, "PLANTED-pw")


def assert_no_secret(*texts: object) -> None:
    """None of `PLANTED` (nor the token without its prefix) in any of `texts` (each read as `str`). The failure names
    the value and the text's index, never the text."""
    names = ("TOKEN", "VIEWER", "SESSION", "proxy password", "token body")
    for at, text in enumerate(texts):
        text = str(text)
        for name, piece in zip(names, (*PLANTED, TOKEN.removeprefix("ecto1:")), strict=True):
            assert piece not in text, "planted %s leaked in text #%d" % (name, at)


# ---- the account ------------------------------------------------------------------------------------------------
def jar(*extra: tuple[str, str]) -> bytes:
    """The sealed jar of a Cookie-Editor export: the session cookie, a Vercel pass, and `extra` (name, value)."""
    cookies = [{"name": "ecto_1_sess", "value": SESSION, "domain": ".meta.ai", "path": "/", "secure": True,
                "httpOnly": True, "expirationDate": 1800000000},
               {"name": "_vcrcs", "value": "vcrcs-1", "domain": "www.meta.ai", "path": "/"},
               *({"name": n, "value": v, "domain": ".meta.ai", "path": "/"} for n, v in extra)]
    return parse_credential(json.dumps(cookies).encode()).blob


def settings(**over) -> FakeSettings:
    """The spec's defaults (and the core's), with `meta.<key>` overridden by `key=value`."""
    return FakeSettings(SPEC, **{"meta__" + k: v for k, v in over.items()})


def job(operation: str = "text_to_image", sent_text: str = "a red fox sitting in a snowy forest [7100]",
        n: int | None = None, ratio: str | None = None, external_ref: str | None = None, state: dict | None = None,
        inputs=()) -> JobView:
    kind = "video" if operation.endswith("video") else "image"
    params = {"kind": kind, **({"n": n} if n else {}), **({"ratio": ratio} if ratio else {})}

    async def load():
        return list(inputs)
    return JobView("job-1", "meta", operation, VIDEO_MODEL if kind == "video" else IMAGE_MODEL, params, sent_text,
                   "7100", sent_text, None, None, external_ref, state or {}, load)


class Ctx:
    """The RunCtx an engine sees. Each platform step goes, in order, into `calls`: ("report_ref", ref, state),
    ("mark_sent",), ("event", text, stage), ("evidence", name, mime), ("renew",). `fail` names a step that raises (a
    lost lease, a failed write-back); `renew_delay_s` stalls the write-back. `stored` is the credential now stored."""

    def __init__(self, *, proxy: Proxy | None = PROXY, blob: bytes | None = None, state: dict | None = None,
                 account_id: str = "acc-1", gen: int = 1, fail: str | None = None, renew_delay_s: float = 0,
                 settings_=None):
        self.account = AccountView(account_id, "meta-1", jar() if blob is None else blob, gen, proxy,
                                   {"viewer": viewer_hash(VIEWER)} if state is None else state,
                                   {"image": None, "video": None}, "pk")
        self.settings = settings_ if settings_ is not None else settings()
        self.calls: list[tuple] = []
        self.refs: list[tuple[str, dict]] = []
        self.events: list[tuple[str, str, str]] = []
        self.evidences: list[tuple[str, bytes, str]] = []
        self.renewed: list[bytes] = []
        self.stored = self.account.credential
        self.fail, self.renew_delay_s = fail, renew_delay_s

    def _step(self, *call) -> None:
        self.calls.append(call)
        if self.fail == call[0]:
            raise RuntimeError("lease lost")

    async def fence(self):
        self._step("fence")

    async def report_ref(self, ref, state):
        self.refs.append((ref, dict(state)))
        self._step("report_ref", ref, dict(state))

    async def mark_sent(self):
        self._step("mark_sent")

    async def event(self, text, stage, source):
        self.events.append((text, stage, source))
        self._step("event", text, stage)

    async def evidence(self, name, data, mime):
        self.evidences.append((name, data, mime))
        self._step("evidence", name, mime)

    async def renew_credential(self, blob, merge=None):
        """Stores `merge(stored, blob)` (or `blob`) and returns what is stored now."""
        self._step("renew")
        if self.renew_delay_s:
            await asyncio.sleep(self.renew_delay_s)
        self.stored = merge(self.stored, blob) if merge is not None else blob
        self.renewed.append(self.stored)
        return self.stored


# ---- the clock ----------------------------------------------------------------------------------------------------
class Clock:
    """A monotonic clock (`clock()`), its wall time (`now()`, from `NOW`), and a sleep that only moves it."""

    def __init__(self, t: float = 1000.0):
        self.start = self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s

    def now(self) -> datetime:
        return NOW + timedelta(seconds=self.t - self.start)

    async def sleep(self, s: float) -> None:
        self.t += s


# ---- a fake curl_cffi AsyncSession ----------------------------------------------------------------------------------
class Resp:
    """One answer: `cookies` are set on the session as a Set-Cookie of the answering host would (None removes one)."""

    def __init__(self, status: int = 200, text: str = "", *, json_body: object = None,
                 cookies: dict[str, str | None] | None = None, headers: dict | None = None):
        self.status_code = status
        self.text = json.dumps(json_body) if json_body is not None else text
        self.cookies = dict(cookies or {})
        self.headers = dict(headers or {})


class Cookies:
    """curl_cffi's `session.cookies`: `set(name, value, domain, path)`, `get(name)`, and `jar` (cookie objects)."""

    def __init__(self):
        self.jar: list[SimpleNamespace] = []

    def set(self, name, value, domain="", path="/"):
        self.jar = [c for c in self.jar if c.name != name] + [SimpleNamespace(name=name, value=value, domain=domain,
                                                                             path=path)]

    def get(self, name, default=None):
        return next((c.value for c in self.jar if c.name == name), default)


class FakeSocket:
    """The gateway's side of one WebSocket: `incoming` raw frames (a `(bytes, curl flags)` tuple is received as it is,
    as `CLOSE_4003`; an exception in it is raised by that `recv`; once they run out `recv` never returns). It records
    each frame sent and fails the test on a second `0x0d` data frame and on any send after the `0x0d`."""

    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.sent: list[bytes] = []
        self.url: str | None = None
        self.closed = False

    async def send(self, data: bytes) -> None:
        assert 0x0d not in (f[0] for f in self.sent if f), "a frame sent after the 0x0d data frame"
        self.sent.append(bytes(data))

    async def recv(self):
        if not self.incoming:
            await asyncio.Event().wait()
        item = self.incoming.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, tuple) else (item, 2)

    async def close(self) -> None:
        self.closed = True

    def data_frames(self) -> list[bytes]:
        return [f for f in self.sent if f and f[0] == 0x0d]


class Net:
    """The network every fake session of a test shares. `routes`: {(METHOD, url prefix): answer}, the longest prefix
    wins; METHOD "WS" is `ws_connect` (an answer is a FakeSocket). An answer is a `Resp`, an exception (raised), a
    callable of the call, or a list taken in order (the last repeats). No route: 404 (a WebSocket: refused).
    `make_session` is the engine's seam; each session has its own cookie jar."""

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})
        self.calls: list[SimpleNamespace] = []
        self.sessions: list[FakeSession] = []

    def make_session(self, proxies):
        s = FakeSession(self, proxies)
        self.sessions.append(s)
        return s

    def answer(self, method: str, url: str, call):
        keys = [k for k in self.routes if k[0] == method and url.startswith(k[1])]
        if not keys:
            return ConnectionError("no route") if method == "WS" else Resp(404, "not found")
        answer = self.routes[max(keys, key=lambda k: len(k[1]))]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer) and not isinstance(answer, Resp | FakeSocket):
            answer = answer(call)
        return answer

    def methods(self) -> list[str]:
        return [c.method for c in self.calls]


class FakeSession:
    """curl_cffi's `AsyncSession`: `request`/`get`/`post`, `ws_connect`, `cookies`, `close`. Each call records the
    url, the headers, the cookies it carried, `proxies`, `allow_redirects` and which session made it."""

    def __init__(self, net: Net, proxies):
        self.net, self.proxies = net, proxies
        self.cookies = Cookies()
        self.closed = False

    def _record(self, method, url, headers, data, proxies, allow_redirects):
        host = urlsplit(url).hostname or ""
        sent = {c.name: c.value for c in self.cookies.jar
                if host == c.domain.lstrip(".") or host.endswith("." + c.domain.lstrip("."))}
        call = SimpleNamespace(method=method, url=url, headers=dict(headers or {}), data=data, cookies=sent,
                               proxies=proxies, allow_redirects=allow_redirects, session=self)
        self.net.calls.append(call)
        return call

    async def request(self, method, url, *, headers=None, data=None, timeout=None, allow_redirects=True,
                      proxies=None, **kw):
        method = method.upper()
        call = self._record(method, url, headers, data, proxies, allow_redirects)
        answer = self.net.answer(method, url, call)
        if isinstance(answer, BaseException):
            raise answer
        host = urlsplit(url).hostname or ""
        for name, value in answer.cookies.items():
            if value is None:
                self.cookies.jar = [c for c in self.cookies.jar if c.name != name]
            else:
                self.cookies.set(name, value, host)
        return answer

    async def get(self, url, **kw):
        return await self.request("GET", url, **kw)

    async def post(self, url, **kw):
        return await self.request("POST", url, **kw)

    async def ws_connect(self, url, *, headers=None, timeout=None, allow_redirects=True, proxies=None, **kw):
        call = self._record("WS", url, headers, None, proxies, allow_redirects)
        answer = self.net.answer("WS", url, call)
        if isinstance(answer, BaseException):
            raise answer
        answer.url = url
        return answer

    async def close(self):
        self.closed = True
