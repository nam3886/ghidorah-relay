"""grok's submit (`engine/submit.py`) driven by the 06/11 captures (fixtures/grok/spike-261106) through the kit's fake
curl_cffi session and WebSocket: no grok.com, no real cookie. Each money rule has a test here and a mutation that
breaks it."""

import asyncio
import itertools
import json
import logging
from datetime import timedelta

import pytest
from grokkit import (
    NOW,
    USER_ID,
    Clock,
    Ctx,
    Cut,
    FakeSession,
    FakeSocket,
    Resp,
    assert_no_secret,
    fixture,
    input_ref,
    job,
    names,
    settings,
)

from relay_core.providers import (
    REFUSED_BY,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Facts,
    Fail,
    NotTaken,
    ProxyBad,
    Rest,
    Retry,
    Success,
    Suspect,
)
from relay_core.testing.mutation import mutation
from relay_grok import errors
from relay_grok.constants import IMAGINE_WS, NEW_CONVERSATION_PATH, QUOTA_PATH, QUOTA_WINDOW, UPLOAD_PATH
from relay_grok.engine import submit as S

SUBMIT_FILE = "packages/provider-grok/src/relay_grok/engine/submit.py"
QUOTA = ("POST", QUOTA_PATH)
WS = ("WS", IMAGINE_WS)
UPLOAD = ("POST", UPLOAD_PATH)
SEND = ("POST", NEW_CONVERSATION_PATH)
SPIKE_REQUEST = "00000000-0000-0000-0000-000000000001"      # -> img_000...01, the capture's request_id
SPIKE_USER = "d0000000-0000-4000-8000-000000000001"
VIDEO_REFILL = NOW.replace(year=2026, month=11, day=7, hour=5, minute=30, second=10)

REPORT_MARK = ("        await self._core(lambda: self.ctx.report_ref(request_id, dict(self.state)))\n"
               "        await self._core(self.ctx.mark_sent)\n")
MARK_REPORT = ("        await self._core(self.ctx.mark_sent)\n"
               "        await self._core(lambda: self.ctx.report_ref(request_id, dict(self.state)))\n")
MARK_SEND = ("            await self._mark(request_id, **{STATE_SENT_TEXT: self.text})\n"
             "            await sock.send_json(frame)\n")
SEND_MARK = ("            await sock.send_json(frame)\n"
             "            await self._mark(request_id, **{STATE_SENT_TEXT: self.text})\n")
UPLOADS = ("        for i, ref in enumerate(refs):\n"
           "            ids.append(await web.upload(await self.deps.read_media(ref), ref.mime,\n"
           "                                        \"image%d%s\" % (i, _EXT.get(ref.mime, \".jpg\"))))\n"
           "        if len(ids) != len(refs):\n"
           "            return _result(Retry(errors.HTTP_ERROR))\n")
UPLOADS_FIRST_ONLY = ("        for i, ref in enumerate(refs[:1]):\n"
                      "            ids.append(await web.upload(await self.deps.read_media(ref), ref.mime,\n"
                      "                                        \"image%d%s\" % (i, _EXT.get(ref.mime, \".jpg\"))))\n")
AFTER_SEND_UNKNOWN = ('        if self.sent:\n'
                      '            return await self._unknown("the run failed after the send: %s" % cause)\n')
DECOY_REST = ('            rest = Rest("video", self._later("grok.decoy_rest_s"), "decoy")\n'
              "            return await self._keep(_result(Fail(errors.DECOY_VIDEO, why), rest), why)\n")


# ---- the kit -----------------------------------------------------------------------------------------------------
def quota(name: str = "quota_before") -> Resp:
    f = fixture(name)
    return Resp(f["status"], f.get("headers"), json_body=f["body"])


def frames_in(name: str) -> list[dict]:
    """The frames grok sent in an Imagine capture, in order."""
    return [f["data"] for f in fixture(name)["frames"] if f["dir"] == "in"]


class Timed(Resp):
    """A streamed answer whose lines arrive at their capture times (`t_ms` from the POST) on the run's fake clock."""

    def __init__(self, name: str, clock: Clock, *, cut: int | None = None, hang: bool = False, lines=None,
                 status: int | None = None):
        f = fixture(name)
        super().__init__(status or f["status"], f.get("headers"), lines=lines or f["lines"], cut=cut, hang=hang)
        self.t_ms, self.clock = f.get("t_ms") or [0] * len(self.lines), clock

    async def aiter_lines(self):
        start = self.clock.t
        lines = self.lines if self.cut is None else self.lines[:self.cut]
        for t, line in zip(self.t_ms, lines, strict=False):
            self.clock.t = start + t / 1000
            yield line.encode()
        await self._end()


