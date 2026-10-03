"""Media storage (contracts §9, I11, I12) against the real MinIO that scripts/check.sh starts."""

import asyncio
import functools
import io
import os
import threading
import uuid
from contextlib import aclosing
from datetime import UTC, datetime, timedelta

import pytest
from minio.error import S3Error

from relay_core import storage
from relay_core.db import open_pool, tx
from relay_core.storage import (
    ObjectNotFound,
    ObjectStore,
    ShareRecord,
    ShareRefused,
    StorageError,
    media_key,
    parse_key,
)
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/storage.py"
HOUR = timedelta(hours=1)
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8


def sync(test):
    """Run an async test on its own loop (the repo has no async test plugin)."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


def minio_settings() -> tuple[str, str, str]:
    endpoint = os.environ.get("RELAY_TEST_MINIO", "")
    if not endpoint:
        raise RuntimeError("RELAY_TEST_MINIO is not set: run the tests through scripts/check.sh")
    return endpoint, os.environ["RELAY_TEST_MINIO_ACCESS_KEY"], os.environ["RELAY_TEST_MINIO_SECRET_KEY"]


@pytest.fixture
def store():
    """A store on a bucket of its own, emptied and removed afterwards."""
    s = ObjectStore(*minio_settings(), bucket="relay-test-" + uuid.uuid4().hex[:12])
    asyncio.run(s.ensure_bucket())
    yield s
    for o in s._client.list_objects(s.bucket, recursive=True):
        s._client.remove_object(s.bucket, o.object_name)
    s._client.remove_bucket(s.bucket)


def store_over(client, like: ObjectStore) -> ObjectStore:
    return ObjectStore(*minio_settings(), bucket=like.bucket, client=client)


def s3_error(code: str) -> S3Error:
    return S3Error(None, code, "m", "r", "id", "host")


class Proxy:
    """The real client with chosen methods replaced."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


# ---- key layout --------------------------------------------------------------------------------------------------
def test_key_layout_and_its_inverse():
    assert media_key("42", "output", 0, "video/mp4") == "42/output/0.mp4"
    assert media_key("job-7_a", "input", 3, "image/jpeg") == "job-7_a/input/3.jpg"
    assert media_key("42", "debug", 1, "text/html; charset=utf-8") == "42/debug/1.html"
    assert media_key("42", "output", 0, "application/x-unknown") == "42/output/0.bin"
    assert parse_key("42/output/0.mp4") == storage.ParsedKey("42", "output", 0)
    for odd in ("stray.txt", "42/output", "42/other/0.mp4", "42/output/x.mp4", "42/output/0", "/output/0.mp4",
                "42/output/0.mp4/extra", "../output/0.mp4", "a b/output/0.mp4"):
        assert parse_key(odd) is None, odd


def test_key_parts_are_validated():
    for job_id in ("", "a/b", "..", "a b", "é"):
        with pytest.raises(ValueError):
            media_key(job_id, "output", 0, "video/mp4")
    with pytest.raises(ValueError):
        media_key("42", "secret", 0, "video/mp4")
    with pytest.raises(ValueError):
        media_key("42", "output", -1, "video/mp4")


# ---- client setup --------------------------------------------------------------------------------------------------
@mutation(SRC, "CONNECT_TIMEOUT_S = 3", "CONNECT_TIMEOUT_S = 300", expect="assert 300 == 3")
def test_http_client_has_the_contract_timeouts_and_one_retry():
    pool = storage.http_client()
    assert pool.connection_pool_kw["timeout"].connect_timeout == 3
    assert pool.connection_pool_kw["timeout"].read_timeout == 30
    assert pool.connection_pool_kw["retries"].total == 1


@mutation(SRC, "urllib3.Retry(total=1)", "urllib3.Retry(total=5)", expect="assert 5 == 1")
def test_one_retry_only():
    assert storage.http_client().connection_pool_kw["retries"].total == 1


