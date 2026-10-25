"""Submit, reconcile, poll and fetch over a fake chat.qwen.ai (no network), fed qwen's recorded answers from
fixtures/qwen/spike-261023. Each money or safety rule has a test that fails when the rule breaks, and a mutation that
proves it."""

import asyncio
import dataclasses
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from qwenkit import (
    ACCESS,
    BLOB,
    BX_UA,
    CHAT,
    CHAT_DROPPED,
    CHAT_EMPTY,
    CHAT_IMAGE,
    CHAT_VIDEO,
    FILE_ID,
    I2V_TASK,
    IMAGE_LINK,
    NO_CREDITS,
    NOW,
    PAIR,
    REFRESH,
    STS_SECRET,
    STS_TOKEN,
    TASK,
    TASK_RUNNING,
    TASK_SUCCESS,
    UMID,
    VERIFY_EMPTY,
    VIDEO_LINK,
    VIDEO_TASK,
    WAF,
    WAF_REC,
    Ctx,
    CutStream,
    Qwen,
    cut_after,
    job,
    minted_blob,
    minter,
    read_media,
    settings,
    sts_ok,
    waf_block,
)

from relay_core.net import UnsafeUrl
from relay_core.providers import (
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Done,
    Facts,
    Fail,
    Failed,
    Found,
    NotSent,
    NotTaken,
    Output,
    Pending,
    Proxy,
    ProxyBad,
    Retry,
    StillUnknown,
    Suspect,
)
from relay_core.testing.fake_media import MediaSite, public_resolver
from relay_core.testing.mutation import mutation
from relay_qwen import errors
from relay_qwen.credential import cred_of
from relay_qwen.engine import results as R
from relay_qwen.engine.http import Sessions
from relay_qwen.engine.submit import submit

SUBMIT_FILE = "packages/provider-qwen/src/relay_qwen/engine/submit.py"
RESULTS_FILE = "packages/provider-qwen/src/relay_qwen/engine/results.py"
NOW_DT = datetime.fromtimestamp(NOW, UTC)
VIDEO = "qwen-happyhorse-1.1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000


def run_submit(q: Qwen, ctx: Ctx, the_job=None, cfg=None, m=None):
    return asyncio.run(submit(ctx, the_job or job(), cfg or settings(), Sessions(), m or minter(ctx.log),
                              transport=q.transport, read_media=read_media, now=lambda: NOW_DT, clock=lambda: NOW))


def sent_body(q: Qwen) -> dict:
    (only,) = q.calls("send")
    return json.loads(only.content)


def stream(*deltas: dict) -> str:
    """An image answer: `response.created`, then one line per delta."""
    lines = ['data: {"response.created": {"chat_id": "%s", "response_id": "r1"}}' % CHAT]
    lines += ["data: " + json.dumps({"choices": [{"delta": d}], "response_id": "r1"}) for d in deltas]
    return "\n\n".join(lines) + "\n\n"


# ---- submit: the write-ahead and the one POST ---------------------------------------------------------------------
WRITE_AHEAD = "        await ctx.report_ref(chat, {CHAT_KEY: chat})       # write-ahead: the chat is on the job first\n"
MARK = "        await ctx.mark_sent()                               # then marked sent, immediately before the POST\n"


@mutation(SUBMIT_FILE, WRITE_AHEAD, "", expect="the chat was not on the job before the paid POST")
@mutation(SUBMIT_FILE, MARK, "", expect="not marked sent right before the paid POST")
def test_the_chat_is_written_ahead_and_marked_sent_right_before_the_one_paid_post():
    log = []
    q = Qwen(log)
    ctx = Ctx(log)
    result = run_submit(q, ctx)
    assert "report_ref" in log and log.index("report_ref") < log.index("send"), \
        "the chat was not on the job before the paid POST: %s" % log
    assert log[log.index("send") - 1] == "mark_sent", "not marked sent right before the paid POST: %s" % log
    assert log.count("send") == 1 and log.index("new_chat") < log.index("report_ref")
    assert ctx.refs == [(CHAT, {"chat": CHAT})]
    assert isinstance(result.job, Completed) and [o.ref for o in result.job.outputs] == [IMAGE_LINK]
    assert result.job.outputs[0].mime == "image/png"


@mutation(SUBMIT_FILE, '        headers = site.headers("/c/" + chat, bx=True)',
          '        headers = site.headers("/c/" + chat)', expect="the paid call went without the pair")
