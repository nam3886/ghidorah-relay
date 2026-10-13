"""The evidence sink of a worker (contracts 5.2 `evidence`): debug files of a job, redacted, capped, never public, and
wired in the process that `serve` builds."""

import asyncio

import pytest
from accountkit import keys as stored_keys
from accountkit import object_store
from jobkit import account, create, key, run
from test_worker import env_for

from relay_core import storage
from relay_core.jobs import evidence, sweeps
from relay_core.jobs import worker as worker_module
from relay_core.jobs.evidence import job_sink
from relay_core.testing.fake_provider import FakeEngine, FakeSpec
from relay_core.testing.mutation import mutation

EVIDENCE = "packages/relay-core/src/relay_core/jobs/evidence.py"
WORKER = "packages/relay-core/src/relay_core/jobs/worker.py"
STORAGE = "packages/relay-core/src/relay_core/storage.py"
PLANTED = "https://operator:hunter2-planted-secret@example.com/path"
PAGE = ("<html><body>sent <a href='%s'>link</a></body></html>" % PLANTED).encode()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def objects():
    with object_store() as s:
        yield s


def rows(db, job_id) -> list[tuple]:
    return db.all("SELECT idx, object_key, mime, size, upstream_id, share_token FROM job_media "
                  "WHERE job_id = %s AND role = 'debug' ORDER BY idx", job_id)


def one_job(db):
    k = key(db)
    account(db, left=100)
    return k, run(db, lambda p: create(p, k, "a cat on a roof"))


def keep_all(db, objects, job_id, files):
    async def scenario(p):
        sink = job_sink(p, objects)
        for name, data, mime in files:
            await sink(str(job_id), name, data, mime)

    run(db, scenario)


# ---- the process wires a sink ---------------------------------------------------------------------------------------
@mutation(WORKER, "evidence_sink=job_sink(db, store),", "evidence_sink=None,",
          expect="the worker that serve builds has no evidence sink")
def test_the_worker_that_serve_builds_has_an_evidence_sink(db, monkeypatch):
    db.pin("UTC")
    seen = {}

    class Recorder:
        def __init__(self, *args, **kw):
            seen.update(kw)

        async def run(self, *, install_signals=True):
            return 0

    monkeypatch.setattr(worker_module, "Worker", Recorder)
    assert asyncio.run(worker_module.serve(FakeSpec(), lambda s, st: FakeEngine(), env_for(db, BILLING_TZ="UTC"),
                                           install_signals=False)) == 0
    assert callable(seen.get("evidence_sink")), "the worker that serve builds has no evidence sink"


# ---- what is stored -------------------------------------------------------------------------------------------------
@mutation(EVIDENCE, "data = await asyncio.to_thread(_redacted, data)", "data = data",
          expect="a planted secret survived in the stored page")
@mutation(EVIDENCE, "if base in KEPT_AS_IS:", "if False:", expect="a screenshot was not kept as it is")
def test_a_page_is_redacted_before_it_is_stored_and_a_screenshot_is_kept_as_it_is(db, objects):
    _, made = one_job(db)
    keep_all(db, objects, made.job_id, [("ui-error-send-170.html", PAGE, "text/html; charset=utf-8"),
                                        ("ui-error-send-170.png", PNG, "image/png")])
    both = rows(db, made.job_id)
    assert len(both) == 2, "a screenshot was not kept as it is"
    page, shot = both
    stored = asyncio.run(objects.read(page[1])).decode()
    assert "hunter2-planted-secret" not in stored, "a planted secret survived in the stored page"
    assert "example.com/path" in stored, "the page itself is kept"
    assert asyncio.run(objects.read(shot[1])) == PNG, "pixels are not rewritten"


def test_a_stored_file_is_a_debug_row_with_the_name_in_its_key_and_nothing_public(db, objects):
    _, made = one_job(db)
    keep_all(db, objects, made.job_id,
             [("After Send 99.jpg", PNG, "image/jpeg"), ("net.json", b"{}", "application/json")])
    first, second = rows(db, made.job_id)
    assert first[:5] == (0, "%s/debug/0-after_send_99.jpg" % made.job_id, "image/jpeg", len(PNG), None)
    assert second[1] == "%s/debug/1-net.json" % made.job_id and second[4] is None
    assert first[5] is None and second[5] is None, "a debug file never has a share token"
    assert storage.parse_key(first[1]) == storage.ParsedKey(str(made.job_id), "debug", 0), "the sweeps must know it"
    assert sorted(stored_keys(objects)) == sorted([first[1], second[1]])


