"""meta's submit over HTTP, driven by the 05/11 captures (fixtures/meta/http-261105) through a fake curl_cffi session
and socket: no meta.ai, no real cookie. Each money rule has a test here and a mutation that breaks it."""

import asyncio
import base64
import dataclasses
import itertools
import json
import logging
from datetime import timedelta
from types import SimpleNamespace

from curl_cffi import WebSocketClosed
from metakit import (
    CLOSE_4003,
    NOW,
    PROXY,
    Clock,
    Ctx,
    FakeSocket,
    Net,
    Resp,
    assert_no_secret,
    frames,
    job,
    page,
    settings,
)

from relay_core import errors
from relay_core import frames as core_frames
from relay_core.providers import (
    REFUSED_BY,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Fail,
    MediaRef,
    NotTaken,
    ProxyBad,
    Retry,
    Success,
    Suspect,
)
from relay_core.testing.mutation import mutation
from relay_meta.constants import FINAL_PATH, GATEWAY_URL, SITE_URL, TEXT_PATH, UPLOAD_URL
from relay_meta.engine import submit as S
from relay_meta.engine import wire
from relay_meta.engine.web import Sessions, ip_key, read_page
from relay_meta.spec import SPEC

SUBMIT_FILE = "packages/provider-meta/src/relay_meta/engine/submit.py"
WEB_FILE = "packages/provider-meta/src/relay_meta/engine/web.py"
WIRE_FILE = "packages/provider-meta/src/relay_meta/engine/wire.py"
CONSTANTS = "packages/provider-meta/src/relay_meta/constants.py"
ERRORS_FILE = "packages/provider-meta/src/relay_meta/errors.py"
HOME = ("GET", SITE_URL + "/")
WS = ("WS", GATEWAY_URL)
UPLOAD = ("POST", UPLOAD_URL)
IP = ip_key(PROXY)
FOX = "a red fox sitting in a snowy forest"
LIMIT_TEXT = "You’ve reached your limit for creating images today. Please come back later."
CDN = "https://scontent.xx.fbcdn.net/v/"


# ---- the kit -----------------------------------------------------------------------------------------------------
def at(path, value) -> list:
    for f in reversed(path[1:]):
        value = [(f, value)]
    return [(path[0], value)]


def response(*fields, done=False) -> bytes:
    """A gateway data frame holding the protobuf `fields` (lists of `at(...)`), `is_final_chunk` when `done`."""
    return wire.frame(wire.DATA, 0, b"\x00\x00" + wire.enc([f for fs in fields for f in fs] +
                                                             ([(3, 1)] if done else [])))


def estab_answer(code=200) -> bytes:
    return wire.frame(wire.ESTAB, 0, json.dumps({"code": code}).encode())


def gateway(name=None, *, answer=(), code=200) -> FakeSocket:
    """A socket that answers the EstabStream `code`, then the capture `name`'s frames (its own answer), or `answer`."""
    if name is not None:
        return FakeSocket(frames(name))
    return FakeSocket([b"\x0a", estab_answer(code), *answer])


def final(i, ext="webp") -> str:
    return CDN + "t39.105495-1/%d_n.%s?_nc_ht=x" % (i, ext)


def preview(i) -> str:
    return CDN + "t58.90637-1/%d_n.webp?_nc_ht=x" % i


def said(text) -> list:
    return at(TEXT_PATH, text)


def shown(*urls) -> list:
    return [f for u in urls for f in (at(FINAL_PATH, u) if "/t39." in u else at((1, 9), u))]


def signed_in(**cookies):
    return Resp(200, page("page_signed_in"), cookies=cookies)


class ProxyError(Exception):
    """Named like curl_cffi's: the proxy refused."""


def go(name="t2i_n2", *, op="text_to_image", n=2, ratio=None, sockets=None, routes=None, ctx=None, sessions=None,
       clk=None, inputs=(), shrink=None, sent_text=FOX, state=None, source=None, held=False, uuids=None,
       margin=S.HOLD_MARGIN_S, bound_s=10.0, **over):
    """One submit. `sockets`: the FakeSockets the gateway gives, in order (default: the capture `name`). `held`: the
    account's token is cached (fresh). `bound_s`: real seconds the run may take."""
    clk = clk or Clock()
    ctx = ctx or Ctx(settings_=settings(**over))
    sessions = sessions or Sessions(clk)
    if held:
        sessions.keep(ctx.account.id, ctx.account.credential_gen, read_page(200, page("page_signed_in")))
    socks = list(sockets) if sockets is not None else [gateway(name)]
    net = Net({HOME: signed_in(), UPLOAD: Resp(200, json_body={"media_id": "7001"}), WS: list(socks), **(routes or {})})
    uuids = uuids if uuids is not None else ("00000000-0000-4000-8000-%012d" % i for i in itertools.count(1))
    reads = []

    async def read_media(ref):
        reads.append(ref)
        return b"img%d" % ref.idx

    async def same(data, mime):
        return data, mime
    deps = S.Deps(make_session=net.make_session, read_media=read_media, shrink=shrink or same, clock=clk, now=clk.now,
                  uuid4=lambda: next(uuids), rand22=lambda: 7, hold_margin_s=margin)
    j = job(op, sent_text=sent_text, n=n if op.endswith("image") else None, ratio=ratio, state=state, inputs=inputs)
    if source is not None:
        j = dataclasses.replace(j, source=source)
    try:
        result = asyncio.run(asyncio.wait_for(S.submit(ctx, j, ctx.settings, sessions, deps=deps), bound_s))
    except TimeoutError:
        raise AssertionError("the verdict was lost: not within the core's bound") from None
    return SimpleNamespace(result=result, ctx=ctx, net=net, socks=socks, sessions=sessions, clk=clk, reads=reads)


