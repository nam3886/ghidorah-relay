"""Submit, poll, reconcile and fetch over a fake vibes.ai (an `httpx.MockTransport`, no network), fed vibes' recorded
answers from fixtures/vibes/s4-capture, s5-live and s6-live. Each money or safety rule has a test that fails when the
rule breaks, and a mutation that proves it."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Completed,
    Cooldown,
    CreateRequest,
    Done,
    Fail,
    Failed,
    Found,
    MediaIn,
    MediaRef,
    OutputGone,
    Pending,
    ProxyBad,
    Retry,
    StillUnknown,
    Success,
    Suspect,
)
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_vibes import errors
from relay_vibes.engine import batch as B
from relay_vibes.engine import build_engine
from relay_vibes.engine.check import check_account
from relay_vibes.engine.http import Rests
from relay_vibes.engine.submit import submit
from relay_vibes.spec import SPEC

SUBMIT_FILE = "packages/provider-vibes/src/relay_vibes/engine/submit.py"
BATCH_FILE = "packages/provider-vibes/src/relay_vibes/engine/batch.py"
HTTP_FILE = "packages/provider-vibes/src/relay_vibes/engine/http.py"
FIX = Path(__file__).resolve().parents[3] / "fixtures" / "vibes"
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
BLOB = json.dumps({"meta_session": "sess-value-1", "cookies": []}).encode()
SETTINGS = FakeSettings(SPEC, vibes__account_min_gap_s=0)    # the spacing has its own tests
MP4 = b"\x00\x00\x00\x20ftypisom" + b"y" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


def rec(name: str) -> dict:
    """One recorded call: `s5-live/video_007_video_generate`."""
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


S4 = json.loads((FIX / "s4-capture" / "capture.json").read_text(encoding="utf-8"))
S4_500 = next(r for r in S4 if r["status"] == 500)         # `Failed to fetch generation batch`, once mid-render


def s4(step: str, n: int = 0) -> dict:
    return [r for r in S4 if r["step"] == step][n]


def answer(r: dict) -> httpx.Response:
    """A recorded answer as an httpx response; the checkpoint's HTML page is rebuilt from its title."""
    headers = {k: v for k, v in (r.get("headers") or {}).items() if k.lower() not in ("content-length", "content-type")}
    body = r["body"]
    if isinstance(body, dict) and (body.get("_html_omitted") or "_binary_bytes" in body):
        return httpx.Response(r["status"], headers={**headers, "content-type": "text/html"},
                              text="<html><title>Vercel Security Checkpoint</title></html>")
    return httpx.Response(r["status"], headers=headers, json=body)


CHECKPOINT = rec("s5-live/video_041_video_poll_after")      # 403, X-Vercel-Mitigated: challenge, Retry-After: 30


def echo_create(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"batch": None, "id": json.loads(request.content)["id"]})


class Vibes:
    """A fake vibes.ai. Each route is a response, an exception (raised), or a callable of the request returning
    either. `log` is shared with the ctx, so the order of calls and of the platform steps can be read in one list."""

    def __init__(self, log: list, *, upload=None, create=echo_create, generate=None, batch=None, download=None):
        self.log, self.seen = log, []
        self.routes = {"upload": upload, "create": create, "generate": generate, "batch": batch,
                       "download": download}
        self.transport = httpx.MockTransport(self.handle)

    def name_of(self, path: str) -> str:
        if path == "/api/upload-image":
            return "upload"
        if path == "/api/generation-batches":
            return "create"
        if path.startswith("/api/generate/"):
            return "generate"
        if path.startswith("/api/generation-batches/"):
            return "batch"
        if path.startswith("/api/download/"):
            return "download"
        raise AssertionError("unexpected call %s" % path)

    def handle(self, request: httpx.Request) -> httpx.Response:
        name = self.name_of(request.url.path)
        self.log.append(name)
        self.seen.append(request)
        out = self.routes[name]
        if callable(out):
            out = out(request)
        if isinstance(out, Exception):
            raise out
        assert out is not None, "no answer set for %s" % name
        return out

    def sent(self, name: str) -> list[httpx.Request]:
        return [r for r in self.seen if self.name_of(r.url.path) == name]


class Ctx:
    """The RunCtx an engine sees: `report_ref` and `mark_sent` write to the shared log."""

    def __init__(self, log: list, account_id: str = "acc-1"):
        self.log = log
        self.account = SimpleNamespace(id=account_id, credential=BLOB, proxy=None)
        self.refs: list[tuple[str, dict]] = []

    async def report_ref(self, ref, state):
        self.log.append("report_ref")
        self.refs.append((ref, dict(state)))

    async def mark_sent(self):
        self.log.append("mark_sent")


