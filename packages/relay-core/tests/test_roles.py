"""The two service roles (I20): what each may do is a matrix compared with the database,
every api-side statement runs as `relay_api`, and the writes it must not make are refused."""

import asyncio
import uuid

import psycopg
from conftest import JOB_INSERT, job_args

from relay_core import billing
from relay_core.db import open_pool, tx
from relay_core.testing.mutation import mutation

GRANTS = "packages/relay-core/src/relay_core/db/migrations/0006_grants.sql"
MONEY = "packages/relay-core/src/relay_core/db/migrations/0020_money_v2.sql"
UNCLEAR = "packages/relay-core/src/relay_core/db/migrations/0015_unclear_replies.sql"
STRIKE_AT = "packages/relay-core/src/relay_core/db/migrations/0016_account_strike_at.sql"
ALERT_RULES = "packages/relay-core/src/relay_core/db/migrations/0023_alert_rules.sql"
S, INS, U, D = "SELECT", "INSERT", "UPDATE", "DELETE"
ALL = (S, INS, U, D)

# table -> privileges per role. A table missing here fails test_every_table_has_a_decision.
API = {
    "api_keys": ALL, "key_wallets": ALL, "ledger": (S, INS), "jobs": (S, INS, U), "job_events": ALL, "job_media": ALL,
    "accounts": (S, INS),                          # UPDATE is column-level, below; deleting is a worker action
    "account_pools": (S,), "account_charges": (S,), "leases": (S,), "worker_heartbeats": (S,),
    "account_shots": (S,), "provider_snapshots": (S,), "commands": (S, INS), "settings_kv": ALL, "request_logs": ALL,
    "admin_users": ALL, "admin_sessions": ALL, "admin_login_attempts": ALL, "schema_migrations": (),
    "billing_pin": (S,), "billing_alerts": (S, INS), "prompt_frames": (S, INS), "prompt_frame_locks": (S, INS),
    "unclear_replies": (S,),                       # UPDATE of the two acknowledge columns is column-level, below
    "account_health": (S,), "account_errors": (S,), "settings_history": (S, INS, D),
    "alert_rules": (S, INS, U),                    # the admin's switch and mute per alert kind
}
WORKER = {
    "api_keys": (S,), "key_wallets": (S, U), "ledger": (S, INS), "jobs": (S, INS, U),
    "job_events": ALL, "job_media": ALL, "accounts": ALL, "account_pools": ALL, "account_charges": ALL,
    "leases": ALL, "worker_heartbeats": ALL,
    "account_shots": ALL, "provider_snapshots": ALL, "commands": ALL, "settings_kv": ALL, "request_logs": ALL,
    "admin_users": (), "admin_sessions": (), "admin_login_attempts": (), "schema_migrations": (), "billing_pin": (S,),
    "billing_alerts": (S, INS), "prompt_frames": (), "prompt_frame_locks": (),
    "unclear_replies": (INS,),                     # append only: the writers insert with ON CONFLICT DO NOTHING
    "account_health": ALL, "account_errors": ALL, "settings_history": (S, INS, D),
    "alert_rules": (),                             # read by the admin routes only
}
API_ACCOUNT_COLUMNS = {"label", "proxy_host", "proxy_port", "proxy_auth_enc", "state", "state_reason", "updated_at",
                       "tier", "cycle_start"}
SECRET_COLUMNS = ("credential_enc", "credential_gen", "identity", "display_name", "provider_state", "last_error",
                  "proxy_ok", "proxy_checked_at", "strikes", "strike_at", "cooldown_until", "cooldown_reason",
                  "check_due_at", "tier_read_at")


def privileges(db, role):
    with db.connect() as c:
        tables = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'relay'")]
        return {t: tuple(p for p in ALL if c.execute("SELECT has_table_privilege(%s, %s, %s)",
                                                     (role, "relay." + t, p)).fetchone()[0]) for t in tables}


def as_role(db, role, statements):
    """Run each (sql, params) as `role` on a real login; returns the failures as (sql, error name)."""
    dsn = db.api_dsn if role == "relay_api" else db.worker_dsn
    failed = []
    with psycopg.connect(dsn, autocommit=True, options="-c search_path=relay") as c:
        for sql, params in statements:
            try:
                c.execute(sql, params)
            except psycopg.Error as e:
                failed.append((sql, type(e).__name__))
    return failed


@mutation("packages/relay-core/src/relay_core/db/migrations/0021_account_history.sql",
          "GRANT SELECT ON account_health, account_errors TO relay_api;",
          "GRANT SELECT, INSERT ON account_health, account_errors TO relay_api;",
          expect="'account_errors': ('INSERT', 'SELECT')")
