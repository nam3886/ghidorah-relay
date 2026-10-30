"""Test kit for muse: an in-process muse.ai (its HTTP site and the account's VM behind a real Noise_XX responder), a
fake run context, settings and the recorded spike captures (fixtures/muse)."""

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from relay_core.providers import MediaRef, Proxy
from relay_muse.engine import noise
from relay_muse.engine.link import Deps
from relay_muse.spec import SPEC

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "muse"
SPIKE = FIXTURES / "spike-261026"
VM_ID = "56336f13-0000-4d09-b2dc-000000000001"
PROXY = Proxy("10.0.0.9", 3128, "user:pw")
SESSION_COOKIE_VALUE = "sess-value-1"
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def spike(name: str) -> dict:
    """A capture of the 26/10 spike by its file name, or any muse fixture by its path under fixtures/muse."""
    return json.loads(((FIXTURES if "/" in name else SPIKE) / name).read_text(encoding="utf-8"))


def _fill(obj, session: str):
    """A recorded event with the redactions made usable again: the VM id, the chat id."""
    text = json.dumps(obj).replace("<vm_id>", VM_ID).replace("<redacted len=36>", session)
    slugs = iter(range(1000))           # each redacted file name gets its own id again
    text = re.sub(r"-<uuid>\.", lambda m: "-f%05d." % next(slugs), text)
    return json.loads(text.replace("<uuid>", "u-1"))


def history(name: str, session: str) -> list[dict]:
    """The final chat events of a recorded job (`job_jNN.json`) or a recorded history answer (`history_*.json`)."""
    data = spike(name)
    events = data["final_history_events"] if "final_history_events" in data else data["result"]["chat_events"]
    return _fill(events, session)


def settings(**over) -> "Settings":
    s = Settings({k: v.default for k, v in SPEC.settings().items()})
    # every wait short; a submit holds 2 s past its margin, so a mutant that never decides still ends
    fast = {"muse.history_poll_s": 0.01, "muse.settle_s": 0.05, "muse.send_ack_timeout_s": 0.3,
            "muse.wake_poll_s": 0, "muse.wake_timeout_s": 0.2, "muse.image_submit_timeout_s": 17,
            "muse.video_submit_timeout_s": 17}
    s.update(fast)
    s.update({"muse." + k: v for k, v in over.items()})
    return s


class Settings(dict):
    def get(self, key, default=None):
        return self[key] if key in self else default


# ---- the run context ----------------------------------------------------------------------------------------------
def jar(*extra: tuple[str, str]) -> bytes:
    cookies = [{"name": "hatch_sess", "value": SESSION_COOKIE_VALUE, "domain": ".muse.ai"},
               {"name": "hatch_vml", "value": "vml-1", "domain": ".muse.ai"},
               *({"name": n, "value": v, "domain": ".muse.ai"} for n, v in extra)]
    return json.dumps({"cookies": cookies}).encode()


class Ctx:
    """RunCtx: records the gates in order; `fail` names a gate that raises."""

    def __init__(self, proxy: Proxy | None = PROXY, credential: bytes | None = None,
                 fail: str | None = None):
        self.account = SimpleNamespace(id="acc-1", credential=credential or jar(), proxy=proxy)
        self.calls: list = []
        self.events: list[tuple[str, str]] = []
        self.renewed: list[bytes] = []
        self.fail = fail
        self.settings = None

    async def report_ref(self, ref, state):
        self.calls.append(("report_ref", ref, dict(state)))
        if self.fail == "report_ref":
            raise RuntimeError("lease lost")

    async def mark_sent(self):
        self.calls.append(("mark_sent",))
        if self.fail == "mark_sent":
            raise RuntimeError("lease lost")

    async def event(self, text, stage, source):
        self.events.append((stage, text))

    async def evidence(self, name, data, mime):
        pass

    async def renew_credential(self, blob, merge=None):
        stored = merge(self.account.credential, blob) if merge else blob
        self.renewed.append(stored)
        return stored


