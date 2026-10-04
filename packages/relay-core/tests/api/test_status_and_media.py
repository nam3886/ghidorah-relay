"""`GET /m/{token}` (contracts §8, §9; I12): Range in bounded chunks, filenames, expired and revoked links, debug media
never public, and the upstream connection released when the client goes away."""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

import pytest
from apikit import run_app

from relay_core import storage
from relay_core.api.filenames import content_disposition, media_filename
from relay_core.api.routes_media import RangeNotSatisfiable, parse_range
from relay_core.jobs.shares import PgShareTable
from relay_core.storage import CHUNK_SIZE, ObjectStore
from relay_core.testing.mutation import mutation

MEDIA = "packages/relay-core/src/relay_core/api/routes_media.py"
NAMES = "packages/relay-core/src/relay_core/api/filenames.py"
STORAGE = "packages/relay-core/src/relay_core/storage.py"
SHARES = "packages/relay-core/src/relay_core/jobs/shares.py"
DATA = bytes(range(256)) * 3072                       # 786 432 bytes: three chunks of CHUNK_SIZE
WHEN = datetime(2026, 10, 3, 9, 30, 15, tzinfo=UTC)


async def plant(env, db, *, role="output", idx=0, data=DATA, mime="video/mp4", expires="NULL", token=True,
                job_id=None, prompt="a prompt", params="{}", kind="video") -> tuple[uuid.UUID, str, str]:
    """A job (or one more output of `job_id`), its stored object and a `job_media` row. Returns (job, token, key)."""
    if job_id is None:
        job_id = db.job(db.key(), kind=kind)
        db.run("UPDATE jobs SET prompt = %s, model = 'fake-video', params = %s::jsonb WHERE id = %s",
               prompt, params, job_id)
    key = storage.media_key(str(job_id), role, idx, mime)
    await env.store.put_bytes(key, data, mime)
    tok = storage.new_share_token() if token else None
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, share_token, share_expires_at) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)" % ("%s", "%s", "%s", "%s", "%s", "%s", "%s", expires),
           job_id, role, idx, key, mime, len(data), tok)
    return job_id, tok, key


class SpyStream:
    """Records every `store.stream` call and the size of each chunk it yields; `read` is forbidden."""

    def __init__(self, store: ObjectStore):
        self.store, self.calls, self.chunks = store, [], []
        self._real = ObjectStore.stream.__get__(store)

    def __enter__(self):
        async def stream(key, offset=0, length=0, chunk_size=CHUNK_SIZE):
            self.calls.append((key, offset, length))
            async for part in self._real(key, offset, length, chunk_size):
                self.chunks.append(len(part))
                yield part

        async def read(*a, **k):
            raise AssertionError("the media route read a whole object into memory")

        self.store.stream, self.store.read = stream, read
        return self

    def __exit__(self, *exc):
        del self.store.stream, self.store.read


# ---- whole file, headers, Range --------------------------------------------------------------------------------
@mutation(MEDIA, 'OUTPUT_CACHE if record.role == "output" else INPUT_CACHE', "INPUT_CACHE",
          expect="an output link is cacheable for a day")
@mutation(MEDIA, '"Accept-Ranges": "bytes"', '"Accept-Ranges": "none"', expect="the server says it takes ranges")
def test_an_output_link_serves_the_whole_file_inline_and_cacheable(db):
    async def scenario(env):
        job_id, token, _ = await plant(env, db, params='{"duration": 5, "ratio": "16:9"}')
        return await env.client.get("/m/" + token, headers={"Origin": "https://app.example.test"})

    r = run_app(db, scenario)
    assert r.status_code == 200 and r.content == DATA
    assert r.headers["content-type"] == "video/mp4" and r.headers["content-length"] == str(len(DATA))
    assert r.headers["accept-ranges"] == "bytes", "the server says it takes ranges"
    assert r.headers["cache-control"] == "public, max-age=86400", "an output link is cacheable for a day"
    assert r.headers["content-disposition"].startswith('inline; filename="a_prompt_5s.mp4"')
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["access-control-allow-origin"] == "*"          # public media, no credentials


@mutation(MEDIA, "store.stream(key, first, last - first + 1), status_code=206",
          "store.stream(key, first, 0), status_code=206", expect="stream was asked for the range, nothing more")
@mutation(MEDIA, "store.stream(key, first, last - first + 1), status_code=206",
          "store.stream(key, 0, last - first + 1), status_code=206",
          expect="stream was asked for the range, nothing more")
