"""The `relay` commands (contracts §2, §7.7, §13; I20, I29, D31): `serve` and its refusals, wallet backfill at start,
shutdown order, `admin create-user`, `keygen`, `key create|disable`, `provider drain|resume`, `jobs restore-mark`,
`e2e`. Each command is run through `main(argv)` against the real database, as the role its URL names."""

import asyncio
import io
import logging
import re
import sys
import uuid
from types import SimpleNamespace

import httpx
import pytest
from accountkit import minio_settings

from relay_cli import main as cli
from relay_core import crypto, keys
from relay_core.admin import users
from relay_core.api import runners
from relay_core.db import open_pool, tx
from relay_core.testing.fake_provider import FAKE_FRAMES, FakeSpec
from relay_core.testing.mutation import mutation

M = "apps/relay/src/relay_cli/main.py"
PASSWORD = "correct-horse-battery"
RAW_KEY = re.compile(r"rk-[A-Za-z0-9_-]{43}")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No private key, DSN or seal key from the host leaks into a command under test."""
    import os

    for name in list(os.environ):
        if "PRIVATE_KEY" in name or name in ("DATABASE_URL", "BILLING_TZ", "PORT", crypto.PUBLIC_KEY_VAR):
            monkeypatch.delenv(name)


def specs(monkeypatch, *names: str) -> None:
    monkeypatch.setattr(cli, "load_specs", lambda: {n: SimpleNamespace(SPEC=FakeSpec(n)) for n in names})


def serve_env(monkeypatch, db, **extra) -> None:
    endpoint, access, secret = minio_settings()
    env = {"DATABASE_URL": db.api_dsn, "MINIO_ENDPOINT": endpoint, "MINIO_ACCESS_KEY": access,
           "MINIO_SECRET_KEY": secret, crypto.PUBLIC_KEY_VAR: crypto.generate_keypair().public,
           "BILLING_TZ": "UTC", "PORT": "8123", **extra}
    for name, value in env.items():
        monkeypatch.setenv(name, value)


class Http:
    """Stands in for uvicorn: records what the process would serve and proves the app answers."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.pools: list[tuple[int, int]] = []          # (max_size, max_waiting) of the served app's pool

    async def __call__(self, app, port):
        self.pools.append((app.state.pool.max_size, app.state.pool.max_waiting))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay.test") as c:
            self.calls.append((port, (await c.get("/healthz")).json()))


@pytest.fixture
def http(monkeypatch):
    spy = Http()
    monkeypatch.setattr(cli, "run_uvicorn", spy)
    return spy


def err(capsys) -> str:
    return capsys.readouterr().err


# ---- serve: refusals ----------------------------------------------------------------------------------------------
@mutation(M, "    if held:\n", "    if False:\n", expect="serve started with a private key set")
def test_serve_refuses_to_start_when_any_private_key_variable_is_set_and_never_prints_it(db, monkeypatch, capsys, http):
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    secret = crypto.generate_keypair().private
    monkeypatch.setenv(crypto.PRIVATE_KEY_VAR, secret)
    code = cli.main(["serve"])
    message = err(capsys)
    assert code == 2 and not http.calls, "serve started with a private key set"
    assert crypto.PRIVATE_KEY_VAR in message and secret not in message
    monkeypatch.setenv(crypto.PRIVATE_KEY_VAR, "")                 # an empty variable holds no key
    assert cli.main(["serve"]) == 0 and http.calls


@mutation(M, 'await billing.verify_billing_tz(conn, env.get("BILLING_TZ", ""))',
          'await billing.verify_billing_tz(conn, "")', expect="BILLING_TZ that differs from the pin")
def test_serve_refuses_a_billing_timezone_that_differs_from_the_pin(db, monkeypatch, capsys, http):
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db, BILLING_TZ="Asia/Tokyo")            # the pin is UTC
    code = cli.main(["serve"])
    assert code == 2 and not http.calls, "serve started with a BILLING_TZ that differs from the pin"
    assert "pinned to UTC" in err(capsys)