def job(operation="text_to_image", sent_text="a red fox in fresh snow", external_ref=None, state=None,
        inputs=(), pinned=None, **params):
    kind = "image" if operation == "text_to_image" else "video"
    params = {"kind": kind, **({"n": 1} if kind == "image" else {"duration": 5}), **params}

    async def load():
        return list(inputs)
    return SimpleNamespace(id="job-1", provider="muse", operation=operation, model="muse-" + kind, params=params,
                           prompt=sent_text, marker="", sent_text=sent_text, render_since=None, sent_at=None,
                           external_ref=external_ref, provider_state=state or {}, inputs=load, source=pinned)


IMAGE_REF = MediaRef(0, "jobs/job-1/input/0.webp", "image/webp", 6)


# ---- the account's VM: a Noise responder that answers HTTP-over-Noise ---------------------------------------------
class Responder:
    def __init__(self):
        self.st = noise.Symmetric()
        self.s, self.e = X25519PrivateKey.generate(), X25519PrivateKey.generate()

    def read1(self, m: bytes) -> bytes:
        self.re = m[:32]
        self.st.mix_hash(self.re)
        return self.st.decrypt_and_hash(m[32:])

    def message2(self) -> bytes:
        e = noise.public_bytes(self.e)
        self.st.mix_hash(e)
        self.st.mix_key(noise.dh(self.e, self.re))
        out = e + self.st.encrypt_and_hash(noise.public_bytes(self.s))
        self.st.mix_key(noise.dh(self.s, self.re))
        return out + self.st.encrypt_and_hash(b"attestation")

    def read3(self, m: bytes) -> tuple[noise.Cipher, noise.Cipher]:
        rs = self.st.decrypt_and_hash(m[:48])
        self.st.mix_key(noise.dh(self.e, rs))
        self.st.decrypt_and_hash(m[48:])
        first, second = self.st.split()
        return second, first          # (send to the client, receive from it)


def response_payload(sid: int, status: int, body: bytes, end: bool = True) -> bytes:
    resp = noise.field(1, status) + (noise.field(3, body) if body else b"") + noise.field(4, end)
    return noise.field(1, noise.field(1, sid) + noise.field(3, resp))


def body_payload(sid: int, data: bytes, end: bool) -> bytes:
    return noise.field(1, noise.field(1, sid) + noise.field(4, noise.field(1, data) + noise.field(2, end)))


def reset_payload(sid: int, code: int, reason: str) -> bytes:
    return noise.field(1, noise.field(1, sid) + noise.field(5, noise.field(1, code) + noise.field(2, reason)))


class Request(SimpleNamespace):
    def json(self):
        return json.loads(self.body) if self.body else None