def test_endpoint_forms_and_env(monkeypatch):
    assert storage.parse_endpoint("minio:9000") == ("minio:9000", False)
    assert storage.parse_endpoint("http://127.0.0.1:59000") == ("127.0.0.1:59000", False)
    assert storage.parse_endpoint("https://s3.example.com") == ("s3.example.com", True)
    endpoint, access, secret = minio_settings()
    s = ObjectStore.from_env({"MINIO_ENDPOINT": endpoint, "MINIO_ACCESS_KEY": access, "MINIO_SECRET_KEY": secret,
                              "MINIO_REGION": "eu-west-1"}, bucket="x")
    assert s.bucket == "x"
    with pytest.raises(StorageError, match="MINIO_SECRET_KEY"):
        ObjectStore.from_env({"MINIO_ENDPOINT": endpoint, "MINIO_ACCESS_KEY": access})


@mutation(SRC, "return await asyncio.to_thread(fn, *args, **kwargs)", "return fn(*args, **kwargs)",
           expect="seen and seen[0] is not threading.main_thread()")
def test_blocking_calls_leave_the_event_loop_thread(store):
    seen = []

    class Spy(Proxy):
        def stat_object(self, *a, **kw):
            seen.append(threading.current_thread())
            return self._real.stat_object(*a, **kw)

    spy = store_over(Spy(store._client), store)
    asyncio.run(spy.stat("1/output/0.mp4"))
    assert seen and seen[0] is not threading.main_thread()


@sync
async def test_an_unreachable_store_is_a_storage_error_without_the_address():
    dead = ObjectStore("127.0.0.1:1", "a", "b", bucket="relay-dead")
    with pytest.raises(StorageError, match="unreachable") as info:
        await dead.stat("1/output/0.mp4")
    assert "127.0.0.1" not in str(info.value)


# ---- bucket --------------------------------------------------------------------------------------------------------
@mutation(SRC, "if exc.code not in _BUCKET_EXISTS:", "if True:", expect="object store error: BucketAlreadyOwnedByYou")
@sync
async def test_bucket_creation_is_idempotent_and_survives_a_race(store):
    await store.ensure_bucket()
    await asyncio.gather(*(store.ensure_bucket() for _ in range(5)))

    class Racy(Proxy):
        """Another process wins the creation between the check and the create."""

        def bucket_exists(self, bucket):
            return False

        def make_bucket(self, bucket, *a, **kw):
            raise s3_error("BucketAlreadyOwnedByYou")

    await store_over(Racy(store._client), store).ensure_bucket()


@mutation(SRC, "if exc.code not in _BUCKET_EXISTS:", "if False:",
           expect="with pytest.raises(StorageError, match=\"AccessDenied\"):")
@sync
async def test_other_bucket_errors_are_not_swallowed(store):
    class Denied(Proxy):
        def bucket_exists(self, bucket):
            return False

        def make_bucket(self, bucket, *a, **kw):
            raise s3_error("AccessDenied")

        def stat_object(self, *a, **kw):
            raise s3_error("AccessDenied")

    with pytest.raises(StorageError, match="AccessDenied"):
        await store_over(Denied(store._client), store).ensure_bucket()
    with pytest.raises(StorageError, match="AccessDenied"):
        await store_over(Denied(store._client), store).stat("1/output/0.mp4")


# ---- upload and read -----------------------------------------------------------------------------------------------
@sync
async def test_upload_verifies_size_and_reads_back(store, tmp_path):
    info = await store.put_bytes("1/output/0.mp4", VIDEO, "video/mp4")
    assert info.size == len(VIDEO) and info.content_type == "video/mp4" and info.last_modified is not None
    assert await store.read("1/output/0.mp4") == VIDEO
    assert (await store.stat("1/output/0.mp4")).size == len(VIDEO)
    path = tmp_path / "out.png"
    path.write_bytes(b"\x89PNG" + os.urandom(100_000))
    info = await store.put_file("1/output/1.png", path, "image/png")
    assert info.size == path.stat().st_size and await store.read("1/output/1.png") == path.read_bytes()
    assert await store.stat("1/output/9.mp4") is None
    with pytest.raises(ObjectNotFound):
        await store.read("1/output/9.mp4")