@mutation(M, 'crypto.seal(crypto.public_key_from_env(env), b"")', "None", expect="without the credential public key")
def test_serve_refuses_without_a_usable_credential_public_key(db, monkeypatch, capsys, http):
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    monkeypatch.delenv(crypto.PUBLIC_KEY_VAR)
    assert cli.main(["serve"]) == 2 and not http.calls, "serve started without the credential public key"
    assert crypto.PUBLIC_KEY_VAR in err(capsys)
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, "not-a-key")
    assert cli.main(["serve"]) == 2 and not http.calls, "serve started without the credential public key"
    assert "base64" in err(capsys)


def test_serve_refuses_a_missing_database_url_and_object_store(db, monkeypatch, capsys, http):
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    monkeypatch.delenv("DATABASE_URL")
    assert cli.main(["serve"]) == 2 and "DATABASE_URL" in err(capsys)
    serve_env(monkeypatch, db)
    monkeypatch.delenv("MINIO_ENDPOINT")
    assert cli.main(["serve"]) == 2 and "MINIO_ENDPOINT" in err(capsys)
    assert not http.calls


@mutation(M, '    if is_example_secret(env.get("MINIO_SECRET_KEY")):\n', "    if False:\n",
          expect="serve started with an example object-store secret")
def test_serve_refuses_an_example_object_store_secret(db, monkeypatch, capsys, http):
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    code = cli.main(["serve"])
    assert code == 2 and "MINIO_SECRET_KEY" in err(capsys), "serve started with an example object-store secret"
    assert not http.calls and "minioadmin" not in err(capsys)


@mutation(M, "        await store.ensure_bucket()", "        pass", expect="serve started without making the bucket")
def test_serve_makes_the_bucket_on_a_fresh_object_store(db, monkeypatch, http):
    from relay_core.storage import ObjectStore

    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    bucket = "relay-test-" + uuid.uuid4().hex[:12]
    monkeypatch.setattr(ObjectStore, "from_env", classmethod(lambda cls, env: cls(
        env["MINIO_ENDPOINT"], env["MINIO_ACCESS_KEY"], env["MINIO_SECRET_KEY"], bucket)))
    client = ObjectStore(*minio_settings(), bucket=bucket)._client
    assert not client.bucket_exists(bucket)
    try:
        assert cli.main(["serve"]) == 0 and http.calls
        assert client.bucket_exists(bucket), "serve started without making the bucket"
    finally:
        if client.bucket_exists(bucket):
            client.remove_bucket(bucket)


BUCKET_THEN_POOL = '''    try:
        await store.ensure_bucket()              # a fresh object store has no bucket: the first media write would fail
    except StorageError as e:
        raise CliError("object store is not usable at start: %s" % e) from None

    pool = await open_pool(dsn, max_size=API_POOL_MAX, name="relay-api")
'''
POOL_THEN_BUCKET = '''    pool = await open_pool(dsn, max_size=API_POOL_MAX, name="relay-api")
    try:
        await store.ensure_bucket()              # a fresh object store has no bucket: the first media write would fail
    except StorageError as e:
        raise CliError("object store is not usable at start: %s" % e) from None
'''


@mutation(M, BUCKET_THEN_POOL, POOL_THEN_BUCKET, expect="the pool was opened before the bucket check")
def test_serve_refuses_to_serve_when_the_object_store_is_unreachable(db, monkeypatch, capsys, http):
    import relay_core.db

    opened: list[str] = []
    real_open_pool = relay_core.db.open_pool

    async def spy(*args, **kwargs):
        opened.append(kwargs.get("name", ""))
        return await real_open_pool(*args, **kwargs)

    monkeypatch.setattr(relay_core.db, "open_pool", spy)
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db, MINIO_ENDPOINT="127.0.0.1:1")
    assert cli.main(["serve"]) == 1
    assert "object store is not usable at start" in err(capsys) and not http.calls
    assert not opened, "the pool was opened before the bucket check: %s" % opened


# ---- serve: start and stop ----------------------------------------------------------------------------------------
@mutation(M, "                await billing.ensure_wallets(conn, spec.name, spec.caps.wallet_kinds)\n",
          "                pass\n", expect="a newly declared pair must be closed on the existing key")
