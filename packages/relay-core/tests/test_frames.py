"""Prompt frames: the grammar, the renderer, the tables and their guards, the
effective lookup, the seed, and the stored text reaching the engine.

The pure parts need no database; the guards run against the per-run Postgres as the two service roles."""

import asyncio
import contextlib
import logging
import threading
import time
import uuid

import psycopg
import pytest
from conftest import JOB_INSERT, job_args
from jobkit import PROVIDER, account, claim, key, run, submit
from lifecyclekit import age, claim_new, due, pass_once, poller, reconciler
from test_roles import as_role

from relay_core import frames
from relay_core.db import migrate, run_migrations, tx
from relay_core.db.migrate import migration_files
from relay_core.jobs import store
from relay_core.providers import Accepted, Ambiguous, FrameUse, NotSent, NotTaken, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, sent
from relay_core.testing.mutation import mutation
from relay_core.testing.pg import disposable_database

F = "packages/relay-core/src/relay_core/frames.py"
M14 = "packages/relay-core/src/relay_core/db/migrations/0014_prompt_frames.sql"
CORE = frames.CORE_FRAME


# ---- the grammar -----------------------------------------------------------------------------------
ACCEPTED = (CORE, "{{tracking_code}}{{client_prompt}}", "{{tracking_code}} {{client_prompt}}",
            "Be brief: {{client_prompt}} (code {{tracking_code}}) a { single } brace } is { text",
            "{{{client_prompt}}} {{tracking_code}}", "x" * (1000 - len(CORE)) + CORE.replace(" ", ""))
PLACEHOLDER = "input.frame_placeholder"
REFUSED = {                  # what the fault is called in the failure message -> (text, the param it names)
    "a missing required placeholder is accepted": [
        ("{{tracking_code}}", "{{client_prompt}}"), ("{{client_prompt}}", "{{tracking_code}}"),
        ("no placeholders at all", "{{client_prompt}}")],
    "a repeated placeholder is accepted": [
        ("{{client_prompt}} {{client_prompt}} {{tracking_code}}", "{{client_prompt}}"),
        ("{{client_prompt}} {{tracking_code}} {{tracking_code}}", "{{tracking_code}}")],
    "an unknown placeholder is accepted": [
        (CORE + " {{other}}", "{{other}}"), ("{{template_prompt}} " + CORE, "{{template_prompt}}"),
        ("{{ client_prompt }} " + CORE, "{{ client_prompt }}"), (CORE + " {{}}", "{{}}")],
    "a stray brace pair is accepted": [(CORE + " {{", "{{"), (CORE + " }}", "}}")],
}
INVALID = {                  # -> texts that answer `input.invalid` with param `text`
    "an empty frame is accepted": ["", None, 7],
    "a frame over 1,000 characters is accepted": [CORE + " " + "x" * 1000],
    "a line break is accepted": [CORE + "\n", CORE + "\r", "a\nb " + CORE, "a\r\nb " + CORE],
    "an invisible character is accepted": [CORE + "\u200b", "\u3164" + CORE, CORE + " a\ufe0f", CORE + "\U000e0041",
                                           CORE + "\u00ad"],
    "a lone surrogate is accepted": ["\ud800 " + CORE, CORE + "\udfff", CORE.replace(" ", "\udc00")],
}


def refusal(why: str, text) -> frames.FrameError:
    """The FrameError the grammar raises for `text`; the failure names the fault when it raises none."""
    try:
        frames.check_frame(text)
    except frames.FrameError as e:
        return e
    pytest.fail("%s: %r" % (why, text))


@mutation(F, "        if slot not in found:\n", "        if False:\n",
          expect="a missing required placeholder is accepted")
@mutation(F, "        if found.count(slot) > 1:\n", "        if found.count(slot) > 99:\n",
          expect="a repeated placeholder is accepted")
@mutation(F, "        if slot not in PLACEHOLDERS:\n", "        if False:\n",
          expect="an unknown placeholder is accepted")
@mutation(F, "        if stray in rest:\n", "        if False:\n", expect="a stray brace pair is accepted")
def test_the_grammar_refuses_each_placeholder_fault_with_its_code_and_the_placeholder_it_names():
    for why, cases in REFUSED.items():
        for text, param in cases:
            got = refusal(why, text)
            assert (got.code, got.param) == (PLACEHOLDER, param), "%s: %r -> %s" % (why, text, got)


