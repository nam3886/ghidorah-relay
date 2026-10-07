"""The one creation path every public door uses (contracts 7.3, 8, 9; D12, D27) and the status view of a job.

    request (native, OpenAI or Gemini)
      -> `DoorRequest`
      -> `plan_job`: resolve the model, the key's allow-list, the provider's snapshots, `spec.plan()`
      -> input checks and media uploads (all foreign I/O, before any transaction)
      -> `store.create_job`: idempotency, cap, charge, job row, input rows, `queued` event (one transaction)

Every input check runs before the charge. A door never charges, caps or inserts on its own.

For the admin's Regenerate (lane C): call `plan_job(gate, allowed_models, CreateRequest(...))` to get today's
`Plan` through the same model resolution, allow-list, snapshots and error mapping a client request gets, then pass
that plan to `regenerate_job`. `Gate.of(request)` builds the gate from the app.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, ValidationError

from relay_core import billing, errors, keys, net
from relay_core.api.errors_http import ApiError, field_param, validation_code
from relay_core.jobs.create import IdempotencyConflict, InputMedia, NewJob, TooManyQueued, body_hash, create_job
from relay_core.keys import ApiKey
from relay_core.providers import CreateRequest, InputError, MediaIn, ModelSpec, Plan, ProviderRegistry, ProviderSpec
from relay_core.settings import Settings
from relay_core.storage import ObjectStore, media_key

log = logging.getLogger("relay.api")

MAX_PROMPT = 50_000
MAX_IMAGES = 10
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_TITLE = 200
MAX_IDEM_KEY = 200
MAX_OPTIONS_BYTES = 16 * 1024    # the client's options, as JSON: kept for good in jobs.request (D56)
RETRY_WINDOW_S = 30 * 60                # a facade retry with no Idempotency-Key finds its job this far back
MAX_BODY_BYTES = 170 * 1024 * 1024      # ten images of 12 MiB as base64, plus the text
# Foreign fetches of client image URLs go through `net`; a test passes `transport` / `resolver` here.
NET_OPTIONS: dict[str, Any] = {"max_bytes": MAX_IMAGE_BYTES, "timeout_s": 20.0}

_DATA_URI = re.compile(r"data:[^,;]*(?:;[^,;]*)*;base64,(.*)", re.DOTALL)
# The request field a plan() refusal is about when the provider names none, by the reason part of its code.
_PARAM_OF = {"bad_duration": "duration", "bad_ratio": "ratio", "bad_model": "model", "bad_style": "style",
             "empty_prompt": "prompt", "prompt_too_long": "prompt"}
_PUBLIC_STATUS = {"queued": ("queued", None), "submitting": ("running", "submitting"),
                  "waiting": ("running", "waiting"), "fetching": ("running", "fetching"),
                  "unknown": ("running", "unknown"), "done": ("done", None), "failed": ("failed", None)}
TERMINAL = ("done", "failed")


class Strict(BaseModel):
    """Base of every request body: an unknown field is refused (D27)."""
    model_config = ConfigDict(extra="forbid")


# ---- the gate -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Gate:
    """What creation needs from the process: the app's pool, settings, object store and provider registry."""
    pool: AsyncConnectionPool
    settings: Settings
    store: ObjectStore
    registry: ProviderRegistry

    @classmethod
    def of(cls, request: Request) -> "Gate":
        s = request.app.state
        return cls(s.pool, s.settings, s.store, s.registry)


def get_gate(request: Request) -> Gate:
    return Gate.of(request)


@dataclass(frozen=True)
class ImageRef:
    """An input image as the client sent it: a data URI or an https URL. `param` names the field for errors;
    `tolerant` (Gemini) accepts url-safe, unpadded base64."""
    ref: str
    param: str
    tolerant: bool = False


@dataclass(frozen=True)
class DoorRequest:
    """A request of any door, after its own field parsing. `endpoint` keeps the idempotency hash of one door apart
    from another's; `media_type` (native doors) is the kind the route serves."""
    endpoint: str
    door: Literal["native", "facade"]
    model: str
    prompt: str
    duration: int | None = None
    ratio: str | None = None
    n: int | None = None
    images: tuple[ImageRef, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)
    title: str | None = None
    callback_url: str | None = None
    idem_key: str | None = None
    media_type: str | None = None
    retry_count: int = 0       # a facade's `x-stainless-retry-count`: how many times the SDK re-sent the request