def job(model="vibes-video", prompt="a small wooden sailboat", *, images=(), ratio=None, batch=None, **options):
    """A job view as the core builds it from vibes' own plan."""
    req = CreateRequest(model=model, prompt=prompt, ratio=ratio, options=options,
                        images=tuple(MediaIn("0" * 64, "image/jpeg", 10) for _ in images))
    p = SPEC.plan(req, SETTINGS, {})

    async def inputs():
        return [MediaRef(i, "in/%d" % i, "image/jpeg", len(data)) for i, data in enumerate(images)]

    return SimpleNamespace(id="job-1", operation=p.operation, params=dict(p.params), prompt=prompt,
                           external_ref=batch, provider_state={"batch": batch} if batch else {}, inputs=inputs,
                           sent_at=None, render_since=None)


def run_submit(fake: Vibes, j, rests: Rests | None = None, ctx: Ctx | None = None, media: bytes = b"jpegbytes"):
    async def read_media(ref):
        return media

    ctx = ctx or Ctx(fake.log)
    out = asyncio.run(submit(ctx, j, SETTINGS, rests or Rests(lambda: NOW), transport=fake.transport,
                             read_media=read_media, now=lambda: NOW))
    return out, ctx


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


# ---- submit: the money rules ---------------------------------------------------------------------------------------
WRITE_AHEAD = "await ctx.report_ref(batch, {BATCH_KEY: batch})"
MARK_SENT = "await ctx.mark_sent()"


@mutation(SUBMIT_FILE, WRITE_AHEAD, "pass", expect="the batch id must be on the job before the generate")
@mutation(SUBMIT_FILE, MARK_SENT, "pass", expect="mark_sent must come right before the generate")
def test_the_batch_id_is_written_then_the_job_marked_sent_right_before_the_one_generate():
    log: list = []
    fake = Vibes(log, generate=answer(rec("s5-live/video_007_video_generate")))
    result, ctx = run_submit(fake, job())
    assert "report_ref" in log and log.index("report_ref") < log.index("generate"), (
        "the batch id must be on the job before the generate: %s" % log)
    assert log[-3:] == ["report_ref", "mark_sent", "generate"], (
        "mark_sent must come right before the generate: %s" % log)
    batch = body(fake.sent("create")[0])["id"]
    assert ctx.refs == [(batch, {"batch": batch})]
    assert body(fake.sent("generate")[0])["batchId"] == batch
    assert result.job == Accepted(batch, {"batch": batch}, taken=True) and result.account == (Success(),)


NO_ANSWER = '            return _result(Ambiguous("the generate got no answer (%s)" % type(e).__name__))'
SEND_AGAIN = ("            resp = await client.post(GENERATE_PATH[run.kind], json=run.generate_body(batch, prompt, "
              "config, frame),\n                                     timeout=GENERATE_TIMEOUT_S)\n"
              "            return run.answer(resp, batch)")


@mutation(SUBMIT_FILE, NO_ANSWER, SEND_AGAIN, expect="exactly one generate POST")
def test_a_generate_that_gets_no_answer_is_unknown_and_never_sent_again():
    for lost in (httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("peer closed"), httpx.ConnectError("gone")):
        calls = []

        def generate(request, calls=calls, lost=lost):
            calls.append(request)
            return lost if len(calls) == 1 else answer(rec("s5-live/video_007_video_generate"))

        log: list = []
        fake = Vibes(log, generate=generate)
        result, _ = run_submit(fake, job())
        assert len(fake.sent("generate")) == 1, "exactly one generate POST: %s" % log
        assert isinstance(result.job, Ambiguous), "a lost generate is unknown, never a retry: %s" % (result.job,)


def test_no_answer_after_the_send_ever_puts_the_job_back_in_the_queue():
    """5xx, sign-in errors, a rate limit, the checkpoint, a page that is not JSON: unknown, one POST, never Retry."""
    cases = [httpx.Response(500, json={"error": "Internal"}), httpx.Response(502, text="bad gateway"),
             httpx.Response(401, json={"error": "Unauthorized"}), httpx.Response(429, json={"error": "slow down"}),
             httpx.Response(200, text="<html>not json</html>"), answer(CHECKPOINT)]
    for resp in cases:
        log: list = []
        fake = Vibes(log, generate=resp)
        rests = Rests(lambda: NOW)
        result, _ = run_submit(fake, job(), rests)
        assert isinstance(result.job, Ambiguous), (resp.status_code, result.job)
        assert len(fake.sent("generate")) == 1
    assert result.account == (Cooldown("vibes security checkpoint", NOW + timedelta(seconds=600), recheck=True),)
    assert rests.left_s("acc-1") == 600, "the checkpoint on the generate rests the account"


def test_an_explicit_refusal_of_the_generate_fails_the_job_with_its_refund():
    for resp in (httpx.Response(200, json={"success": False, "error": "prompt blocked"}),
                 httpx.Response(400, json={"error": "invalid prompt"})):
        fake = Vibes([], generate=resp)
        result, _ = run_submit(fake, job())
        assert isinstance(result.job, Fail) and result.job.code == errors.GENERATE_REFUSED, result.job
        assert len(fake.sent("generate")) == 1 and "batch" in result.job.state_patch


