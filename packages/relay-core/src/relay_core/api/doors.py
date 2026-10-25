"""The one creation path every public door uses and the status view of a job.

    request (native, OpenAI or Gemini)
      -> `DoorRequest`
      -> `plan_job`: resolve the model, the key's allow-list, the paused models, the provider's snapshots,
         `spec.plan()`, the prompt frame, the core's price and upstream cost (`relay_core.pricing`)
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
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from fastapi import Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, ValidationError

from relay_core import billing, errors, frames, keys, net, pricing
from relay_core.api import progress
from relay_core.api.errors_http import ApiError, field_param, validation_code
from relay_core.jobs.create import (
    FrameUnusable,
    IdempotencyConflict,
    InputMedia,
    NewJob,
    SourceUnusable,
    TooManyQueued,
    body_hash,
    create_job,
    marker_for,
)
from relay_core.jobs.titles import SHOWN_TITLE
from relay_core.keys import ApiKey
from relay_core.providers import (
    CreateRequest,
    FrameUse,
    InputError,
    MediaIn,
    ModelSpec,
    Plan,
    ProviderRegistry,
    ProviderSpec,
    SourceIn,
)
from relay_core.settings import Settings
from relay_core.storage import ObjectStore, media_key

log = logging.getLogger("relay.api")

MAX_IMAGES = 10
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_TITLE = 200
MAX_IDEM_KEY = 200
MAX_OPTIONS_BYTES = 16 * 1024    # the client's options, as JSON: kept for good in jobs.request
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
    """Base of every request body: an unknown field is refused."""
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
    source_job_id: str | None = None   # an earlier job of this key whose output the request works on (native doors)


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
    """`cls` from a parsed body. An unknown field is `input.invalid` naming it (whatever the field is called);
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


def model_disabled(settings: Any, spec: ProviderSpec, model: ModelSpec) -> bool:
    """The admin paused the model for every key (`<p>.disabled_models`, catalog ids; a variant or alias is its base)."""
    return model.id in settings.get(spec.name + ".disabled_models")


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


def _plan(gate: Gate, spec: ProviderSpec, creq: CreateRequest, snapshots: Mapping[str, dict]
          ) -> tuple[ProviderSpec, Plan]:
    """`spec.plan()` with a refusal turned into the ApiError of its own code."""
    try:
        return spec, spec.plan(creq, gate.settings, snapshots)
    except Exception as e:
        refusal = _refusal(e)
        if refusal is None:
            raise
        raise refusal from None


SAMPLE_JOB_ID = uuid.UUID(int=0)      # the job id whose marker renders the length check and the admin preview


def frame_unusable(spec: ProviderSpec, model: ModelSpec) -> ApiError:
    """500 `internal.frame_unusable`, logged at ERROR with what is missing: a provider with frames and no usable frame
    for the model is ours to fix, and nothing falls back to the core frame."""
    log.error("no usable prompt frame: provider=%s kind=%s model=%s", spec.name, model.media_type, model.id)
    return ApiError("internal.frame_unusable")


async def frame_of(pool: AsyncConnectionPool, spec: ProviderSpec, model: ModelSpec) -> frames.Effective | None:
    """The model's effective stored frame (one read); None for a provider without frames or without a usable row."""
    if getattr(spec, "frames", None) is None:
        return None
    async with pool.connection() as conn:
        return await frames.effective(conn, spec.name, model.media_type, model.id)


