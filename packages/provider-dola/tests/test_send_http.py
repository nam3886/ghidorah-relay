"""The plain-HTTP send (`dola.send_path` = http): its money rules, against the 23/10 spike's recorded streams and an
in-process dola (no socket, no browser). The signer is the real one (`relay_dola.signing`)."""

import asyncio
import datetime as dt
import json
import random
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest
from kit_b import Settings
from kit_c import FakeCtx, make_job, sync

from relay_browser.session import BrowserLaunchError
from relay_core.providers import (
    AccountView,
    Ambiguous,
    Completed,
    Cooldown,
    Fail,
    Found,
    NotSent,
    NotTaken,
    Retry,
    StillUnknown,
)
from relay_core.testing.mutation import mutation
from relay_dola import errors, signing
from relay_dola.engine import reconcile as rec
from relay_dola.engine import send_http
from relay_dola.engine.http import DolaHttp
from relay_dola.engine.submit import Deps, submit

SPIKE = Path(__file__).resolve().parents[3] / "fixtures" / "dola" / "api-spike-261023"
SEND = "packages/provider-dola/src/relay_dola/engine/send_http.py"
SUBMIT = "packages/provider-dola/src/relay_dola/engine/submit.py"
RECONCILE = "packages/provider-dola/src/relay_dola/engine/reconcile.py"
IMAGE_CONV, VIDEO_CONV = "38418214902690065", "38418288710690833"
WEB_ID, BOT_ID = "7000000000000000001", "7339470600000000001"
PAGE = '<script>{"9al":"bdms-sdk"}{"9al":"a68c472ba3"}</script>'      # the webpack runtime naming today's bdms
HTTP_ON = Settings({"dola.send_path": "http"})


def sse(name: str) -> list[str]:
    return [line for _t, line in json.loads((SPIKE / name).read_text(encoding="utf-8"))]


def chain(name: str) -> dict:
    d = json.loads((SPIKE / name).read_text(encoding="utf-8"))
    r = d.get("response", d) if "downlink_body" not in d else d
    return json.loads(r) if isinstance(r, str) else r


IMAGE_SSE, VIDEO_SSE = sse("send_sse.json"), sse("video_sse.json")
BADSIG_SSE, NOSIG_SSE, REPLAY_SSE = sse("badsig_sse.json"), sse("nosig_sse.json"), sse("replay_sse.json")
IMAGE_TEXT = json.loads((SPIKE / "send_request.json").read_text())["write_ahead"]["text"].removeprefix(
    "Generate image: ")         # the job's sent text: dola's page puts "Generate image: " in front of it
VIDEO_TEXT = json.loads((SPIKE / "video_request.json").read_text())["write_ahead"]["text"]
THROTTLE_SSE = [line.replace(',"decision":', ',"x":') for line in NOSIG_SSE]     # 710022004 with no verify decision


def account(id: str = "acct-1") -> AccountView:
    cookies = [{"name": n, "value": "%s-FAKE" % n, "domain": ".dola.com", "path": "/"}
               for n in ("sessionid", "uid_tt", "s_v_web_id")]
    return AccountView(id, "acct", json.dumps({"cookies": cookies}).encode(), 1, None, {}, {"video": 4, "image": None})


class Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content = status, body, content

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class Dola:
    """In-process dola. `stream`: the lines of the send's SSE answer; `drop`: raised after them (a cut stream)."""

    def __init__(self, log: list, *, stream=IMAGE_SSE, drop: Exception | None = None, chains=None, listing=None,
                 passport="success", page=PAGE, unreachable=False):
        self.log, self.stream, self.drop = log, stream, drop
        self.chains = chains if chains is not None else {IMAGE_CONV: chain("chain_after_send.json"),
                                                         VIDEO_CONV: chain("video_chain_final.json")}
        self.listing, self.passport, self.page, self.unreachable = listing, passport, page, unreachable
        self.sends: list[tuple[str, bytes, dict]] = []
        self.launches = 0

    def post_data(self, url, *, data, headers, timeout, proxies, lines=None):
        path = urlsplit(url).path
        if path == "/alice/user/get_web_anon_id":
            return Resp(200, {"code": 0, "web_id": WEB_ID})
        if path == "/alice/user/launch":
            self.launches += 1
            return Resp(200, {"code": 0, "data": {"assistant_bot_id": BOT_ID}})
        assert path == "/chat/completion", path
        self.log.append("POST")
        self.sends.append((url, data, dict(headers)))
        lines.extend(self.stream)
        if self.drop is not None:
            raise self.drop
        return Resp(200)

    def get(self, url, *, headers, timeout, proxies):
        if self.unreachable:
            raise OSError("curl: (7) could not connect")
        if "/passport/account/info/v2/" in url:
            return Resp(200, {"message": self.passport, "data": {"user_id_str": "1"} if self.passport == "success"
                              else {"name": "account_info_error"}})
        assert url.endswith("/chat"), url
        return Resp(200, None, self.page.encode())

    def post(self, url, *, params, headers, json, timeout, proxies):
        if url.endswith("/im/chain/recent_conv"):
            return Resp(200, self.listing)
        conv = json["uplink_body"]["pull_singe_chain_uplink_body"]["conversation_id"]
        return Resp(200, self.chains.get(conv, {"status_code": 712017001}))


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


async def _nosleep(_s):
    return None


def deps_for(dola: Dola, clock=None) -> Deps:
    return Deps(http=lambda acc: DolaHttp.for_account(acc, dola),
                signer=lambda ua: signing.Signer(signing.Fingerprint(ua), rng=random.Random(7)),
                sleep=_nosleep, poll_s=0, image_poll_s=0, image_hold_s=0, clock=clock or Clock(),
                now=lambda: dt.datetime(2026, 10, 23, 7, 0, tzinfo=dt.UTC))


def image_job(**kw):
    return make_job("image", ratio=None, sent_text=IMAGE_TEXT, **kw)


def video_job(**kw):
    return make_job("video", duration=5, ratio="16:9", model="seedance_v2.0", sent_text=VIDEO_TEXT, **kw)


async def run(dola: Dola, job=None, *, acct=None, clock=None, settings=HTTP_ON):
    ctx = FakeCtx(dola.log, acct or account())
    result = await submit(ctx, job or image_job(), settings, deps=deps_for(dola, clock))
    return ctx, result


@pytest.fixture(autouse=True)
def fresh_state():
    send_http._SESSIONS.clear()
    send_http._LOCKS.clear()
    send_http.ALARM.__init__()
    yield


def sent_body(dola: Dola) -> dict:
    return json.loads(dola.sends[0][1])


# ---- the write-ahead and the one POST ------------------------------------------------------------------------------
@mutation(SEND, "        await self._core(lambda: ctx.report_ref(None, {SEND_PATH_KEY: \"http\", **ids}))\n"
          "        await self._mark_sent()\n",
          "        await self._mark_sent()\n"
          "        await self._core(lambda: ctx.report_ref(None, {SEND_PATH_KEY: \"http\", **ids}))\n",
          expect="ids before mark_sent")
@sync
async def test_the_client_ids_are_on_the_job_before_mark_sent_and_are_the_ids_sent():
    log: list = []
    dola = Dola(log)
    ctx, _ = await run(dola)
    assert log.index("report_ref") < log.index("mark_sent"), "ids before mark_sent"
    ref, state = ctx.refs[0]
    assert ref is None and state["send_path"] == "http"
    body = sent_body(dola)
    assert state["local_message_id"] == body["messages"][0]["local_message_id"]
    assert state["local_conversation_id"] == body["client_meta"]["local_conversation_id"]
    assert state["unique_key"] == body["option"]["unique_key"]
    assert state["collect_id"] == body["option"]["collect_id"] == body["ext"]["collection_id"]
    assert state["local_conversation_id"].startswith("local_") and len(state["local_conversation_id"]) == 22


