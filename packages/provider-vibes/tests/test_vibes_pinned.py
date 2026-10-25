"""The modes that work on the customer's own earlier vibes job (pinned jobs: the core runs them on the account that made
the source): extend, video edit and image edit, over a fake vibes.ai fed vibes' recorded answers
(fixtures/vibes/pinned-261025: the live smoke of 25/10; v2v-261024: the v2v spike). Each money or safety rule has a
test that fails when the rule breaks, and a mutation that proves it."""

from datetime import timedelta
from types import SimpleNamespace

import httpx
from test_vibes_jobs import NOW, SETTINGS, Vibes, answer, body, rec, run_submit

from relay_core import frames
from relay_core.providers import (
    Accepted,
    Ambiguous,
    Completed,
    Cooldown,
    CreateRequest,
    Fail,
    Output,
    Retry,
    SourceIn,
    SourceJob,
    Success,
)
from relay_core.testing.mutation import mutation
from relay_vibes import errors
from relay_vibes.engine.http import Rests
from relay_vibes.spec import SPEC

SUBMIT_FILE = "packages/provider-vibes/src/relay_vibes/engine/submit.py"
SOURCE_READ = rec("pinned-261025/extend_001_read_source")
SOURCE_BATCH = SOURCE_READ["body"]["batch"]
SOURCE_ITEM = SOURCE_BATCH["content"][0]
IMAGE_SOURCE = rec("pinned-261025/edit_001_read_source")["body"]["batch"]["content"][0]


def source_job(kind="video") -> SourceJob:
    """The source as the core hands it to the engine: the earlier job's batch and its recorded outputs."""
    if kind == "image":
        batch = IMAGE_SOURCE["batchId"]
        out = {"ref": IMAGE_SOURCE["id"], "mime": "image/png", "idx": 0, "upstream_id": IMAGE_SOURCE["mediaEntId"],
               "meta": {"batch": batch}}
        return SourceJob("src-image", "vibes-image", "text_to_image", batch, {"batch": batch, "outputs": [out]})
    out = {"ref": SOURCE_ITEM["id"], "mime": "video/mp4", "idx": 0, "upstream_id": SOURCE_ITEM["mediaEntId"],
           "meta": {"batch": SOURCE_BATCH["id"]}}
    return SourceJob("src-video", "vibes-video", "image_to_video", SOURCE_BATCH["id"],
                     {"batch": SOURCE_BATCH["id"], "outputs": [out]})


def job(model="vibes-video-extend", prompt="the birds fly away into the sunset", *, source=None):
    kind = "image" if model == "vibes-image-edit" else "video"
    src = source or source_job(kind)
    p = SPEC.plan(CreateRequest(model=model, prompt=prompt, source=SourceIn(src.id, "vibes", src.model, kind,
                                                                            src.operation)), SETTINGS, {})

    async def inputs():
        return []

    text = frames.render(SPEC.frames.defaults[p.media_type], prompt, "7100", SPEC.frames.verbatim)
    return SimpleNamespace(id="job-1", operation=p.operation, params=dict(p.params), prompt=prompt, sent_text=text,
                           frame_id=1, external_ref=None, provider_state={}, inputs=inputs, sent_at=None,
                           render_since=None, source=src)


def read_source(request):
    return answer(SOURCE_READ)


# ---- extend and video edit -------------------------------------------------------------------------------------------
SOURCE_INPUT = "            body.update(inputs=[source_input], projectId=None)"


@mutation(SUBMIT_FILE, SOURCE_INPUT, "            pass", expect="the source video goes in the generate")
def test_an_extend_reads_the_source_batch_then_sends_the_extend_as_vibes_web_app_does():
    log: list = []
    fake = Vibes(log, batch=read_source, generate=answer(rec("pinned-261025/extend_003_generate")))
    result, ctx = run_submit(fake, job())
    assert log == ["batch", "create", "report_ref", "mark_sent", "generate"], log
    assert fake.sent("batch")[0].url.path == "/api/generation-batches/" + SOURCE_BATCH["id"]
    created, sent = body(fake.sent("create")[0]), body(fake.sent("generate")[0])
    [given] = sent["inputs"]
    assert given["type"] == "extend", "the source video goes in the generate: %s" % given
    assert (given["mediaEntId"], given["videoUrl"]) == (SOURCE_ITEM["mediaEntId"], SOURCE_ITEM["videoUrl"])
    # the source's own prompt goes on; the customer's words are the directive
    assert given["prompt"] == created["prompt"] == SOURCE_ITEM["prompt"]
    assert given["extendDirective"] == sent["config"]["extendDirective"] == "the birds fly away into the sunset"
    config = sent["config"]
    assert config == given["config"] == created["config"]
    assert (config["videoModel"], config["generationType"], config["directGeneration"]) == (
        "midjen-extend", "extend", True)
    assert config["sourceContentItemIds"] == [{"id": SOURCE_ITEM["id"], "source": "extend_video"}]
    assert config["sourceVideoUrl"] == config["audioSourceUrl"] == SOURCE_ITEM["videoUrl"]
    assert sent["projectId"] is None and sent["batchId"] == created["id"]
    # what the recorded live extend sent, field by field (its ids and URLs are the source's)
    live = rec("pinned-261025/extend_003_generate")["request"]
    assert sorted(config) == sorted(live["config"]) and sorted(given) == sorted(live["inputs"][0])
    assert isinstance(result.job, Accepted) and result.job.taken and ctx.refs == [(created["id"],
                                                                                  {"batch": created["id"]})]


