"""Admin: jobs (contracts 7.1, 7.5, 16; D14, D15).

List (newest first, a keyset cursor), detail (events, media, debug files), Re-check, Recover, Regenerate and rename.
Re-check is a store function; Recover is a `recover` command the provider's worker runs under an account lease
(the api holds no credential); Regenerate makes a NEW job through the ordinary creation gate with a FRESH plan, so it
is charged today's price and sees today's catalog and the key's allow-list, never the old job's.
"""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from psycopg_pool import AsyncConnectionPool
from pydantic import Field

from relay_core import billing, storage
from relay_core.admin.routes_common import MAX_PAGE, Strict, enqueue, ms
from relay_core.api.deps import get_pool, get_registry, get_settings, get_store
from relay_core.api.doors import Gate, plan_job
from relay_core.api.errors_http import ApiError
from relay_core.api.routes_media import serve_job_media
from relay_core.db import tx
from relay_core.errors import http_status
from relay_core.jobs import store as jobs
from relay_core.jobs.create import InputMedia
from relay_core.providers import CreateRequest, MediaIn, Plan, ProviderRegistry
from relay_core.settings import Settings
from relay_core.storage import ObjectStore

log = logging.getLogger("relay.admin")
router = APIRouter()

TITLE_LIMIT = 200
RECHECKABLE = ("failed", "waiting", "unknown")
TERMINAL = ("done", "failed")
DEBUG_READ_MAX = 8 * 1024 * 1024
_RASTER = ("image/png", "image/jpeg", "image/webp", "image/gif")
_LIST = (
    "SELECT j.id, j.provider, j.key_id, k.label, j.operation, j.media_type, j.model, j.title, j.status, "
    "j.error_code, j.price, j.account_id, a.label, j.external_ref, j.created_at, j.finished_at "
    "FROM jobs j LEFT JOIN api_keys k ON k.id = j.key_id LEFT JOIN accounts a ON a.id = j.account_id "
    "WHERE (%(status)s::text IS NULL OR j.status = %(status)s) AND (%(provider)s::text IS NULL OR "
    "j.provider = %(provider)s) AND (%(key)s::uuid IS NULL OR j.key_id = %(key)s) "
    "AND (%(at)s::timestamptz IS NULL OR (j.created_at, j.id) < (%(at)s::timestamptz, %(cid)s::uuid)) "
    "ORDER BY j.created_at DESC, j.id DESC LIMIT %(limit)s")
_DETAIL = (
    "SELECT j.id, j.provider, j.key_id, k.label, j.operation, j.media_type, j.model, j.title, j.status, "
    "j.error_code, j.price, j.account_id, a.label, j.external_ref, j.created_at, j.finished_at, j.prompt, j.marker, "
    "j.params, j.completion, j.wallet_kind, j.pool, j.account_cost, j.attempts, j.claims, j.sent_at, j.render_since, "
    "j.upstream_price, j.upstream_model, j.error, j.warnings, j.callback_status, j.callback_attempts, "
    "j.callback_last, j.updated_at, j.provider_state "
    "FROM jobs j LEFT JOIN api_keys k ON k.id = j.key_id LEFT JOIN accounts a ON a.id = j.account_id WHERE j.id = %s")
_SUMMARY = ("id", "provider", "key_id", "key_label", "operation", "media_type", "model", "title", "status",
            "error_code", "price", "account_id", "account_label", "external_ref", "created_at", "finished_at")
_NET = ("SELECT COALESCE(sum(CASE entry WHEN 'charge' THEN amount ELSE -amount END), 0) FROM ledger "
        "WHERE job_id = %s AND entry IN ('charge', 'refund')")


class JobPatch(Strict):
    """Rename. `title: null` clears it: a cleared title is eligible for the provider's title sweep again."""

    title: str | None = Field(max_length=2000)


class RecoverBody(Strict):
    account: str = Field(min_length=1, max_length=100, description="the account's id or its label")


