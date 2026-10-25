"""Submit, reconcile, poll and fetch over a fake flow.google.com (no network), fed flow's recorded answers from
fixtures/flow/live-261023. Each money or safety rule has a test that fails when the rule breaks, and a mutation that
proves it."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from flowkit import (
    AT_TOKEN,
    BAD_ARG,
    GENERATED,
    GET_MEDIA,
    IMG2_BATCH,
    IMG2_MEDIA,
    LISTING,
    NEW_PSIDTS,
    PROJECT,
    PSID,
    PSIDTS,
    REFUSED_7,
    REFUSED_8,
    Ctx,
    Flow,
    args_of,
    frame,
    settings,
)

from relay_core import frames
from relay_core.net import UnsafeUrl
from relay_core.providers import (
    Ambiguous,
    Completed,
    Cooldown,
    CreateRequest,
    Done,
    Found,
    JobView,
    NotTaken,
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
from relay_flow import errors
from relay_flow.engine import results as R
from relay_flow.engine.captcha import CaptchaError
from relay_flow.engine.http import Sessions
from relay_flow.engine.submit import REASONS, submit
from relay_flow.spec import SPEC

SUBMIT_FILE = "packages/provider-flow/src/relay_flow/engine/submit.py"
HTTP_FILE = "packages/provider-flow/src/relay_flow/engine/http.py"
RESULTS_FILE = "packages/provider-flow/src/relay_flow/engine/results.py"
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
WALL = 1_792_737_600.0          # the wall clock of the runs; the imported jar was never rotated, so the first run does
GOOD = frame("ogiZ0b", GENERATED["payload"])
SIGNED_LINK = GENERATED["payload"][0][0][6][0][13]
TOKENS = ("tok-%d-" % i + "x" * 40 for i in range(1000))
ALL_POOLS = ("image", "video", "video_pro", "video_ultra")      # every pool rests after a generate


def job(state=None, ref=None, ratio="16:9", model="flow-nano-banana-2.1", prompt="a red paper boat", sent=None,
        frame_id=1):
    """`sent_text` rendered with the shipped default frame unless `sent` gives it; `frame_id` None is a job queued
    before flow had frames."""
    plan = SPEC.plan(CreateRequest(model=model, prompt=prompt, ratio=ratio), settings(), {})
    text = frames.render(SPEC.frames.defaults["image"], prompt, "7100", SPEC.frames.verbatim) if sent is None else sent

    async def inputs():
        return []
    return JobView("job-1", "flow", plan.operation, plan.model, plan.params, prompt, "7100", text, None, None, ref,
                   state or {}, inputs, frame_id=frame_id)


class Tokens:
    """The token source: hands out new tokens, or raises the scripted `CaptchaError`s in order."""

    def __init__(self, log, *fails):
        self.log, self.fails, self.given = log, list(fails), []

    async def __call__(self, action, url, proxy=None):
        self.log.append("token")
        self.proxies = [*getattr(self, "proxies", []), proxy]
        self.actions = [*getattr(self, "actions", []), action]
        assert url == "https://flow.google.com/project/" + PROJECT
        if self.fails:
            fail = self.fails.pop(0)
            if fail is not None:
                raise fail
        token = next(TOKENS)
        self.given.append(token)
        return token


class Clock:
    def __init__(self, step=0.0):
        self.t, self.step, self.slept = 0.0, step, []

    def __call__(self):
        self.t += self.step
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s


def run_submit(flow, ctx, tokens=None, cfg=None, clock=None, the_job=None):
    clock = clock or Clock()
    tokens = tokens or Tokens(ctx.log)
    return asyncio.run(submit(ctx, the_job or job(), cfg or settings(), Sessions(), tokens, transport=flow.transport,
                              now=lambda: NOW, clock=clock, wall=lambda: WALL, sleep=clock.sleep, seed=lambda: 7))


def rested(result) -> list:
    return [e for e in result.account if isinstance(e, Rest)]


# ---- submit: the write-ahead and the one POST ---------------------------------------------------------------------
WRITE_AHEAD = ("                await ctx.report_ref(batch, {BATCH_KEY: batch, BATCHES_KEY: list(batches), "
               "PROJECT_KEY: project})\n")
MARK_FIRST = ("                    await ctx.mark_sent()          # then marked sent, immediately before the POST\n"
              "                    sent = True\n")


@mutation(SUBMIT_FILE, WRITE_AHEAD, "", expect="the batch id was not on the job before the generate")
@mutation(SUBMIT_FILE, MARK_FIRST, "                    sent = True\n", expect="not marked sent before the POST")
@mutation(SUBMIT_FILE, "for m in refs] or None, seed, params[\"aspect\"], params[\"model\"],",
          "for m in refs] or None, seed, 1, params[\"model\"],", expect="the ratio")
def test_the_batch_is_written_ahead_and_marked_sent_right_before_the_one_generate_post():
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": GOOD}), Ctx(log)
    result = run_submit(flow, ctx)
    sent = log[log.index("token"):]
    assert "mark_sent" in sent and sent.index("mark_sent") == sent.index("ogiZ0b") - 1, \
        "not marked sent before the POST: %s" % log
    assert sent[:3] == ["token", "report_ref", "mark_sent"], "the batch id was not on the job before the generate: %s" \
        % sent
    assert log.count("ogiZ0b") == 1 and log.count("mark_sent") == 1
    (batch, state), = ctx.refs
    assert state == {"batch": batch, "batches": [batch], "project": PROJECT}
    args = args_of(flow.calls("ogiZ0b")[0])
    entry = args[1][0]
    assert args[4] == [batch], "the batch id sent is the one written ahead"
    assert (entry[3], entry[4], entry[5], entry[8]) == (7, 3, "BELUGA", [[["a red paper boat"]]]), "the ratio"
    assert args[3][5] == PROJECT and args[3][10][1] == 1 and args[3][10][0].startswith("tok-")
    assert isinstance(result.job, Completed)
    assert result.job.outputs == (Output(SIGNED_LINK, "image/jpeg", 0, IMG2_MEDIA, {"batch": batch}),)
    assert SIGNED_LINK.startswith("https://flow-content.google/image/" + IMG2_MEDIA)
    assert Success() in result.account and rested(result) == [
        Rest(pool, NOW + timedelta(seconds=300), "flow pacing between two generates") for pool in ALL_POOLS]


@mutation(SUBMIT_FILE, "    return job.prompt if job.frame_id is None else job.sent_text", "    return job.prompt",
          expect="the framed text was not sent")
@mutation(SUBMIT_FILE, "    return job.prompt if job.frame_id is None else job.sent_text", "    return job.sent_text",
          expect="a job queued before frames")
def test_the_generate_carries_the_framed_text_and_a_job_queued_before_frames_its_bare_words():
    cases = ((job(sent="Style: a red paper boat\non a pond"), "Style: a red paper boat\non a pond",
              "the framed text was not sent"),
             (job(sent="a red paper boat [7100]", frame_id=None), "a red paper boat",
              "a job queued before frames was sent its core-frame text"))
    for the_job, want, why in cases:
        log = []
        flow, ctx = Flow(log, rpc={"ogiZ0b": GOOD}), Ctx(log)
        run_submit(flow, ctx, the_job=the_job)
        assert args_of(flow.calls("ogiZ0b")[0])[1][0][8] == [[[want]]], why


# ---- submit: the immediate reCAPTCHA refusal ----------------------------------------------------------------------
REFUSAL_GATE = "        if self.status != 200 or not self.clean or len(self.items) != 1:"
REFUSAL_ITEM = "        return item.refusal_shape and item.code in REFUSED_CODES"
SHAPE_LEN = "    if len(item) != 7 or item[6] != \"generic\":"
SHAPE_EMPTY = "    if item[2] is not None or item[3] is not None or item[4] is not None:"
SHAPE_ENVELOPE = ("    if not isinstance(err, list) or len(err) not in (2, 3) or type(err[0]) is not int "
                  "or err[1] is not None:")
SHAPE_DETAILS = "and d[0].startswith(ERROR_TYPE_PREFIX) for d in details)"


@mutation(SUBMIT_FILE, "                if not reply.refused_before_anything:", "                if True:",
          expect="refused tries")
def test_a_7_refusal_made_nothing_and_is_tried_again_with_a_new_token_and_a_new_batch():
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [REFUSED_7, REFUSED_7, GOOD]}), Ctx(log)
    tokens, clock = Tokens(log), Clock()
    result = run_submit(flow, ctx, tokens, clock=clock)
    assert isinstance(result.job, Completed), "refused tries were not tried again: %s" % (result.job,)
    posts = flow.calls("ogiZ0b")
    assert len(posts) == 3 and log.count("mark_sent") == 3
    batches = [args_of(p)[4][0] for p in posts]
    tokens_sent = [args_of(p)[3][10][0] for p in posts]
    assert len(set(batches)) == 3 and tokens_sent == tokens.given and len(set(tokens_sent)) == 3
    assert [state["batches"] for _, state in ctx.refs] == [batches[:1], batches[:2], batches]
    for i in range(3):        # each try: its batch written, then marked, then its one POST
        at = [j for j, step in enumerate(log) if step == "ogiZ0b"][i]
        assert log[at - 2:at] == ["report_ref", "mark_sent"], log
    assert clock.slept == [60, 60], "the tries are spaced by flow.captcha_retry_gap_s"
    assert len(ctx.events) == 2 and "code 7" in ctx.events[0] and "code 7" in ctx.events[1]
    assert result.job.outputs[0].meta == {"batch": batches[-1]}


TRAFFIC_STOP = "                if reply.code == TOO_MUCH_TRAFFIC:     # no more tries here"
TRAFFIC_REST = '    if code == TOO_MUCH_TRAFFIC and int(settings.get("flow.traffic_rest_s")) > gap:'


@mutation(SUBMIT_FILE, TRAFFIC_STOP, "                if False:     # no more tries here", expect="hammered")
@mutation(SUBMIT_FILE, TRAFFIC_REST, "    if False:", expect="the traffic rest")
def test_a_code_8_too_much_traffic_ends_the_tries_requeues_the_job_and_rests_the_account():
    """Code 8 (RESOURCE_EXHAUSTED, ..._TOO_MUCH_TRAFFIC) may be a traffic cap: nothing was made, so the job goes back
    to the queue for another account, and this one rests `flow.traffic_rest_s` instead of being tried again."""
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [REFUSED_7, REFUSED_8, GOOD]}), Ctx(log)
    result = run_submit(flow, ctx)
    assert len(flow.calls("ogiZ0b")) == 2, "hammered after a code 8"
    batches = [args_of(p)[4][0] for p in flow.calls("ogiZ0b")]
    assert result.job == NotTaken(errors.TOO_MUCH_TRAFFIC, {"refused_batches": batches})
    assert rested(result) == [Rest(p, NOW + timedelta(seconds=1800), "flow said too much traffic (code 8)")
                              for p in ALL_POOLS], "the traffic rest: %s" % rested(result)
    assert "code 8" in ctx.events[-1] and not any(isinstance(e, Success) for e in result.account)
    # a pacing longer than the traffic rest is kept; a rest of 0 leaves the pacing alone
    for cfg, secs in ((settings(flow__account_min_gap_s=3600), 3600), (settings(flow__traffic_rest_s=0), 300)):
        log = []
        result = run_submit(Flow(log, rpc={"ogiZ0b": REFUSED_8}), Ctx(log), cfg=cfg)
        assert {e.until for e in rested(result)} == {NOW + timedelta(seconds=secs)}, "the traffic rest"


RECORDED = ["wrb.fr", "ogiZ0b", None, None, None,
            [7, None, [["type.googleapis.com/google.rpc.ErrorInfo", ["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]], "generic"]


def perturbed():
    """The recorded refusal item with one slot changed: each is not the refusal."""
    def with_slot(i, value):
        shape = json.loads(json.dumps(RECORDED))
        shape[i] = value
        return shape
    envelope = RECORDED[5]
    return [
        ("a value in slot 3", with_slot(3, "unexpected")),
        ("a value in slot 4", with_slot(4, 0)),
        ("a list in slot 2", with_slot(2, [])),
        ("another last slot", with_slot(6, "other")),
        ("an eighth slot", [*RECORDED, None]),
        ("a value in the envelope's slot 1", with_slot(5, [7, "x", envelope[2]])),
        ("a longer envelope", with_slot(5, [7, None, envelope[2], "more"])),
        ("details that are not a list", with_slot(5, [7, None, "text"])),
        ("a detail that is not a type url", with_slot(5, [7, None, [["other.example/Info", ["x"]]]])),
        ("a detail that is not a list", with_slot(5, [7, None, ["type.googleapis.com/x"]])),
    ]


def test_the_recorded_refusal_shape_and_only_it_is_the_refusal():
    from relay_flow.engine.http import read_reply, refusal_shape
    assert refusal_shape(RECORDED) and refusal_shape([*RECORDED[:5], [8, None], "generic"])
    assert read_reply(200, REFUSED_7, "ogiZ0b").refused_before_anything
    assert read_reply(200, REFUSED_8, "ogiZ0b").refused_before_anything
    for what, shape in perturbed():
        assert not refusal_shape(shape), what


def ambiguous_replies():
    item = json.dumps([["wrb.fr", "ogiZ0b", json.dumps([[]]), None, None, [7, None, []], "generic"]])
    with_payload = ")]}'\n\n%d\n%s\n" % (len(item), item)
    refusal = ('["wrb.fr","ogiZ0b",null,null,null,[7,null,[["type.googleapis.com/google.rpc.ErrorInfo",'
               '["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]],"generic"]')

    drawn = json.dumps(["wrb.fr", "ogiZ0b", json.dumps(GENERATED["payload"]), None, None, None, "generic"])

    def framed(*items: str) -> str:
        body = "[%s]" % ",".join(items)
        return ")]}'\n\n%d\n%s\n" % (len(body), body)
    return {
        "a refusal then the drawn image in a later frame": REFUSED_7 + GOOD,
        "the drawn image then a refusal": GOOD + REFUSED_7,
        "two refusal items in one frame": framed(refusal, refusal),
        "a refusal next to a short item of ours": framed(refusal, '["wrb.fr","ogiZ0b"]'),
        "a refusal next to a short item of ours in a later frame": REFUSED_7 + framed('["wrb.fr","ogiZ0b"]'),
        "a refusal next to an item with our rpc id elsewhere": framed(refusal, '["af.httprm","ogiZ0b",1]'),
        "a refusal next to an item of another kind": framed(refusal, '["wrb.fx","ogiZ0b",null,null,null,[],"generic"]'),
        "a refusal next to our rpc id alone": framed(refusal, '"ogiZ0b"'),
        "a refusal with our item nested deeper": framed(refusal, '[["wrb.fr","ogiZ0b","[[]]"]]'),
        "the drawn image, then a last frame with a refusal": framed(drawn) + framed(refusal),
        "a refusal then a cut frame": REFUSED_7 + '[["wrb.fr","ogiZ0b","[[[\\"1d61f6cc',
        "a refusal then a line that is not a frame": REFUSED_7 + "<html>error</html>\n",
        "a refusal whose result slot is not JSON":
            framed(refusal.replace(",null,null,null,", ',"garbage",null,null,', 1)),
        "a refusal of another shape": framed(refusal.replace(',"generic"]', "]")),
        **{"a refusal with %s" % what: framed(json.dumps(shape)) for what, shape in perturbed()},
        "the refusal shape with code 3": framed(json.dumps([*RECORDED[:5], [3, None, RECORDED[5][2]], "generic"])),
        "error envelope 3": BAD_ARG.replace("Zzl0ze", "ogiZ0b"),
        "code 7 with a payload": with_payload,
        "HTTP 500": httpx.Response(500, text=REFUSED_7),
        "HTTP 429": httpx.Response(429, text=REFUSED_8),
        "an empty 200": "",
        "a payload without a link": frame("ogiZ0b", [[["media-1", None, "wf"]], []]),
        "the item of another rpc": REFUSED_7.replace("ogiZ0b", "Zzl0ze"),
        "a timeout": httpx.ReadTimeout("slow"),
        "a dropped connection": httpx.RemoteProtocolError("peer closed"),
    }


@mutation(HTTP_FILE, REFUSAL_GATE, "        if self.status != 200 or len(self.items) != 1:", expect="sent twice")
@mutation(HTTP_FILE, REFUSAL_GATE, "        if self.status != 200 or not self.clean or not self.items:",
          expect="sent twice")
@mutation(HTTP_FILE, REFUSAL_GATE, "        if not self.clean or len(self.items) != 1:", expect="sent twice")
@mutation(HTTP_FILE, REFUSAL_ITEM, "        return item.code in REFUSED_CODES", expect="sent twice")
@mutation(HTTP_FILE, REFUSAL_ITEM, "        return item.refusal_shape and item.code is not None", expect="sent twice")
@mutation(HTTP_FILE, SHAPE_LEN, "    if len(item) != 7:", expect="sent twice")
@mutation(HTTP_FILE, SHAPE_LEN, "    if len(item) < 7 or item[6] != \"generic\":", expect="sent twice")
@mutation(HTTP_FILE, SHAPE_EMPTY, "    if item[3] is not None or item[4] is not None:", expect="sent twice")
@mutation(HTTP_FILE, SHAPE_EMPTY, "    if item[2] is not None or item[4] is not None:", expect="sent twice")
@mutation(HTTP_FILE, SHAPE_EMPTY, "    if item[2] is not None or item[3] is not None:", expect="sent twice")
@mutation(HTTP_FILE, SHAPE_ENVELOPE,
          "    if not isinstance(err, list) or type(err[0]) is not int or err[1] is not None:",
          expect="sent twice")
@mutation(HTTP_FILE, SHAPE_ENVELOPE,
          "    if not isinstance(err, list) or len(err) not in (2, 3) or type(err[0]) is not int:",
          expect="sent twice")
@mutation(HTTP_FILE, SHAPE_DETAILS, "and True for d in details)", expect="sent twice")
@mutation(HTTP_FILE, "            clean = False          #", "            pass          #", expect="sent twice")
@mutation(HTTP_FILE, "            elif rpcid in json.dumps(i):", "            elif False:", expect="sent twice")
@mutation(HTTP_FILE, "                clean = False      # our rpc id in any other shape",
          "                pass      # our rpc id in any other shape", expect="sent twice")
@mutation(HTTP_FILE, "                items.append(_item(i))", "                items = [_item(i)]",
          expect="a refusal then the drawn image in a later frame: Completed")
@mutation(HTTP_FILE, "REFUSED_CODES = (7, 8)", "REFUSED_CODES = (3, 7, 8)", expect="sent twice")
@mutation(SUBMIT_FILE, "                    return answer(reply, batch, settings, now())",
          "                    continue", expect="sent twice")
@pytest.mark.parametrize("name", sorted(ambiguous_replies()))
def test_any_other_reply_that_is_not_a_drawn_image_is_ambiguous_and_never_sent_again(name):
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [ambiguous_replies()[name], GOOD]}), Ctx(log)
    result = run_submit(flow, ctx)
    assert len(flow.calls("ogiZ0b")) == 1, "sent twice after %s" % name
    assert isinstance(result.job, Ambiguous), "not Ambiguous (sent twice or re-queued) after %s: %s" % (
        name, result.job)
    assert rested(result), "the account rests after any generate"


@mutation(SUBMIT_FILE, "            for attempt in range(tries):", "            for attempt in range(tries + 1):",
          expect="more tries than flow.captcha_max_tries")
@mutation(SUBMIT_FILE, "            return _result(NotTaken(refused, {\"refused_batches\": batches}), *read,\n",
          "            return _result(Ambiguous(\"refused\"), *read,\n", expect="NotTaken")
def test_when_every_try_is_refused_the_job_goes_back_to_the_queue_not_taken_and_the_account_rests():
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [REFUSED_7] * 4 + [GOOD]}), Ctx(log)
    result = run_submit(flow, ctx, cfg=settings(flow__captcha_max_tries=4))
    assert len(flow.calls("ogiZ0b")) == 4, "more tries than flow.captcha_max_tries"
    assert type(result.job) is NotTaken and result.job.code == errors.CAPTCHA_REFUSED, "NotTaken: %s" % (result.job,)
    assert result.job.state_patch == {"refused_batches": [args_of(p)[4][0] for p in flow.calls("ogiZ0b")]}
    assert rested(result) and not any(isinstance(e, Success) for e in result.account)


DENIED = REFUSED_7.replace("PUBLIC_ERROR_UNUSUAL_ACTIVITY", "PUBLIC_ERROR_MODEL_ACCESS_DENIED")


@mutation(SUBMIT_FILE, "                refused = REASONS.get(reply.reason, errors.CAPTCHA_REFUSED)", "",
          expect="model access")
@mutation(HTTP_FILE, "        reason = err[2][0][1][0]", "        reason = None", expect="the reason")
def test_the_refusals_reason_names_the_code_when_the_tries_run_out_and_code_7_is_still_tried_again():
    """flow2api (16/10): code 7 comes as PUBLIC_ERROR_UNUSUAL_ACTIVITY (the reCAPTCHA refusal) or as
    PUBLIC_ERROR_MODEL_ACCESS_DENIED. Both are still retried with a new token; the last one names the code."""
    from relay_flow.engine.http import read_reply
    assert read_reply(200, REFUSED_7, "ogiZ0b").reason == "PUBLIC_ERROR_UNUSUAL_ACTIVITY", "the reason"
    assert read_reply(200, DENIED, "ogiZ0b").reason == "PUBLIC_ERROR_MODEL_ACCESS_DENIED", "the reason"
    assert read_reply(200, REFUSED_8, "ogiZ0b").reason == "PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC"
    assert read_reply(200, GOOD, "ogiZ0b").reason is None
    for answers, code in (([REFUSED_7, DENIED], errors.MODEL_ACCESS_DENIED), ([DENIED, REFUSED_7],
                                                                               errors.CAPTCHA_REFUSED)):
        log = []
        flow, ctx = Flow(log, rpc={"ogiZ0b": answers}), Ctx(log)
        result = run_submit(flow, ctx, cfg=settings(flow__captcha_max_tries=2))
        assert len(flow.calls("ogiZ0b")) == 2, "code 7 is tried again whatever its reason"
        assert result.job.code == code, "model access: %s" % (result.job,)
        assert "PUBLIC_ERROR_MODEL_ACCESS_DENIED" in " ".join(ctx.events), "the reason is in the job's events"
    assert set(REASONS.values()) <= set(SPEC.codes())


@mutation(SUBMIT_FILE, "API_TIMEOUT_S > SUBMIT_TIMEOUT_S:", "API_TIMEOUT_S > 10**9:",
          expect="a try past the submit bound")
def test_no_try_starts_that_would_not_end_inside_the_submit_bound():
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [REFUSED_7] * 5}), Ctx(log)
    result = run_submit(flow, ctx, clock=Clock(step=200.0))      # each read of the clock moves 200 s on
    assert len(flow.calls("ogiZ0b")) < 5, "a try past the submit bound"
    assert type(result.job) is NotTaken


@mutation(SUBMIT_FILE, "IMAGE_ACTION, BASE_URL + path, ctx.account.proxy)", "IMAGE_ACTION, BASE_URL + path)",
          expect="the token source was not given the proxy")
def test_the_token_source_is_given_the_accounts_proxy():
    log = []
    proxy = Proxy("10.0.0.1", 8080, "user:pw")
    tokens = Tokens(log)
    run_submit(Flow(log, rpc={"ogiZ0b": GOOD}), Ctx(log, proxy=proxy), tokens)
    assert tokens.proxies == [proxy], "the token source was not given the proxy: %s" % tokens.proxies
    assert tokens.actions == ["IMAGE_GENERATION"]


@mutation(SUBMIT_FILE, "                    if not sent:\n                        await ctx.event(",
          "                    if True:\n                        await ctx.event(", expect="Retry after a send")
def test_no_token_is_a_retry_before_the_send_and_not_taken_after_a_refusal():
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": GOOD}), Ctx(log)
    result = run_submit(flow, ctx, Tokens(log, CaptchaError("FLOW_YESCAPTCHA_KEY is not set on the flow worker")))
    assert result.job == Retry(errors.CAPTCHA_FAILED) and "mark_sent" not in log and not flow.calls("ogiZ0b")
    assert ctx.events == ["no reCAPTCHA token: FLOW_YESCAPTCHA_KEY is not set on the flow worker"]
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [REFUSED_7, GOOD]}), Ctx(log)
    result = run_submit(flow, ctx, Tokens(log, None, CaptchaError("no token in 120 s")))
    assert type(result.job) is NotTaken, "Retry after a send is unknown to the core: %s" % (result.job,)
    assert len(flow.calls("ogiZ0b")) == 1


RETRY_MARK = ("                    except Exception:              # every earlier try was refused before anything was "
              "made\n                        return _result(NotTaken(")


@mutation(SUBMIT_FILE, RETRY_MARK, "                    except ZeroDivisionError:\n"
          "                        return _result(NotTaken(", expect="the worker is stopping")
def test_a_worker_stopping_between_two_tries_sends_nothing_more_and_is_not_taken():
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [REFUSED_7, GOOD]}), Ctx(log)

    class Stopping(RuntimeError):
        pass

    real = ctx.mark_sent

    async def mark_once():
        await real()
        ctx.mark_error = Stopping("the worker is stopping")
    ctx.mark_sent = mark_once
    result = run_submit(flow, ctx)
    assert len(flow.calls("ogiZ0b")) == 1 and type(result.job) is NotTaken


def test_the_first_mark_sent_failure_reaches_the_core_unchanged_and_nothing_is_sent():
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": GOOD}), Ctx(log)
    ctx.mark_error = LookupError("lease lost")
    with pytest.raises(LookupError):
        run_submit(flow, ctx)
    assert not flow.calls("ogiZ0b")


# ---- submit: before the send ---------------------------------------------------------------------------------------
def test_before_the_send_a_signed_out_page_is_a_spaced_strike_and_a_dead_proxy_a_retry():
    log = []
    result = run_submit(Flow(log, signed_in=False), Ctx(log))
    assert result.job == Retry("pool.account_logged_out") and "mark_sent" not in log
    assert [type(e) for e in result.account] == [Suspect, Cooldown]
    log = []
    result = run_submit(Flow(log, page_status=500), Ctx(log))
    assert result.job == Retry(errors.HTTP_ERROR) and "mark_sent" not in log
    log = []
    flow = Flow(log)
    flow.handle = lambda r: (_ for _ in ()).throw(httpx.ConnectError("connect to http://u:pw-9@10.0.0.1:8080"))
    flow.transport = httpx.MockTransport(flow.handle)
    result = run_submit(flow, Ctx(log))
    assert result.job == Retry("pool.proxy_failed") and "pw-9" not in repr(result)
    assert [type(e) for e in result.account] == [ProxyBad, Cooldown]


def test_an_account_without_a_project_gets_one_before_the_send_and_the_job_names_it():
    log = []
    flow = Flow(log, rpc={"jHPbke": frame("jHPbke", [PROJECT, ["relay"]]), "ogiZ0b": GOOD})
    ctx = Ctx(log, state={})
    result = run_submit(flow, ctx)
    assert isinstance(result.job, Completed)
    assert log.index("jHPbke") < log.index("mark_sent") and ctx.refs[0][1]["project"] == PROJECT
    log = []
    flow = Flow(log, rpc={"jHPbke": BAD_ARG.replace("Zzl0ze", "jHPbke")})
    assert run_submit(flow, Ctx(log, state={})).job == Retry(errors.HTTP_ERROR) and "mark_sent" not in log


def test_no_secret_reaches_a_log_an_event_a_reference_or_a_result(caplog):
    log = []
    flow, ctx = Flow(log, rpc={"ogiZ0b": [REFUSED_7, BAD_ARG.replace("Zzl0ze", "ogiZ0b")]}), Ctx(log)
    tokens = Tokens(log)
    with caplog.at_level(logging.DEBUG):
        result = run_submit(flow, ctx, tokens)
    seen = caplog.text + repr(ctx.events) + repr(ctx.refs) + repr(result)
    for secret in (PSID, PSIDTS, NEW_PSIDTS, AT_TOKEN, *tokens.given):
        assert secret not in seen


# ---- reconcile -----------------------------------------------------------------------------------------------------
def reconcile(flow, ctx, state):
    return asyncio.run(R.reconcile(ctx, job(state), settings(), Sessions(), transport=flow.transport))


ABSENT = "    if hit is None:\n        return StillUnknown(answered=True)"


@mutation(RESULTS_FILE, ABSENT, "    if hit is None:\n        return Found(batches[-1], {}, True)",
          expect="never found from absence")
@mutation(RESULTS_FILE, "        if len(record) > 5 and record[5] in batches",
          "        if len(record) > 5 and record[5] == batches[0]", expect="any batch the job sent")
def test_reconcile_finds_any_batch_the_job_sent_and_absence_stays_unknown():
    log = []
    listing = frame("Zzl0ze", LISTING["payload"])
    state = {"project": PROJECT, "batch": IMG2_BATCH, "batches": ["refused-batch-0", IMG2_BATCH]}
    got = reconcile(Flow(log, rpc={"Zzl0ze": listing}), Ctx(log), state)
    assert got == Found(IMG2_BATCH, {"batch": IMG2_BATCH, "media": IMG2_MEDIA,
                                     "workflow": "186b683d-de15-4a96-b173-b3b39a1b39bc"}, True), \
        "any batch the job sent: %s" % (got,)
    missing = {"project": PROJECT, "batch": "b-never", "batches": ["b-never"]}
    got = reconcile(Flow(log, rpc={"Zzl0ze": listing}), Ctx(log), missing)
    assert got == StillUnknown(answered=True), "never found from absence: %s" % (got,)


def test_a_listing_that_cannot_be_read_says_nothing():
    log = []
    state = {"project": PROJECT, "batch": IMG2_BATCH, "batches": [IMG2_BATCH]}
    for answer in (BAD_ARG, httpx.Response(500, text=""), httpx.ConnectError("down")):
        assert reconcile(Flow(log, rpc={"Zzl0ze": answer}), Ctx(log), state) == StillUnknown(), answer
    assert reconcile(Flow(log, signed_in=False), Ctx(log), state) == StillUnknown()
    assert reconcile(Flow(log), Ctx(log), {}) == StillUnknown()


def test_the_listing_is_read_with_the_media_option():
    log = []
    flow = Flow(log, rpc={"Zzl0ze": frame("Zzl0ze", LISTING["payload"])})
    reconcile(flow, Ctx(log), {"project": PROJECT, "batches": [IMG2_BATCH]})
    assert args_of(flow.calls("Zzl0ze")[0]) == ["projects/" + PROJECT, None, None, None, [1]]


# ---- poll ----------------------------------------------------------------------------------------------------------
def poll(flow, ctx, state):
    return asyncio.run(R.poll(ctx, job(state), settings(), Sessions(), transport=flow.transport, rand=lambda: 0))


def test_poll_signs_the_found_image_again_and_delivers_it():
    log = []
    flow = Flow(log, rpc={"as29s": frame("as29s", GET_MEDIA["payload"])})
    got = poll(flow, Ctx(log), {"project": PROJECT, "batch": IMG2_BATCH, "media": IMG2_MEDIA})
    assert isinstance(got.outcome, Done) and got.outcome.outputs[0].ref.startswith(
        "https://flow-content.google/image/" + IMG2_MEDIA)
    assert args_of(flow.calls("as29s")[0]) == [IMG2_MEDIA] and "Zzl0ze" not in log
    log = []
    flow = Flow(log, rpc={"Zzl0ze": frame("Zzl0ze", LISTING["payload"]), "as29s": frame("as29s",
                                                                                         GET_MEDIA["payload"])})
    got = poll(flow, Ctx(log), {"project": PROJECT, "batch": IMG2_BATCH, "batches": [IMG2_BATCH]})
    assert isinstance(got.outcome, Done) and got.outcome.outputs[0].upstream_id == IMG2_MEDIA


def test_poll_waits_while_the_image_is_not_listed_or_flow_cannot_be_read():
    log = []
    got = poll(Flow(log, rpc={"Zzl0ze": frame("Zzl0ze", LISTING["payload"])}), Ctx(log),
               {"project": PROJECT, "batches": ["b-never"]})
    assert got.outcome == Pending("not listed yet", 30)
    got = poll(Flow(log, rpc={"as29s": BAD_ARG.replace("Zzl0ze", "as29s")}), Ctx(log),
               {"project": PROJECT, "media": IMG2_MEDIA})
    assert isinstance(got.outcome, Pending) and got.outcome.state_patch == {"media": IMG2_MEDIA}
    got = poll(Flow(log, rpc={"as29s": httpx.ReadTimeout("slow")}), Ctx(log), {"project": PROJECT, "media": "m"})
    assert isinstance(got.outcome, Pending) and not got.outcome.answered


# ---- fetch ---------------------------------------------------------------------------------------------------------
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2000


@mutation(RESULTS_FILE, "out.ref, hosts=MEDIA_HOSTS, proxy=proxy_url(ctx.account.proxy),",
          "out.ref, hosts=MEDIA_HOSTS, proxy=None,", expect="not through the account proxy")
def test_fetch_downloads_the_signed_link_through_the_account_proxy_only():
    def drain(ref, site, proxy):
        ctx = Ctx([], proxy=proxy)

        async def go():
            return b"".join([c async for c in R.fetch(ctx, Output(ref, "image/jpeg", 0, IMG2_MEDIA),
                                                      media_transport=site, media_resolver=public_resolver)])
        return asyncio.run(go())

    site = MediaSite(b"direct-bytes", JPEG)
    got = drain(SIGNED_LINK, site, Proxy("10.0.0.1", 8080, "user:pw"))
    assert site.routes == ["http://user:pw@10.0.0.1:8080"], "not through the account proxy: %s" % site.routes
    assert got == JPEG
    plain = MediaSite(JPEG)
    assert drain(SIGNED_LINK, plain, None) == JPEG and plain.routes == [None]
    with pytest.raises(UnsafeUrl):
        drain("https://evil.example/image/x", MediaSite(JPEG), None)
