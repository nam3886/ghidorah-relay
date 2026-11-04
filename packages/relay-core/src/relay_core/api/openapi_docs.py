"""The public OpenAPI schema: what a client may call, and nothing of the admin tree.

FastAPI's own schema lists every route (admin included) and cannot see the create bodies, which the doors parse by
hand so that an unknown field is always `input.invalid`. `public_schema(app)` starts from FastAPI's schema, drops the
admin paths and the schemas only they used, declares the four request bodies explicitly, the typed answers of the
four native job calls (documentation only: `api/schemas.py`), the error bodies, the request-time error codes of each
operation by HTTP status, the security schemes, an introduction (`info.description`), the webhook, a tag and an
English description per operation, and a few example answers. Text only: nothing here changes what a route does.
The result is served at `/openapi.json`, where `app.py` adds `servers` from the `public_address` setting (else the
request's own address) and writes the `non_stream_hold_s` setting in place of `HOLD_MARK` (`live`), on each request.
The failure codes it lists are the neutral public codes (`errors.PUBLIC_CODES`),
never an internal code.
"""

import copy
import json
from typing import Any

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from pydantic import BaseModel

from relay_core import errors
from relay_core.api.errors_http import is_facade_path
from relay_core.api.routes_gemini import GenerateBody
from relay_core.api.routes_native import ImageBody, VideoBody
from relay_core.api.routes_openai import ChatBody
from relay_core.api.schemas import JobCreated, JobStatus

ADMIN_PREFIX = "/admin"
_REF = "#/components/schemas/"

_ERROR = {"type": "object", "required": ["error"], "properties": {"error": {
    "type": "object", "required": ["code", "message", "type", "param"], "description": "What went wrong.",
    "properties": {
        "code": {"type": "string", "description": "Stable machine-readable code, such as input.bad_model. Act on it."},
        "message": {"type": "string", "description": "Fixed English text of the code, for a person to read."},
        "type": {"type": "string", "description": "OpenAI-style error class, such as invalid_request_error."},
        "param": {"type": ["string", "null"], "description": "The request field the error is about, else null."}}}},
    "examples": [{"error": {"code": "input.bad_model", "message": "the model does not exist or is not available",
                            "type": "invalid_request_error", "param": "model"}}]}
_GEMINI_ERROR = {"type": "object", "required": ["error"], "properties": {"error": {
    "type": "object", "required": ["code", "message", "status", "details"], "description": "What went wrong.",
    "properties": {
        "code": {"type": "integer", "description": "The HTTP status of the answer."},
        "message": {"type": "string", "description": "Fixed English text of the error, for a person to read."},
        "status": {"type": "string", "description": "Google RPC status name, such as INVALID_ARGUMENT."},
        "details": {"type": "array", "items": {"type": "object"}, "description": (
            "One google.rpc.ErrorInfo: its reason is the error code (as on the other routes) and its metadata.param "
            "the request field, when there is one.")}}}},
    "examples": [{"error": {"code": 400, "message": "the prompt is empty", "status": "INVALID_ARGUMENT", "details": [
        {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "input.empty_prompt", "domain": "relay",
         "metadata": {"param": "contents"}}]}}]}
_SECURITY = {
    "BearerAuth": {"type": "http", "scheme": "bearer", "description": "Authorization: Bearer <API key>. Every route."},
    "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key",
                     "description": "The same API key in X-API-Key. Every route."},
    "GoogleApiKeyHeader": {"type": "apiKey", "in": "header", "name": "x-goog-api-key", "description": (
        "The same API key as the Gemini SDK sends it. Every route.")},
}
_KEYLESS = {"/healthz", "/m/{token}"}

# request bodies the handlers parse themselves: (method, path) -> model
_BODIES: dict[tuple[str, str], type[BaseModel]] = {
    ("post", "/v1/video/generations"): VideoBody,
    ("post", "/v1/images/generations"): ImageBody,
    ("post", "/v1/chat/completions"): ChatBody,
    **{("post", "%s/models/{model}:%s" % (pre, verb)): GenerateBody
       for pre in ("/v1beta", "") for verb in ("generateContent", "streamGenerateContent")},
}

# answers the handlers return as plain dicts: (method, path) -> the model that documents the 200 answer
_RESPONSES: dict[tuple[str, str], type[BaseModel]] = {
    ("post", "/v1/video/generations"): JobCreated,
    ("post", "/v1/images/generations"): JobCreated,
    ("get", "/v1/video/generations/{job_id}"): JobStatus,
    ("get", "/v1/images/generations/{job_id}"): JobStatus,
}

