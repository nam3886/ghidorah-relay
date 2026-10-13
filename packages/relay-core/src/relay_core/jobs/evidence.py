"""The evidence sink of a worker (contracts §5.2 `evidence`): debug files of a job, kept as `job_media(role='debug')`.

Text (HTML, network logs) goes through the shared redactor again before it is stored; an image is pixels and is kept as
it is. A file is never public (`share_token` stays NULL), the admin reads it through its own session, and the retention
sweep (`log_retention_days`) removes the row and the object. Nothing here fails a job: a refused or failed store is
logged and the caller goes on.

The row comes first (it takes the index), then the object. A failed store keeps the row (the upload may have worked), so
the retention sweep removes the row and any object together; the whole call is bounded by `TIMEOUT_S`. A job
keeps at most `MAX_FILES` files and no file is larger than `MAX_BYTES`: more is dropped with a warning.

Only the types relay itself emits are kept: PNG, JPEG and WebP as they are, and text-like types (`text/*`, JSON,
NDJSON, XML and any `+json` or `+xml`) redacted. Any other type is dropped with a warning.

Known limit: a long store outage can fill one job's 40 debug rows with files that never landed; that job keeps no
further debug files until retention.
"""

import asyncio
import logging

from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from relay_core import storage
from relay_core.db.pool import tx
from relay_core.jobs.runctx import EvidenceSink
from relay_core.redact import redact_text
from relay_core.storage import ObjectStore

log = logging.getLogger("relay.evidence")

MAX_FILES = 40
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 15
_TRIES = 3


KEPT_AS_IS = ("image/png", "image/jpeg", "image/webp")
TEXT_TYPES = ("application/json", "application/x-ndjson", "application/xml")


def _kind(mime: str) -> str | None:
    """What to do with a file of this type: "image" (kept as it is), "text" (redacted), or None (not stored). One
    allow-list on the base type; `image/svg+xml` ends in `+xml`, so it is text."""
    base = (mime or "").split(";")[0].strip().lower()
    if base in KEPT_AS_IS:
        return "image"
    if base.startswith("text/") or base in TEXT_TYPES or base.endswith(("+json", "+xml")):
        return "text"
    return None


def _redacted(data: bytes) -> bytes:
    return redact_text(data.decode("utf-8", "replace")).encode("utf-8")


async def _reserve(db: AsyncConnectionPool, job_id: str, name: str, size: int, mime: str) -> int | None:
    """The row of the next free index, or None when the job already holds `MAX_FILES`."""
    for _ in range(_TRIES):
        try:
            async with tx(db) as conn:
                count, nxt = await (await conn.execute(
                    "SELECT count(*), COALESCE(max(idx) + 1, 0) FROM job_media WHERE job_id = %s AND role = 'debug'",
                    (job_id,))).fetchone()
                if count >= MAX_FILES:
                    return None
                await conn.execute(
                    "INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
                    "VALUES (%s, 'debug', %s, %s, %s, %s)",
                    (job_id, nxt, storage.media_key(job_id, "debug", nxt, mime, name), mime, size))
                return nxt
        except UniqueViolation:         # two writers took the same index: read it again
            continue
    return None


async def keep(db: AsyncConnectionPool, store: ObjectStore, job_id: str, name: str, data: bytes, mime: str) -> None:
    """Store one evidence file of a job within `TIMEOUT_S`. Never raises (a failure or a timeout is logged)."""
    try:
        async with asyncio.timeout(TIMEOUT_S):
            await _keep(db, store, job_id, name, data, mime)
    except TimeoutError:
        log.warning("evidence %r of a job dropped: the store did not answer in %s s", name, TIMEOUT_S)
    except Exception as e:      # noqa: BLE001 - evidence is diagnostics: it never fails a job
        log.warning("evidence %r of a job not kept: %s", name, type(e).__name__)


async def _keep(db: AsyncConnectionPool, store: ObjectStore, job_id: str, name: str, data: bytes, mime: str) -> None:
    kind = _kind(mime)
    if kind is None:
        log.warning("evidence %r of a job dropped: %s is not a type that is kept", name, mime or "no type")
        return
    if kind == "text":
        data = await asyncio.to_thread(_redacted, data)           # a whole page: not on the event loop
    if len(data) > MAX_BYTES:
        log.warning("evidence %r of a job dropped: %d bytes is over the %d limit", name, len(data), MAX_BYTES)
        return
    idx = await _reserve(db, job_id, name, len(data), mime)
    if idx is None:
        log.warning("evidence %r of a job dropped: the job already holds %d files", name, MAX_FILES)
        return
    # A failed store keeps its row: the upload may have worked, and the retention sweep removes row and object together.
    await store.put_bytes(storage.media_key(job_id, "debug", idx, mime, name), data, mime)


def job_sink(db: AsyncConnectionPool, store: ObjectStore) -> EvidenceSink:
    """The sink a worker wires: `(job_id, name, data, mime)`."""
    async def sink(job_id: str, name: str, data: bytes, mime: str) -> None:
        await keep(db, store, str(job_id), name, data, mime)
    return sink