@dataclass(frozen=True)
class Made:
    """What a door answers: the job and where it stands. `replayed`: an earlier request made it, nothing charged."""
    job_id: uuid.UUID
    provider: str
    status: str            # the public status
    queue_position: int
    warnings: list[dict]
    replayed: bool


# ---- body parsing -------------------------------------------------------------------------------------------------
async def read_json(request: Request) -> Any:
    """The JSON body, read with a size ceiling. Anything that is not JSON is `input.invalid` with no field."""
    size, chunks = 0, []
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ApiError("input.invalid")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except ValueError:
        raise ApiError("input.invalid") from None


def _field_path(loc: tuple, kind: str) -> tuple:
    """`loc` without pydantic's union labels: `content, list[tagged-union[..]], 0, text, cache_control` is the field
    `content.0.cache_control` (the label and the tag after the index are not request fields); a part whose `type` is
    unknown is about `type`."""
    out, i = [], 0
    while i < len(loc):
        if isinstance(loc[i], str) and "[" in loc[i]:             # a union branch label
            if i + 1 < len(loc):
                out.append(loc[i + 1])                           # the index
            i += 3                                               # label, index, tag
            continue
        out.append(loc[i])
        i += 1
    return (*out, "type") if kind.startswith("union_tag") else tuple(out)


def validated[M: BaseModel](cls: type[M], data: Any) -> M:
    """`cls` from a parsed body. An unknown field is `input.invalid` naming it (D27, whatever the field is called);
    any other bad field gets the code of its field. Never echoes the value."""
    if not isinstance(data, dict):
        raise ApiError("input.invalid")
    try:
        return cls.model_validate(data)
    except ValidationError as e:
        found = e.errors(include_url=False, include_context=False, include_input=False)
        # A `str | list[...]` field reports every branch, `str` first: the `str` complaint is never the real one
        # when the list branch got further.
        first = dict(next((x for x in found if (x.get("loc") or ("",))[-1] != "str"), found[0]))
        param = field_param(_field_path(tuple(first.get("loc") or ()), str(first.get("type"))), str(first.get("type")))
        code = "input.invalid" if first.get("type") == "extra_forbidden" else validation_code(first)
        raise ApiError(code, param) from None


# ---- model, allow-list, plan --------------------------------------------------------------------------------------
def resolve_model(registry: ProviderRegistry, requested: str) -> tuple[ProviderSpec, ModelSpec]:
    """The provider and catalog entry for a public name: an exact id or alias, else the longest catalog name that
    the request extends with `-...` (a variant such as `base-5s`); the provider's `plan()` judges the variant."""
    hit = registry.resolve(requested)
    if hit is None:
        names = {n: registry.resolve(n) for s in registry.names() for m in registry.spec(s).catalog()
                 for n in m.names()}
        base = max((n for n in names if requested.startswith(n + "-")), key=len, default=None)
        hit = names[base] if base else None
    if hit is None:
        raise ApiError("input.bad_model", "model")
    return hit


def _check_allowed(registry: ProviderRegistry, allowed: Sequence[str], requested: str, model: ModelSpec) -> None:
    try:
        catalog = {n: m.id for s in registry.names() for m in registry.spec(s).catalog() for n in m.names()}
        catalog.setdefault(requested, model.id)      # a variant belongs to its base model
        ok = keys.model_allowed(allowed, requested, catalog)
    except Exception:
        log.exception("model permission lookup failed")
        raise ApiError("auth.permission_unknown") from None   # fail closed
    if not ok:
        raise ApiError("auth.model_forbidden", "model")


async def snapshots_of(pool: AsyncConnectionPool, provider: str) -> dict[str, dict]:
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT name, data FROM provider_snapshots WHERE provider = %s",
                                         (provider,))).fetchall()
    return {name: data for name, data in rows}