@sync
async def test_only_layout_keys_are_written(store):
    with pytest.raises(ValueError):
        await store.put_bytes("stray.txt", b"x", "text/plain")
    with pytest.raises(ValueError):
        await store.put_bytes("../output/0.mp4", b"x", "video/mp4")


@mutation(SRC, "if info is None or info.size != size:", "if False:",
           expect="with pytest.raises(StorageError, match=\"truncated\"):")
@sync
async def test_truncated_upload_is_refused_and_removed(store):
    class Truncating(Proxy):
        def put_object(self, bucket, key, data, length, **kw):
            half = data.read(length // 2)
            return self._real.put_object(bucket, key, io.BytesIO(half), len(half), **kw)

    cut = store_over(Truncating(store._client), store)
    with pytest.raises(StorageError, match="truncated"):
        await cut.put_bytes("1/output/0.mp4", VIDEO, "video/mp4")
    assert await store.stat("1/output/0.mp4") is None             # the short object is not left behind to be served


@mutation(SRC, "if size <= 0:", "if size < 0:", expect="with pytest.raises(StorageError, match=\"empty\"):")
@sync
async def test_empty_file_is_refused(store, tmp_path):
    with pytest.raises(StorageError, match="empty"):
        await store.put_bytes("1/output/0.mp4", b"", "video/mp4")
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(StorageError, match="empty"):
        await store.put_file("1/output/0.mp4", empty, "video/mp4")
    assert await store.stat("1/output/0.mp4") is None


@mutation(SRC, "self._client.get_object, self.bucket, key, offset=offset, length=length)",
          "self._client.get_object, self.bucket, key)",
           expect="await store.read(\"1/output/0.mp4\", 10, 20) == VIDEO[10:30]")
@sync
async def test_range_reads(store):
    await store.put_bytes("1/output/0.mp4", VIDEO, "video/mp4")
    assert await store.read("1/output/0.mp4", 10, 20) == VIDEO[10:30]
    assert await store.read("1/output/0.mp4", len(VIDEO) - 5) == VIDEO[-5:]
    assert await store.read("1/output/0.mp4", 0, 4) == VIDEO[:4]
    with pytest.raises(StorageError):
        await store.read("1/output/0.mp4", len(VIDEO) + 10, 5)
    with pytest.raises(ValueError):
        await store.read("1/output/0.mp4", -1)


@mutation(SRC, 'list_prefix(job_id + "/")', "list_prefix(job_id)", expect="assert 3 == 2")
@sync
async def test_list_and_remove_by_job_prefix(store):
    for key in ("1/output/0.mp4", "1/input/0.png", "10/output/0.mp4"):
        await store.put_bytes(key, b"data", "video/mp4")
    assert sorted(o.key for o in await store.list_prefix("1/")) == ["1/input/0.png", "1/output/0.mp4"]
    assert await store.remove_job("1") == 2
    assert [o.key for o in await store.list_prefix("")] == ["10/output/0.mp4"]    # job 10 is not job 1's
    await store.remove("10/output/0.mp4")
    assert await store.list_prefix("") == []


# ---- share tokens ---------------------------------------------------------------------------------------------------
class Table:
    """The database's side of a share link, in memory."""

    def __init__(self):
        self.rows: dict[str, ShareRecord] = {}
        self.jobs: dict[str, str] = {}
        self.finds = 0

    def add(self, job_id: str, role: str, now: datetime, mime: str = "video/mp4") -> str:
        token = storage.new_share_token()
        self.rows[token] = ShareRecord(media_key(job_id, role, 0, mime), role, storage.share_expiry(role, now))
        self.jobs[token] = job_id
        return token

    async def find(self, token):
        self.finds += 1
        return self.rows.get(token)

    async def clear_job(self, job_id):
        tokens = [t for t, j in self.jobs.items() if j == job_id and t in self.rows]
        for t in tokens:
            del self.rows[t]
        return len(tokens)


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_share_tokens_are_128_bit_random_and_expiry_follows_the_role():
    tokens = {storage.new_share_token() for _ in range(200)}
    assert len(tokens) == 200 and all(len(t) >= 22 and set(t) <= storage._SAFE_ID for t in tokens)
    assert storage.share_expiry("input", NOW) == NOW + timedelta(days=7)
    assert storage.share_expiry("output", NOW) is None
    with pytest.raises(ValueError):
        storage.share_expiry("debug", NOW)


@mutation(SRC, "(expires is not None and now >= expires)", "False", expect="with pytest.raises(ShareRefused) as info:")
@sync
async def test_an_input_link_stops_working_at_its_expiry():
    table = Table()
    token = table.add("1", "input", NOW)
    expiry = NOW + timedelta(days=7)
    assert (await storage.resolve_share(table, token, now=expiry - timedelta(seconds=1))).role == "input"
    for later in (expiry, expiry + timedelta(days=1)):
        with pytest.raises(ShareRefused) as info:
            await storage.resolve_share(table, token, now=later)
        assert info.value.reason == "expired"


@mutation(SRC, '(record.role == "input" and expires is None)', "False",
           expect="with pytest.raises(ShareRefused, match=\"expired\"):")
@sync
async def test_an_input_row_without_an_expiry_fails_closed():
    table = Table()
    token = storage.new_share_token()
    table.rows[token] = ShareRecord("1/input/0.png", "input", None)
    with pytest.raises(ShareRefused, match="expired"):
        await storage.resolve_share(table, token, now=NOW)


@sync
async def test_an_output_link_does_not_expire():
    table = Table()
    token = table.add("1", "output", NOW)
    record = await storage.resolve_share(table, token, now=NOW + timedelta(days=3650))
    assert record.key == "1/output/0.mp4"


@mutation(SRC, "return await table.clear_job(job_id)", "return 0", expect="assert 0 == 2")
@sync
async def test_revoked_links_are_refused():
    table = Table()
    a, b = table.add("1", "input", NOW), table.add("1", "output", NOW)
    other = table.add("2", "output", NOW)
    assert await storage.revoke_job_shares(table, "1") == 2
    for token in (a, b):
        with pytest.raises(ShareRefused) as info:
            await storage.resolve_share(table, token, now=NOW)
        assert info.value.reason == "unknown_or_revoked"
    assert (await storage.resolve_share(table, other, now=NOW)).key == "2/output/0.mp4"


@mutation(SRC, "if record.role not in SHARED_ROLES:", "if False:",
           expect="with pytest.raises(ShareRefused, match=\"not_shareable\"):")
@sync
async def test_debug_media_is_never_served_by_link():
    table = Table()
    token = storage.new_share_token()
    table.rows[token] = ShareRecord("1/debug/0.html", "debug", None)         # even if a row somehow holds a token
    with pytest.raises(ShareRefused, match="not_shareable"):
        await storage.resolve_share(table, token, now=NOW)


@mutation(SRC, "if not 16 <= len(token) <= 64 or not set(token) <= _SAFE_ID:", "if False:",
           expect="Actual message: 'unknown_or_revoked'")
@sync
async def test_malformed_tokens_never_reach_the_table():
    table = Table()
    for junk in ("", "short", "a" * 65, "../../etc/passwd" + "x" * 10, "tok en" + "x" * 20, "é" * 20):
        with pytest.raises(ShareRefused, match="malformed"):
            await storage.resolve_share(table, junk, now=NOW)
    assert table.finds == 0
    with pytest.raises(ShareRefused, match="unknown_or_revoked"):
        await storage.resolve_share(table, storage.new_share_token(), now=NOW)


# ---- sweeps ----------------------------------------------------------------------------------------------------------
class Owners:
    """`owned_jobs`: the ids with a job row, recording what it was asked."""

    def __init__(self, owned: set[str], fail: bool = False):
        self.owned, self.fail, self.asked = owned, fail, []

    async def __call__(self, ids: set[str]) -> set[str]:
        self.asked.append(set(ids))
        if self.fail:
            raise ConnectionError("database down")
        return ids & self.owned


async def seed(store: ObjectStore) -> None:
    await store.put_bytes("jobA/output/0.mp4", b"owned", "video/mp4")
    await store.put_bytes("jobB/output/0.mp4", b"orphan", "video/mp4")
    await store.put_bytes("jobB/debug/0.html", b"orphan", "text/html")
    await store.put_bytes("jobC/input/0.png", b"orphan", "image/png")
    store._client.put_object(store.bucket, "stray.txt", io.BytesIO(b"x"), 1)      # outside the layout


async def keys(store: ObjectStore) -> list[str]:
    return sorted(o.key for o in await store.list_prefix(""))


@mutation(SRC, "now - o.last_modified >= min_age", "True", expect="young == [] and len(await keys(store)) == 5")
@sync
async def test_orphan_sweep_needs_age(store):
    await seed(store)
    soon = datetime.now(UTC) + timedelta(minutes=1)
    young = await storage.sweep_orphans(store, Owners({"jobA"}), min_age=HOUR, now=soon)
    assert young == [] and len(await keys(store)) == 5


@mutation(SRC, "gone = [o.key for o, p in old if p.job_id not in owned]", "gone = [o.key for o, p in old]",
           expect="sorted(gone) == [\"jobB/debug/0.html\", \"jobB/output/0.mp4\", \"jobC/input/0.png\"]")
@sync
async def test_orphan_sweep_needs_no_owner(store):
    await seed(store)
    owners = Owners({"jobA"})
    gone = await storage.sweep_orphans(store, owners, min_age=HOUR, now=datetime.now(UTC) + 2 * HOUR)
    assert sorted(gone) == ["jobB/debug/0.html", "jobB/output/0.mp4", "jobC/input/0.png"]
    assert await keys(store) == ["jobA/output/0.mp4", "stray.txt"]       # owned and unrecognised objects stay
    assert owners.asked == [{"jobA", "jobB", "jobC"}]


@mutation(SRC, "if not enabled:", "if False:",
           expect="await storage.sweep_orphans(store, owners, min_age=HOUR, now=datetime.now(UTC) + 9 * HOUR,")
@sync
async def test_orphan_sweep_can_be_switched_off(store):
    await seed(store)
    owners = Owners(set())
    assert await storage.sweep_orphans(store, owners, min_age=HOUR, now=datetime.now(UTC) + 9 * HOUR,
                                       enabled=False) == []
    assert len(await keys(store)) == 5 and owners.asked == []


@sync
async def test_orphan_sweep_removes_nothing_when_the_owner_lookup_fails(store):
    await seed(store)
    with pytest.raises(ConnectionError):
        await storage.sweep_orphans(store, Owners(set(), fail=True), min_age=HOUR,
                                    now=datetime.now(UTC) + 9 * HOUR)
    assert len(await keys(store)) == 5


@mutation(SRC, "p.role in roles and now - o.last_modified >= retention", "now - o.last_modified >= retention",
           expect="gone == [\"jobB/debug/0.html\"]")
@mutation(SRC, "now - o.last_modified >= retention", "True",
           expect="await storage.sweep_retention(store, retention=timedelta(days=30), now=start + timedelta(")
@sync
async def test_retention_sweep_removes_only_old_debug_media(store):
    await seed(store)
    start = datetime.now(UTC)
    assert await storage.sweep_retention(store, retention=timedelta(days=30), now=start + timedelta(days=29)) == []
    gone = await storage.sweep_retention(store, retention=timedelta(days=30), now=start + timedelta(days=31))
    assert gone == ["jobB/debug/0.html"]
    assert len(await keys(store)) == 4                                    # outputs and inputs are kept indefinitely


@mutation(SRC, "assert_no_tx()\n        try:", "try:",
           expect="with pytest.raises(RuntimeError, match=\"foreign I/O\"):")
def test_object_store_calls_inside_a_database_transaction_are_refused(store, db):
    async def main():
        pool = await open_pool(db.dsn)
        try:
            async with tx(pool):
                with pytest.raises(RuntimeError, match="foreign I/O"):
                    await store.put_bytes("1/output/0.mp4", VIDEO, "video/mp4")
                with pytest.raises(RuntimeError, match="foreign I/O"):
                    await store.stat("1/output/0.mp4")
            assert await store.stat("1/output/0.mp4") is None          # nothing was written; works outside a tx
        finally:
            await pool.close()

    asyncio.run(main())


def test_other_client_exceptions_become_storage_errors(store):
    from minio.error import ServerError

    class Broken(Proxy):
        def stat_object(self, *a, **kw):
            raise ServerError("boom", 500)

    with pytest.raises(StorageError, match="unreachable"):
        asyncio.run(store_over(Broken(store._client), store).stat("1/output/0.mp4"))


# ---- streaming ---------------------------------------------------------------------------------------------------
class SpyResponse:
    """Wraps a real response: records chunk sizes and whether the connection went back to the pool."""

    def __init__(self, real, log):
        self._real, self.log = real, log

    def stream(self, size):
        self.log["asked"] = size
        for part in self._real.stream(size):
            self.log["sizes"].append(len(part))
            yield part

    def close(self):
        self.log["closed"] = True
        self._real.close()

    def release_conn(self):
        self.log["released"] = True
        self._real.release_conn()


def spied(store):
    log = {"sizes": [], "closed": False, "released": False}

    class Spy(Proxy):
        def get_object(self, *a, **kw):
            return SpyResponse(self._real.get_object(*a, **kw), log)

    return store_over(Spy(store._client), store), log


BIG = os.urandom(3 * 1024 * 1024 + 123)


@mutation(SRC, "resp.stream(chunk_size)", "resp.stream(10**9)", expect="assert (1 >= (3145851 // (64 * 1024)))")
@sync
async def test_a_large_object_streams_in_bounded_chunks(store):
    await store.put_bytes("1/output/0.mp4", BIG, "video/mp4")
    spy, log = spied(store)
    parts = [part async for part in spy.stream("1/output/0.mp4", chunk_size=64 * 1024)]
    assert b"".join(parts) == BIG
    assert len(parts) >= len(BIG) // (64 * 1024) and max(len(p) for p in parts) <= 64 * 1024
    assert log["asked"] == 64 * 1024 and log["released"] and log["closed"]


@sync
async def test_a_range_streams_only_that_range(store):
    await store.put_bytes("1/output/0.mp4", BIG, "video/mp4")
    spy, log = spied(store)
    start, size = 1_000_000, 700_000
    got = b"".join([p async for p in spy.stream("1/output/0.mp4", start, size, chunk_size=100_000)])
    assert got == BIG[start:start + size] and max(log["sizes"]) <= 100_000
    assert sum(log["sizes"]) == size                           # nothing beyond the range was pulled
    assert await store.read("1/output/0.mp4", start, 50) == BIG[start:start + 50]
    with pytest.raises(ObjectNotFound):
        [p async for p in store.stream("1/output/9.mp4")]
    with pytest.raises(ValueError):
        [p async for p in store.stream("1/output/0.mp4", chunk_size=0)]


@mutation(SRC, "asyncio.to_thread(self._release, resp)", "asyncio.to_thread(lambda r: None, resp)",
           expect="log[\"released\"] and log[\"closed\"] and len(log[\"sizes\"]) < 10")
@sync
async def test_the_connection_is_released_when_the_reader_stops_early(store):
    await store.put_bytes("1/output/0.mp4", BIG, "video/mp4")
    spy, log = spied(store)
    it = spy.stream("1/output/0.mp4", chunk_size=64 * 1024)
    assert len(await anext(it)) == 64 * 1024
    assert not log["released"]
    await it.aclose()                                          # a client hanging up mid-download
    assert log["released"] and log["closed"] and len(log["sizes"]) < 10
    spy2, log2 = spied(store)
    with pytest.raises(RuntimeError, match="reader failed"):
        async with aclosing(spy2.stream("1/output/0.mp4", chunk_size=64 * 1024)) as parts:
            async for _ in parts:
                raise RuntimeError("reader failed")
    assert log2["released"]