def test_a_range_is_read_in_bounded_chunks_and_never_whole(db):
    async def scenario(env):
        _, token, key = await plant(env, db)
        with SpyStream(env.store) as spy:
            try:
                r = await env.client.get("/m/" + token, headers={"Range": "bytes=100-599999"})
            except Exception as exc:                   # a body longer than its Content-Length breaks the exchange
                r = exc
        return r, spy, key

    r, spy, key = run_app(db, scenario)
    assert spy.calls == [(key, 100, 599900)], "stream was asked for the range, nothing more"
    assert r.status_code == 206 and r.content == DATA[100:600000]
    assert r.headers["content-range"] == "bytes 100-599999/%d" % len(DATA)
    assert r.headers["content-length"] == "599900"
    assert len(spy.chunks) >= 3 and max(spy.chunks) <= CHUNK_SIZE, "no chunk is larger than CHUNK_SIZE"


def test_a_whole_file_is_streamed_in_chunks_too(db):
    async def scenario(env):
        _, token, key = await plant(env, db)
        with SpyStream(env.store) as spy:
            r = await env.client.get("/m/" + token)
        return r, spy, key

    r, spy, key = run_app(db, scenario)
    assert r.content == DATA and spy.calls == [(key, 0, 0)]
    assert len(spy.chunks) == 3 and max(spy.chunks) <= CHUNK_SIZE


def test_range_forms_and_the_ones_that_are_not_honoured():
    size = 1000
    assert parse_range(None, size) is None and parse_range("", size) is None
    assert parse_range("bytes=0-99", size) == (0, 99)
    assert parse_range("bytes=900-", size) == (900, 999)
    assert parse_range("bytes=-100", size) == (900, 999)
    assert parse_range("bytes=-5000", size) == (0, 999)                     # a longer suffix is the whole object
    assert parse_range("bytes=990-5000", size) == (990, 999)                # the end is clamped
    assert parse_range("bytes=0-0,5-9", size) is None                        # several ranges: the whole object
    assert parse_range("items=0-1", size) is None and parse_range("bytes=-", size) is None
    assert parse_range("bytes=9-3", size) is None                            # first after last: ignored
    assert parse_range("bytes=0-5", 0) is None                               # nothing to cut
    for past in ("bytes=1000-", "bytes=1000-1200", "bytes=-0"):
        with pytest.raises(RangeNotSatisfiable):
            parse_range(past, size)


@mutation(MEDIA, "if start >= size:", "if start > size:", expect="the last byte plus one is past the end")
def test_a_range_past_the_end_is_416_with_the_size(db):
    async def scenario(env):
        _, token, _ = await plant(env, db, data=b"x" * 50)
        past = await env.client.get("/m/" + token, headers={"Range": "bytes=50-"})
        last = await env.client.get("/m/" + token, headers={"Range": "bytes=49-"})
        junk = await env.client.get("/m/" + token, headers={"Range": "bytes=0-1,4-5"})
        return past, last, junk

    past, last, junk = run_app(db, scenario)
    assert (past.status_code, past.headers.get("content-range")) == (416, "bytes */50"), \
        "the last byte plus one is past the end"
    assert last.status_code == 206 and last.content == b"x"
    assert junk.status_code == 200 and junk.content == b"x" * 50


# ---- the client goes away ---------------------------------------------------------------------------------------
def scope_of(path: str, spec_version: str) -> dict:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": spec_version}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "headers": [(b"host", b"relay.test")], "client": ("203.0.113.7", 4321),
            "server": ("relay.test", 80)}


class Releases:
    """Every upstream response the store let go of, and whether it had been closed (not handed back half read)."""

    def __init__(self, store: ObjectStore):
        self.store, self.closed = store, []
        self._real = ObjectStore._release

    def __enter__(self):
        def release(resp) -> None:
            self._real(resp)
            self.closed.append(resp.isclosed())

        self.store._release = release
        return self

    def __exit__(self, *exc):
        del self.store._release

    async def settled(self, n: int = 1) -> None:
        for _ in range(200):
            if len(self.closed) >= n:
                return
            await asyncio.sleep(0.02)


def test_a_hang_up_while_a_chunk_is_read_closes_the_upstream_connection(db):
    """Servers older than ASGI 2.4 cancel the response when the client disconnects."""
    async def scenario(env):
        _, token, _ = await plant(env, db)
        sent, requested, first = [], False, asyncio.Event()

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await first.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                first.set()

        with Releases(env.store) as rel:
            await asyncio.wait_for(env.app(scope_of("/m/" + token, "2.0"), receive, send), 10)
            await rel.settled()
        return sent, rel.closed

    sent, closed = run_app(db, scenario)
    assert closed == [True], "the connection was released once, closed"
    assert sum(len(m.get("body", b"")) for m in sent) < len(DATA)       # it stopped early