def _refusal(exc: Exception) -> ApiError | None:
    """A `plan()` refusal: an `InputError` carrying a registered platform code (and optionally `param`)."""
    if isinstance(exc, InputError) and exc.code != errors.INTERNAL and errors.rule(exc.code).code == exc.code:
        return ApiError(exc.code, exc.param or _PARAM_OF.get(exc.code.partition(".")[2]))
    return None


async def plan_job(gate: Gate, allowed_models: Sequence[str], creq: CreateRequest) -> tuple[ProviderSpec, Plan]:
    """Model -> provider, the key's allow-list, the provider's snapshots, `spec.plan()`. Raises ApiError with the
    refusal's own code; touches nothing."""
    spec, model = resolve_model(gate.registry, creq.model)
    _check_allowed(gate.registry, allowed_models, creq.model, model)
    snapshots = await snapshots_of(gate.pool, spec.name)
    try:
        return spec, spec.plan(creq, gate.settings, snapshots)
    except Exception as e:
        refusal = _refusal(e)
        if refusal is None:
            raise
        raise refusal from None


# ---- inputs -------------------------------------------------------------------------------------------------------
def decode_b64(text: str, *, tolerant: bool) -> bytes:
    """Strict base64. `tolerant` first maps the url-safe alphabet and adds the padding (the google-genai SDK
    sends url-safe)."""
    s = text.strip()
    if tolerant:
        s = s.replace("-", "+").replace("_", "/")
        s += "=" * (-len(s) % 4)
    return base64.b64decode(s, validate=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_image(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def load_image(ref: ImageRef) -> tuple[bytes, str]:
    """(bytes, mime) of one input image. The bytes decide the type, never the declared one. https only, SSRF-checked
    for a URL (D27)."""
    if ref.ref.startswith("data:"):
        m = _DATA_URI.fullmatch(ref.ref)
        if m is None or len(m.group(1)) > MAX_IMAGE_BYTES * 4 // 3 + 16:
            raise ApiError("input.image_unreadable", ref.param)
        try:
            data = await asyncio.to_thread(decode_b64, m.group(1), tolerant=ref.tolerant)
        except (binascii.Error, ValueError):
            raise ApiError("input.image_unreadable", ref.param) from None
    else:
        try:
            r = await net.safe_get(ref.ref, **NET_OPTIONS)
        except net.UnsafeUrl:
            raise ApiError("input.invalid", ref.param) from None
        except net.FetchFailed:
            raise ApiError("input.image_unreadable", ref.param) from None
        if r.status_code != 200:
            raise ApiError("input.image_unreadable", ref.param)
        data = r.content
    mime = sniff_image(data)
    if not data or len(data) > MAX_IMAGE_BYTES or mime is None:
        raise ApiError("input.image_unreadable", ref.param)
    return data, mime


async def check_callback(url: str) -> None:
    try:
        await net.check_url(url, NET_OPTIONS.get("resolver", net.system_resolver))
    except net.UnsafeUrl:
        raise ApiError("input.invalid", "callback_url") from None


def _check_text(req: DoorRequest) -> str:
    prompt = req.prompt.strip()
    if not prompt:
        raise ApiError("input.empty_prompt", "prompt")
    if len(prompt) > MAX_PROMPT:
        raise ApiError("input.prompt_too_long", "prompt")
    if req.title is not None and (len(req.title) > MAX_TITLE or any(c in req.title for c in "\r\n")):
        raise ApiError("input.invalid", "title")
    if req.idem_key is not None and (not req.idem_key or len(req.idem_key) > MAX_IDEM_KEY
                                     or not req.idem_key.isprintable()):
        raise ApiError("input.invalid", "Idempotency-Key")
    if len(req.images) > MAX_IMAGES:
        raise ApiError("input.too_many_images", "images")
    if len(json.dumps(req.options, separators=(",", ":"), default=str).encode()) > MAX_OPTIONS_BYTES:
        raise ApiError("input.invalid", "options")
    return prompt


# ---- creation -----------------------------------------------------------------------------------------------------
async def create_job_for(gate: Gate, key: ApiKey, req: DoorRequest) -> Made:
    """Create the job of a request, or return the one its idempotency key already made (I3). Every door calls this
    and nothing else. Errors are ApiError with the rule's code: 400 input, 403 model, 409 idempotency conflict,
    429 cap or wallet, 503 unreadable permissions; nothing is charged on any of them.

    The request hash and both lookups (the idempotency key's, and a facade retry's) come before anything foreign is
    touched: an image URL that no longer downloads, or a callback host that is gone, cannot turn a replay into an
    error. Images are hashed as the client sent them (the reference text), not as downloaded."""
    prompt = _check_text(req)
    request_hash = body_hash({
        "endpoint": req.endpoint, "model": req.model, "prompt": prompt, "duration": req.duration,
        "ratio": req.ratio, "n": req.n, "options": dict(req.options), "title": req.title,
        "callback_url": req.callback_url,
        "images": [await asyncio.to_thread(_sha256, r.ref.encode()) for r in req.images]})
    idem_hash = None if req.idem_key is None else request_hash
    if req.idem_key is not None:                 # first, before the plan, the allow-list and any upload (7.3)
        replay = await _replay_of(gate.pool, key.id, req.idem_key, idem_hash)
        if replay is not None:
            return replay
    elif req.door == "facade" and req.retry_count >= 1:
        retried = await _retry_of(gate.pool, key.id, request_hash)
        if retried is not None:
            return retried
    if req.callback_url is not None:
        await check_callback(req.callback_url)
    loaded = [(ref, *await load_image(ref)) for ref in req.images]
    media = tuple([MediaIn(await asyncio.to_thread(_sha256, data), mime, len(data)) for _, data, mime in loaded])
    creq = CreateRequest(model=req.model, prompt=prompt, duration=req.duration, ratio=req.ratio, n=req.n,
                         images=media, options=dict(req.options), title=req.title, callback_url=req.callback_url,
                         door=req.door)
    spec, plan = await plan_job(gate, key.models, creq)
    if req.media_type is not None and plan.media_type != req.media_type:
        raise ApiError("input.bad_model", "model")     # an image model on the video door, or the other way round

    job_id = uuid.uuid4()
    inputs = []
    for idx, (_, data, mime) in enumerate(loaded):
        object_key = media_key(str(job_id), "input", idx, mime)
        await gate.store.put_bytes(object_key, data, mime)     # an orphan if the creation fails; swept later
        inputs.append(InputMedia(idx, object_key, mime, len(data)))

    sent = {"endpoint": req.endpoint, "model": req.model, "duration": req.duration, "ratio": req.ratio, "n": req.n,
            "options": dict(req.options), "title": req.title, "callback_url": req.callback_url, "prompt": prompt,
            "images": [{"input": i.idx} for i in inputs]}
    new = NewJob(spec.name, key.id, plan, prompt, title=req.title, callback_url=req.callback_url,
                 idem_key=req.idem_key, idem_body_hash=idem_hash, inputs=tuple(inputs), job_id=job_id,
                 request=sent, request_hash=request_hash)
    try:
        created = await create_job(gate.pool, new, gate.settings)
    except (IdempotencyConflict, TooManyQueued, billing.BillingError) as refused:
        raise ApiError(refused.code) from None
    warnings = [{"code": w.code, "text": w.text} for w in plan.warnings]
    if created.replayed and req.idem_key is not None:     # a race: another request won between lookup and insert
        replay = await _replay_of(gate.pool, key.id, req.idem_key, idem_hash)
        if replay is not None:
            return replay
    return Made(created.job_id, spec.name, _PUBLIC_STATUS[created.status][0], created.queue_position, warnings,
                created.replayed)


async def _replay_of(pool: AsyncConnectionPool, key_id, idem_key: str, body_hash_: str | None) -> Made | None:
    """The job this key's idempotency key already made, as the answer a replay gets; None when there is none, 409
    for the same key with another body. A read: `create_job` still decides a race inside its transaction."""
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT id, idem_body_hash, status, provider, warnings FROM jobs WHERE key_id = %s AND idem_key = %s",
            (key_id, idem_key))).fetchone()
    if row is None:
        return None
    if row[1] != body_hash_:
        raise ApiError("input.idempotency_conflict")
    job_id, _, status, provider, warnings = row
    return await _made_of(pool, key_id, job_id, status, provider, warnings)