@mutation(STORAGE, 'if dash and (parts[1] != "debug" or not label or not set(label) <= _SAFE_NAME):',
          "if False:", expect="a named key of another role was accepted")
def test_only_a_debug_key_may_carry_a_name_and_only_of_safe_characters():
    job = "0f9e8d7c"
    assert storage.parse_key("%s/debug/3-ui_error-170.png" % job) == storage.ParsedKey(job, "debug", 3)
    for bad in ("%s/output/3-x.png", "%s/input/3-x.png", "%s/debug/3-.png", "%s/debug/3-A b.png",
                "%s/debug/-x.png"):
        assert storage.parse_key(bad % job) is None, "a named key of another role was accepted: %r" % bad
    assert storage.media_key(job, "debug", 2, "text/html", "../Bad Name!.html") == "%s/debug/2-___bad_name_.html" % job
    assert storage.media_key(job, "output", 2, "video/mp4", "name") == "%s/output/2.mp4" % job


# ---- the size guard -------------------------------------------------------------------------------------------------
@mutation(EVIDENCE, "if count >= MAX_FILES:", "if False:", expect="a job kept more than its file limit")
def test_a_job_keeps_at_most_the_file_limit_and_the_rest_is_dropped(db, objects, monkeypatch, caplog):
    monkeypatch.setattr(evidence, "MAX_FILES", 3)
    _, made = one_job(db)
    keep_all(db, objects, made.job_id, [("shot-%d.png" % i, PNG, "image/png") for i in range(5)])
    assert len(rows(db, made.job_id)) == 3, "a job kept more than its file limit"
    assert len(stored_keys(objects)) == 3 and "already holds 3 files" in caplog.text


@mutation(EVIDENCE, "if len(data) > MAX_BYTES:", "if False:", expect="a file over the size limit was kept")
def test_a_file_over_the_size_limit_is_dropped_whole(db, objects, monkeypatch, caplog):
    monkeypatch.setattr(evidence, "MAX_BYTES", 100)
    _, made = one_job(db)
    keep_all(db, objects, made.job_id, [("big.png", b"x" * 101, "image/png"), ("small.png", PNG[:50], "image/png")])
    kept = rows(db, made.job_id)
    assert len(kept) == 1, "a file over the size limit was kept"
    [only] = kept
    assert only[1].endswith("-small.png") and only[3] == 50, "a file over the size limit was kept"
    assert "over the 100 limit" in caplog.text


@mutation(EVIDENCE, 'base.endswith(("+json", "+xml"))', 'base.endswith(("+zzz",))',
          expect="a text type was not stored")
@mutation(EVIDENCE, 'if kind == "text":', "if False:", expect="a secret survived in the stored network log")
def test_every_text_like_type_is_redacted_and_an_svg_is_text(db, objects):
    har = (b'{"log":{"entries":[{"request":{"headers":[{"name":"Cookie","value":"sessionid=planted-cookie-1234"},'
           b'{"name":"Authorization","value":"Bearer planted-token-5678"}]}}]}}')
    _, made = one_job(db)
    types = ("application/har+json", "application/x-ndjson", "application/vnd.api+json", "text/plain; charset=utf-8",
             "application/xml", "image/svg+xml", "TEXT/HTML")
    keep_all(db, objects, made.job_id, [("net-%d" % i, har, t) for i, t in enumerate(types)])
    stored = rows(db, made.job_id)
    assert len(stored) == len(types), "a text type was not stored"
    for row in stored:
        text = asyncio.run(objects.read(row[1])).decode()
        assert "planted-cookie-1234" not in text and "planted-token-5678" not in text, \
            "a secret survived in the stored network log (%r)" % row[2]


def break_the_stat(monkeypatch, objects):
    """The upload works and the call that follows it fails: the object may be in the bucket."""
    async def stat_fails(key):
        raise storage.StorageError("the store stopped answering")

    monkeypatch.setattr(objects, "stat", stat_fails)