# ---- submit: the three operations ---------------------------------------------------------------------------------
def test_text_to_image_is_drawn_inside_the_request_and_ends_completed_with_the_png_download():
    log: list = []
    fake = Vibes(log, generate=answer(rec("s5-live/image_003_image_generate")))
    result, _ = run_submit(fake, job("vibes-image", "a watercolor lighthouse", ratio="16:9"))
    created, sent = body(fake.sent("create")[0]), body(fake.sent("generate")[0])
    assert fake.sent("generate")[0].url.path == "/api/generate/images"
    assert created["type"] == "images" and created["content"] == [
        {"id": created["id"] + "-content-0", "type": "image", "isLoading": True}]
    assert created["config"] == sent["config"] == {"imageModel": "midjen-base", "aspectRatio": "16:9"}
    assert sent["inputs"] == [{"type": "variation", "image_prompt": "a watercolor lighthouse",
                               "original_prompt": "a watercolor lighthouse", "config": {}}]
    assert sent["mg_request_id"].startswith("www-") and "directPromptImageHandle" not in sent
    assert isinstance(result.job, Completed) and result.account == (Success(),)
    [out] = result.job.outputs
    recorded = rec("s5-live/image_003_image_generate")["body"]["updatedBatch"]["content"][0]
    assert (out.ref, out.mime, out.idx, out.upstream_id) == (recorded["id"], "image/png", 0, recorded["mediaEntId"])


def test_an_image_answer_that_is_not_finished_is_accepted_and_polled():
    recorded = rec("s5-live/image_003_image_generate")["body"]
    unfinished = {**recorded, "updatedBatch": {**recorded["updatedBatch"], "isComplete": False}}
    result, _ = run_submit(Vibes([], generate=httpx.Response(200, json=unfinished)), job("vibes-image"))
    assert isinstance(result.job, Accepted) and result.job.taken


def test_text_to_video_starts_the_render_with_the_resolution_and_no_ratio_unless_asked():
    fake = Vibes([], generate=answer(rec("s5-live/video_007_video_generate")))
    run_submit(fake, job(resolution="720p"))
    sent = body(fake.sent("generate")[0])
    assert fake.sent("generate")[0].url.path == "/api/generate/videos"
    assert sent["config"] == {"videoModel": "midjen-short", "resolution": "720p"}
    assert sent["inputs"] == [{"type": "prompt", "value": "a small wooden sailboat",
                               "original_prompt": "a small wooden sailboat", "config": {}}]


def test_image_to_video_uploads_the_start_frame_and_hands_its_id_to_the_generate():
    log: list = []
    upload = answer(rec("s6-live/i2v_001_upload_image"))
    fake = Vibes(log, upload=upload, generate=answer(rec("s6-live/i2v_003_generate")))
    result, _ = run_submit(fake, job(prompt="it comes alive", images=(b"jpeg",)), media=b"\xff\xd8jpeg")
    assert log[:2] == ["upload", "create"]
    assert body(fake.sent("upload")[0]) == {"image": "data:image/jpeg;base64," + base64.b64encode(
        b"\xff\xd8jpeg").decode()}
    up = rec("s6-live/i2v_001_upload_image")["body"]
    assert body(fake.sent("generate")[0])["directPromptImageHandle"] == {
        "image_ent_id": up["mediaEntId"], "image_url": up["imageUrl"], "source": "upload"}
    assert isinstance(result.job, Accepted) and result.job.taken


# ---- submit: failures before the send are retries -------------------------------------------------------------------
def test_a_failure_before_the_send_is_a_retry_and_nothing_is_marked_or_sent():
    out = {}
    for name, kw in (("checkpoint", dict(create=answer(CHECKPOINT))),
                     ("signed_out", dict(create=httpx.Response(401, json={"error": "Unauthorized"}))),
                     ("error", dict(create=httpx.Response(500, json={"error": "Internal"}))),
                     ("wrong_id", dict(create=httpx.Response(200, json={"batch": None, "id": "other"}))),
                     ("network", dict(create=httpx.ConnectError("connect to http://u:pw@10.0.0.1:1 failed"))),
                     ("upload", dict(upload=httpx.Response(200, json={"error": "too big"})))):
        log: list = []
        fake = Vibes(log, generate=answer(rec("s5-live/video_007_video_generate")), **kw)
        rests = Rests(lambda: NOW)
        result, _ = run_submit(fake, job(images=(b"x",)) if name == "upload" else job(), rests)
        assert "mark_sent" not in log and "report_ref" not in log and "generate" not in log, (name, log)
        assert isinstance(result.job, Retry), (name, result.job)
        out[name] = result
    assert out["checkpoint"].job.code == "pool.account_throttled"
    assert out["checkpoint"].account == (Cooldown("vibes security checkpoint", NOW + timedelta(seconds=600),
                                                  recheck=True),)
    assert out["signed_out"].job.code == "pool.account_logged_out"
    assert [type(e) for e in out["signed_out"].account] == [Suspect, Cooldown]
    assert out["error"].job.code == out["wrong_id"].job.code == out["upload"].job.code == errors.HTTP_ERROR
    assert out["network"].job.code == "pool.proxy_failed" and isinstance(out["network"].account[0], ProxyBad)
    assert "pw" not in repr(out["network"]), "the proxy login never leaves"