def refused(name: str) -> Resp:
    """A refused answer from a capture: a JSON body or an HTML page, with its headers."""
    if name.endswith(".html"):
        return Resp(403, {"content-type": "text/html"}, text=fixture(name))
    f = fixture(name)
    if "lines" in f:
        return Resp(f["status"], f.get("headers"), lines=f["lines"])
    return Resp(f["status"], f.get("headers"), json_body=f["body"])


def deps(session, clock=None, *, uuids=None, hold_margin_s=15.0, read_media=None) -> S.Deps:
    clock = clock or Clock()
    ids = uuids or itertools.repeat(SPIKE_REQUEST)

    async def media(ref):
        return b"\x89PNG-input-%d" % ref.idx
    return S.Deps(make_session=session, read_media=read_media or media, clock=clock, now=clock.now,
                  uuid4=lambda: next(ids), hold_margin_s=hold_margin_s)


def run(ctx, j, session, clock=None, cap_s: float = 10.0, **kw):
    """One submit, bounded here too, so a hang fails the test instead of the run."""
    async def go():
        return await asyncio.wait_for(S.submit(ctx, j, ctx.settings, deps=deps(session, clock, **kw)), cap_s)
    return asyncio.run(go())


def t2i_session(ctx, frames, *, q="quota_before", after=None, then=None):
    sock = FakeSocket(frames, then=then, calls=ctx.calls)
    session = FakeSession({QUOTA: [quota(q), after or quota("quota_after_t2i")], WS: sock}, calls=ctx.calls)
    return session, sock


def media_session(ctx, answer, *, q="quota_before", after=None, uploads=None):
    routes = {QUOTA: [quota(q), after or quota("quota_after_t2v")], SEND: answer}
    if uploads is not None:
        routes[UPLOAD] = uploads
    return FakeSession(routes, calls=ctx.calls)


def sends(session) -> list:
    return [r for r in session.requests if r.method == "POST" and r.path == NEW_CONVERSATION_PATH]


def body_of(request) -> dict:
    return json.loads(request.data)


# ---- the order of a send -----------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, REPORT_MARK, MARK_REPORT, expect="report_ref < mark_sent < send")
@mutation(SUBMIT_FILE, "        await self._core(lambda: self.ctx.report_ref(request_id, dict(self.state)))\n", "",
          expect="report_ref < mark_sent < send")
@mutation(SUBMIT_FILE, MARK_SEND, SEND_MARK, expect="report_ref < mark_sent < send")
def test_t2i_reports_the_ref_marks_sent_sends_one_frame_and_delivers_n_images():
    ctx = Ctx()
    session, sock = t2i_session(ctx, frames_in("imagine_t2i_n2"))
    result = run(ctx, job("t2i", n=2), session)
    seq = names(ctx.calls)
    assert "report_ref" in seq and "mark_sent" in seq and "item.create" in seq, "report_ref < mark_sent < send"
    assert seq.index("report_ref") < seq.index("mark_sent") < seq.index("item.create"), \
        "report_ref < mark_sent < send"
    assert seq.index("reset") < seq.index("report_ref")
    assert len(sock.generations()) == 1
    props = sock.generations()[0]["item"]["content"][0]["properties"]
    assert props["num_generations"] == 2 and props["aspect_ratio"] == "1:1" and props["enable_pro"] is False
    first_ref, first_state = ctx.refs[0]
    assert first_ref == "img_00000000000000000000000000000001"
    assert first_state["op"] == "t2i" and first_state["sent_text"] == job("t2i").sent_text
    assert ctx.refs[-1][1]["conversation_id"] == "c0000000-0000-4000-8000-000000000008"
    assert isinstance(result.job, Completed)
    assert [o.ref for o in result.job.outputs] == [
        "https://imagine-public.x.ai/imagine-public/images/af000000-0000-4000-8000-000000000003.jpg",
        "https://imagine-public.x.ai/imagine-public/images/a1000000-0000-4000-8000-000000000002.jpg"]
    assert [o.mime for o in result.job.outputs] == ["image/jpeg"] * 2
    assert Success() in result.account


@mutation(SUBMIT_FILE, '            request_id = "img_" + self.deps.uuid4().replace("-", "")\n',
          '            request_id = "img_" + "0" * 32\n', expect="a fresh request id per run")
def test_each_run_uses_a_fresh_request_id():
    refs = []
    uuids = (str(i) * 8 + "-0000-4000-8000-" + str(i) * 12 for i in itertools.count(1))
    j = job("t2i", n=2)
    for _ in range(2):
        ctx = Ctx()
        frames = frames_in("imagine_t2i_n2")
        session, _sock = t2i_session(ctx, frames, then=Cut("closed"))
        run(ctx, j, session, uuids=uuids)
        refs.append(ctx.refs[0][0])
    assert refs[0] != refs[1], "a fresh request id per run"


