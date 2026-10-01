-- Table privileges for the two service roles (contracts §4.5). The owner runs migrations only.
-- Every table is decided here; tests/test_roles.py compares the result with the matrix and fails on a table
-- that has no decision, so a later expand migration must grant its own tables.
--
-- relay_api: jobs, charge and refund, sealed account rows, admin commands, settings, admin sessions, logs.
--   Cannot rewrite a credential or write leases, worker heartbeats, capacity, account charges or snapshots.
-- relay_worker: everything else except DDL. The ledger stays append-only for both roles. The admin tables
--   belong to the api only.
GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys, key_wallets TO relay_api;
GRANT SELECT ON api_keys, key_wallets TO relay_worker;
GRANT UPDATE ON key_wallets TO relay_worker;

GRANT SELECT, INSERT ON ledger TO relay_api, relay_worker;

GRANT SELECT, INSERT, UPDATE ON jobs TO relay_api, relay_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON job_events, job_media TO relay_api, relay_worker;

GRANT SELECT, INSERT, DELETE ON accounts TO relay_api;
GRANT UPDATE (label, proxy_host, proxy_port, proxy_auth_enc, proxy_ok, proxy_checked_at, state, state_reason,
              strikes, cooldown_until, cooldown_reason, check_due_at, updated_at) ON accounts TO relay_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON accounts TO relay_worker;

GRANT SELECT ON account_capacity, account_charges, leases, worker_heartbeats, account_shots, provider_snapshots
    TO relay_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON account_capacity, account_charges, leases, worker_heartbeats,
    account_shots, provider_snapshots TO relay_worker;

GRANT SELECT, INSERT ON commands TO relay_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON commands TO relay_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON settings_kv, request_logs TO relay_api, relay_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON admin_users, admin_sessions, admin_login_attempts TO relay_api;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA relay TO relay_api, relay_worker;