class VM:
    """The VM side of one WebSocket. `routes(req)` answers a request: (status, body) where a body that is a dict or
    a list is sent as JSON, "silent" sends nothing, ("reset", reason) resets the stream, ("split", status, bytes) sends
    the answer as a response and body chunks. The subscription stays open; `push(event)` writes to it."""

    def __init__(self, routes):
        import asyncio
        self.routes, self.inbox = routes, asyncio.Queue()
        self.stage, self.hs, self.tx, self.rx = 0, Responder(), None, None
        self.parts: dict[int, dict[int, bytes]] = {}
        self.requests: list[Request] = []
        self.stream_sid = None
        self.closed = False
        self.chunk = 0

    # the client's socket
    async def send(self, data: bytes) -> None:
        if self.stage == 0:
            assert self.hs.read1(data)[:2] == b"\x0a\x20"
            self.stage = 1
            self.inbox.put_nowait(self.hs.message2())
        elif self.stage == 1:
            self.tx, self.rx = self.hs.read3(data)
            self.stage = 2
        elif self.stage == 2:
            assert self.rx.decrypt(b"", data) == b""          # the kick frame
            self.stage = 3
        else:
            self._frame(self.rx.decrypt(b"", data))

    async def recv(self) -> bytes:
        item = await self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True

    def broken(self) -> None:
        self.inbox.put_nowait(ConnectionError("socket closed"))

    def _out(self, payload: bytes) -> None:
        self.chunk += 1
        for frame in noise.chunks(self.chunk, payload):
            self.inbox.put_nowait(self.tx.encrypt(b"", frame))

    def push(self, event: dict) -> None:
        self._out(body_payload(self.stream_sid, json.dumps(event).encode(), False))

    def _frame(self, plaintext: bytes) -> None:
        f = noise.parse(plaintext)
        cid, idx, total = f.get(1, [0])[0], f.get(2, [0])[0], f.get(3, [1])[0]
        pieces = self.parts.setdefault(cid, {})
        pieces[idx] = f.get(4, [b""])[0]
        if len(pieces) < total:
            return
        payload = b"".join(pieces[i] for i in range(total))
        del self.parts[cid]
        sr = noise.parse(payload)
        frame = noise.parse(sr[2][0])
        r = noise.parse(frame[2][0])
        headers = {noise.parse(h)[1][0].decode(): noise.parse(h)[2][0].decode() for h in r.get(3, [])}
        req = Request(service=sr.get(1, [0])[0], sid=frame[1][0], verb=r[1][0].decode(), path=r[2][0].decode(),
                      headers=headers, body=r.get(4, [b""])[0])
        self.requests.append(req)
        if req.path == "/chat/subscribe":
            self.stream_sid = req.sid
        answer = self.routes(req)
        if answer == "silent":
            return
        if answer[0] == "reset":
            self._out(reset_payload(req.sid, 5, answer[1]))
            return
        if answer[0] == "split":
            _, status, data = answer
            self._out(response_payload(req.sid, status, data[:10], end=False))
            self._out(body_payload(req.sid, data[10:], True))
            return
        status, body = answer
        data = json.dumps(body).encode() if isinstance(body, dict | list) else (body or b"")
        self._out(response_payload(req.sid, status, data, end=req.path != "/chat/subscribe"))

    def paths(self) -> list[str]:
        return [r.path for r in self.requests]


def vm_routes(histories=(), *, stream=None, files=None, overrides=None):
    """The VM's answers: the setup calls, `/chat/stream` acknowledged for the chat it names, the job's history from
    `histories` (one per read, the last repeated), the files of `files` (ref -> bytes; others 404), a chat delete."""
    reads = list(histories)
    files = files or {}

    def route(req):
        for prefix, answer in (overrides or {}).items():
            if req.path.startswith(prefix):
                return answer(req) if callable(answer) else answer
        if req.path == "/client/register-capabilities":
            return 200, {"accepted_data_sources": 0, "capabilities": [], "rejected_ids": []}
        if req.path.startswith("/chat/history?limit=1&"):
            return 200, {"result": {"chat_events": [{"seq": 41, "event_name": "message.assistant"}]}}
        if req.path == "/chat/subscribe":
            return 200, b""
        if req.path == "/api/nodes/register":
            return 200, {"node_id": req.json()["node_id"], "status": "registered"}
        if req.path == "/chat/stream":
            if stream is not None:
                return stream(req) if callable(stream) else stream
            return 200, {"channel": "main", "is_thread": True, "message_id": "m-1",
                         "session_id": req.json()["session_id"]}
        if req.path.startswith("/chat/history?"):
            events = reads.pop(0) if len(reads) > 1 else (reads[0] if reads else [])
            if callable(events):
                events = events(req)
            return 200, {"result": {"chat_events": events}}
        if req.path.startswith("/fs/raw/"):
            data = files.get(req.path[len("/fs/raw/"):])
            return (200, data) if data is not None else (404, {"ok": False, "error": {"code": "not_found"}})
        if req.path.startswith("/approvals/"):
            return 200, {"ok": True}
        if req.path == "/session/delete":       # measured 30/10
            return 200, {"ok": True, "result": {"deleted": True, "session_id": req.json()["session_id"]}}
        return 404, {"ok": False, "error": {"code": "not_found", "message": "route not found"}}
    return route


# ---- muse.ai's site ---------------------------------------------------------------------------------------------
class Resp(SimpleNamespace):
    def json(self):
        return json.loads(self.text)