def test_the_image_request_is_the_web_apps_t2i_and_only_the_paid_call_carries_the_pair():
    log = []
    q = Qwen(log)
    run_submit(q, Ctx(log), job(ratio="3:4", sent="the framed words"))
    body = sent_body(q)
    msg = body["messages"][0]
    assert (body["stream"], body["chat_id"], body["chatId"], body["model"], body["size"]) == (
        True, CHAT, CHAT, "qwen3.7-plus", "3:4")
    assert (msg["chat_type"], msg["sub_chat_type"], msg["content"], msg["files"]) == ("t2i", "t2i",
                                                                                      "the framed words", [])
    assert msg["extra"] == {"meta": {"subChatType": "t2i", "size": "3:4", "model": "qwen-image-3.0"}}
    (send,) = q.calls("send")
    assert send.headers.get("bx-ua") == BX_UA and send.headers.get("bx-umidtoken") == UMID, \
        "the paid call went without the pair"
    assert send.headers["user-agent"] == PAIR["user-agent"] and send.headers["authorization"] == "Bearer " + ACCESS
    assert send.url.params["chat_id"] == CHAT
    for other in q.calls("new_chat") + q.calls("refresh"):
        assert "bx-ua" not in other.headers and "bx-umidtoken" not in other.headers
    assert "authorization" not in q.calls("refresh")[0].headers


def test_a_job_queued_before_frames_sends_its_bare_words():
    log = []
    q = Qwen(log)
    run_submit(q, Ctx(log), job(prompt="bare words", sent="framed words", frame_id=None))
    assert sent_body(q)["messages"][0]["content"] == "bare words"


QWEN_COST = '    if quota.get("state") == "deducted" and type(amount) is int and amount > 0:'


@mutation(SUBMIT_FILE, QWEN_COST, "    if False:", expect="the credits qwen says it took")
def test_a_video_checks_its_credits_first_and_is_accepted_with_its_task():
    log = []
    q = Qwen(log, send=VIDEO_TASK)
    result = run_submit(q, Ctx(log), job(VIDEO))
    assert log.index("verify") < log.index("new_chat") < log.index("mark_sent") < log.index("send")
    assert json.loads(q.calls("verify")[0].content) == {"chat_type": "t2v", "task_info": {"create_credits": 30}}
    assert result.job.facts == Facts(30, cost_source="reply"), \
        "the credits qwen says it took (quota.consume_amount 30, deducted): %s" % (result.job.facts,)
    assert result.job == Accepted("tsk_6993c8657bf4a05bc4593e3eca35a1f7",
                                  {"chat": CHAT, "task": "tsk_6993c8657bf4a05bc4593e3eca35a1f7"},
                                  Facts(30, cost_source="reply"), taken=True)
    body = sent_body(q)
    msg = body["messages"][0]
    assert body["stream"] is False and (msg["chat_type"], msg["sub_chat_type"]) == ("t2v", "zaodian_generate_video")
    meta = msg["extra"]["meta"]
    assert (meta["size"], meta["videoGenerationMode"]) == ("16:9", "standard")
    assert json.loads(meta["biz_data"]) == {"bizScene": "genVideo", "req": {
        "rootModel": "happyhorse11", "genMode": "vid_gen",
        "params": {"resolution": "720P", "size": "16:9", "duration": 5, "audio": True}}}


@mutation(SUBMIT_FILE, QWEN_COST, '    if type(amount) is int and amount > 0:', expect="not deducted")
@mutation(SUBMIT_FILE, QWEN_COST, '    if quota.get("state") == "deducted" and type(amount) is int:',
          expect="an actual cost is a whole number above 0")
def test_qwen_states_a_cost_only_when_the_quota_says_it_was_deducted():
    from relay_qwen.engine.submit import stated_cost
    assert stated_cost(json.loads(VIDEO_TASK)) == stated_cost(json.loads(I2V_TASK)) == Facts(30, cost_source="reply")
    for change, why in ((lambda q: q.update(state="pending"), "not deducted"),
                        (lambda q: q.update(consume_amount="30"), "not a number"),
                        (lambda q: q.update(consume_amount=True), "a boolean"),
                        (lambda q: q.update(consume_amount=0), "zero: nothing taken"),
                        (lambda q: q.pop("consume_amount"), "no amount")):
        body = json.loads(VIDEO_TASK)
        change(body["data"]["messages"][0]["extra"]["quota"])
        assert stated_cost(body) == Facts(), why
    assert stated_cost(None) == stated_cost({}) == Facts(), "no answer states nothing"


ATTACH = '            params["attachments"] = [{"type": "image", "materialId": f["id"]} for f in files]\n'


@mutation(SUBMIT_FILE, ATTACH, "            pass\n", expect="attachments")
def test_an_image_to_video_uploads_its_frame_first_and_names_it_by_its_file_id():
    log = []
    q = Qwen(log, send=I2V_TASK)
    result = run_submit(q, Ctx(log), job("qwen-happyhorse-1.0", image=True))
    assert isinstance(result.job, Accepted)
    assert log.index("sts") < log.index("put") < log.index("new_chat") < log.index("mark_sent")
    assert json.loads(q.calls("sts")[0].content) == {"filename": "input.jpg", "filesize": "13", "filetype": "image"}
    (put,) = q.calls("put")
    assert str(put.url) == "https://qwen-webui-prod.oss-accelerate.aliyuncs.com/u1/%s_input.jpg" % FILE_ID
    assert put.content == b"\xff\xd8\xffJPEG-bytes" and put.headers["x-oss-security-token"] == STS_TOKEN
    assert put.headers["authorization"].startswith("OSS4-HMAC-SHA256 Credential=STS.PLANTEDKEYID/")
    assert "/ap-southeast-1/oss/aliyun_v4_request,Signature=" in put.headers["authorization"]
    assert "cookie" not in put.headers, "the jar went to the object store"
    msg = sent_body(q)["messages"][0]
    assert msg["chat_type"] == "i2v" and [f["id"] for f in msg["files"]] == [FILE_ID]
    assert msg["files"][0]["url"] == sts_ok()["data"]["file_url"]
    params = json.loads(msg["extra"]["meta"]["biz_data"])["req"]["params"]
    assert params.get("attachments") == [{"type": "image", "materialId": FILE_ID}], "attachments: %s" % params
    assert json.loads(msg["extra"]["meta"]["biz_data"])["req"]["rootModel"] == "happyhorse"