def data_frames(k) -> list[bytes]:
    return [f for s in k.socks for f in s.data_frames()]


def request(k) -> bytes:
    """The Request protobuf of the one data frame sent."""
    (raw,) = data_frames(k)
    _, _, payload = wire.parse_frame(raw)
    assert payload[:2] == b"\x00\x80"
    return base64.b64decode(json.loads(payload[2:])["payload"])


def sent_text(k) -> str:
    return next(wire.values(request(k), (2, 2))).decode()


def steps(k) -> list[str]:
    return [c[0] for c in k.ctx.calls if c[0] in ("report_ref", "mark_sent")]


def page_gets(k) -> int:
    return sum(1 for c in k.net.calls if c.method == "GET" and c.url.startswith(SITE_URL))


def opened(k) -> int:
    return sum(1 for c in k.net.calls if c.method == "WS")


def refs(n=1, mime="image/png"):
    return [MediaRef(i, "in/%d" % i, mime, 10) for i in range(n)]


def stale(clk, sessions, ctx, gate_closed=False):
    """The account's token was read past `meta.token_reread_s`; `gate_closed`: a page was loaded on its IP just now."""
    sessions.keep(ctx.account.id, ctx.account.credential_gen, read_page(200, page("page_signed_in")))
    clk.advance(3601)
    if gate_closed:
        sessions.gate.take(IP, 300)


def unsent(k):
    assert data_frames(k) == [] and "mark_sent" not in steps(k), "nothing is sent"


# ---- the send ----------------------------------------------------------------------------------------------------
REPORT = '        await self._core(lambda: self.ctx.report_ref(conversation, {"conversation_id": conversation}))\n'
REPORT_MARK = ('        await self._core(lambda: self.ctx.report_ref(conversation, '
               '{"conversation_id": conversation}))\n'
               "        await self._core(self.ctx.mark_sent)\n")
MARK_REPORT = ("        await self._core(self.ctx.mark_sent)\n"
               '        await self._core(lambda: self.ctx.report_ref(conversation, '
               '{"conversation_id": conversation}))\n')
MARK_SEND = "        await self._core(self.ctx.mark_sent)\n        self.sent = True\n        await sock.send(data)\n"
SEND_MARK = "        await sock.send(data)\n        await self._core(self.ctx.mark_sent)\n        self.sent = True\n"


@mutation(SUBMIT_FILE, REPORT_MARK, MARK_REPORT, expect="report_ref < mark_sent < send")
@mutation(SUBMIT_FILE, REPORT, "", expect="report_ref < mark_sent < send")
@mutation(SUBMIT_FILE, MARK_SEND, SEND_MARK, expect="report_ref < mark_sent < send")
def test_a_text_to_image_reports_the_ref_marks_sent_sends_one_frame_and_delivers_n_images():
    log = []
    ctx = Ctx()
    step = ctx._step
    ctx._step = lambda *call: (log.append(call[0]), step(*call))
    sock = gateway("t2i_n2")
    send = sock.send

    async def logged(data):
        log.append("send%02x" % data[0])
        await send(data)
    sock.send = logged
    k = go(ctx=ctx, sockets=[sock])
    flow = [x for x in log if x in ("report_ref", "mark_sent", "send0d")]
    assert flow == ["report_ref", "mark_sent", "send0d"], "report_ref < mark_sent < send: %r" % (flow,)
    assert log.index("send0f") < log.index("report_ref"), "the stream opens before anything is reported"
    assert len(data_frames(k)) == 1
    assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 2
    conv = k.ctx.refs[0][0]
    assert k.ctx.refs == [(conv, {"conversation_id": conv})]
    assert [(o.idx, o.mime, o.meta) for o in k.result.job.outputs] == [
        (i, "image/webp", {"conversation_id": conv}) for i in range(2)]
    assert next(wire.values(request(k), (2, 1, 2, 1))).decode() == conv, "the Request names the reported chat"
    assert Success() in k.result.account
    assert all(PROXY.host in c.proxies["https"] and c.allow_redirects is False for c in k.net.calls), \
        "every call through the proxy, never a redirect"
    assert sock.closed


@mutation(SUBMIT_FILE, "        self.conversation = self.deps.uuid4()\n",
          "        self.conversation = '00000000-0000-4000-8000-000000000001'\n", expect="a fresh chat per run")
def test_each_run_uses_a_fresh_conversation_uuid():
    clk = Clock()
    sessions = Sessions(clk)
    uuids = ("00000000-0000-4000-8000-%012d" % i for i in itertools.count(1))
    one = go(clk=clk, sessions=sessions, uuids=uuids)
    two = go(clk=clk, sessions=sessions, uuids=uuids)
    assert isinstance(one.result.job, Completed) and isinstance(two.result.job, Completed)
    assert one.ctx.refs[0][0] != two.ctx.refs[0][0], "a fresh chat per run"


# ---- the words ---------------------------------------------------------------------------------------------------
LEAD = '            count_lead(self.n) if self.operation == "text_to_image" else "")'


@mutation(SUBMIT_FILE, LEAD, '            "")', expect="asks for the count")
@mutation(SUBMIT_FILE, "    return IMAGE_FROM_TEXT if n == 1 else IMAGES_FROM_TEXT % n",
          '    return "" if n == 1 else IMAGES_FROM_TEXT % n', expect="asks for the count")