@mutation(SEND, "        await self._mark_sent()\n        lines: list[str] = []\n", "        lines: list[str] = []\n",
          expect="mark_sent before the POST")
@sync
async def test_mark_sent_runs_immediately_before_the_one_post():
    log: list = []
    await run(Dola(log))
    assert "mark_sent" in log and log.index("mark_sent") == log.index("POST") - 1, "mark_sent before the POST"
    assert log.count("POST") == 1


@mutation(SEND, "        return judged, None\n", "        return await super()._gate(conversation, images, judged)\n",
          expect="no re-send")
@sync
async def test_a_question_is_never_answered_by_a_second_post():
    log: list = []
    asked = chain("video_chain_final.json")
    msgs = asked["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]
    for m in msgs:
        if m.get("user_type") == 2:
            m["content"] = json.dumps([{"block_type": 10000, "content": {"text_block": {
                "text": "Video generation currently supports durations from 4 to 15 seconds. Do you want to proceed "
                        "with 15 seconds?"}}}])
            m["content_block"] = []
            m["ext"] = {"is_finish": "1"}
    dola = Dola(log, stream=VIDEO_SSE, chains={VIDEO_CONV: asked})
    _, result = await run(dola, video_job())
    assert log.count("POST") == 1, "no re-send"
    assert type(result.job).__name__ == "Accepted" and result.job.taken is False, "no re-send: %r" % (result.job,)


# ---- what the stream says --------------------------------------------------------------------------------------------
@mutation(SEND, '            if cid.isdigit() and cid != "0":', "            if False:", expect="the ack is taken")
@sync
async def test_an_ack_is_the_order_taken_and_images_finish_in_the_session():
    log: list = []
    ctx, result = await run(Dola(log))
    assert isinstance(result.job, Completed), "the ack is taken: %r" % (result.job,)
    assert ctx.refs[-1] == (IMAGE_CONV, {})
    assert len(result.job.outputs) == 4


@sync
async def test_a_video_ack_keeps_the_conversation_for_the_poll():
    log: list = []
    ctx, result = await run(Dola(log, stream=VIDEO_SSE), video_job())
    assert type(result.job).__name__ == "Accepted" and result.job.ref == VIDEO_CONV
    assert (VIDEO_CONV, {}) in ctx.refs and log.count("POST") == 1


@mutation(SEND, "            if stream.early is not None:", "            if False:", expect="not taken")
@sync
async def test_an_error_before_any_ack_is_not_taken():
    log: list = []
    _, refused = await run(Dola(log, stream=BADSIG_SSE))
    assert isinstance(refused.job, Fail) and refused.job.code == errors.REFUSED_710022002, "not taken"
    _, throttled = await run(Dola(log, stream=THROTTLE_SSE), acct=account("acct-2"))
    assert isinstance(throttled.job, NotTaken) and throttled.job.code == "pool.account_throttled", "not taken"
    assert any(isinstance(e, Cooldown) for e in throttled.account)
    assert log.count("POST") == 2


@mutation(SEND, "        if stream.conversation is None:\n",
          "        if stream.conversation is None or failure:\n", expect="taken despite the drop")
@sync
async def test_a_drop_before_the_ack_is_unknown_and_after_it_is_still_taken():
    log: list = []
    _, before = await run(Dola(log, stream=IMAGE_SSE[:4], drop=TimeoutError("cut")))
    assert isinstance(before.job, Ambiguous)
    ack_end = next(i for i, line in enumerate(IMAGE_SSE) if line.startswith("data:") and "ack_client_meta" in line)
    _, after = await run(Dola(log, stream=IMAGE_SSE[:ack_end + 2], drop=TimeoutError("cut")))
    assert isinstance(after.job, Completed), "taken despite the drop: %r" % (after.job,)
    assert log.count("POST") == 2


@sync
async def test_nothing_at_all_after_the_send_is_unknown_and_rests_the_route():
    log: list = []
    _, result = await run(Dola(log, stream=[], drop=OSError("curl: (56)")))
    assert isinstance(result.job, Ambiguous)
    assert any(type(e).__name__ == "ProxyBad" for e in result.account)


@sync
async def test_a_replay_answer_is_unknown_never_a_second_post():
    log: list = []
    _, result = await run(Dola(log, stream=REPLAY_SSE))
    assert isinstance(result.job, Ambiguous) and log.count("POST") == 1


# ---- the signer alarm ------------------------------------------------------------------------------------------------
@mutation(SEND, "        elif len(self.refused) >= accounts:", "        elif False:", expect="signer suspect")
@sync
async def test_refusals_on_several_accounts_blame_the_signer_rest_no_account_and_pause_http_sends():
    log: list = []
    clock = Clock()
    first = [await run(Dola(log, stream=BADSIG_SSE), acct=account("a%d" % i), clock=clock) for i in (1, 2)]
    assert all(isinstance(r.job, Fail) for _, r in first)
    _, third = await run(Dola(log, stream=BADSIG_SSE), acct=account("a3"), clock=clock)
    assert isinstance(third.job, NotTaken) and third.job.code == errors.SIGNER_SUSPECT, "signer suspect"
    assert not any(isinstance(e, Cooldown) for e in third.account)
    before = len(log)
    _, paused = await run(Dola(log), acct=account("a4"), clock=clock)
    assert isinstance(paused.job, Retry) and paused.job.code == errors.SIGNER_SUSPECT
    assert "mark_sent" not in log[before:] and "POST" not in log[before:]
    clock.t += 900
    _, later = await run(Dola(log), acct=account("a4"), clock=clock)
    assert isinstance(later.job, Completed)


@sync
async def test_a_captcha_decision_blames_the_signer_at_once_and_an_ack_clears_the_count():
    log: list = []
    _, result = await run(Dola(log, stream=NOSIG_SSE))
    assert isinstance(result.job, NotTaken) and result.job.code == errors.SIGNER_SUSPECT
    assert not any(isinstance(e, Cooldown) for e in result.account), "no account rests"
    send_http.ALARM.__init__()
    clock = Clock()
    await run(Dola(log, stream=BADSIG_SSE), acct=account("b1"), clock=clock)
    await run(Dola(log, stream=BADSIG_SSE), acct=account("b2"), clock=clock)
    await run(Dola(log), acct=account("b3"), clock=clock)          # an acknowledged send: the signer works
    _, again = await run(Dola(log, stream=BADSIG_SSE), acct=account("b4"), clock=clock)
    assert isinstance(again.job, Fail) and again.job.code == errors.REFUSED_710022002


@sync
async def test_a_new_bdms_sdk_on_the_page_pauses_http_sends_before_anything_is_sent():
    log: list = []
    _, result = await run(Dola(log, page=PAGE.replace("a68c472ba3", "0123456789")))
    assert isinstance(result.job, Retry) and result.job.code == errors.SIGNER_SUSPECT
    assert "POST" not in log and "mark_sent" not in log
    assert send_http.ALARM.paused_until > 0


# ---- the signature: the bytes signed are the bytes sent, never a first signature ------------------------------------
@mutation(SEND, "    query = signer.signed_query(base_query(web_id), body)\n",
          "    query = urlencode(base_query(web_id))\n", expect="detect 8")
@sync
async def test_the_send_is_signed_over_the_exact_bytes_sent_with_the_ua_sent_and_never_first():
    log: list = []
    dola = Dola(log)
    await run(dola)
    url, data, headers = dola.sends[0]
    query, _, ab = urlsplit(url).query.rpartition("&a_bogus=")
    ab = unquote(ab)
    assert signing.verify(ab, query, data, headers["user-agent"]), "the signature binds what was sent"
    assert headers["user-agent"] == send_http.USER_AGENT
    assert signing.decode(ab, user_agent=headers["user-agent"]).detect == 8, "detect 8"
    keys = [k for k, _ in parse_qsl(query, keep_blank_values=True)]
    assert keys == [k for k, _ in send_http.send_query("1")], "the page's query order"
    assert json.loads(data) == sent_body(dola) and data == json.dumps(json.loads(data), ensure_ascii=False,
                                                                       separators=(",", ":")).encode()


@mutation(SEND, "    async with _LOCKS.setdefault(key, asyncio.Lock()):", "    async with asyncio.Lock():",
          expect="one bootstrap")
@sync
async def test_two_jobs_of_one_account_at_once_share_one_session_and_both_send_with_detect_8():
    log: list = []
    dola = Dola(log)
    ctx_a, ctx_b = FakeCtx(log, account()), FakeCtx(log, account())
    await asyncio.gather(submit(ctx_a, image_job(), HTTP_ON, deps=deps_for(dola)),
                         submit(ctx_b, image_job(), HTTP_ON, deps=deps_for(dola)))
    assert dola.launches == 1, "one bootstrap"
    for url, _data, headers in dola.sends:
        ab = unquote(urlsplit(url).query.rpartition("&a_bogus=")[2])
        assert signing.decode(ab, user_agent=headers["user-agent"]).detect == 8


# ---- the body ------------------------------------------------------------------------------------------------------
def test_the_body_matches_the_spike_shape_for_image_and_video():
    ids = send_http.new_ids(random.Random(1))
    from relay_dola.engine.submit import order_spec
    img = json.loads(send_http.build_body(order_spec(image_job()), ids, BOT_ID, "fp", 1))
    want = json.loads((SPIKE / "send_request.json").read_text())["body"]
    assert img.keys() == want.keys() and img["option"].keys() == want["option"].keys()
    assert img["ext"].keys() == want["ext"].keys() and img["client_meta"].keys() == want["client_meta"].keys()
    assert img["chat_ability"] == want["chat_ability"] and img["messages"][0]["content_block"][0]["content"] == \
        want["messages"][0]["content_block"][0]["content"]
    vid = json.loads(send_http.build_body(order_spec(video_job()), ids, BOT_ID, "fp", 1))
    want_v = json.loads((SPIKE / "video_request.json").read_text())["body"]
    assert vid["chat_ability"] == want_v["chat_ability"]


def test_unproven_parameters_are_sent_as_dolas_config_names_them():
    from relay_dola.engine.submit import order_spec
    pro = order_spec(make_job("image", ratio=None, model="seedream-5.0-pro"))
    inner = json.loads(send_http.chat_ability(pro)[0]["ability_param"])["ability_param"]
    assert inner["model"] == "Seedream 5.0 Pro"            # UNPROVEN over HTTP: the config's option key
    long = order_spec(make_job("video", duration=30, ratio="16:9"))
    # UNPROVEN over HTTP: 30 s on 2.5 (proven on the browser path, with the action-bar patch)
    assert json.loads(send_http.chat_ability(long)[0]["ability_param"])["duration"] == 30


# ---- the setting ---------------------------------------------------------------------------------------------------
@mutation(SEND, 'return "http" if settings is not None and settings.get(SEND_PATH_SETTING) == "http" else "browser"',
          'return "browser" if settings is not None and settings.get(SEND_PATH_SETTING) == "browser" else "http"',
          expect="the browser by default")
@sync
async def test_the_setting_picks_the_path_per_job_and_reference_images_stay_on_the_browser():
    def no_browser(*_a, **_k):
        raise BrowserLaunchError("no chrome in this test")

    for settings, job in ((Settings({}), image_job()), (Settings({"dola.send_path": "browser"}), image_job()),
                          (HTTP_ON, replace(video_job(), operation="image_to_video")),
                          (HTTP_ON, make_job("image", ratio="16:9")), (HTTP_ON, make_job("image", ratio=None,
                                                                                          style="anime")),
                          (HTTP_ON, make_job("video", duration=5, ratio="9:16", model="seedance_v2.0"))):
        log: list = []
        dola = Dola(log)
        deps = deps_for(dola)
        deps.open_session = no_browser
        try:
            await submit(FakeCtx(log, account()), job, settings, deps=deps)
        except BrowserLaunchError:
            pass                    # the browser path was taken: it could not start Chrome here
        assert dola.sends == [], "the browser by default"
        assert "mark_sent" not in log, "the browser by default"
    log = []
    dola = Dola(log)
    await run(dola)
    assert len(dola.sends) == 1


# ---- before the send -------------------------------------------------------------------------------------------------
@sync
async def test_before_the_send_a_guest_or_a_dead_route_is_an_ordinary_retry():
    log: list = []
    _, guest = await run(Dola(log, passport="error"))
    assert isinstance(guest.job, Retry) and guest.job.code == "pool.account_logged_out"
    _, dead = await run(Dola(log, unreachable=True))
    assert isinstance(dead.job, Retry) and dead.job.code == "pool.proxy_failed"
    assert "mark_sent" not in log and "POST" not in log


# ---- reconcile by local_message_id -----------------------------------------------------------------------------------
LOCAL_ID = "850f5333-81dc-4ffa-8c02-1e68b211bc7d"       # the user message of chain_after_send.json carries it


def http_job(local_id=LOCAL_ID):
    job = make_job("image", sent_text=IMAGE_TEXT, provider_state={"send_path": "http", "local_message_id": local_id})
    return replace(job, sent_at=dt.datetime(2026, 10, 23, 6, 34, tzinfo=dt.UTC))


def listing(*ids, more=False):
    return {"status_code": 0, "downlink_body": {"pull_recent_conv_chain_downlink_body": {
        "has_more": more, "cells": [{"conversation": {"conversation_id": i}} for i in ids]}}}


class RCtx:
    def __init__(self):
        self.account = account()


@mutation(RECONCILE, '    if any(str(m.get("local_message_id") or "") == local_message_id for m in messages):',
          '    if any(str(m.get("local_message_id") or "") != local_message_id for m in messages):',
          expect="found by its id")
@sync
async def test_reconcile_finds_the_http_send_by_its_local_message_id_over_http():
    dola = Dola([], listing=listing(VIDEO_CONV, IMAGE_CONV))
    got = await rec.reconcile(RCtx(), http_job(), None, http=DolaHttp.for_account(account(), dola))
    assert isinstance(got, Found) and got.ref == IMAGE_CONV and got.taken, "found by its id"
    gone = await rec.reconcile(RCtx(), http_job("00000000-0000-4000-8000-000000000000"), None,
                               http=DolaHttp.for_account(account(), dola))
    assert isinstance(gone, NotSent) and gone.covers_send_window, "found by its id"


@mutation(RECONCILE, "    if first > 1:\n        return Reading(None)", "    if False:\n        return Reading(None)",
          expect="cannot rule it out")
@sync
async def test_reconcile_proves_nothing_from_a_read_that_does_not_reach_the_first_message_or_a_failed_list():
    cut = chain("video_chain_final.json")
    for m in cut["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]:
        m["index_in_conv"] = str(int(m["index_in_conv"]) + 20)
    dola = Dola([], chains={VIDEO_CONV: cut}, listing=listing(VIDEO_CONV))
    got = await rec.reconcile(RCtx(), http_job(), None, http=DolaHttp.for_account(account(), dola))
    assert isinstance(got, StillUnknown), "cannot rule it out: %r" % (got,)
    failed = Dola([], listing={"status_code": 712012001})
    assert isinstance(await rec.reconcile(RCtx(), http_job(), None, http=DolaHttp.for_account(account(), failed)),
                      StillUnknown)
