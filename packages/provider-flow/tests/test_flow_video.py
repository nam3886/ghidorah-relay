"""Veo text to video over a fake flow.google.com (no network), fed the recorded video of 23/10 (fixtures/flow/
live-261023/video_*): the credit check, the write-ahead and the one `YhhmEf` send, the acceptance, the status poll and
the mp4 link. Each money or verdict rule has a test that fails when the rule breaks, and a mutation that proves it."""

import asyncio
import copy
import json
from datetime import timedelta

import httpx
import pytest
from flowkit import (
    BAD_ARG,
    CREDITS_50,
    GENERATED,
    PROJECT,
    REFUSED_7,
    REFUSED_8,
    STATUS_DONE,
    STATUS_RUNNING,
    VIDEO_BATCH,
    VIDEO_LISTED,
    VIDEO_MEDIA,
    VIDEO_MEDIA_DONE,
    VIDEO_NOT_LISTED,
    VIDEO_SENT,
    VIDEO_WORKFLOW,
    Ctx,
    Flow,
    args_of,
    frame,
    settings,
)
from test_flow_jobs import NOW, WALL, Clock, Tokens, rested

from relay_core import frames
from relay_core.providers import (
    Accepted,
    Ambiguous,
    Balance,
    CreateRequest,
    Done,
    Found,
    JobView,
    Pending,
    Rest,
    Retry,
    StillUnknown,
    Success,
)
from relay_core.testing.mutation import mutation
from relay_flow import errors
from relay_flow.engine import results as R
from relay_flow.engine.http import Sessions
from relay_flow.engine.submit import submit
from relay_flow.spec import SPEC

SUBMIT_FILE = "packages/provider-flow/src/relay_flow/engine/submit.py"
RESULTS_FILE = "packages/provider-flow/src/relay_flow/engine/results.py"
PROMPT = "a slow pan over a misty pine forest at dawn, cinematic"


def video_job(state=None, ref=None, prompt=PROMPT):
    plan = SPEC.plan(CreateRequest(model="flow-veo-3.1-lite", prompt=prompt), settings(), {})
    text = frames.render(SPEC.frames.defaults["video"], prompt, "7100", SPEC.frames.verbatim)

    async def inputs():
        return []
    return JobView("job-v", "flow", plan.operation, plan.model, plan.params, prompt, "7100", text, None, None, ref,
                   state or {}, inputs, frame_id=1)


def accepted_for(request) -> str:
    """flow's recorded acceptance, with the batch id of THIS request echoed where flow echoed it."""
    batch = args_of(request)[2][0]
    return frame("YhhmEf", json.loads(json.dumps(VIDEO_SENT["payload"]).replace(VIDEO_BATCH, batch)))


CREDITS = frame("nzlxg", CREDITS_50["payload"])


def run(flow, ctx, tokens=None, cfg=None, clock=None):
    clock = clock or Clock()
    tokens = tokens or Tokens(ctx.log)
    return asyncio.run(submit(ctx, video_job(), cfg or settings(), Sessions(), tokens, transport=flow.transport,
                              now=lambda: NOW, clock=clock, wall=lambda: WALL, sleep=clock.sleep, seed=lambda: 7))


# ---- the send ------------------------------------------------------------------------------------------------------
WRITE_AHEAD = ("                await ctx.report_ref(batch, {BATCH_KEY: batch, BATCHES_KEY: list(batches), "
               "PROJECT_KEY: project})\n")
MARK_FIRST = ("                    await ctx.mark_sent()          # then marked sent, immediately before the POST\n"
              "                    sent = True\n")
ACTION = "token = await tokens(VIDEO_ACTION if video else IMAGE_ACTION,"
VIDEO_IF = ("                if video:      # a 7/8 envelope on YhhmEf was never captured: no proof nothing was made, "
            "never re-sent\n")
VIDEO_BRANCH = ("                if video:      # a 7/8 envelope on YhhmEf was never captured: "
                "no proof nothing was made, never re-sent\n"
                "                    return video_answer(reply, batch, batches, project, settings, now())\n")


@mutation(SUBMIT_FILE, WRITE_AHEAD, "", expect="the batch id was not on the job before the send")
@mutation(SUBMIT_FILE, MARK_FIRST, "                    sent = True\n", expect="not marked sent before the POST")
@mutation(SUBMIT_FILE, ACTION, "token = await tokens(IMAGE_ACTION,", expect="VIDEO_GENERATION")
@mutation(SUBMIT_FILE, VIDEO_BRANCH, "", expect="Accepted")
@mutation(SUBMIT_FILE, '    left = (Balance("video", balance),) if balance is not None else ()', "    left = ()",
          expect="the new balance")