def test_an_account_that_rests_is_not_called_at_all_by_a_submit():
    rests = Rests(lambda: NOW)
    rests.start("acc-1", 300)
    log: list = []
    result, _ = run_submit(Vibes(log), job(), rests)
    assert log == [] and result.job == Retry("pool.account_throttled")
    assert result.account[0].until == NOW + timedelta(seconds=300)


# ---- poll ----------------------------------------------------------------------------------------------------------
def poll(fake: Vibes, j=None, rests=None, account_id="acc-1", rand=lambda: 0.0):
    j = j or job(batch="3fbc9c32-3f9d-47d1-aa43-377c9bf65697")
    return asyncio.run(B.poll(Ctx(fake.log, account_id), j, SETTINGS, rests or Rests(lambda: NOW),
                              transport=fake.transport, rand=rand))


DONE_RULE = ("    return batch.get(\"isComplete\") is True and bool(content) and all(c.get(\"isLoading\") is False "
             "for c in content)")


def recorded_polls() -> list[dict]:
    """Every recorded read of a batch that was still rendering, and the empty shells."""
    names = sorted(p.stem for p in (FIX / "s5-live").glob("video_*_video_poll_*.json"))
    reads = [rec("s5-live/" + n) for n in names] + [rec("s6-live/i2v_%03d_poll" % i) for i in range(4, 14)]
    return [r for r in reads if r["status"] == 200]


@mutation(BATCH_FILE, DONE_RULE, "    return bool(content) and all(c.get(\"isLoading\") is False for c in content)",
          expect="done needs isComplete and not isLoading")
@mutation(BATCH_FILE, DONE_RULE, "    return batch.get(\"isComplete\") is True and bool(content)",
          expect="done needs isComplete and not isLoading")
def test_done_needs_is_complete_and_no_item_loading_whatever_urls_a_read_shows():
    reads = recorded_polls()
    assert len(reads) > 40 and any(r["body"]["batch"]["content"][0].get("videoUrl") for r in reads)
    for r in reads:
        result = poll(Vibes([], batch=answer(r)))
        assert isinstance(result.outcome, Pending) and result.outcome.answered, (r["step"], result.outcome)
    shell = rec("s6-live/i2v_014_poll")["body"]
    item = shell["batch"]["content"][0]
    for complete, loading in ((True, True), (False, False)):
        flicker = {"batch": {**shell["batch"], "isComplete": complete, "content": [{**item, "isLoading": loading}]}}
        result = poll(Vibes([], batch=httpx.Response(200, json=flicker)))
        assert isinstance(result.outcome, Pending), "done needs isComplete and not isLoading: %s" % (
            result.outcome,)


def test_a_finished_batch_is_done_with_the_content_item_to_download():
    for name, kind in (("s5-live/recheck_001_recheck_batch", "vibes-video"), ("s6-live/i2v_014_poll", "vibes-video"),
                       ("s5-live/image_004_image_get_batch_after", "vibes-image")):
        r = rec(name)
        batch = r["body"]["batch"]
        result = poll(Vibes([], batch=answer(r)), job(kind, batch=batch["id"]))
        assert isinstance(result.outcome, Done) and result.effects == (Success(),), name
        [out] = result.outcome.outputs
        assert out.ref == batch["id"] + "-content-0" and out.upstream_id == batch["content"][0]["mediaEntId"]
        assert out.mime == ("video/mp4" if kind == "vibes-video" else "image/png")


def test_a_finished_batch_with_an_error_fails_with_its_refund():
    done = rec("s5-live/recheck_001_recheck_batch")["body"]["batch"]
    item = done["content"][0]
    for bad, code in (({**done, "hasError": True, "error": "moderation"}, errors.RENDER_FAILED),
                      ({**done, "content": [{**item, "error": "capacity"}]}, errors.CONTENT_REFUSED),
                      ({**done, "content": [{**item, "mediaEntId": None, "imageUrl": None, "videoUrl": None}]},
                       errors.CONTENT_REFUSED)):
        result = poll(Vibes([], batch=httpx.Response(200, json={"batch": bad})))
        assert isinstance(result.outcome, Failed) and result.outcome.code == code, result.outcome
        assert result.outcome.taken
    for code in (errors.RENDER_FAILED, errors.CONTENT_REFUSED, errors.OUTPUT_MISSING):
        rule = errors.CODES[code]
        assert rule.terminal and rule.refund and rule.public, code


# ---- a finished batch whose item vibes refused (production job f0176f20, 23/10) ----------------------------------
REFUSED = rec("refused-261023/t2i_generate_refused")
REFUSED_BATCH = REFUSED["body"]["updatedBatch"]
REFUSED_TEXT = "This content could not be generated. Please try a different prompt."
SUBMIT_REFUSAL = "                if why is not None:     # finished, but vibes refused the item: nothing to deliver"
ITEM_REFUSAL = "        if c.get(\"error\") or not has_media(c):"
MEDIA_RULE = "    return bool(item.get(\"mediaEntId\") or item.get(\"imageUrl\") or item.get(\"videoUrl\"))"
OLD_MEDIA_RULE = "    return bool(item.get(\"data\") or item.get(\"mediaEntId\"))"
COMPLETE_IS_ACCEPTED = "batch.get(\"needsPolling\") is True or batch.get(\"isComplete\") is True"


