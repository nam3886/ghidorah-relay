"""`relay migrate` is the only DDL path, idempotent, and builds the dola-shaped schema."""

import asyncio
import uuid

import psycopg
import pytest
from conftest import API_PASSWORD

from relay_core.db import migrate, migration_files, open_pool, run_migrations
from relay_core.testing.mutation import mutation
from relay_core.testing.pg import disposable_database

TABLES = {
    "api_keys", "key_wallets", "ledger", "jobs", "job_events", "job_media", "accounts", "account_pools",
    "account_charges", "leases", "account_shots", "admin_login_attempts", "commands", "settings_kv", "admin_users",
    "admin_sessions", "request_logs", "worker_heartbeats", "provider_snapshots", "schema_migrations",
    "billing_pin", "billing_alerts", "prompt_frames", "prompt_frame_locks", "unclear_replies",
}
# the unique indexes the money and lease rules stand on
INDEXES = {
    "jobs_queued", "jobs_poll", "jobs_callback", "jobs_unfinished", "leases_account", "leases_owner", "ledger_job",
    "job_events_job", "request_logs_at", "ledger_one_refund", "leases_one_job_lease",
    "account_charges_window",
}
# members built later, by expand migrations
NOT_YET = [("jobs", "source_job_id"), ("jobs", "source_idx"), ("jobs", "pin_account_id"),
           ("accounts", "credential_version"), ("accounts", "slots")]