def test_a_video_is_written_ahead_marked_sent_right_before_its_one_send_and_accepted_with_its_media_id():
    log = []
    flow, ctx = Flow(log, rpc={"nzlxg": CREDITS, "YhhmEf": accepted_for}), Ctx(log)
    tokens = Tokens(log)
    result = run(flow, ctx, tokens)
    assert tokens.actions == ["VIDEO_GENERATION"], "the token is minted for VIDEO_GENERATION: %s" % tokens.actions
    assert log.index("nzlxg") < log.index("token"), "the credits are read before anything is spent"
    sent = log[log.index("token"):]
    assert sent[:4] == ["token", "report_ref", "mark_sent", "YhhmEf"], \
        "the batch id was not on the job before the send, or not marked sent before the POST: %s" % sent
    assert log.count("YhhmEf") == 1 and log.count("mark_sent") == 1 and "ogiZ0b" not in log
    (batch, state), = ctx.refs
    assert state == {"batch": batch, "batches": [batch], "project": PROJECT}
    args = args_of(flow.calls("YhhmEf")[0])
    recorded = VIDEO_SENT["request"]["args"]
    # the recorded shape, field by field: only our fresh ids and the token differ
    assert args[0][0][:4] == [[None, None, [[["a slow pan over a misty pine forest at dawn, cinematic"]]]],
                              "veo_3_1_t2v_lite", 2, None] == recorded[0][0][:4]
    assert args[0][0][4][:4] == [None] * 4 and len(args[0][0][4]) == 6 and len(args[0]) == 1 and len(args) == 3
    assert args[1][:10] == recorded[1][:10] and args[1][10][1] == 1 and args[1][10][0].startswith("tok-")
    assert args[2] == [batch, 1]
    assert isinstance(result.job, Accepted), "Accepted: %s" % (result.job,)
    assert result.job == Accepted(batch, {"batch": batch, "batches": [batch], "project": PROJECT,
                                          "media": VIDEO_MEDIA, "workflow": VIDEO_WORKFLOW}, taken=True)
    assert Success() in result.account and Balance("video", 40) in result.account, "the new balance: %s" % (
        result.account,)
    assert rested(result) == [Rest(p, NOW + timedelta(seconds=300), "flow pacing between two generates")
                              for p in ("image", "video")]


CREDIT_GATE = '                if balance < int(job.params["credits"]):'
CREDIT_READ = "                if balance is None:\n                    return _result(Retry(errors.HTTP_ERROR))\n"


@mutation(SUBMIT_FILE, CREDIT_GATE, "                if False:", expect="sent without the credits")
@mutation(SUBMIT_FILE, CREDIT_GATE, '                if balance <= int(job.params["credits"]) - 1 - 1:',
          expect="sent without the credits")
@mutation(SUBMIT_FILE, CREDIT_READ, "                balance = 10**6 if balance is None else balance\n",
          expect="sent on an unread balance")
def test_too_few_flow_credits_or_an_unread_balance_is_a_retry_before_anything_is_sent():
    short = copy.deepcopy(CREDITS_50["payload"])
    short[0] = short[5] = 9
    for answer, want, why in (
            (frame("nzlxg", short), Retry("pool.account_out_of_points"), "sent without the credits"),
            (BAD_ARG.replace("Zzl0ze", "nzlxg"), Retry(errors.HTTP_ERROR), "sent on an unread balance"),
            (frame("nzlxg", ["fifty"]), Retry(errors.HTTP_ERROR), "sent on an unread balance"),
            (httpx.Response(500, text=CREDITS), Retry(errors.HTTP_ERROR), "sent on an unread balance")):
        log = []
        flow, ctx = Flow(log, rpc={"nzlxg": answer, "YhhmEf": accepted_for}), Ctx(log)
        result = run(flow, ctx)
        assert not flow.calls("YhhmEf") and "mark_sent" not in log and "token" not in log, why
        assert result.job == want, "%s: %s" % (why, result.job)
    log = []
    exact = copy.deepcopy(CREDITS_50["payload"])
    exact[0] = 10
    flow = Flow(log, rpc={"nzlxg": frame("nzlxg", exact), "YhhmEf": accepted_for})
    assert isinstance(run(flow, Ctx(log)).job, Accepted), "exactly enough credits is enough"
    log = []
    result = run(Flow(log, rpc={"nzlxg": frame("nzlxg", short)}), Ctx(log))
    assert Balance("video", 9) in result.account