def _summary(row: tuple) -> dict[str, Any]:
    item = dict(zip(_SUMMARY, row, strict=True))
    status, code, external_ref = item["status"], item["error_code"], item["external_ref"]
    item["id"] = str(item["id"])
    item["key_id"] = str(item["key_id"])
    item["account_id"] = None if item["account_id"] is None else str(item["account_id"])
    item["created_ms"], item["finished_ms"] = ms(item.pop("created_at")), ms(item.pop("finished_at"))
    item["error_status"] = http_status(code) if status == "failed" and code else None
    item["can_recheck"] = status in RECHECKABLE and external_ref is not None
    return item


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _cursor_text(created_at: datetime, job_id) -> str:
    """`<epoch microseconds>.<job id>`: exact, and safe in a query string (an ISO time carries a `+`)."""
    return "%d.%s" % ((created_at - _EPOCH) // timedelta(microseconds=1), job_id)


def _cursor(text: str) -> tuple[datetime, uuid.UUID]:
    try:
        micros, cid = text.split(".")
        return _EPOCH + timedelta(microseconds=int(micros)), uuid.UUID(cid)
    except (ValueError, OverflowError):
        raise ApiError("input.invalid", "cursor") from None


@router.get("/jobs")
async def list_jobs(status: str | None = None, provider: str | None = None, key_id: uuid.UUID | None = None,
                    limit: int = 50, cursor: str | None = None,
                    pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Newest first. `next_cursor` is None on the last page; pass it back as `cursor` for the next one."""
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError("input.invalid", "limit")
    at, cid = _cursor(cursor) if cursor else (None, None)
    async with tx(pool) as conn:
        rows = await (await conn.execute(_LIST, {"status": status, "provider": provider, "key": key_id, "at": at,
                                                 "cid": cid, "limit": limit + 1})).fetchall()
    page = rows[:limit]
    more = len(rows) > limit
    return {"items": [_summary(r) for r in page],
            "next_cursor": _cursor_text(page[-1][14], page[-1][0]) if more else None}


@router.get("/jobs/{job_id}")
async def job_detail(job_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    async with tx(pool) as conn:
        row = await (await conn.execute(_DETAIL, (job_id,))).fetchone()
        if row is None:
            raise ApiError("resource.not_found")
        net = (await (await conn.execute(_NET, (job_id,))).fetchone())[0]
        events = await (await conn.execute(
            "SELECT id, at, source, stage, text FROM job_events WHERE job_id = %s ORDER BY at, id",
            (job_id,))).fetchall()
        media = await (await conn.execute(
            "SELECT role, idx, mime, size, width, height, duration_s, share_token FROM job_media WHERE job_id = %s "
            "ORDER BY role, idx", (job_id,))).fetchall()
    item = _summary(row[:16])
    (prompt, marker, params, completion, wallet_kind, pool_name, account_cost, attempts, claims, sent_at, render_since,
     upstream_price, upstream_model, error, warnings, callback_status, callback_attempts, callback_last, updated_at,
     state) = row[16:]
    item.update({
        "prompt": prompt, "marker": marker, "params": params, "completion": completion, "wallet_kind": wallet_kind,
        "pool": pool_name, "account_cost": account_cost, "attempts": attempts, "claims": claims,
        "sent_ms": ms(sent_at), "render_since_ms": ms(render_since), "updated_ms": ms(updated_at),
        "upstream_price": upstream_price, "upstream_model": upstream_model, "error": error, "warnings": warnings,
        "callback": {"status": callback_status, "attempts": callback_attempts, "last": callback_last},
        "net_charged": net, "no_recheck": state.get("no_recheck") is True,
        "events": [{"id": e[0], "at_ms": ms(e[1]), "source": e[2], "stage": e[3], "text": e[4]} for e in events],
        # only same-origin `/m/` links are ever handed to the page; a revoked or expired token has none
        "media": [{"role": m[0], "idx": m[1], "mime": m[2], "size": m[3], "width": m[4], "height": m[5],
                   "duration_s": m[6], "url": None if m[7] is None else "/m/" + m[7]}
                  for m in media if m[0] != "debug"],
        "debug_files": [{"idx": m[1], "mime": m[2], "size": m[3], "url": "/admin/jobs/%s/debug/%d" % (job_id, m[1])}
                        for m in media if m[0] == "debug"],
    })
    return item


@router.get("/jobs/{job_id}/debug/{idx}")
async def debug_file(job_id: uuid.UUID, idx: int, pool: AsyncConnectionPool = Depends(get_pool),
                     store: ObjectStore = Depends(get_store)) -> Response:
    """A debug object, never a public link. A raster image is served as it is; anything else (HTML, JSON, text) is
    served as `text/plain`, so a captured page can never run in the admin's origin."""
    async with tx(pool) as conn:
        row = await (await conn.execute(
            "SELECT object_key, mime, size FROM job_media WHERE job_id = %s AND role = 'debug' AND idx = %s",
            (job_id, idx))).fetchone()
    if row is None:
        raise ApiError("resource.not_found")
    key, mime, size = row
    try:
        data = await store.read(key, 0, min(size, DEBUG_READ_MAX))
    except storage.ObjectNotFound:
        raise ApiError("resource.not_found") from None
    kind = mime.split(";")[0].strip().lower()
    return Response(data, media_type=kind if kind in _RASTER else "text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


# ---- media proxies: the files of a job through the admin session (outputs and input images, never debug) ----------
def _admin_media(role: str):
    async def serve(job_id: str, request: Request, idx: int = 0):
        if idx < 0:
            raise ApiError("resource.not_found")
        return await serve_job_media(request, job_id, role, idx)
    return serve


async def _listing(pool: AsyncConnectionPool, job_id: uuid.UUID, role: str) -> dict:
    async with tx(pool) as conn:
        if await (await conn.execute("SELECT 1 FROM jobs WHERE id = %s", (job_id,))).fetchone() is None:
            raise ApiError("resource.not_found")
        rows = await (await conn.execute(
            "SELECT idx, mime, size FROM job_media WHERE job_id = %s AND role = %s ORDER BY idx",
            (job_id, role))).fetchall()
    return {"items": [{"idx": i, "mime": m, "size": s} for i, m, s in rows]}


router.add_api_route("/jobs/{job_id}/content", _admin_media("output"), methods=["GET"])
router.add_api_route("/jobs/{job_id}/content/{idx}", _admin_media("output"), methods=["GET"])
router.add_api_route("/jobs/{job_id}/outputs/{idx}/content", _admin_media("output"), methods=["GET"])
router.add_api_route("/jobs/{job_id}/images/{idx}/content", _admin_media("input"), methods=["GET"])


@router.get("/jobs/{job_id}/outputs")
async def list_outputs(job_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    return await _listing(pool, job_id, "output")


@router.get("/jobs/{job_id}/images")
async def list_images(job_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    return await _listing(pool, job_id, "input")


@router.patch("/jobs/{job_id}")
async def rename_job(job_id: uuid.UUID, body: JobPatch, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    title = " ".join((body.title or "").split())
    if len(title) > TITLE_LIMIT:
        raise ApiError("input.invalid", "title")
    async with tx(pool) as conn:
        row = await (await conn.execute("UPDATE jobs SET title = %s, updated_at = clock_timestamp() WHERE id = %s "
                                        "RETURNING title", (title or None, job_id))).fetchone()
    if row is None:
        raise ApiError("resource.not_found")
    return {"id": str(job_id), "title": row[0]}


# ---- Re-check, Recover, Regenerate -------------------------------------------------------------------------------
@router.post("/jobs/{job_id}/recheck")
async def recheck(job_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Poll the upstream again for a result that was already paid for. The refusal's reason is the rule's code."""
    try:
        recharged = await jobs.recheck_job(pool, job_id)
    except jobs.JobRefused as e:
        raise ApiError(e.code) from None
    return {"id": str(job_id), "recharged": recharged}


@router.post("/jobs/{job_id}/recover", status_code=202)
async def recover(job_id: uuid.UUID, body: RecoverBody, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Queue a `recover` command for a finished job: the worker fetches the outputs from the named account's
    upstream history under a command lease. Refused (409 `job.busy`) while the job is still running."""
    async with tx(pool) as conn:
        job = await (await conn.execute("SELECT provider, status, external_ref FROM jobs WHERE id = %s",
                                        (job_id,))).fetchone()
        if job is None:
            raise ApiError("resource.not_found")
        provider, status, external_ref = job
        if status not in TERMINAL:
            raise ApiError("job.busy")
        try:
            wanted = (uuid.UUID(body.account), None)
        except ValueError:
            wanted = (None, body.account)
        rows = await (await conn.execute(
            "SELECT id FROM accounts WHERE provider = %s AND (id = %s OR label = %s) LIMIT 2",
            (provider, wanted[0], wanted[1]))).fetchall()
        if len(rows) != 1:                                   # none, or a label two accounts share
            raise ApiError("input.invalid", "account")
        command_id = await enqueue(conn, provider, "recover", account_id=rows[0][0], job_id=job_id,
                                   args={"job_id": str(job_id), "account_id": str(rows[0][0]),
                                         "external_ref": external_ref})
    return {"command_id": str(command_id)}


@router.post("/jobs/{job_id}/fetch-title", status_code=202)
async def fetch_title(job_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Queue a `titles` command for this job's upstream reference, on the account that made it (the reference is
    only readable under that account's session); the worker sets the title only while it is empty."""
    async with tx(pool) as conn:
        job = await (await conn.execute("SELECT provider, external_ref, account_id FROM jobs WHERE id = %s",
                                        (job_id,))).fetchone()
        if job is None:
            raise ApiError("resource.not_found")
        if job[1] is None or job[2] is None:
            raise ApiError("job.not_submitted")
        command_id = await enqueue(conn, job[0], "titles", account_id=job[2], job_id=job_id,
                                   args={"refs": [job[1]], "account_id": str(job[2])})
    return {"command_id": str(command_id)}


async def fresh_plan(gate: Gate, job_id) -> Plan:
    """The old job's request as it stands today, planned through the one creation path (`doors.plan_job`: model
    resolution, the key's allow-list, snapshots, `spec.plan()`): today's price, catalog and timeout. The request is
    the one the client sent (`jobs.request`), never the old plan's `params` (those are the provider's own, and may
    hold internal keys). A stored input has no hash, so its object key stands in as its identity. The callback is
    not carried over: the old job's webhook was the client's, and the new job is made without it. A job with no
    stored request (made before it was kept) cannot be regenerated: `input.invalid`."""
    async with tx(gate.pool) as conn:
        job = await (await conn.execute(
            "SELECT j.request, k.models FROM jobs j JOIN api_keys k ON k.id = j.key_id WHERE j.id = %s",
            (job_id,))).fetchone()
        if job is None:
            raise ApiError("resource.not_found")
        if job[0] is None:
            raise ApiError("input.invalid", "job")
        inputs = await (await conn.execute(
            "SELECT object_key, mime, size FROM job_media WHERE job_id = %s AND role = 'input' ORDER BY idx",
            (job_id,))).fetchall()
    sent, allowed = job
    request = CreateRequest(
        model=sent["model"], prompt=sent["prompt"], duration=sent.get("duration"), ratio=sent.get("ratio"),
        n=sent.get("n"), images=tuple(MediaIn(hashlib.sha256(k.encode()).hexdigest(), m, s) for k, m, s in inputs),
        options=sent.get("options") or {}, title=sent.get("title"),
        door="facade" if sent.get("endpoint") in ("openai", "gemini") else "native")
    return (await plan_job(gate, allowed, request))[1]


@router.post("/jobs/{job_id}/regenerate")
async def regenerate(job_id: uuid.UUID, pool: AsyncConnectionPool = Depends(get_pool),
                     registry: ProviderRegistry = Depends(get_registry),
                     settings: Settings = Depends(get_settings), store: ObjectStore = Depends(get_store)) -> dict:
    """A new job for the same key through the creation gate (cap, wallet, charge), at today's price."""
    plan = await fresh_plan(Gate(pool, settings, store, registry), job_id)

    async def copy_inputs(new_job_id, old: list[InputMedia]) -> tuple[InputMedia, ...]:
        copies = []
        for m in old:
            try:
                data = await store.read(m.object_key)
            except storage.ObjectNotFound:
                raise ApiError("resource.not_found", "input") from None
            key = storage.media_key(str(new_job_id), "input", m.idx, m.mime)
            await store.put_bytes(key, data, m.mime)
            copies.append(InputMedia(m.idx, key, m.mime, m.size, m.width, m.height))
        return tuple(copies)

    try:
        created = await jobs.regenerate_job(pool, job_id, settings, plan=plan, copy_inputs=copy_inputs)
    except jobs.JobRefused as e:
        raise ApiError(e.code) from None
    except jobs.CreateRefused as e:
        raise ApiError(e.code) from None
    except billing.WalletMissing:
        raise
    except billing.BillingError as e:
        raise ApiError(e.code) from None
    return {"id": str(created.job_id), "status": created.status, "queue_position": created.queue_position}
