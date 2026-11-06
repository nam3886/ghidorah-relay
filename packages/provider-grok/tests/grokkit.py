"""Test kit for grok: planted secrets, a Cookie-Editor export of grok.com, the settings, planned jobs, a fake curl_cffi
`AsyncSession` (and its WebSocket) fed from a route table, a recording `RunCtx`, a fake clock and the 06/11 captures
(fixtures/grok/spike-261106, read at call time, so a test that needs none runs before they exist).

Every platform step the ctx takes and every request or WebSocket frame a fake sends go, in order, into one shared
`calls` list (pass `ctx.calls` to the fakes), so a test reads the whole run as one sequence; `names(calls)` turns it
into short names ("report_ref", "mark_sent", "item.create", "POST /rest/app-chat/conversations/new", ...). Planted
values all hold `PLANTED`: `assert_no_secret` proves none of them reached a text the run produced."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from relay_core.providers import AccountView, CreateRequest, JobView, MediaIn, MediaRef, Proxy
from relay_core.testing.fake_provider import FakeSettings
from relay_grok.catalog import IMAGE_MODEL, VIDEO_MODEL
from relay_grok.credential import parse_credential
from relay_grok.spec import SPEC

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "grok" / "spike-261106"
NOW = datetime(2026, 11, 6, 5, 0, tzinfo=UTC)

# ---- planted secrets: never in a log, an error, an event, a state, evidence or a result ------------------------------
SSO = "sso-PLANTED-eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
SSO_RW = "sso-rw-PLANTED-eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
USER_ID = "uid-PLANTED-0000-4000-8000-000000000001"
EMAIL = "planted@PLANTED.invalid"
PROXY = Proxy("10.0.0.9", 3128, "user:PLANTED-pw")
PLANTED: tuple[str, ...] = (SSO, SSO_RW, USER_ID, EMAIL, PROXY.auth)
_PLANTED_NAMES = ("SSO", "SSO_RW", "USER_ID", "EMAIL", "PROXY.auth")


def assert_no_secret(*texts: object) -> None:
    """No planted value (nor any other `PLANTED` piece) in any of `texts` (each read as `str`). The failure names which
    planted value and which text, never the text itself (it would print the leak again)."""
    for at, text in enumerate(texts):
        text = str(text)
        for name, piece in zip(_PLANTED_NAMES, PLANTED, strict=True):
            assert piece not in text, "planted %s leaked in text #%d" % (name, at)
        assert "PLANTED" not in text, "a PLANTED value leaked in text #%d" % at


def fixture(name: str) -> dict | str:
    """A capture of the 06/11 spike or step-0 probe by its file name (fixtures/grok/spike-261106/): `<stem>` or
    `<stem>.json` is read as JSON, `<stem>.html` as text."""
    if name.endswith(".html"):
        return (FIX / name).read_text(encoding="utf-8")
    return json.loads((FIX / (name.removesuffix(".json") + ".json")).read_text(encoding="utf-8"))


# ---- the account -----------------------------------------------------------------------------------------------------
def export() -> list[dict]:
    """A Cookie-Editor export of grok.com: sso, sso-rw, x-userid, a Cloudflare cookie, and two strangers."""
    def cookie(domain, name, value, host_only=False, **more):
        return dict(domain=domain, name=name, value=value, path="/", hostOnly=host_only, **more)
    signed = dict(secure=True, httpOnly=True, expirationDate=1800000000.5)
    return [cookie(".grok.com", "sso", SSO, **signed), cookie(".grok.com", "sso-rw", SSO_RW, **signed),
            cookie("grok.com", "x-userid", USER_ID, True), cookie(".grok.com", "__cf_bm", "cf-bm-value"),
            cookie(".x.ai", "xai-stranger", "stranger-xai"), cookie(".google.com", "NID", "stranger-google")]


BLOB = parse_credential(json.dumps(export()).encode()).blob


def settings(**over) -> FakeSettings:
    """The spec's defaults (and the core's), with `grok.<key>` overridden by `key=value`; a text to image goes over the
    fake curl socket (`http`) unless `t2i_transport="browser"` (test_grok_browser.py drives the page)."""
    over.setdefault("t2i_transport", "http")
    return FakeSettings(SPEC, **{"grok__" + k: v for k, v in over.items()})


# ---- jobs ------------------------------------------------------------------------------------------------------------
_OPS = {"t2i": "text_to_image", "edit": "image_to_image", "t2v": "text_to_video"}


def input_ref(i: int = 0) -> MediaRef:
    """An input image of an edit, as the core hands it to the engine."""
    return MediaRef(i, "inputs/%d.png" % i, "image/png", 9)


def job(op: str = "t2i", *, prompt: str = "a red paper boat on a lake at dawn", n: int = 1, ratio: str | None = None,
        inputs=(), state: dict | None = None, sent_at: datetime | None = None,
        external_ref: str | None = None) -> JobView:
    """A job as `SPEC.plan` makes it. `op`: t2i, edit or t2v (or the operation's own name). An edit with no `inputs`
    gets one (`input_ref(0)`); `inputs` are the MediaRefs `job.inputs()` returns."""
    operation = _OPS.get(op, op)
    refs = list(inputs) or ([input_ref(0)] if operation == "image_to_image" else [])
    images = tuple(MediaIn("%064x" % i, "image/png", 9) for i in range(len(refs)))
    plan = SPEC.plan(CreateRequest(model=VIDEO_MODEL if operation == "text_to_video" else IMAGE_MODEL, prompt=prompt,
                                   n=None if operation != "text_to_image" else n, ratio=ratio, images=images),
                     settings(), {})
    assert plan.operation == operation, "the kit planned %s, not %s" % (plan.operation, operation)

    async def load():
        return list(refs)
    return JobView("job-1", "grok", plan.operation, plan.model, plan.params, prompt, "7100", prompt, None, sent_at,
                   external_ref, state or {}, load)


# ---- the run context -------------------------------------------------------------------------------------------------
class Ctx:
    """The RunCtx an engine sees. Each platform step goes, in order, into `calls`: ("report_ref", ref, state),
    ("mark_sent",), ("event", text, stage), ("evidence", name, mime), ("renew",), ("fence",). `refs`, `events` and
    `evidences` keep what was written. `fail` names a step that raises (a lost lease)."""

    def __init__(self, *, proxy: Proxy | None = PROXY, blob: bytes | None = None, state: dict | None = None,
                 account_id: str = "acc-1", fail: str | None = None, settings_=None, calls: list | None = None):
        self.account = AccountView(account_id, "grok-1", BLOB if blob is None else blob, 1, proxy, state or {},
                                   {"image": None, "video": None}, "pk")
        self.browser_token = None
        self.settings = settings_ if settings_ is not None else settings()
        self.calls: list[tuple] = calls if calls is not None else []
        self.refs: list[tuple[str, dict]] = []
        self.events: list[tuple[str, str, str]] = []
        self.evidences: list[tuple[str, bytes, str]] = []
        self.fail = fail

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
        """grok writes nothing back: a call is recorded so a test can prove it never happens."""
        self._step("renew")
        return self.account.credential


def names(calls: list[tuple]) -> list[str]:
    """`calls` as short names: a ctx step by its name, a request as "METHOD /path", a WebSocket open as "ws open", the
    generating frame as "item.create", the reset frame as "reset", any other frame as "ws <type>"."""
    out = []
    for call in calls:
        if call[0] == "http":
            out.append("%s %s" % (call[1], urlsplit(call[2]).path))
        elif call[0] == "ws_open":
            out.append("ws open")
        elif call[0] == "ws_send":
            frame = call[1]
            out.append("item.create" if FakeSocket.is_generation(frame) else "reset" if FakeSocket.is_reset(frame)
                       else "ws %s" % (frame.get("type") if isinstance(frame, dict) else "?"))
        else:
            out.append(call[0])
    return out


# ---- the clock -------------------------------------------------------------------------------------------------------
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


# ---- a fake curl_cffi AsyncSession -----------------------------------------------------------------------------------
class Cut(Exception):
    """The peer broke the stream (what a dropped connection raises in a test)."""


class Resp:
    """One answer. `text` (or `json_body`) is the body; `lines` makes it a stream (`aiter_lines` yields them as bytes).
    A stream is cut with `error` (default `Cut`) after `cut` lines, or hangs forever after them with `hang=True`.
    `cookies` are set on the session as a Set-Cookie of the answering host would (None expires one); `content` is the
    bytes `aiter_content` yields (a media file)."""

    def __init__(self, status: int = 200, headers: dict | None = None, text: str = "", lines: list[str] | None = None,
                 *, json_body: object = None, cut: int | None = None, hang: bool = False,
                 error: BaseException | None = None, cookies: dict[str, str | None] | None = None,
                 content: bytes | None = None):
        self.status_code = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        if json_body is not None:
            self.text = json.dumps(json_body)
            self.headers.setdefault("content-type", "application/json")
        else:
            self.text = text
        self.lines = lines
        self.cut, self.hang, self.error = cut, hang, error
        self.cookies = dict(cookies or {})
        self.content = content if content is not None else self.text.encode()
        self.closed = False

    def json(self):
        return json.loads(self.text)

    async def _end(self) -> None:
        if self.hang:
            await asyncio.Event().wait()
        if self.cut is not None:
            raise self.error or Cut("stream cut")

    async def aiter_lines(self):
        lines = self.lines if self.lines is not None else self.text.splitlines()
        for line in lines if self.cut is None else lines[:self.cut]:
            yield line.encode() if isinstance(line, str) else line
        await self._end()

    async def aiter_content(self, chunk_size=None):
        yield self.content
        await self._end()

    async def aclose(self):
        self.closed = True


class Cookies:
    """curl_cffi's `session.cookies`: `set(name, value, domain, path)`, `get(name)`, and `jar` (cookie objects)."""

    def __init__(self):
        self.jar: list[SimpleNamespace] = []

    def set(self, name, value, domain="", path="/"):
        self.jar = [c for c in self.jar if c.name != name] + [SimpleNamespace(name=name, value=value, domain=domain,
                                                                             path=path)]

    def get(self, name, default=None):
        return next((c.value for c in self.jar if c.name == name), default)


def _sent_by_jar(cookie, host: str) -> bool:
    """Would curl send this jar cookie to `host`? `.grok.com` goes to grok.com and its subdomains, a host-only
    `grok.com` (or no domain, which this fake reads as host-only grok.com) to grok.com alone."""
    domain = (cookie.domain or "grok.com").lower()
    if domain.startswith("."):
        return host == domain[1:] or host.endswith(domain)
    return host == domain


class FakeSession:
    """A curl_cffi `AsyncSession` stand-in answering from `routes`: `{(METHOD, prefix): answer}`, METHOD "WS" for
    `ws_connect`. The prefix is matched against the URL's path, `host + path` and the whole URL; the longest prefix
    wins. An answer is a `Resp` (a `FakeSocket` for "WS"), an exception (raised), a callable of the call, or a list
    taken in order (the last repeats). No route: 404 (a WebSocket: ConnectionError).

    The instance is the engine's `make_session` seam too: `FakeSession(...)(proxies)` records `proxies` and returns
    itself (one jar for the test). Every request is a `Call` in `requests` (method, url, host, path, headers, data,
    json, kw, cookies sent, `has_cookie`, `proxies`, `allow_redirects`, `stream`, `timeout`) and ("http", METHOD, url)
    in `calls`; a WebSocket open is ("ws_open", url) and its socket logs into the same `calls`."""

    def __init__(self, routes: dict | None = None, calls: list | None = None):
        self.routes = dict(routes or {})
        self.calls = calls if calls is not None else []
        self.requests: list[SimpleNamespace] = []
        self.cookies = Cookies()
        self.proxies = None
        self.made: list = []
        self.closed = False

    def __call__(self, proxies=None, **kw):
        self.proxies = proxies
        self.made.append(proxies)
        return self

    def _route(self, method: str, url: str, call):
        parts = urlsplit(url)
        forms = (parts.path, (parts.hostname or "") + parts.path, url)
        keys = [k for k in self.routes if k[0] == method and any(f.startswith(k[1]) for f in forms)]
        if not keys:
            return ConnectionError("no route") if method == "WS" else Resp(404, text='{"detail":"not found"}')
        answer = self.routes[max(keys, key=lambda k: len(k[1]))]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer) and not isinstance(answer, Resp | FakeSocket):
            answer = answer(call)
        return answer

    def _record(self, method, url, *, headers=None, data=None, json=None, proxies=None, allow_redirects=True,
                stream=False, timeout=None, cookies=None, **kw):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        sent = {c.name: c.value for c in self.cookies.jar if _sent_by_jar(c, host)}
        sent.update(cookies or {})
        heads = dict(headers or {})
        has_cookie = bool(sent) or any(k.lower() == "cookie" for k in heads)
        call = SimpleNamespace(method=method, url=url, host=host, path=parts.path, headers=heads, data=data, json=json,
                               kw=kw, cookies=sent, has_cookie=has_cookie,
                               proxies=proxies if proxies is not None else self.proxies,
                               allow_redirects=allow_redirects, stream=stream, timeout=timeout)
        self.requests.append(call)
        return call

    async def request(self, method, url, **kw):
        method = method.upper()
        call = self._record(method, url, **kw)
        self.calls.append(("http", method, url))
        answer = self._route(method, url, call)
        if isinstance(answer, BaseException):
            raise answer
        for name, value in answer.cookies.items():
            if value is None:       # Set-Cookie with Max-Age=0: gone from the jar, not kept as ""
                self.cookies.jar = [c for c in self.cookies.jar if c.name != name]
            else:
                self.cookies.set(name, value, "." + call.host if call.host.endswith("grok.com") else call.host)
        return answer

    async def get(self, url, **kw):
        return await self.request("GET", url, **kw)

    async def post(self, url, **kw):
        return await self.request("POST", url, **kw)

    def stream(self, method, url, **kw):
        """`async with session.stream(...) as resp`: the request with `stream=True`."""
        session = self

        class _Stream:
            async def __aenter__(self):
                self.resp = await session.request(method, url, stream=True, **kw)
                return self.resp

            async def __aexit__(self, *exc):
                await self.resp.aclose()
        return _Stream()

    async def ws_connect(self, url, **kw):
        call = self._record("WS", url, **kw)
        self.calls.append(("ws_open", url))
        answer = self._route("WS", url, call)
        if isinstance(answer, BaseException):
            raise answer
        answer.url = url
        if answer.calls is None:
            answer.calls = self.calls
        return answer

    async def close(self):
        self.closed = True


