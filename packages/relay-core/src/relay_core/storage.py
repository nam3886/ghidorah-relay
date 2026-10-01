"""Media storage (contracts §9, I11, I12): the S3 subset on the `minio` SDK, share tokens and the sweeps.

One bucket, keys `<job_id>/<role>/<idx>.<ext>` with role `input`, `output` or `debug`. The SDK is blocking, so every
call runs in `asyncio.to_thread`; its HTTP client has a 3 s connect and 30 s read timeout and one retry.

Share tokens are 128-bit random; only `input` and `output` objects may carry one (debug media are never reachable
by link, I12). Which rows hold which token is the database's business, so this module sees them through the small
`ShareTable` protocol and stays DB-free.
"""

import asyncio
import io
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import urllib3
from minio import Minio
from minio.error import MinioException, S3Error

from relay_core.db.pool import assert_no_tx

BUCKET = "relay"
CONNECT_TIMEOUT_S = 3
READ_TIMEOUT_S = 30
CHUNK_SIZE = 256 * 1024
INPUT_LINK_TTL = timedelta(days=7)
ROLES = ("input", "output", "debug")
SHARED_ROLES = ("input", "output")
_BUCKET_EXISTS = ("BucketAlreadyOwnedByYou", "BucketAlreadyExists")
_NOT_FOUND = ("NoSuchKey", "NoSuchObject")
_EXT = {"video/mp4": "mp4", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
        "text/html": "html", "application/json": "json", "text/plain": "txt"}
_SAFE_ID = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class StorageError(RuntimeError):
    """The store could not do what was asked, or an upload cannot be trusted. Messages carry no endpoint, key or
    credential."""


class ObjectNotFound(StorageError):
    pass


class ShareRefused(Exception):
    """A share link is not served. `reason` is one of: malformed, unknown_or_revoked, not_shareable, expired."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---- key layout ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ParsedKey:
    job_id: str
    role: str
    idx: int


def media_key(job_id: str, role: str, idx: int, mime: str) -> str:
    if not job_id or not set(job_id) <= _SAFE_ID:
        raise ValueError("a job id is letters, digits, '_' and '-'")
    if role not in ROLES:
        raise ValueError("unknown media role %r" % role)
    if idx < 0:
        raise ValueError("a media index is not negative")
    return "%s/%s/%d.%s" % (job_id, role, idx, _EXT.get((mime or "").split(";")[0].strip().lower(), "bin"))


def parse_key(key: str) -> ParsedKey | None:
    """The parts of a key that follows the layout; None for any other object in the bucket."""
    parts = key.split("/")
    if len(parts) != 3 or not parts[0] or not set(parts[0]) <= _SAFE_ID or parts[1] not in ROLES:
        return None
    stem, dot, ext = parts[2].partition(".")
    if not dot or not ext or not stem.isascii() or not stem.isdigit():
        return None
    return ParsedKey(parts[0], parts[1], int(stem))


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    last_modified: datetime | None = None
    content_type: str = ""


# ---- the store ----------------------------------------------------------------------------------------------------
def parse_endpoint(endpoint: str) -> tuple[str, bool]:
    """('host:port', secure) from `host:port`, `http://host:port` or `https://host:port`."""
    if "://" not in endpoint:
        return endpoint, False
    parts = urlsplit(endpoint)
    return parts.netloc, parts.scheme == "https"


class ObjectStore:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str = BUCKET, *,
                 region: str | None = None, client: object | None = None):
        host, secure = parse_endpoint(endpoint)
        self.bucket = bucket
        self._client = client or Minio(host, access_key=access_key, secret_key=secret_key, secure=secure,
                                       region=region or None, http_client=http_client())

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ, bucket: str = BUCKET) -> "ObjectStore":
        try:
            return cls(env["MINIO_ENDPOINT"], env["MINIO_ACCESS_KEY"], env["MINIO_SECRET_KEY"], bucket,
                       region=env.get("MINIO_REGION"))
        except KeyError as missing:
            raise StorageError("%s is not set" % missing.args[0]) from None

    async def _call(self, fn: Callable, *args, **kwargs):
        assert_no_tx()
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except S3Error as exc:
            if exc.code in _NOT_FOUND:
                raise ObjectNotFound("object not found") from None
            raise StorageError("object store error: %s" % exc.code) from None
        except (MinioException, urllib3.exceptions.HTTPError, OSError):
            raise StorageError("object store unreachable") from None

    async def ensure_bucket(self) -> None:
        """Create the bucket if it is missing. Losing a creation race to another process is success."""
        def make() -> None:
            if self._client.bucket_exists(self.bucket):
                return
            try:
                self._client.make_bucket(self.bucket)
            except S3Error as exc:
                if exc.code not in _BUCKET_EXISTS:
                    raise

        await self._call(make)

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> ObjectInfo:
        return await self._put(key, lambda: io.BytesIO(data), len(data), content_type)

    async def put_file(self, key: str, path: str | Path, content_type: str) -> ObjectInfo:
        def opened():
            return open(path, "rb")

        return await self._put(key, opened, os.path.getsize(path), content_type)

    async def _put(self, key: str, open_stream: Callable, size: int, content_type: str) -> ObjectInfo:
        if parse_key(key) is None:
            raise ValueError("not a media key: %r" % key)
        if size <= 0:
            raise StorageError("refusing to store an empty file")

        def upload() -> None:
            stream = open_stream()
            try:
                self._client.put_object(self.bucket, key, stream, size, content_type=content_type)
            finally:
                stream.close()

        await self._call(upload)
        info = await self.stat(key)
        if info is None or info.size != size:
            await self.remove(key)                            # a truncated object must not be served
            raise StorageError("upload was truncated: %s of %d bytes stored" % (info.size if info else 0, size))
        return info

    async def stat(self, key: str) -> ObjectInfo | None:
        try:
            st = await self._call(self._client.stat_object, self.bucket, key)
        except ObjectNotFound:
            return None
        return ObjectInfo(key, int(st.size), st.last_modified, st.content_type or "")

    async def stream(self, key: str, offset: int = 0, length: int = 0,
                     chunk_size: int = CHUNK_SIZE) -> AsyncIterator[bytes]:
        """The object (or `length` bytes from `offset`; length 0 = to the end: a Range request) in chunks of at most
        `chunk_size`, never the whole range in memory. The connection is released when the iterator ends, fails or is
        closed early (a client that hangs up);
        a reader that may stop before the end should wrap it in `contextlib.aclosing`."""
        if offset < 0 or length < 0 or chunk_size <= 0:
            raise ValueError("offset and length are not negative and chunk_size is positive")
        resp = await self._call(self._client.get_object, self.bucket, key, offset=offset, length=length)
        chunks = resp.stream(chunk_size)
        try:
            while (part := await self._call(next, chunks, None)) is not None:
                yield part
        finally:
            await asyncio.shield(asyncio.to_thread(self._release, resp))

    @staticmethod
    def _release(resp) -> None:
        resp.close()
        resp.release_conn()

    async def read(self, key: str, offset: int = 0, length: int = 0) -> bytes:
        """The same range as one `bytes`, for small objects (a reference image). Large ones go through `stream`."""
        return b"".join([part async for part in self.stream(key, offset, length)])

    async def list_prefix(self, prefix: str = "") -> list[ObjectInfo]:
        # ponytail: lists the whole prefix in memory; paginate or use inventory when the bucket outgrows a sweep.
        def listing() -> list[ObjectInfo]:
            return [ObjectInfo(o.object_name, int(o.size or 0), o.last_modified)
                    for o in self._client.list_objects(self.bucket, prefix=prefix, recursive=True)
                    if not o.is_dir]

        return await self._call(listing)

    async def remove(self, key: str) -> None:
        await self._call(self._client.remove_object, self.bucket, key)

    async def remove_job(self, job_id: str) -> int:
        """Every object of one job. The prefix ends with '/', so job `12` never touches job `123`."""
        keys = [o.key for o in await self.list_prefix(job_id + "/")]
        for key in keys:
            await self.remove(key)
        return len(keys)


def http_client() -> urllib3.PoolManager:
    return urllib3.PoolManager(timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
                               retries=urllib3.Retry(total=1), maxsize=10)


# ---- share tokens (DB-free) ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ShareRecord:
    key: str
    role: str
    share_expires_at: datetime | None


class ShareTable(Protocol):
    """What the database offers for links: find the row that holds a token, and clear a job's tokens."""

    async def find(self, token: str) -> ShareRecord | None: ...

    async def clear_job(self, job_id: str) -> int: ...


