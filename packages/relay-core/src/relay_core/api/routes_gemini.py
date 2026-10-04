"""The Gemini facade (contracts 8): `:generateContent` and `:streamGenerateContent` under `/v1beta/models/…` and
`/models/…`.

Strict (D27): only `contents` and `systemInstruction` are accepted (plus the empty `generationConfig` the SDK sends
with a system instruction); any other field, `generationConfig` with content included, is 400 `input.invalid` naming
it. `alt` is ignored: a stream is always server-sent events. The prompt is the last `user` content's text parts
(the `systemInstruction` text goes first); `inlineData` image parts and `fileData` https image URIs are the inputs.
Base64 may be url-safe and unpadded, and is then decoded strictly. Errors have the Gemini body (wave 1), also
inside a stream.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AliasChoices, Field

from relay_core.api import facade_common as fc
from relay_core.api.deps import require_key
from relay_core.api.doors import (
    DoorRequest,
    Gate,
    ImageRef,
    Strict,
    create_job_for,
    get_gate,
    public_base,
    read_json,
    validated,
)
from relay_core.api.errors_http import ApiError, gemini_body
from relay_core.keys import ApiKey

router = APIRouter()


class _Inline(Strict):
    """The SDK writes `mime_type` here where the REST docs say `mimeType`: both are accepted."""
    data: str
    mime_type: str = Field(validation_alias=AliasChoices("mimeType", "mime_type"))


class _File(Strict):
    file_uri: str = Field(validation_alias=AliasChoices("fileUri", "file_uri"))
    mime_type: str | None = Field(default=None, validation_alias=AliasChoices("mimeType", "mime_type"))


class _Part(Strict):
    text: str | None = None
    inline_data: _Inline | None = Field(default=None, validation_alias="inlineData")
    file_data: _File | None = Field(default=None, validation_alias="fileData")


class _Content(Strict):
    role: Literal["user", "model"] | None = None
    parts: list[_Part]


class GenerateBody(Strict):
    contents: list[_Content]
    system_instruction: _Content | None = Field(default=None, validation_alias="systemInstruction")
    generation_config: dict[str, Any] | None = Field(default=None, validation_alias="generationConfig")


def _text(content: _Content | None) -> str:
    return "\n".join(t for p in (content.parts if content else []) if p.text and (t := p.text.strip()))


def extract(body: GenerateBody) -> tuple[str, tuple[ImageRef, ...]]:
    if body.generation_config:                    # `{}` is what the SDK sends beside a systemInstruction
        raise ApiError("input.invalid", "generationConfig")
    if not body.contents:
        raise ApiError("input.empty_prompt", "contents")
    at = max((i for i, c in enumerate(body.contents) if (c.role or "user") == "user"), default=len(body.contents) - 1)
    refs = []
    for i, part in enumerate(body.contents[at].parts):
        where = "contents.%d.parts.%d" % (at, i)
        if part.inline_data is not None:
            if not part.inline_data.mime_type.lower().startswith("image/"):
                raise ApiError("input.invalid", where + ".inlineData.mimeType")
            refs.append(ImageRef("data:%s;base64,%s" % (part.inline_data.mime_type, part.inline_data.data),
                                 where + ".inlineData.data", tolerant=True))
        elif part.file_data is not None:
            mime = (part.file_data.mime_type or "").lower()
            if mime and not mime.startswith("image/"):
                raise ApiError("input.invalid", where + ".fileData.mimeType")
            refs.append(ImageRef(part.file_data.file_uri, where + ".fileData.fileUri"))
    prompt, system = _text(body.contents[at]), _text(body.system_instruction)
    return (system + "\n\n" + prompt).strip() if system else prompt, tuple(refs)


def _reply(model: str, job_id, text: str, finish: str | None, *, thought: bool = False) -> dict:
    part: dict[str, Any] = {"text": text}
    if thought:
        part["thought"] = True
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": [part]}, "index": 0}
    if finish:
        candidate["finishReason"] = finish
    return {"candidates": [candidate], "modelVersion": model, "responseId": str(job_id)}


async def _stream(gate: Gate, base_url: str, key_id, job_id, model: str) -> AsyncIterator[str]:
    async for kind, value in fc.watch(gate.pool, job_id):
        if kind == "beat":
            yield fc.sse(_reply(model, job_id, fc.heartbeat_text(value), None, thought=True))
        elif value is not None and value[0] == "failed":
            yield fc.sse(gemini_body(fc.failure_code(value)))
        else:
            text, finished = await fc.result_text(gate.pool, base_url, key_id, job_id, value)
            yield fc.sse(_reply(model, job_id, text, "STOP" if finished else "MAX_TOKENS"))


async def _generate(request: Request, model: str, key: ApiKey, gate: Gate, idem: str | None, retries: str | None,
                    *, stream: bool):
    body = validated(GenerateBody, await read_json(request))
    prompt, refs = extract(body)
    made = await create_job_for(gate, key, DoorRequest("gemini", "facade", model, prompt, images=refs, idem_key=idem,
                                                       retry_count=fc.retries_of(retries)))
    request.state.job_id, request.state.provider = made.job_id, made.provider
    base_url = public_base(request)
    if stream:
        return StreamingResponse(_stream(gate, base_url, key.id, made.job_id, model), media_type="text/event-stream",
                                 headers=fc.SSE_HEADERS)
    state = await fc.hold(gate.pool, made.job_id, request.is_disconnected)
    if state is not None and state[0] == "failed":
        raise ApiError(fc.failure_code(state))
    text, finished = await fc.result_text(gate.pool, base_url, key.id, made.job_id, state)
    return JSONResponse(_reply(model, made.job_id, text, "STOP" if finished else "MAX_TOKENS"))


@router.post("/v1beta/models/{model}:generateContent")
@router.post("/models/{model}:generateContent")
async def generate_content(model: str, request: Request, key: ApiKey = Depends(require_key),
                           gate: Gate = Depends(get_gate), idempotency_key: str | None = Header(default=None),
                           x_stainless_retry_count: str | None = Header(default=None)):
    return await _generate(request, model, key, gate, idempotency_key, x_stainless_retry_count, stream=False)


@router.post("/v1beta/models/{model}:streamGenerateContent")
@router.post("/models/{model}:streamGenerateContent")
async def stream_generate_content(model: str, request: Request, key: ApiKey = Depends(require_key),
                                  gate: Gate = Depends(get_gate), idempotency_key: str | None = Header(default=None),
                                  x_stainless_retry_count: str | None = Header(default=None)):
    return await _generate(request, model, key, gate, idempotency_key, x_stainless_retry_count, stream=True)
