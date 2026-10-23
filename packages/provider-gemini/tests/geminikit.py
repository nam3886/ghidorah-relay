"""A fake gemini.google.com (an `httpx.MockTransport`, no network) fed gemini's recorded answers from
fixtures/gemini/live-261023, a RunCtx that writes every platform step to the same log as the calls, and a
Cookie-Editor export with planted values."""

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from relay_core.providers import AccountView, CreateRequest, JobView, MediaRef, Proxy
from relay_core.testing.fake_provider import FakeSettings
from relay_gemini.credential import parse_credential
from relay_gemini.spec import SPEC

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "gemini" / "live-261023"
PSID = "g.a000PLANTED-PSID-value_0123456789"            # planted values: never in a log, an error or an event
PSIDTS = "sidts-PLANTED-OLD-0123456789"
NEW_PSIDTS = "sidts-PLANTED-NEW-9876543210"
AT_TOKEN = "AJpMEPLANTED-at:1792737600000"
EMAIL = "owner@example.com"
SENT_AT = datetime(2026, 10, 23, 15, 42, 11, tzinfo=UTC)      # the t2i send of the fixtures started here


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


T2I = fixture("t2i_16x9_reply.txt")               # a complete answer with one 1408x768 image
I2I = fixture("i2i_reply.txt")
NOT_ENTITLED = fixture("t2v_not_entitled_reply.txt")   # "If you upgrade your subscription, ..."
READ = fixture("t2i_read_hNvQHb.txt")              # the chat of the t2i send: its prompt and its /gg/ image
LISTING = fixture("listing_MaZiqc.txt")            # 11 chats of the owner, none of ours
T2I_CHAT = "c_000000000000a000"
T2I_PROMPT = "A red apple on a white table, studio photo"
T2I_LINK = "https://lh3.googleusercontent.com/gg-dl/IMAGE1"
READ_LINK = "https://lh3.googleusercontent.com/gg/IMAGE7"
UPLOADED = "/contrib_service/ttl_1d/REDACTED"


def frames(*payloads, end: bool = True, rpcid=None) -> str:
    """A `)]}'`-framed answer carrying `payloads`, closed by the end item unless `end` is False."""
    out = [")]}'", ""]
    for p in payloads:
        line = json.dumps([["wrb.fr", rpcid, json.dumps(p)]])
        out += [str(len(line)), line]
    if end:
        out += ["25", '[["e",4,null,null,1234]]']
    return "\n".join(out) + "\n"


def answer(text: str, *, cid: str = "c_00000000000000f1", image: bool = False, end: bool = True) -> str:
    """A send's answer: the ids frame, then one content payload with a final `text` (and an image record)."""
    record = [None, 1, "img.png", "https://lh3.googleusercontent.com/gg-dl/NEWIMAGE", None, "$tok", None, None,
              None, [1, 2], None, "image/png", None, None, None, [1024, 1024, 900000]]
    cand = ["rc_00000000000000f3", [text], None, None, None, None, None, None, [1], "en", None, None,
            [None, None, None, None, None, None, None, [[[[None, None, None, record]]]]] if image else None]
    return frames([None, [cid, "r_00000000000000f2"], {"18": "r_00000000000000f2"}],
                  [None, [cid, "r_00000000000000f2"], None, None, [cand]], end=end)


def listing(*chats_) -> str:
    """A MaZiqc answer: (cid, unix start) per chat."""
    rows = [[cid, "title", None, None, None, [int(at), 0]] + [None] * 15 + ["rc_x"] for cid, at in chats_]
    return frames([None, None, rows], rpcid="MaZiqc")


def chat_read(prompt: str, text: str, *, image: bool = True, cid: str = "c_00000000000000f1") -> str:
    record = [None, 1, "img.png", "https://lh3.googleusercontent.com/gg/READIMAGE", None, "$tok", None, None, None,
              [1, 2], None, "image/png", None, None, None, [1024, 1024, 900000]]
    cand = ["rc_00000000000000f3", [text], None, None, None, None, None, None, [2], None, None, None,
            [None, None, None, None, None, None, None, [[[[None, None, None, record]]]]] if image else None]
    turn = [[cid, "r_x"], None, [[prompt], 1], [[cand]]]
    return frames([[turn]], rpcid="hNvQHb")