@mutation(SUBMIT_FILE, UPLOADS, UPLOADS_FIRST_ONLY, expect="an edit was sent without all its images")
def test_the_edit_uploads_every_input_before_the_one_post_and_names_their_ids():
    ctx = Ctx()
    ups = [Resp(200, json_body={"fileMetadata": {"fileMetadataId": "file-%d" % i}}) for i in range(3)]
    session = media_session(ctx, refused("ndjson_edit"), uploads=ups, after=quota("quota_after_t2v"))
    result = run(ctx, job("edit", inputs=[input_ref(0), input_ref(1), input_ref(2)]), session)
    seq = names(ctx.calls)
    assert seq.count("POST " + NEW_CONVERSATION_PATH) == 1
    sent = sends(session)
    assets = body_of(sent[0])["mediaGenInput"]["imageToImage"]["inputAssets"] if sent else None
    assert assets == ["file-0", "file-1", "file-2"], "an edit was sent without all its images"
    assert seq.count("POST " + UPLOAD_PATH) == 3
    assert max(i for i, n in enumerate(seq) if n == "POST " + UPLOAD_PATH) < seq.index("mark_sent")
    assert body_of(sent[0])["modelName"] == "imagine-image-edit"
    assert ctx.refs[0][1]["inputs"] == ["file-0", "file-1", "file-2"]
    assert isinstance(result.job, Completed)
    out = result.job.outputs[0]
    assert out.ref == "https://assets.grok.com/users/%s/generated/af000000-0000-4000-8000-000000000002/image.jpg" % (
        SPIKE_USER)
    assert out.mime == "image/jpeg" and out.upstream_id == "af000000-0000-4000-8000-000000000002"


def test_an_upload_failure_sends_nothing():
    ctx = Ctx()
    session = media_session(ctx, refused("ndjson_edit"), uploads=[Resp(200, json_body={"fileMetadata": {
        "fileMetadataId": "file-0"}}), Cut("upload broke")])
    result = run(ctx, job("edit", inputs=[input_ref(0), input_ref(1)]), session)
    assert result.job == Retry(errors.HTTP_ERROR)
    assert "mark_sent" not in names(ctx.calls) and not sends(session)


def test_a_terminal_upload_error_fails_refunded_with_input_unusable():
    ctx = Ctx()
    session = media_session(ctx, refused("ndjson_edit"), uploads=Resp(200, json_body={"terminalError": "bad image"}))
    result = run(ctx, job("edit"), session)
    assert isinstance(result.job, Fail) and result.job.code == errors.INPUT_UNUSABLE
    assert "mark_sent" not in names(ctx.calls) and not sends(session)


# ---- the quota read before the send ------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, "        if left < self.n:\n", "        if False:\n",
          expect="an empty quota must stop before the send")
def test_too_few_quota_left_sends_nothing_and_rests_the_pool():
    ctx = Ctx()
    session, sock = t2i_session(ctx, frames_in("imagine_t2i_n2"), q="quota_after_t2i")      # imagePro 2
    result = run(ctx, job("t2i", n=3), session)
    assert result.job == Retry(S.POOL_OUT_OF_POINTS), "an empty quota must stop before the send"
    assert result.account == (Balance("image", 2, NOW + timedelta(seconds=86400), window=QUOTA_WINDOW),)
    assert "ws open" not in names(ctx.calls) and "mark_sent" not in names(ctx.calls)

    ctx = Ctx()
    session = media_session(ctx, refused("ndjson_t2v"), q="quota_video_exhausted")
    result = run(ctx, job("t2v"), session)
    assert result.job == Retry(S.POOL_OUT_OF_POINTS), "an empty quota must stop before the send"
    assert result.account == (Balance("video", 0, VIDEO_REFILL, window=QUOTA_WINDOW),)
    assert not sends(session) and "mark_sent" not in names(ctx.calls)


# ---- text to video -----------------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, "                    if new:\n                        await self._ids(new)\n", "",
          expect="the video id was not reported before reading on")
def test_t2v_sends_6s_720p_custom_mode_and_reports_the_video_id_before_reading_on():
    ctx, clock = Ctx(settings_=settings(video_submit_timeout_s=60)), Clock()
    # the capture's video ids come on line 5; the answer hangs after it
    session = media_session(ctx, Timed("ndjson_t2v", clock, cut=5, hang=True))
    result = run(ctx, job("t2v"), session, clock, hold_margin_s=59.7)
    body = body_of(sends(session)[0])
    v = body["mediaGenInput"]["textToVideo"]
    assert v["duration"] == 6 and v["resolutionName"] == "720p" and v["aspectRatio"] == "16:9"
    assert body["message"].endswith(" --mode=custom") and body["kind"] == "CONVERSATION_KIND_IMAGINE"
    assert body["modelName"] == "imagine-video-gen"
    assert isinstance(result.job, Ambiguous)
    last = ctx.refs[-1][1]
    assert last.get("video_id") == "90000000-0000-4000-8000-000000000001", \
        "the video id was not reported before reading on"
    assert last["video_post_id"] == "80000000-0000-4000-8000-000000000001"
    assert last["conversation_id"] == "c0000000-0000-4000-8000-000000000007"
    assert ctx.refs[-1][0] == "c0000000-0000-4000-8000-000000000007"
    assert last["sent_text"] == body["message"]