@mutation(UNCLEAR, "GRANT SELECT ON unclear_replies TO relay_api;",
          "GRANT SELECT, DELETE ON unclear_replies TO relay_api;",
          expect="'unclear_replies': ('DELETE', 'SELECT')")
@mutation(UNCLEAR, "GRANT INSERT ON unclear_replies TO relay_worker;",
          "GRANT SELECT, INSERT ON unclear_replies TO relay_worker;",
          expect="'unclear_replies': ('INSERT', 'SELECT')")
@mutation(UNCLEAR, "GRANT INSERT ON unclear_replies TO relay_worker;",
          "GRANT INSERT, UPDATE ON unclear_replies TO relay_worker;",
          expect="'unclear_replies': ('INSERT', 'UPDATE')")
@mutation(ALERT_RULES, "GRANT SELECT, INSERT, UPDATE ON alert_rules TO relay_api;",
          "GRANT SELECT, INSERT, UPDATE, DELETE ON alert_rules TO relay_api;",
          expect="'alert_rules': ('DELETE', 'INSERT', 'SELECT', 'UPDATE')")
def test_every_table_has_a_decision_and_the_grants_match_the_matrix(db):
    api, worker = privileges(db, "relay_api"), privileges(db, "relay_worker")
    assert set(api) == set(API) == set(WORKER), "a table has no entry in the matrix: %s" % (set(api) ^ set(API))
    assert {t: tuple(sorted(p)) for t, p in api.items()} == {t: tuple(sorted(p)) for t, p in API.items()}
    assert {t: tuple(sorted(p)) for t, p in worker.items()} == {t: tuple(sorted(p)) for t, p in WORKER.items()}


@mutation(GRANTS, "GRANT UPDATE (label, proxy_host,", "GRANT UPDATE (credential_enc, label, proxy_host,",
           expect="assert {'credential_")
@mutation("packages/relay-core/src/relay_core/db/migrations/0008_account_restrict.sql",
          "cooldown_reason, check_due_at)", "cooldown_reason)", expect="assert {'check_due_a")
@mutation(MONEY, "GRANT UPDATE (tier, cycle_start) ON accounts TO relay_api;",
          "GRANT UPDATE (tier, tier_read_at, cycle_start) ON accounts TO relay_api;", expect="assert {'cycle_start")
def test_the_api_cannot_update_a_credential_or_worker_owned_account_columns(db):
    with db.connect() as c:
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                        "WHERE table_schema = 'relay' AND table_name = 'accounts'")}
        got = {col for col in cols if c.execute(
            "SELECT has_column_privilege('relay_api', 'relay.accounts', %s, 'UPDATE')", (col,)).fetchone()[0]}
    assert got == API_ACCOUNT_COLUMNS
    assert not set(SECRET_COLUMNS) & got


@mutation(UNCLEAR, "GRANT UPDATE (acknowledged_at, acknowledged_by) ON unclear_replies",
          "GRANT UPDATE (text, acknowledged_at, acknowledged_by) ON unclear_replies",
          expect="api column privileges on unclear_replies")
def test_the_api_updates_only_the_two_acknowledge_columns_of_an_unclear_reply_and_has_no_use_of_its_sequence(db):
    with db.connect() as c:
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                        "WHERE table_schema = 'relay' AND table_name = 'unclear_replies'")}
        got = {col for col in cols if c.execute(
            "SELECT has_column_privilege('relay_api', 'relay.unclear_replies', %s, 'UPDATE')", (col,)).fetchone()[0]}
        usage = {role: c.execute("SELECT has_sequence_privilege(%s, 'relay.unclear_replies_id_seq', 'USAGE')",
                                 (role,)).fetchone()[0] for role in ("relay_api", "relay_worker")}
    assert got == {"acknowledged_at", "acknowledged_by"}, "api column privileges on unclear_replies: %s" % sorted(got)
    assert usage == {"relay_api": False, "relay_worker": True}