@mutation(SUBMIT_FILE, '        if self.job.operation == "video_to_video":', "        if False:",
          expect="a video edit is a v2v")
def test_a_video_edit_sends_the_source_as_a_v2v_input_with_the_customers_words():
    fake = Vibes([], batch=read_source, generate=answer(rec("pinned-261025/v2v_003_generate")))
    result, _ = run_submit(fake, job("vibes-video-edit", "make it a pencil sketch"))
    created, sent = body(fake.sent("create")[0]), body(fake.sent("generate")[0])
    [given] = sent["inputs"]
    assert given["type"] == "video", "a video edit is a v2v: %s" % given
    assert (given["mediaEntId"], given["videoUrl"], given["prompt"]) == (
        SOURCE_ITEM["mediaEntId"], SOURCE_ITEM["videoUrl"], "make it a pencil sketch")
    assert sent["config"] == given["config"] == created["config"] == {
        "directGeneration": True, "videoModel": "midjen-video-edit", "imageModel": "midjen-base", "editType": "v2v",
        "generationType": "v2v", "sourceVideoUrl": SOURCE_ITEM["videoUrl"],
        "sourceContentItemIds": [{"id": SOURCE_ITEM["id"], "source": "v2v"}]}
    assert created["prompt"] == "make it a pencil sketch"
    assert isinstance(result.job, Accepted) and result.job.taken


GONE_404 = "        if resp.status_code == 404:\n            raise self._source_gone("


@mutation(SUBMIT_FILE, GONE_404, "        if False:\n            raise self._source_gone(",
          expect="a batch vibes no longer has")
def test_a_source_vibes_no_longer_holds_ends_the_job_before_anything_is_sent_and_a_failed_read_is_a_retry():
    gone_item = {**SOURCE_BATCH, "content": [{**SOURCE_ITEM, "videoUrl": None}]}
    cases = {"a batch vibes no longer has": httpx.Response(404, json={"error": "Not found"}),
             "an item with no video": httpx.Response(200, json={"batch": gone_item}),
             "a batch with no items": httpx.Response(200, json={"batch": {**SOURCE_BATCH, "content": []}})}
    for name, resp in cases.items():
        log: list = []
        result, _ = run_submit(Vibes(log, batch=resp), job())
        assert isinstance(result.job, Fail) and result.job.code == errors.SOURCE_MISSING, (name, result.job)
        assert log == ["batch"], "%s: nothing is marked or sent: %s" % (name, log)
    for resp in (httpx.Response(500, json={"error": "Failed to fetch generation batch"}), answer(
            rec("s5-live/video_041_video_poll_after"))):
        log = []
        result, _ = run_submit(Vibes(log, batch=resp), job())
        assert isinstance(result.job, Retry) and log == ["batch"], (resp.status_code, result.job, log)
    src = source_job()
    log = []
    result, _ = run_submit(Vibes(log), job(source=SourceJob(src.id, src.model, src.operation, None, {})))
    assert isinstance(result.job, Fail) and result.job.code == errors.SOURCE_MISSING and log == []


def test_the_extend_picks_the_item_the_source_job_delivered():
    other = {**SOURCE_ITEM, "id": "other-content", "mediaEntId": "999", "videoUrl": "https://cdn.example/other.mp4"}
    two = {**SOURCE_BATCH, "content": [other, SOURCE_ITEM]}
    fake = Vibes([], batch=httpx.Response(200, json={"batch": two}),
                 generate=answer(rec("pinned-261025/extend_003_generate")))
    run_submit(fake, job())
    assert body(fake.sent("generate")[0])["inputs"][0]["mediaEntId"] == SOURCE_ITEM["mediaEntId"]


def test_an_image_edit_works_on_the_output_the_request_named():
    src = source_job("image")
    second = {**src.provider_state["outputs"][0], "idx": 1, "ref": "other", "upstream_id": "4242"}
    two = SourceJob(src.id, src.model, src.operation, src.external_ref,
                    {**src.provider_state, "outputs": [src.provider_state["outputs"][0], second]}, idx=1)
    fake = Vibes([], generate=answer(EDIT))
    run_submit(fake, job("vibes-image-edit", source=two))
    assert body(fake.sent("generate")[0])["sourceImageEntId"] == "4242"