def test_an_image_edit_is_a_t2i_chat_with_its_one_file():
    log = []
    q = Qwen(log)
    result = run_submit(q, Ctx(log), job(image=True))
    msg = sent_body(q)["messages"][0]
    assert msg["chat_type"] == "t2i" and [f["id"] for f in msg["files"]] == [FILE_ID]
    assert msg["files"][0]["file_class"] == "vision" and msg["files"][0]["status"] == "uploaded"
    assert isinstance(result.job, Completed)


# ---- the full catalog (25/10): several images, the modes, 1080P, the pro options -------------------------------------
def sts_each(n: int) -> list[dict]:
    """getstsToken answers with a new file id per upload: f0, f1, ..."""
    out = []
    for i in range(n):
        body = sts_ok()
        body["data"] = {**body["data"], "file_id": "f%d" % i, "file_path": "u1/f%d_input.jpg" % i}
        out.append(body)
    return out


def biz(q: Qwen) -> tuple[dict, dict]:
    meta = sent_body(q)["messages"][0]["extra"]["meta"]
    return meta, json.loads(meta["biz_data"])["req"]


UPLOAD_ALL = ("                files = [await upload_image(site, await read_media(ref), ref.mime) for ref in await "
              "job.inputs()]")


@mutation(SUBMIT_FILE, UPLOAD_ALL, "                files = [await upload_image(site, await read_media(ref), "
          "ref.mime) for ref in (await job.inputs())[:1]]", expect="every reference image")
@mutation(SUBMIT_FILE, '        gen = "multi_ref" if mode == "multi-reference" else "vid_gen"', '        gen = "vid_gen"',
          expect="multi_ref")
def test_happyhorse_reference_images_are_all_uploaded_and_sent_as_multi_ref_in_their_order():
    log = []
    q = Qwen(log, send=I2V_TASK, sts=sts_each(9))
    result = run_submit(q, Ctx(log), job(VIDEO, image=9))
    assert isinstance(result.job, Accepted)
    assert len(q.calls("put")) == 9 and log.index("put") < log.index("new_chat")
    assert [p.content for p in q.calls("put")][:3] == [b"\xff\xd8\xffJPEG-bytes", b"\xff\xd8\xffJPEG-1",
                                                      b"\xff\xd8\xffJPEG-2"]
    msg = sent_body(q)["messages"][0]
    assert [f["id"] for f in msg["files"]] == ["f%d" % i for i in range(9)], "every reference image"
    meta, req = biz(q)
    assert (msg["chat_type"], meta["videoGenerationMode"], req["genMode"]) == ("i2v", "multi-reference", "multi_ref")
    assert req["params"]["attachments"] == [{"type": "image", "materialId": "f%d" % i} for i in range(9)]


def test_wan_first_and_last_frames_go_first_then_last_and_1080p_is_asked_as_1080P():
    log = []
    q = Qwen(log, send=I2V_TASK, sts=sts_each(2))
    run_submit(q, Ctx(log), job("qwen-wan-2.7", image=2, duration=10, options={"resolution": "1080p"}))
    assert json.loads(q.calls("verify")[0].content) == {"chat_type": "i2v", "task_info": {"create_credits": 60}}
    meta, req = biz(q)
    assert (meta["videoGenerationMode"], req["genMode"], req["rootModel"]) == ("first-last-frame", "vid_gen", "wan27")
    assert req["params"] == {"resolution": "1080P", "size": "16:9", "duration": 10, "audio": True,
                             "attachments": [{"type": "image", "materialId": "f0"},
                                             {"type": "image", "materialId": "f1"}]}


def test_a_video_planned_before_modes_is_sent_standard():
    queued = job(VIDEO)
    old = {k: v for k, v in queued.params.items() if k != "mode"}
    log = []
    q = Qwen(log, send=VIDEO_TASK)
    run_submit(q, Ctx(log), dataclasses.replace(queued, params=old))
    meta, req = biz(q)
    assert (meta["videoGenerationMode"], req["genMode"]) == ("standard", "vid_gen")


