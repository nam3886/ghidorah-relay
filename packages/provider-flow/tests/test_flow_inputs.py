"""Input images over a fake flow.google.com (no network): the upload (`maseQ`, recorded 25/10 in
fixtures/flow/live-261025b), image to image, the video operations of flow's web app (start frame, first and last
frame, references), Omni's 360p and the plan gate. Each rule has a test that fails when it breaks, and a mutation that
proves it."""

import asyncio
import base64
import copy
import dataclasses
import json

import httpx
from flowkit import (
    BAD_ARG,
    CREDITS_50,
    FIX,
    GENERATED,
    PROJECT,
    STATUS_DONE,
    VIDEO_BATCH,
    VIDEO_MEDIA,
    VIDEO_MEDIA_DONE,
    VIDEO_SENT,
    VIDEO_WORKFLOW,
    Ctx,
    Flow,
    args_of,
    frame,
    settings,
)
from test_flow_jobs import NOW, WALL, Clock, Tokens

from relay_core import frames
from relay_core.providers import (
    Accepted,
    Completed,
    CreateRequest,
    Done,
    JobView,
    MediaIn,
    MediaRef,
    Retry,
    Tier,
)
from relay_core.testing.mutation import mutation
from relay_flow import errors
from relay_flow.engine import results as R
from relay_flow.engine.http import Sessions
from relay_flow.engine.submit import submit
from relay_flow.spec import SPEC

SUBMIT_FILE = "packages/provider-flow/src/relay_flow/engine/submit.py"
RESULTS_FILE = "packages/provider-flow/src/relay_flow/engine/results.py"
UPLOADED = json.loads((FIX / "live-261025b" / "upload_maseQ.json").read_text(encoding="utf-8"))
CREDITS_PRO = json.loads((FIX / "live-261025" / "credits_pro.json").read_text(encoding="utf-8"))["payload"]
IMAGES = (b"\xff\xd8\xffJPEG-one", b"\x89PNG-two", b"\xff\xd8\xffJPEG-three")
PROMPT = "the fox walks slowly through the snow"


def media_id(i: int) -> str:
    return "0000000%d-1111-4111-8111-111111111111" % i


def uploads():
    """flow's recorded upload answer, each call naming a new media id (the first, the second...)."""
    seen = []

    def answer(request):
        p = copy.deepcopy(UPLOADED["payload"])
        p[0][0] = p[1][3][4] = media_id(len(seen))
        seen.append(request)
        return frame("maseQ", p)
    return answer


def make_job(model, n, mode=None, duration=None, ratio=None, prompt=PROMPT):
    refs = [MediaRef(i, "inputs/job/%d" % i, "image/png" if i == 1 else "image/jpeg", len(IMAGES[i % 3]))
            for i in range(n)]
    plan = SPEC.plan(CreateRequest(model=model, prompt=prompt, ratio=ratio, duration=duration,
                                   images=tuple(MediaIn("0" * 64, r.mime, r.size) for r in refs),
                                   options={"mode": mode} if mode else {}), settings(), {})
    text = frames.render(SPEC.frames.defaults[plan.media_type], prompt, "7100", SPEC.frames.verbatim)

    async def inputs():
        return refs
    return JobView("job-i", "flow", plan.operation, plan.model, plan.params, prompt, "7100", text, None, None, None,
                   {}, inputs, frame_id=1)


async def read_media(ref):
    return IMAGES[ref.idx % 3]


def run(flow, ctx, job, tokens=None):
    clock = Clock()
    return asyncio.run(submit(ctx, job, settings(), Sessions(), tokens or Tokens(ctx.log), transport=flow.transport,
                              now=lambda: NOW, clock=clock, wall=lambda: WALL, sleep=clock.sleep, seed=lambda: 7,
                              read_media=read_media))


def accepted(rpcid):
    """flow's recorded video acceptance (the same reply type for every VideoFx send), our batch echoed."""
    def answer(request):
        batch = args_of(request)[2][0]
        return frame(rpcid, json.loads(json.dumps(VIDEO_SENT["payload"]).replace(VIDEO_BATCH, batch)))
    return answer