def new_share_token() -> str:
    return secrets.token_urlsafe(16)          # 128 bits


def share_expiry(role: str, now: datetime) -> datetime | None:
    """When a link for a new media row expires: inputs after 7 days, outputs never; debug media get no link."""
    if role == "input":
        return now + INPUT_LINK_TTL
    if role == "output":
        return None
    raise ValueError("a %s object never gets a share link" % role)


async def resolve_share(table: ShareTable, token: str, *, now: datetime) -> ShareRecord:
    """The object a token may serve, or `ShareRefused`. A revoked token is gone from the table, so it is unknown."""
    if not 16 <= len(token) <= 64 or not set(token) <= _SAFE_ID:
        raise ShareRefused("malformed")
    record = await table.find(token)
    if record is None:
        raise ShareRefused("unknown_or_revoked")
    if record.role not in SHARED_ROLES:
        raise ShareRefused("not_shareable")
    expires = record.share_expires_at
    if (record.role == "input" and expires is None) or (expires is not None and now >= expires):
        raise ShareRefused("expired")
    return record


async def revoke_job_shares(table: ShareTable, job_id: str) -> int:
    """Revoke every link of a job (the admin action); returns how many tokens were cleared."""
    return await table.clear_job(job_id)


# ---- sweeps -------------------------------------------------------------------------------------------------------
OwnedJobs = Callable[[set[str]], Awaitable[set[str]]]


async def sweep_orphans(store: ObjectStore, owned_jobs: OwnedJobs, *, min_age: timedelta, now: datetime,
                        enabled: bool = True) -> list[str]:
    """Remove objects no job owns. Both conditions are required: no owning job AND older than `min_age`; a file
    uploaded a moment before its job row commits looks exactly like an orphan. `owned_jobs(ids)` returns the subset
    of ids that still have a job row; if it fails nothing is removed. `enabled=False` is the switch the restore
    runbook uses. Objects outside the key layout are left alone."""
    if not enabled:
        return []
    old = [(o, p) for o in await store.list_prefix("") if (p := parse_key(o.key)) is not None
           and o.last_modified is not None and now - o.last_modified >= min_age]
    if not old:
        return []
    owned = await owned_jobs({p.job_id for _, p in old})
    gone = [o.key for o, p in old if p.job_id not in owned]
    for key in gone:
        await store.remove(key)
    return gone


async def sweep_retention(store: ObjectStore, *, retention: timedelta, now: datetime,
                          roles: tuple[str, ...] = ("debug",)) -> list[str]:
    """Remove objects of the given roles (debug media by default) older than `retention`; returns their keys so
    the caller can drop the matching rows."""
    gone = [o.key for o in await store.list_prefix("") if o.last_modified is not None
            and (p := parse_key(o.key)) is not None and p.role in roles and now - o.last_modified >= retention]
    for key in gone:
        await store.remove(key)
    return gone