def plan_request(gate: Gate, spec: ProviderSpec, model: ModelSpec, creq: CreateRequest,
                 snapshots: Mapping[str, dict], frame: FrameUse | None) -> tuple[ProviderSpec, Plan, str]:
    """The one path from a request to the text it will send: `spec.plan()`, then the frame attached, checked against
    the grammar again and against the provider's length cap with the sample marker (every marker has four digits, so
    the length is exact). `frame` is the stored frame, or the admin's draft for a preview. Returns the plan with its
    frame and the rendered text. A provider without frames gets the core frame; one with frames and no usable `frame`
    is 500 `internal.frame_unusable`."""
    spec, plan = _plan(gate, spec, creq, snapshots)
    rules = getattr(spec, "frames", None)
    if rules is None:
        use = FrameUse(None, frames.CORE_FRAME)
    else:
        try:
            use = None if frame is None else FrameUse(frame.id, frames.check_frame(frame.text), rules.verbatim)
        except frames.FrameError:
            use = None
        if use is None:
            raise frame_unusable(spec, model)
    text = frames.render(use.text, creq.prompt, marker_for(SAMPLE_JOB_ID), use.verbatim)
    if rules is not None and len(text) > rules.max_sent_chars:
        raise ApiError("input.prompt_too_long", "prompt")
    return spec, replace(plan, frame=use), text


async def plan_job(gate: Gate, allowed_models: Sequence[str], creq: CreateRequest) -> tuple[ProviderSpec, Plan]:
    """Model -> provider, the key's allow-list, the paused models, the provider's snapshots, `spec.plan()`, the prompt
    frame, then the core's price and upstream cost (`pricing.price_plan`: an option with no price or no cost, or one
    no account can afford, is refused). Raises ApiError with the refusal's own code; touches nothing."""
    spec, model = resolve_model(gate.registry, creq.model)
    _check_allowed(gate.registry, allowed_models, creq.model, model)
    if model_disabled(gate.settings, spec, model):
        raise ApiError("model.disabled", "model")
    if creq.source is not None and creq.source.provider != spec.name:
        raise ApiError("input.bad_source", "source_job_id")     # another provider's output: no account of ours owns it
    snapshots = await snapshots_of(gate.pool, spec.name)
    stored = await frame_of(gate.pool, spec, model)
    use = None if stored is None else FrameUse(stored.version.id, stored.version.text)
    spec, plan, _ = plan_request(gate, spec, model, creq, snapshots, use)
    if creq.source is not None and not plan.uses_source:
        raise ApiError("input.source_unsupported", "source_job_id")   # never run a source job on another account
    if plan.uses_source and creq.source is None:
        raise ApiError("input.invalid", "source_job_id")       # the provider should have refused it itself
    try:
        return spec, pricing.price_plan(spec, plan, gate.settings)
    except pricing.PricingRefused as refused:
        raise ApiError(refused.code, "model") from None


# The source of a pinned job: a finished job of the same key that ran on an account. The provider, the plan and the
# creation transaction judge the rest (same provider, a mode that takes a source, still finished at the charge).
_SOURCE = ("SELECT provider, model, media_type, operation FROM jobs WHERE id = %s AND key_id = %s AND status = 'done' "
           "AND account_id IS NOT NULL")