@mutation(F, "or \"\\r\" in text or \"\\n\" in text", "or \"\\r\" in text", expect="a line break is accepted")
@mutation(F, "or not text or len(text)", "or len(text)", expect="an empty frame is accepted")
@mutation(F, "len(text) > FRAME_MAX", "len(text) > FRAME_MAX * 9", expect="a frame over 1,000 characters is accepted")
@mutation(F, "            or has_invisible(text) or has_surrogate(text):", "            or has_surrogate(text):",
          expect="an invisible character is accepted")
@mutation(F, "            or has_invisible(text) or has_surrogate(text):", "            or has_invisible(text):",
          expect="a lone surrogate is accepted")
def test_the_grammar_refuses_an_empty_long_multi_line_or_invisible_text_as_invalid_input():
    for why, cases in INVALID.items():
        for text in cases:
            got = refusal(why, text)
            assert (got.code, got.param) == ("input.invalid", "text"), "%s: %r -> %s" % (why, text, got)


def test_a_frame_with_both_placeholders_once_each_is_accepted_and_single_braces_are_text():
    for text in ACCEPTED:
        assert frames.check_frame(text) == text
        assert len(text) <= frames.FRAME_MAX


# ---- the renderer ----------------------------------------------------------------------------------------------------
@mutation(F, '    return " ".join(_SLOT.sub(lambda m: values[m.group(0)], frame).split())',
          '    return " ".join(_SLOT.sub(lambda m: values.get(m.group(0), m.group(0)), _SLOT.sub('
          'lambda m: values.get(m.group(0), m.group(0)), frame)).split())', expect="the renderer re-scans values")
@mutation(F, '    return " ".join(_SLOT.sub(lambda m: values[m.group(0)], frame).split())',
          '    return _SLOT.sub(lambda m: values[m.group(0)], frame).strip()',
          expect="whitespace runs are not collapsed")
@mutation(F, 'TRACKING_CODE: "[%s]" % marker', 'TRACKING_CODE: "%s" % marker',
          expect="the tracking code is not in brackets")
def test_the_renderer_fills_both_slots_once_never_rescans_a_value_and_collapses_white_space():
    assert frames.render(CORE, "a  cat\non\ta\r\nmat", "4321") == "a cat on a mat [4321]", \
        "whitespace runs are not collapsed (or the tracking code is not in brackets)"
    # a value that looks like a slot stays text: one pass
    tricky = "see {{tracking_code}} and {{client_prompt}} and {{x}}"
    assert frames.render(CORE, tricky, "4321") == tricky + " [4321]", "the renderer re-scans values"
    assert frames.render("  Fixed.  {{tracking_code}}\t{{client_prompt}} ", " in  the  middle ", "7000") == \
        "Fixed. [7000] in the middle", "whitespace runs are not collapsed"
    assert frames.render(CORE, "ünï 日本語", "3000") == "ünï 日本語 [3000]"


@mutation(F, "    check_frame(frame)\n    values", "    values", expect="a frame that fails the grammar was rendered")
def test_a_frame_that_fails_the_grammar_cannot_be_rendered():
    for bad in ("no slots", "{{client_prompt}}", CORE + " {{other}}", CORE + "\n"):
        with pytest.raises(frames.FrameError):
            frames.render(bad, "p", "1234")
            pytest.fail("a frame that fails the grammar was rendered: %r" % bad)


def test_the_invisible_character_fold_is_the_one_the_page_applies_nfkc_first():
    assert frames.shows_nothing("") and frames.shows_nothing(" \u200b\u3164\ufe0f\n\U000e0041") and \
        not frames.shows_nothing("a\u200b")
    assert frames.has_invisible("\uffa0") and not frames.has_invisible("ab \u00e9")      # U+FFA0 is U+1160 under NFKC


# ---- the tables and their guards ----------------------------------------------------
INSERT_VERSION = ("INSERT INTO prompt_frames (provider, kind, model, text, created_by) VALUES "
                  "('p1', 'video', %s, %s, 'test') RETURNING id")
INSERT_LOCK = ("INSERT INTO prompt_frame_locks (provider, kind, model, locked, created_by) "
               "VALUES ('p1', 'video', 'm', true, 'test')")