def test_an_image_job_asks_for_the_count_it_paid_for_before_the_text_n_1_included():
    k = go(ratio="9:16")
    assert sent_text(k) == "Create 2 images: %s, 9:16" % FOX, "asks for the count: %r" % sent_text(k)
    k = go("t2i_n1", n=1, sent_text="a red   fox\nin the snow")
    assert sent_text(k) == "Create 1 image: a red fox in the snow", "asks for the count: %r" % sent_text(k)


@mutation(SUBMIT_FILE, '        lead = VIDEO_FROM_TEXT if self.operation == "text_to_video" else (',
          '        lead = "" if self.operation == "text_to_video" else (', expect="asks for a video")
@mutation(SUBMIT_FILE, '        if job.params.get("ratio") and (self.kind, job.params["ratio"]) not in RATIO_UNTYPED:',
          '        if job.params.get("ratio"):', expect="a 16:9 video goes with no tail")
@mutation(CONSTANTS, 'RATIO_UNTYPED = (("video", "16:9"),)', "RATIO_UNTYPED = ()",
          expect="a 16:9 video goes with no tail")
@mutation(CONSTANTS, 'RATIO_TAIL = ", %s"', 'RATIO_TAIL = " %s"', expect="the ratio")
def test_only_a_video_from_text_gets_the_video_wording_and_16_9_video_gets_no_tail():
    k = go("t2v", op="text_to_video", ratio="16:9")
    assert sent_text(k) == "Create a short video: " + FOX, "asks for a video; a 16:9 video goes with no tail: %r" % (
        sent_text(k),)
    assert isinstance(k.result.job, Completed) and k.result.job.outputs[0].mime == "video/mp4"
    k = go("t2v", op="text_to_video", ratio="9:16")
    assert sent_text(k) == "Create a short video: %s, 9:16" % FOX, "the ratio: %r" % sent_text(k)
    assert sent_text(go("i2v", op="image_to_video", inputs=refs(1))) == FOX
    assert sent_text(go("edit", op="image_to_image", n=1, inputs=refs(1))) == FOX


@mutation(SUBMIT_FILE, '            self.text += RATIO_TAIL % job.params["ratio"]',
          '            self.text = (RATIO_TAIL % job.params["ratio"]).lstrip(", ") + " " + self.text',
          expect="the frame first, the ratio tail second")
def test_an_admin_frame_is_sent_first_and_the_ratio_tail_after_it():
    admin = "Style: watercolor. {{client_prompt}} {{tracking_code}} Keep it calm."
    for frame in (SPEC.frames.defaults["image"], admin):
        text = core_frames.render(frame, FOX, "7100", SPEC.frames.verbatim)
        k = go("t2i_n1", n=None, ratio="4:3", sent_text=text)
        assert sent_text(k) == " ".join(text.split()) + ", 4:3", "the frame first, the ratio tail second: %r" % (
            sent_text(k),)


@mutation(WIRE_FILE, "                 for i, url in enumerate(tuple(urls)[:n]))",
          "                 for i, url in enumerate(urls))", expect="at most the n asked")
@mutation(SUBMIT_FILE, '    if n is None:\n        return ""\n', "    if n is None:\n        return IMAGE_FROM_TEXT\n",
          expect="from before n")
@mutation(SUBMIT_FILE, '        self.n = job.params.get("n")', '        self.n = job.params.get("n") or 1',
          expect="from before n")
def test_a_job_delivers_at_most_n_the_first_in_order_and_a_job_from_before_n_every_image():
    every = wire.Reader()
    for raw in frames("t2i_n4"):
        every.feed(raw)
    finals = every.answer("image").finals
    k = go("t2i_n4", n=2)
    assert [o.ref for o in k.result.job.outputs] == list(finals[:2]), "at most the n asked: %r" % (k.result.job,)
    k = go("t2i_n2", n=None)
    assert sent_text(k) == FOX, "from before n: %r" % sent_text(k)
    assert len(k.result.job.outputs) == 2, "from before n: %r" % (k.result.job,)


# ---- the inputs --------------------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, "        for i, (data, mime) in enumerate(images):\n            name =",
          "        for i, (data, mime) in enumerate(images[:1]):\n            name =", expect="every reference")
def test_an_edit_uploads_every_reference_in_order_before_the_send_and_attaches_their_media_ids():
    ups = [Resp(200, json_body={"media_id": "700%d" % i}) for i in (1, 2, 3)]
    k = go("edit", op="image_to_image", n=1, inputs=refs(3, "image/jpeg"), routes={UPLOAD: ups})
    posts = [i for i, c in enumerate(k.net.calls) if c.method == "POST" and c.url.startswith(UPLOAD_URL)]
    assert len(posts) == 3, "every reference: %d uploads" % len(posts)
    ws = next(i for i, c in enumerate(k.net.calls) if c.method == "WS")
    assert max(posts) < ws, "every upload is answered before the socket opens"
    assert [k.net.calls[i].data for i in posts] == [b"img0", b"img1", b"img2"], "in the client's order"
    assert all(k.net.calls[i].cookies == {} for i in posts), "the upload carries no cookie"
    ids = list(wire.values(request(k), (2, 3, 1, 1)))
    assert ids == [7001, 7002, 7003], "every reference attached by its media id, in order: %r" % (ids,)
    assert [x.decode() for x in wire.values(request(k), (2, 3, 7))] == ["upload.jpg", "upload1.jpg", "upload2.jpg"]
    assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 1


@mutation(SUBMIT_FILE, "                if refused.status in (401, 403):", "                if False:",
          expect="the dead token is forgotten")
