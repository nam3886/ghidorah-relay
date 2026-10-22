"""Admin: prompt frames and keep-own locks.

The text relay sends to a provider is one admin-written frame per model (or per kind: the default). Every save is a
version (who, when); the current version of a target is its newest row. This module is thin: the grammar, the SQL and
the renderer are `relay_core.frames`, and the preview runs the very path a creation takes (`api.doors.plan_request`).
Who saved comes from the admin session (`request.state.admin_user`), never from a body.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool

from relay_core import frames
from relay_core.admin.routes_common import MAX_PAGE, Strict, ms, spec_of
from relay_core.api.deps import get_pool
from relay_core.api.doors import Gate, checked_prompt, frame_of, plan_request, resolve_model, snapshots_of
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.providers import CreateRequest, FrameRules, FrameUse, ProviderSpec

router = APIRouter()

DEFAULT = "default"


class TextBody(Strict):
    text: str | None


class ApplyBody(Strict):
    text: str


class RollbackBody(Strict):
    version_id: int


class LockBody(Strict):
    locked: bool


class PreviewBody(Strict):
    model: str
    prompt: str
    duration: int | None = None
    ratio: str | None = None
    options: dict[str, Any] = {}
    text: str | None = None


# ---- shapes -------------------------------------------------------------------------------------------------------
def _v(v: frames.Version | None) -> dict | None:
    return None if v is None else {
        "id": v.id, "text": v.text, "created_ms": ms(v.created_at), "created_by": v.created_by,
        "created_by_id": None if v.created_by_id is None else str(v.created_by_id), "restored_from": v.restored_from}


def _l(lock: frames.Lock | None) -> dict | None:
    return None if lock is None else {
        "locked": lock.locked, "created_ms": ms(lock.created_at), "created_by": lock.created_by,
        "created_by_id": None if lock.created_by_id is None else str(lock.created_by_id)}


def _by(request: Request) -> frames.By:
    user = request.state.admin_user
    return frames.By(user.id, user.username)


# ---- what exists --------------------------------------------------------------------------------------------------
def _rules(spec: ProviderSpec) -> FrameRules:
    rules = getattr(spec, "frames", None)
    if rules is None:
        raise ApiError("resource.not_found", "provider")
    return rules


def _models(spec: ProviderSpec, kind: str) -> list[str]:
    """The catalog model ids of one media kind, in catalog order; 404 `kind` when the provider has none."""
    found = [m.id for m in spec.catalog() if m.media_type == kind]
    if kind not in frames.KINDS or not found:
        raise ApiError("resource.not_found", "kind")
    return found


def _target(spec: ProviderSpec, kind: str, target: str) -> str | None:
    """None for the kind's default, else the model id; an unknown model is 404 `target`."""
    models = _models(spec, kind)
    if target == DEFAULT:
        return None
    if target not in models:
        raise ApiError("resource.not_found", "target")
    return target


def _checked(text: str) -> str:
    try:
        return frames.check_frame(text)
    except frames.FrameError as e:
        raise ApiError(e.code, e.param) from None


