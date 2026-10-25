"""`relay migrate` is the only DDL path, idempotent, and builds the dola-shaped schema."""

import asyncio
import uuid

import psycopg
import pytest
from conftest import API_PASSWORD, WORKER_PASSWORD

from relay_core.db import migrate, migration_files, open_pool, run_migrations
from relay_core.testing.mutation import mutation
from relay_core.testing.pg import disposable_database

TABLES = {
    "api_keys", "key_wallets", "ledger", "jobs", "job_events", "job_media", "accounts", "account_pools",
    "account_charges", "leases", "account_shots", "admin_login_attempts", "commands", "settings_kv", "admin_users",
    "admin_sessions", "request_logs", "worker_heartbeats", "provider_snapshots", "schema_migrations",
    "billing_pin", "billing_alerts", "prompt_frames", "prompt_frame_locks", "unclear_replies",
    "account_health", "account_errors", "settings_history", "alert_rules",
}
# the unique indexes the money and lease rules stand on
INDEXES = {
    "jobs_queued", "jobs_poll", "jobs_callback", "jobs_unfinished", "leases_account", "leases_owner", "ledger_job",
    "job_events_job", "request_logs_at", "ledger_one_refund", "leases_one_job_lease",
    "account_charges_window",
}
# members built later, by expand migrations
NOT_YET = [("accounts", "credential_version"), ("accounts", "slots")]


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
        assert main(["migrate"]) == 0
        assert "applied 0001" in capsys.readouterr().out
        assert main(["migrate"]) == 0
        assert "up to date" in capsys.readouterr().out
        login = psycopg.conninfo.make_conninfo(dsn, user="relay_api", password=API_PASSWORD)
        with psycopg.connect(login) as c:
            assert c.execute("SELECT current_user").fetchone()[0] == "relay_api"
        monkeypatch.setenv("DATABASE_URL", psycopg.conninfo.make_conninfo(dsn, dbname="relay_no_such_db"))
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
    """Migrate `dsn` to 0019 only (a copy of the files in `tmp_path`), then point at a copy that ends at 0020, so the
    next run applies 0020 alone and a later expand migration does not blur what 0020 kept."""
    files = migration_files()
    upto = tmp_path / "upto20"
    upto.mkdir()
    for version, path in files:
        if version < 20:
            (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        if version <= 20:
            (upto / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
    assert run_migrations(dsn)[-1] == 19
    monkeypatch.setattr(migrate, "MIGRATIONS", upto)


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
    c.execute("INSERT INTO commands (id, provider, kind, account_id, args, status, attempts) "
              "VALUES (%s, 'p1', 'wake', %s, '{\"why\": \"owner\"}', 'queued', 2)", (ACCOUNT_COMMAND, account))
    admin = c.execute("INSERT INTO admin_users (username, password_hash) VALUES ('boss', 'hash') RETURNING id"
                      ).fetchone()[0]
    c.execute("INSERT INTO admin_sessions (token_hash, user_id, expires_at) "
              "VALUES ('tok', %s, now() + interval '1 day')", (admin,))
    c.execute("INSERT INTO admin_login_attempts (ip) VALUES ('203.0.113.9')")
    c.execute("INSERT INTO provider_snapshots (provider, name, data) VALUES ('p1', 'catalog', '{\"m\": 1}')")
    c.execute("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at, slots_busy) "
              "VALUES ('w1', 'p1', now(), now(), 3)")
    return key, account


ACCOUNT_COMMAND = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
# the rows 0020 keeps: (table, where) read back by their own key
KEPT = (("api_keys", "key_prefix = 'rk-x'"), ("accounts", "provider = 'p1'"), ("settings_kv", "key = 'p1.prices'"),
        ("settings_kv", "key = 'log_retention_days'"), ("prompt_frames", "provider = 'p1'"),
        ("commands", "id = '%s'" % ACCOUNT_COMMAND), ("admin_users", "username = 'boss'"),
        ("admin_sessions", "token_hash = 'tok'"), ("admin_login_attempts", "ip = '203.0.113.9'"),
        ("provider_snapshots", "name = 'catalog'"), ("worker_heartbeats", "worker_id = 'w1'"),
        ("request_logs", "route = '/v1/x'"))


def _rows(c, table, where):
    cur = c.execute("SELECT * FROM %s WHERE %s" % (table, where))
    names = [d.name for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def test_0020_wipes_jobs_and_money_and_keeps_keys_accounts_settings_frames(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        _below_0020(tmp_path, monkeypatch, dsn)
        with _owner(dsn) as c:
            key, account = _plant_the_0019_schema(c)
            before = {(t, w): _rows(c, t, w) for t, w in KEPT}
        assert run_migrations(dsn)[0] == 20          # with the later ones, which wipe nothing
        with _owner(dsn) as c:
            after = {(t, w): _rows(c, t, w) for t, w in KEPT}
            def count(table):
                return c.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
            wiped = {t: count(t) for t in ("jobs", "job_events", "job_media", "leases", "unclear_replies",
                                           "ledger", "billing_alerts", "account_charges", "account_pools")}
            kept = {t: count(t) for t in ("api_keys", "accounts", "prompt_frames", "request_logs", "billing_pin")}
            stored = c.execute("SELECT key FROM settings_kv ORDER BY key").fetchall()
            jobs_columns = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                                    "WHERE table_schema = 'relay' AND table_name = 'jobs'")}
            commands = count("commands")
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
    for (table, where), rows in before.items():
        assert len(rows) == 1, "planted: %s %s" % (table, where)
        assert len(after[table, where]) == 1, "kept: %s %s" % (table, where)
        gone = {k: (v, after[table, where][0].get(k)) for k, v in rows[0].items() if after[table, where][0].get(k) != v}
        assert not gone, "%s changed (before, after): %r" % (table, gone)
    assert after["commands", "id = '%s'" % ACCOUNT_COMMAND][0]["attempts"] == 2, "the account command keeps its fields"
    assert commands == 1, "only the account command is left"


def test_0020_deletes_the_commands_that_name_a_job_and_keeps_the_rest(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        _below_0020(tmp_path, monkeypatch, dsn)
        with _owner(dsn) as c:
            _plant_the_0019_schema(c)
        run_migrations(dsn)
        with _owner(dsn) as c:
            kinds = c.execute("SELECT kind, job_id FROM commands ORDER BY kind").fetchall()
            fk = c.execute("SELECT count(*) FROM pg_constraint WHERE conname = 'commands_job_id_fkey' "
                           "AND confrelid = 'relay.jobs'::regclass").fetchone()[0]
    assert kinds == [("wake", None)], "the 'recover' command of the wiped job is gone, the account command stays"
    assert fk == 1, "commands.job_id names a job again"


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


REFUND_TRIGGER = "packages/relay-core/src/relay_core/db/migrations/0020_money_v2.sql"


@mutation(REFUND_TRIGGER, "(NEW.key_id, NEW.scope, NEW.job_id, NEW.provider, NEW.amount) THEN",
          "(NEW.key_id, NEW.scope, NEW.job_id, NEW.provider, c.amount) THEN",
          expect="DID NOT RAISE")
@mutation(REFUND_TRIGGER, "(NEW.key_id, NEW.scope, NEW.job_id, NEW.provider, NEW.amount) THEN",
          "(NEW.key_id, NEW.scope, c.job_id, NEW.provider, NEW.amount) THEN", expect="DID NOT RAISE")
@mutation(REFUND_TRIGGER, "(NEW.key_id, NEW.scope, NEW.job_id, NEW.provider, NEW.amount) THEN",
          "(NEW.key_id, c.scope, NEW.job_id, NEW.provider, NEW.amount) THEN", expect="DID NOT RAISE")
@mutation(REFUND_TRIGGER, "(NEW.key_id, NEW.scope, NEW.job_id, NEW.provider, NEW.amount) THEN",
          "(c.key_id, NEW.scope, NEW.job_id, NEW.provider, NEW.amount) THEN", expect="DID NOT RAISE")
@mutation(REFUND_TRIGGER, "(NEW.key_id, NEW.scope, NEW.job_id, NEW.provider, NEW.amount) THEN",
          "(NEW.key_id, NEW.scope, NEW.job_id, c.provider, NEW.amount) THEN", expect="DID NOT RAISE")
@mutation(REFUND_TRIGGER, "WHERE id = NEW.refund_of AND entry = 'charge';", "WHERE id = NEW.refund_of;",
          expect="DID NOT RAISE")
def test_the_ledger_refuses_a_refund_that_is_not_the_full_reversal_of_its_charge(db):
    key, other = db.key(("p1", None, None)), db.key(("p1", None, None))
    job, other_job = db.job(key, price=5), db.job(key, price=0)
    charge = db.one("INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day) "
                    "VALUES (%s, '*', %s, 'p1', 'charge', 5, current_date) RETURNING id", key, job)[0]
    insert = ("INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day, refund_of) "
              "VALUES (%s, %s, %s, %s, 'refund', %s, current_date, %s)")
    wrong = {"amount 50": (key, "*", job, "p1", 50), "another job": (key, "*", other_job, "p1", 5),
             "another scope": (key, "p1", job, "p1", 5), "another key": (other, "*", job, "p1", 5),
             "another provider": (key, "*", job, "p2", 5)}
    for what, row in wrong.items():
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run(insert, *row, charge)
        assert db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 0, what + " was accepted"
    refund = db.one(insert + " RETURNING id", key, "*", job, "p1", 5, charge)[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.run(insert, key, "*", job, "p1", 5, refund)         # a refund of a refund: a refund is of a charge only
    assert db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 1


# ---- key_last_used: when and from where a key last called /v1 ---------------------------------------------------
LAST_USED = next(v for v, p in migration_files() if p.name.endswith("_key_last_used.sql"))


def test_key_last_used_adds_two_empty_columns_keeps_every_key_and_lets_the_api_role_write_them(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        real = migrate.MIGRATIONS
        for version, path in migration_files():
            if version < LAST_USED:
                (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
        assert run_migrations(dsn)[-1] == LAST_USED - 1
        monkeypatch.setattr(migrate, "MIGRATIONS", real)
        key = uuid.uuid4()
        with _owner(dsn) as c:
            c.execute("INSERT INTO api_keys (id, key_hash, key_prefix, label) VALUES (%s, 'h', 'rk-old', 'kept')",
                      (key,))
        assert run_migrations(dsn)[0] == LAST_USED
        with _owner(dsn) as c:
            row = _rows(c, "api_keys", "id = '%s'" % key)[0]
        assert (row["label"], row["key_prefix"], row["last_used_at"], row["last_used_ip"]) == (
            "kept", "rk-old", None, None)
        login = psycopg.conninfo.make_conninfo(dsn, user="relay_api", password=API_PASSWORD,
                                               options="-c search_path=relay")
        with psycopg.connect(login, autocommit=True) as c:
            c.execute("UPDATE api_keys SET last_used_at = now(), last_used_ip = '203.0.113.8' WHERE id = %s", (key,))
        with _owner(dsn) as c:
            assert _rows(c, "api_keys", "id = '%s'" % key)[0]["last_used_ip"] == "203.0.113.8"


REFILL_WINDOW = next(v for v, p in migration_files() if p.name.endswith("_pool_refill_window.sql"))


def test_pool_refill_window_adds_an_empty_column_keeps_every_pool_row_and_lets_the_worker_write_it(tmp_path,
                                                                                                  monkeypatch):
    with disposable_database() as dsn:
        real = migrate.MIGRATIONS
        for version, path in migration_files():
            if version < REFILL_WINDOW:
                (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(migrate, "MIGRATIONS", tmp_path)
        assert run_migrations(dsn)[-1] == REFILL_WINDOW - 1
        monkeypatch.setattr(migrate, "MIGRATIONS", real)
        account = uuid.uuid4()
        with _owner(dsn) as c:
            c.execute("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01')", (account,))
            c.execute("INSERT INTO account_pools (account_id, pool, reported_left, reported_at, refill_at) "
                      "VALUES (%s, 'video', 3, now(), now() + interval '1 hour')", (account,))
        assert run_migrations(dsn)[0] == REFILL_WINDOW
        with _owner(dsn) as c:
            row = _rows(c, "account_pools", "account_id = '%s'" % account)[0]
        assert (row["reported_left"], row["refill_window"]) == (3, None)
        login = psycopg.conninfo.make_conninfo(dsn, user="relay_worker", password=WORKER_PASSWORD,
                                               options="-c search_path=relay")
        with psycopg.connect(login, autocommit=True) as c:
            c.execute("UPDATE account_pools SET refill_window = '5h' WHERE account_id = %s", (account,))
        with _owner(dsn) as c:
            assert _rows(c, "account_pools", "account_id = '%s'" % account)[0]["refill_window"] == "5h"


# ---- pool_drift alerts: the worker stores them next to the wallet and cost alerts ------------------------------
POOL_DRIFT_SQL = "packages/relay-core/src/relay_core/db/migrations/0026_pool_drift_alerts.sql"


@mutation(POOL_DRIFT_SQL, "    CHECK (alert <> 'pool_drift' OR (account_id IS NOT NULL AND scope IS NOT NULL",
          "    CHECK (alert <> 'pool_drift' OR (scope IS NOT NULL", expect="DID NOT RAISE")
@mutation(POOL_DRIFT_SQL, "    CHECK (alert IN ('cost_mismatch', 'pool_drift') OR (key_id IS NOT NULL",
          "    CHECK (true OR (key_id IS NOT NULL", expect="DID NOT RAISE")
def test_a_pool_drift_names_its_account_pool_and_figures_and_a_wallet_alert_still_names_its_key(db):
    account = uuid.uuid4()
    with psycopg.connect(db.worker_dsn, autocommit=True, options="-c search_path=relay") as c:
        c.execute("INSERT INTO billing_alerts (alert, account_id, scope, provider, expected, actual) "
                  "VALUES ('pool_drift', %s, 'video', 'p1', 4, 0)", (account,))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.run("INSERT INTO billing_alerts (alert, scope, provider, expected, actual) "
               "VALUES ('pool_drift', 'video', 'p1', 4, 0)")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.run("INSERT INTO billing_alerts (alert, account_id, scope, provider, expected, actual) "
               "VALUES ('drift', %s, 'video', 'p1', 4, 0)", account)          # a wallet drift names its key
    db.run("INSERT INTO billing_alerts (alert, job_id, provider, price_key, expected, actual) "
           "VALUES ('cost_mismatch', %s, 'p1', 'k', 1, 2)", db.job(db.key(("p1", None, None))))
    assert db.one("SELECT count(*) FROM billing_alerts")[0] == 2