def test_the_pro_image_names_its_model_and_auto_size_and_takes_three_images():
    log = []
    q = Qwen(log, sts=sts_each(3))
    result = run_submit(q, Ctx(log), job("qwen-image-3.0-pro", image=3))
    assert isinstance(result.job, Completed)
    msg = sent_body(q)["messages"][0]
    assert msg["extra"]["meta"] == {"subChatType": "t2i", "size": "auto", "model": "qwen-image-3.0-pro"}
    assert msg["chat_type"] == "t2i" and [f["id"] for f in msg["files"]] == ["f0", "f1", "f2"]


POOL_OF_JOB = "    pool = C.pool_of(job.model, job.params)"


@mutation(SUBMIT_FILE, POOL_OF_JOB, "    pool = kind", expect="video_pro")
def test_a_pro_option_out_of_credits_or_quota_empties_its_own_pool():
    log = []
    q = Qwen(log, verify=VERIFY_EMPTY)
    result = run_submit(q, Ctx(log), job(VIDEO, options={"resolution": "1080p"}))
    assert Balance("video_pro", 0) in result.account, "video_pro: %s" % (result.account,)
    quota = stream({"role": "assistant", "content": IMAGE_QUOTA, "phase": "answer", "status": "typing"},
                   {"content": "", "status": "finished", "phase": "answer"})
    result = run_submit(Qwen([], send=quota), Ctx([]), job("qwen-image-3.0-pro"))
    assert isinstance(result.job, NotTaken) and Balance("image_pro", 0) in result.account


# ---- the firewall: the one answer after the send that requeues -------------------------------------------------
@mutation(SUBMIT_FILE, '        if verdict == "waf":\n', '        if False:\n', expect="NotTaken")
@mutation(SUBMIT_FILE, "    await drop_pair(ctx, sessions, minted_at)\n", "", expect="the stale pair was kept")
def test_the_wafs_exact_edge_block_drops_the_pair_and_requeues_not_taken_without_reading_the_chat():
    log = []
    ctx = Ctx(log)
    result = run_submit(Qwen(log, send=WAF), ctx)
    assert isinstance(result.job, NotTaken) and result.job.code == "qwen.waf_refused", result.job
    assert ctx.renewed and cred_of(ctx.renewed[-1]).bx is None, "the stale pair was kept"
    assert log[log.index("send"):] == ["send", "renew"], "the chat was read as proof: %s" % log
    assert cred_of(ctx.renewed[-1]).minted_at == cred_of(minted_blob()).minted_at


NEAR_MISSES = {
    "no-bxpunish": dict(drop=("bxpunish",)), "no-bxuuid": dict(drop=("bxuuid",)),
    "app-request-id": dict(add={"x-request-id": "r1"}), "app-status": dict(add={"x-actual-status-code": "200"}),
    "no-waf-cookie": dict(cookies=("x5sectag",)),
    "html": dict(drop=("content-type",), add={"content-type": "text/html"}),
    "status-500": dict(status=500), "with-success": dict(body={**WAF_REC["body"], "success": False}),
    "other-ret": dict(body={**WAF_REC["body"], "ret": ["FAIL_SYS_TOKEN_EXOIRED", WAF_REC["body"]["ret"][1]]}),
    "short-ret": dict(body={**WAF_REC["body"], "ret": ["FAIL_SYS_USER_VALIDATE"]}),
    "no-punish-url": dict(body={**WAF_REC["body"], "data": {"url": "https://chat.qwen.ai/x"}}),
}


@mutation(SUBMIT_FILE, '    if status != 200 or headers.get("bxpunish") != "1" or not headers.get("bxuuid"):\n',
          "    if False:\n", expect="requeued")
@mutation(SUBMIT_FILE, "    if any(h in headers for h in APP_HEADERS) or not", "    if not", expect="requeued")
@mutation(SUBMIT_FILE, "    if WAF_COOKIE not in", "    if WAF_COOKIE in", expect="requeued")
@mutation(SUBMIT_FILE, '    if body is None or set(body) != {"ret", "data"}:\n', "    if body is None:\n",
          expect="requeued")
@mutation(SUBMIT_FILE, "            and isinstance(data[\"url\"], str) and WAF_PUNISH in data[\"url\"])",
          "            and isinstance(data[\"url\"], str))", expect="requeued")
@pytest.mark.parametrize("change", list(NEAR_MISSES), ids=list(NEAR_MISSES))
def test_anything_short_of_the_exact_edge_block_is_ambiguous_and_keeps_the_pair(change):
    for kind in ("image", "video"):
        log = []
        ctx = Ctx(log)
        result = run_submit(Qwen(log, send=lambda r: waf_block(**NEAR_MISSES[change])), ctx,
                            job(VIDEO) if kind == "video" else job())
        assert isinstance(result.job, Ambiguous), "a near miss (%s, %s) was requeued: %s" % (change, kind, result.job)
        assert log.count("send") == 1 and "renew" not in log


def test_a_cut_edge_block_is_ambiguous():
    log = []
    cut = httpx.Response(200, headers=list(waf_block().headers.multi_items()),
                         stream=CutStream(json.dumps(WAF_REC["body"])[:40]))
    result = run_submit(Qwen(log, send=cut), Ctx(log))
    assert isinstance(result.job, Ambiguous), result.job