def test_serve_backfills_closed_wallets_for_a_newly_declared_pair_and_leaves_the_others(db, monkeypatch, http):
    key = db.key(("fake", "video", 5, None))                       # "other" is new; "fake"/"image" is new too
    specs(monkeypatch, "fake", "other")
    serve_env(monkeypatch, db)
    assert cli.main(["serve"]) == 0 and http.calls == [(8123, {"ok": True})]
    rows = db.all("SELECT provider, kind, day_limit, total_limit FROM key_wallets WHERE key_id = %s "
                  "ORDER BY provider, kind", key)
    assert rows == [("fake", "image", 0, None), ("fake", "video", 5, None),
                    ("other", "image", 0, None), ("other", "video", 0, None)], (
        "a newly declared pair must be closed on the existing key, and an existing wallet untouched")


@mutation(M, '                    await frames.seed(conn, spec.name, spec.frames.defaults)', "                    pass",
          expect="serve did not seed the shipped default frames")
@mutation(M, '                if getattr(spec, "frames", None) is not None:', "                if True:",
          expect="seeded a provider that declares no frames")
def test_serve_seeds_the_shipped_default_frames_once_and_warns_at_every_start_when_one_differs(
        db, monkeypatch, http, caplog):
    monkeypatch.setattr(cli, "load_specs", lambda: {
        "fake": SimpleNamespace(SPEC=FakeSpec("fake", frames=FAKE_FRAMES)),
        "plain": SimpleNamespace(SPEC=FakeSpec("plain"))})
    serve_env(monkeypatch, db)
    try:
        assert cli.main(["serve"]) == 0 and cli.main(["serve"]) == 0, \
            "serve did not seed the shipped default frames, or seeded a provider that declares no frames"
    except AttributeError as e:
        pytest.fail("serve seeded a provider that declares no frames: %r" % e)
    rows = db.all("SELECT provider, kind, model, text, created_by, created_by_id FROM prompt_frames ORDER BY kind")
    assert rows == [("fake", "image", None, FAKE_FRAMES.defaults["image"], "system", None),
                    ("fake", "video", None, FAKE_FRAMES.defaults["video"], "system", None)], \
        "serve did not seed the shipped default frames, or seeded them twice, or seeded a provider with no frames"
    db.run("INSERT INTO prompt_frames (provider, kind, model, text, created_by) VALUES ('fake', 'video', NULL, "
           "'Edited. {{client_prompt}} {{tracking_code}}', 'admin')")
    with caplog.at_level(logging.WARNING, logger="relay_core.frames"):
        assert cli.main(["serve"]) == 0 and cli.main(["serve"]) == 0
    warned = [r for r in caplog.records if "differs from the shipped one" in r.getMessage()]
    assert len(warned) == 2 and "video" in warned[0].getMessage(), "the warning comes at every start"
    assert db.one("SELECT count(*) FROM prompt_frames")[0] == 3, "a stored default is never rewritten by a start"


@mutation(M, "        if running is not None:\n            await running.stop()\n        await settings.stop()\n"
             "        await pool.close()\n",
          "        await settings.stop()\n        await pool.close()\n"
          "        if running is not None:\n            await running.stop()\n",
          expect="the runners must stop before the pool closes")
def test_serve_stops_the_runners_before_it_closes_the_pool(db, monkeypatch, http):
    events: list[str] = []

    class Spy:
        async def stop(self):
            events.append("runners.stop")

    def start(pool, settings, store, **kw):
        real_close = pool.close

        async def close():
            events.append("pool.close")
            await real_close()

        pool.close = close
        events.append("runners.start")
        return Spy()

    monkeypatch.setattr(runners, "start_runners", start)
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    assert cli.main(["serve"]) == 0
    assert events == ["runners.start", "runners.stop", "pool.close"], "the runners must stop before the pool closes"


def test_serve_stops_the_runners_even_when_the_server_fails(db, monkeypatch):
    events: list[str] = []

    class Spy:
        async def stop(self):
            events.append("runners.stop")

    async def broken(app, port):
        raise OSError("address already in use")

    monkeypatch.setattr(runners, "start_runners", lambda *a, **kw: Spy())
    monkeypatch.setattr(cli, "run_uvicorn", broken)
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    assert cli.main(["serve"]) == 1 and events == ["runners.stop"]