# ---- image to image -------------------------------------------------------------------------------------------------
UPLOAD_FIRST = '            if job.operation not in ("text_to_image", "text_to_video"):'


@mutation(SUBMIT_FILE, "[[m, None, None, None, 1] for m in refs] or None", "None", expect="references")
@mutation(SUBMIT_FILE, "[[m, None, None, None, 1] for m", "[[m, None, None, None, 2] for m", expect="references")
@mutation(SUBMIT_FILE, UPLOAD_FIRST, "            if False:", expect="uploaded")
@mutation(SUBMIT_FILE, "project], base64.b64encode(data).decode(), mime,", "project], data.hex(), mime,",
          expect="as recorded")
def test_input_images_are_uploaded_first_without_a_token_and_sent_as_references_of_the_image():
    log = []
    flow = Flow(log, rpc={"maseQ": uploads(), "ogiZ0b": frame("ogiZ0b", GENERATED["payload"])})
    ctx = Ctx(log)
    tokens = Tokens(log)
    result = run(flow, ctx, make_job("flow-nano-banana-2.1", 2, ratio="1:1"), tokens)
    assert isinstance(result.job, Completed), result.job
    assert log[log.index("page"):].count("maseQ") == 2, "each input image is uploaded"
    assert log.index("maseQ") < log.index("token") and log.index("maseQ") < log.index("mark_sent"), \
        "uploaded before the token and before anything is sent: %s" % log
    assert tokens.actions == ["IMAGE_GENERATION"], "an upload takes no reCAPTCHA token"
    for i, call in enumerate(flow.calls("maseQ")):
        assert args_of(call) == [[None, 22, None, None, None, PROJECT], base64.b64encode(IMAGES[i]).decode(),
                                 ("image/jpeg", "image/png")[i], True, None, None, None, False], \
            "the upload is sent as recorded"
    entry = args_of(flow.calls("ogiZ0b")[0])[1][0]
    assert entry[2] == [[media_id(0), None, None, None, 1], [media_id(1), None, None, None, 1]], \
        "the uploads are the image's references: %s" % entry[2]
    assert entry[4:6] == [1, "BELUGA"]
    log = []
    flow = Flow(log, rpc={"ogiZ0b": frame("ogiZ0b", GENERATED["payload"])})
    run(flow, Ctx(log), make_job("flow-nano-banana-2.1", 0))
    assert "maseQ" not in log and args_of(flow.calls("ogiZ0b")[0])[1][0][2] is None, "text to image as before"


OUR_PROJECT = "    if len(record) > 1 and isinstance(record[0], str) and record[0] and record[1] == project:"


@mutation(SUBMIT_FILE, OUR_PROJECT, "    if len(record) > 1 and isinstance(record[0], str) and record[0]:",
          expect="another project")
@mutation(SUBMIT_FILE, "                    mid = uploaded(up.payload, project) if up.status == 200 else None",
          "                    mid = uploaded(up.payload, project)", expect="HTTP 500")
def test_an_input_image_flow_did_not_take_is_a_retry_before_anything_is_sent():
    other = copy.deepcopy(UPLOADED["payload"])
    other[0][1] = "77777777-7777-4777-8777-777777777777"
    for answer, why in ((BAD_ARG.replace("Zzl0ze", "maseQ"), "an error envelope"),
                        (frame("maseQ", other), "an upload into another project"),
                        (httpx.Response(500, text=frame("maseQ", UPLOADED["payload"])), "HTTP 500"),
                        (frame("maseQ", [None]), "no media record")):
        log = []
        flow = Flow(log, rpc={"maseQ": answer, "nzlxg": frame("nzlxg", CREDITS_50["payload"]),
                              "eb1hJf": accepted("eb1hJf")})
        ctx = Ctx(log)
        result = run(flow, ctx, make_job("flow-veo-3.1-lite", 1))
        assert result.job == Retry(errors.UPLOAD_FAILED), "%s: %s" % (why, result.job)
        assert "token" not in log and "mark_sent" not in log and not flow.calls("eb1hJf"), why
        assert ctx.events and Tier("free") in result.account, "named, and the pre-send read reported"