@mutation(MEDIA, "async with contextlib.aclosing(self.body_iterator):", "async with contextlib.nullcontext():",
          expect="released before the failed send came back")
def test_a_failed_send_closes_the_upstream_connection_before_it_returns(db):
    """ASGI 2.4 servers raise from `send` instead; the body iterator is then left suspended unless it is closed."""
    async def scenario(env):
        _, token, _ = await plant(env, db)
        chunks = 0

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal chunks
            if message["type"] == "http.response.body" and message.get("body"):
                chunks += 1
                if chunks == 2:
                    raise OSError("broken pipe")

        with Releases(env.store) as rel:
            with contextlib.suppress(Exception):                # Starlette raises ClientDisconnect
                await env.app(scope_of("/m/" + token, "2.4"), receive, send)
            return list(rel.closed), chunks            # no await since the app returned: no finalizer ran yet

    closed, chunks = run_app(db, scenario)
    assert chunks == 2
    assert closed == [True], "released before the failed send came back"


# ---- I12 --------------------------------------------------------------------------------------------------------
@mutation(STORAGE, "if record.role not in SHARED_ROLES:", "if False:", expect="a debug object is never public")
def test_debug_media_is_never_public_even_with_a_token_on_the_row(db):
    async def scenario(env):
        _, token, _ = await plant(env, db, role="debug", mime="image/png", data=b"png")
        try:
            return await env.client.get("/m/" + token)
        finally:                                              # the checker refuses such a row; undo the plant
            db.run("UPDATE job_media SET share_token = NULL WHERE role = 'debug'")

    r = run_app(db, scenario)
    assert r.status_code == 404, "a debug object is never public"
    assert r.json()["error"]["code"] == "resource.not_found" and b"png" not in r.content


@mutation(STORAGE, "or (expires is not None and now >= expires)", "or False",
          expect="an input link past its expiry is gone")
def test_an_input_link_works_until_its_expiry_and_not_after(db):
    async def scenario(env):
        _, live, _ = await plant(env, db, role="input", mime="image/png", data=b"ref",
                                 expires="now() + interval '1 day'")
        _, dead, _ = await plant(env, db, role="input", mime="image/png", data=b"ref",
                                 expires="now() - interval '1 minute'")
        return await env.client.get("/m/" + live), await env.client.get("/m/" + dead)

    live, dead = run_app(db, scenario)
    assert live.status_code == 200 and live.content == b"ref" and live.headers["cache-control"] == "private, no-cache"
    assert dead.status_code == 404, "an input link past its expiry is gone"


@mutation(SHARES, "UPDATE job_media SET share_token = NULL WHERE job_id = %s AND share_token IS NOT NULL ",
          "UPDATE job_media SET share_token = share_token WHERE job_id = %s AND share_token IS NOT NULL ",
          expect="a revoked link is gone")
def test_a_revoked_link_is_404_for_every_media_of_the_job(db):
    async def scenario(env):
        job_id, t0, _ = await plant(env, db)
        _, t1, _ = await plant(env, db, job_id=job_id, idx=1)
        _, ref, _ = await plant(env, db, job_id=job_id, role="input", mime="image/png", data=b"ref",
                                expires="now() + interval '1 day'")
        before = [(await env.client.get("/m/" + t)).status_code for t in (t0, t1, ref)]
        cleared = await storage.revoke_job_shares(PgShareTable(env.pool), str(job_id))
        after = [(await env.client.get("/m/" + t)).status_code for t in (t0, t1, ref)]
        return before, cleared, after

    before, cleared, after = run_app(db, scenario)
    assert before == [200, 200, 200] and cleared == 3
    assert after == [404, 404, 404], "a revoked link is gone"


def test_unknown_malformed_and_orphaned_tokens_are_404_in_the_openai_shape(db):
    async def scenario(env):
        _, token, key = await plant(env, db)
        await env.store.remove(key)                         # the row survives, the object is gone
        gone = await env.client.get("/m/" + token)
        unknown = await env.client.get("/m/" + storage.new_share_token())
        short = await env.client.get("/m/abc")
        return gone, unknown, short

    for r in run_app(db, scenario):
        assert r.status_code == 404 and r.json()["error"]["code"] == "resource.not_found"


# ---- filenames --------------------------------------------------------------------------------------------------
def name(pattern="{prompt}_{duration}", **kw) -> str:
    base = {"prompt": "a prompt", "model": "m", "media_type": "video", "mime": "video/mp4", "idx": 0,
            "job_id": "0123456789abcdef", "created_at": WHEN, "duration": 5}
    return media_filename(pattern, **{**base, **kw})