# ---- admin create-user ---------------------------------------------------------------------------------------------
@mutation(M, "await users.create_user(pool, username, password)", 'await users.create_user(pool, username, "x" * 12)',
          expect="the stored password must be the one typed")
def test_create_user_reads_the_password_from_stdin_never_argv_and_prints_nothing_secret(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.api_dsn)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main(["admin", "create-user", "alice"]) == 0
    out = capsys.readouterr()
    assert PASSWORD not in out.out + out.err
    stored = db.one("SELECT password_hash FROM admin_users WHERE username = 'alice'")[0]
    assert stored != PASSWORD and asyncio.run(users.verify_password(stored, PASSWORD)), (
        "the stored password must be the one typed")
    with pytest.raises(SystemExit) as refused:                       # a password argument does not exist
        cli.main(["admin", "create-user", "bob", PASSWORD])
    assert refused.value.code == 2 and db.one("SELECT count(*) FROM admin_users WHERE username = 'bob'")[0] == 0


def test_create_user_without_echo_on_a_terminal_asks_twice_and_refuses_a_mismatch(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.api_dsn)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    answers = iter([PASSWORD, PASSWORD + "x"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["admin", "create-user", "carol"]) == 2 and "differ" in err(capsys)
    answers = iter([PASSWORD, PASSWORD])
    assert cli.main(["admin", "create-user", "carol"]) == 0
    assert db.one("SELECT count(*) FROM admin_users WHERE username = 'carol'")[0] == 1


def test_create_user_refuses_a_weak_password_and_a_taken_name(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.api_dsn)
    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    assert cli.main(["admin", "create-user", "dave"]) == 2
    assert db.one("SELECT count(*) FROM admin_users")[0] == 0
    capsys.readouterr()
    for expected in (0, 2):
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
        assert cli.main(["admin", "create-user", "dave"]) == expected
    assert "taken" in err(capsys)


# ---- keygen --------------------------------------------------------------------------------------------------------
@mutation(M, '(crypto.PUBLIC_KEY_VAR, pair.public))', '(crypto.PUBLIC_KEY_VAR, pair.private))',
          expect="the public line must be the public key")
def test_keygen_prints_a_pair_whose_public_key_seals_what_its_private_key_opens(capsys):
    assert cli.main(["keygen"]) == 0
    lines = {ln.split("=", 1)[0]: ln.split("=", 1)[1].split()[0] for ln in capsys.readouterr().out.splitlines()
             if "=" in ln}
    sealed = crypto.seal(lines[crypto.PUBLIC_KEY_VAR], b"cookie")
    try:
        opened = crypto.open_blob(sealed, [lines[crypto.PRIVATE_KEY_VAR]])
    except crypto.CryptoError:
        opened = None
    assert opened == b"cookie", "the public line must be the public key"
    assert lines[crypto.PUBLIC_KEY_VAR] != lines[crypto.PRIVATE_KEY_VAR]


# ---- key create / disable ------------------------------------------------------------------------------------------
@mutation(M, 'print("%s" % made.raw)', 'print("%s\\n%s" % (made.raw, made.raw))', expect="exactly once")
@mutation(M, "max_unfinished=max_unfinished, declared=declared, wallets=wallets)",
          "max_unfinished=max_unfinished, declared=declared, wallets=None)", expect="the day limit")
def test_key_create_prints_the_raw_key_once_stores_only_its_hash_and_opens_only_the_named_wallets(
        db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.api_dsn)
    specs(monkeypatch, "fake")
    assert cli.main(["key", "create", "--label", "ops", "--models", "fake-video", "--max-unfinished", "3",
                     "--wallet", "fake:video=7"]) == 0
    out = capsys.readouterr()
    assert len(RAW_KEY.findall(out.out)) == 1, "the raw key must be printed exactly once"
    raw = RAW_KEY.search(out.out).group(0)
    assert raw not in out.err
    row = db.one("SELECT id, key_hash, label, models, max_unfinished FROM api_keys")
    assert row[1] == keys.hash_key(raw) and raw not in row[1:]
    assert (row[2], row[3], row[4]) == ("ops", ["fake-video"], 3)
    assert db.all("SELECT kind, day_limit, total_limit FROM key_wallets ORDER BY kind") == [
        ("image", 0, None), ("video", 7, None)], "the day limit of the named wallet, the other closed"

    async def lookup():
        pool = await open_pool(db.api_dsn, max_size=1)
        try:
            async with tx(pool) as conn:
                return await keys.lookup(conn, raw)
        finally:
            await pool.close()

    assert asyncio.run(lookup()).id == row[0]


def test_key_create_unlimited_and_refusals_make_no_key(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.api_dsn)
    specs(monkeypatch, "fake")
    assert cli.main(["key", "create", "--wallet", "fake:image=unlimited"]) == 0
    assert db.all("SELECT kind, day_limit, total_limit FROM key_wallets ORDER BY kind") == [
        ("image", None, None), ("video", 0, None)]
    capsys.readouterr()
    for bad in ("nobody:video=1", "fake:video", "fake:video=lots"):
        assert cli.main(["key", "create", "--wallet", bad]) == 2, bad
    assert cli.main(["key", "create", "--models", " "]) == 2
    assert db.one("SELECT count(*) FROM api_keys")[0] == 1


@mutation(M, "keys.set_enabled(conn, wanted, False)", "keys.set_enabled(conn, wanted, True)",
          expect="the key must be disabled")
def test_key_disable_disables_and_refuses_an_unknown_or_malformed_id(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db.api_dsn)
    key = db.key(("fake", "video", 5, None))
    assert cli.main(["key", "disable", str(key)]) == 0
    assert db.one("SELECT enabled FROM api_keys WHERE id = %s", key) == (False,), "the key must be disabled"
    assert db.one("SELECT count(*) FROM key_wallets WHERE key_id = %s", key) == (1,)    # disabled, never deleted
    import uuid

    assert cli.main(["key", "disable", str(uuid.uuid4())]) == 1
    assert cli.main(["key", "disable", "not-an-id"]) == 2


# ---- provider drain / resume ---------------------------------------------------------------------------------------
@mutation(M, 'args.mode == "drain"', 'args.mode != "drain"', expect="drain must store false")
def test_provider_drain_and_resume_flip_the_claims_setting_and_unknown_providers_are_refused(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.api_dsn)
    specs(monkeypatch, "fake")
    stored = lambda: db.one("SELECT value FROM settings_kv WHERE key = 'fake.claims_enabled'")  # noqa: E731
    assert cli.main(["provider", "drain", "fake"]) == 0
    assert stored() == (False,), "drain must store false"
    assert cli.main(["provider", "resume", "fake"]) == 0
    assert stored() == (True,), "resume must store true"
    capsys.readouterr()
    assert cli.main(["provider", "drain", "dola"]) == 2 and "no provider" in err(capsys)


# ---- jobs restore-mark ---------------------------------------------------------------------------------------------
def a_sent_job(db) -> str:
    job = db.job(db.key())
    db.run("UPDATE jobs SET status = 'waiting', claims = 1, external_ref = 'ref', next_poll_at = clock_timestamp(), "
           "sent_at = clock_timestamp() - interval '10 minutes' WHERE id = %s", job)
    return job


def future() -> str:
    import datetime

    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).isoformat()