def schema_state(dsn):
    with psycopg.connect(dsn) as c:
        tables = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'relay'")}
        indexes = {r[0] for r in c.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'relay'")}
        columns = {(r[0], r[1]) for r in c.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'relay'")}
        roles = {r[0] for r in c.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'relay\\_%'")}
    return tables, indexes, columns, roles


def test_a_fresh_database_gets_the_whole_schema_and_both_roles():
    with disposable_database() as dsn:
        applied = run_migrations(dsn)
        tables, indexes, columns, roles = schema_state(dsn)
    assert applied == [v for v, _ in migration_files()]
    assert tables == TABLES
    assert INDEXES <= indexes
    assert {"relay_api", "relay_worker"} <= roles
    assert [pair for pair in NOT_YET if pair in columns] == []


def test_running_migrate_again_changes_nothing():
    with disposable_database() as dsn:
        run_migrations(dsn)
        before = schema_state(dsn)
        assert run_migrations(dsn) == []
        assert schema_state(dsn) == before
        with psycopg.connect(dsn) as c:
            versions = [r[0] for r in c.execute("SELECT version FROM relay.schema_migrations ORDER BY 1")]
    assert versions == [v for v, _ in migration_files()]


def test_opening_a_pool_runs_no_ddl():
    async def scenario(dsn):
        pool = await open_pool(dsn, max_size=2)
        try:
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
        finally:
            await pool.close()

    with disposable_database() as dsn:
        asyncio.run(scenario(dsn))
        with psycopg.connect(dsn) as c:
            assert c.execute("SELECT count(*) FROM pg_namespace WHERE nspname = 'relay'").fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN "
                             "('pg_catalog', 'information_schema')").fetchone()[0] == 0


def test_a_gap_in_the_numbering_is_refused(tmp_path, monkeypatch):
    for name in ("0001_a.sql", "0003_c.sql"):
        (tmp_path / name).write_text("SELECT 1;")
    monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
    with pytest.raises(RuntimeError, match="without gaps"):
        migration_files()


def test_a_failing_migration_leaves_nothing_behind(tmp_path, monkeypatch):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE ok_table (a int);")
    (tmp_path / "0002_bad.sql").write_text("CREATE TABLE half (a int); SELECT 1/0;")
    monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
    with disposable_database() as dsn:
        with pytest.raises(psycopg.errors.DivisionByZero):
            run_migrations(dsn)
        with psycopg.connect(dsn) as c:
            tables = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'relay'")}
            versions = [r[0] for r in c.execute("SELECT version FROM relay.schema_migrations")]
    assert tables == {"ok_table", "schema_migrations"}
    assert versions == [1]


def test_bad_arguments_are_refused_before_any_change():
    with disposable_database() as dsn:
        with pytest.raises(ValueError, match="not service roles"):
            run_migrations(dsn, passwords={"postgres": "x"})
        with pytest.raises(psycopg.Error):
            run_migrations(dsn, billing_tz="Not/AZone")
        with psycopg.connect(dsn) as c:
            assert c.execute("SELECT count(*) FROM pg_namespace WHERE nspname = 'relay'").fetchone()[0] == 0


def test_the_migrate_command_applies_sets_role_passwords_and_is_idempotent(monkeypatch, capsys):
    from relay_cli.main import main

    # the roles are cluster-wide: the password must be the one the session fixture gave them
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["migrate"]) == 1
    assert "DATABASE_URL is not set" in capsys.readouterr().err
    with disposable_database() as dsn:
        monkeypatch.setenv("DATABASE_URL", dsn)
        monkeypatch.setenv("RELAY_API_DB_PASSWORD", API_PASSWORD)
        monkeypatch.setenv("BILLING_TZ", "UTC")
        assert main(["migrate"]) == 0
        assert "applied 0001" in capsys.readouterr().out
        assert main(["migrate"]) == 0
        assert "up to date" in capsys.readouterr().out
        login = psycopg.conninfo.make_conninfo(dsn, user="relay_api", password=API_PASSWORD)
        with psycopg.connect(login) as c:
            assert c.execute("SELECT current_user").fetchone()[0] == "relay_api"
        monkeypatch.setenv("BILLING_TZ", "Not/AZone")
        assert main(["migrate"]) == 1
        assert capsys.readouterr().err.startswith("relay migrate: ")


@mutation("packages/relay-core/src/relay_core/db/migrate.py", "row = _pin_row(conn)", "row = None",
           expect="violates unique constraint \"billing_pin_pkey\"")
def test_a_timezone_mismatch_is_refused_before_any_pending_migration_is_applied(tmp_path, monkeypatch):
    from relay_core.billing import BillingTzMismatch

    with disposable_database() as dsn:
        run_migrations(dsn, billing_tz="Asia/Tokyo")
        for _, path in migration_files():
            (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        next_version = len(migration_files()) + 1
        (tmp_path / ("%04d_probe.sql" % next_version)).write_text("CREATE TABLE pending_probe (a int);")
        monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
        with pytest.raises(BillingTzMismatch):
            run_migrations(dsn, billing_tz="UTC")
        with psycopg.connect(dsn) as c:
            assert c.execute("SELECT to_regclass('relay.pending_probe')").fetchone()[0] is None
            assert c.execute("SELECT max(version) FROM relay.schema_migrations").fetchone()[0] == next_version - 1
        assert run_migrations(dsn, billing_tz="Asia/Tokyo") == [next_version]      # the same value goes through


# ---- 0020: the owner-approved wipe ------------------------------------------------------------------------------
def _owner(dsn):
    return psycopg.connect(dsn, autocommit=True, options="-c search_path=relay")


def _below_0020(tmp_path, monkeypatch, dsn):
    """Migrate `dsn` to 0019 only (a copy of the files in `tmp_path`), then put the real directory back."""
    real = migrate.MIGRATIONS
    for version, path in migration_files():
        if version < 20:
            (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
    assert run_migrations(dsn)[-1] == 19
    monkeypatch.setattr(migrate, "MIGRATIONS", real)


def _plant_the_0019_schema(c):
    """One key with a wallet, a failed job with everything that names it, an account with its pool rows, a stored price
    table and the two budget settings, a frame and a log row: the data 0020 either wipes or keeps."""
    key, job, account = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    c.execute("INSERT INTO api_keys (id, key_hash, key_prefix) VALUES (%s, 'h', 'rk-x')", (key,))
    c.execute("INSERT INTO key_wallets (key_id, provider, kind, day_limit) VALUES (%s, 'p1', 'video', 5)", (key,))
    c.execute("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01')", (account,))
    c.execute("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, "
              "completion, after_sent, submit_timeout_s, wallet_kind, price, account_id) VALUES (%s, 'p1', %s, "
              "'generate', 'video', 'm', 'p', 'mk', 'failed', 'detach', 'reconcile', 60, 'video', 5, %s)",
              (job, key, account))
    ledger = c.execute("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
                       "VALUES (%s, %s, 'p1', 'video', 'charge', 5, current_date) RETURNING id", (key, job)
                       ).fetchone()[0]
    c.execute("INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, used_total) "
              "VALUES ('clamp', %s, 'p1', 'video', %s, 5, 0, 0)", (key, ledger))
    c.execute("INSERT INTO account_capacity (account_id, pool, left_n, source) VALUES (%s, 'video', 3, 'counted')",
              (account,))
    c.execute("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) "
              "VALUES (%s, %s, 'video', 1, current_date)", (account, job))
    c.execute("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'system', 's', 't')", (job,))
    c.execute("INSERT INTO commands (provider, kind, job_id) VALUES ('p1', 'recover', %s)", (job,))
    c.execute("INSERT INTO unclear_replies (provider, job_id, text, wording, group_id) "
              "VALUES ('p1', %s, 't', 'w', 'g')", (job,))
    c.execute("INSERT INTO settings_kv (key, value) VALUES ('p1.prices', '{\"x\": 3}'), "
              "('p1.video_budget', '4'), ('p1.image_budget', '9'), ('log_retention_days', '7')")
    c.execute("INSERT INTO prompt_frames (provider, kind, model, text, created_by) "
              "VALUES ('p1', 'video', NULL, '{{client_prompt}} {{tracking_code}}', 'me')")
    c.execute("INSERT INTO request_logs (route, status_code, duration_ms) VALUES ('/v1/x', 200, 1)")
    return key, account


def test_0020_wipes_jobs_and_money_and_keeps_keys_accounts_settings_frames(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        _below_0020(tmp_path, monkeypatch, dsn)
        with _owner(dsn) as c:
            key, account = _plant_the_0019_schema(c)
        assert run_migrations(dsn) == [20]
        with _owner(dsn) as c:
            def count(table):
                return c.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
            wiped = {t: count(t) for t in ("jobs", "job_events", "job_media", "leases", "commands", "unclear_replies",
                                           "ledger", "billing_alerts", "account_charges", "account_pools")}
            kept = {t: count(t) for t in ("api_keys", "accounts", "prompt_frames", "request_logs", "billing_pin")}
            stored = c.execute("SELECT key FROM settings_kv ORDER BY key").fetchall()
            jobs_columns = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                                    "WHERE table_schema = 'relay' AND table_name = 'jobs'")}
            tier = c.execute("SELECT tier, tier_read_at, cycle_start FROM accounts").fetchone()
            first_ledger = c.execute("INSERT INTO ledger (key_id, scope, entry, amount, billing_day) "
                                     "VALUES (%s, '*', 'adjust', 1, current_date) RETURNING id", (key,)
                                     ).fetchone()[0]
    assert wiped == dict.fromkeys(wiped, 0)
    assert kept == {"api_keys": 1, "accounts": 1, "prompt_frames": 1, "request_logs": 1, "billing_pin": 1}
    assert stored == [("log_retention_days",), ("p1.prices",)]
    assert "wallet_kind" not in jobs_columns and {"price_key", "cost_source"} <= jobs_columns
    assert tier == (None, None, None), "an account has no tier until it is read or set"
    assert first_ledger == 1, "the ledger starts again from 1"
    assert account


def test_0020_leaves_every_key_with_closed_star_and_provider_rows(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        _below_0020(tmp_path, monkeypatch, dsn)
        with _owner(dsn) as c:
            key, _ = _plant_the_0019_schema(c)
            other = uuid.uuid4()
            c.execute("INSERT INTO api_keys (id, key_hash, key_prefix) VALUES (%s, 'h2', 'rk-y')", (other,))
            c.execute("INSERT INTO settings_kv (key, value) VALUES ('p2.claims_enabled', 'true')")
        run_migrations(dsn)
        with _owner(dsn) as c:
            rows = c.execute("SELECT key_id, scope, day_limit, total_limit, used_day, used_total FROM key_wallets "
                             "ORDER BY key_id, scope").fetchall()
    for k in (key, other):
        assert [r[1:] for r in rows if r[0] == k] == [("*", 0, None, 0, 0), ("p1", 0, None, 0, 0),
                                                       ("p2", 0, None, 0, 0)]
    assert len(rows) == 6, "a scope appears once per key, however many accounts or settings name it"


def test_0020_drops_the_budget_settings(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        _below_0020(tmp_path, monkeypatch, dsn)
        with _owner(dsn) as c:
            _plant_the_0019_schema(c)
            c.execute("INSERT INTO settings_kv (key, value) VALUES ('vibes.video_budget', '1'), "
                      "('p1.video_budget_note', '1'), ('p1.pools', '{}')")
        run_migrations(dsn)
        with _owner(dsn) as c:
            keys = [r[0] for r in c.execute("SELECT key FROM settings_kv ORDER BY key")]
    assert keys == ["log_retention_days", "p1.pools", "p1.prices", "p1.video_budget_note"]