@pytest.mark.parametrize("answer", [
    httpx.Response(500, text="oops"), httpx.Response(429, json={"success": False}), httpx.ReadTimeout("slow"),
    httpx.ConnectError("gone"), cut_after('data: {"response.created": {"chat_id": "x"}}\n\n'), "not json",
    json.dumps({"success": False, "data": {"code": "RateLimited", "num": 4}}),
    json.dumps({"ret": ["FAIL_SYS_USER_VALIDATE"], "success": True}), json.dumps({"success": True, "data": {}}),
], ids=["500", "429", "timeout", "connect", "cut", "text", "ratelimited", "waf-like-with-success", "empty"])
def test_any_other_answer_after_the_send_is_ambiguous_and_never_sent_again(answer):
    log = []
    result = run_submit(Qwen(log, send=answer), Ctx(log))
    assert isinstance(result.job, Ambiguous), result.job
    assert log.count("send") == 1 and "mark_sent" in log


def test_a_video_send_with_no_answer_is_ambiguous():
    for answer in (httpx.ReadTimeout("slow"), httpx.Response(502, text="bad gateway"), "{}"):
        log = []
        result = run_submit(Qwen(log, send=answer), Ctx(log), job(VIDEO))
        assert isinstance(result.job, Ambiguous) and log.count("send") == 1


# ---- what qwen said after the send --------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, '    if extra.get("quota_limit") is True:\n        return "no_credits", words\n',
          '    if False:\n        return "no_credits", words\n', expect="out_of_credits")
def test_insufficient_credits_after_the_send_ends_the_job_refunded_and_empties_the_pool():
    log = []
    result = run_submit(Qwen(log, send=NO_CREDITS), Ctx(log), job(VIDEO))
    assert isinstance(result.job, Fail) and result.job.code == "qwen.out_of_credits", result.job
    assert "Insufficient Credits" in result.job.detail and Balance("video", 0) in result.account


VIDEO_REFUSAL = {"success": True, "data": {"chat_id": CHAT, "messages": [
    {"role": "assistant", "content": "", "extra": {}, "status": "finished", "content_list": [
        {"content": "I can't make a video of that.", "phase": "answer", "status": "finished"}]}]}}
REFUSED_BRANCH = ('    if msg.get("role") == "assistant" and words and "wanx" not in extra and msg.get("status") == '
                  '"finished":\n        return "refused", words\n')


@mutation(SUBMIT_FILE, REFUSED_BRANCH, '    if False:\n        return "refused", words\n', expect="qwen.refused")
def test_a_complete_video_answer_in_words_with_no_task_is_a_refusal_refunded_at_once():
    log = []
    result = run_submit(Qwen(log, send=VIDEO_REFUSAL), Ctx(log), job(VIDEO))
    assert isinstance(result.job, Fail) and result.job.code == "qwen.refused", result.job
    assert "can't make a video" in result.job.detail and log.count("send") == 1


@mutation(SUBMIT_FILE, ' and msg.get("status") == "finished":\n        return "refused", words\n',
          ':\n        return "refused", words\n', expect="refused without qwen's end marker")
def test_video_words_without_qwens_finished_marker_are_ambiguous():
    for status in (None, "typing"):
        message = {**VIDEO_REFUSAL["data"]["messages"][0], "status": status}
        log = []
        answer = {"success": True, "data": {"chat_id": CHAT, "messages": [message]}}
        result = run_submit(Qwen(log, send=answer), Ctx(log), job(VIDEO))
        assert isinstance(result.job, Ambiguous), "refused without qwen's end marker: %s" % (result.job,)


@mutation(SUBMIT_FILE, "    if status == 200 and finished and not cut:\n", "    if False:\n", expect="qwen.refused")
def test_an_image_stream_that_ends_with_words_and_no_image_is_qwen_refusing():
    log = []
    answer = stream({"role": "assistant", "content": "I can't create that image.", "phase": "answer",
                     "status": "typing"}, {"content": "", "status": "finished", "phase": "answer"})
    result = run_submit(Qwen(log, send=answer), Ctx(log))
    assert isinstance(result.job, Fail) and result.job.code == "qwen.refused", result.job
    assert "can't create" in result.job.detail


IMAGE_QUOTA = "You’ve used up your daily image generation quota. Upgrade your plan to generate more images."
QUOTA_BRANCH = '        if verdict == "refused" and kind == "image" and is_image_quota(said):\n'


@mutation(SUBMIT_FILE, QUOTA_BRANCH, '        if False:\n', expect="qwen.image_limit")
@mutation(SUBMIT_FILE, "NotTaken(errors.IMAGE_LIMIT, {\"limit_chat\": chat}), Balance(pool, 0), *rest)",
          "NotTaken(errors.IMAGE_LIMIT, {\"limit_chat\": chat}), *rest)", expect="the image pool")