@mutation(M, "raise CliError(str(e), EXIT_FAILED) from None\n        print(\"marked",
          "raise CliError(str(e), EXIT_OK) from None\n        print(\"marked", expect="must exit non-zero")
def test_restore_mark_is_refused_while_a_worker_heartbeat_is_fresh_and_marks_nothing(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.worker_dsn)
    job = a_sent_job(db)
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
           "('worker-p1', 'p1', clock_timestamp() - interval '1 hour', clock_timestamp())")
    code = cli.main(["jobs", "restore-mark", "--dump-time", future()])
    assert code != 0, "restore-mark must exit non-zero while a worker is running"
    assert "worker-p1" in err(capsys)
    assert db.one("SELECT status FROM jobs WHERE id = %s", job) == ("waiting",)
    db.run("DELETE FROM worker_heartbeats")


@mutation(M, 'print("marked %d job(s) unknown" % marked)', 'print("marked 0 job(s) unknown")', expect="the count")
def test_restore_mark_marks_unknown_prints_the_count_and_refuses_a_naive_or_bad_time(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.worker_dsn)
    job = a_sent_job(db)
    for bad in ("2026-10-03T02:00:00", "yesterday"):
        assert cli.main(["jobs", "restore-mark", "--dump-time", bad]) == 2, bad
    assert db.one("SELECT status FROM jobs WHERE id = %s", job) == ("waiting",)
    capsys.readouterr()
    assert cli.main(["jobs", "restore-mark", "--dump-time", future()]) == 0
    assert "marked 1 job(s) unknown" in capsys.readouterr().out, "the count"
    assert db.one("SELECT status FROM jobs WHERE id = %s", job) == ("unknown",)