def refused_read(**item) -> httpx.Response:
    """The refused batch as a batch read answers it, its item changed by `item`."""
    return httpx.Response(200, json={"batch": {**REFUSED_BATCH,
                                               "content": [{**REFUSED_BATCH["content"][0], **item}]}})


@mutation(SUBMIT_FILE, SUBMIT_REFUSAL, "                if False:", expect="a refused item is never Completed")
def test_an_image_generate_whose_finished_item_vibes_refused_fails_at_once_with_its_refund():
    assert REFUSED_BATCH["isComplete"] is True and REFUSED_BATCH["hasError"] is False
    assert REFUSED_BATCH["content"][0]["error"] == REFUSED_TEXT
    fake = Vibes([], generate=answer(REFUSED))
    result, _ = run_submit(fake, job("vibes-image"))
    assert isinstance(result.job, Fail), "a refused item is never Completed: %s" % (result.job,)
    assert result.job.code == errors.CONTENT_REFUSED and REFUSED_TEXT in result.job.detail
    assert "batch" in result.job.state_patch and len(fake.sent("generate")) == 1
    assert errors.CODES[errors.CONTENT_REFUSED].terminal and errors.CODES[errors.CONTENT_REFUSED].refund


@mutation(BATCH_FILE, ITEM_REFUSAL, "        if c.get(\"error\"):", expect="a refused item fails the poll")
@mutation(BATCH_FILE, ITEM_REFUSAL, "        if not has_media(c):", expect="a refused item fails the poll")
@mutation(BATCH_FILE, MEDIA_RULE, OLD_MEDIA_RULE, expect="a refused item fails the poll")
def test_a_poll_that_reads_a_refused_item_fails_the_job_with_its_refund():
    for item in ({}, {"error": None}, {"error": REFUSED_TEXT, "mediaEntId": "1347", "imageUrl": "https://x/y.jpeg"}):
        result = poll(Vibes([], batch=refused_read(**item)), job("vibes-image", batch=REFUSED_BATCH["id"]))
        assert isinstance(result.outcome, Failed) and result.outcome.code == errors.CONTENT_REFUSED, (
            "a refused item fails the poll: %s %s" % (item, result.outcome))
        assert result.effects == (Success(),)
    assert REFUSED_TEXT in poll(Vibes([], batch=refused_read())).outcome.detail


@mutation(BATCH_FILE, COMPLETE_IS_ACCEPTED, "batch.get(\"needsPolling\") is True",
          expect="a refused batch is found, then failed")
def test_a_reconcile_that_reads_a_refused_batch_finds_it_and_the_next_poll_fails_it():
    """reconcile cannot fail a job: it finds the order (never unknown until the deadline), and the poll that follows
    fails it at once with its refund."""
    j = job("vibes-image", batch=REFUSED_BATCH["id"])
    for item in ({}, {"data": None}):
        found = reconcile(Vibes([], batch=refused_read(**item)), j)
        assert found == Found(REFUSED_BATCH["id"], {"batch": REFUSED_BATCH["id"]}, True), (
            "a refused batch is found, then failed: %s" % (found,))
        result = poll(Vibes([], batch=refused_read(**item)), j)
        assert isinstance(result.outcome, Failed) and result.outcome.code == errors.CONTENT_REFUSED


CHECKPOINT_BRANCH = ("    if is_checkpoint(resp):\n        seconds, cooldown = checkpoint_rest(ctx, settings, rests, "
                     "\"vibes security checkpoint on a poll\")")
NO_CHECKPOINT = ("    if False:\n        seconds, cooldown = checkpoint_rest(ctx, settings, rests, "
                 "\"vibes security checkpoint on a poll\")")
REST_GATE = "            left = self.left_s(account_id)\n            if left:\n                raise Resting(left)"
NO_REST_GATE = "            left = 0\n            if left:\n                raise Resting(left)"


@mutation(BATCH_FILE, CHECKPOINT_BRANCH, NO_CHECKPOINT, expect="the checkpoint rests the account")
@mutation(HTTP_FILE, REST_GATE, NO_REST_GATE, expect="no call while the account rests")
def test_the_checkpoint_rests_the_account_and_the_job_keeps_waiting():
    log: list = []
    rests = Rests(lambda: NOW)
    result = poll(Vibes(log, batch=answer(CHECKPOINT)), rests=rests)
    assert isinstance(result.outcome, Pending) and not result.outcome.answered, result.outcome
    assert result.outcome.next_in_s == 600 and result.effects == (
        Cooldown("vibes security checkpoint on a poll", NOW + timedelta(seconds=600), recheck=True),), (
        "the checkpoint rests the account: %s" % (result,))
    # another job of the same account makes no call during the rest; another account still reads
    log.clear()
    other = poll(Vibes(log, batch=answer(rec("s5-live/video_020_video_poll_after"))), rests=rests)
    assert log == [], "no call while the account rests: %s" % log
    assert isinstance(other.outcome, Pending) and other.outcome.next_in_s == 600
    assert isinstance(poll(Vibes(log, batch=answer(rec("s5-live/video_020_video_poll_after"))), rests=rests,
                           account_id="acc-2").outcome, Pending) and log == ["batch"]
    rests.now = lambda: NOW + timedelta(seconds=601)
    assert isinstance(poll(Vibes(log, batch=answer(rec("s5-live/recheck_001_recheck_batch"))),
                           rests=rests).outcome, Done), "after the rest the account reads again"


