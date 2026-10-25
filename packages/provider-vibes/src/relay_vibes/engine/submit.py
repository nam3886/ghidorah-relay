"""Submit: send one job to vibes and map what vibes said to a `SubmitResult`.

The steps: (image to video only) upload the start frame; create the empty batch, whose id we choose; write that id to
the job (`ctx.report_ref`); `ctx.mark_sent()`; then the ONE generate POST. An image is drawn inside that POST (HOLD:
`Completed`), a video is only started by it (DETACH: `Accepted`, then polled).

Money rules that shape this file (each has a test and a mutation):
- The batch id is on the job BEFORE the generate is sent, so a send whose answer is lost can be looked up.
- `ctx.mark_sent()` runs immediately before the generate POST, never after: a send that was not marked could be sent
  again.
- There is ONE generate POST per job, ever. Nothing here repeats it, and after `mark_sent` nothing returns `Retry`
  or `NotTaken` (either would put the job back in the queue and send it again).
- A generate that gives no clear answer (no answer at all, a timeout, a 5xx, a sign-in error, the checkpoint) is
  `Ambiguous`: the job becomes unknown and the reconciler reads its batch. Only a 200 `success: true` is taken; only an
  explicit refusal from vibes' app (a 4xx JSON other than 401/403/429, or `success: false`) fails the job, and so
  does a finished image batch whose item vibes refused (`success: true`, but the item has an `error` or no media).

Before `mark_sent` every failure is an ordinary retry: the checkpoint `pool.account_throttled` with the account's
rest, a signed-out answer `pool.account_logged_out` with a spaced strike, a proxy that cannot reach vibes
`pool.proxy_failed`, any other error answer `vibes.http_error`.

The three modes that work on the customer's own earlier job (`job.source`, a pinned job on the account that made it):
- extend and video edit read the source's batch first (the video URL is signed and short-lived, so it is read, never
  stored), then go the batch way above with the body vibes' web app builds (bundle `/create`: handleExtendVideo,
  handleVideoToVideo; the v2v spike 24/10). A source vibes no longer holds fails before the send
  (`vibes.source_missing`, refunded).
- image edit (`POST /api/generate/image-edit` with the source's `mediaEntId`) answers synchronously with the edited
  item and a batch vibes chose, so nothing is written ahead and a lost answer cannot be looked up: its plan is
  `fail_refund`, and the core fails an `Ambiguous` at once with its refund. The item downloads like any image, by its
  id (live 25/10: 200 `image/png`).
"""

import base64
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Completed,
    Cooldown,
    Fail,
    Output,
    ProxyBad,
    Retry,
    SubmitResult,
    Success,
)
from relay_vibes import errors
from relay_vibes.constants import (
    BATCHES_PATH,
    GENERATE_PATH,
    GENERATE_TIMEOUT_S,
    IMAGE_EDIT_PATH,
    NETWORK_REST_S,
    OUTPUT_MIME,
    UPLOAD_PATH,
    UPLOAD_TIMEOUT_S,
)
from relay_vibes.engine.batch import (
    BATCH_KEY,
    batch_of,
    checkpoint_rest,
    has_media,
    is_done,
    items,
    outputs,
    render_error,
)
from relay_vibes.engine.check import SIGNED_OUT_STATUSES, doubt_effects
from relay_vibes.engine.http import Resting, Rests, client_for, is_checkpoint, json_of

THROTTLED = "pool.account_throttled"
LOGGED_OUT = "pool.account_logged_out"
PROXY_FAILED = "pool.proxy_failed"
NOT_A_REFUSAL = (*SIGNED_OUT_STATUSES, 429)     # 4xx answers that do not say vibes' app refused the order
VIDEO_SOURCE_OPS = ("extend_video", "video_to_video")     # the pinned video modes (their source is a video job)

_store = None