@mutation(M14, "GRANT SELECT, INSERT ON prompt_frames TO relay_api;",
          "GRANT SELECT, INSERT, UPDATE ON prompt_frames TO relay_api;", expect="the api may update prompt_frames")
@mutation(M14, "GRANT SELECT, INSERT ON prompt_frame_locks TO relay_api;",
          "GRANT SELECT, INSERT, UPDATE ON prompt_frame_locks TO relay_api;",
          expect="the api may update prompt_frame_locks")
@mutation(M14, "GRANT SELECT, INSERT ON prompt_frame_locks TO relay_api;",
          "GRANT SELECT, INSERT ON prompt_frame_locks TO relay_api, relay_worker;",
          expect="the worker may read prompt_frame_locks")
def test_the_api_appends_versions_and_locks_the_worker_never_reads_them_and_nobody_edits_a_row(db):
    ok = [(INSERT_VERSION.replace(" RETURNING id", ""), (None, CORE)), (INSERT_LOCK, ()),
          ("SELECT count(*) FROM prompt_frames, prompt_frame_locks", ()),
          ("SELECT last_value FROM prompt_frames_id_seq", ()), ("SELECT last_value FROM prompt_frame_locks_id_seq", ())]
    assert as_role(db, "relay_api", ok) == [], "the api cannot append a version or a lock"
    refused = {
        "the api may update prompt_frames": ("UPDATE prompt_frames SET text = text", (), "relay_api"),
        "the api may delete prompt_frames": ("DELETE FROM prompt_frames", (), "relay_api"),
        "the api may update prompt_frame_locks": ("UPDATE prompt_frame_locks SET locked = false", (), "relay_api"),
        "the api may delete prompt_frame_locks": ("DELETE FROM prompt_frame_locks", (), "relay_api"),
        "the worker may read prompt_frames": ("SELECT * FROM prompt_frames", (), "relay_worker"),
        "the worker may read prompt_frame_locks": ("SELECT * FROM prompt_frame_locks", (), "relay_worker"),
        "the worker may write prompt_frames": (INSERT_VERSION.replace(" RETURNING id", ""), (None, CORE),
                                               "relay_worker"),
        "the worker may write prompt_frame_locks": (INSERT_LOCK, (), "relay_worker"),
    }
    for why, (sql, params, role) in refused.items():
        failed = as_role(db, role, [(sql, params)])
        assert failed and failed[0][1] == "InsufficientPrivilege", "%s: %s" % (why, failed)


FROZEN = ("sent_text = 'another text'", "prompt = 'another prompt'", "marker = 'other'", "frame_id = %(frame)s")


@mutation(M14, "CREATE TRIGGER jobs_frozen_columns BEFORE UPDATE OF sent_text, frame_id, marker, prompt ON jobs\n"
          "    FOR EACH ROW EXECUTE FUNCTION jobs_frozen_columns();", "SELECT 1;", expect="a frozen column was changed")
@mutation(M14, "BEFORE UPDATE OF sent_text, frame_id, marker, prompt ON jobs",
          "BEFORE UPDATE OF sent_text, frame_id ON jobs", expect="a frozen column was changed")
def test_no_role_can_change_sent_text_frame_id_marker_or_prompt_of_a_job_after_the_insert(db):
    k = db.key(("p1", "video", None, None))
    frame = db.one(INSERT_VERSION, None, CORE)[0]
    job = db.job(k)
    for role in ("relay_api", "relay_worker"):
        for assignment in FROZEN:
            sql = "UPDATE jobs SET %s WHERE id = %%(job)s" % assignment
            failed = as_role(db, role, [(sql, {"job": job, "frame": frame})])
            assert failed and failed[0][1] == "IntegrityConstraintViolation", \
                "a frozen column was changed by %s: %s" % (role, assignment)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        db.run("UPDATE jobs SET prompt = 'owner edit' WHERE id = %s", job)
    # the rest of the row moves as before, and a column set to the value it already has is not a change
    for role in ("relay_api", "relay_worker"):
        same = ("UPDATE jobs SET title = 't', callback_attempts = 1, prompt = prompt, marker = marker, "
                "sent_text = sent_text, frame_id = frame_id WHERE id = %s")
        assert as_role(db, role, [(same, (job,))]) == [], role


