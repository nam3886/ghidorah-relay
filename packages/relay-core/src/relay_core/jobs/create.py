"""Job creation (without the source step; invariants I3, I15).

One transaction: the idempotency lookup, the per-key cap, the wallet charge with its ledger row, the job row, the
input media rows and the `queued` event. Input objects are uploaded BEFORE this runs (no foreign I/O in a
transaction); an object whose creation failed is an orphan the media sweep removes later.
"""

import hashlib
import json
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from relay_core import billing, failpoints, frames
from relay_core.db import tx
from relay_core.jobs.events import add_event
from relay_core.providers import Plan
from relay_core.redact import redact_log

INPUT_LINK_TTL_S = 7 * 86_400
IDEM_CONSTRAINT = "jobs_key_id_idem_key_key"


class Settings(Protocol):
    def get(self, key: str) -> Any: ...


class CreateRefused(Exception):
    code = "internal.unexpected"


class IdempotencyConflict(CreateRefused):
    code = "input.idempotency_conflict"


class TooManyQueued(CreateRefused):
    code = "quota.too_many_queued"


class FrameUnusable(CreateRefused):
    """The plan has no frame, or one that fails the grammar: nothing falls back to the core frame."""
    code = "internal.frame_unusable"


@dataclass(frozen=True)
class InputMedia:
    """An input object that is already in the object store."""
    idx: int
    object_key: str
    mime: str
    size: int
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class NewJob:
    provider: str
    key_id: Any
    plan: Plan
    prompt: str
    title: str | None = None
    callback_url: str | None = None
    idem_key: str | None = None
    idem_body_hash: str | None = None
    inputs: tuple[InputMedia, ...] = ()
    job_id: uuid.UUID = field(default_factory=uuid.uuid4)
    request: Mapping[str, Any] | None = None     # what the client sent (7.1); a regenerated job keeps its source's
    request_hash: str | None = None

    def __post_init__(self):
        if (self.idem_key is None) != (self.idem_body_hash is None):
            raise ValueError("an idempotency key and its body hash go together")


@dataclass(frozen=True)
class Created:
    job_id: uuid.UUID
    status: str
    queue_position: int
    replayed: bool          # True: an earlier request with the same key and body made this job; nothing was charged