async def read_input_media(ref) -> bytes:
    """One job input image from the object store, the store created on first use (a text job needs none). The one
    place the engine reaches past `RunCtx` for a file: the protocol hands out only a `MediaRef`."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


class _Stop(Exception):
    """A step before the send failed: the result to return is attached."""

    def __init__(self, result: SubmitResult):
        super().__init__(type(result.job).__name__)
        self.result = result


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


def _config(params) -> dict[str, Any]:
    """The generation config vibes takes: the model, the video resolution, and the ratio as `aspectRatio` (measured
    23/10: an image asked for 16:9 came back 1280x720)."""
    kind = params["kind"]
    config: dict[str, Any] = {("imageModel" if kind == "image" else "videoModel"): params["model"]}
    if kind == "video" and params.get("resolution"):         # a pinned video mode keeps the source's size
        config["resolution"] = params["resolution"]
    if params.get("ratio"):
        config["aspectRatio"] = params["ratio"]
    return config


class _Run:
    def __init__(self, ctx, job, settings, rests: Rests, client: httpx.AsyncClient, now: Callable[[], datetime]):
        self.ctx, self.job, self.settings, self.rests, self.client, self.now = ctx, job, settings, rests, client, now
        self.kind = job.params["kind"]

    def _before_send(self, resp: httpx.Response, what: str) -> None:
        """A step before the send that vibes did not answer with 200 raises `_Stop` with its retry."""
        if resp.status_code == 200:
            return
        if is_checkpoint(resp):
            _, cooldown = checkpoint_rest(self.ctx, self.settings, self.rests, "vibes security checkpoint")
            raise _Stop(_result(Retry(THROTTLED), cooldown))
        if resp.status_code in SIGNED_OUT_STATUSES:
            raise _Stop(_result(Retry(LOGGED_OUT), *doubt_effects(
                "not signed in: vibes answered %d to the %s" % (resp.status_code, what), self.now())))
        raise _Stop(_result(Retry(errors.HTTP_ERROR)))

    async def _post(self, path: str, body: dict, what: str, timeout: float | None = None) -> dict:
        resp = await self.client.post(path, json=body, **({"timeout": timeout} if timeout else {}))
        self._before_send(resp, what)
        return json_of(resp)

    async def start_frame(self, read_media) -> dict | None:
        """Image to video: upload the one input image as a data URL; vibes gives back the handle the generate takes."""
        if self.job.operation != "image_to_video":
            return None
        ref = (await self.job.inputs())[0]
        data = await read_media(ref)
        url = "data:%s;base64,%s" % (ref.mime, base64.b64encode(data).decode("ascii"))
        body = await self._post(UPLOAD_PATH, {"image": url}, "upload", UPLOAD_TIMEOUT_S)
        if not body.get("mediaEntId"):
            raise _Stop(_result(Retry(errors.HTTP_ERROR)))
        return {"image_ent_id": str(body["mediaEntId"]), "image_url": body.get("imageUrl"), "source": "upload"}

    async def create_batch(self, prompt: str, config: dict) -> str:
        batch = str(uuid.uuid4())
        plural = self.kind + "s"
        body = await self._post(BATCHES_PATH, {
            "id": batch, "type": plural, "prompt": prompt, "timestamp": int(self.now().timestamp() * 1000),
            "isComplete": False, "config": config,
            "content": [{"id": batch + "-content-0", "type": self.kind, "isLoading": True}]}, "batch create")
        if body.get("id") != batch:
            raise _Stop(_result(Retry(errors.HTTP_ERROR)))
        return batch

    def _source_outputs(self) -> list:
        state = self.job.source.provider_state or {}
        outs = state.get("outputs")
        return outs if isinstance(outs, list) else []

    def _source_gone(self, why: str) -> _Stop:
        return _Stop(_result(Fail(errors.SOURCE_MISSING, why)))

    async def source_item(self) -> tuple[dict, dict]:
        """An extend or a video edit: the source job's batch as vibes holds it now, and the content item that job
        delivered (by its id, else the batch's first item). Before the send: a read that fails is a retry; a batch or an
        item vibes no longer has, or one without a video, ends the job (`vibes.source_missing`, refunded)."""
        src = self.job.source
        outs = self._source_outputs()
        batch = (src.provider_state or {}).get(BATCH_KEY) or src.external_ref
        if not batch:
            raise self._source_gone("the source job has no vibes batch")
        resp = await self.client.get("%s/%s" % (BATCHES_PATH, quote(str(batch), safe="")))
        if resp.status_code == 404:
            raise self._source_gone("vibes has no batch %s any more" % batch)
        self._before_send(resp, "source read")
        found = batch_of(resp)
        content = items(found or {})
        want = outs[0].get("ref") if outs and isinstance(outs[0], dict) else None
        item = next((c for c in content if c.get("id") == want), content[0] if content else None)
        if found is None or item is None or not item.get("videoUrl") or not item.get("mediaEntId"):
            raise self._source_gone("the source batch %s holds no video vibes can work on" % batch)
        return found, item

    def source_video(self, found: dict, item: dict, prompt: str) -> tuple[str, dict, dict]:
        """(the batch prompt, the config, the one generate input) of an extend or a video edit, built as vibes' web
        app builds them. An extend sends the source's own prompt and the customer's words as `extendDirective`; a video
        edit sends the customer's words as the prompt."""
        url, ent, model = item["videoUrl"], str(item["mediaEntId"]), self.job.params["model"]
        if self.job.operation == "video_to_video":
            config = {"directGeneration": True, "videoModel": model, "imageModel": "midjen-base", "editType": "v2v",
                      "generationType": "v2v", "sourceVideoUrl": url,
                      "sourceContentItemIds": [{"id": item["id"], "source": "v2v"}]}
            return prompt, config, {"type": "video", "mediaEntId": ent, "videoUrl": url, "prompt": prompt,
                                    "config": config}
        shaped = item.get("structuredOutput") if isinstance(item.get("structuredOutput"), dict) else {}
        old = found.get("config") if isinstance(found.get("config"), dict) else {}
        config = {**shaped, **old, "videoModel": model,
                  "imageModel": old.get("imageModel") or shaped.get("imageModel") or "midjen-base",
                  "generationType": "extend"}
        if item.get("videoHandle"):
            config["sourceVideoHandle"] = item["videoHandle"]
        if not config.get("sourceVideoUrl"):
            config["sourceVideoUrl"] = url
        if not config.get("audioSourceUrl"):
            config["audioSourceUrl"] = config["sourceVideoUrl"]
        config.update(directGeneration=True, sourceContentItemIds=[{"id": item["id"], "source": "extend_video"}],
                      extendDirective=prompt)
        original = str(item.get("prompt") or item.get("videoPrompt") or found.get("prompt") or prompt)
        return original, config, {"type": "extend", "mediaEntId": ent, "videoUrl": url, "prompt": original,
                                  "extendDirective": prompt, "config": config}

    def source_image(self) -> str:
        """An image edit: the `mediaEntId` of the image the source job delivered (its output's upstream id), which
        vibes edits only for the account that made it."""
        outs = self._source_outputs()
        ent = outs[0].get("upstream_id") if outs and isinstance(outs[0], dict) else None
        if not ent:
            raise self._source_gone("the source job left no vibes image id")
        return str(ent)

    def edited(self, resp: httpx.Response) -> SubmitResult:
        """What the ONE image-edit POST's answer means. Never `Retry` or `NotTaken`. The edited item is the result;
        anything unclear is `Ambiguous`, which this mode's plan fails at once with its refund (nothing to read back)."""
        body = json_of(resp)
        if is_checkpoint(resp):
            _, cooldown = checkpoint_rest(self.ctx, self.settings, self.rests, "vibes security checkpoint")
            return _result(Ambiguous("vibes' security checkpoint answered the image edit"), cooldown)
        if resp.status_code == 200 and body.get("success") is True:
            item = body.get("contentItem")
            if not isinstance(item, dict) or not item.get("id"):
                return _result(Ambiguous("vibes answered the image edit with no item"))
            if item.get("error") or not has_media(item):
                return _result(Fail(errors.CONTENT_REFUSED, "vibes refused the edit: %s" % str(
                    item.get("error") or "it ended with no media")[:200]), Success())
            out = Output(str(item["id"]), OUTPUT_MIME["image"], 0, str(item.get("mediaEntId") or "") or None,
                         {"batch": str(body.get("batchId") or "")})
            return _result(Completed((out,)), Success())
        refused = (resp.status_code == 200 and body.get("success") is False) or (
            400 <= resp.status_code < 500 and resp.status_code not in NOT_A_REFUSAL and body)
        if refused:
            said = body.get("error") or body.get("message") or "no reason given"
            return _result(Fail(errors.GENERATE_REFUSED, "vibes refused the image edit (%d): %s"
                                % (resp.status_code, str(said)[:300])))
        if resp.status_code in SIGNED_OUT_STATUSES:     # unclear for the job, but a doubt about the account
            return _result(Ambiguous("vibes answered %d to the image edit" % resp.status_code), *doubt_effects(
                "not signed in: vibes answered %d to the image edit" % resp.status_code, self.now()))
        return _result(Ambiguous("vibes answered %d to the image edit" % resp.status_code))

    def generate_body(self, batch: str, prompt: str, config: dict, frame: dict | None) -> dict:
        if self.kind == "image":
            item = {"type": "variation", "image_prompt": prompt, "original_prompt": prompt, "config": {}}
        else:
            item = {"type": "prompt", "value": prompt, "original_prompt": prompt, "config": {}}
        body = {"batchId": batch, "inputs": [item], "config": config, "mg_request_id": "www-%s" % uuid.uuid4()}
        if frame is not None:
            body["directPromptImageHandle"] = frame
        return body

    def answer(self, resp: httpx.Response, batch: str) -> SubmitResult:
        """What the ONE generate POST's answer means. Never `Retry` or `NotTaken`: the job is sent."""
        body = json_of(resp)
        if is_checkpoint(resp):
            _, cooldown = checkpoint_rest(self.ctx, self.settings, self.rests, "vibes security checkpoint")
            return _result(Ambiguous("vibes' security checkpoint answered the generate"), cooldown)
        if resp.status_code == 200 and body.get("success") is True:
            done = body.get("updatedBatch")
            if self.kind == "image" and isinstance(done, dict) and is_done(done) and outputs(done, self.kind):
                why = render_error(done)
                if why is not None:     # finished, but vibes refused the item: nothing to deliver
                    return _result(Fail(*why, {BATCH_KEY: batch}), Success())
                return _result(Completed(outputs(done, self.kind)), Success())
            return _result(Accepted(batch, {BATCH_KEY: batch}, taken=True), Success())
        refused = (resp.status_code == 200 and body.get("success") is False) or (
            400 <= resp.status_code < 500 and resp.status_code not in NOT_A_REFUSAL and body)
        if refused:
            said = body.get("error") or body.get("message") or "no reason given"
            return _result(Fail(errors.GENERATE_REFUSED, "vibes refused the order (%d): %s"
                                % (resp.status_code, str(said)[:300]), {BATCH_KEY: batch}))
        return _result(Ambiguous("vibes answered %d to the generate" % resp.status_code))


async def submit(ctx, job, settings, rests: Rests, *, transport=None,
                 read_media: Callable[..., Awaitable[bytes]] = read_input_media,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` / `ctx.mark_sent` (a lost lease, a stopping worker) reach the
    core unchanged. `transport`, `read_media` and `now` are seams for tests."""
    # the core's framed text; a job queued before vibes had frames carries the core frame's (the words and the
    # tracking code) and goes as it went then: the bare words
    prompt = job.prompt if job.frame_id is None else job.sent_text
    if job.operation == "edit_image":
        return await _edit_image(ctx, job, settings, rests, transport, now, prompt)
    config = _config(job.params)
    async with client_for(ctx.account, transport, rests, ctx=ctx) as client:
        run = _Run(ctx, job, settings, rests, client, now)
        source_input = None
        try:
            frame = await run.start_frame(read_media)
            batch_prompt = prompt
            if job.operation in VIDEO_SOURCE_OPS:
                batch_prompt, config, source_input = run.source_video(*await run.source_item(), prompt)
            batch = await run.create_batch(batch_prompt, config)
        except _Stop as stop:
            return stop.result
        except Resting as e:
            return _result(Retry(THROTTLED), Cooldown("vibes security checkpoint",
                                                      now() + timedelta(seconds=e.left_s), recheck=True))
        except httpx.HTTPError as e:
            # the text may carry the proxy URL: only its kind goes out
            why = "vibes.ai could not be reached (%s)" % type(e).__name__
            return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
        body = run.generate_body(batch, prompt, config, frame)
        if source_input is not None:
            body.update(inputs=[source_input], projectId=None)
        await ctx.report_ref(batch, {BATCH_KEY: batch})     # write-ahead: the batch id is on the job first
        await ctx.mark_sent()                               # then marked sent, immediately before the POST
        try:
            resp = await client.post(GENERATE_PATH[run.kind], json=body, timeout=GENERATE_TIMEOUT_S)
        except httpx.HTTPError as e:
            return _result(Ambiguous("the generate got no answer (%s)" % type(e).__name__))
        except Resting:
            # another job met the checkpoint between the batch create and now: the generate was not made, but the job
            # is marked sent, and a sent job is never sent again
            return _result(Ambiguous("the account began a checkpoint rest before the generate; it was not sent"))
        return run.answer(resp, batch)


async def _edit_image(ctx, job, settings, rests: Rests, transport, now: Callable[[], datetime],
                      prompt: str) -> SubmitResult:
    """An image edit: the source image's id, `mark_sent`, then the ONE edit POST. There is no batch to write ahead."""
    async with client_for(ctx.account, transport, rests, ctx=ctx) as client:
        run = _Run(ctx, job, settings, rests, client, now)
        try:
            source = run.source_image()
        except _Stop as stop:
            return stop.result
        left = rests.left_s(ctx.account.id)
        if left:        # nothing is sent while the account rests after a checkpoint: an ordinary retry, not a failure
            return _result(Retry(THROTTLED), Cooldown("vibes security checkpoint",
                                                      now() + timedelta(seconds=left), recheck=True))
        await ctx.mark_sent()                               # immediately before the POST
        try:
            resp = await client.post(IMAGE_EDIT_PATH, json={"sourceImageEntId": source, "editPrompt": prompt,
                                                            "projectId": None}, timeout=GENERATE_TIMEOUT_S)
        except httpx.HTTPError as e:
            return _result(Ambiguous("the image edit got no answer (%s)" % type(e).__name__))
        except Resting:
            return _result(Ambiguous("the account began a checkpoint rest before the image edit; it was not sent"))
        return run.edited(resp)