_POLL_TEXT = "Poll every 10 to 30 seconds until status is done or failed. progress, render_started_at and " \
             "estimated_finish_at are estimates from recent render times of this model and length, not the " \
             "provider's own report."
_RENDERS = "Rendering continues after the call returns; poll the status call, or pass callback_url."
_CHARGE = "Charged at creation, at the price of the model's option (`prices` in GET /v1/models)."
_IDEMPOTENT = " Send an Idempotency-Key header to make a retry safe: the same key with the same body returns the " \
              "same job and charges nothing; with another body it is refused 409. A key is at most 200 characters, " \
              "belongs to your API key on every endpoint, and is never forgotten."
# `live()` writes the `non_stream_hold_s` setting in place of HOLD_MARK each time /openapi.json is served.
HOLD_MARK = "{non_stream_hold_s}"
_HOLDS = " The request waits at most " + HOLD_MARK + " seconds from its start. If the job fails while it waits, the " \
         "answer is an error with the job's code and the HTTP status of that failure (422, 500, 502, 503 or " \
         "504): act on the code, not the status; the job is refunded. If the wait ends first, the answer is the " \
         "poll hint and the job runs on (see Chat and Gemini routes in the introduction)."
_STREAMS = " The stream sends a heartbeat every 5 seconds while the job runs, then the result or the error (after " \
           "30 minutes, the poll hint)."
_SUFFIX = " Only the model and the prompt are read: a length or a ratio goes in the model name, for a model that " \
          "takes it (see Chat and Gemini routes in the introduction)."

_DOCS: dict[tuple[str, str], tuple[str, str]] = {
    ("post", "/v1/video/generations"): (
        "Native", "Create a video job. " + _CHARGE + " Returns the job id at once. " + _RENDERS + _IDEMPOTENT),
    ("get", "/v1/video/generations/{job_id}"): (
        "Native", "Read a video job (only the key that created it). " + _POLL_TEXT + " When done, result_urls holds "
                  "the files; when failed, error_code says why and refunded is true."),
    ("post", "/v1/images/generations"): (
        "Native", "Create an image job. " + _CHARGE + " Returns the job id at once. " + _RENDERS + _IDEMPOTENT),
    ("get", "/v1/images/generations/{job_id}"): (
        "Native", "Read an image job (only the key that created it). " + _POLL_TEXT + " When done, images holds the "
                  "files; when failed, error_code says why and refunded is true."),
    ("get", "/v1/video/generations/{job_id}/content"): (
        "Native", "Download the job's first video (only the key that created it). Supports Range."),
    ("get", "/v1/video/generations/{job_id}/content/{idx}"): (
        "Native", "Download the job's video number idx, from 0 (only the key that created it). Supports Range."),
    ("get", "/v1/images/generations/{job_id}/content/{idx}"): (
        "Native", "Download the job's image number idx, from 0 (only the key that created it). Supports Range."),
    ("post", "/v1/chat/completions"): (
        "OpenAI", "OpenAI-style chat completion: creates a job, then holds or streams until it ends or the wait "
                  "limit passes. Only model, messages and stream are accepted." + _SUFFIX + " The answer's content is "
                  "the result links, one per line; job_id is the job, readable with the status call of its kind. "
                  + _CHARGE + _HOLDS + _STREAMS + " A stream reports a failed job as an error event, with status "
                  "200." + _IDEMPOTENT),
    ("get", "/v1/models"): ("Catalog", "Models this key may use: kind (video or image), limits (the durations, "
                                       "ratios, counts and inputs a model takes) and prices (points per option; "
                                       "null means the option cannot be ordered yet). A paused model is left out; a "
                                       "request for it is refused 403 model.disabled. Read this list rather than "
                                       "keep your own: models and prices change."),
    ("get", "/v1/models/aliases"): ("Catalog", "Model aliases this key may use, each with the model it runs as "
                                               "base_model."),
    ("get", "/v1beta/models"): ("Catalog", "Models in the Gemini list shape."),
    ("get", "/models"): ("Catalog", "Models in the Gemini list shape."),
    ("get", "/v1beta/models/{model}"): ("Catalog", "One model in the Gemini shape."),
    ("get", "/models/{model}"): ("Catalog", "One model in the Gemini shape."),
    ("get", "/v1/providers/{provider}/options"): (
        "Catalog", "Choices a provider offers for creation requests beyond the model limits, such as named styles; "
                   "provider is a model's owned_by. The shape is the provider's own; an empty object means none."),
    ("get", "/v1/points"): ("Usage", "This key's points: the day and total limits and what is used of them (points), "
                                     "the cap of each provider on top of it (providers), points_left, spent_today, "
                                     "the models it may use, and its unfinished jobs against max_unfinished_jobs. A "
                                     "limit of null is unlimited, 0 is closed."),
    ("get", "/m/{token}"): ("Media", "Download an output or input file by its share token, the link a job's "
                                     "result_urls or images give. No key: the link is the credential, so keep it "
                                     "private. A link to an input file stops working 7 days after it was made; a link "
                                     "to an output file does not expire unless it is revoked. Supports Range."),
    ("get", "/healthz"): ("Service", "Liveness probe."),
}
for _pre in ("/v1beta", ""):
    _DOCS[("post", "%s/models/{model}:generateContent" % _pre)] = (
        "Gemini", "Gemini-style generateContent: creates a job and holds until it ends or the wait limit passes."
                  + _SUFFIX + " The answer's text part is the result links, one per line; responseId is the job id. "
                  + _CHARGE + _HOLDS + _IDEMPOTENT)
    _DOCS[("post", "%s/models/{model}:streamGenerateContent" % _pre)] = (
        "Gemini", "Gemini-style streaming generateContent (server-sent events; alt is ignored)." + _SUFFIX + " "
                  + _CHARGE + _STREAMS + " A failed job is an error event, with status 200." + _IDEMPOTENT)