def test_the_daily_image_quota_answer_requeues_the_job_with_no_attempt_and_empties_the_image_pool():
    """Measured 25/10: the 11th image of the day on one account got this answer and no image."""
    log = []
    answer = stream({"role": "assistant", "content": IMAGE_QUOTA, "phase": "answer", "status": "typing"},
                    {"content": "", "status": "finished", "phase": "answer"})
    result = run_submit(Qwen(log, send=answer), Ctx(log))
    assert isinstance(result.job, NotTaken) and result.job.code == "qwen.image_limit", "qwen.image_limit: %s" % (
        result.job,)
    assert Balance("image", 0) in result.account, "the image pool was not emptied: %s" % (result.account,)
    rule = errors.CODES["qwen.image_limit"]
    assert not rule.counts_attempt and not rule.terminal, "a used-up account is not the job's fault"
    # the same words on a video job are no image quota: an ordinary refusal
    video = {"success": True, "data": {"chat_id": CHAT, "messages": [
        {"role": "assistant", "content": IMAGE_QUOTA, "extra": {}, "status": "finished"}]}}
    log = []
    assert run_submit(Qwen(log, send=video), Ctx(log), job(VIDEO)).job.code == "qwen.refused"


@mutation(RESULTS_FILE, '    if (message or {}).get("done") is True and is_image_quota(text_of(message)):\n',
          "    if False:\n", expect="out_of_credits")
@mutation(RESULTS_FILE, "effects=(Balance(C.pool_of(job.model, job.params), 0),))", 'effects=(Balance("image", 0),))',
          expect="image_pro")
def test_a_lost_image_answer_found_to_be_the_quota_is_refunded_and_empties_the_image_pool():
    record = {"success": True, "data": {"chat": {"messages": [
        {"role": "user", "content": "x"}, {"role": "assistant", "content": IMAGE_QUOTA, "done": True}]}}}
    got = run_poll(Qwen([], chat=record), job(state={"chat": CHAT}))
    assert isinstance(got.outcome, Failed) and got.outcome.code == "qwen.out_of_credits", "out_of_credits: %s" % (
        got.outcome,)
    assert got.effects == (Balance("image", 0),)
    pro = run_poll(Qwen([], chat=record), job("qwen-image-3.0-pro", state={"chat": CHAT}))
    assert pro.effects == (Balance("image_pro", 0),), "the pool of the job, image_pro: %s" % (pro.effects,)


def test_an_image_link_counts_even_in_a_stream_cut_after_it_and_a_cut_before_it_is_unknown():
    link = {"role": "assistant", "content": IMAGE_LINK, "phase": "image_gen", "status": "typing"}
    log = []
    result = run_submit(Qwen(log, send=cut_after(stream(link))), Ctx(log))
    assert isinstance(result.job, Completed) and result.job.outputs[0].ref == IMAGE_LINK
    log = []
    result = run_submit(Qwen(log, send=cut_after(stream())), Ctx(log))
    assert isinstance(result.job, Ambiguous)


def test_a_link_off_qwens_cdn_is_no_image():
    log = []
    answer = stream({"content": "https://evil.example/x.png", "phase": "image_gen", "status": "typing"})
    result = run_submit(Qwen(log, send=answer), Ctx(log))
    assert isinstance(result.job, Ambiguous)


# ---- before the send: retries, nothing sent -------------------------------------------------------------------------
def before_send(log: list) -> None:
    assert "mark_sent" not in log and "send" not in log and "report_ref" not in log, log


@mutation(SUBMIT_FILE, '                if data.get("access") is not True:\n', "                if False:\n",
          expect="sent with no credits")
def test_too_few_credits_is_a_retry_before_anything_is_sent():
    log = []
    result = run_submit(Qwen(log, verify=VERIFY_EMPTY, send=NO_CREDITS), Ctx(log), job(VIDEO))
    assert "send" not in log, "sent with no credits: %s" % log
    assert result.job == Retry("pool.account_out_of_points") and Balance("video", 0) in result.account
    before_send(log)
    assert "new_chat" not in log


def test_a_signed_out_refresh_is_a_spaced_strike_and_a_dead_proxy_a_retry():
    log = []
    result = run_submit(Qwen(log, refresh=httpx.Response(401, json={"success": False})), Ctx(log))
    assert result.job == Retry("pool.account_logged_out")
    assert any(isinstance(e, Suspect) and e.min_gap_s > 0 for e in result.account)
    assert any(isinstance(e, Cooldown) and e.recheck for e in result.account)
    before_send(log)
    log = []
    ctx = Ctx(log, proxy=Proxy("10.0.0.1", 8080, "user:pw-9"))
    result = run_submit(Qwen(log, refresh=httpx.ConnectError("proxy http://user:pw-9@10.0.0.1:8080 refused")), ctx)
    assert result.job == Retry("pool.proxy_failed") and any(isinstance(e, ProxyBad) for e in result.account)
    assert "pw-9" not in repr(result)
    before_send(log)


def test_a_bad_step_before_the_send_is_a_retry_with_nothing_sent():
    for answers in (dict(new_chat={"success": False}), dict(sts=httpx.Response(500)), dict(put=httpx.Response(403)),
                    dict(sts={"success": True, "data": {**sts_ok()["data"], "endpoint": "evil.example"}})):
        log = []
        result = run_submit(Qwen(log, **answers), Ctx(log), job(image=True))
        assert result.job == Retry("qwen.http_error"), (answers, result.job)
        before_send(log)


