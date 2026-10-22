"""The OpenAI facade: `POST /v1/chat/completions`, with and without `stream`.

Strict: only `model`, `messages` and `stream` are accepted; anything else is 400 `input.invalid` naming the
field. The prompt is the last message; `image_url` parts are its input images. The job is created before anything
is streamed, so a refusal is a real HTTP error. A held request or a stream that ends early never cancels the job.
"""

import time
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from relay_core import errors
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
from relay_core.api.errors_http import ApiError
from relay_core.keys import ApiKey

router = APIRouter()


class _ImageUrl(Strict):
    url: str
    detail: str | None = None


class _Text(Strict):
    type: Literal["text"]
    text: str


class _Image(Strict):
    type: Literal["image_url"]
    image_url: _ImageUrl


class _Message(Strict):
    role: str
    content: str | list[Annotated[_Text | _Image, Field(discriminator="type")]] | None = None


class ChatBody(Strict):
    model: str
    messages: list[_Message]
    stream: bool = False


def extract(body: ChatBody) -> tuple[str, tuple[ImageRef, ...]]:
    """The last message's text (parts joined by a newline) and its images."""
    if not body.messages:
        raise ApiError("input.empty_prompt", "messages")
    last, at = body.messages[-1], len(body.messages) - 1
    if isinstance(last.content, str):
        return last.content, ()
    texts, refs = [], []
    for i, part in enumerate(last.content or []):
        if isinstance(part, _Text):
            texts.append(part.text.strip())
        else:
            refs.append(ImageRef(part.image_url.url.strip(), "messages.%d.content.%d.image_url.url" % (at, i)))
    return "\n".join(t for t in texts if t), tuple(refs)


def _chunk(cid: str, model: str, delta: dict, finish: str | None = None) -> str:
    return fc.sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


async def _stream(gate: Gate, base_url: str, key_id, job_id, model: str) -> AsyncIterator[str]:
    cid = "chatcmpl-%s" % job_id
    yield _chunk(cid, model, {"role": "assistant"})
    async for kind, value in fc.watch(gate.pool, job_id):
        if kind == "beat":
            yield _chunk(cid, model, {"reasoning_content": fc.heartbeat_text(value)})
            continue
        if value is not None and value[0] == "failed":
            yield fc.sse(errors.envelope(fc.failure_code(value)))
        else:
            text, finished = await fc.result_text(gate.pool, base_url, key_id, job_id, value)
            yield _chunk(cid, model, {"content": text}, "stop" if finished else "length")
    yield "data: [DONE]\n\n"


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, key: ApiKey = Depends(require_key), gate: Gate = Depends(get_gate),
                           idempotency_key: str | None = Header(default=None),
                           x_stainless_retry_count: str | None = Header(default=None)):
    body = validated(ChatBody, await read_json(request))
    prompt, refs = extract(body)
    made = await create_job_for(gate, key, DoorRequest(
        "openai", "facade", body.model, prompt, images=refs, idem_key=idempotency_key,
        retry_count=fc.retries_of(x_stainless_retry_count)))
    request.state.job_id, request.state.provider = made.job_id, made.provider
    base_url = public_base(request)
    if body.stream:
        return StreamingResponse(_stream(gate, base_url, key.id, made.job_id, body.model),
                                 media_type="text/event-stream", headers=fc.SSE_HEADERS)
    state = await fc.hold(gate.pool, made.job_id, request.is_disconnected)
    if state is not None and state[0] == "failed":
        raise ApiError(fc.failure_code(state))
    text, finished = await fc.result_text(gate.pool, base_url, key.id, made.job_id, state)
    return JSONResponse({
        "id": "chatcmpl-%s" % made.job_id, "object": "chat.completion", "created": int(time.time()),
        "model": body.model, "job_id": str(made.job_id),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop" if finished else "length"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