async def _made_of(pool: AsyncConnectionPool, key_id, job_id, status: str, provider: str, warnings) -> Made:
    position = 0
    if status == "queued":
        position = (await job_status(pool, "", key_id, str(job_id)))["queue_position"] or 0
    return Made(job_id, provider, _PUBLIC_STATUS[status][0], position, warnings, True)


async def _retry_of(pool: AsyncConnectionPool, key_id, request_hash: str) -> Made | None:
    """The newest job of this key made from the same request within RETRY_WINDOW_S: an SDK that re-sent a request
    after a dropped connection (it sends no Idempotency-Key) is answered with it; nothing is made or charged."""
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT id, status, provider, warnings FROM jobs WHERE key_id = %s AND request_hash = %s "
            "AND created_at > clock_timestamp() - make_interval(secs => %s) ORDER BY created_at DESC, id DESC LIMIT 1",
            (key_id, request_hash, RETRY_WINDOW_S))).fetchone()
    return None if row is None else await _made_of(pool, key_id, row[0], row[1], row[2], row[3])


# ---- status -------------------------------------------------------------------------------------------------------
def public_base(request: Request) -> str:
    return (request.app.state.public_base_url or str(request.base_url)).rstrip("/")


_STATUS = ("SELECT j.status, j.error_code, j.model, j.params, j.title, j.warnings, j.created_at, j.finished_at, "
           "j.media_type, EXISTS (SELECT 1 FROM ledger l WHERE l.job_id = j.id AND l.entry = 'refund') "
           "FROM jobs j WHERE j.id = %s AND j.key_id = %s")