def test_the_tags_fill_in_and_forbidden_characters_become_underscores():
    assert name() == "a_prompt_5s.mp4"
    assert name("{prompt}") == "a_prompt.mp4"
    assert name("{PROMPT}_{Model}_{ratio}_{resolution}_{id}", model="veo 3", ratio="16:9", resolution="720p") \
        == "a_prompt_veo_3_16x9_720p_01234567.mp4"
    hostile = name("{prompt}", prompt='a/b\\c:d*e?f"g<h>i|j\x00k\nl')
    assert not set(hostile[:-4]) & set('/\\:*?"<>|\x00\n') and hostile == "a_b_c_d_e_f_g_h_i_j_k_l.mp4"
    assert name("{bogus}_{id}") == "{bogus}_01234567.mp4"                    # an unknown tag stays text
    assert len(name("{prompt}", prompt="x" * 500)) == 45 + len(".mp4")        # one tag value is capped
    assert len(name("a" * 400)) == 120 + len(".mp4")                          # and so is the whole name


def test_an_empty_name_falls_back_to_the_media_type_and_the_time():
    local = WHEN.astimezone()
    stamp = local.strftime("%Y-%m-%d_%H-%M-%S")
    assert name("{prompt}", prompt="") == "video.mp4"
    assert name("{model}", prompt="", model="") == "video_%s.mp4" % stamp
    assert name("{ratio}", prompt="", ratio="", media_type="image", mime="image/png") == "image_%s_01.png" % stamp


@mutation(NAMES, "    elif idx:\n        stem = ", "    elif False:\n        stem = ",
          expect="the second output does not overwrite the first")
def test_the_nth_output_gets_a_suffix_so_downloads_do_not_overwrite_each_other():
    names = [name(idx=i) for i in range(3)]
    assert names == ["a_prompt_5s.mp4", "a_prompt_5s_2.mp4", "a_prompt_5s_3.mp4"], \
        "the second output does not overwrite the first"
    images = [name("{prompt}", idx=i, media_type="image", mime="image/png") for i in range(2)]
    assert images == ["a_prompt_01.png", "a_prompt_02.png"]
    assert name(mime="image/jpeg", media_type="image").endswith("_01.jpg")


def test_content_disposition_carries_an_ascii_fallback_and_the_utf8_name():
    assert content_disposition("a_prompt_5s.mp4") == \
        "inline; filename=\"a_prompt_5s.mp4\"; filename*=UTF-8''a_prompt_5s.mp4"
    vi = content_disposition("Sóng_biển_10s.mp4")
    assert 'filename="Sng_bin_10s.mp4"' in vi and "filename*=UTF-8''S%C3%B3ng_bi%E1%BB%83n_10s.mp4" in vi
    assert content_disposition("日本語.mp4").startswith('inline; filename="media.mp4"')
    assert "'" not in content_disposition("it's.mp4").split("filename*=UTF-8''")[1]       # RFC 5987 attr-char


@mutation(MEDIA, 'settings.get("filename_pattern")', '"{prompt}_{duration}"',
          expect="the name follows the filename_pattern setting")
def test_the_download_name_follows_the_setting_and_numbers_the_outputs(db):
    async def scenario(env):
        job_id, t0, _ = await plant(env, db, params='{"duration": 10, "ratio": "9:16"}', prompt="Sóng biển")
        _, t1, _ = await plant(env, db, job_id=job_id, idx=1)
        default = (await env.client.get("/m/" + t0)).headers["content-disposition"]
        await env.settings.set("filename_pattern", "{model}-{ratio}")
        first = (await env.client.get("/m/" + t0)).headers["content-disposition"]
        second = (await env.client.get("/m/" + t1)).headers["content-disposition"]
        return default, first, second

    default, first, second = run_app(db, scenario)
    assert "filename*=UTF-8''S%C3%B3ng_bi%E1%BB%83n_10s.mp4" in default
    assert first.startswith('inline; filename="fake-video-9x16.mp4"'), "the name follows the filename_pattern setting"
    assert second.startswith('inline; filename="fake-video-9x16_2.mp4"')



@mutation(MEDIA, '@router.get("/m/{token}")', '@router.api_route("/m/{token}", methods=["GET", "HEAD"])',
          expect="HEAD on a media link is 405")
def test_head_on_a_media_link_is_405_with_allow_get(db):
    async def scenario(env):
        _, token, _ = await plant(env, db)
        return await env.client.head("/m/" + token)

    r = run_app(db, scenario)
    assert r.status_code == 405, "HEAD on a media link is 405: %d" % r.status_code
    assert r.headers["allow"] == "GET"