class FakeSocket:
    """grok's side of the Imagine WebSocket (curl_cffi's `AsyncWebSocket`): `incoming` frames in order (a dict is sent
    as its JSON, a str or bytes as is, an exception is raised by that `recv`). Once they run out `recv` raises `then`,
    or never returns. Every frame the engine sends is parsed and logged as ("ws_send", frame) in `calls` and kept in
    `sent`; the test fails on a second generating frame (`conversation.item.create` with an `input_text`) and on any
    frame sent after the generating one."""

    def __init__(self, incoming=(), *, then: BaseException | None = None, calls: list | None = None):
        self.incoming = list(incoming)
        self.then = then
        self.calls = calls
        self.sent: list = []
        self.url: str | None = None
        self.closed = False

    @staticmethod
    def _content(frame) -> list:
        if not isinstance(frame, dict) or frame.get("type") != "conversation.item.create":
            return []
        item = frame.get("item")
        content = item.get("content") if isinstance(item, dict) else None
        return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []

    @classmethod
    def is_generation(cls, frame) -> bool:
        return any(c.get("type") == "input_text" for c in cls._content(frame))

    @classmethod
    def is_reset(cls, frame) -> bool:
        return any(c.get("type") == "reset" for c in cls._content(frame))

    def generations(self) -> list[dict]:
        return [f for f in self.sent if self.is_generation(f)]

    def _record(self, frame) -> None:
        if isinstance(frame, bytes | bytearray):
            frame = frame.decode("utf-8", errors="replace")
        if isinstance(frame, str):
            try:
                frame = json.loads(frame)
            except ValueError:
                pass
        assert not (self.generations() and self.is_generation(frame)), "a second item.create was sent for one job"
        assert not self.generations(), "a frame was sent after the item.create"
        self.sent.append(frame)
        if self.calls is not None:
            self.calls.append(("ws_send", frame))

    async def send(self, data, flags=None) -> None:
        self._record(data)

    async def send_str(self, data: str) -> None:
        self._record(data)

    async def send_bytes(self, data: bytes) -> None:
        self._record(data)

    async def send_json(self, obj) -> None:
        self._record(json.dumps(obj))

    async def recv(self):
        if not self.incoming:
            if self.then is not None:
                raise self.then
            await asyncio.Event().wait()
        item = self.incoming.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict | list):
            item = json.dumps(item)
        return (item.encode() if isinstance(item, str) else item), 1

    async def recv_str(self) -> str:
        data, _flags = await self.recv()
        return data.decode("utf-8", errors="replace")

    async def recv_json(self):
        return json.loads(await self.recv_str())

    async def close(self, *args) -> None:
        self.closed = True
