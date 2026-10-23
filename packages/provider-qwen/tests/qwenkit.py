"""A fake chat.qwen.ai (an `httpx.MockTransport`, no network) fed qwen's recorded answers from fixtures/qwen, a RunCtx
that writes every platform step to the same log as the calls, a Cookie-Editor export with planted values and a fake
mint."""

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from relay_core.providers import AccountView, CreateRequest, JobView, MediaIn, MediaRef, Proxy
from relay_core.testing.fake_provider import FakeSettings
from relay_qwen.credential import Cred, cred_of, parse_credential
from relay_qwen.engine.mint import Minter
from relay_qwen.spec import SPEC

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "qwen" / "spike-261023"
REFRESH = "rt-PLANTED-refresh-0123456789"           # planted values: never in a log, an error, an event or a result
ACCESS = "at-PLANTED-access-0123456789"
BX_UA = "231!PLANTED-bx-ua-0123456789"
UMID = "T2gA-PLANTED-umid-0123456789"
STS_SECRET = "sts-PLANTED-secret-0123456789"
STS_TOKEN = "CAIS-PLANTED-token-0123456789"
EMAIL = "owner@example.com"
CHAT = "c0ffee00-0000-4000-8000-000000000001"
FILE_ID = "f11e0000-0000-4000-8000-000000000002"
NOW = 1_792_740_000.0                               # the wall clock of the runs


def rec(name: str) -> dict:
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


def body_of(name: str) -> str:
    """A recorded call's answer as qwen sent it (a JSON body or the stream's lines)."""
    r = rec(name)
    if "stream_lines" in r:
        return "\n\n".join(line for _, line in r["stream_lines"]) + "\n\n"
    return json.dumps(r["body"])


IMAGE_STREAM = body_of("t2i_send")             # 1664x928 PNG link in an image_gen delta, then `finished`
VIDEO_TASK = body_of("t2v_send")               # the t2v answer: wanx task, 30 credits deducted
I2V_TASK = body_of("i2v_send")
NO_CREDITS = body_of("t2v_no_credits")         # "Insufficient Credits", quota_limit, no wanx
WAF_REC = rec("waf_plain_no_bx")               # the Aliyun WAF edge block: FAIL_SYS_USER_VALIDATE


def waf_block(*, drop: tuple[str, ...] = (), add: dict | None = None, cookies=("x5secdata", "x5sectag"), status=200,
              body: dict | None = None) -> httpx.Response:
    """The WAF's edge block as recorded (status, its headers, its cookies, its body); a test changes one part."""
    headers = [(k, v) for k, v in WAF_REC["headers"].items()
               if k.lower() not in ("content-length", "date") and k.lower() not in drop]
    headers += list((add or {}).items()) + [("set-cookie", "%s=planted; Path=/" % c) for c in cookies]
    return httpx.Response(status, headers=headers, content=json.dumps(body or WAF_REC["body"]).encode())


def WAF(request) -> httpx.Response:     # noqa: N802 - a route answer: a fresh recorded block per call
    return waf_block()
CHAT_EMPTY = rec("chat_read_empty")["body"]
CHAT_IMAGE = rec("t2i_chat_read")["body"]
CHAT_VIDEO = rec("t2v_chat_read")["body"]
CHAT_DROPPED = rec("t2i_dropped_chat_read")["body"]
TASK_RUNNING = rec("t2v_task_running")["body"]
TASK_SUCCESS = rec("t2v_task_success")["body"]
VERIFY_OK = rec("credits_verify_ok")["body"]
VERIFY_EMPTY = rec("credits_verify_empty")["body"]
CREDITS = rec("credits_history_acc1")["body"]  # totalCredits 10
IMAGE_LINK = json.loads(rec("t2i_send")["stream_lines"][-2][1][5:])["choices"][0]["delta"]["content"]
VIDEO_LINK = TASK_SUCCESS["data"]["content"]
TASK = TASK_SUCCESS["data"]["tool_result"]["record_id"]