@mutation(SUBMIT_FILE, '            return "decoy"\n', '            return "done"\n',
          expect="a decoy video was delivered")
@mutation(SUBMIT_FILE, DECOY_REST, "            return await self._keep(_result(Fail(errors.DECOY_VIDEO, why)), why)\n",
          expect="the decoy must rest the video pool")
def test_a_real_t2v_completes_and_a_decoy_fails_refunded_and_rests_the_video_pool():
    ctx, clock = Ctx(), Clock()
    result = run(ctx, job("t2v"), media_session(ctx, Timed("ndjson_t2v", clock)), clock)
    assert isinstance(result.job, Completed)
    out = result.job.outputs[0]
    assert out.ref == ("https://assets.grok.com/users/%s/generated/a1000000-0000-4000-8000-000000000001/"
                       "generated_video.mp4" % SPIKE_USER)
    assert out.mime == "video/mp4" and out.upstream_id == "90000000-0000-4000-8000-000000000001"

    ctx, clock = Ctx(), Clock()
    result = run(ctx, job("t2v"), media_session(ctx, Timed("ndjson_t2v_decoy", clock)), clock)
    assert not isinstance(result.job, Completed), "a decoy video was delivered"
    assert isinstance(result.job, Fail) and result.job.code == errors.DECOY_VIDEO
    rests = [e for e in result.account if isinstance(e, Rest)]
    assert rests and rests[0].pool == "video" and rests[0].until == clock.now() + timedelta(seconds=86400), \
        "the decoy must rest the video pool"
    assert any("stock clip" in e[0] for e in ctx.events)


@mutation(SUBMIT_FILE, "(not a.rrated_present or a.progress_at.get(100, 0.0) < decoy_min_s):",
          "(a.progress_at.get(100, 0.0) < decoy_min_s):",
          expect="a final without rRated is a decoy even with no time check")
def test_decoy_min_s_zero_still_checks_rrated():
    ctx, clock = Ctx(settings_=settings(decoy_min_s=0)), Clock()
    result = run(ctx, job("t2v"), media_session(ctx, Timed("ndjson_t2v_decoy", clock)), clock)
    assert isinstance(result.job, Fail) and result.job.code == errors.DECOY_VIDEO, \
        "a final without rRated is a decoy even with no time check"
    # a real video slower than the setting, rRated false: delivered
    ctx, clock = Ctx(settings_=settings(decoy_min_s=0)), Clock()
    assert isinstance(run(ctx, job("t2v"), media_session(ctx, Timed("ndjson_t2v", clock)), clock).job, Completed)


def test_a_fast_video_with_rrated_is_still_a_decoy():
    ctx, clock = Ctx(settings_=settings(decoy_min_s=60)), Clock()
    result = run(ctx, job("t2v"), media_session(ctx, Timed("ndjson_t2v", clock)), clock)
    assert isinstance(result.job, Fail) and result.job.code == errors.DECOY_VIDEO


# ---- short delivery, more than n, refused, empty -----------------------------------------------------------------
def _n2_with(drop_final_of: str | None = None, moderate: tuple[str, ...] = ()) -> list[dict]:
    out = []
    for f in frames_in("imagine_t2i_n2"):
        f = dict(f)
        if f.get("type") == "image" and f.get("percentage_complete") == 100 and f.get("job_id") == drop_final_of:
            continue
        if f.get("current_status") == "completed" and f.get("job_id") in moderate:
            f["moderated"] = True
        out.append(f)
    return out


@mutation(SUBMIT_FILE, "            return await self._keep(_result(Completed(outputs), Success()), note)\n",
          "            return await self._keep(_result(Completed(outputs), Success()))\n",
          expect="a short delivery must say so on the timeline")