@mutation(M14, "    CHECK (text IS NULL OR (", "    CHECK (true OR (", expect="the placeholder CHECK let a bad text in")
@mutation(M14, "    CHECK (model IS NOT NULL OR text IS NOT NULL),", "    CHECK (true),",
          expect="a default without a text was stored")
def test_the_table_refuses_a_text_outside_the_placeholder_rule_and_a_default_without_a_text(db):
    for text in ("{{tracking_code}}", "{{client_prompt}}", "{{client_prompt}} {{client_prompt}} {{tracking_code}}",
                 CORE + " {{x}}", CORE + " {{", CORE + " }}", CORE + "\n", CORE + "\r", "", CORE + " " + "x" * 1000):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run(INSERT_VERSION.replace(" RETURNING id", ""), None, text)
            pytest.fail("the placeholder CHECK let a bad text in: %r" % text)
    for text in ACCEPTED:
        db.run(INSERT_VERSION.replace(" RETURNING id", ""), None, text)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.run(INSERT_VERSION.replace(" RETURNING id", ""), None, None)
        pytest.fail("a default without a text was stored")
    db.run(INSERT_VERSION.replace(" RETURNING id", ""), "a-model", None)           # a model that follows the default


@contextlib.contextmanager
def database_at_0013(tmp_path, monkeypatch, *, ended: bool | None):
    """A migrated-to-0013 database holding one job (`ended` True or False: ended or still queued; None: no job); yields
    (dsn, job id) and leaves `migrate.MIGRATIONS` pointing at the real directory again."""
    real = migrate.MIGRATIONS
    for version, path in migration_files():
        if version <= 13:
            (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
    with disposable_database() as dsn:
        assert run_migrations(dsn)[-1] == 13
        job_id, key_id = uuid.uuid4(), uuid.uuid4()
        with psycopg.connect(dsn, autocommit=True, options="-c search_path=relay") as c:
            c.execute("INSERT INTO api_keys (id, key_hash, key_prefix) VALUES (%s, 'h', 'rk-t')", (key_id,))
            if ended is not None:
                c.execute(JOB_INSERT, job_args(job_id, key_id))
            if ended:
                c.execute("UPDATE jobs SET status = 'failed', error_code = 'render.deadline', finished_at = now() "
                          "WHERE id = %s", (job_id,))
        monkeypatch.setattr(migrate, "MIGRATIONS", real)
        yield dsn, (key_id, job_id)


def test_0014_applies_on_a_database_at_0013_whose_jobs_have_all_ended(tmp_path, monkeypatch):
    with database_at_0013(tmp_path, monkeypatch, ended=True) as (dsn, (_, job_id)):
        assert run_migrations(dsn)[0] == 14, "0014 is the first migration applied after 0013"
        with psycopg.connect(dsn, autocommit=True, options="-c search_path=relay") as c:
            assert c.execute("SELECT sent_text, frame_id, prompt FROM jobs WHERE id = %s", (job_id,)).fetchone() == (
                None, None, "a prompt"), "a job made before 0014 keeps NULL sent_text and frame_id"
            assert c.execute("SELECT count(*) FROM prompt_frames").fetchone() == (0,), "nothing is backfilled"
            with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
                c.execute("UPDATE jobs SET sent_text = 'now' WHERE id = %s", (job_id,))


@mutation(M14, "    IF EXISTS (SELECT 1 FROM jobs WHERE status NOT IN ('done', 'failed')) THEN", "    IF false THEN",
          expect="0014 ran with a job in flight")
def test_0014_refuses_to_run_while_a_job_is_in_flight_and_applies_nothing_of_itself(tmp_path, monkeypatch):
    with database_at_0013(tmp_path, monkeypatch, ended=False) as (dsn, _):
        try:
            run_migrations(dsn)
        except psycopg.errors.ObjectNotInPrerequisiteState as e:
            assert "drain" in str(e) and "finish" in str(e), "the refusal does not say what to do"
        else:
            pytest.fail("0014 ran with a job in flight")
        with psycopg.connect(dsn, autocommit=True, options="-c search_path=relay") as c:
            assert c.execute("SELECT to_regclass('prompt_frames')").fetchone() == (None,)
            assert c.execute("SELECT max(version) FROM schema_migrations").fetchone() == (13,)
            assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'jobs' "
                             "AND column_name = 'sent_text'").fetchone() == (0,)