def test_an_upload_refused_401_or_403_forgets_the_token_so_the_next_job_reads_the_page():
    for status in (401, 403):
        k = go("edit", op="image_to_image", n=1, inputs=refs(1), held=True, routes={UPLOAD: Resp(status, "no")})
        assert k.result.job == Retry("meta.http_error"), k.result.job
        unsent(k)
        assert k.sessions.token(k.ctx.account.id, k.ctx.account.credential_gen) is None, \
            "the dead token is forgotten (%d)" % status
    k = go("edit", op="image_to_image", n=1, inputs=refs(1), held=True, routes={UPLOAD: Resp(500, "no")})
    assert k.sessions.token(k.ctx.account.id, k.ctx.account.credential_gen) is not None, "a 500 says nothing of it"


def test_an_upload_not_answered_sends_nothing_and_is_a_retry():
    for answer in (Resp(500, "no"), Resp(200, json_body={"status": "ok"}), ConnectionError("reset")):
        k = go("edit", op="image_to_image", n=1, inputs=refs(2), routes={UPLOAD: answer})
        assert k.result.job == Retry("meta.http_error"), (answer, k.result.job)
        assert opened(k) == 0
        unsent(k)


@mutation(SUBMIT_FILE, "            if mime is None:\n", "            if False:\n", expect="input_unusable")
def test_an_input_that_cannot_be_shrunk_fails_refunded_and_opens_nothing():
    async def never(data, mime):
        return None
    k = go("i2v", op="image_to_video", inputs=refs(1), shrink=never)
    assert isinstance(k.result.job, Fail) and k.result.job.code == "meta.input_unusable", \
        "input_unusable: %r" % (k.result.job,)
    assert k.net.calls == [], "nothing opened"
    unsent(k)
    r = errors.rule("meta.input_unusable")
    assert (r.terminal, r.refund) == (True, True)


def test_a_small_input_is_uploaded_as_it_is():
    from relay_meta.engine.shrink import shrink
    assert asyncio.run(shrink(b"tiny", "image/webp")) == (b"tiny", "image/webp")
    k = go("i2v", op="image_to_video", inputs=refs(1, "image/webp"), shrink=shrink)
    up = next(c for c in k.net.calls if c.method == "POST")
    assert up.data == b"img0" and up.headers["Content-Type"] == "image/webp"


# ---- the token ---------------------------------------------------------------------------------------------------
def test_a_cached_fresh_token_sends_with_no_page_load():
    k = go(held=True)
    assert page_gets(k) == 0 and isinstance(k.result.job, Completed)
    k = go()
    assert page_gets(k) == 1 and isinstance(k.result.job, Completed), "no token: one page read"


@mutation(SUBMIT_FILE, "        except GateClosed as closed:\n            if held is not None:\n",
          "        except GateClosed as closed:\n            if False:\n", expect="keeps the old token")
def test_a_stale_token_rereads_the_page_and_a_closed_gate_keeps_the_old_token():
    clk, ctx = Clock(), Ctx()
    sessions = Sessions(clk)
    stale(clk, sessions, ctx)
    k = go(clk=clk, ctx=ctx, sessions=sessions)
    assert page_gets(k) == 1 and isinstance(k.result.job, Completed), "a stale token is read again"
    clk, ctx = Clock(), Ctx()
    sessions = Sessions(clk)
    stale(clk, sessions, ctx, gate_closed=True)
    k = go(clk=clk, ctx=ctx, sessions=sessions)
    assert page_gets(k) == 0 and isinstance(k.result.job, Completed), "keeps the old token: %r" % (k.result.job,)


WAIT = "        return _result(Retry(errors.CHECKPOINT if marked else errors.PAGE_WAIT), rest)\n"


@mutation(SUBMIT_FILE, WAIT, "        return _result(Retry(errors.HTTP_ERROR), rest)\n", expect="page_wait")
@mutation(SUBMIT_FILE, WAIT, "        return _result(Retry(errors.CHECKPOINT if marked else errors.PAGE_WAIT))\n",
          expect="a cooldown")
@mutation(ERRORS_FILE, '    Rule(PAGE_WAIT, 503, "api_error", False, False, False, True,',
          '    Rule(PAGE_WAIT, 503, "api_error", True, True, False, True,', expect="no attempt")
def test_no_token_and_a_closed_gate_is_page_wait_with_a_cooldown_and_no_attempt():
    clk = Clock()
    sessions = Sessions(clk)
    sessions.gate.take(IP, 300)
    clk.advance(100)
    k = go(clk=clk, sessions=sessions)
    assert k.result.job == Retry("meta.page_wait"), "page_wait: %r" % (k.result.job,)
    assert page_gets(k) == 0 and opened(k) == 0
    unsent(k)
    assert [type(e) for e in k.result.account] == [Cooldown], "a cooldown until the gate opens: %r" % (
        k.result.account,)
    assert k.result.account[0] == Cooldown(k.result.account[0].reason, NOW + timedelta(seconds=300), recheck=True)
    r = errors.rule("meta.page_wait")
    assert (r.terminal, r.refund, r.counts_attempt) == (False, False, False), "no attempt"


@mutation(SUBMIT_FILE, WAIT, "        return _result(Retry(errors.CHECKPOINT if marked else errors.PAGE_WAIT), rest, "
          "*signed_out_effects(self.deps.now()))\n", expect="no strike")
@mutation(ERRORS_FILE, '    Rule(CHECKPOINT, 503, "api_error", False, False, False, True,',
          '    Rule(CHECKPOINT, 503, "api_error", True, True, False, True,', expect="never a job failure")