_TAGS = [
    {"name": "Native", "description": "Create video and image jobs, read them, download their files."},
    {"name": "OpenAI", "description": "The chat completions shape, for OpenAI SDKs."},
    {"name": "Gemini", "description": "The generateContent shape, for Gemini SDKs."},
    {"name": "Catalog", "description": "The models, options and prices this key may use."},
    {"name": "Usage", "description": "This key's limits and spend."},
    {"name": "Media", "description": "Files by share link."},
    {"name": "Service", "description": "Health."},
]

INTRO = """\
Make videos and images. Every create call makes a **job**: you get its id at once and read the result when it ends.

## Authentication
Every route except `/healthz` and `/m/{token}` needs an API key, sent as `Authorization: Bearer <key>`,
`X-API-Key: <key>` or `x-goog-api-key: <key>` (as the Gemini SDK sends it); each header works on every route. A key in
the query string is never read. A missing or unknown key is 401 `auth.invalid_key`; a disabled one is 403
`auth.key_disabled`.

## Models and options
Do not hard-code models or prices: read `GET /v1/models`. The model ids in this reference (`example-video`,
`example-image`) are examples only. Each entry has its `kind`, its `limits` (durations, ratios, styles, how many input
images and outputs, the longest prompt) and its `prices` in points per option. Some models add their own words around
the prompt, so their longest accepted prompt can be a little shorter than `max_prompt_chars`. A model's provider is
its `owned_by`; `GET /v1/providers/{provider}/options` lists that provider's extra choices.

## Job lifecycle
`queued` (waiting for capacity) → `running` (see `stage`) → `done` or `failed`. A running job can go back to `queued`
when it has to wait again; it is not charged again. Treat a `status` or `stage` you do not know as still running.

`done` is final. `failed` is final for you, with one exception: an operator may check a failed, refunded job again
(a result can arrive late). The job then shows `running` again and is charged again; it ends `done`, or `failed` and
refunded again. With a `callback_url`, that new end sends a new webhook event with a new `event_id`.

## Polling and webhooks
Poll `GET /v1/video/generations/{id}` or `GET /v1/images/generations/{id}` every 10 to 30 seconds. Or pass
`callback_url` (https, a public address): when the job ends, relay POSTs
`{"event": "job.done" | "job.failed", "event_id", "job_id", "status"}` to it, with the headers `X-Relay-Event` and
`X-Relay-Event-Id`. A 2xx answer is a delivery; a 4xx answer ends it; anything else is tried again: a 3xx (redirects
are not followed), a 5xx, or no answer within 10 seconds. There are three tries in all: the second 30 seconds after
the first, the third 300 seconds after the second. **The webhook is not signed**: take it as a hint and read the job
with your key before you act. The same event can come more than once with the same `event_id`: drop repeats.

## Chat and Gemini routes
`POST /v1/chat/completions` and the Gemini `generateContent` routes read only the model and the prompt (with its
images). For a model that takes a length or a ratio, put them in the model name as suffixes after a dash, in any
order: `seedance-2.5-30s-16:9` is 30 seconds at 16:9, and an image model takes a ratio and a style, as in
`seedream-4.5-9:16-anime`. A suffix left out is the model's default; one the model does not take is refused 400
(`input.bad_model`, `input.bad_duration`, `input.bad_ratio` or `input.bad_style`). The native create calls refuse a
name with suffixes (`input.bad_model`): send `duration`, `ratio` and `style` as fields there.

A request without streaming waits for its job at most {non_stream_hold_s} seconds, counted from the start of the
request. A job done by then gives the result links (finish_reason `stop`; Gemini finishReason `STOP`). Otherwise the
answer is the **poll hint**: the content is `still rendering — poll job <id>`, with finish_reason `length` (Gemini
finishReason `MAX_TOKENS`). It means the job is not finished: it runs on and stays charged. Take `job_id` (Gemini
`responseId`) and poll `GET /v1/video/generations/{id}` or `GET /v1/images/generations/{id}`. A stream ends the same
way after 30 minutes. A job that fails while the request waits answers with its error: the body's code is the job's
code, and the HTTP status is the one of that failure (422, 500, 502, 503 or 504). Act on the body's `code`, not on the
status. A stream reports the failure as an error event instead, with status 200.

A stream sends a heartbeat every 5 seconds while the job runs: a chunk whose `delta.reasoning_content` says how long
it has waited, or on the Gemini routes a part marked `"thought": true`. Do not show it as the result. Then comes one
last event (the result, the poll hint or the error) and, on `/v1/chat/completions`, `data: [DONE]`.

Sending again: with no `Idempotency-Key`, the same request sent again by an SDK on its own (it carries
`x-stainless-retry-count` of 1 or more, as the OpenAI SDKs send) within 30 minutes gets the same job, and nothing is
charged. A request you send again yourself makes a new job, charged again: send an `Idempotency-Key` to make that safe.

## Request size
The public address is behind Cloudflare, which refuses a request body over 100 MB with its own 413 page. The relay
itself refuses a body over 170 MB with 400 `input.invalid` and no `param`. A request takes at most 10 input images, each
at most 12 MiB. An image URL must load within 20 seconds, and all the URLs of one request within 45 seconds together;
otherwise the first image not loaded is refused `input.image_unreadable` and no job is made. Base64 makes an image a
third larger: for many or large images, send URLs rather than data URIs.

## Points
A job is charged when it is created, at the price of its model option (`prices` in `GET /v1/models`). A job that
delivers any output is `done` and keeps the whole charge. A job that delivers nothing is `failed` and its charge is
given back: its status shows `refunded: true`. `GET /v1/points` shows the key's limits and spend.

## Limits
Two limits apply to a key; both refuse a create call with 429 and charge nothing.
- **Unfinished jobs**: a key holds at most `max_unfinished_jobs` jobs that are not yet `done` or `failed`
  (`GET /v1/points` shows it beside `unfinished_jobs`; null is no cap). One more is refused `quota.too_many_queued`.
  Send it again once one of your jobs ends.
- **Points**: the key's points have a day limit and a total limit, and each provider can have its own cap on top
  (`points` and `providers` in `GET /v1/points`; null is unlimited, 0 is closed). A job whose price does not fit is
  refused `quota.key_exceeded`. The day limit starts again each day at midnight in Asia/Ho_Chi_Minh time (UTC+7);
  the total limit does not.

There is no per-second request limit, and no `Retry-After` or rate-limit header: read `GET /v1/points` to see what is
left.

## Errors
Every error the relay sends has the body `{"error": {"code", "message", "type", "param"}}`; the Gemini routes use the
Gemini error body, with the code in `details[0].reason`. Act on `code`; `message` is fixed text for a person. Each
operation lists the codes it can refuse a request with, by HTTP status; nothing is charged on any of them. A failed
job's code is its `error_code`, one of a small fixed set listed in the job status: `content_refused`,
`generation_failed`, `temporarily_unavailable`, `timeout`, `invalid_input` and `internal_error`. Its `error` says what
happened and whether sending the request again can help. A held or streamed request on `/v1/chat/completions` or the
Gemini routes whose job fails answers with the same code (see Chat and Gemini routes). On those routes an error answer
carries `x-should-retry: false`, so an SDK does not send the request (and make a job) a second time.

An answer that is not JSON (an HTML page with a 413, a 5xx or a 524) comes from Cloudflare in front of the relay, not
from the relay. A 413 means the body is too large (see Request size). A 502, 503 or 524 means the relay could not be
reached or did not answer within 100 seconds: a job may have been made, so send the request again with the same
`Idempotency-Key`.

## Server address
`servers` holds the relay's public address (its `public_address` setting), else the address this reference was read
from.

## Changes
- **2026-11-04, 2026.11.04**: a request to `/v1/chat/completions` or `generateContent` without streaming waits at most
  {non_stream_hold_s} seconds from its start (it was always 540), then gives the poll hint while the job runs on.
  The reference now documents the poll hint, the HTTP status of a failed held request, model name suffixes, the
  re-send rules, request size, link expiry, webhook tries and a re-checked job.
- **2026-10-30, 2026.10.30**: a model option the service cannot serve at all for now (an option of a paid plan while
  the service runs none) is refused at once, 403 `model.unavailable`, nothing charged, instead of waiting queued until
  it fails `temporarily_unavailable`.
- **2026-10-26, 2026.10.26.2**: error codes are neutral. A failed job, and a failed held or streamed facade request,
  now give one of six codes that say what it means for you, not which part of the service failed:
  `content_refused` (send it changed), `generation_failed` (sending again may work), `temporarily_unavailable` (send it
  again later), `timeout`, `invalid_input` (change the input) and `internal_error`; each comes with a sentence that says
  whether sending again can help. Codes that named a provider's or the service's own machinery are no longer shown. The
  request-time codes (`input.*`, `auth.*`, `model.*`, `quota.*`, `resource.*`) are unchanged.
- **2026-10-26**: `source_job_id` and `source_index` work on an earlier job's output; the job status lists every code
  a job can end with; the reference documents the limits, the response headers of the file downloads, the server
  address and an operationId per call.
"""