def test_short_delivery_charges_full_with_a_note_and_zero_fails_refunded():
    ctx = Ctx()
    session, _ = t2i_session(ctx, frames_in("imagine_t2i_n2"), after=quota("quota_before"))
    result = run(ctx, job("t2i", n=3), session)
    assert isinstance(result.job, Completed) and len(result.job.outputs) == 2
    assert any("grok made 2 of the 3 images" in e[0] for e in ctx.events), \
        "a short delivery must say so on the timeline"

    ctx = Ctx()
    session, _ = t2i_session(ctx, _n2_with(moderate=("af000000-0000-4000-8000-000000000003",
                                                     "a1000000-0000-4000-8000-000000000002")))
    result = run(ctx, job("t2i", n=2), session)
    assert isinstance(result.job, Fail) and result.job.code == errors.CONTENT_REFUSED

    ctx = Ctx()
    session, _ = t2i_session(ctx, _n2_with(moderate=("af000000-0000-4000-8000-000000000003",)))
    result = run(ctx, job("t2i", n=2), session)
    assert isinstance(result.job, Completed) and len(result.job.outputs) == 1
    assert result.job.outputs[0].upstream_id == "a1000000-0000-4000-8000-000000000002"

    ctx = Ctx()
    session, _ = t2i_session(ctx, frames_in("imagine_moderated"))
    assert run(ctx, job("t2i"), session).job.code == errors.CONTENT_REFUSED


@mutation(SUBMIT_FILE, "enumerate(finals[:self.n]))", "enumerate(finals))", expect="more than n images were delivered")
def test_more_finals_than_n_delivers_the_first_n():
    ctx = Ctx()
    session, _ = t2i_session(ctx, frames_in("imagine_t2i_n2"))
    result = run(ctx, job("t2i", n=1), session)
    assert isinstance(result.job, Completed)
    assert len(result.job.outputs) == 1, "more than n images were delivered"
    assert result.job.outputs[0].upstream_id == "af000000-0000-4000-8000-000000000003"
    assert any("the first 1 are delivered" in e[0] for e in ctx.events)


def test_a_settled_answer_with_no_image_and_no_reason_is_no_image():
    ctx = Ctx()
    session, _ = t2i_session(ctx, [f for f in frames_in("imagine_t2i_n2") if f.get("type") != "image"])
    result = run(ctx, job("t2i", n=2), session)
    assert isinstance(result.job, Fail) and result.job.code == errors.NO_IMAGE


@mutation(SUBMIT_FILE, '    if a.moderated or a.safety:\n        return "refused"\n', "",
          expect="a moderated edit was delivered")
def test_the_edit_moderated_is_content_refused_and_an_ended_empty_answer_is_no_image():
    ctx = Ctx()
    result = run(ctx, job("edit"), media_session(ctx, refused("ndjson_edit_moderated"), uploads=refused("upload_v2")))
    assert not isinstance(result.job, Completed), "a moderated edit was delivered"
    assert isinstance(result.job, Fail) and result.job.code == errors.CONTENT_REFUSED
    lines = [x for x in fixture("ndjson_edit")["lines"] if "streamingImageGenerationResponse" not in x]
    ctx = Ctx()
    result = run(ctx, job("edit"), media_session(ctx, Resp(200, lines=lines), uploads=refused("upload_v2")))
    assert isinstance(result.job, Fail) and result.job.code == errors.NO_IMAGE
    lines = [x for x in fixture("ndjson_t2v")["lines"] if "streamingVideoGenerationResponse" not in x]
    ctx = Ctx()
    result = run(ctx, job("t2v"), media_session(ctx, Resp(200, lines=lines)))
    assert isinstance(result.job, Fail) and result.job.code == errors.NO_VIDEO


# ---- limits and refusals -----------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, '        if kind == "limit":\n', '        if kind == "limit-x":\n',
          expect="a limit answer must move the job to another account")
def test_a_limit_answer_moves_the_job_to_another_account_then_fails():
    ctx = Ctx()
    session = media_session(ctx, refused("send_out_of_quota"))
    result = run(ctx, job("t2v"), session)
    assert isinstance(result.job, NotTaken) and result.job.code == S.POOL_OUT_OF_POINTS, \
        "a limit answer must move the job to another account"
    assert result.job.state_patch[REFUSED_BY] == ["acc-1"]
    assert result.account == (Balance("video", 0, NOW + timedelta(seconds=86400), window=QUOTA_WINDOW),)
    assert len(sends(session)) == 1

    ctx = Ctx(settings_=settings(refusal_max_accounts=2))
    result = run(ctx, job("t2v", state={REFUSED_BY: ["acc-0"]}), media_session(ctx, refused("send_out_of_quota")))
    assert isinstance(result.job, Fail) and result.job.code == errors.LIMIT
    assert result.job.state_patch[REFUSED_BY] == ["acc-0", "acc-1"]

    # the Imagine limit frame before any job started
    ctx = Ctx()
    session, sock = t2i_session(ctx, frames_in("imagine_limit_frame"))
    result = run(ctx, job("t2i"), session)
    assert isinstance(result.job, NotTaken) and result.job.code == S.POOL_OUT_OF_POINTS
    assert Balance("image", 0, NOW + timedelta(seconds=86400), window=QUOTA_WINDOW) in result.account
    assert len(sock.generations()) == 1