def test_every_statement_the_api_makes_runs_under_relay_api(db):
    key = db.key(("p1", None, None))
    job = uuid.uuid4()
    account = uuid.uuid4()

    async def scenario():
        pool = await open_pool(db.api_dsn, max_size=4)
        try:
            async with tx(pool) as conn:      # create a job: the charge and the job row
                charged = await billing.charge(conn, key, "p1", 5, job)
                await conn.execute(JOB_INSERT, job_args(job, key, price=5))
            async with tx(pool) as conn:
                for row in charged.ledger_ids:
                    assert (await billing.refund(conn, row)).refunded
                await billing.ensure_wallets(conn, "p2")
                await billing.create_wallets(conn, key, {"p3": (1, None)})
        finally:
            await pool.close()

    asyncio.run(scenario())
    statements = [
        ("INSERT INTO api_keys (id, key_hash, key_prefix) VALUES (%s, 'api-hash', 'rk-a')", (uuid.uuid4(),)),
        ("UPDATE api_keys SET enabled = false, label = 'x', models = '{a}', max_unfinished = 3 WHERE id = %s", (key,)),
        ("UPDATE key_wallets SET day_limit = 9, total_limit = 99 WHERE key_id = %s", (key,)),
        ("UPDATE accounts SET tier = 'pro', cycle_start = current_date WHERE id = %s", (account,)),
        ("UPDATE jobs SET status = 'failed', error_code = 'x', title = 't', callback_status = 'pending' "
         "WHERE id = %s", (job,)),
        ("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'system', 's', 't')", (job,)),
        ("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
         "VALUES (%s, 'output', 0, 'k', 'video/mp4', 1)", (job,)),
        ("UPDATE job_media SET share_token = NULL WHERE job_id = %s", (job,)),
        ("DELETE FROM job_media WHERE job_id = %s", (job,)),
        ("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x00')", (account,)),
        ("UPDATE accounts SET label = 'l', state = 'disabled', state_reason = 'r', proxy_host = 'h', proxy_port = 1, "
         "proxy_auth_enc = NULL, updated_at = now() WHERE id = %s", (account,)),
        ("INSERT INTO commands (provider, kind, account_id, args) VALUES ('p1', 'check', %s, '{}')", (account,)),
        ("INSERT INTO settings_kv (key, value, updated_by) VALUES ('k', '1', 'admin')", ()),
        ("UPDATE settings_kv SET value = '2' WHERE key = 'k'", ()),
        ("DELETE FROM settings_kv WHERE key = 'k'", ()),
        ("INSERT INTO admin_users (username, password_hash) VALUES ('root', 'h')", ()),
        ("INSERT INTO admin_sessions (token_hash, user_id, expires_at) SELECT 't', id, now() FROM admin_users", ()),
        ("DELETE FROM admin_sessions WHERE expires_at <= now()", ()),
        ("INSERT INTO admin_login_attempts (ip) VALUES ('10.0.0.1')", ()),
        ("DELETE FROM admin_login_attempts WHERE at < now()", ()),
        ("INSERT INTO request_logs (route, status_code, duration_ms) VALUES ('/v1/x', 200, 1)", ()),
        ("DELETE FROM request_logs WHERE at < now()", ()),
        ("SELECT * FROM leases, account_pools, account_charges, worker_heartbeats, account_shots, "
         "provider_snapshots, ledger", ()),
        ("DELETE FROM job_events WHERE at < now()", ()),
        ("INSERT INTO alert_rules (source, kind, updated_by) VALUES ('live', 'disk_low', 'admin') "
         "ON CONFLICT (source, kind) DO UPDATE SET muted_until = now()", ()),
    ]
    assert as_role(db, "relay_api", statements) == []


@mutation(MONEY, "GRANT SELECT ON account_pools, account_charges TO relay_api;",
          "GRANT SELECT, INSERT, UPDATE ON account_pools, account_charges TO relay_api;",
          expect="INSERT INTO account_charges (account_id, job_id, pool, amount)")