# ---- the video operations -------------------------------------------------------------------------------------------
# operation -> (rpc, the entry flow's web app builds, with M0/M1 the uploaded media ids and IDS our request ids)
M0, M1, M2 = media_id(0), media_id(1), media_id(2)
TEXT = [None, None, [[[PROMPT]]]]
SHAPES = {
    ("flow-veo-3.1-lite", 1, None, None): ("eb1hJf", [TEXT, "veo_3_1_i2v_lite", 2, None, [None, M0], "IDS"]),
    ("flow-veo-3.1-lite", 2, None, None): ("nprQif", [TEXT, "veo_3_1_interpolation_lite", 2, None, [None, M0],
                                                      [None, M1], "IDS"]),
    ("flow-veo-3.1-fast", 3, "references", None): ("MZZa6b", [TEXT, [[None, M0], [None, M1], [None, M2]],
                                                              "veo_3_1_r2v_fast_landscape", 2, None, "IDS"]),
    ("flow-omni-flash-360p", 0, None, 4): ("YhhmEf", [TEXT, "abra_t2v_4s_360p", 2, None, "IDS", None, None, [4]]),
    ("flow-omni-flash-360p", 1, None, 4): ("eb1hJf", [TEXT, "abra_i2v_4s_360p", 2, None, [None, M0], "IDS", None,
                                                      None, None, [4]]),
    ("flow-omni-flash-360p", 2, None, 4): ("nprQif", [TEXT, "omni_flash_i2v_4s_first_last_360p", 2, None,
                                                      [None, M0], [None, M1], "IDS"]),
    ("flow-omni-flash-360p", 1, "references", 4): ("MZZa6b", [TEXT, [[None, M0]], "abra_r2v_4s_360p", 2, None, "IDS",
                                                              None, None, None, None, None, [4]]),
    ("flow-omni-flash-720p", 1, None, 10): ("eb1hJf", [TEXT, "abra_i2v_10s", 2, None, [None, M0], "IDS"]),
}


@mutation(SUBMIT_FILE, "None, frames[0], ids, None", "None, ids, frames[0], None", expect="as flow's web app")
@mutation(SUBMIT_FILE, "frames[0], ids, None, None, None, res]", "frames[0], ids, res]", expect="as flow's web app")
@mutation(SUBMIT_FILE, '[text, frames, params["model"], params["aspect"]',
          '[text, params["model"], frames, params["aspect"]', expect="as flow's web app")
@mutation(SUBMIT_FILE, "None, frames[0], frames[1], ids]", "None, frames[1], frames[0], ids]",
          expect="as flow's web app")
@mutation(SUBMIT_FILE, '    res = [params["resolution"]] if params.get("resolution") else None', "    res = None",
          expect="as flow's web app")
@mutation(SUBMIT_FILE, "                    rpcid = VIDEO_RPCS[job.operation]",
          "                    rpcid = GENERATE_VIDEO", expect="its own rpc")
@mutation(SUBMIT_FILE, '    return job.operation in VIDEO_RPCS', '    return job.operation == "text_to_video"',
          expect="Accepted")