def test_without_a_fresh_pair_the_submit_mints_one_writes_it_back_and_sends_with_it():
    log = []
    q = Qwen(log)
    ctx = Ctx(log, blob=BLOB)
    result = run_submit(q, ctx)
    assert isinstance(result.job, Completed)
    assert log.index("mint") < log.index("renew") < log.index("mark_sent"), log
    assert cred_of(ctx.renewed[0]).bx == PAIR and q.calls("send")[0].headers["bx-ua"] == BX_UA
    assert "minted-cna" in q.calls("send")[0].headers["cookie"], "the send did not carry the browser's cookies"
    stale = Ctx([], blob=minted_blob(NOW - 1500))
    log = stale.log
    run_submit(Qwen(log), stale)
    assert log.count("mint") == 1, "a pair as old as qwen.bx_max_age_s was reused"


def test_a_mint_that_fails_is_a_retry_before_the_send_and_its_text_holds_no_value():
    log = []
    ctx = Ctx(log, blob=BLOB)
    m = minter(log, fail=RuntimeError("boom " + REFRESH))
    result = run_submit(Qwen(log), ctx, m=m)
    assert result.job == Retry("qwen.mint_failed")
    before_send(log)
    assert not any(REFRESH in e for e in ctx.events)


def test_a_mark_sent_that_fails_reaches_the_core_and_nothing_is_sent():
    log = []
    ctx = Ctx(log)
    ctx.mark_error = RuntimeError("lease lost")
    with pytest.raises(RuntimeError, match="lease lost"):
        run_submit(Qwen(log), ctx)
    assert "send" not in log


def test_no_secret_reaches_a_log_an_event_a_reference_or_a_result(caplog):
    caplog.set_level(logging.DEBUG)
    for answers, the_job in ((dict(send=I2V_TASK), job(VIDEO, image=True)), (dict(send=WAF), job()),
                             (dict(refresh=httpx.Response(403)), job())):
        log = []
        ctx = Ctx(log)
        result = run_submit(Qwen(log, **answers), ctx, the_job)
        seen = "\n".join([caplog.text, repr(result), repr(ctx.refs), *ctx.events])
        for secret in (REFRESH, ACCESS, BX_UA, UMID, STS_SECRET, STS_TOKEN):
            assert secret not in seen, "a planted value leaked"


# ---- reconcile -----------------------------------------------------------------------------------------------------
def run_reconcile(q, the_job):
    log = q.log
    return asyncio.run(R.reconcile(Ctx(log), the_job, settings(), Sessions(), transport=q.transport))


NEVER_NOT_SENT = ("        return StillUnknown(answered=True)          # never NotSent: the read may lag the send "
                  "(no re-send, ever)\n")


@mutation(RESULTS_FILE, NEVER_NOT_SENT,
          '        return __import__("relay_core.providers").providers.NotSent(covers_send_window=True)\n',
          expect="an empty chat was taken for not sent")
def test_reconcile_never_calls_an_empty_chat_not_sent_and_a_message_of_ours_is_found():
    state = {"chat": CHAT}
    empty = run_reconcile(Qwen([], chat=CHAT_EMPTY), job(state=state))
    assert empty == StillUnknown(answered=True), "an empty chat was taken for not sent: %s" % (empty,)
    assert not isinstance(empty, NotSent)
    assert run_reconcile(Qwen([], chat=CHAT_IMAGE), job(state=state)) == Found(CHAT, {"chat": CHAT}, True)
    task = "tsk_6993c8657bf4a05bc4593e3eca35a1f7"
    assert run_reconcile(Qwen([], chat=CHAT_VIDEO), job(VIDEO, state=state)) == Found(
        task, {"chat": CHAT, "task": task}, True)
    assert run_reconcile(Qwen([], chat=CHAT_DROPPED), job(state=state)) == Found(CHAT, {"chat": CHAT}, True)


def test_a_chat_that_cannot_be_read_settles_nothing():
    for chat in (httpx.Response(500), httpx.ConnectError("down"), {"success": False}):
        assert run_reconcile(Qwen([], chat=chat), job(state={"chat": CHAT})) == StillUnknown()
    assert run_reconcile(Qwen([]), job()) == StillUnknown()


# ---- poll ----------------------------------------------------------------------------------------------------------
def run_poll(q, the_job, now=NOW_DT):
    return asyncio.run(R.poll(Ctx(q.log), the_job, settings(), Sessions(), transport=q.transport,
                              rand=lambda: 0.0, now=lambda: now))