# ---- e2e -----------------------------------------------------------------------------------------------------------
def test_e2e_refuses_a_provider_that_has_no_e2e_run_yet(capsys):
    assert cli.main(["e2e", "--provider", "vibes"]) == 2
    assert "no e2e run" in err(capsys)


def test_a_command_that_fails_prints_one_line_and_no_trace(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:pw@127.0.0.1:1/none")
    specs(monkeypatch, "fake")
    assert cli.main(["key", "disable", "00000000-0000-0000-0000-000000000000"]) == 1
    message = err(capsys)
    assert len(message.strip().splitlines()) == 1 and "pw@" not in message


@mutation(M, 'max_size=API_POOL_MAX, name="relay-api"', 'name="relay-api"', expect="the api pool is 15 connections")
def test_serve_opens_the_api_pool_at_15_with_a_bounded_queue(db, monkeypatch, http):
    specs(monkeypatch, "fake")
    serve_env(monkeypatch, db)
    assert cli.main(["serve"]) == 0
    [(size, waiting)] = http.pools
    assert size == 15, "the api pool is 15 connections (contracts 4.5): %d" % size
    assert 0 < waiting <= 4 * size, "the queue of waiting requests is bounded: %d" % waiting


@mutation(M, '    install_log_redaction("uvicorn")                    # not uvicorn.access: see AccessPathOnly\n',
          "    pass\n", expect="a secret reached the server log")
def test_the_serve_config_redacts_what_uvicorn_writes_to_the_process_log(capsys):
    """uvicorn's own handlers (built by its Config) get the redactor too: its error line for a crashed request
    carries the exception text."""
    import logging

    from fastapi import FastAPI

    cli.uvicorn_config(FastAPI(), 0)
    logging.getLogger("uvicorn.error").error("Exception in ASGI application: %s",
                                             "Cookie: sessionid=PLANTEDcookie9876543210")
    err = capsys.readouterr().err
    assert "Exception in ASGI application" in err, err
    assert "PLANTEDcookie9876543210" not in err, "a secret reached the server log: %s" % err


@mutation(M, "        access.addFilter(AccessPathOnly())\n", "        pass\n",
          expect="the raw key reached the access log")
def test_the_access_log_of_the_serve_config_never_holds_a_query_string(monkeypatch):
    import logging
    import socket

    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    capture = Capture()
    access = logging.getLogger("uvicorn.access")
    raw = "rk-" + "A1b2C3d4" * 5 + "xyz"

    async def scenario() -> int:
        config = cli.uvicorn_config(app, 0)
        config.lifespan = "off"
        access.addHandler(capture)          # after the config: building it resets uvicorn's log handlers
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            await asyncio.sleep(0.01)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get("http://127.0.0.1:%d/ping?key=%s&alt=sse" % (sock.getsockname()[1], raw))
            return r.status_code
        finally:
            server.should_exit = True
            await task

    try:
        assert asyncio.run(scenario()) == 200
    finally:
        access.removeHandler(capture)
    assert len(lines) == 1 and "/ping" in lines[0] and "200" in lines[0], lines
    assert raw not in lines[0] and "key=" not in lines[0] and "?" not in lines[0], (
        "the raw key reached the access log: %s" % lines[0].replace(raw, "<key>"))



@mutation(M, '    install_log_redaction("uvicorn")                    # not uvicorn.access: see AccessPathOnly\n',
          '    install_log_redaction("uvicorn", "uvicorn.access")\n', expect="the access line failed to format")
def test_one_request_writes_one_well_formed_access_line_and_no_logging_error(capsys):
    """uvicorn formats its access line from the record args, so the handler must keep them: through its own
    handler (not a stand-in) a request leaves exactly one line, redacted, and no "Logging error" on stderr."""
    import socket

    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/m/{token}")
    async def shared(token: str) -> dict:
        return {"ok": True}

    token = "Zq9" * 8

    async def scenario() -> int:
        config = cli.uvicorn_config(app, 0)
        config.lifespan = "off"
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            await asyncio.sleep(0.01)
        try:
            async with httpx.AsyncClient() as client:
                url = "http://127.0.0.1:%d/m/%s?key=x" % (sock.getsockname()[1], token)
                return (await client.get(url)).status_code
        finally:
            server.should_exit = True
            await task

    assert asyncio.run(scenario()) == 200
    out, errors = capsys.readouterr()
    lines = [line for line in out.splitlines() if "GET" in line]
    assert "Logging error" not in errors and "Logging error" not in out, "the access line failed to format: " + errors
    assert len(lines) == 1 and lines[0].endswith("200 OK"), lines
    assert token not in lines[0] and "key=" not in lines[0], lines[0]


@mutation(M, "        if isinstance(args, tuple):\n", "        if isinstance(args, tuple) and len(args) == 5:\n",
          expect="a key reached the access record")
def test_an_access_record_of_another_shape_is_redacted_too():
    """A record that is not uvicorn's 5-tuple (and that its formatter may then refuse) still reaches the handler's
    error dump: its text arguments are redacted whatever the length."""
    raw = "rk-" + "A1b2C3d4" * 5 + "xyz"
    record = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1, "%s %s %d", ("peer", "key=" + raw, 7), None)
    assert cli.AccessPathOnly().filter(record)
    assert raw not in str(record.args), "a key reached the access record: %s" % str(record.args).replace(raw, "<key>")
    assert record.args[0] == "peer" and record.args[2] == 7