@mutation(SUBMIT_FILE, VIDEO_IF, "                if video and not reply.refused_before_anything:\n",
          expect="sent twice")
@mutation(SUBMIT_FILE, VIDEO_BRANCH, "", expect="sent twice")
@pytest.mark.parametrize("code", (7, 8))
def test_a_7_or_8_envelope_to_a_video_send_is_ambiguous_and_never_sent_again(code):
    """Codex review of d92c5f4, owner's ruling 24/10: the 7/8 refusal is proven on images only. On YhhmEf it is no
    proof that nothing was made (Flow could take the video and still answer it), so it is reconciled, never re-sent."""
    log = []
    envelope = (REFUSED_7 if code == 7 else REFUSED_8).replace("ogiZ0b", "YhhmEf")
    flow, ctx = Flow(log, rpc={"nzlxg": CREDITS, "YhhmEf": [envelope, accepted_for]}), Ctx(log)
    result = run(flow, ctx)
    assert len(flow.calls("YhhmEf")) == 1 and log.count("mark_sent") == 1, "sent twice after a %d envelope" % code
    assert isinstance(result.job, Ambiguous), "a %d envelope on a video must be Ambiguous: %s" % (code, result.job)
    (batch, state), = ctx.refs
    assert state["batches"] == [batch] and rested(result)


TAKEN_MATCH = ("    if wf[3][5] != batch or wf[4] != project or media[1] != project or wf[3][4] != media[0] "
               "or media[2] != wf[0]:")
TAKEN_ONE = ("    if not (isinstance(flows, list) and len(flows) == 1 and isinstance(medias, list) and "
             "len(medias) == 1):")


def ambiguous_video_replies():
    def ours(change):
        """The recorded acceptance with this request's batch echoed, then `change`d."""
        def answer(request):
            p = json.loads(json.dumps(VIDEO_SENT["payload"]).replace(VIDEO_BATCH, args_of(request)[2][0]))
            change(p)
            return frame("YhhmEf", p)
        return answer

    def set_at(path, value):
        def change(p):
            node = p
            for i in path[:-1]:
                node = node[i]
            node[path[-1]] = value
        return change
    other = "77777777-7777-4777-8777-777777777777"
    return {
        # the recorded acceptance with the batch flow echoes NOT ours (the fixture's own batch): not proven ours
        "another batch echoed": frame("YhhmEf", VIDEO_SENT["payload"]),
        "the workflow of another project": ours(set_at([2, 0, 4], other)),
        "the media of another project": ours(set_at([3, 0, 1], other)),
        "the workflow names another media": ours(set_at([2, 0, 3, 4], other)),
        "the media names another workflow": ours(set_at([3, 0, 2], other)),
        "no media record": ours(set_at([3], [])),
        "two media records": ours(lambda p: p[3].append(copy.deepcopy(p[3][0]))),
        "two workflows": ours(lambda p: p[2].append(copy.deepcopy(p[2][0]))),
        "HTTP 500 with the acceptance": lambda r: httpx.Response(500, text=accepted_for(r)),
        "a payload too short": frame("YhhmEf", [None, 40]),
        "an image's reply": frame("YhhmEf", GENERATED["payload"]),
        "error envelope 3": BAD_ARG.replace("Zzl0ze", "YhhmEf"),
        "code 7 with a payload": ")]}'\n\n1\n" + json.dumps(
            [["wrb.fr", "YhhmEf", json.dumps([[]]), None, None, [7, None, []], "generic"]]) + "\n",
        "the acceptance then a cut frame": lambda r: accepted_for(r) + '[["wrb.fr","YhhmEf","[null',
        "HTTP 500": httpx.Response(500, text=REFUSED_7.replace("ogiZ0b", "YhhmEf")),
        "an empty 200": "",
        "a timeout": httpx.ReadTimeout("slow"),
        "a dropped connection": httpx.RemoteProtocolError("peer closed"),
    }


@mutation(SUBMIT_FILE, TAKEN_MATCH, "    if wf[4] != project or media[1] != project:", expect="Ambiguous")
@mutation(SUBMIT_FILE, TAKEN_MATCH, "    if wf[3][5] != batch:", expect="Ambiguous")
@mutation(SUBMIT_FILE, TAKEN_MATCH, "    if wf[3][5] != batch or wf[4] != project or media[1] != project:",
          expect="Ambiguous")
@mutation(SUBMIT_FILE, TAKEN_ONE, "    if not (isinstance(flows, list) and flows and isinstance(medias, list) and "
          "medias):", expect="Ambiguous")