def test_a_vercel_page_with_a_token_still_sends_and_without_one_is_checkpoint_with_a_cooldown():
    vercel = {HOME: Resp(429, page("page_vercel"))}
    clk, ctx = Clock(), Ctx()
    sessions = Sessions(clk)
    stale(clk, sessions, ctx)
    k = go(clk=clk, ctx=ctx, sessions=sessions, routes=vercel)
    assert page_gets(k) == 1 and isinstance(k.result.job, Completed), "a held token still sends: %r" % (k.result.job,)
    k = go(routes=vercel)
    assert k.result.job == Retry("meta.checkpoint"), k.result.job
    assert not any(isinstance(e, Suspect) for e in k.result.account), "no strike"
    (rest,) = k.result.account
    assert isinstance(rest, Cooldown) and rest.recheck is True and rest.until == NOW + timedelta(seconds=600)
    unsent(k)
    r = errors.rule("meta.checkpoint")
    assert (r.terminal, r.refund, r.counts_attempt) == (False, False, False), "never a job failure"


@mutation(SUBMIT_FILE, '        if said == "unauthorized":\n', "        if False:\n", expect="reread once")
@mutation(SUBMIT_FILE, '        if said == "unauthorized":\n', '        while said == "unauthorized":\n',
          expect="never twice")
def test_a_refused_estab_rereads_once_on_a_new_socket_and_never_twice():
    first, second = gateway(code=401), gateway("t2i_n2")
    k = go(held=True, sockets=[first, second])
    assert isinstance(k.result.job, Completed), "reread once: %r" % (k.result.job,)
    assert page_gets(k) == 1 and opened(k) == 2 and first.closed
    assert [f[0] for f in first.sent] == [wire.ESTAB] and [f[0] for f in second.sent] == [wire.ESTAB, wire.DATA]
    clk = Clock()
    k = go(held=True, clk=clk, sockets=[gateway(code=401), gateway(code=401), gateway(code=401)])
    assert k.result.job == Retry("meta.http_error"), "never twice: %r" % (k.result.job,)
    assert page_gets(k) == 1 and opened(k) == 2, "never twice"
    unsent(k)
    # Review Focus 1: the token died while the gate is closed and no other token exists
    clk = Clock()
    sessions = Sessions(clk)
    sessions.gate.take(IP, 300)
    k = go(held=True, clk=clk, sessions=sessions, sockets=[gateway(code=401), gateway("t2i_n2")])
    assert k.result.job == Retry("meta.page_wait") and isinstance(k.result.account[0], Cooldown), k.result
    assert page_gets(k) == 0 and opened(k) == 1
    unsent(k)
    assert sessions.token(k.ctx.account.id, k.ctx.account.credential_gen) is None, "the dead token is forgotten"


@mutation(SUBMIT_FILE, "            return sock, closed.cause", "            raise", expect="a 4003 close rereads once")
@mutation(WEB_FILE, "            raise GatewayClosed(b\"\") from None", "            raise",
          expect="the close is named")
def test_a_gateway_close_before_the_send_is_its_cause_and_a_4003_rereads_once():
    # 06/11 live: a stale token's socket is closed with 4003 right after the estab (it was a FrameError, a lost try)
    first = FakeSocket([b"\x0a", CLOSE_4003])
    k = go(held=True, sockets=[first, gateway("t2i_n2")])
    assert isinstance(k.result.job, Completed), "a 4003 close rereads once: %r" % (k.result.job,)
    assert page_gets(k) == 1 and opened(k) == 2 and first.closed and len(data_frames(k)) == 1
    k = go(held=True, sockets=[FakeSocket([((1011).to_bytes(2, "big") + b"reason-PLANTED", 8)])])
    assert k.result.job == Retry("meta.http_error"), k.result.job
    assert any("closed:1011" in e[0] for e in k.ctx.events) and "reason-PLANTED" not in repr(k.ctx.events)
    assert page_gets(k) == 0 and k.socks[0].closed
    unsent(k)
    k = go(held=True, sockets=[FakeSocket([b"\x0a", WebSocketClosed("gone")])])     # curl saw the close first
    assert k.result.job == Retry("meta.http_error"), k.result.job
    assert any("closed:none" in e[0] for e in k.ctx.events), "the close is named: %r" % (k.ctx.events,)
    unsent(k)


@mutation(SUBMIT_FILE, "        except BaseException:\n            await quiet_close(sock)\n            raise\n", "",
          expect="the socket is closed")
def test_a_socket_that_fails_during_the_estab_is_closed():
    broken = FakeSocket([ConnectionError("reset")])
    k = go(held=True, sockets=[broken])
    assert k.result.job == Retry("meta.http_error"), k.result.job
    unsent(k)
    assert broken.closed, "the socket is closed"


class HangingClose(FakeSocket):
    async def close(self) -> None:
        await asyncio.Event().wait()


@mutation(SUBMIT_FILE, "            if self.outcome is not None:\n                return self.outcome\n", "",
          expect="the verdict is kept")
def test_a_deadline_hit_while_closing_the_socket_keeps_the_verdict():
    k = go(sockets=[HangingClose(frames("t2i_n2"))], margin=59.5, image_submit_timeout_s=60, bound_s=3.0)
    assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 2, \
        "the verdict is kept: %r" % (k.result.job,)


def test_an_estab_answered_with_anything_else_is_a_retry():
    k = go(held=True, sockets=[gateway(code=500)])
    assert k.result.job == Retry("meta.http_error")
    unsent(k)