def test_the_api_is_refused_what_belongs_to_the_worker(db):
    key = db.key(("p1", None, None))
    account = uuid.uuid4()
    db.one("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01') RETURNING 1", account)
    refused = [
        ("UPDATE accounts SET credential_enc = '\\x02' WHERE id = %s", (account,)),
        ("UPDATE accounts SET credential_gen = credential_gen + 1 WHERE id = %s", (account,)),
        ("UPDATE accounts SET strikes = 0 WHERE id = %s", (account,)),
        ("UPDATE accounts SET strike_at = now() WHERE id = %s", (account,)),
        ("UPDATE accounts SET cooldown_until = NULL WHERE id = %s", (account,)),
        ("UPDATE accounts SET check_due_at = now() WHERE id = %s", (account,)),
        ("UPDATE accounts SET proxy_ok = true WHERE id = %s", (account,)),
        ("UPDATE accounts SET proxy_checked_at = now() WHERE id = %s", (account,)),
        ("UPDATE accounts SET cooldown_reason = 'x' WHERE id = %s", (account,)),
        ("DELETE FROM accounts WHERE id = %s", (account,)),
        ("UPDATE accounts SET identity = 'someone' WHERE id = %s", (account,)),
        ("INSERT INTO leases (account_id, purpose, owner, until) VALUES (%s, 'check', 'w', now())", (account,)),
        ("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES ('w', 'p1', now(), now())", ()),
        ("INSERT INTO account_pools (account_id, pool) VALUES (%s, 'video')", (account,)),
        ("INSERT INTO account_charges (account_id, job_id, pool, amount) "
         "VALUES (%s, gen_random_uuid(), 'video', 1)", (account,)),
        ("UPDATE accounts SET tier_read_at = now() WHERE id = %s", (account,)),
        ("INSERT INTO provider_snapshots (provider, name, data) VALUES ('p1', 'n', '{}')", ()),
        ("UPDATE commands SET status = 'done'", ()),
        ("UPDATE ledger SET amount = 0", ()),
        ("DELETE FROM ledger", ()),
        ("CREATE TABLE relay.intruder (a int)", ()),
        ("DROP TABLE relay.jobs", ()),
        ("ALTER TABLE relay.api_keys ADD COLUMN x int", ()),
    ]
    failed = as_role(db, "relay_api", refused)
    assert [s for s, _ in failed] == [s for s, _ in refused]
    assert {name for _, name in failed} == {"InsufficientPrivilege"}
    assert key and db.one("SELECT credential_enc FROM accounts WHERE id = %s", account)[0] == b"\x01"


def test_the_worker_does_what_it_needs_and_not_ddl_the_ledger_or_the_admin_tables(db):
    key = db.key(("p1", None, None))
    job = db.job(key)
    account = uuid.uuid4()
    ok = [
        ("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01')", (account,)),
        ("UPDATE accounts SET credential_enc = '\\x02', credential_gen = 2, identity = 'me' WHERE id = %s", (account,)),
        ("INSERT INTO leases (account_id, purpose, owner, until) VALUES (%s, 'check', 'w', now())", (account,)),
        ("UPDATE leases SET until = now() WHERE owner = 'w'", ()),
        ("DELETE FROM leases WHERE owner = 'w'", ()),
        ("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES ('w', 'p1', now(), now())", ()),
        ("INSERT INTO account_pools (account_id, pool) VALUES (%s, 'video')", (account,)),
        ("INSERT INTO account_charges (account_id, job_id, pool, amount) "
         "VALUES (%s, %s, 'video', 1)", (account, job)),
        ("UPDATE jobs SET status = 'submitting' WHERE id = %s", (job,)),
        ("UPDATE commands SET status = 'done'", ()),
        ("INSERT INTO provider_snapshots (provider, name, data) VALUES ('p1', 'n', '{}')", ()),
        ("UPDATE key_wallets SET used_total = used_total", ()),
        ("SELECT * FROM api_keys, settings_kv", ()),
    ]
    assert as_role(db, "relay_worker", ok) == []
    refused = [
        ("UPDATE ledger SET amount = 0", ()), ("DELETE FROM ledger", ()),
        ("SELECT * FROM admin_users", ()), ("INSERT INTO admin_login_attempts (ip) VALUES ('10.0.0.1')", ()),
        ("UPDATE api_keys SET enabled = false", ()), ("CREATE TABLE relay.intruder (a int)", ()),
    ]
    assert {n for _, n in as_role(db, "relay_worker", refused)} == {"InsufficientPrivilege"}