@mutation(SUBMIT_FILE, "if reply.status == 200 and reply.clean else None",
          "if reply.status == 200 else None", expect="Ambiguous")
@mutation(SUBMIT_FILE, "if reply.status == 200 and reply.clean else None",
          "if reply.clean else None", expect="Ambiguous")
@pytest.mark.parametrize("name", sorted(ambiguous_video_replies()))
def test_any_video_reply_that_is_not_the_recorded_acceptance_of_our_batch_is_ambiguous_and_never_sent_again(name):
    log = []
    flow, ctx = Flow(log, rpc={"nzlxg": CREDITS, "YhhmEf": [ambiguous_video_replies()[name], accepted_for]}), Ctx(log)
    result = run(flow, ctx)
    assert len(flow.calls("YhhmEf")) == 1, "sent twice after %s" % name
    assert isinstance(result.job, Ambiguous), "Ambiguous after %s: %s" % (name, result.job)
    assert rested(result) and not any(isinstance(e, Success) for e in result.account)


# ---- reconcile and poll --------------------------------------------------------------------------------------------
def reconcile(flow, ctx, state):
    return asyncio.run(R.reconcile(ctx, video_job(state), settings(), Sessions(), transport=flow.transport))


def poll(flow, ctx, state):
    return asyncio.run(R.poll(ctx, video_job(state), settings(), Sessions(), transport=flow.transport,
                              rand=lambda: 0))


ACCEPTED_STATE = {"project": PROJECT, "batch": VIDEO_BATCH, "batches": [VIDEO_BATCH], "media": VIDEO_MEDIA,
                  "workflow": VIDEO_WORKFLOW}


def test_a_lost_video_reply_is_found_by_its_batch_in_the_listing_and_absence_stays_unknown():
    log = []
    state = {"project": PROJECT, "batch": VIDEO_BATCH, "batches": ["refused-0", VIDEO_BATCH]}
    got = reconcile(Flow(log, rpc={"Zzl0ze": frame("Zzl0ze", VIDEO_LISTED["payload"])}), Ctx(log), state)
    assert got == Found(VIDEO_BATCH, {"batch": VIDEO_BATCH, "media": VIDEO_MEDIA, "workflow": VIDEO_WORKFLOW}, True)
    got = reconcile(Flow(log, rpc={"Zzl0ze": frame("Zzl0ze", VIDEO_NOT_LISTED["payload"])}), Ctx(log), state)
    assert got == StillUnknown(answered=True)


def status(code: int | list) -> str:
    p = copy.deepcopy(STATUS_DONE["payload"])
    for rec in p[2]:
        if rec[0] == VIDEO_MEDIA:
            rec[5][8] = code if isinstance(code, list) else [code]
    return frame("jwpduf", p)


DONE_GATE = "                if status != DONE:"
PREFIX = "media_link(reply.payload, VIDEO_PREFIX) if video else media_link("


@mutation(RESULTS_FILE, PREFIX, "media_link(reply.payload) if video else media_link(", expect="the poster")
@mutation(RESULTS_FILE, "                if status in (QUEUED, RENDERING):", "                if False:",
          expect="Pending")
@mutation(RESULTS_FILE, DONE_GATE, "                if False:", expect="never captured")
def test_poll_reads_the_status_waits_at_6_and_2_and_delivers_the_mp4_at_3():
    media = frame("as29s", VIDEO_MEDIA_DONE["payload"])
    for answer, why in ((status(6), "queued"), (frame("jwpduf", STATUS_RUNNING["payload"]), "rendering")):
        log = []
        flow = Flow(log, rpc={"jwpduf": answer, "as29s": media})
        got = poll(flow, Ctx(log), ACCEPTED_STATE)
        assert got.outcome == Pending(why, 30, {"media": VIDEO_MEDIA}), "Pending while %s: %s" % (why, got.outcome)
        assert "as29s" not in log
    log = []
    flow = Flow(log, rpc={"jwpduf": frame("jwpduf", STATUS_DONE["payload"]), "as29s": media})
    got = poll(flow, Ctx(log), ACCEPTED_STATE)
    assert args_of(flow.calls("jwpduf")[0]) == [None, None, [[VIDEO_WORKFLOW], [VIDEO_MEDIA], [VIDEO_BATCH]]], \
        "the status read is sent as recorded"
    assert isinstance(got.outcome, Done) and got.effects == (Success(),)
    (out,) = got.outcome.outputs
    assert out.ref.startswith("https://flow-content.google/video/" + VIDEO_MEDIA), "the poster was delivered: %s" \
        % out.ref
    assert (out.mime, out.upstream_id, dict(out.meta)) == ("video/mp4", VIDEO_MEDIA, {"batch": VIDEO_BATCH})
    assert args_of(flow.calls("as29s")[0]) == [VIDEO_MEDIA]
    for odd in (4, 5, 0, [4, [None, "Media not found."], ["Media not found."]]):
        log = []
        ctx = Ctx(log)
        flow = Flow(log, rpc={"jwpduf": status(odd), "as29s": media})
        got = poll(flow, ctx, ACCEPTED_STATE)
        assert isinstance(got.outcome, Pending) and got.outcome.answered, \
            "a status never captured (%s) was delivered: %s" % (odd, got.outcome)
        assert "as29s" not in log and len(ctx.events) == 1