def _page(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError("input.invalid", "limit")
    if not 0 <= offset < 2**31:
        raise ApiError("input.invalid", "offset")


async def _effective(conn, spec: ProviderSpec, kind: str, model: str) -> frames.Version | None:
    got = await frames.effective(conn, spec.name, kind, model)
    return None if got is None else got.version


# ---- reads --------------------------------------------------------------------------------------------------------
@router.get("/frames")
async def list_frames(request: Request, provider: str, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    spec = spec_of(request.app.state.registry, provider)
    rules = _rules(spec)
    shown = {m.id: m.display_name or m.id for m in spec.catalog()}
    kinds = []
    async with tx(pool) as conn:
        for kind in sorted({m.media_type for m in spec.catalog()} & set(frames.KINDS)):
            default = await frames.newest(conn, provider, kind, None)
            locks = await frames.current_locks(conn, provider, kind)
            models = []
            for model in _models(spec, kind):
                own = await frames.newest(conn, provider, kind, model)
                own = own if own is not None and own.text is not None else None
                effective = await frames.effective(conn, provider, kind, model)
                lock = locks.get(model)
                models.append({"model": model, "name": shown[model], "source": "own" if own else "default",
                               "own": _v(own), "effective": _v(None if effective is None else effective.version),
                               "locked": bool(lock and lock.locked), "lock": _l(lock)})
            shipped = rules.defaults.get(kind)
            kinds.append({"kind": kind, "default": _v(default), "shipped": shipped,
                          "shipped_differs": default is not None and default.text != shipped, "models": models})
    return {"provider": provider, "kinds": kinds}


@router.get("/frames/{provider}/{kind}/{target}")
async def get_frame(provider: str, kind: str, target: str, request: Request, limit: int = 50, offset: int = 0,
                    pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    spec = spec_of(request.app.state.registry, provider)
    _rules(spec)
    model = _target(spec, kind, target)
    _page(limit, offset)
    async with tx(pool) as conn:
        versions = await frames.history(conn, provider, kind, model, limit, offset)
        current = versions[0] if offset == 0 and versions else await frames.newest(conn, provider, kind, model)
        effective = (await frames.newest(conn, provider, kind, None) if model is None
                     else await _effective(conn, spec, kind, model))
        out: dict[str, Any] = {"current": _v(current), "effective": _v(effective), "history": [_v(v) for v in versions]}
        if model is not None:
            out["lock"] = _l((await frames.current_locks(conn, provider, kind)).get(model))
            out["lock_history"] = [_l(x) for x in await frames.lock_history(conn, provider, kind, model, limit, offset)]
    return out


# ---- writes -------------------------------------------------------------------------------------------------------
def _saved(rules: FrameRules, kind: str, version: frames.Version, unchanged: bool) -> dict:
    warnings = list(rules.warnings(kind, version.text)) if version.text is not None else []
    return {"version": _v(version), "unchanged": unchanged, "warnings": warnings}


@router.put("/frames/{provider}/{kind}/{target}")
async def put_frame(provider: str, kind: str, target: str, body: TextBody, request: Request,
                    pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    spec = spec_of(request.app.state.registry, provider)
    rules = _rules(spec)
    model = _target(spec, kind, target)
    if body.text is None:
        if model is None:
            raise ApiError("input.invalid", "text")       # the default follows nothing
    else:
        _checked(body.text)
    async with tx(pool) as conn:
        current = await frames.newest(conn, provider, kind, model)
        if (current.text == body.text) if current is not None else (body.text is None):
            # the text is the current version (or a model that already follows the default): nothing is added
            return _saved(rules, kind, current, True) if current else {"version": None, "unchanged": True,
                                                                       "warnings": []}
        version = await frames.insert_version(conn, provider, kind, model, body.text, _by(request))
    return _saved(rules, kind, version, False)


@router.post("/frames/{provider}/{kind}/{target}/rollback")
async def rollback_frame(provider: str, kind: str, target: str, body: RollbackBody, request: Request,
                         pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    spec = spec_of(request.app.state.registry, provider)
    rules = _rules(spec)
    model = _target(spec, kind, target)
    async with tx(pool) as conn:
        found = await frames.version_of(conn, body.version_id)
        if found is None or found[1:] != (provider, kind, model):
            raise ApiError("resource.not_found", "version_id")      # another target's version is not ours to copy
        old = found[0]
        if old.text is not None:
            _checked(old.text)
        version = await frames.insert_version(conn, provider, kind, model, old.text, _by(request),
                                              restored_from=old.id)
    return _saved(rules, kind, version, False)


@router.post("/frames/{provider}/{kind}/reset")
async def reset_frame(provider: str, kind: str, request: Request,
                      pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    spec = spec_of(request.app.state.registry, provider)
    rules = _rules(spec)
    _models(spec, kind)
    text = _checked(rules.defaults[kind])
    async with tx(pool) as conn:
        version = await frames.insert_version(conn, provider, kind, None, text, _by(request))
    return _saved(rules, kind, version, False)


@router.put("/frames/{provider}/{kind}/{model}/lock")
async def put_lock(provider: str, kind: str, model: str, body: LockBody, request: Request,
                   pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    spec = spec_of(request.app.state.registry, provider)
    _rules(spec)
    if _target(spec, kind, model) is None:
        raise ApiError("input.invalid", "target")             # the kind's default cannot be locked
    async with tx(pool) as conn:
        lock, unchanged = await frames.set_lock(conn, provider, kind, model, body.locked, _by(request))
    return {"model": model, "lock": _l(lock), "unchanged": unchanged}


@router.post("/frames/{provider}/{kind}/apply-all")
async def apply_all(provider: str, kind: str, body: ApplyBody, request: Request,
                    pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    spec = spec_of(request.app.state.registry, provider)
    rules = _rules(spec)
    models = _models(spec, kind)
    text = _checked(body.text)
    async with tx(pool) as conn:
        applied, kept, unchanged = await frames.apply_all(conn, provider, kind, models, text, _by(request))
    return {"applied": applied, "kept": kept, "unchanged": unchanged, "warnings": list(rules.warnings(kind, text))}


# ---- preview ------------------------------------------------------------------------------------------------------
@router.post("/frames/preview")
async def preview(body: PreviewBody, request: Request) -> dict:
    """The text a job would send, by the path a creation takes (`plan_request`), with the marker of the all-zero job
    id. No key, so no allow-list and no wallet; nothing is stored."""
    gate = Gate.of(request)
    spec, model = resolve_model(gate.registry, body.model)
    rules = _rules(spec)
    prompt = checked_prompt(body.prompt, spec.max_prompt_chars)
    snapshots = await snapshots_of(gate.pool, spec.name)
    if body.text is not None:
        use, source = FrameUse(None, _checked(body.text)), "draft"
    else:
        stored = await frame_of(gate.pool, spec, model)          # the lookup a creation makes
        use = None if stored is None else FrameUse(stored.version.id, stored.version.text)
        source = None if stored is None else stored.source
    creq = CreateRequest(model=body.model, prompt=prompt, duration=body.duration, ratio=body.ratio,
                         options=dict(body.options))
    _, plan, text = plan_request(gate, spec, model, creq, snapshots, use)
    return {"text": text, "length": len(text), "frame_id": plan.frame.id, "source": source,
            "warnings": list(rules.warnings(model.media_type, plan.frame.text))}
