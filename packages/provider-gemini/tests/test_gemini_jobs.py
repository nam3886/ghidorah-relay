"""Submit, reconcile, poll and fetch over a fake gemini.google.com (no network), fed gemini's recorded answers from
fixtures/gemini/live-261023. Each money or verdict rule has a test that fails when the rule breaks, and a mutation that
proves it."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from geminikit import (
    I2I,
    LISTING,
    NOT_ENTITLED,
    PSID,
    READ,
    READ_LINK,
    SENT_AT,
    T2I,
    T2I_CHAT,
    T2I_LINK,
    T2I_PROMPT,
    UPLOADED,
    Ctx,
    Gemini,
    answer,
    args_of,
    chat_read,
    inner_of,
    job,
    listing,
    settings,
)

from relay_core.providers import (
    Ambiguous,
    Completed,
    Cooldown,
    Done,
    Fail,
    Failed,
    Found,
    Output,
    Pending,
    Proxy,
    ProxyBad,
    Rest,
    Retry,
    StillUnknown,
    Success,
    Suspect,
)
from relay_core.testing.fake_media import MediaSite, public_resolver
from relay_core.testing.mutation import mutation
from relay_gemini import errors
from relay_gemini.engine import results as R
from relay_gemini.engine.http import Sessions
from relay_gemini.engine.reply import read_answer
from relay_gemini.engine.submit import submit

SUBMIT_FILE = "packages/provider-gemini/src/relay_gemini/engine/submit.py"
RESULTS_FILE = "packages/provider-gemini/src/relay_gemini/engine/results.py"
REPLY_FILE = "packages/provider-gemini/src/relay_gemini/engine/reply.py"
HTTP_FILE = "packages/provider-gemini/src/relay_gemini/engine/http.py"
NOW = datetime(2026, 10, 23, 15, 42, 40, tzinfo=UTC)
WALL = 1_792_770_131.0
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


async def media(ref) -> bytes:
    return PNG


def run(g: Gemini, ctx: Ctx, j=None, cfg=None):
    return asyncio.run(submit(ctx, j or job(), cfg or settings(), Sessions(), transport=g.transport,
                              read_media=media, now=lambda: NOW, wall=lambda: WALL))


def sent(log) -> int:
    return log.count("SEND")


# ---- the send: write-ahead, one send, the request ---------------------------------------------------------------
WRITE_AHEAD = "            await ctx.report_ref(rid, {REQUEST_KEY: rid})\n"
MARK = "            await ctx.mark_sent()\n            sent = True\n"


@mutation(SUBMIT_FILE, WRITE_AHEAD, "", expect="the job was not on record before its send")
@mutation(SUBMIT_FILE, MARK, "            sent = True\n", expect="not marked sent before the send")
def test_the_job_is_on_record_and_marked_sent_right_before_its_one_send_and_a_drawn_image_is_completed():
    log = []
    g, ctx = Gemini(log, send=T2I), Ctx(log)
    result = run(g, ctx)
    assert "report_ref" in log and log.index("report_ref") < log.index("SEND"), \
        "the job was not on record before its send: %s" % log
    assert "mark_sent" in log and log.index("mark_sent") == log.index("SEND") - 1, \
        "not marked sent before the send: %s" % log
    assert sent(log) == 1
    assert isinstance(result.job, Completed)
    (out,) = result.job.outputs
    assert (out.ref, out.mime, out.idx) == (T2I_LINK, "image/png", 0)
    assert out.meta == {"chat": T2I_CHAT, "width": 1408, "height": 768}
    assert Success() in result.account
    rid = ctx.refs[0][0]
    assert ctx.refs[0][1] == {"request": rid} and ctx.refs[-1] == (T2I_CHAT, {"request": rid, "chat": T2I_CHAT})


@mutation(SUBMIT_FILE, "41: [1], 49: IMAGE_TOOL,", "41: [1], 49: 0,", expect="the image tool")
@mutation(SUBMIT_FILE, "53: 0, 54: [], 55: [[C.RATIO_IDS[ratio]]] if ratio else [],", "53: 0, 54: [], 55: [],",
          expect="the ratio option")
def test_the_send_is_the_web_apps_image_request_with_the_verbatim_text_and_the_ratio():
    log = []
    g = Gemini(log, send=T2I)
    run(g, Ctx(log), job("a lighthouse at dawn\nwith gulls", ratio="16:9"))
    inner = inner_of(g.sends()[0])
    assert len(inner) == 99 and inner[49] == 14, "the image tool was not chosen: %s" % inner[49]
    assert inner[55] == [[63]], "the ratio option id is missing: %s" % inner[55]
    assert inner[0][0] == "a lighthouse at dawn\nwith gulls", "the text is not the customer's words verbatim"
    assert inner[0][9][6] == [None, [None, "16:9"]] and inner[3] is None and inner[1] == ["en"]
    headers = g.sends()[0].headers
    assert json.loads(headers["x-goog-ext-525005358-jspb"]) == [inner[59], 1]
    assert json.loads(headers["x-goog-ext-525001261-jspb"])[4] == "cf41b0e0dd7d53e5"
    log = []
    g = Gemini(log, send=T2I)
    run(g, Ctx(log), job("a lighthouse"))
    inner = inner_of(g.sends()[0])
    assert inner[55] == [] and len(inner[0]) == 7, "no ratio: gemini picks"


def test_a_job_queued_before_gemini_had_frames_goes_as_its_words():
    log = []
    g = Gemini(log, send=T2I)
    run(g, Ctx(log), job("a lighthouse", sent="a lighthouse [7100]", frame_id=None))
    assert inner_of(g.sends()[0])[0][0] == "a lighthouse"


UPLOAD_GATE = "                if path is None:\n                    return _result(Retry(errors.UPLOAD_FAILED))\n"


@mutation(SUBMIT_FILE, UPLOAD_GATE, "", expect="sent without its image")
def test_an_edit_uploads_its_image_first_and_a_failed_upload_is_a_retry_with_nothing_sent():
    log = []
    g = Gemini(log, send=I2I)
    result = run(g, Ctx(log), job("make the apple green", images=1))
    assert log.index("upload:start") < log.index("upload:upload, finalize") < log.index("mark_sent")
    content = inner_of(g.sends()[0])[0]
    assert content[3][0][0][0] == UPLOADED and content[3][0][0][3] == "image/png" and content[3][0][1] == "input.png"
    assert isinstance(result.job, Completed)
    for bad in ({"upload_url": None}, {"upload_body": "<html>error</html>"},
                {"upload_url": "https://evil.example/upload?x"}):
        log = []
        result = run(Gemini(log, send=I2I, **bad), Ctx(log), job("make the apple green", images=1))
        assert sent(log) == 0 and "mark_sent" not in log, "sent without its image: %s" % log
        assert result.job == Retry(errors.UPLOAD_FAILED), bad


# ---- the verdict of an answer -----------------------------------------------------------------------------------
ENDED = "    if not answer.clean or not answer.ended:\n"


@mutation(SUBMIT_FILE, ENDED, "    if not answer.clean:\n", expect="a cut answer")
@mutation(SUBMIT_FILE, ENDED, "    if not answer.ended:\n", expect="an unreadable answer")
@mutation(REPLY_FILE, '        ended = bool(frame) and all(isinstance(i, list) and i and i[0] == "e" for i in frame)',
          '        ended = True', expect="a cut answer")
def test_only_a_complete_answer_is_a_verdict_a_cut_or_unreadable_one_is_unknown():
    refused = answer("I can't help with that.")
    cases = {
        "a cut answer": answer("I can't help with that.", end=False),
        "a cut answer that held an image": answer("", image=True, end=False),
        "an unreadable answer": refused.replace('[["e"', '[["e"') + "{not json\n",
    }
    for why, text in cases.items():
        log = []
        result = run(Gemini(log, send=text), Ctx(log))
        assert isinstance(result.job, Ambiguous), "%s became %s" % (why, result.job)
        assert sent(log) == 1
    log = []
    assert isinstance(run(Gemini(log, send=refused), Ctx(log)).job, Fail)


STATUS = '    if status != 200 or answer is None:\n        return "unknown", "gemini answered %d" % status\n'


@mutation(SUBMIT_FILE, STATUS, "", expect="a status that is not 200")
def test_no_answer_a_timeout_or_a_status_that_is_not_200_is_unknown_and_never_sent_again():
    for out in (httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("cut"), httpx.ConnectError("gone"),
                httpx.Response(500, text=T2I), httpx.Response(429, text=answer("no")),
                httpx.Response(400, text="")):
        log = []
        result = run(Gemini(log, send=out), Ctx(log))
        assert isinstance(result.job, Ambiguous), "a status that is not 200 became %s (%r)" % (result.job, out)
        assert sent(log) == 1 and "mark_sent" in log
        assert any(isinstance(e, Rest) for e in result.account), "no pacing after a send"


PLACEHOLDER = "    if not final or IMAGE_PLACEHOLDER in final:\n"


@mutation(SUBMIT_FILE, PLACEHOLDER, "    if not final:\n", expect="an image promised")
@mutation(SUBMIT_FILE, PLACEHOLDER, "    if IMAGE_PLACEHOLDER in final:\n", expect="an empty answer")
@mutation(SUBMIT_FILE, '    if answer.errors:\n        return "unknown", "the answer carried an error item"\n', "",
          expect="an error item")
def test_an_answer_with_no_image_record_is_unknown_unless_its_final_text_is_a_plain_answer():
    cases = {
        "an image promised": answer("\n\nhttp://googleusercontent.com/image_generation_content/0_1\n\n"),
        "an empty answer": answer(""),
        "an error item": answer("I can't help with that.").replace('[["e"', '[["er",null,null,null,null,500],["e"'),
    }
    for why, text in cases.items():
        log = []
        assert isinstance(run(Gemini(log, send=text), Ctx(log)).job, Ambiguous), why


NOT_ENTITLED_LINE = "    if NOT_ENTITLED_LINK in final:\n"


@mutation(SUBMIT_FILE, NOT_ENTITLED_LINE, "    if False:\n", expect="not_entitled")
@mutation(SUBMIT_FILE, '    return "refused", (errors.REFUSED, final)', '    return "unknown", final',
          expect="a refusal")
def test_a_complete_answer_with_a_final_text_and_no_image_fails_with_its_refund_and_is_never_sent_again():
    log = []
    result = run(Gemini(log, send=answer("I can't create images of that person.")), Ctx(log))
    assert isinstance(result.job, Fail), "a refusal became %s" % result.job
    assert result.job.code == errors.REFUSED and "I can't create images of that person." in result.job.detail
    assert result.job.state_patch == {"chat": "c_00000000000000f1"} and sent(log) == 1
    rule = errors.CODES[errors.REFUSED]
    assert rule.refund and rule.terminal
    log = []
    result = run(Gemini(log, send=NOT_ENTITLED), Ctx(log))      # the recorded "upgrade your subscription" answer
    assert result.job.code == errors.NOT_ENTITLED, "not_entitled: %s" % result.job
    assert errors.CODES[errors.NOT_ENTITLED].refund and sent(log) == 1


def test_after_any_send_the_accounts_pool_rests_for_the_pacing_gap():
    for text in (T2I, answer("no"), answer("", end=False)):
        log = []
        result = run(Gemini(log, send=text), Ctx(log))
        rests = [e for e in result.account if isinstance(e, Rest)]
        assert rests == [Rest("image", NOW + timedelta(seconds=30), "gemini pacing between two sends")]
    log = []
    result = run(Gemini(log, send=T2I), Ctx(log), cfg=settings(gemini__account_min_gap_s=0))
    assert not any(isinstance(e, Rest) for e in result.account)


# ---- before the send ---------------------------------------------------------------------------------------------
SIGNED_OUT = "            if not page.signed_in:\n                return _result(Retry(LOGGED_OUT),"


@mutation(SUBMIT_FILE, SIGNED_OUT, "            if False:\n                return _result(Retry(LOGGED_OUT),",
          expect="sent while signed out")
def test_a_signed_out_page_an_error_page_or_no_proxy_route_is_a_retry_with_nothing_sent():
    log = []
    result = run(Gemini(log, signed_in=False, send=T2I), Ctx(log))
    assert sent(log) == 0 and "mark_sent" not in log, "sent while signed out: %s" % log
    assert result.job == Retry("pool.account_logged_out")
    assert [type(e) for e in result.account] == [Suspect, Cooldown]
    log = []
    assert run(Gemini(log, page_status=500, send=T2I), Ctx(log)).job == Retry(errors.HTTP_ERROR) and sent(log) == 0
    log = []
    g = Gemini(log, send=T2I)
    g.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(
        httpx.ConnectError("connect to http://user:pw-123@10.0.0.1:8080 failed")))
    result = run(g, Ctx(log))
    assert result.job == Retry("pool.proxy_failed")
    assert [type(e) for e in result.account] == [ProxyBad, Cooldown]
    assert "pw-123" not in repr(result)


def test_a_mark_sent_that_fails_sends_nothing():
    log = []
    ctx = Ctx(log)
    ctx.mark_error = RuntimeError("lease lost")
    with pytest.raises(RuntimeError, match="lease lost"):
        run(Gemini(log, send=T2I), ctx)
    assert sent(log) == 0


# ---- reconcile -------------------------------------------------------------------------------------------------------
def reconcile(g, j, ctx=None):
    return asyncio.run(R.reconcile(ctx or Ctx(g.log), j, settings(), Sessions(), transport=g.transport))


def test_a_job_whose_send_named_its_chat_is_found_by_reading_that_chat():
    log = []
    g = Gemini(log, rpc={"hNvQHb": READ})
    found = reconcile(g, job(state={"chat": T2I_CHAT, "request": "R1"}))
    assert found == Found(T2I_CHAT, {"chat": T2I_CHAT, "request": "R1"}, True)
    assert args_of(next(r for r in g.seen if "hNvQHb" in str(r.url)))[0] == T2I_CHAT
    assert "MaZiqc" not in log and "SEND" not in log
    g = Gemini([], rpc={"hNvQHb": '%s\n' % ")]}'"})
    assert reconcile(g, job(state={"chat": T2I_CHAT})) == StillUnknown(answered=False)


START = SENT_AT.timestamp()
WINDOW = "    near = sorted((c for c in listed if start - FIND_BEFORE_S <= c.started <= start + FIND_AFTER_S),"


@mutation(RESULTS_FILE, "        if turn is not None and same_text(turn.prompt, text):",
          "        if turn is not None:", expect="another chat was taken for the job")
@mutation(RESULTS_FILE, WINDOW, "    near = sorted((c for c in listed),", expect="a chat outside the send window")
def test_a_lost_send_is_found_by_its_exact_text_among_the_chats_started_near_it_never_another():
    other = "c_00000000000000b1"
    ours = "c_00000000000000b2"
    old = "c_00000000000000b3"

    def reads(request):
        cid = args_of(request)[0]
        prompt = {ours: T2I_PROMPT, other: "a different prompt", old: T2I_PROMPT}[cid]
        return chat_read(prompt, "", cid=cid)
    log = []
    g = Gemini(log, rpc={"MaZiqc": listing((old, START - 3600), (other, START + 1), (ours, START + 2)),
                         "hNvQHb": reads})
    found = reconcile(g, job())
    assert found == Found(ours, {"chat": ours, "request": None}, True), \
        "another chat was taken for the job: %s" % (found,)
    log = []
    g = Gemini(log, rpc={"MaZiqc": listing((old, START - 3600), (other, START + 1)), "hNvQHb": reads})
    got = reconcile(g, job())
    assert got == StillUnknown(answered=True), "a chat outside the send window was taken: %s" % (got,)
    assert "SEND" not in log


def test_a_listing_with_none_of_ours_or_that_cannot_be_read_is_still_unknown_never_not_sent():
    g = Gemini([], rpc={"MaZiqc": LISTING})
    assert reconcile(g, job()) == StillUnknown(answered=True)
    for bad in ('%s\n' % ")]}'", httpx.Response(500, text="x"), LISTING.replace('[["e"', '[["x"')):
        assert reconcile(Gemini([], rpc={"MaZiqc": bad}), job()) == StillUnknown(answered=False), bad
    assert reconcile(Gemini([], signed_in=False), job()) == StillUnknown()
    assert reconcile(Gemini([], rpc={"MaZiqc": LISTING}), job(sent_at=None)) == StillUnknown(answered=False)
    g = Gemini([])
    g.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))
    assert reconcile(g, job()) == StillUnknown()


# ---- poll ------------------------------------------------------------------------------------------------------------
def poll(g, j):
    return asyncio.run(R.poll(Ctx(g.log), j, settings(), Sessions(), transport=g.transport, rand=lambda: 0.0))


@mutation(RESULTS_FILE, '    if verdict == "refused":\n        code, text = what\n        return PollResult(Failed(',
          '    if False:\n        code, text = what\n        return PollResult(Failed(', expect="a refused chat")
def test_poll_reads_the_found_chat_an_image_is_done_a_plain_answer_fails_anything_else_waits():
    state = {"chat": T2I_CHAT}
    done = poll(Gemini([], rpc={"hNvQHb": READ}), job(state=state))
    assert done.outcome == Done((Output(READ_LINK, "image/png", 0, "watermarked_img_10815355836771573014.png",
                                        {"chat": T2I_CHAT, "width": 1408, "height": 768}),))
    refused = poll(Gemini([], rpc={"hNvQHb": chat_read(T2I_PROMPT, "I can't make that.", image=False)}),
                   job(state=state))
    assert isinstance(refused.outcome, Failed), "a refused chat must fail: %s" % refused.outcome
    assert refused.outcome.code == errors.REFUSED
    waiting = poll(Gemini([], rpc={"hNvQHb": chat_read(T2I_PROMPT, "", image=False)}), job(state=state))
    assert isinstance(waiting.outcome, Pending) and waiting.outcome.next_in_s == 30
    assert poll(Gemini([]), job()).outcome.answered is False


# ---- the reply reader on the recorded answers ---------------------------------------------------------------------
def test_the_recorded_answers_read_as_measured():
    a = read_answer(T2I)
    assert (a.clean, a.ended, a.cid) == (True, True, T2I_CHAT)
    assert [(i.width, i.height, i.size) for i in a.images] == [(1408, 768, 1178053)]
    b = read_answer(I2I)
    assert [(i.width, i.height, i.size) for i in b.images] == [(1408, 768, 1410987)]
    c = read_answer(NOT_ENTITLED)
    assert c.images == () and "upgrade your subscription" in c.texts[-1]


# ---- fetch -----------------------------------------------------------------------------------------------------------
def drain(g, ref, site, proxy=None):
    async def go():
        return b"".join([p async for p in R.fetch(Ctx(g.log, proxy=proxy), Output(ref, "image/png", 0), settings(),
                                                  Sessions(), transport=g.transport, media_transport=site,
                                                  media_resolver=public_resolver)])
    return asyncio.run(go())


HOP_GATE = "            if parts.scheme != \"https\" or parts.hostname not in LINK_HOSTS:"


@mutation(HTTP_FILE, HOP_GATE, "            if False:", expect="a hop off the link hosts")
def test_fetch_resolves_the_link_with_the_session_then_downloads_the_bytes_through_the_account_proxy():
    log = []
    g = Gemini(log)
    site = MediaSite(b"direct", PNG)
    got = drain(g, T2I_LINK, site, Proxy("10.0.0.1", 8080, "user:pw"))
    assert got == PNG and site.routes == ["http://user:pw@10.0.0.1:8080"], site.routes
    first = next(r for r in g.seen if r.url.host == "lh3.googleusercontent.com")
    assert str(first.url) == T2I_LINK + "=d-I?alr=yes"
    assert "cookie" not in first.headers, "a session cookie went to lh3.googleusercontent.com"
    hop = next(r for r in g.seen if r.url.host == "lh3.google.com")
    assert "__Secure-1PSID=%s" % PSID in hop.headers["cookie"], "the google.com hop had no session"
    log = []
    g = Gemini(log, chain={"lh3.googleusercontent.com": (200, "text/plain", "https://evil.example/x")})
    with pytest.raises(RuntimeError, match="did not resolve"):
        drain(g, READ_LINK, MediaSite(PNG))
    assert not any(r.url.host == "evil.example" for r in g.seen), "a hop off the link hosts was followed"