class Cookies:
    def __init__(self):
        self.jar: list[SimpleNamespace] = []

    def set(self, name, value, domain=""):
        self.jar = [c for c in self.jar if c.name != name] + [SimpleNamespace(name=name, value=value, domain=domain)]


SESSION_OK = {"status": "assigned", "vm_id": VM_ID, "endpoint_url": "wss://%s.metaaivm.com/" % VM_ID,
              "vm_state": "RUNNING", "vms": [{"notary_token_expiration_ts": 1793000000}]}


class Site:
    """muse.ai over HTTP: `answers` maps "GET /api/session" (method and path) to (status, body, set_cookies) or a list
    of them (one per call, the last repeated) or a function of the request; an exception is raised instead. The VMs it
    hands out are kept on `vms`; the calls on `calls`."""

    def __init__(self, answers=None, vm_routes_=None, rotate=(("hatch_vml", "vml-2"),)):
        self.answers = {
            "GET /api/session": (200, SESSION_OK, dict(rotate)),
            "POST /api/hatch/token": (200, {"token": "tok-abc", "notary_token": "endorsement.v1.n\n"}, {}),
            "POST /api/hatch/vm/wake": (200, {"ok": True}, {}),
            **(answers or {})}
        self.routes = vm_routes_ or vm_routes()
        self.vms: list[VM] = []
        self.calls: list[SimpleNamespace] = []
        self.made: list = []
        self.cookies_seen: list[dict] = []

    def session(self, proxies):
        """`Deps.make_session`."""
        self.made.append(proxies)
        return _Session(self, proxies)

    def deps(self, **kw) -> Deps:
        return Deps(make_session=self.session, **kw)

    def paths(self) -> list[str]:
        return ["%s %s" % (c.method, c.path) for c in self.calls]


class _Session:
    def __init__(self, site: Site, proxies):
        self.site, self.proxies, self.cookies, self.closed = site, proxies, Cookies(), False

    async def _do(self, method, url, headers=None, data=None, timeout=None, allow_redirects=None):
        assert allow_redirects is False
        if url.startswith("https://api.ipify.org"):
            path = "ipify"
        else:
            assert url.startswith("https://muse.ai/")
            path = url[len("https://muse.ai"):]
        call = SimpleNamespace(method=method.upper(), path=path, headers=dict(headers or {}), data=data)
        self.site.calls.append(call)
        self.site.cookies_seen.append({c.name: c.value for c in self.cookies.jar})
        key = "%s %s" % (call.method, path)
        answer = self.site.answers.get(key)
        if answer is None:
            answer = next((v for k, v in self.site.answers.items() if k.endswith("*") and key.startswith(k[:-1])),
                          (404, {"error": "not found"}, {}))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            answer = answer(call)
        if isinstance(answer, Exception):
            raise answer
        status, body, cookies = answer if len(answer) == 3 else (*answer, {})
        for name, value in cookies.items():
            self.cookies.set(name, value, ".muse.ai")
        return Resp(status_code=status, text=body if isinstance(body, str) else json.dumps(body))

    async def get(self, url, **kw):
        return await self._do("get", url, **kw)

    async def post(self, url, **kw):
        return await self._do("post", url, **kw)

    async def ws_connect(self, url, headers=None, timeout=None, proxies=None, impersonate=None):
        self.site.calls.append(SimpleNamespace(method="WS", path=url, headers=dict(headers or {}), data=None,
                                               proxies=proxies))
        answer = self.site.answers.get("WS")
        if isinstance(answer, Exception):
            raise answer
        vm = VM(self.site.routes)
        self.site.vms.append(vm)
        return SimpleNamespace(send=vm.send, recv=_recv_pair(vm), close=vm.close, vm=vm)

    async def close(self):
        self.closed = True


def _recv_pair(vm):
    async def recv():
        return await vm.recv(), 2
    return recv


def stream_bodies(site: Site) -> list[dict]:
    return [r.json() for vm in site.vms for r in vm.requests if r.path == "/chat/stream"]


def deep(obj):
    return copy.deepcopy(obj)