def test_each_video_operation_goes_to_its_own_rpc_with_the_images_where_flows_web_app_puts_them():
    """The shapes read from flow's web app (`BatchAsyncGenerateVideoStartImage`, `...StartAndEndImage`,
    `...ReferenceImages`), each accepted live 25/10; the trailing empty fields are not sent."""
    for (model, n, mode, duration), (rpcid, want) in SHAPES.items():
        log = []
        flow = Flow(log, rpc={"maseQ": uploads(), "nzlxg": frame("nzlxg", CREDITS_50["payload"]),
                              "ogiZ0b": frame("ogiZ0b", GENERATED["payload"]),
                              **{r: accepted(r) for r in ("YhhmEf", "eb1hJf", "nprQif", "MZZa6b")}})
        ctx = Ctx(log)
        tokens = Tokens(log)
        result = run(flow, ctx, make_job(model, n, mode, duration), tokens)
        why = "%s %d images %s" % (model, n, mode)
        assert isinstance(result.job, Accepted), "Accepted: %s: %s" % (why, result.job)
        sent = [r for r in ("YhhmEf", "eb1hJf", "nprQif", "MZZa6b") if flow.calls(r)]
        assert sent == [rpcid], "%s goes to its own rpc %s: %s" % (why, rpcid, sent)
        args = args_of(flow.calls(rpcid)[0])
        entry = args[0][0]
        ids = entry[want.index("IDS")]
        assert isinstance(ids, list) and ids[:4] == [None] * 4 and len(ids) == 6, \
            "%s as flow's web app builds it: %s" % (why, entry)
        assert entry == [ids if w == "IDS" else w for w in want], "%s as flow's web app builds it: %s" % (why, entry)
        assert len(args) == 3 and args[2][1] == 1 and args[1][5] == PROJECT and tokens.actions == ["VIDEO_GENERATION"]
        assert log.count(rpcid) == 1 and log.count("mark_sent") == 1 and log.count("maseQ") == n


TIER_GATE = '                if job.params.get("tiers") and tier not in job.params["tiers"]:'


@mutation(SUBMIT_FILE, TIER_GATE, "                if False:", expect="sent on a plan without the option")
@mutation(SUBMIT_FILE, "                tier = next((e.tier for e in read if isinstance(e, Tier)), None)",
          '                tier = "ultra"', expect="sent on a plan without the option")
def test_an_option_the_accounts_plan_lacks_is_a_retry_before_anything_is_uploaded_or_spent():
    """Veo's 4 s and 6 s are Ultra's only and Quality runs on Pro and Ultra (GetModels 25/10): the plan read right
    before the send (`nzlxg`) decides; another account may take the job."""
    for model, n, duration, credits, ok in (("flow-veo-3.1-lite", 1, 4, CREDITS_50["payload"], False),
                                            ("flow-veo-3.1-fast", 0, 6, CREDITS_PRO, False),
                                            ("flow-veo-3.1-quality", 0, 8, CREDITS_50["payload"], False),
                                            ("flow-veo-3.1-quality", 1, 8, CREDITS_PRO, True),
                                            ("flow-veo-3.1-lite", 1, 8, CREDITS_50["payload"], True)):
        log = []
        flow = Flow(log, rpc={"maseQ": uploads(), "nzlxg": frame("nzlxg", credits),
                              **{r: accepted(r) for r in ("YhhmEf", "eb1hJf")}})
        ctx = Ctx(log)
        result = run(flow, ctx, make_job(model, n, duration=duration))
        why = "%s %d s on %s" % (model, duration, "pro" if credits is CREDITS_PRO else "free")
        if ok:
            assert isinstance(result.job, Accepted), "%s: %s" % (why, result.job)
            continue
        assert result.job == Retry(errors.TIER_REQUIRED), "sent on a plan without the option: %s: %s" % (
            why, result.job)
        assert not {"maseQ", "token", "mark_sent", "YhhmEf"} & set(log), why
        assert ctx.events and any(isinstance(e, Tier) for e in result.account), "named, and the plan read reported"


def test_a_video_from_images_is_polled_as_a_video_and_delivers_its_mp4():
    log = []
    status = copy.deepcopy(STATUS_DONE["payload"])
    flow = Flow(log, rpc={"jwpduf": frame("jwpduf", status), "as29s": frame("as29s", VIDEO_MEDIA_DONE["payload"])})
    job = dataclasses.replace(make_job("flow-veo-3.1-lite", 2), provider_state={
        "project": PROJECT, "batch": VIDEO_BATCH, "media": VIDEO_MEDIA, "workflow": VIDEO_WORKFLOW})
    got = asyncio.run(R.poll(Ctx(log), job, settings(), Sessions(), transport=flow.transport, rand=lambda: 0))
    assert isinstance(got.outcome, Done) and got.outcome.outputs[0].mime == "video/mp4", got.outcome