@mutation(M, "        elif isinstance(args, Mapping):", "        elif False:",
          expect="a key reached the mapping record")
def test_an_access_record_with_mapping_args_or_a_secret_in_its_format_is_redacted_too():
    """logging keeps a lone dict argument as a mapping (`%(path)s`); the format string itself is redacted as well."""
    raw = "rk-" + "A1b2C3d4" * 5 + "xyz"
    args = ({"path": "/x?key=" + raw, "n": 3},)
    record = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1, "%(path)s", args, None)
    assert isinstance(record.args, dict) and cli.AccessPathOnly().filter(record)
    shown = record.getMessage()
    assert raw not in shown, "a key reached the mapping record: %s" % shown.replace(raw, "<key>")
    assert record.args["n"] == 3


def access_record(raw: str, msg: str = "m", **extra) -> logging.LogRecord:
    record = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1, msg, None, None)
    record.__dict__.update(extra)
    assert cli.AccessPathOnly().filter(record)
    return record


RAW_KEY_TEXT = "rk-" + "A1b2C3d4" * 5 + "xyz"


@mutation(M, "        if isinstance(record.msg, str):\n", "        if False:\n",
          expect="a key reached the format string")
def test_a_secret_in_the_format_string_of_an_access_record_is_redacted():
    record = access_record(RAW_KEY_TEXT, "key=" + RAW_KEY_TEXT)
    assert RAW_KEY_TEXT not in record.getMessage(), "a key reached the format string"


@mutation(M, "        if record.exc_info and not record.exc_text:", "        if False:",
          expect="a key reached the traceback")
def test_a_secret_in_the_traceback_of_an_access_record_is_redacted():
    try:
        raise ValueError("Cookie: sessionid=PLANTEDcookie9876543210 key=" + RAW_KEY_TEXT)
    except ValueError:
        import sys

        record = access_record(RAW_KEY_TEXT, exc_info=sys.exc_info())
    shown = logging.Formatter().format(record)
    assert "ValueError" in shown and RAW_KEY_TEXT not in shown and "PLANTEDcookie" not in shown, (
        "a key reached the traceback: %s" % shown.replace(RAW_KEY_TEXT, "<key>"))