def page(signed_in: bool = True) -> str:
    """The parts of gemini's page the provider reads (the recorded page is not kept: it held the session)."""
    at = '"SNlM0e":"%s",' % AT_TOKEN if signed_in else ""
    email = '"oPEP7c":"%s",' % EMAIL if signed_in else '"oPEP7c":"",'
    return ('<html><script>window.WIZ_global_data = {%s"FdrFJe":"-1234567890123456789",'
            '"cfb2h":"boq_gemini-web-uiserver_20261022.15_p0",%s"x":1};</script></html>' % (at, email))


def export(**extra) -> list[dict]:
    """A Cookie-Editor export of gemini.google.com: the .google.com session set, one .gemini cookie, one stranger."""
    return [
        {"domain": ".google.com", "name": "__Secure-1PSID", "value": PSID, "path": "/", "hostOnly": False,
         "secure": True, "httpOnly": True, "sameSite": "unspecified", "expirationDate": 1826000000.5, **extra},
        {"domain": ".google.com", "name": "__Secure-1PSIDTS", "value": PSIDTS, "path": "/", "hostOnly": False},
        {"domain": ".google.com", "name": "SIDCC", "value": "sidcc-old", "path": "/", "hostOnly": False},
        {"domain": ".gemini.google.com", "name": "COMPASS", "value": "compass-planted", "path": "/",
         "hostOnly": False},
        {"domain": ".youtube.com", "name": "VISITOR_INFO1_LIVE", "value": "yt", "path": "/", "hostOnly": False},
    ]


BLOB = parse_credential(json.dumps(export()).encode()).blob


def settings(**overrides) -> FakeSettings:
    return FakeSettings(SPEC, **overrides)


def _take(table, key):
    out = table.get(key)
    if isinstance(out, list):
        out = out.pop(0) if len(out) > 1 else out[0]      # the last answer repeats
    return out


class Gemini:
    """A fake gemini.google.com + accounts.google.com + push.clients6 + the lh3 link chain. `send` and `rpc[rpcid]`
    are a response text, an `httpx.Response`, an exception, a callable of the request, or a list of those taken in
    order (the last one repeats). `log` is shared with the ctx."""

    def __init__(self, log: list, *, signed_in=True, page_status=200, rotate=200, send=None, rpc=None,
                 upload_url="https://push.clients6.google.com/upload/?upload_id=u1", upload_body=UPLOADED,
                 chain=None):
        self.log, self.seen = log, []
        self.signed_in, self.page_status, self.rotate = signed_in, page_status, rotate
        self.send, self.rpc = send, dict(rpc or {})
        self.upload_url, self.upload_body = upload_url, upload_body
        # the link chain: host -> (status, content-type, body)
        self.chain = chain if chain is not None else {
            "lh3.googleusercontent.com": (200, "text/plain; charset=UTF-8", "https://lh3.google.com/rd/abc"),
            "lh3.google.com": (200, "text/plain; charset=UTF-8",
                               "https://lh3.googleusercontent.com/rd-gg-dl/BYTES=d-I?alr=yes"),
        }
        self.transport = httpx.MockTransport(self.handle)

    @staticmethod
    def _out(out, request):
        if callable(out):
            out = out(request)
        if isinstance(out, Exception):
            raise out
        assert out is not None, "no answer set for %s" % request.url
        return out if isinstance(out, httpx.Response) else httpx.Response(200, text=out)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = urlsplit(str(request.url))
        if url.netloc == "accounts.google.com" and url.path == "/RotateCookies":
            self.log.append("rotate")
            if isinstance(self.rotate, Exception):
                raise self.rotate
            headers = [("set-cookie", "__Secure-1PSIDTS=%s; Domain=.google.com; Path=/; Secure; HttpOnly" % NEW_PSIDTS),
                       ("set-cookie", "SIDCC=sidcc-rotated; Domain=.google.com; Path=/; Secure")] \
                if self.rotate == 200 else []
            return httpx.Response(self.rotate, headers=headers, text="")
        if url.netloc == "push.clients6.google.com":
            command = request.headers.get("x-goog-upload-command")
            self.log.append("upload:" + command)
            if command == "start":
                return httpx.Response(200, headers={"x-goog-upload-url": self.upload_url} if self.upload_url else {})
            return httpx.Response(200, text=self.upload_body)
        if url.netloc in self.chain or url.netloc == "work.fife.usercontent.google.com":
            self.log.append("link:" + url.netloc)
            status, kind, body = self.chain.get(url.netloc, (404, "text/html", "no"))
            if "rd-gg-dl" in url.path:
                return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")
            return httpx.Response(status, headers={"content-type": kind}, text=body)
        if url.netloc != "gemini.google.com":
            self.log.append("off:" + url.netloc)
            return httpx.Response(404, text="not here")
        if request.method == "GET":
            self.log.append("page")
            return httpx.Response(self.page_status, text=page(self.signed_in), headers=[
                ("set-cookie", "SIDCC=sidcc-from-page; Domain=.google.com; Path=/; Secure")])
        if url.path.endswith("/StreamGenerate"):
            self.log.append("SEND")
            out = self.send.pop(0) if isinstance(self.send, list) and len(self.send) > 1 else (
                self.send[0] if isinstance(self.send, list) else self.send)
            return self._out(out, request)
        rpcid = parse_qs(url.query)["rpcids"][0]
        self.log.append(rpcid)
        return self._out(_take(self.rpc, rpcid), request)

    def sends(self) -> list[httpx.Request]:
        return [r for r in self.seen if r.url.path.endswith("/StreamGenerate")]