@mutation(RESULTS_FILE, "    if said in TASK_FAILED:\n", "    if False:\n", expect="render_failed")
def test_a_video_is_read_by_its_task_until_the_mp4_or_a_failure():
    the_job = job(VIDEO, state={"chat": CHAT, "task": TASK})
    running = run_poll(Qwen([], task=TASK_RUNNING), the_job)
    assert isinstance(running.outcome, Pending) and running.outcome.answered and running.outcome.next_in_s == 30
    done = run_poll(Qwen([], task=TASK_SUCCESS), the_job)
    assert done.outcome == Done((Output(VIDEO_LINK, "video/mp4", 0, TASK, {"chat": CHAT}),))
    failed = {"success": True, "data": {**TASK_RUNNING["data"], "task_status": "failed", "message": "moderation"}}
    out = run_poll(Qwen([], task=failed), the_job).outcome
    assert isinstance(out, Failed) and out.code == "qwen.render_failed", "render_failed: %s" % (out,)
    unread = run_poll(Qwen([], task=httpx.Response(500)), the_job).outcome
    assert isinstance(unread, Pending) and not unread.answered


def test_a_video_found_without_its_task_learns_it_from_the_chat_or_ends_on_insufficient_credits():
    out = run_poll(Qwen([], chat=CHAT_VIDEO), job(VIDEO, state={"chat": CHAT})).outcome
    assert isinstance(out, Pending) and out.state_patch == {"task": "tsk_6993c8657bf4a05bc4593e3eca35a1f7"}
    no_credits = json.loads(NO_CREDITS)["data"]["messages"][0]
    record = {"success": True, "data": {"chat": {"messages": [{"role": "user", "content": "x"}, no_credits]}}}
    out = run_poll(Qwen([], chat=record), job(VIDEO, state={"chat": CHAT})).outcome
    assert isinstance(out, Failed) and out.code == "qwen.out_of_credits" and not out.taken


GRACE = "    if words and job.sent_at is not None and (now - job.sent_at).total_seconds() >= grace:\n"


@mutation(RESULTS_FILE, GRACE, "    if False:\n", expect="refused")
def test_a_lost_video_answer_whose_chat_holds_words_and_no_task_is_a_refusal_after_the_grace():
    reply = VIDEO_REFUSAL["data"]["messages"][0]
    record = {"success": True, "data": {"chat": {"messages": [{"role": "user", "content": "x"}, reply]}}}
    young = job(VIDEO, state={"chat": CHAT}, sent_at=NOW_DT - timedelta(seconds=120))
    assert isinstance(run_poll(Qwen([], chat=record), young).outcome, Pending), "refused before the grace"
    old = job(VIDEO, state={"chat": CHAT}, sent_at=NOW_DT - timedelta(seconds=300))
    out = run_poll(Qwen([], chat=record), old).outcome
    assert isinstance(out, Failed) and out.code == "qwen.refused" and not out.taken, "refused: %s" % (out,)
    with_task = run_poll(Qwen([], chat=CHAT_VIDEO), old).outcome
    assert isinstance(with_task, Pending) and with_task.state_patch.get("task"), "a named task was not followed"


@mutation(RESULTS_FILE, "    if job.sent_at is not None and (now - job.sent_at).total_seconds() >= lost_after:\n",
          "    if False:\n", expect="image_lost")
def test_an_image_is_read_from_the_chat_and_a_chat_still_empty_long_after_is_a_lost_image():
    done = run_poll(Qwen([], chat=CHAT_IMAGE), job(state={"chat": CHAT})).outcome
    assert done == Done((Output(IMAGE_LINK, "image/png", 0, None, {"chat": CHAT}),))
    young = job(state={"chat": CHAT}, sent_at=NOW_DT - timedelta(seconds=120))
    assert isinstance(run_poll(Qwen([], chat=CHAT_DROPPED), young).outcome, Pending)
    old = job(state={"chat": CHAT}, sent_at=NOW_DT - timedelta(seconds=900))
    out = run_poll(Qwen([], chat=CHAT_DROPPED), old).outcome
    assert isinstance(out, Failed) and out.code == "qwen.image_lost", "image_lost: %s" % (out,)


# ---- fetch ---------------------------------------------------------------------------------------------------------
@mutation(RESULTS_FILE, "            out.ref, hosts=MEDIA_HOSTS, proxy=proxy_url(ctx.account.proxy),",
          "            out.ref, hosts=MEDIA_HOSTS, proxy=None,", expect="not through the account proxy")
def test_fetch_downloads_the_cdn_link_through_the_account_proxy_only():
    def drain(ref, site, proxy):
        ctx = Ctx([], proxy=proxy)

        async def go():
            return b"".join([c async for c in R.fetch(ctx, Output(ref, "image/png", 0),
                                                      media_transport=site, media_resolver=public_resolver)])
        return asyncio.run(go())

    site = MediaSite(b"direct-bytes", PNG)
    got = drain(IMAGE_LINK, site, Proxy("10.0.0.1", 8080, "user:pw"))
    assert site.routes == ["http://user:pw@10.0.0.1:8080"], "not through the account proxy: %s" % site.routes
    assert got == PNG
    plain = MediaSite(PNG)
    assert drain(IMAGE_LINK, plain, None) == PNG and plain.routes == [None]
    with pytest.raises(UnsafeUrl):
        drain("https://evil.example/output/x.png", MediaSite(PNG), None)