API_TITLE = "Ghidorah Relay API"
API_VERSION = "2026.11.04"   # bumped by hand with each entry of ## Changes above

# Request-time refusals, by operation. The HTTP status of each code is its rule's (`relay_core.errors`).
_KEY = ("auth.invalid_key", "auth.key_disabled")
_FACADE_CREATE = _KEY + (
    "input.invalid", "input.empty_prompt", "input.prompt_too_long", "input.too_many_images", "input.image_unreadable",
    "input.bad_model", "input.bad_duration", "input.bad_ratio", "input.bad_style", "input.unsupported",
    "input.prompt_ratio_conflict", "input.prompt_duration_conflict", "input.too_costly", "auth.model_forbidden",
    "model.disabled", "model.unpriced", "model.uncosted", "model.unavailable", "input.idempotency_conflict",
    "quota.key_exceeded", "quota.too_many_queued", "auth.permission_unknown")
_NATIVE_CREATE = _FACADE_CREATE + ("input.bad_source", "input.source_unsupported")
_NOT_FOUND = _KEY + ("resource.not_found",)
_ERRORS: dict[tuple[str, str], tuple[str, ...]] = {
    **{(m, p): _NOT_FOUND for m, p in _DOCS if m == "get" and p.startswith(("/v1/video/", "/v1/images/"))},
    ("post", "/v1/video/generations"): _NATIVE_CREATE,
    ("post", "/v1/images/generations"): _NATIVE_CREATE,
    ("post", "/v1/chat/completions"): _FACADE_CREATE,
    **{(m, p): _FACADE_CREATE for m, p in _DOCS if ":" in p},
    ("get", "/v1/models"): _KEY,
    ("get", "/v1/models/aliases"): _KEY,
    ("get", "/v1beta/models"): _KEY,
    ("get", "/models"): _KEY,
    ("get", "/v1beta/models/{model}"): _NOT_FOUND,
    ("get", "/models/{model}"): _NOT_FOUND,
    ("get", "/v1/providers/{provider}/options"): _NOT_FOUND,
    ("get", "/v1/points"): _KEY,
    ("get", "/m/{token}"): ("resource.not_found",),
}