def test_a_transient_read_failure_is_read_again_at_the_interval():
    for bad in (answer(S4_500), httpx.ReadTimeout("slow"), httpx.Response(401, json={}),
                httpx.Response(200, json={"batch": None})):
        result = poll(Vibes([], batch=bad))
        assert isinstance(result.outcome, Pending) and not result.outcome.answered, bad
        assert result.outcome.next_in_s == 20 and result.effects == ()
    assert S4_500["body"] == {"error": "Failed to fetch generation batch"}


def test_the_poll_interval_is_the_setting_plus_up_to_half_again_never_less():
    assert SPEC.settings()["vibes.poll_interval_s"].default == 20
    assert [B.interval_s(SETTINGS, lambda r=r: r) for r in (0.0, 0.5, 0.999)] == [20, 25, 29]
    result = poll(Vibes([], batch=answer(rec("s5-live/video_020_video_poll_after"))), rand=lambda: 0.5)
    assert result.outcome.next_in_s == 25


# ---- reconcile -----------------------------------------------------------------------------------------------------
def reconcile(fake: Vibes, j=None, rests=None):
    j = j or job(batch="3fbc9c32-3f9d-47d1-aa43-377c9bf65697")
    return asyncio.run(B.reconcile(Ctx(fake.log), j, SETTINGS, rests or Rests(lambda: NOW), transport=fake.transport))


SHELL_UNKNOWN = "    return StillUnknown(answered=True)"
ACCEPTED_RULE = "    return (batch.get(\"needsPolling\") is True or batch.get(\"isComplete\") is True"
ALWAYS_ACCEPTED = "    return (True or batch.get(\"needsPolling\") is True or batch.get(\"isComplete\") is True"


@mutation(BATCH_FILE, SHELL_UNKNOWN, "    return Found(batch, {BATCH_KEY: batch}, True)",
          expect="an empty shell proves nothing")
@mutation(BATCH_FILE, ACCEPTED_RULE, ALWAYS_ACCEPTED,
          expect="an empty shell proves nothing")
def test_an_empty_shell_after_a_lost_generate_stays_unknown_and_is_never_sent_again():
    shells = [rec("s5-live/video_%03d_video_poll_during_post" % i) for i in (3, 4, 5, 6)] + [
        rec("s5-live/video_002_video_get_empty_batch"), rec("s5-live/image_002_image_get_empty_batch"),
        s4("shell_poll", 0), s4("shell_poll", 2)]
    for r in shells:
        log: list = []
        found = reconcile(Vibes(log, batch=answer(r)))
        assert found == StillUnknown(answered=True), "an empty shell proves nothing: %s %s" % (r["step"], found)
        assert log == ["batch"], "a reconcile only reads"


def test_a_batch_vibes_took_is_found_and_charged():
    for name in ("s5-live/video_008_video_poll_during_post", "s5-live/video_020_video_poll_after",
                 "s5-live/recheck_001_recheck_batch", "s6-live/i2v_004_poll"):
        found = reconcile(Vibes([], batch=answer(rec(name))))
        assert found == Found("3fbc9c32-3f9d-47d1-aa43-377c9bf65697",
                              {"batch": "3fbc9c32-3f9d-47d1-aa43-377c9bf65697"}, True), name


def test_a_reconcile_read_that_fails_settles_nothing_and_the_checkpoint_rests_the_account():
    for bad in (answer(S4_500), httpx.ConnectError("x"), httpx.Response(401, json={})):
        assert reconcile(Vibes([], batch=bad)) == StillUnknown(answered=False)
    rests = Rests(lambda: NOW)
    assert reconcile(Vibes([], batch=answer(CHECKPOINT)), rests=rests) == StillUnknown(answered=False)
    log: list = []
    assert reconcile(Vibes(log), rests=rests) == StillUnknown() and log == [] and rests.left_s("acc-1") == 600
    assert reconcile(Vibes([]), job()) == StillUnknown(), "no batch id: nothing to read"


# ---- fetch ---------------------------------------------------------------------------------------------------------
def fetch(fake: Vibes, out, rests=None) -> bytes:
    async def go():
        chunks = B.fetch(Ctx(fake.log), out, SETTINGS, rests or Rests(lambda: NOW), transport=fake.transport)
        return b"".join([c async for c in chunks])

    return asyncio.run(go())