def refresh_ok() -> dict:
    body = json.loads(json.dumps(rec("auth_refresh")["body"]))
    body["data"].update(access_token=ACCESS, refresh_token=REFRESH, email=EMAIL)
    return body


def sts_ok() -> dict:
    return {"success": True, "data": {
        "access_key_id": "STS.PLANTEDKEYID", "access_key_secret": STS_SECRET, "security_token": STS_TOKEN,
        "file_url": "https://cdn.qwenlm.ai/u1/%s_input.jpg?key=k" % FILE_ID,
        "file_path": "u1/%s_input.jpg" % FILE_ID, "file_id": FILE_ID, "bucketname": "qwen-webui-prod",
        "region": "oss-ap-southeast-1", "endpoint": "oss-accelerate.aliyuncs.com"}}


def export() -> list[dict]:
    """A Cookie-Editor export of chat.qwen.ai: the .qwen.ai session, one host-only cookie, one stranger."""
    return [
        {"domain": ".qwen.ai", "name": "refresh_token", "value": REFRESH, "path": "/", "hostOnly": False,
         "secure": True, "httpOnly": True, "sameSite": "lax", "expirationDate": 1795000000.5},
        {"domain": "chat.qwen.ai", "name": "ssxmod_itna", "value": "planted-itna", "path": "/", "hostOnly": True},
        {"domain": ".aliyun.com", "name": "cna", "value": "stranger", "path": "/", "hostOnly": False},
    ]


BLOB = parse_credential(json.dumps(export()).encode()).blob
PAIR = {"bx-ua": BX_UA, "bx-umidtoken": UMID, "bx-v": "2.5.37", "user-agent": "Mozilla/5.0 PLANTED-browser",
        "sec-ch-ua": '"Chromium";v="154"', "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Linux"',
        "version": "0.3.22"}


def minted_blob(minted_at: float = NOW - 60, bx: dict | None = None) -> bytes:
    """The jar with a pair minted at `minted_at` (a minute before the runs: fresh)."""
    cred = cred_of(BLOB)
    return Cred({**cred.cookies, ("chat.qwen.ai", "/", "cna"): "browser-cna"}, dict(bx or PAIR), minted_at).blob()


def settings(**overrides) -> FakeSettings:
    return FakeSettings(SPEC, **overrides)


class Qwen:
    """A fake qwen. Each route answer is a dict (JSON 200), an `httpx.Response`, a str (200 text), an exception, a
    callable of the request, or a list taken in order (the last repeats). `log` is shared with the ctx."""

    def __init__(self, log: list, **answers):
        self.log, self.seen = log, []
        self.answers = {"refresh": refresh_ok(), "new_chat": {"success": True, "data": {"id": CHAT}},
                        "verify": VERIFY_OK, "sts": sts_ok(), "put": httpx.Response(200), "send": IMAGE_STREAM,
                        "chat": CHAT_EMPTY, "task": TASK_RUNNING, "credits": CREDITS, **answers}
        self.transport = httpx.MockTransport(self.handle)

    def name_of(self, request: httpx.Request) -> str:
        url = urlsplit(str(request.url))
        if url.netloc == "auth.qwen.ai" and url.path == "/api/v2/auths/refresh":
            return "refresh"
        if url.netloc == "sg-entitlement.qwen.com":
            return "credits"
        if url.netloc.endswith(".aliyuncs.com") and request.method == "PUT":
            return "put"
        assert url.netloc == "chat.qwen.ai", "a call off qwen: %s" % url.netloc
        return {"/api/v2/chats/new": "new_chat", "/api/v2/entitlement/credits/verify": "verify",
                "/api/v2/files/getstsToken": "sts", "/api/v2/chat/completions": "send"}.get(url.path) or (
            "chat" if url.path.startswith("/api/v2/chats/") else
            "task" if url.path.startswith("/api/v2/task/status/") else "unknown:" + url.path)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        name = self.name_of(request)
        self.log.append(name)
        out = self.answers.get(name)
        if isinstance(out, list):
            out = out.pop(0) if len(out) > 1 else out[0]
        if callable(out) and not isinstance(out, httpx.Response):
            out = out(request)
        if isinstance(out, Exception):
            raise out
        assert out is not None, "no answer set for %s" % name
        if isinstance(out, httpx.Response):
            return out
        if isinstance(out, dict):
            return httpx.Response(200, json=out)
        return httpx.Response(200, text=out)

    def calls(self, name: str) -> list[httpx.Request]:
        return [r for r in self.seen if self.name_of(r) == name]