OURS = "        if not (isinstance(rec, list) and len(rec) > 5 and rec[0] == media and rec[1] == project):"


@mutation(RESULTS_FILE, OURS, "        if not (isinstance(rec, list) and len(rec) > 5 and rec[0] == media):",
          expect="another project's record")
@mutation(RESULTS_FILE, OURS, "        if not (isinstance(rec, list) and len(rec) > 5 and rec[1] == project):",
          expect="another media's record")
def test_only_our_media_record_of_our_project_gives_the_status():
    media = frame("as29s", VIDEO_MEDIA_DONE["payload"])
    other = json.loads(json.dumps(STATUS_DONE["payload"]).replace(
        '["%s", "%s"' % (VIDEO_MEDIA, PROJECT), '["%s", "77777777-7777-4777-8777-777777777777"' % VIDEO_MEDIA))
    stranger = json.loads(json.dumps(STATUS_DONE["payload"]).replace(VIDEO_MEDIA, "88888888-8888-4888-8888-8888"))
    for payload, why in ((other, "another project's record"), (stranger, "another media's record")):
        log = []
        got = poll(Flow(log, rpc={"jwpduf": frame("jwpduf", payload), "as29s": media}), Ctx(log), ACCEPTED_STATE)
        assert isinstance(got.outcome, Pending), "%s was read as ours: %s" % (why, got.outcome)


def test_an_odd_status_is_named_once_and_an_unreadable_status_read_waits_unanswered():
    media = frame("as29s", VIDEO_MEDIA_DONE["payload"])
    log = []
    ctx = Ctx(log)
    got = poll(Flow(log, rpc={"jwpduf": status(4), "as29s": media}), ctx, {**ACCEPTED_STATE, "odd_status": 4})
    assert isinstance(got.outcome, Pending) and ctx.events == []
    for answer in (BAD_ARG.replace("Zzl0ze", "jwpduf"), httpx.Response(500, text=""), httpx.ReadTimeout("slow")):
        log = []
        got = poll(Flow(log, rpc={"jwpduf": answer}), Ctx(log), ACCEPTED_STATE)
        assert isinstance(got.outcome, Pending) and not got.outcome.answered, answer
    # done, but no mp4 link yet: waits
    log = []
    no_link = json.loads(json.dumps(VIDEO_MEDIA_DONE["payload"]).replace("https://flow-content.google/video/", "x:"))
    got = poll(Flow(log, rpc={"jwpduf": status(3), "as29s": frame("as29s", no_link)}), Ctx(log), ACCEPTED_STATE)
    assert isinstance(got.outcome, Pending)


def test_a_video_found_by_the_reconciler_is_polled_by_its_media_id():
    log = []
    flow = Flow(log, rpc={"jwpduf": status(3), "as29s": frame("as29s", VIDEO_MEDIA_DONE["payload"])})
    found = {"project": PROJECT, "batch": VIDEO_BATCH, "media": VIDEO_MEDIA, "workflow": VIDEO_WORKFLOW}
    got = poll(flow, Ctx(log), found)
    assert isinstance(got.outcome, Done)
    assert args_of(flow.calls("jwpduf")[0]) == [None, None, [[VIDEO_WORKFLOW], [VIDEO_MEDIA], [VIDEO_BATCH]]]
    log = []
    flow = Flow(log, rpc={"Zzl0ze": frame("Zzl0ze", VIDEO_LISTED["payload"]), "jwpduf": status(2)})
    got = poll(flow, Ctx(log), {"project": PROJECT, "batches": [VIDEO_BATCH]})
    assert got.outcome == Pending("rendering", 30, {"media": VIDEO_MEDIA})
    assert args_of(flow.calls("jwpduf")[0]) == [None, None, [[VIDEO_WORKFLOW], [VIDEO_MEDIA], [VIDEO_BATCH]]], \
        "the listed workflow and batch go in the status read as recorded"