async def job_status(pool: AsyncConnectionPool, base_url: str, key_id, job_id: str, *,
                     media_type: str | None = None) -> dict:
    """The status body of a job the key owns (contracts 8). 404 `resource.not_found` for any other job, a malformed
    id, or (when given) a job of the other media type. `error` is the rule's message of the job's code, masked
    for a non-public code: never engine or upstream text."""
    try:
        wanted = uuid.UUID(job_id)
    except ValueError:
        raise ApiError("resource.not_found") from None
    async with pool.connection() as conn:
        row = await (await conn.execute(_STATUS, (wanted, key_id))).fetchone()
        if row is None or (media_type is not None and row[8] != media_type):
            raise ApiError("resource.not_found")
        status, code, model, params, title, warnings, created, finished, kind, refunded = row
        outputs = await (await conn.execute(
            "SELECT share_token FROM job_media WHERE job_id = %s AND role = 'output' AND share_token IS NOT NULL "
            "ORDER BY idx", (wanted,))).fetchall() if status == "done" else []
        position = (await (await conn.execute(
            "SELECT count(*) FROM jobs j, jobs me WHERE me.id = %s AND j.provider = me.provider "
            "AND j.status = 'queued' AND j.pool IS NOT DISTINCT FROM me.pool "
            "AND (j.created_at, j.id) <= (me.created_at, me.id)", (wanted,))).fetchone())[0] \
            if status == "queued" else None
    public, stage = _PUBLIC_STATUS[status]
    urls = ["%s/m/%s" % (base_url, token) for (token,) in outputs]
    shown_code, message = errors.shown(code or errors.INTERNAL) if status == "failed" else (None, None)
    return {"id": str(wanted), "status": public, "stage": stage,
            "result_urls": urls if kind == "video" else [], "images": urls if kind == "image" else [],
            "error": message, "error_code": shown_code, "refunded": bool(refunded) and status == "failed",
            "model": model, "duration": params.get("duration"), "ratio": params.get("ratio"), "n": params.get("n"),
            "style": params.get("style"), "title": title, "warnings": warnings, "queue_position": position,
            "created_at": created.isoformat(), "finished_at": finished.isoformat() if finished else None}


async def job_state(pool: AsyncConnectionPool, job_id) -> tuple[str, str | None] | None:
    """(db status, error code) of a job: what a held request polls."""
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT status, error_code FROM jobs WHERE id = %s", (job_id,))).fetchone()
    return None if row is None else (row[0], row[1])