def inner_of(request: httpx.Request) -> list:
    """The decoded `inner` of one send."""
    freq = parse_qs(request.content.decode())["f.req"][0]
    return json.loads(json.loads(freq)[1])


def args_of(request: httpx.Request) -> list:
    freq = parse_qs(request.content.decode())["f.req"][0]
    return json.loads(json.loads(freq)[0][0][1])


class Ctx:
    """The RunCtx an engine sees. Every platform step goes to the shared log; `renewed` keeps each written jar."""

    def __init__(self, log: list, *, blob: bytes = BLOB, gen: int = 1, proxy: Proxy | None = None,
                 account_id: str = "acc-1", renew_ok: bool = True):
        self.log = log
        self.account = AccountView(account_id, "acc", blob, gen, proxy, {}, {"image": None}, "pk")
        self.refs: list[tuple[str, dict]] = []
        self.events: list[str] = []
        self.renewed: list[bytes] = []
        self.merges: list = []
        self.stored_as = None
        self.renew_ok = renew_ok
        self.mark_error: BaseException | None = None

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
        if not self.renew_ok:
            return None
        return self.stored_as(blob) if self.stored_as is not None else blob


def job(prompt=T2I_PROMPT, *, ratio=None, images=0, state=None, sent_at=SENT_AT, sent=None, frame_id=1,
        ref=None) -> JobView:
    """A job as the core hands it to the engine: planned by the spec, `sent_text` the verbatim frame render."""
    from relay_core import frames
    from relay_core.providers import MediaIn
    req = CreateRequest(model="gemini-nano-banana-2", prompt=prompt, ratio=ratio,
                        images=tuple(MediaIn("0" * 64, "image/png", 10) for _ in range(images)))
    plan = SPEC.plan(req, settings(), {})
    text = frames.render(SPEC.frames.defaults["image"], prompt, "7100", SPEC.frames.verbatim) if sent is None else sent

    async def inputs():
        return [MediaRef(0, "jobs/j1/input/0.png", "image/png", 10)] if images else []
    return JobView("job-1", "gemini", plan.operation, plan.model, dict(plan.params), prompt, "7100", text, None,
                   sent_at, ref, dict(state or {}), inputs, frame_id)