_JOB_ID = "6f0c3c1e-7a58-4b8e-9d57-0a2d1f0e5a11"
_LINK = "https://relay.example.com/m/Qk9yX2V4YW1wbGU"
# Example answers of the routes that return plain dicts with no model (`_RESPONSES` documents the job calls).
_EXAMPLES: dict[tuple[str, str], dict] = {
    ("post", "/v1/chat/completions"): {
        "id": "chatcmpl-" + _JOB_ID, "object": "chat.completion", "created": 1792310400, "model": "example-video",
        "job_id": _JOB_ID, "choices": [{"index": 0, "message": {"role": "assistant", "content": _LINK},
                                        "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
    ("get", "/v1/models"): {"object": "list", "data": [{
        "id": "example-video", "name": "Example Video", "object": "model", "owned_by": "example-provider",
        "kind": "video", "operations": ["text_to_video", "image_to_video"], "aliases": [],
        "limits": {"durations": [5, 10], "default_duration": 10, "ratios": ["16:9", "9:16"], "max_images": 1,
                   "max_prompt_chars": 4000},
        "prices": {"5s": 4, "10s": 8}, "is_alias": False}]},
    ("get", "/v1/points"): {
        "points": {"day_limit": 1000, "used_day": 120, "total_limit": None, "used_total": 4520}, "providers": {},
        "models": ["*"], "unfinished_jobs": 1, "max_unfinished_jobs": 10, "name": "my app",
        "points_left": {"day": 880, "total": None}, "spent_today": 120},
}
for _pre in ("/v1beta", ""):
    _EXAMPLES[("post", "%s/models/{model}:generateContent" % _pre)] = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": _LINK}]}, "index": 0, "finishReason": "STOP"}],
        "modelVersion": "example-image", "responseId": _JOB_ID}
# The facades' second 200 answer: the wait ended before the job (`facade_common.waiting_text`).
_HINT = "still rendering — poll job " + _JOB_ID
_PENDING: dict[tuple[str, str], dict] = {("post", "/v1/chat/completions"): {
    **_EXAMPLES[("post", "/v1/chat/completions")],
    "choices": [{"index": 0, "message": {"role": "assistant", "content": _HINT}, "finish_reason": "length"}]}}
for _pre in ("/v1beta", ""):
    _PENDING[("post", "%s/models/{model}:generateContent" % _pre)] = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": _HINT}]}, "index": 0,
                        "finishReason": "MAX_TOKENS"}],
        "modelVersion": "example-image", "responseId": _JOB_ID}

