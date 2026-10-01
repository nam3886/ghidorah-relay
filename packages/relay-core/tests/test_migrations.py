"""`relay migrate` is the only DDL path, idempotent, and builds the dola-shaped schema (contracts §4, §7.7)."""

import asyncio

import psycopg
import pytest
from conftest import API_PASSWORD

from relay_core.db import migrate, migration_files, open_pool, run_migrations
from relay_core.testing.mutation import mutation
from relay_core.testing.pg import disposable_database

TABLES = {
    "api_keys", "key_wallets", "ledger", "jobs", "job_events", "job_media", "accounts", "account_capacity",
    "account_charges", "leases", "account_shots", "admin_login_attempts", "commands", "settings_kv", "admin_users",
    "admin_sessions", "request_logs", "worker_heartbeats", "provider_snapshots", "schema_migrations",
    "billing_pin", "billing_alerts",
}
# contracts §4.5 and the unique indexes the money and lease rules stand on
INDEXES = {
    "jobs_queued", "jobs_poll", "jobs_callback", "jobs_unfinished", "leases_account", "leases_owner", "ledger_job",
    "job_events_job", "request_logs_at", "ledger_one_refund", "leases_one_job_lease",
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


@mutation("packages/relay-core/src/relay_core/db/migrate.py", "row = _pin_row(conn)", "row = None")
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