@mutation(M14, "LOCK TABLE jobs IN SHARE ROW EXCLUSIVE MODE;", "SELECT 1;",
          expect="0014 ran past a job committed after its check")
def test_a_job_committed_while_0014_waits_is_still_seen_by_its_guard(tmp_path, monkeypatch):
    with database_at_0013(tmp_path, monkeypatch, ended=None) as (dsn, (key_id, job_id)):
        outcome = []

        def migrate_now():
            try:
                outcome.append(run_migrations(dsn))
            except psycopg.Error as e:
                outcome.append(e)

        with psycopg.connect(dsn, options="-c search_path=relay") as writer:     # a creation that has not committed
            writer.execute(JOB_INSERT, job_args(job_id, key_id))
            runner = threading.Thread(target=migrate_now)
            runner.start()
            time.sleep(1.0)                                             # the migration is waiting for the writer
            assert not outcome, "0014 did not wait for the open transaction that writes a job"
            writer.commit()
        runner.join(30)
        got = outcome[0] if outcome else None
        assert isinstance(got, psycopg.errors.ObjectNotInPrerequisiteState) and "drain" in str(got), \
            "0014 ran past a job committed after its check: %r" % (got,)
        with psycopg.connect(dsn, autocommit=True, options="-c search_path=relay") as c:
            assert c.execute("SELECT to_regclass('prompt_frames')").fetchone() == (None,)
            assert c.execute("SELECT max(version) FROM schema_migrations").fetchone() == (13,)


# ---- the effective lookup and the seed ----------------------------------------------------------
USER = frames.By(uuid.UUID(int=7), "ann")
DEFAULTS = {"video": "Shipped video. " + CORE, "image": "Shipped image. " + CORE}


async def add(p, kind, model, text, by=USER):
    async with tx(p) as conn:
        return await frames.insert_version(conn, "p1", kind, model, text, by)


async def effective(p, kind, model):
    async with tx(p) as conn:
        return await frames.effective(conn, "p1", kind, model)


@mutation(F, "    if own is not None and own.text is not None:", "    if False:", expect="the own frame is ignored")
def test_a_models_own_frame_beats_the_default_and_a_null_text_follows_the_default_again(db):
    async def scenario(p):
        assert await effective(p, "video", "m") is None, "no row at all is not a frame"
        default = await add(p, "video", None, "Default. " + CORE)
        assert (await effective(p, "video", "m")).version.id == default.id
        own = await add(p, "video", "m", "Own. " + CORE)
        got = await effective(p, "video", "m")
        assert (got.source, got.version.id) == ("own", own.id), "the own frame is ignored"
        assert (await effective(p, "video", "other")).source == "default"
        assert await effective(p, "image", "m") is None, "another kind has its own default"
        await add(p, "video", "m", None)                                  # follow the default again
        assert (await effective(p, "video", "m")).source == "default"
        newer = await add(p, "video", None, "Newer default. " + CORE)
        assert (await effective(p, "video", "m")).version.id == newer.id, "the newest default row wins"

    run(db, scenario)


@mutation(F, "            await insert_version(conn, provider, kind, None, defaults[kind], SYSTEM)", "            pass",
          expect="the shipped defaults were not seeded")
@mutation(F, "        elif current.text != defaults[kind]:", "        elif False:",
          expect="a default that differs from the shipped one was not flagged")
def test_seeding_writes_version_one_as_system_once_and_flags_a_default_that_differs_from_the_shipped_one(db, caplog):
    async def scenario(p):
        async with tx(p) as conn:
            first = await frames.seed(conn, "p1", DEFAULTS)
        async with tx(p) as conn:
            again = await frames.seed(conn, "p1", DEFAULTS)
        await add(p, "video", None, "Edited. " + CORE)                           # the admin changed the video default
        with caplog.at_level(logging.WARNING, logger="relay_core.frames"):
            async with tx(p) as conn:
                differs = await frames.seed(conn, "p1", DEFAULTS)
        return first, again, differs

    first, again, differs = run(db, scenario)
    assert first == [] and again == [], "the shipped defaults were not seeded, or seeded twice"
    rows = db.all("SELECT kind, model, text, created_by, created_by_id FROM prompt_frames ORDER BY id")
    assert rows[:2] == [("image", None, DEFAULTS["image"], "system", None),
                        ("video", None, DEFAULTS["video"], "system", None)] and len(rows) == 3, \
        "the shipped defaults were not seeded: %r" % rows
    assert differs == ["video"], "a default that differs from the shipped one was not flagged"
    assert any("differs from the shipped one" in r.getMessage() and "video" in r.getMessage() for r in caplog.records)
    assert db.one("SELECT count(*) FROM prompt_frames WHERE kind = 'video'") == (2,), "seeding never rewrites a default"