# A held facade request whose job failed answers with the HTTP status of the failure (`errors.http_status`).
_HELD_STATUSES = ("422", "500", "502", "503", "504")
_HELD_FAILED = ("A held request whose job failed: the body's code is the job's code (see the job status), and this "
                "status is the one of that failure. Act on the code, not the status. The job is refunded.")

# The POST a job's callback_url gets (`jobs/webhooks.py`), as an OpenAPI 3.1 webhook.
_WEBHOOK = {"post": {
    "summary": "Job ended", "tags": ["Native"],
    "description": "Sent to the job's callback_url when it reaches done or failed. Not signed: read the job with your "
                   "key before acting on it; for job.failed its error_code says why (listed under the job status). "
                   "Answer 2xx within 10 seconds; a repeat keeps the same event_id. Three tries in all, 30 and 300 "
                   "seconds apart; redirects are not followed, and a 3xx is tried again. A failed job that an operator "
                   "checks again can end once more: that end is a new event with a new event_id.",
    "parameters": [
        {"name": "X-Relay-Event", "in": "header", "required": True, "schema": {"type": "string"},
         "description": "job.done or job.failed, as event in the body."},
        {"name": "X-Relay-Event-Id", "in": "header", "required": True, "schema": {"type": "string"},
         "description": "The same as event_id in the body."}],
    "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["event", "event_id", "job_id", "status"], "properties": {
            "event": {"type": "string", "enum": ["job.done", "job.failed"], "description": "What happened to the job."},
            "event_id": {"type": "string", "description": "Stable id of this event across tries: drop repeats."},
            "job_id": {"type": "string", "description": "The job; read its status for the result."},
            "status": {"type": "string", "enum": ["done", "failed"], "description": (
                "The job's status when the event was made. A failed job may still turn done after an operator "
                "re-check, with a new event.")}},
        "examples": [{"event": "job.done", "event_id": _JOB_ID + ":1792310400120", "job_id": _JOB_ID,
                      "status": "done"}]}}}},
    "responses": {"200": {"description": "Any 2xx answer counts as delivered."}}}}


# The file downloads (`routes_media.respond`): whole, or one byte range.
_MEDIA_PATHS = {"/m/{token}", "/v1/video/generations/{job_id}/content",
                "/v1/video/generations/{job_id}/content/{idx}", "/v1/images/generations/{job_id}/content/{idx}"}
