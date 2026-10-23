"""A fake flow.google.com (an `httpx.MockTransport`, no network) fed flow's recorded answers from fixtures/flow, a
RunCtx that writes every platform step to the same log as the calls, and a Cookie-Editor export with planted values."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from relay_core.providers import AccountView, Proxy
from relay_core.testing.fake_provider import FakeSettings
from relay_flow.credential import parse_credential
from relay_flow.spec import SPEC

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "flow"
PROJECT = "33333333-3333-4333-8333-333333333333"      # the redacted project id of the fixtures
PSID = "g.a000PLANTED-PSID-value_0123456789"            # planted values: never in a log, an error or an event
PSIDTS = "sidts-PLANTED-OLD-0123456789"
NEW_PSIDTS = "sidts-PLANTED-NEW-9876543210"
AT_TOKEN = "AJpMEPLANTED-at:1792737600000"
EMAIL = "owner@example.com"


def live(name: str) -> dict:
    return json.loads((FIX / "live-261023" / (name + ".json")).read_text(encoding="utf-8"))


def frame(rpcid: str, payload) -> str:
    """A `)]}'`-framed batchexecute answer carrying `payload` for `rpcid` (the fixtures keep the decoded payload)."""
    item = json.dumps([["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"], ["di", 163]])
    return ")]}'\n\n%d\n%s\n25\n[[\"e\",4,null,null,228]]\n" % (len(item), item)


GENERATED = live("img2_011_generate")                   # the accepted image: 16.6 s held reply, payload with the link
REFUSED_7 = live("img1_generate_refused")["body_raw"]   # PUBLIC_ERROR_UNUSUAL_ACTIVITY, 0.42 s
REFUSED_8 = live("img6_generate_refused")["body_raw"]   # ..._TOO_MUCH_TRAFFIC, 0.43 s
BAD_ARG = live("read_listing_bad_arg")["body_raw"]      # an error envelope [3]
LISTING = live("img8_listing_1min_later")               # lists img2's batch (723245f9-...) and img8's
GET_MEDIA = live("read_getmedia_as29s")
IMG2_BATCH = live("img2_005_written_ahead")["batch"]
IMG2_MEDIA = "1d61f6cc-61ab-496e-bf15-556baec3ce21"


def page(signed_in: bool = True) -> str:
    """The parts of flow's Angular page the provider reads (the recorded page is not kept: it held the session)."""
    at = '"SNlM0e":"%s",' % AT_TOKEN if signed_in else ""
    email = '"oPEP7c":"%s",' % EMAIL if signed_in else '"oPEP7c":"",'
    return ('<html><script>window.WIZ_global_data = {%s"FdrFJe":"-1234567890123456789",'
            '"cfb2h":"boq_labs-ai-sandbox-frontend_20261021.10_p0",%s"x":1};</script></html>' % (at, email))


def export(**extra) -> list[dict]:
    """A Cookie-Editor export of flow.google.com: the .google.com session set, the host-only OSID, one stranger."""
    return [
        {"domain": ".google.com", "name": "__Secure-1PSID", "value": PSID, "path": "/", "hostOnly": False,
         "secure": True, "httpOnly": True, "sameSite": "unspecified", "expirationDate": 1826000000.5, **extra},
        {"domain": ".google.com", "name": "__Secure-1PSIDTS", "value": PSIDTS, "path": "/", "hostOnly": False},
        {"domain": ".google.com", "name": "SIDCC", "value": "sidcc-old", "path": "/", "hostOnly": False},
        {"domain": "flow.google.com", "name": "OSID", "value": "osid-planted", "path": "/", "hostOnly": True},
        {"domain": ".youtube.com", "name": "VISITOR_INFO1_LIVE", "value": "yt", "path": "/", "hostOnly": False},
    ]


BLOB = parse_credential(json.dumps(export()).encode()).blob


def settings(**overrides) -> FakeSettings:
    return FakeSettings(SPEC, **overrides)


class Flow:
    """A fake flow.google.com + accounts.google.com. `rpc[rpcid]` is a response text, an exception, a callable of the
    request, or a list of those taken in order (the last one repeats). `log` is shared with the ctx."""

    def __init__(self, log: list, *, signed_in=True, page_status=200, rotate=200, rpc=None):
        self.log, self.seen = log, []
        self.signed_in, self.page_status, self.rotate = signed_in, page_status, rotate
        self.rpc = dict(rpc or {})
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = urlsplit(str(request.url))
        if url.netloc == "accounts.google.com" and url.path == "/RotateCookies":
            self.log.append("rotate")
            out = self.rotate
            if isinstance(out, Exception):
                raise out
            headers = [("set-cookie", "__Secure-1PSIDTS=%s; Domain=.google.com; Path=/; Secure; HttpOnly" % NEW_PSIDTS),
                       ("set-cookie", "SIDCC=sidcc-rotated; Domain=.google.com; Path=/; Secure")] if out == 200 else []
            return httpx.Response(out, headers=headers, text="")
        assert url.netloc == "flow.google.com", "a call off flow: %s" % url.netloc
        if request.method == "GET":
            self.log.append("page")
            return httpx.Response(self.page_status, text=page(self.signed_in), headers=[
                ("set-cookie", "SIDCC=sidcc-from-page; Domain=.google.com; Path=/; Secure")])
        rpcid = parse_qs(url.query)["rpcids"][0]
        self.log.append(rpcid)
        out = self.rpc.get(rpcid)
        if isinstance(out, list):
            out = out.pop(0) if len(out) > 1 else out[0]      # the last answer repeats
        if callable(out):
            out = out(request)
        if isinstance(out, Exception):
            raise out
        assert out is not None, "no answer set for %s" % rpcid
        return out if isinstance(out, httpx.Response) else httpx.Response(200, text=out)

    def calls(self, rpcid: str) -> list[httpx.Request]:
        return [r for r in self.seen if parse_qs(urlsplit(str(r.url)).query).get("rpcids") == [rpcid]]


def args_of(request: httpx.Request) -> list:
    """The decoded args of one batchexecute request."""
    freq = parse_qs(request.content.decode())["f.req"][0]
    return json.loads(json.loads(freq)[0][0][1])


class Ctx:
    """The RunCtx an engine sees. Every platform step goes to the shared log; `renewed` keeps each written jar."""

    def __init__(self, log: list, *, blob: bytes = BLOB, gen: int = 1, proxy: Proxy | None = None,
                 state: dict | None = None, account_id: str = "acc-1", renew_ok: bool = True):
        self.log = log
        self.account = AccountView(account_id, "acc", blob, gen, proxy,
                                   {"project": PROJECT} if state is None else state, {"image": None}, "pk")
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
        """What the core stores: `blob`, or `stored_as(blob)` when a test plays a merge with another run."""
        self.log.append("renew")
        self.renewed.append(blob)
        self.merges.append(merge)
        if not self.renew_ok:
            return None
        return self.stored_as(blob) if self.stored_as is not None else blob