def test_the_worker_refunds_a_charge_under_its_own_role(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario():
        owner_pool = await open_pool(db.dsn, max_size=2)
        worker_pool = await open_pool(db.worker_dsn, max_size=2)
        try:
            async with tx(owner_pool) as conn:
                charged = await billing.charge(conn, key, "p1", 4, job)
            async with tx(worker_pool) as conn:
                for row in charged.ledger_ids:
                    assert (await billing.refund(conn, row)).refunded
        finally:
            await owner_pool.close()
            await worker_pool.close()

    asyncio.run(scenario())
    assert db.wallet(key) == (0, 0)


# Every table that points at an account, with a statement that makes one row point at the given account.
CHILDREN = {
    "leases": "INSERT INTO leases (account_id, purpose, owner, until) VALUES (%s, 'check', 'w', now())",
    "account_pools": "INSERT INTO account_pools (account_id, pool) VALUES (%s, 'video')",
    "account_charges": "INSERT INTO account_charges (account_id, job_id, pool, amount) "
                       "SELECT %s, id, 'video', 2 FROM jobs LIMIT 1",
    "account_shots": "INSERT INTO account_shots (account_id, object_key) VALUES (%s, 'k')",
    "commands": "INSERT INTO commands (provider, kind, account_id) VALUES ('p1', 'check', %s)",
    "jobs": "UPDATE jobs SET account_id = %s",
}
DELETE_ACCOUNT = "DELETE FROM accounts WHERE id = %s"


@mutation("packages/relay-core/src/relay_core/db/migrations/0008_account_restrict.sql",
          "ALTER TABLE leases ADD CONSTRAINT leases_account_id_fkey\n    FOREIGN KEY (account_id) "
          "REFERENCES accounts(id) ON DELETE RESTRICT;",
          "ALTER TABLE leases ADD CONSTRAINT leases_account_id_fkey\n    FOREIGN KEY (account_id) "
          "REFERENCES accounts(id) ON DELETE CASCADE;",
           expect="refused == [(DELETE_ACCOUNT, \"ForeignKeyViolation\")], table")
def test_a_plain_delete_of_a_used_account_is_refused_by_the_database_and_removes_nothing(db):
    # Referential actions run as the table owner: with CASCADE or SET NULL a plain DELETE would reach worker and
    # money rows that the roles' grants forbid them to touch.
    for table, child in CHILDREN.items():
        db.job(db.key(("p1", None, None)))
        account = uuid.uuid4()
        db.one("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01') RETURNING 1", account)
        db.run(child, account)
        refused = as_role(db, "relay_worker", [(DELETE_ACCOUNT, (account,))])      # the role that may delete
        assert refused == [(DELETE_ACCOUNT, "ForeignKeyViolation")], table
        assert db.one("SELECT count(*) FROM accounts WHERE id = %s", account)[0] == 1, table
        assert db.one("SELECT count(*) FROM %s WHERE account_id = %%s" % table, account)[0] == 1, table
        db.reset()


def test_the_api_cannot_delete_an_account_not_even_an_unused_one(db):
    account = uuid.uuid4()
    db.one("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01') RETURNING 1", account)
    refused = as_role(db, "relay_api", [(DELETE_ACCOUNT, (account,))])
    assert refused == [(DELETE_ACCOUNT, "InsufficientPrivilege")]
    assert db.one("SELECT count(*) FROM accounts")[0] == 1


@mutation(STRIKE_AT, "ALTER TABLE accounts ADD COLUMN strike_at timestamptz NULL;",
          "ALTER TABLE accounts ADD COLUMN strike_at timestamptz NULL;\n"
          "GRANT UPDATE (strike_at) ON accounts TO relay_api;", expect="the api wrote strike_at")
def test_the_time_of_the_last_strike_is_a_nullable_column_only_the_worker_writes(db):
    account = uuid.uuid4()
    db.one("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01') RETURNING 1", account)
    assert db.one("SELECT strike_at FROM accounts WHERE id = %s", account)[0] is None, "a new account has a strike time"
    with db.connect() as c:
        nullable, default = c.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns WHERE table_schema = 'relay' "
            "AND table_name = 'accounts' AND column_name = 'strike_at'").fetchone()
    assert (nullable, default) == ("YES", None), "an expand migration adds a nullable column with no default"
    update = [("UPDATE accounts SET strike_at = now() WHERE id = %s", (account,))]
    assert as_role(db, "relay_api", update), "the api wrote strike_at"
    assert as_role(db, "relay_worker", update) == [], "the worker could not write strike_at"


@mutation(STRIKE_AT, "ALTER TABLE jobs ADD COLUMN fault_retries int NOT NULL DEFAULT 0;",
          "ALTER TABLE jobs ADD COLUMN fault_retries int;", expect="fault_retries must be NOT NULL with a default of 0")
def test_a_jobs_fault_retry_count_is_not_null_defaults_to_zero_and_both_roles_update_it(db):
    with db.connect() as c:
        nullable, default = c.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns WHERE table_schema = 'relay' "
            "AND table_name = 'jobs' AND column_name = 'fault_retries'").fetchone()
        grants = {role: c.execute("SELECT has_column_privilege(%s, 'relay.jobs', 'fault_retries', 'UPDATE')",
                                  (role,)).fetchone()[0] for role in ("relay_api", "relay_worker")}
    assert (nullable, default) == ("NO", "0"), "fault_retries must be NOT NULL with a default of 0"
    assert grants == {"relay_api": True, "relay_worker": True}, "the table-level UPDATE grant on jobs covers it"