@mutation(SUBMIT_FILE, AFTER_SEND_UNKNOWN, "", expect="a failure after the send must be ambiguous")
def test_a_refusal_after_the_first_line_is_never_not_taken():
    lines = fixture("ndjson_t2v")["lines"][:5] + ['{"error":{"code":8,"message":"usage limit reached"}}']
    ctx = Ctx()
    result = run(ctx, job("t2v"), media_session(ctx, Resp(200, lines=lines)))
    assert isinstance(result.job, Ambiguous), "a failure after the send must be ambiguous"

    ctx = Ctx()
    result = run(ctx, job("t2v"), media_session(ctx, Resp(200, lines=fixture("ndjson_t2v")["lines"], cut=6)))
    assert isinstance(result.job, Ambiguous), "a failure after the send must be ambiguous"

    ctx = Ctx()
    result = run(ctx, job("edit"), media_session(ctx, refused("rest_403_code7"), uploads=refused("upload_v2")))
    assert isinstance(result.job, NotTaken) and result.job.code == errors.ANTIBOT
    assert any(isinstance(e, Cooldown) for e in result.account)

    ctx = Ctx()
    result = run(ctx, job("t2v"), media_session(ctx, refused("rest_401_bad_credentials")))
    assert isinstance(result.job, NotTaken) and result.job.code == S.POOL_LOGGED_OUT
    assert any(isinstance(e, Suspect) for e in result.account)

    ctx = Ctx()
    result = run(ctx, job("t2v"), media_session(ctx, refused("rest_400_code3")))
    assert isinstance(result.job, Fail) and result.job.code == errors.HTTP_ERROR

    ctx = Ctx()
    result = run(ctx, job("t2v"), media_session(ctx, Resp(500, text="upstream connect error")))
    assert isinstance(result.job, Ambiguous)


@mutation("packages/provider-grok/src/relay_grok/engine/wire.py",
          "    if status == 429 or (400 <= status < 500 and is_limit(body)):",
          "    if status == 429 or is_limit(body):", expect="a 5xx with limit words must be ambiguous")
def test_a_5xx_with_limit_words_is_ambiguous_never_another_account():
    ctx = Ctx()
    answer = Resp(500, {"content-type": "application/json"},
                  json_body={"error": {"code": 13, "message": "usage limit during response processing"}})
    session = media_session(ctx, answer)
    result = run(ctx, job("t2v"), session)
    assert isinstance(result.job, Ambiguous), "a 5xx with limit words must be ambiguous"
    assert not any(isinstance(e, Balance) for e in result.account)
    assert len(sends(session)) == 1


@mutation(SUBMIT_FILE, "    if a.limit and not a.jobs:\n", "    if a.limit:\n",
          expect="a limit after a job started must be ambiguous")
def test_an_imagine_limit_after_a_job_started_is_ambiguous():
    limit = frames_in("imagine_limit_frame")[-1]
    ctx = Ctx()
    session, sock = t2i_session(ctx, frames_in("imagine_t2i_n2")[:2] + [limit])
    result = run(ctx, job("t2i", n=2), session)
    assert isinstance(result.job, Ambiguous), "a limit after a job started must be ambiguous"
    assert len(sock.generations()) == 1


@mutation(SUBMIT_FILE, '        if verdict == "throttled":\n', '        if verdict == "throttled-x":\n',
          expect="grok's image rate limit before anything was made must move the job to another account")
def test_the_real_imagine_rate_limit_moves_the_job_and_rests_the_image_pool():
    # 06/11 live: one error frame, no session frame, quota unchanged, no conversation made
    ctx = Ctx()
    session, sock = t2i_session(ctx, frames_in("imagine_rate_limit_frame"))
    result = run(ctx, job("t2i"), session)
    assert isinstance(result.job, NotTaken) and result.job.code == S.POOL_THROTTLED, \
        "grok's image rate limit before anything was made must move the job to another account"
    assert result.job.state_patch[REFUSED_BY] == ["acc-1"]
    rests = [e for e in result.account if isinstance(e, Rest)]
    assert [(r.pool, r.until) for r in rests] == [("image", NOW + timedelta(seconds=3600))]
    assert not any(isinstance(e, Balance) and e.left_n == 0 for e in result.account), "uses are left: no empty pool"
    assert len(sock.generations()) == 1

    ctx = Ctx(settings_=settings(rate_limit_rest_s=120))
    session, _ = t2i_session(ctx, frames_in("imagine_rate_limit_frame"))
    result = run(ctx, job("t2i"), session)
    rests = [(e.pool, e.until) for e in result.account if isinstance(e, Rest)]
    assert rests == [("image", NOW + timedelta(seconds=120))]

    ctx = Ctx(settings_=settings(refusal_max_accounts=2))
    session, _ = t2i_session(ctx, frames_in("imagine_rate_limit_frame"))
    result = run(ctx, job("t2i", state={REFUSED_BY: ["acc-0"]}), session)
    assert isinstance(result.job, Fail) and result.job.code == errors.LIMIT
    assert result.job.state_patch[REFUSED_BY] == ["acc-0", "acc-1"]


