"""`GET /m/{token}`: public input and output media (contracts §8, §9; invariant I12).

The token is the only credential. A token is served only while a `job_media` row of role input or output holds it:
revoking or expiring clears it, and a debug object never has one. The body is streamed from the object store in
chunks (`ObjectStore.stream`, never `read`), one Range at most, and the stream is closed however the response ends.
"""

import contextlib
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from psycopg_pool import AsyncConnectionPool

from relay_core import storage
from relay_core.api.deps import get_pool, get_settings, get_store
from relay_core.api.errors_http import ApiError
from relay_core.api.filenames import content_disposition, media_filename
from relay_core.jobs.shares import PgShareTable
from relay_core.settings import Settings
from relay_core.storage import ObjectStore, ShareRefused

router = APIRouter()

OUTPUT_CACHE = "public, max-age=86400"
INPUT_CACHE = "private, no-cache"          # an input link dies at its expiry, so no shared cache may keep it
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_MEDIA = """
SELECT m.mime, j.prompt, j.model, j.media_type, j.params, j.created_at, j.id
  FROM job_media m JOIN jobs j ON j.id = m.job_id
 WHERE m.object_key = %s AND m.share_token = %s
"""


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The one (first, last) byte pair, both included, that `Range` asks for; None serves the whole object (no
    header, or one this server does not honour: several ranges, or a malformed one). `RangeNotSatisfiable` when the
    range lies past the end."""
    m = _RANGE.fullmatch((header or "").strip())
    if m is None or size <= 0:
        return None
    first, last = m.groups()
    if first == "":                                  # a suffix: the last `last` bytes
        if last == "":
            return None
        if int(last) == 0:
            raise RangeNotSatisfiable
        return max(0, size - int(last)), size - 1
    start = int(first)
    end = size - 1 if last == "" else min(int(last), size - 1)
    if start >= size:
        raise RangeNotSatisfiable
    return (start, end) if start <= end else None


class ClosingStream(StreamingResponse):
    """A streaming response that closes its body iterator however it ends: finished, failed, cancelled, or the client
    went away while a chunk was being sent. The store releases the upstream connection when its iterator is closed
    (it is closed, never handed back half read), and Starlette does not close the iterator by itself when the send
    fails."""

    async def stream_response(self, send) -> None:
        async with contextlib.aclosing(self.body_iterator):
            await super().stream_response(send)


@router.get("/m/{token}")
async def public_media(token: str, request: Request, pool: AsyncConnectionPool = Depends(get_pool),
                       store: ObjectStore = Depends(get_store), settings: Settings = Depends(get_settings)):
    try:
        record = await storage.resolve_share(PgShareTable(pool), token, now=datetime.now(UTC))
    except ShareRefused:
        raise ApiError("resource.not_found") from None
    parsed = storage.parse_key(record.key)
    async with pool.connection() as conn:
        row = await (await conn.execute(_MEDIA, (record.key, token))).fetchone()
    info = await store.stat(record.key) if parsed is not None and row is not None else None
    if info is None:
        raise ApiError("resource.not_found")
    mime, prompt, model, media_type, params, created_at, job_id = row
    name = download_name(settings, prompt, model, media_type, mime, parsed.idx, job_id, created_at, params)
    cache = OUTPUT_CACHE if record.role == "output" else INPUT_CACHE
    return respond(request, store, record.key, info.size, mime, name, cache)


def download_name(settings: Settings, prompt, model, media_type, mime, idx, job_id, created_at, params) -> str:
    return media_filename(settings.get("filename_pattern"), prompt=prompt, model=model, media_type=media_type,
                          mime=mime, idx=idx, job_id=str(job_id), created_at=created_at,
                          duration=params.get("duration"), ratio=params.get("ratio") or "",
                          resolution=params.get("resolution") or "")


def respond(request: Request, store: ObjectStore, key: str, size: int, mime: str, name: str, cache: str) -> Response:
    """One object as a response: whole or one Range, streamed in chunks, closed however it ends. The one place the
    public link and the authenticated content routes share."""
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": content_disposition(name),
               "Cache-Control": cache,
               "X-Content-Type-Options": "nosniff"}
    try:
        part = parse_range(request.headers.get("range"), size)
    except RangeNotSatisfiable:
        return Response(status_code=416, headers={"Content-Range": "bytes */%d" % size})
    if part is None:
        return ClosingStream(store.stream(key), media_type=mime,
                             headers={**headers, "Content-Length": str(size)})
    first, last = part
    return ClosingStream(store.stream(key, first, last - first + 1), status_code=206, media_type=mime,
                         headers={**headers, "Content-Range": "bytes %d-%d/%d" % (first, last, size),
                                  "Content-Length": str(last - first + 1)})


_JOB_MEDIA = """
SELECT m.object_key, m.mime, j.prompt, j.model, j.media_type, j.params, j.created_at
  FROM job_media m JOIN jobs j ON j.id = m.job_id
 WHERE j.id = %s AND m.role = %s AND m.idx = %s AND (%s::uuid IS NULL OR j.key_id = %s)
   AND (%s::text IS NULL OR j.media_type = %s)
"""


async def serve_job_media(request: Request, job_id: str, role: str, idx: int, *, key_id=None,
                          media_type: str | None = None) -> Response:
    """Output or input `idx` of a job, streamed through the API (the content routes). `key_id` limits it to that
    key's jobs (another key's job is 404, like a missing one); `media_type` to video or image jobs. A debug object
    is never served: the role is checked here, not only by the callers."""
    if role not in ("input", "output"):
        raise ApiError("resource.not_found")
    state = request.app.state
    try:
        wanted = uuid.UUID(job_id)
    except ValueError:
        raise ApiError("resource.not_found") from None
    async with state.pool.connection() as conn:
        row = await (await conn.execute(_JOB_MEDIA, (wanted, role, idx, key_id, key_id, media_type,
                                                     media_type))).fetchone()
    info = await state.store.stat(row[0]) if row is not None else None
    if row is None or info is None:
        raise ApiError("resource.not_found")
    key, mime, prompt, model, kind, params, created_at = row
    name = download_name(state.settings, prompt, model, kind, mime, idx, wanted, created_at, params)
    return respond(request, state.store, key, info.size, mime, name, INPUT_CACHE)