@mutation(M, "        if record.stack_info:", "        if False:", expect="a key reached the stack text")
def test_a_secret_in_the_stack_text_of_an_access_record_is_redacted():
    record = access_record(RAW_KEY_TEXT, stack_info="Stack: key=" + RAW_KEY_TEXT)
    assert RAW_KEY_TEXT not in logging.Formatter().format(record), "a key reached the stack text"


# ---- worker: the bucket ------------------------------------------------------------------------------------------
@pytest.fixture
def fake_worker(monkeypatch):
    """A provider package `relayfake` with a SPEC and a build_engine, and the worker loop replaced by a spy."""
    import relay_core.jobs.worker as worker_module
    from relay_core.storage import ObjectStore

    spec = FakeSpec("fake")
    monkeypatch.setitem(sys.modules, "relayfake.spec", SimpleNamespace(SPEC=spec))
    monkeypatch.setitem(sys.modules, "relayfake.engine", SimpleNamespace(build_engine=lambda *_: None))
    monkeypatch.setattr(cli, "PROVIDERS", {"fake": "relayfake"})
    bucket = "relay-test-" + uuid.uuid4().hex[:12]
    endpoint, access, secret = minio_settings()
    for name, value in {"MINIO_ENDPOINT": endpoint, "MINIO_ACCESS_KEY": access, "MINIO_SECRET_KEY": secret}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ObjectStore, "from_env", classmethod(lambda cls, env: cls(
        env["MINIO_ENDPOINT"], env["MINIO_ACCESS_KEY"], env["MINIO_SECRET_KEY"], bucket)))
    client = ObjectStore(endpoint, access, secret, bucket=bucket)._client
    ran: list[bool] = []

    async def serve(*_args, **_kwargs) -> int:
        ran.append(client.bucket_exists(bucket))
        return 0

    monkeypatch.setattr(worker_module, "serve", serve)
    yield SimpleNamespace(ran=ran, client=client, bucket=bucket)
    if client.bucket_exists(bucket):
        client.remove_bucket(bucket)


@mutation(M, "        await ObjectStore.from_env(os.environ).ensure_bucket()", "        pass",
          expect="the worker started without making the bucket")
def test_worker_makes_the_bucket_before_it_runs(fake_worker):
    assert cli.worker("fake") == 0
    assert fake_worker.ran == [True], "the worker started without making the bucket"


def test_worker_does_not_run_when_the_object_store_is_unreachable(fake_worker, monkeypatch, capsys):
    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:1")
    assert cli.worker("fake") == 1
    assert "object store is not usable at start" in err(capsys) and fake_worker.ran == []


@mutation(M, "    if isinstance(value, (dict, list, tuple)):\n", "    if False:\n",
          expect="a key reached the nested argument")
def test_a_dict_nested_in_the_args_of_an_access_record_is_redacted_and_the_tuple_stays_a_tuple():
    nested = ({"path": "/x?key=" + RAW_KEY_TEXT, "n": 3}, ("a", RAW_KEY_TEXT), [RAW_KEY_TEXT])
    record = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1, "%s %s %s", nested, None)
    assert cli.AccessPathOnly().filter(record)
    shown = record.getMessage()
    assert RAW_KEY_TEXT not in shown, "a key reached the nested argument: %s" % shown.replace(RAW_KEY_TEXT, "<key>")
    assert isinstance(record.args[1], tuple) and isinstance(record.args[0], dict), (
        "a key reached the nested argument unredacted or the argument lost its type: %r" % (record.args,))


@mutation(M, "    return redact_text(str(value))\n", "    return value\n",
          expect="a key reached the exception argument")
def test_an_exception_argument_of_an_access_record_is_redacted():
    args = (ValueError("key=" + RAW_KEY_TEXT), 7)
    record = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1, "%s %s", args, None)
    assert cli.AccessPathOnly().filter(record)
    shown = record.getMessage()
    assert RAW_KEY_TEXT not in shown, "a key reached the exception argument: %s" % shown.replace(RAW_KEY_TEXT, "<key>")
    assert record.args[1] == 7