@mutation(SUBMIT_FILE, "    if a.rate_limit and a.conversation_id is None and not a.jobs:\n",
          "    if a.rate_limit and not a.jobs:\n", expect="a rate limit after the session frame must be ambiguous")
def test_a_rate_limit_after_the_session_frame_or_a_job_is_ambiguous():
    rate = frames_in("imagine_rate_limit_frame")[-1]
    session_frame = frames_in("imagine_t2i_n2")[0]
    for frames in ([session_frame, rate], frames_in("imagine_t2i_n2")[:2] + [rate]):
        ctx = Ctx()
        session, sock = t2i_session(ctx, frames)
        result = run(ctx, job("t2i", n=2), session)
        assert isinstance(result.job, Ambiguous), "a rate limit after the session frame must be ambiguous"
        assert not any(isinstance(e, Rest) for e in result.account)
        assert len(sock.generations()) == 1


def test_t2i_breaking_after_the_send_is_ambiguous_and_never_a_second_frame():
    head = frames_in("imagine_t2i_n2")[:3]
    odd = {"type": "error", "err_code": "x", "err_msg": "something odd"}
    for frames, then in ((head, Cut("socket closed")),       # the socket closed
                         (head + [odd], None),                # an error frame that is no limit and no safety
                         (head + ["not json"], None)):        # an unreadable frame, then nothing: the timeout
        ctx = Ctx(settings_=settings(image_submit_timeout_s=30))
        session, sock = t2i_session(ctx, frames, then=then)
        result = run(ctx, job("t2i", n=2), session, hold_margin_s=29.7)
        assert isinstance(result.job, Ambiguous), "a failure after the send must be ambiguous"
        assert len(sock.generations()) == 1


@mutation(SUBMIT_FILE, "            return _result(NotTaken(errors.CLOUDFLARE), ",
          "            return _result(Retry(errors.CLOUDFLARE), ", expect="cloudflare uses no attempt")
def test_cloudflare_and_antibot_before_the_send_rest_the_account_with_no_attempt():
    ctx = Ctx()
    result = run(ctx, job("t2i"), FakeSession({QUOTA: refused("rest_403_cloudflare.html")}, calls=ctx.calls))
    assert isinstance(result.job, NotTaken) and result.job.code == errors.CLOUDFLARE, "cloudflare uses no attempt"
    assert result.account == (Cooldown("grok.com answered a Cloudflare page", NOW + timedelta(seconds=600),
                                       recheck=True),)
    assert "mark_sent" not in names(ctx.calls)

    ctx = Ctx()
    result = run(ctx, job("t2v"), FakeSession({QUOTA: refused("rest_403_code7")}, calls=ctx.calls))
    assert isinstance(result.job, NotTaken) and result.job.code == errors.ANTIBOT
    assert [type(e) for e in result.account] == [Cooldown]
    assert result.account[0].until == NOW + timedelta(seconds=1800)
    assert "mark_sent" not in names(ctx.calls)


@mutation(SUBMIT_FILE, "                return _result(Retry(POOL_LOGGED_OUT), *effects)\n",
          "                return _result(Retry(POOL_LOGGED_OUT))\n", expect="a signed-out account must take a strike")
def test_signed_out_before_the_send_is_a_strike_and_sends_nothing():
    cases = (
        {QUOTA: refused("rest_401_no_credentials")},
        {QUOTA: refused("rest_401_bad_credentials")},
        {QUOTA: quota(), WS: RuntimeError("curl: (22) Refused WebSocket upgrade: 401")},
    )
    for routes in cases:
        ctx = Ctx()
        result = run(ctx, job("t2i"), FakeSession(routes, calls=ctx.calls))
        assert result.job == Retry(S.POOL_LOGGED_OUT)
        assert any(isinstance(e, Suspect) for e in result.account), "a signed-out account must take a strike"
        assert "mark_sent" not in names(ctx.calls)


def test_a_failed_proxy_rests_the_account_and_retries():
    class ProxyError(Exception):
        pass
    ctx = Ctx()
    result = run(ctx, job("t2i"), FakeSession({QUOTA: ProxyError("proxy user:PLANTED-pw failed")}, calls=ctx.calls))
    assert result.job == Retry(S.POOL_PROXY_FAILED)
    assert [type(e) for e in result.account] == [ProxyBad, Cooldown]
    assert_no_secret(result, ctx.events)