class CutStream(httpx.AsyncByteStream):
    """A response body that sends `text` and then breaks (a peer that cut the stream)."""

    def __init__(self, text: str):
        self.text = text

    async def __aiter__(self):
        yield self.text.encode()
        raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")


def cut_after(text: str) -> httpx.Response:
    return httpx.Response(200, stream=CutStream(text), headers={"content-type": "text/event-stream"})


class Ctx:
    """The RunCtx an engine sees. Every platform step goes to the shared log; `renewed` keeps each written
    credential."""

    def __init__(self, log: list, *, blob: bytes | None = None, gen: int = 1, proxy: Proxy | None = None,
                 account_id: str = "acc-1"):
        self.log = log
        self.account = AccountView(account_id, "acc", minted_blob() if blob is None else blob, gen, proxy, {},
                                   {"video": None, "image": None}, "pk")
        self.refs: list[tuple[str, dict]] = []
        self.events: list[str] = []
        self.renewed: list[bytes] = []
        self.merges: list = []
        self.mark_error: BaseException | None = None
        self.stored_as = None       # a test playing a write that lost to another run: what the core stored instead

    async def report_ref(self, ref, state):
        self.log.append("report_ref")
        self.refs.append((ref, dict(state)))

    async def mark_sent(self):
        self.log.append("mark_sent")
        if self.mark_error is not None:
            raise self.mark_error

    async def event(self, text, stage, source):
        self.events.append(text)

    async def renew_credential(self, blob: bytes, merge=None) -> bytes | None:
        self.log.append("renew")
        self.renewed.append(blob)
        self.merges.append(merge)
        return self.stored_as(blob) if self.stored_as is not None else blob


class FakeMint:
    """The browser mint, played: it returns the jar with a new pair (or raises), and logs `mint`."""

    def __init__(self, log: list, *, fail: BaseException | None = None, bx: dict | None = None):
        self.log, self.fail, self.bx, self.calls = log, fail, bx or PAIR, 0

    async def __call__(self, account, cred, token):
        self.log.append("mint")
        self.calls += 1
        assert token is not None and not token.released, "a mint ran without a browser token"
        if self.fail is not None:
            raise self.fail
        return Cred({**cred.cookies, ("chat.qwen.ai", "/", "cna"): "minted-cna"}, dict(self.bx), 0.0)


def minter(log: list, **kw) -> Minter:
    return Minter(FakeMint(log, **kw), clock=lambda: NOW)


def job(model="qwen-image-3.0", prompt="a red paper boat", *, ratio=None, duration=None, image=False, state=None,
        ref=None, sent_at=None, frame_id=1, sent=None):
    images = (MediaIn("0" * 64, "image/jpeg", 9),) if image else ()
    plan = SPEC.plan(CreateRequest(model=model, prompt=prompt, ratio=ratio, duration=duration, images=images),
                     settings(), {})

    async def inputs():
        return [MediaRef(0, "inputs/one.jpg", "image/jpeg", 9)] if image else []
    return JobView("job-1", "qwen", plan.operation, plan.model, plan.params, prompt, "7100",
                   prompt if sent is None else sent, None, sent_at, ref, state or {}, inputs, frame_id=frame_id)


async def read_media(ref) -> bytes:
    assert ref.object_key == "inputs/one.jpg"
    return b"\xff\xd8\xffJPEG-bytes"