_RANGE_PARAM = {"name": "Range", "in": "header", "required": False, "schema": {"type": "string"},
                "example": "bytes=0-1048575",
                "description": "One byte range, such as bytes=0-1048575 or bytes=-500. Several ranges, or one that "
                               "cannot be read, get the whole file."}


def _header(text: str, example: str | None = None) -> dict:
    return {"description": text, "schema": {"type": "string", **({"example": example} if example else {})}}


_FILE_HEADERS = {
    "Content-Type": _header("The file's own type, such as video/mp4 or image/png.", "video/mp4"),
    "Content-Length": _header("Bytes in this answer.", "1048576"),
    "Accept-Ranges": _header("Always bytes: a Range request is honoured.", "bytes"),
    "Content-Disposition": _header("inline, with the file's download name."),
    "Cache-Control": _header("public, max-age=86400 for a job's output by share link; private, no-cache otherwise."),
    "X-Content-Type-Options": _header("Always nosniff.", "nosniff"),
}
_FILE = {"*/*": {"schema": {"type": "string", "format": "binary"}}}
_MEDIA_RESPONSES = {
    "200": {"description": "The whole file.", "headers": _FILE_HEADERS, "content": _FILE},
    "206": {"description": "Partial content: the one byte range asked for in Range.", "content": _FILE, "headers": {
        **_FILE_HEADERS, "Content-Range": _header("The range sent and the file's size.", "bytes 0-1048575/7340032")}},
    "416": {"description": "The range starts past the end of the file, or is bytes=-0. No body.", "headers": {
        "Content-Range": _header("bytes */ and the file's size.", "bytes */7340032")}},
}
# The facade routes (OpenAI, Gemini): an error answer tells the SDK not to send again (`errors_http.NO_RETRY`).
_NO_RETRY = {"x-should-retry": _header("Always false: an SDK must not send the request again by itself.", "false")}
_SSE = {"text/event-stream": {"schema": {"type": "string"}}}
_STREAM_NOTE = " A streamed answer is text/event-stream with Cache-Control: no-cache."

# operationId: the route function's name in camelCase. A function on two paths gets one name per path: the Gemini
# short paths (`/models/...`) end in Short, the video's first file has its own.
_OPERATION_IDS = {("get", "/v1/video/generations/{job_id}/content"): "videoFirstContent"}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def _operation_id(names: dict[tuple[str, str], str], method: str, path: str) -> str:
    if (method, path) in _OPERATION_IDS:
        return _OPERATION_IDS[(method, path)]
    return _camel(names[(method, path)]) + ("Short" if path.startswith("/models") else "")


def _failure_codes() -> str:
    """The codes a job can end with: the neutral public codes (every job-ending rule of the registry maps to one, a
    test holds it), each with its meaning and whether sending again helps. Never an internal code."""
    return "\n".join("- `%s`: %s Sending again helps: %s Points refunded." % (code, text, helps)
                     for code, (helps, text) in errors.PUBLIC_CODES.items())


def _error_responses(codes: tuple[str, ...], ref: str, headers: dict | None = None) -> dict:
    """`{status: response}` naming each code under its rule's HTTP status, in the order given."""
    by_status: dict[str, list[str]] = {}
    for code in codes:
        by_status.setdefault(str(errors.http_status(code)), []).append(code)
    out = {}
    for status, found in sorted(by_status.items()):
        text = "Codes: %s." % ", ".join("`%s`" % c for c in found)
        if status == "429":
            text += " See Limits in the introduction; no Retry-After header is sent."
        out[status] = {"description": text, "content": {"application/json": {"schema": {"$ref": ref}}}}
        if headers:
            out[status]["headers"] = headers
    return out


def _refs(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF):
            found.add(ref[len(_REF):])
        for v in node.values():
            _refs(v, found)
    elif isinstance(node, list):
        for v in node:
            _refs(v, found)


def _prune(schema: dict) -> None:
    """Drop component schemas no remaining path reaches (the admin bodies)."""
    comps = schema.get("components", {}).get("schemas", {})
    keep: set[str] = set()
    _refs(schema["paths"], keep)
    todo = list(keep)
    while todo:
        more: set[str] = set()
        _refs(comps.get(todo.pop(), {}), more)
        todo.extend(more - keep)
        keep |= more
    for name in [n for n in comps if n not in keep]:
        del comps[name]