def test_the_result_is_downloaded_by_its_content_item_id_and_delivered_as_downloaded():
    done = rec("s5-live/image_004_image_get_batch_after")["body"]["batch"]
    [out] = B.outputs(done, "image")
    fake = Vibes([], download=httpx.Response(200, headers={"content-type": "image/png"}, content=PNG))
    assert fetch(fake, out) == PNG
    sent = fake.seen[0]
    assert sent.url.path == "/api/download/png" and sent.url.params["id"] == done["id"] + "-content-0"
    video = B.outputs(rec("s5-live/recheck_001_recheck_batch")["body"]["batch"], "video")[0]
    fake = Vibes([], download=httpx.Response(200, headers={"content-type": "video/mp4"}, content=MP4))
    assert fetch(fake, video) == MP4 and fake.seen[0].url.path == "/api/download/video"


def test_a_download_that_is_not_the_file_raises_so_the_fetcher_tries_again():
    out = B.outputs(rec("s5-live/recheck_001_recheck_batch")["body"]["batch"], "video")[0]
    page = httpx.Response(200, headers={"content-type": "text/html"}, text="<html>")
    for bad in (httpx.Response(404, json={}), page):
        with pytest.raises(B.DownloadError):
            fetch(Vibes([], download=bad), out)
    rests = Rests(lambda: NOW)
    with pytest.raises(B.DownloadError):
        fetch(Vibes([], download=answer(rec("s5-live/video_125_video_download_0"))), out, rests)
    assert rests.left_s("acc-1") == 600, "the checkpoint on a download rests the account"
    log: list = []
    with pytest.raises(B.DownloadError):
        fetch(Vibes(log), out, rests)
    assert log == []


GONE_RULE = "                if resp.status_code == 404 and mime == \"application/json\":"
GONE_SAID = "                    if said:"


@mutation(BATCH_FILE, GONE_RULE, "                if False:", expect="the file does not exist: fail now")
def test_a_download_vibes_says_does_not_exist_fails_the_job_at_once():
    gone = rec("refused-261023/t2i_download_404")
    [out] = B.outputs(REFUSED_BATCH, "image")
    try:
        fetch(Vibes([], download=answer(gone)), out)
    except OutputGone as e:
        assert e.code == errors.OUTPUT_MISSING and gone["body"]["error"] in e.detail
    except B.DownloadError as e:
        raise AssertionError("the file does not exist: fail now, never try again: %s" % e) from None
    else:
        raise AssertionError("delivered a file that does not exist")
    assert errors.CODES[errors.OUTPUT_MISSING].terminal and errors.CODES[errors.OUTPUT_MISSING].refund


@mutation(BATCH_FILE, GONE_RULE, "                if mime == \"application/json\":", expect="a transient")
@mutation(BATCH_FILE, GONE_SAID, "                    if True:", expect="a transient")
def test_a_transient_download_failure_is_still_tried_again():
    [out] = B.outputs(REFUSED_BATCH, "image")
    for bad in (httpx.Response(404, json={}), httpx.Response(404, text="<html>not found</html>"),
                httpx.Response(404, json=["x"]), httpx.Response(500, json={"error": "Internal"}),
                httpx.Response(502, text="bad gateway"), answer(CHECKPOINT)):
        try:
            fetch(Vibes([], download=bad), out)
        except B.DownloadError:
            continue
        except OutputGone as e:
            raise AssertionError("a transient failure is tried again, never final: %s %s" % (
                bad.status_code, e)) from None
        raise AssertionError("delivered: %s" % bad.status_code)
    with pytest.raises(httpx.ConnectError):     # the fetcher retries an error from the network
        fetch(Vibes([], download=httpx.ConnectError("gone")), out)


# ---- the engine ----------------------------------------------------------------------------------------------------
def test_the_engine_shares_one_rest_map_across_its_calls_and_runs_no_command():
    engine = build_engine(SPEC, SETTINGS)
    assert isinstance(engine.rests, Rests)
    for kind in ("titles", "recover", "refresh_catalog"):
        with pytest.raises(ValueError, match="vibes runs no command"):
            asyncio.run(engine.run_command(None, kind, {}))


