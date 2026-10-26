"""The native doors: create and read video and image jobs. Strict: an unknown field, an http
URL and the compatibility fields of older clients are refused or absent. Media routes live in `routes_media`."""

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ConfigDict, Field, StrictInt

from relay_core.api.deps import require_key
from relay_core.api.doors import (
    DoorRequest,
    Gate,
    ImageRef,
    Made,
    Strict,
    create_job_for,
    get_gate,
    job_status,
    public_base,
    read_json,
    validated,
)
from relay_core.api.errors_http import ApiError
from relay_core.api.routes_media import serve_job_media
from relay_core.keys import ApiKey

router = APIRouter()


class _Common(Strict):
    model: str = Field(description=(
        "Model id or alias, from GET /v1/models: a video model on the video call, an image model on the image call."))
    prompt: str = Field(description=(
        "What to make. Trimmed, never blank, at most the model's `limits.max_prompt_chars` characters."))
    ratio: str | None = Field(default=None, description=(
        "Aspect ratio, such as 16:9, from the model's `limits.ratios`. Omit it for the model's default."))
    n: StrictInt | None = Field(default=None, description=(
        "How many outputs to make, for a model that makes several (see its `limits`). Omit it for the default."))
    image: str | None = Field(default=None, description=(
        "One input image: an https URL or a base64 data URI. JPEG, PNG or WebP, at most 12 MiB."))
    images: list[str] | None = Field(default=None, description=(
        "Input images, each as in `image`. At most 10 together with `image`; how many a model takes is its "
        "`limits.max_images`."))
    options: dict[str, Any] | None = Field(default=None, description=(
        "Model-specific settings (a mode, a resolution). What a model takes is in its `limits` in GET /v1/models and "
        "in GET /v1/providers/{provider}/options, where provider is the model's `owned_by`. At most 16 KiB as JSON."))
    title: str | None = Field(default=None, description=(
        "Your name for the job, one line of at most 200 characters. The status shows it back as `title`."))
    callback_url: str | None = Field(default=None, description=(
        "An https URL that gets a POST when the job ends (see Polling and webhooks in the introduction). Checked at "
        "creation: a URL that is not https or not a public address is refused `input.invalid`."))
    source_job_id: str | None = Field(default=None, description=(
        "Id of an earlier finished job of this API key whose output this request works on (an extend, an edit). Only "
        "for a model that takes a source (its `limits.source` names the kind of output it needs). The job is made "
        "where the source was made, and fails at once, refunded, when it can no longer be made there."))
    source_index: StrictInt | None = Field(default=None, description=(
        "Which output of the source job to work on, from 0 (the default) for a job that made several. Only with "
        "source_job_id."))


class VideoBody(_Common):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "model": "example-video", "prompt": "A red kite over a beach at sunset, slow pan", "duration": 5,
        "ratio": "16:9", "title": "Kite", "callback_url": "https://example.com/hooks/relay"}]})

    duration: StrictInt | None = Field(default=None, description=(
        "Length in seconds, from the model's `limits.durations`. Omit it for the model's default."))


class ImageBody(_Common):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "model": "example-image", "prompt": "A watercolor fox in the snow", "ratio": "1:1",
        "images": ["https://example.com/fox.jpg"]}]})

    style: str | None = Field(default=None, description=(
        "Image style, for a model with styles: its `limits.styles`, or the styles of GET "
        "/v1/providers/{provider}/options."))


def _refs(body: _Common) -> tuple[ImageRef, ...]:
    refs = [ImageRef(body.image, "image")] if body.image else []
    return tuple(refs + [ImageRef(u, "images.%d" % i) for i, u in enumerate(body.images or [])])


def created_body(made: Made) -> dict:
    return {"id": str(made.job_id), "status": made.status, "queue_position": made.queue_position or None,
            "warnings": made.warnings}


async def _create(request: Request, gate: Gate, key: ApiKey, req: DoorRequest) -> dict:
    made = await create_job_for(gate, key, req)
    request.state.job_id, request.state.provider = made.job_id, made.provider
    return created_body(made)


@router.post("/v1/video/generations")
async def create_video(request: Request, key: ApiKey = Depends(require_key), gate: Gate = Depends(get_gate),
                       idempotency_key: str | None = Header(default=None)):
    b = validated(VideoBody, await read_json(request))
    return await _create(request, gate, key, DoorRequest(
        "video", "native", b.model, b.prompt, duration=b.duration, ratio=b.ratio, n=b.n, images=_refs(b),
        options=b.options or {}, title=b.title, callback_url=b.callback_url, idem_key=idempotency_key,
        media_type="video", source_job_id=b.source_job_id, source_index=b.source_index))


@router.post("/v1/images/generations")
async def create_image(request: Request, key: ApiKey = Depends(require_key), gate: Gate = Depends(get_gate),
                       idempotency_key: str | None = Header(default=None)):
    b = validated(ImageBody, await read_json(request))
    # The provider-neutral request has no style field: it rides in `options`, named as sent.
    extra = {"style": b.style} if b.style is not None else {}
    return await _create(request, gate, key, DoorRequest(
        "image", "native", b.model, b.prompt, ratio=b.ratio, n=b.n, images=_refs(b),
        options={**(b.options or {}), **extra}, title=b.title, callback_url=b.callback_url,
        idem_key=idempotency_key, media_type="image", source_job_id=b.source_job_id,
        source_index=b.source_index))


@router.get("/v1/video/generations/{job_id}")
async def video_status(job_id: str, request: Request, key: ApiKey = Depends(require_key),
                       gate: Gate = Depends(get_gate)):
    return await job_status(gate.pool, public_base(request), key.id, job_id, media_type="video", estimate=True,
                            registry=gate.registry)


@router.get("/v1/images/generations/{job_id}")
async def image_status(job_id: str, request: Request, key: ApiKey = Depends(require_key),
                       gate: Gate = Depends(get_gate)):
    return await job_status(gate.pool, public_base(request), key.id, job_id, media_type="image", estimate=True,
                            registry=gate.registry)


# ---- content: the job's output files through the API ----------------------------------------------
async def _content(request: Request, key: ApiKey, job_id: str, idx: int, media_type: str):
    if idx < 0:
        raise ApiError("resource.not_found")
    return await serve_job_media(request, job_id, "output", idx, key_id=key.id, media_type=media_type)


@router.get("/v1/video/generations/{job_id}/content")
@router.get("/v1/video/generations/{job_id}/content/{idx}")
async def video_content(job_id: str, request: Request, idx: int = 0, key: ApiKey = Depends(require_key)):
    return await _content(request, key, job_id, idx, "video")


@router.get("/v1/images/generations/{job_id}/content/{idx}")
async def image_content(job_id: str, idx: int, request: Request, key: ApiKey = Depends(require_key)):
    return await _content(request, key, job_id, idx, "image")