# ---- image edit ------------------------------------------------------------------------------------------------------
EDIT_MARK = ("        await ctx.mark_sent()                               # immediately before the POST\n"
             "        try:\n            resp = await client.post(IMAGE_EDIT_PATH")
EDIT = rec("pinned-261025/edit_002_generate")


@mutation(SUBMIT_FILE, EDIT_MARK, "        try:\n            resp = await client.post(IMAGE_EDIT_PATH",
          expect="marked sent right before the one edit")
def test_an_image_edit_is_marked_sent_right_before_its_one_post_and_completes_with_the_edited_item():
    log: list = []
    fake = Vibes(log, generate=answer(EDIT))
    result, ctx = run_submit(fake, job("vibes-image-edit", "turn it into a snowy winter scene"))
    assert log == ["mark_sent", "generate"], "marked sent right before the one edit: %s" % log
    sent = fake.sent("generate")[0]
    assert sent.url.path == "/api/generate/image-edit" and ctx.refs == []
    assert body(sent) == {"sourceImageEntId": IMAGE_SOURCE["mediaEntId"],
                          "editPrompt": "turn it into a snowy winter scene", "projectId": None}
    item = EDIT["body"]["contentItem"]
    assert result.job == Completed((Output(item["id"], "image/png", 0, item["mediaEntId"],
                                           {"batch": EDIT["body"]["batchId"]}),)) and result.account == (Success(),)


def test_an_unclear_image_edit_is_ambiguous_one_post_and_its_plan_fails_it_with_the_refund():
    cases = [httpx.Response(500, json={"error": "Internal"}), httpx.Response(401, json={"error": "Unauthorized"}),
             httpx.Response(429, json={"error": "slow down"}), httpx.Response(200, text="<html>not json</html>"),
             httpx.Response(200, json={"success": True}), httpx.ReadTimeout("slow"),
             answer(rec("s5-live/video_041_video_poll_after"))]
    for resp in cases:
        fake = Vibes([], generate=resp)
        rests = Rests(lambda: NOW)
        result, _ = run_submit(fake, job("vibes-image-edit"), rests)
        assert isinstance(result.job, Ambiguous), (resp, result.job)
        assert len(fake.sent("generate")) == 1
    assert result.account == (Cooldown("vibes security checkpoint", NOW + timedelta(seconds=600), recheck=True),)


RESTING_FIRST = "        left = rests.left_s(ctx.account.id)\n        if left:"
SIGNED_OUT_DOUBT = "        if resp.status_code in SIGNED_OUT_STATUSES:     # unclear for the job"


@mutation(SUBMIT_FILE, RESTING_FIRST, "        left = 0\n        if left:", expect="a rest sends nothing")
@mutation(SUBMIT_FILE, SIGNED_OUT_DOUBT, "        if False:     # unclear for the job",
          expect="a doubt about the account")
def test_an_image_edit_on_a_resting_account_is_a_retry_and_a_signed_out_answer_doubts_the_account():
    rests = Rests(lambda: NOW)
    rests.start("acc-1", 300)
    log: list = []
    result, _ = run_submit(Vibes(log), job("vibes-image-edit"), rests)
    assert log == [] and result.job == Retry("pool.account_throttled"), "a rest sends nothing: %s %s" % (log,
                                                                                                         result.job)
    result, _ = run_submit(Vibes([], generate=httpx.Response(401, json={"error": "Unauthorized"})),
                           job("vibes-image-edit"))
    assert isinstance(result.job, Ambiguous)
    assert [type(e).__name__ for e in result.account] == ["Suspect", "Cooldown"], (
        "a doubt about the account: %s" % (result.account,))


@mutation(SUBMIT_FILE, '            if item.get("error") or not has_media(item):', "            if False:",
          expect="a refused edit")
def test_an_image_edit_vibes_refused_fails_with_its_refund_and_a_source_without_an_image_id_sends_nothing():
    refused_item = {**EDIT["body"], "contentItem": {**EDIT["body"]["contentItem"], "imageUrl": None, "mediaEntId": None,
                                                    "error": "This content could not be generated."}}
    for resp, code in ((httpx.Response(200, json=refused_item), errors.CONTENT_REFUSED),
                       (httpx.Response(200, json={"success": False, "error": "blocked"}), errors.GENERATE_REFUSED),
                       (httpx.Response(400, json={"error": "invalid prompt"}), errors.GENERATE_REFUSED)):
        result, _ = run_submit(Vibes([], generate=resp), job("vibes-image-edit"))
        assert isinstance(result.job, Fail) and result.job.code == code, "a refused edit: %s" % (result.job,)
    bare = source_job("image")
    bare = SourceJob(bare.id, bare.model, bare.operation, bare.external_ref, {"batch": bare.external_ref})
    log: list = []
    result, _ = run_submit(Vibes(log), job("vibes-image-edit", source=bare))
    assert isinstance(result.job, Fail) and result.job.code == errors.SOURCE_MISSING and log == []