LOCKED_SEED = ("        await _lock_target(conn, provider, kind)\n"
                "        current = await newest(conn, provider, kind, None)")


@mutation(F, LOCKED_SEED, "        current = await newest(conn, provider, kind, None)", expect="seeding ran twice")
def test_two_servers_seeding_at_once_write_each_default_once(db):
    async def scenario(p):
        released = asyncio.Event()

        async def first():
            async with tx(p) as conn:
                await frames.seed(conn, "p1", DEFAULTS)
                await released.wait()                              # the transaction stays open: nothing is committed

        async def second():
            async with tx(p) as conn:
                await frames.seed(conn, "p1", DEFAULTS)

        a = asyncio.create_task(first())
        await asyncio.sleep(0.3)
        b = asyncio.create_task(second())
        await asyncio.sleep(0.5)
        waited = not b.done()                                      # blocked on the (provider, kind) lock
        released.set()
        await asyncio.gather(a, b)
        return waited

    assert run(db, scenario), "the second seed did not wait for the first: seeding ran twice"
    assert db.one("SELECT count(*) FROM prompt_frames") == (2,), "seeding ran twice"


# ---- the stored text reaches the engine on every path ----------------------------------------------------------
def test_the_engine_gets_the_stored_text_on_the_first_send_the_reconcile_and_the_re_send(db):
    k, _ = key(db), account(db, left=100)
    text = "Fixed words. {{tracking_code}} {{client_prompt}}"
    frame = db.one(INSERT_VERSION.replace("'p1'", "'%s'" % PROVIDER), None, text)[0]
    use = FrameUse(frame, text)

    async def scenario(p):
        engine = FakeEngine()
        engine.script_submit("*", sent(SubmitResult(Ambiguous("no answer"))),
                             sent(SubmitResult(NotTaken("pool.account_throttled"))),
                             sent(SubmitResult(Accepted("ref-1"))))
        got = await claim_new(p, k, "a  cat\non\ta mat", frame=use)
        assert await submit(p, got, engine) == "unknown"
        # a frame saved after the creation changes nothing
        db.run("INSERT INTO prompt_frames (provider, kind, model, text, created_by) VALUES (%s, 'video', NULL, "
               "'Later. {{client_prompt}} {{tracking_code}}', 't')", PROVIDER)
        db.run("UPDATE jobs SET next_poll_at = clock_timestamp() - interval '1 second' WHERE id = %s", got.job_id)
        age(db, got.job_id, "sent_at", 1_000)
        engine.script_reconcile("*", NotSent(covers_send_window=True))
        await pass_once(reconciler(p, engine))
        again = await claim(p, owner="w2")
        assert again is not None and again.job_id == got.job_id, "the job was not queued again"
        assert await submit(p, again, engine) == "queued", "a NotTaken answer sends the job back to the queue"
        due(db, got.job_id)                         # a requeued job is paused for 30 s
        third = await claim(p, owner="w3")
        assert third is not None and third.job_id == got.job_id
        assert await submit(p, third, engine) == "waiting"
        due(db, got.job_id)
        await pass_once(poller(p, engine))
        return got.job_id, engine, await store.job_view(p, got.job_id)

    job_id, engine, view = run(db, scenario)
    marker = view.marker
    expected = "Fixed words. [%s] a cat on a mat" % marker
    assert [(m, t) for m, _, t in engine.texts] == [
        ("submit", expected), ("reconcile", expected), ("submit", expected), ("submit", expected),
        ("poll", expected)], "the engine did not get the stored text on every path: %r" % engine.texts
    assert view.sent_text == expected and db.one("SELECT sent_text, frame_id FROM jobs WHERE id = %s", job_id) == (
        expected, frame)