def public_schema(app: FastAPI) -> dict:
    """The public-only schema of `app`. A new dict on every call."""
    schema = copy.deepcopy(app.openapi())
    schema["paths"] = {p: ops for p, ops in schema["paths"].items()
                       if p != ADMIN_PREFIX and not p.startswith(ADMIN_PREFIX + "/")}
    comps = schema.setdefault("components", {})
    schemas = comps.setdefault("schemas", {})
    schemas["Error"], schemas["GeminiError"] = copy.deepcopy(_ERROR), copy.deepcopy(_GEMINI_ERROR)
    for model in set(_BODIES.values()) | set(_RESPONSES.values()):
        body = model.model_json_schema(ref_template=_REF + "{model}")
        schemas.update(body.pop("$defs", {}))
        schemas[model.__name__] = body
        if model is JobStatus:
            error_code = body["properties"]["error_code"]
            error_code["description"] += " Every failed job is refunded (refunded is true). The codes a job can end " \
                "with:\n\n" + _failure_codes()
    names = {(m.lower(), c.path_format): c.name for c in iter_route_contexts(app.routes) for m in c.methods or ()}
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            if not isinstance(op, dict):
                continue
            tag, text = _DOCS.get((method, path), ("Other", ""))
            op["tags"], op["description"] = [tag], text or op.get("description", "")
            op["operationId"] = _operation_id(names, method, path)
            if path.startswith("/models"):
                op["description"] += " Identical to %s /v1beta%s." % (method.upper(), path)
            if (method, path) in _BODIES:
                op["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": {"$ref": _REF + _BODIES[(method, path)].__name__}}}}
            gemini = path.startswith(("/v1beta", "/models"))
            op.setdefault("responses", {}).pop("422", None)       # a bad request is a 400 with the error envelope
            if (method, path) in _RESPONSES:
                op["responses"]["200"] = {"description": "The job", "content": {"application/json": {
                    "schema": {"$ref": _REF + _RESPONSES[(method, path)].__name__}}}}
            if (method, path) in _EXAMPLES and "200" in op["responses"]:
                media = op["responses"]["200"].setdefault("content", {}).setdefault("application/json", {})
                if (method, path) in _PENDING:
                    media["examples"] = {
                        "done": {"summary": "The job is done: the result links",
                                 "value": copy.deepcopy(_EXAMPLES[(method, path)])},
                        "pollHint": {"summary": "The wait ended first: poll the job",
                                     "value": copy.deepcopy(_PENDING[(method, path)])}}
                else:
                    media["example"] = copy.deepcopy(_EXAMPLES[(method, path)])
            if path in _MEDIA_PATHS:
                op["responses"].update(copy.deepcopy(_MEDIA_RESPONSES))
                op.setdefault("parameters", []).append(copy.deepcopy(_RANGE_PARAM))
            if method == "post" and (path == "/v1/chat/completions" or path.endswith(":streamGenerateContent")):
                op["responses"]["200"].setdefault("content", {}).update(copy.deepcopy(_SSE))
                op["description"] += _STREAM_NOTE
            error_ref = _REF + ("GeminiError" if gemini else "Error")
            headers = copy.deepcopy(_NO_RETRY) if is_facade_path(path) else None
            op["responses"].update(_error_responses(_ERRORS.get((method, path), ()), error_ref, headers))
            if (method, path) in _PENDING:
                for status in _HELD_STATUSES:
                    answer = op["responses"].setdefault(status, {"description": "", "content": {
                        "application/json": {"schema": {"$ref": error_ref}}}, "headers": copy.deepcopy(_NO_RETRY)})
                    answer["description"] = (answer["description"] + " " + _HELD_FAILED).strip()
            op["responses"]["default"] = {
                "description": "Error", "content": {"application/json": {"schema": {"$ref": error_ref}}}}
            if headers:
                op["responses"]["default"]["headers"] = headers
            if path not in _KEYLESS:
                op["security"] = [{"BearerAuth": []}, {"ApiKeyHeader": []}, {"GoogleApiKeyHeader": []}]
    comps["securitySchemes"] = copy.deepcopy(_SECURITY)
    schema["info"].update(title=API_TITLE, version=API_VERSION, description=INTRO)
    schema["tags"] = copy.deepcopy(_TAGS)
    schema["webhooks"] = {"jobEnded": copy.deepcopy(_WEBHOOK)}
    _prune(schema)
    return schema


def live(schema: dict, hold_s: int) -> dict:
    """A copy of `schema` with the `non_stream_hold_s` setting written in place of HOLD_MARK, so the reference always
    states the wait the routes use."""
    return json.loads(json.dumps(schema).replace(HOLD_MARK, str(int(hold_s))))