def test_a_failed_store_keeps_its_row_and_the_retention_sweep_takes_row_and_object_together(db, objects, monkeypatch):
    break_the_stat(monkeypatch, objects)
    _, made = one_job(db)
    keep_all(db, objects, made.job_id, [("a.png", PNG, "image/png")])
    monkeypatch.undo()
    assert len(rows(db, made.job_id)) == 1, "the row of a failed store was removed: the sweep cannot find it"
    assert len(stored_keys(objects)) == 1, "premise: the upload landed"
    db.run("UPDATE job_media SET created_at = created_at - interval '31 days' WHERE job_id = %s", made.job_id)
    run(db, lambda p: sweeps.sweep_retention(p, objects, retention_days=30))
    assert rows(db, made.job_id) == [] and stored_keys(objects) == [], "the sweep left the row or the object"


def hang_everything(monkeypatch, objects):
    async def hang(*args, **kw):
        await asyncio.sleep(3600)

    for method in ("put_bytes", "stat", "remove", "read"):
        monkeypatch.setattr(objects, method, hang)


@mutation(EVIDENCE, "async with asyncio.timeout(TIMEOUT_S):", "async with asyncio.timeout(None):",
          expect="the sink hung on a store that does not answer")
def test_a_store_that_hangs_on_everything_is_given_up_on_and_the_caller_goes_on(db, objects, monkeypatch, caplog):
    hang_everything(monkeypatch, objects)
    monkeypatch.setattr(evidence, "TIMEOUT_S", 0.2)
    _, made = one_job(db)

    async def scenario(p):
        try:
            await asyncio.wait_for(job_sink(p, objects)(str(made.job_id), "a.png", PNG, "image/png"), 5)
        except TimeoutError:
            pytest.fail("the sink hung on a store that does not answer")

    run(db, scenario)
    assert "the store did not answer in 0.2 s" in caplog.text


@mutation(EVIDENCE, "    except TimeoutError:", "    except (TimeoutError, asyncio.CancelledError):",
          expect="a cancel from outside was swallowed")
def test_a_cancel_from_outside_goes_through_the_sink_at_once(db, objects, monkeypatch):
    hang_everything(monkeypatch, objects)
    _, made = one_job(db)

    async def scenario(p):
        task = asyncio.ensure_future(job_sink(p, objects)(str(made.job_id), "a.png", PNG, "image/png"))
        await asyncio.sleep(0.5)                  # the store call is now hanging
        task.cancel()
        try:
            await asyncio.wait_for(task, 5)
        except asyncio.CancelledError:
            return "cancelled"
        except TimeoutError:
            return "hung"
        return "swallowed"

    assert run(db, scenario) == "cancelled", "a cancel from outside was swallowed or hung"


@mutation(EVIDENCE, 'if kind is None:', "if False:", expect="a type that is not kept was stored")
def test_a_type_that_is_not_on_the_list_is_dropped_whatever_its_bytes_and_a_png_is_kept_byte_for_byte(
        db, objects, caplog):
    _, made = one_job(db)
    binary = b"\x00\x00\x00\x18ftypmp42\xff\xfe\xfd" * 4
    note = b"cookie: sessionid=planted-note-9999"        # valid UTF-8 under a binary type: still not rewritten
    keep_all(db, objects, made.job_id,
             [("clip.mp4", binary, "video/mp4"), ("blob.bin", note, "application/octet-stream"),
              ("doc.pdf", b"%PDF-1.7 planted", "application/pdf"), ("none", note, ""),
              ("gif.gif", PNG, "image/gif"), ("shot.png", PNG, "image/png")])
    stored = rows(db, made.job_id)
    assert [r[2] for r in stored] == ["image/png"], "a type that is not kept was stored"
    assert asyncio.run(objects.read(stored[0][1])) == PNG, "pixels are not rewritten"
    assert caplog.text.count("is not a type that is kept") == 5


# ---- the "dropped" warning is gone from the wired path ---------------------------------------------------------------
def test_a_job_run_context_with_the_sink_does_not_log_a_dropped_warning(db, objects, caplog):
    from relay_core.jobs.poller import ReadCtx

    _, made = one_job(db)

    async def scenario(p):
        ctx = ReadCtx(p, made.job_id, account=None, settings=None, provider="fake", evidence_sink=job_sink(p, objects))
        await ctx.evidence("ui-error-x-1.png", PNG, "image/png")

    run(db, scenario)
    assert "dropped: no evidence sink" not in caplog.text and len(rows(db, made.job_id)) == 1