@mutation(SUBMIT_FILE, "                                           *signed_out_effects(self.deps.now())))",
          "))", expect="a strike and a rest")
def test_a_signed_out_page_is_a_strike_and_a_rest_and_sends_nothing():
    k = go(routes={HOME: Resp(200, page("page_signed_out"))})
    assert k.result.job == Retry("pool.account_logged_out")
    kinds = [type(e) for e in k.result.account]
    assert kinds == [Suspect, Cooldown] and k.result.account[1].recheck, "a strike and a rest: %r" % (
        k.result.account,)
    assert opened(k) == 0
    unsent(k)


@mutation(SUBMIT_FILE, "        if isinstance(exc, NetError) and exc.proxy:", "        if False:",
          expect="proxy_failed")
def test_a_proxy_failure_before_the_send_is_proxy_failed_with_a_300_s_rest():
    for routes in ({HOME: ProxyError("refused")}, {WS: ProxyError("refused")}):
        k = go(routes=routes)
        assert k.result.job == Retry("pool.proxy_failed"), "proxy_failed: %r" % (k.result.job,)
        assert ProxyBad in [type(e) for e in k.result.account]
        assert Cooldown("proxy failed", NOW + timedelta(seconds=300)) in k.result.account
        unsent(k)
    k = go(routes={HOME: ConnectionError("reset")})
    assert k.result.job == Retry("meta.http_error")


def test_an_unreadable_page_is_a_retry():
    k = go(routes={HOME: Resp(200, "<html>nothing</html>")})
    assert k.result.job == Retry("meta.http_error")
    unsent(k)


# ---- after the send ----------------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, "            cause = str(exc)", "            pass", expect="the close code is told")
@mutation(SUBMIT_FILE, '        if self.sent:\n            return await self._unknown("the run failed after the send',
          '        if False:\n            return await self._unknown("the run failed after the send',
          expect="never a Retry after the send")
@mutation(SUBMIT_FILE, "        if self.sent:\n            return await self._unknown(why)\n",
          "        if False:\n            return await self._unknown(why)\n", expect="never a Retry after the send")
def test_whatever_breaks_after_the_send_is_ambiguous_and_never_a_second_frame():
    bad = wire.frame(wire.DATA, 0, b"\x00\x00\x0b\x01")
    cases = {
        "socket closed": dict(sockets=[gateway(answer=[ConnectionError("closed")])]),
        "unreadable frame": dict(sockets=[gateway(answer=[bad])]),
        "gateway close": dict(sockets=[gateway(answer=[CLOSE_4003])]),
        "curl saw it closed": dict(sockets=[gateway(answer=[WebSocketClosed("gone")])]),
        "end of data before the final chunk": dict(sockets=[gateway(answer=[wire.frame(wire.EOD, 0, b"")])]),
        "deadline": dict(sockets=[gateway(answer=[])], margin=59.9, image_submit_timeout_s=60),
    }
    for what, kw in cases.items():
        k = go(**kw)
        assert isinstance(k.result.job, Ambiguous), "%s: never a Retry after the send: %r" % (what, k.result.job)
        assert len(data_frames(k)) == 1, what
        assert steps(k) == ["report_ref", "mark_sent"], what
        if what == "unreadable frame":
            assert any("unreadable gateway frame" in e[0] for e in k.ctx.events)
        if what == "gateway close":
            assert any("closed the socket (code 4003)" in e[0] for e in k.ctx.events), "the close code is told"


@mutation(SUBMIT_FILE, "        bound = asyncio.timeout(max(0.0, self.deadline - self.deps.clock()))",
          "        bound = asyncio.timeout(None)", expect="the verdict was lost")
@mutation(SUBMIT_FILE, '"meta.%s_submit_timeout_s" % self.kind) - deps.hold_margin_s',
          '"meta.%s_submit_timeout_s" % self.kind) + deps.hold_margin_s', expect="the verdict was lost")
def test_the_verdict_comes_within_the_core_bound_whatever_hangs():
    k = go(sockets=[gateway(answer=[])], margin=59.5, image_submit_timeout_s=60, bound_s=2.0)
    assert isinstance(k.result.job, Ambiguous) and len(data_frames(k)) == 1, k.result.job
    # before the send: a socket that never answers the EstabStream is a retry, within the bound
    hung = FakeSocket([])
    k = go(held=True, sockets=[hung], margin=59.5, image_submit_timeout_s=60, bound_s=2.0)
    assert k.result.job == Retry("meta.http_error"), "nothing was sent: %r" % (k.result.job,)
    unsent(k)


POLICY_IF = '    if error.get("failure_kind") in POLICY_KINDS or error.get("error_code") in POLICY_CODES:\n'


# ---- the verdict -------------------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, '        if verdict == "policy":\n', "        if False:\n", expect="content_refused")
@mutation(SUBMIT_FILE, POLICY_IF, "    if False:\n", expect="content_refused")
def test_a_policy_refusal_fails_at_once_refunded_with_content_refused():
    k = go("refusal", n=1)
    assert isinstance(k.result.job, Fail) and k.result.job.code == "meta.content_refused", \
        "content_refused: %r" % (k.result.job,)
    assert not any(isinstance(e, Balance) for e in k.result.account), "a refusal empties no pool"
    assert len(data_frames(k)) == 1
    r = errors.rule("meta.content_refused")
    assert (r.terminal, r.refund) == (True, True)
    # on a second account too: never moved to a third
    k = go("refusal", n=1, state={REFUSED_BY: ["acc-0"]}, refusal_max_accounts=9)
    assert isinstance(k.result.job, Fail) and k.result.job.code == "meta.content_refused"