# ---- one account's calls: spaced, queued, and stopped by a checkpoint for all of them --------------------------------
class Clock:
    """A monotonic clock that a fake sleep moves forward."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        await asyncio.sleep(0)


def slow(resp: httpx.Response, starts: list, clock=None):
    """An async route that gives the loop away a few times before it answers, as a real network does."""
    async def handle(request):
        starts.append(clock() if clock else 0)
        for _ in range(3):
            await asyncio.sleep(0)
        return resp
    return handle


def polls(fake: Vibes, rests: Rests, n: int, settings=SETTINGS):
    async def go():
        return await asyncio.gather(*(B.poll(Ctx(fake.log), job(batch="b-%d" % i), settings, rests,
                                             transport=fake.transport, rand=lambda: 0.0) for i in range(n)))
    return asyncio.run(go())


GAP_WAIT = "            if wait > 0:\n                await self.sleep(wait)"
NO_GAP_WAIT = "            if False:\n                await self.sleep(wait)"
ACCOUNT_LOCK = "        async with self._locks.setdefault(account_id, asyncio.Lock()):"
NO_ACCOUNT_LOCK = "        if True:"


@mutation(HTTP_FILE, GAP_WAIT, NO_GAP_WAIT, expect="each call of one account starts the minimum gap after the last")
def test_the_calls_of_one_account_queue_and_start_the_minimum_gap_apart():
    gapped = FakeSettings(SPEC)
    assert gapped.get("vibes.account_min_gap_s") == 8 and SPEC.settings()["vibes.account_min_gap_s"].min == 2
    clock, starts = Clock(), []
    rests = Rests(lambda: NOW, settings=gapped, clock=clock, sleep=clock.sleep)
    fake = Vibes([], batch=slow(answer(rec("s5-live/video_020_video_poll_after")), starts, clock))
    results = polls(fake, rests, 3, gapped)
    assert all(isinstance(r.outcome, Pending) for r in results) and len(starts) == 3
    assert all(b - a >= 8 for a, b in zip(starts, starts[1:], strict=False)), (
        "each call of one account starts the minimum gap after the last: %s" % starts)


@mutation(HTTP_FILE, ACCOUNT_LOCK, NO_ACCOUNT_LOCK, expect="only the first call meets the checkpoint")
@mutation(HTTP_FILE, REST_GATE, NO_REST_GATE, expect="only the first call meets the checkpoint")
def test_concurrent_polls_of_one_account_after_a_checkpoint_make_one_call_and_all_rest():
    starts: list = []
    rests = Rests(lambda: NOW, settings=SETTINGS)
    fake = Vibes([], batch=slow(answer(CHECKPOINT), starts))
    results = polls(fake, rests, 3)
    assert len(starts) == 1, "only the first call meets the checkpoint: %d calls" % len(starts)
    assert all(isinstance(r.outcome, Pending) and not r.outcome.answered and r.outcome.next_in_s == 600
               for r in results), results
    assert sum(1 for r in results if r.effects) == 1 and rests.left_s("acc-1") == 600


CHECKPOINT_STARTS_REST = "            if is_checkpoint(resp) and self.settings is not None:"


@mutation(HTTP_FILE, CHECKPOINT_STARTS_REST, "            if False:", expect="the checkpoint is no strike")
def test_a_checkpoint_on_the_account_check_rests_the_account_and_is_no_strike():
    rests = Rests(lambda: NOW, settings=SETTINGS)
    transport = httpx.MockTransport(lambda request: answer(CHECKPOINT))
    health = asyncio.run(check_account(Ctx([]), SETTINGS, transport=transport, now=lambda: NOW, rests=rests))
    assert (health.state, health.effects) == ("unknown", (
        Cooldown("vibes security checkpoint", NOW + timedelta(seconds=600), recheck=True),)), (
        "the checkpoint is no strike, it rests the account: %s" % (health,))


@mutation(HTTP_FILE, "            del self._until[account_id]", "            pass", expect="an ended rest stays")
def test_an_ended_rest_is_dropped_from_the_map():
    rests = Rests(lambda: NOW)
    rests.start("acc-1", 60)
    assert rests.left_s("acc-1") == 60 and "acc-1" in rests._until
    rests.now = lambda: NOW + timedelta(seconds=61)
    assert rests.left_s("acc-1") == 0 and "acc-1" not in rests._until, "an ended rest stays in the map"


MIME_RULE = "                if resp.status_code != 200 or mime != out.mime:"
SIGNATURE_RULE = "                if not has_signature(kind, head):"


@mutation(BATCH_FILE, MIME_RULE, "                if resp.status_code != 200:", expect="not the file")
@mutation(BATCH_FILE, SIGNATURE_RULE, "                if False:", expect="not the file")
def test_a_download_is_delivered_only_with_its_media_type_and_its_file_signature():
    png = B.outputs(rec("s5-live/image_004_image_get_batch_after")["body"]["batch"], "image")[0]
    mp4 = B.outputs(rec("s5-live/recheck_001_recheck_batch")["body"]["batch"], "video")[0]
    html = b"<!DOCTYPE html><html><title>Vercel Security Checkpoint</title></html>"
    for out, mime, content in ((png, "application/octet-stream", PNG), (png, "image/png", html),
                               (png, "image/png", b'{"error": "x"}'), (mp4, "video/mp4", html),
                               (mp4, "application/octet-stream", MP4), (mp4, "video/mp4", PNG)):
        fake = Vibes([], download=httpx.Response(200, headers={"content-type": mime}, content=content))
        try:
            fetch(fake, out)
        except B.DownloadError:
            continue
        raise AssertionError("not the file, delivered: %s %s %r" % (out.mime, mime, content[:20]))
    fake = Vibes([], download=httpx.Response(200, headers={"content-type": "image/png; x=1"}, content=PNG))
    assert fetch(fake, png) == PNG