def body_hash(request: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of a request whose images are already replaced by the sha256 of each reference
    as the client sent it."""
    text = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def marker_for(job_id: uuid.UUID) -> str:
    """The marker is derived from the job id; Regenerate makes a new job and so gets a new marker. It is the dola
    contract's: four digits, 3000-9999 (below 3000 reads as a year or a resolution, and letters read as content),
    `3000 + sha1(job id)[:8] % 7000`. Two jobs can share one; the provider judges a marker
    against its own time window, the core only sends it."""
    return str(3000 + int(hashlib.sha1(str(job_id).encode()).hexdigest()[:8], 16) % 7000)


async def _position(conn, provider: str, job_id) -> int:
    """1-based place among the queued jobs of the same provider AND pool."""
    return (await (await conn.execute(
        "SELECT count(*) FROM jobs j, jobs me WHERE me.id = %s AND j.provider = %s AND j.status = 'queued' "
        "AND j.pool IS NOT DISTINCT FROM me.pool "
        "AND (j.created_at, j.id) <= (me.created_at, me.id)", (job_id, provider))).fetchone())[0]


async def _replay(conn, new: NewJob) -> Created | None:
    """The job an earlier request with this idempotency key made: the same body replays it, another body refuses."""
    if new.idem_key is None:
        return None
    row = await (await conn.execute(
        "SELECT id, idem_body_hash, status FROM jobs WHERE key_id = %s AND idem_key = %s",
        (new.key_id, new.idem_key))).fetchone()
    if row is None:
        return None
    if row[1] != new.idem_body_hash:
        raise IdempotencyConflict("idempotency key %r was used with a different request" % new.idem_key)
    return Created(row[0], row[2], await _position(conn, new.provider, row[0]) if row[2] == "queued" else 0, True)


async def _lock_key(conn, new: NewJob, settings: Settings) -> int:
    """Lock the key row and return its cap (the key's own value, else the default; 0 = unlimited). The lock
    serialises this key's creators, which is what makes the cap exact. It is `NO KEY UPDATE`: as exclusive among
    creators as `UPDATE`, but it does not block the foreign-key check of another writer inserting a job for the key."""
    row = await (await conn.execute("SELECT max_unfinished FROM api_keys WHERE id = %s FOR NO KEY UPDATE",
                                    (new.key_id,))).fetchone()
    if row is None:
        raise billing.WalletMissing("no key %s" % new.key_id)
    return settings.get("max_unfinished_default") if row[0] is None else row[0]


async def _check_cap(conn, new: NewJob, limit: int) -> None:
    """Refuse when the key's unfinished jobs have reached the cap (the key row is locked by now)."""
    if limit == 0:
        return
    unfinished = (await (await conn.execute(
        "SELECT count(*) FROM jobs WHERE key_id = %s AND status NOT IN ('done', 'failed')",
        (new.key_id,))).fetchone())[0]
    if unfinished >= limit:
        raise TooManyQueued("key %s has %d unfinished jobs (limit %d)" % (new.key_id, unfinished, limit))


_INSERT = (
    "INSERT INTO jobs (id, provider, key_id, idem_key, idem_body_hash, operation, media_type, model, params, prompt, "
    "title, marker, status, completion, after_sent, submit_timeout_s, wallet_kind, price, pool, account_cost, "
    "callback_url, warnings, request, request_hash, sent_text, frame_id) VALUES (%(id)s, %(provider)s, %(key)s, "
    "%(idem)s, %(hash)s, %(op)s, %(media)s, %(model)s, %(params)s, %(prompt)s, %(title)s, %(marker)s, 'queued', "
    "%(completion)s, %(after_sent)s, %(timeout)s, %(kind)s, %(price)s, %(pool)s, %(cost)s, %(callback)s, "
    "%(warnings)s, %(request)s, %(rhash)s, %(sent)s, %(frame)s)")


def rendered(new: NewJob, marker: str) -> tuple[str, int | None]:
    """(the text this job sends, the frame version's id), rendered once with the job's own marker. A plan without a
    frame, or a frame that fails the grammar, refuses inside the creation transaction, so the charge rolls back."""
    frame = new.plan.frame
    if frame is None:
        raise FrameUnusable("the plan of a %s job carries no frame" % new.provider)
    try:
        return frames.render(frame.text, new.prompt, marker, frame.verbatim), frame.id
    except frames.FrameError as e:
        raise FrameUnusable("the frame of a %s job fails the grammar: %s" % (new.provider, e)) from e


async def _insert(conn, new: NewJob) -> None:
    p = new.plan
    marker = marker_for(new.job_id)
    sent_text, frame_id = rendered(new, marker)
    await conn.execute(_INSERT, {
        "id": new.job_id, "provider": new.provider, "key": new.key_id, "idem": new.idem_key,
        "hash": new.idem_body_hash, "op": p.operation, "media": p.media_type, "model": p.model,
        "params": Jsonb(dict(p.params)), "prompt": new.prompt, "title": new.title, "marker": marker,
        "completion": str(p.completion), "after_sent": str(p.after_sent), "timeout": p.submit_timeout_s,
        "kind": p.wallet_kind, "price": p.price, "pool": p.pool, "cost": p.account_cost,
        "callback": new.callback_url, "rhash": new.request_hash,
        "request": None if new.request is None else Jsonb(dict(new.request)), "sent": sent_text, "frame": frame_id,
        "warnings": Jsonb([{"code": w.code, "text": redact_log(w.text)} for w in p.warnings])})
    for m in new.inputs:
        await conn.execute(
            "INSERT INTO job_media (job_id, role, idx, object_key, mime, size, width, height, share_token, "
            "share_expires_at) VALUES (%s, 'input', %s, %s, %s, %s, %s, %s, %s, "
            "clock_timestamp() + make_interval(secs => %s))",
            (new.job_id, m.idx, m.object_key, m.mime, m.size, m.width, m.height, secrets.token_urlsafe(24),
             INPUT_LINK_TTL_S))


async def _create(pool: AsyncConnectionPool, new: NewJob, settings: Settings) -> Created:
    async with tx(pool) as conn:
        hit = await _replay(conn, new)
        if hit is None:
            await failpoints.hit("job.create.lookup_missed")
            limit = await _lock_key(conn, new, settings)
            hit = await _replay(conn, new)     # a creator that held the lock may have made it while we waited
        if hit is not None:
            return hit
        await _check_cap(conn, new, limit)
        await billing.charge(conn, new.key_id, new.provider, new.plan.wallet_kind, new.plan.price, new.job_id)
        await failpoints.hit("job.create.charged")
        await _insert(conn, new)
        await add_event(conn, new.job_id, "system", "queued", "queued")
        return Created(new.job_id, "queued", await _position(conn, new.provider, new.job_id), False)


async def create_job(pool: AsyncConnectionPool, new: NewJob, settings: Settings) -> Created:
    """Create a job, or replay the one this idempotency key already made (I3).

    The lookup comes before the cap: a retry of a request that succeeded must not be refused because its own
    job fills the cap. Raises IdempotencyConflict (409), TooManyQueued (429, nothing charged, no job row) and the
    billing refusals (KeyDisabled, QuotaExceeded, WalletMissing). A unique violation on the idempotency key means
    a writer that does not take the key lock got there first: this transaction is rolled back (its charge with
    it) and the winner is re-read under the same rules."""
    try:
        return await _create(pool, new, settings)
    except psycopg.errors.UniqueViolation as e:
        if e.diag.constraint_name != IDEM_CONSTRAINT:
            raise
    async with tx(pool) as conn:
        hit = await _replay(conn, new)
    if hit is None:
        raise RuntimeError("idempotency violation without a winner")
    return hit