LIMIT_IF = "    if error.get(\"failure_kind\") in LIMIT_KINDS or (answer.error is None and is_limit(answer.text)):\n"
LIMIT_MOVES = "            outcome = account_refused(self.job, self.account.id, self.settings, "
LIMIT_REST = ('            return _result(outcome, limit_balance(self.settings, self.deps.now(), self.kind)), '
              'line, "submit"\n')


def limit(**kw):
    return go(sockets=[gateway(answer=[response(said(LIMIT_TEXT), done=True)])], **kw)


@mutation(SUBMIT_FILE, LIMIT_IF, "    if False:\n", expect="the job did not go to another account")
@mutation(SUBMIT_FILE, "    return any(p in t for p in LIMIT_PHRASES)", "    return False",
          expect="the job did not go to another account")
@mutation(SUBMIT_FILE, LIMIT_REST, '            return _result(outcome), line, "submit"\n',
          expect="the pool was not emptied")
@mutation(SUBMIT_FILE, "not_taken=POOL_OUT_OF_POINTS,", "not_taken=errors.NO_IMAGE,",
          expect="the job did not go to another account")
def test_a_limit_answer_moves_the_job_to_another_account_and_empties_the_pool():
    k = limit()
    assert k.result.job == NotTaken("pool.account_out_of_points", {REFUSED_BY: ["acc-1"]}), \
        "the job did not go to another account: %r" % (k.result.job,)
    assert len(data_frames(k)) == 1
    r = errors.rule("pool.account_out_of_points")
    assert (r.terminal, r.refund, r.counts_attempt) == (False, False, False)
    assert Balance("image", 0, NOW + timedelta(hours=4)) in k.result.account, \
        "the pool was not emptied: %r" % (k.result.account,)
    assert any("another account" in e[0] for e in k.ctx.events)
    assert S.is_limit("You've hit your  LIMIT for today") and not S.is_limit("Here is your fox")


@mutation(SUBMIT_FILE, LIMIT_MOVES,
          "            outcome = (lambda *a, **k: NotTaken(POOL_OUT_OF_POINTS, {REFUSED_BY: []}))(self.job, "
          "self.account.id, self.settings, ", expect="the last account's limit did not end the job")
def test_the_limit_on_the_last_account_fails_it_refunded_with_todays_code():
    k = limit(state={REFUSED_BY: ["acc-0"]})
    assert isinstance(k.result.job, Fail) and k.result.job.code == "meta.no_image", \
        "the last account's limit did not end the job: %r" % (k.result.job,)
    assert k.result.job.detail == ("meta finished its answer with no image (refused on 2 account(s), the most "
                                   "meta.refusal_max_accounts allows)")
    assert k.result.job.state_patch == {REFUSED_BY: ["acc-0", "acc-1"]}
    assert Balance("image", 0, NOW + timedelta(hours=4)) in k.result.account
    assert isinstance(limit(state={REFUSED_BY: ["acc-0"]}, refusal_max_accounts=3).result.job, NotTaken)
    pinned = limit(source=SimpleNamespace(job_id="src", idx=0)).result.job
    assert isinstance(pinned, Fail) and pinned.code == "meta.no_image", "a pinned job runs on one account"
    video = go(op="text_to_video", state={REFUSED_BY: ["acc-0"]},
               sockets=[gateway(answer=[response(said(LIMIT_TEXT), done=True)])]).result
    assert isinstance(video.job, Fail) and video.job.code == "meta.no_video"
    assert Balance("video", 0, NOW + timedelta(hours=4)) in video.account


@mutation(SUBMIT_FILE, '    return "empty"\n', '    return "unknown"\n', expect="no_image")
def test_a_done_answer_with_no_media_and_no_reason_fails_refunded():
    k = go(sockets=[gateway(answer=[response(said("No."), done=True)])])
    assert k.result.job == Fail("meta.no_image", "meta finished its answer with no image"), "no_image: %r" % (
        k.result.job,)
    assert not any(isinstance(e, Balance) for e in k.result.account), "a plain refusal empties no pool"
    other = json.dumps({"failure_kind": "tool_error", "error_code": "x"})
    k = go(sockets=[gateway(answer=[response(at((1, 4, 6, 4), other), done=True)])])
    assert k.result.job.code == "meta.no_image" and any("tool_error" in e[0] for e in k.ctx.events)
    r = errors.rule("meta.no_image")
    assert (r.terminal, r.refund) == (True, True)


DONE_IF = ('        return "done" if answer.previews == 0 or (n is not None and len(answer.finals) >= n) '
           'else "unknown"\n')


@mutation(SUBMIT_FILE, DONE_IF, '        return "done"\n', expect="never a part delivery")
@mutation(SUBMIT_FILE, DONE_IF, '        return "done" if answer.previews == 0 else "unknown"\n', expect="a draft")
@mutation(SUBMIT_FILE, '    if answer.previews or answer.other:\n        return "unknown"\n',
          '    if False:\n        return "unknown"\n', expect="never a refund")