# ---- bounds, the quota after, evidence, secrets ------------------------------------------------------------------
@mutation(SUBMIT_FILE, "        bound = asyncio.timeout(max(0.0, self.deadline - self.deps.clock()))\n",
          "        bound = asyncio.timeout(None)\n", expect="the verdict did not come within the submit timeout")
def test_the_verdict_comes_within_the_submit_timeout_whatever_hangs():
    ctx = Ctx(settings_=settings(image_submit_timeout_s=30))
    session, sock = t2i_session(ctx, frames_in("imagine_t2i_n2")[:3])
    try:
        result = run(ctx, job("t2i", n=2), session, cap_s=3, hold_margin_s=29.7)
    except (TimeoutError, asyncio.CancelledError):
        pytest.fail("the verdict did not come within the submit timeout")
    assert isinstance(result.job, Ambiguous) and len(sock.generations()) == 1

    ctx, clock = Ctx(settings_=settings(video_submit_timeout_s=60)), Clock()
    session = media_session(ctx, Timed("ndjson_t2v", clock, cut=8, hang=True))
    try:
        result = run(ctx, job("t2v"), session, clock, cap_s=3, hold_margin_s=59.7)
    except (TimeoutError, asyncio.CancelledError):
        pytest.fail("the verdict did not come within the submit timeout")
    assert isinstance(result.job, Ambiguous)

    # a hang before the send: nothing sent, a retry
    ctx = Ctx(settings_=settings(image_submit_timeout_s=30))
    session = FakeSession({QUOTA: quota()}, calls=ctx.calls)

    async def hanging_ws(url, **kw):
        await asyncio.Event().wait()
    session.ws_connect = hanging_ws
    try:
        result = run(ctx, job("t2i"), session, cap_s=3, hold_margin_s=29.7)
    except (TimeoutError, asyncio.CancelledError):
        pytest.fail("the verdict did not come within the submit timeout")
    assert result.job == Retry(errors.HTTP_ERROR) and "mark_sent" not in names(ctx.calls)


@mutation(SUBMIT_FILE, "        if isinstance(job, Completed) and mine and self.before - mine[0].left_n > 0:\n",
          "        if False:\n", expect="the uses spent must be the job's facts")
def test_the_quota_read_after_the_job_gives_facts_and_never_changes_the_result():
    ctx = Ctx()
    session, _ = t2i_session(ctx, frames_in("imagine_t2i_n2"))      # 4 -> 2
    result = run(ctx, job("t2i", n=2), session)
    assert isinstance(result.job, Completed)
    assert result.job.facts == Facts(upstream_price=2, cost_source="balance_delta"), \
        "the uses spent must be the job's facts"
    assert Balance("image", 2, None, window=QUOTA_WINDOW) in result.account

    ctx = Ctx()
    session, _ = t2i_session(ctx, frames_in("imagine_t2i_n2"), after=Cut("quota read broke"))
    result = run(ctx, job("t2i", n=2), session)
    assert isinstance(result.job, Completed) and result.job.facts == Facts()
    assert result.account == (Success(),)


@mutation(SUBMIT_FILE, "    if not isinstance(outcome.job, Completed) and run.items:\n", "    if run.items:\n",
          expect="a delivered job keeps no evidence")
def test_a_failed_job_keeps_a_redacted_summary_and_a_delivered_one_none():
    ctx = Ctx()
    session, _ = t2i_session(ctx, frames_in("imagine_t2i_n2"))
    run(ctx, job("t2i", n=2), session)
    assert ctx.evidences == [], "a delivered job keeps no evidence"

    ctx = Ctx()
    session, _ = t2i_session(ctx, frames_in("imagine_t2i_n2")[:4], then=Cut("closed"))
    result = run(ctx, job("t2i", n=2), session)
    assert isinstance(result.job, Ambiguous)
    [(name, data, mime)] = ctx.evidences
    assert name == "grok-answer.json" and mime == "application/json"
    text = data.decode()
    assert "imagine-public.x.ai" in text and "/imagine-public/images/" not in text
    assert "af000000" not in text and "iVBOR" not in text


def test_no_cookie_user_id_email_or_user_path_reaches_an_event_evidence_result_or_log(caplog):
    caplog.set_level(logging.DEBUG)
    lines = [x.replace(SPIKE_USER, USER_ID) for x in fixture("ndjson_t2v")["lines"]]
    texts = []
    for answer in (Resp(200, lines=lines[:-4], cut=len(lines) - 4),      # cut: ambiguous, evidence kept
                   Resp(200, lines=lines)):                            # the decoy rule (no clock): failed, evidence
        ctx = Ctx()
        result = run(ctx, job("t2v"), media_session(ctx, answer))
        assert not isinstance(result.job, Completed)
        texts += [result, ctx.refs, ctx.events, [e[1] for e in ctx.evidences]]
    assert_no_secret(*texts, caplog.text)
    assert "users/" not in str(texts)