async def source_of(pool: AsyncConnectionPool, key_id, job_id: str) -> tuple[uuid.UUID, SourceIn]:
    """The job a request names in `source_job_id`, or 400 `input.bad_source`: not an id, another key's job, a job that
    is not done. Another key's job and a missing one answer alike, so a key learns nothing of other keys' jobs."""
    try:
        wanted = uuid.UUID(job_id)
    except (ValueError, AttributeError, TypeError):
        raise ApiError("input.bad_source", "source_job_id") from None
    async with pool.connection() as conn:
        row = await (await conn.execute(_SOURCE, (wanted, key_id))).fetchone()
    if row is None:
        raise ApiError("input.bad_source", "source_job_id")
    return wanted, SourceIn(str(wanted), *row)


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
    for a URL."""
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


def prompt_cap(registry: ProviderRegistry, requested: str) -> int:
    """The prompt cap of the model's provider. An unknown model gets the largest cap of any provider: `plan_job`, which
    runs later, still answers it `input.bad_model`, and a prompt no provider takes is refused first."""
    try:
        return resolve_model(registry, requested)[0].max_prompt_chars
    except ApiError:
        return max((registry.spec(n).max_prompt_chars for n in registry.names()), default=0)


def checked_prompt(text: str, max_chars: int) -> str:
    """The customer's words as a creation takes them: trimmed, not blank, not over the provider's `max_chars`, no lone
    surrogate. The doors and the admin preview both call it, so a preview renders what a job would send."""
    prompt = text.strip()
    if not prompt:
        raise ApiError("input.empty_prompt", "prompt")
    if len(prompt) > max_chars:
        raise ApiError("input.prompt_too_long", "prompt")
    if frames.has_surrogate(prompt):
        raise ApiError("input.invalid", "prompt")
    return prompt


def _check_text(registry: ProviderRegistry, req: DoorRequest) -> str:
    prompt = checked_prompt(req.prompt, prompt_cap(registry, req.model))
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
    prompt = _check_text(gate.registry, req)
    # a request without a source hashes as it always did, so a retry across this build still finds its job
    named = {} if req.source_job_id is None else {"source_job_id": req.source_job_id}
    request_hash = body_hash({
        "endpoint": req.endpoint, "model": req.model, "prompt": prompt, "duration": req.duration,
        "ratio": req.ratio, "n": req.n, "options": dict(req.options), "title": req.title,
        "callback_url": req.callback_url, **named,
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
    source_id, source = (None, None) if req.source_job_id is None else await source_of(gate.pool, key.id,
                                                                                         req.source_job_id)
    loaded = [(ref, *await load_image(ref)) for ref in req.images]
    media = tuple([MediaIn(await asyncio.to_thread(_sha256, data), mime, len(data)) for _, data, mime in loaded])
    creq = CreateRequest(model=req.model, prompt=prompt, duration=req.duration, ratio=req.ratio, n=req.n,
                         images=media, options=dict(req.options), title=req.title, callback_url=req.callback_url,
                         door=req.door, source=source)
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
            "images": [{"input": i.idx} for i in inputs], **named}
    new = NewJob(spec.name, key.id, plan, prompt, title=req.title, callback_url=req.callback_url,
                 idem_key=req.idem_key, idem_body_hash=idem_hash, inputs=tuple(inputs), job_id=job_id,
                 request=sent, request_hash=request_hash, source_job_id=source_id)
    try:
        created = await create_job(gate.pool, new, gate.settings)
    except (IdempotencyConflict, TooManyQueued, billing.BillingError) as refused:
        raise ApiError(refused.code) from None
    except SourceUnusable as refused:        # the source stopped being a finished job of this key since the lookup
        raise ApiError(refused.code, "source_job_id") from None
    except FrameUnusable as unusable:
        log.error("the frame of a %s job failed at insert: %s", spec.name, unusable)
        raise ApiError(unusable.code) from None
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
    return (request.app.state.settings.get("public_address") or str(request.base_url)).rstrip("/")


# `points` is what the job's charge took from the key's points wallet (the `*` rows; the provider cap's rows repeat
# them): its standing charge, net of re-check cycles (a re-check after a refund charges again, once; a refused
# re-charge leaves 0). A failed job has had that charge given back (I21, `refunded`): it shows the amount refunded, its
# last charge, so a client's net is `refunded ? 0 : points`. Both ledger reads go through the `ledger_job` index.
_STATUS = ("SELECT j.status, j.error_code, j.model, j.params, " + SHOWN_TITLE + ", j.warnings, j.created_at, "
           "j.finished_at, "
           "j.media_type, "
           "EXISTS (SELECT 1 FROM ledger l WHERE l.job_id = j.id AND l.scope = '*' AND l.entry = 'refund'), "
           "COALESCE((SELECT CASE WHEN j.status = 'failed' "
           "THEN (array_agg(l.amount ORDER BY l.id DESC) FILTER (WHERE l.entry = 'charge'))[1] "
           "ELSE sum(CASE l.entry WHEN 'charge' THEN l.amount ELSE -l.amount END) END "
           "FROM ledger l WHERE l.job_id = j.id AND l.scope = '*' AND l.entry IN ('charge', 'refund')), 0) "
           ", COALESCE(j.render_since, j.sent_at), "
           "GREATEST(EXTRACT(EPOCH FROM clock_timestamp() - COALESCE(j.render_since, j.sent_at)), 0)::float8, "
           "j.source_job_id FROM jobs j WHERE j.id = %s AND j.key_id = %s")


async def job_status(pool: AsyncConnectionPool, base_url: str, key_id, job_id: str, *,
                     media_type: str | None = None, estimate: bool = False,
                     registry: ProviderRegistry | None = None) -> dict:
    """The status body of a job the key owns. 404 `resource.not_found` for any other job, a malformed
    id, or (when given) a job of the other media type. `error` is the rule's message of the job's code, masked
    for a non-public code: never engine or upstream text. The last three keys are estimates (`api/progress.py`):
    only a caller that passes `estimate` and the `registry` may cost a read, and a read that fails costs the answer
    nothing."""
    try:
        wanted = uuid.UUID(job_id)
    except ValueError:
        raise ApiError("resource.not_found") from None
    async with pool.connection() as conn:
        row = await (await conn.execute(_STATUS, (wanted, key_id))).fetchone()
        if row is None or (media_type is not None and row[8] != media_type):
            raise ApiError("resource.not_found")
        (status, code, model, params, title, warnings, created, finished, kind, refunded, points, clock, elapsed,
         source) = row
        outputs = await (await conn.execute(
            "SELECT share_token FROM job_media WHERE job_id = %s AND role = 'output' AND share_token IS NOT NULL "
            "ORDER BY idx", (wanted,))).fetchall() if status == "done" else []
        position = (await (await conn.execute(
            "SELECT count(*) FROM jobs j, jobs me WHERE me.id = %s AND j.provider = me.provider "
            "AND j.status = 'queued' AND j.pool IS NOT DISTINCT FROM me.pool "
            "AND (j.created_at, j.id) <= (me.created_at, me.id)", (wanted,))).fetchone())[0] \
            if status == "queued" else None
    expected = None
    if estimate and status == "waiting" and clock is not None:
        expected = await _expected(pool, registry, model, params.get("duration"), wanted)
    public, stage = _PUBLIC_STATUS[status]
    urls = ["%s/m/%s" % (base_url, token) for (token,) in outputs]
    shown_code, message = errors.shown(code or errors.INTERNAL) if status == "failed" else (None, None)
    return {"id": str(wanted), "status": public, "stage": stage,
            "result_urls": urls if kind == "video" else [], "images": urls if kind == "image" else [],
            "error": message, "error_code": shown_code, "refunded": bool(refunded) and status == "failed",
            "points": points, "model": model, "duration": params.get("duration"), "ratio": params.get("ratio"),
            "n": params.get("n"), "style": params.get("style"), "title": title, "warnings": warnings,
            "queue_position": position, "created_at": created.isoformat(),
            "finished_at": finished.isoformat() if finished else None,
            "progress": progress.progress_of(status, elapsed, expected.seconds if expected else None),
            "render_started_at": _iso(progress.render_started(status, clock)),
            "estimated_finish_at": _iso(progress.finish_of(status, clock, elapsed, expected)),
            "source_job_id": str(source) if source else None}


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def _expected(pool: AsyncConnectionPool, registry: ProviderRegistry | None, model: str, duration,
                    job_id) -> progress.Expected | None:
    """The expected render time of this job's shape, or None (the answer then shows the stage only). Never raises."""
    try:
        spec, base = resolve_model(registry, model)     # type: ignore[arg-type]  # None only if a caller forgot it
        shape = progress.shape_of(spec, base, duration)
    except Exception as exc:     # a model gone from the catalog, a catalog that fails, a caller without a registry
        log.warning("render estimate shape failed: %s", type(exc).__name__)
        return None
    return await progress.expected_for(pool, shape, job_id)


async def job_state(pool: AsyncConnectionPool, job_id) -> tuple[str, str | None] | None:
    """(db status, error code) of a job: what a held request polls."""
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT status, error_code FROM jobs WHERE id = %s", (job_id,))).fetchone()
    return None if row is None else (row[0], row[1])