def test_previews_left_or_only_the_other_kind_is_ambiguous_never_a_part_delivery():
    part = response(shown(preview(1), preview(2), final(1)), done=True)
    k = go(sockets=[gateway(answer=[part])], n=2)
    assert isinstance(k.result.job, Ambiguous), "never a part delivery: %r" % (k.result.job,)
    k = go(sockets=[gateway(answer=[part])], n=None)
    assert isinstance(k.result.job, Ambiguous), "never a part delivery (a job from before n): %r" % (k.result.job,)
    # n finals with a draft preview left (an edit's thinking step): complete
    k = go(sockets=[gateway(answer=[part])], op="image_to_image", n=1, inputs=refs(1))
    assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 1, "a draft: %r" % (k.result.job,)
    for answer in ([response(shown(final(1, "mp4")), done=True)], [response(shown(preview(1)), done=True)]):
        k = go(sockets=[gateway(answer=answer)], n=1)
        assert isinstance(k.result.job, Ambiguous), "never a refund at once: %r" % (k.result.job,)
    assert S.judge(wire.Answer(False, (final(1),), 0, 0, None, ""), "image", 1) == "unknown"


NOTE_LOG = '\n                log.warning("meta job %s: %s", self.job.id, note)'


@mutation(SUBMIT_FILE, "            if note is not None:" + NOTE_LOG, "            if False:" + NOTE_LOG,
          expect="noted")
@mutation(SUBMIT_FILE, "        if line is not None:\n", "        if False:\n", expect="noted")
def test_more_or_fewer_finals_than_paid_are_delivered_up_to_n_charged_in_full_and_noted(caplog):
    three = response(shown(final(1), final(2), final(3)), done=True)
    k = go(sockets=[gateway(answer=[three])], n=4)
    assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 3, k.result.job
    note = "meta made 3 of the 4 images asked; all are charged (any output is a full charge)"
    assert note in [e[0] for e in k.ctx.events], "noted: %r" % (k.ctx.events,)
    assert any("job-1" in r.getMessage() and note in r.getMessage() for r in caplog.records), "noted in the log"
    k = go(sockets=[gateway(answer=[three])], n=2)
    assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 2
    assert "meta made 3 images, 2 were paid: the first 2 are delivered, the rest are not" in [
        e[0] for e in k.ctx.events], "noted"
    for n in (3, None):
        k = go(sockets=[gateway(answer=[three])], n=n)
        assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 3 and k.ctx.events == []


@mutation(SUBMIT_FILE, "        self.outcome = result\n", "", expect="the verdict is kept")
def test_a_deadline_hit_while_the_note_is_written_keeps_the_verdict():
    ctx = Ctx(settings_=settings(image_submit_timeout_s=60))

    async def hangs(text, stage, source):
        if text.startswith("meta made"):     # only the note's write hangs
            await asyncio.Event().wait()
    ctx.event = hangs
    three = response(shown(final(1), final(2), final(3)), done=True)
    k = go(ctx=ctx, sockets=[gateway(answer=[three])], n=4, margin=59.5, bound_s=3.0)
    assert isinstance(k.result.job, Completed) and len(k.result.job.outputs) == 3, \
        "the verdict is kept: %r" % (k.result.job,)


def test_the_captured_answers_of_every_kind_complete():
    for name, op, n, inputs in (("t2i_n1", "text_to_image", 1, ()), ("t2i_n4", "text_to_image", 4, ()),
                                ("t2v", "text_to_video", None, ()), ("i2v", "image_to_video", None, refs(1)),
                                ("edit", "image_to_image", 1, refs(1))):
        k = go(name, op=op, n=n, inputs=inputs)
        assert isinstance(k.result.job, Completed), (name, k.result.job)
        assert len(k.result.job.outputs) == (n or 1) and k.ctx.evidences == [], name


# ---- evidence and secrets ----------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, "    if not isinstance(outcome.job, Completed) and run.reader.frames:\n",
          "    if False:\n", expect="frame summary")
def test_a_failed_job_keeps_a_redacted_frame_summary_and_a_delivered_one_none():
    k = go("refusal", n=1)
    assert [(n, m) for n, _, m in k.ctx.evidences] == [("gateway-frames.json", "application/json")], \
        "frame summary: %r" % (k.ctx.evidences,)
    rows = json.loads(k.ctx.evidences[0][1])
    assert any(r.get("text", "") and r["text"].startswith("I wasn't able") for r in rows)
    assert go().ctx.evidences == []
    assert go(routes={HOME: Resp(200, page("page_signed_out"))}).ctx.evidences == [], "no frames, no evidence"

    async def broken(*a):
        raise RuntimeError("store down")
    ctx = Ctx()
    ctx.evidence = broken
    assert isinstance(go("refusal", n=1, ctx=ctx).result.job, Fail), "evidence never fails the run"


def test_no_token_viewer_or_socket_url_reaches_an_event_evidence_or_log(caplog):
    caplog.set_level(logging.DEBUG)
    texts = []
    for kw in ({"name": "refusal", "n": 1}, {"sockets": [gateway(answer=[ConnectionError("wss://x?Authorization=y")])]},
               {"held": True, "sockets": [gateway(code=401), gateway(code=401)]},
               {"routes": {HOME: ProxyError("http://user:PLANTED-pw@10.0.0.9:3128")}},
               {"routes": {HOME: Resp(200, page("page_signed_out"))}}):
        k = go(**kw)
        texts += [k.result, *k.ctx.events, *(e[1].decode() for e in k.ctx.evidences), *k.ctx.refs]
    texts += [r.getMessage() for r in caplog.records]
    assert_no_secret(*texts)
    assert not any(GATEWAY_URL in str(t) for t in texts), "the socket URL is never kept"


def test_a_lost_lease_at_report_ref_or_mark_sent_propagates_and_sends_nothing():
    for gate in ("report_ref", "mark_sent"):
        ctx = Ctx(fail=gate)
        try:
            go(ctx=ctx)
        except RuntimeError as e:
            assert str(e) == "lease lost"
        else:
            raise AssertionError("the core's verdict was swallowed at %s" % gate)
