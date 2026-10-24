-- Money v2 (plans/money-v2.md section 1.1). The owner-approved CONTRACT migration: it wipes the job history and the money
-- tables and recreates them, so it runs with every service stopped (dump first, plans/money-v2.md section 8). Kept:
-- api_keys, accounts, settings_kv, prompt_frames*, admin_*, provider_snapshots, request_logs, billing_pin,
-- worker_heartbeats. Media objects of the wiped jobs become orphans the existing orphan sweep removes.

-- 1. The job history. CASCADE takes every table that names a job: job_events, job_media, leases, unclear_replies,
--    commands, ledger (and billing_alerts, which names the ledger) and account_charges.
TRUNCATE jobs RESTART IDENTITY CASCADE;

-- 2. The old money schema goes, with no shim.
DROP TABLE billing_alerts, ledger, key_wallets, account_charges, account_capacity CASCADE;

-- 3. A job names the option it was priced by; `price` is now the total points charged, `account_cost` the expected
--    upstream cost and `upstream_price` the actual upstream cost, both in the pool's unit.
ALTER TABLE jobs DROP COLUMN wallet_kind,
    ADD COLUMN price_key text NOT NULL DEFAULT '',
    ADD COLUMN cost_source text NULL CHECK (cost_source IN ('reply', 'balance_delta'));

-- 4. An account's tier (NULL = free), when it was last read from the upstream, and the day its monthly cycle starts.
ALTER TABLE accounts ADD COLUMN tier text NULL CHECK (tier IN ('free', 'pro', 'ultra')),
    ADD COLUMN tier_read_at timestamptz NULL,
    ADD COLUMN cycle_start date NULL;

-- 5. The recreated money tables. NULL limit = unlimited (set explicitly only); 0 = closed.
-- scope '*' is the key's one points wallet; any other scope is a provider's cap on top of it.
CREATE TABLE key_wallets (
    key_id uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    scope text NOT NULL,
    day_limit int NULL CHECK (day_limit IS NULL OR day_limit >= 0),
    total_limit int NULL CHECK (total_limit IS NULL OR total_limit >= 0),
    used_day int NOT NULL DEFAULT 0 CHECK (used_day >= 0),
    used_total int NOT NULL DEFAULT 0 CHECK (used_total >= 0),
    day_anchor date NULL,
    PRIMARY KEY (key_id, scope));

-- Append-only (no role can UPDATE or DELETE it). job_id is deferrable so a charge can precede its job row in the same
-- transaction; `adjust` rows have no job. A charge writes one row per scope it took ('*' and the provider's cap).
CREATE TABLE ledger (
    id bigserial PRIMARY KEY, key_id uuid NOT NULL, scope text NOT NULL, job_id uuid NULL,
    provider text NULL,
    entry text NOT NULL CHECK (entry IN ('charge', 'refund', 'adjust')),
    amount int NOT NULL, billing_day date NOT NULL, refund_of bigint NULL REFERENCES ledger(id),
    at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (key_id, scope) REFERENCES key_wallets (key_id, scope),
    CHECK ((entry = 'refund') = (refund_of IS NOT NULL)),
    CHECK (entry = 'adjust' OR amount >= 0), CHECK ((entry = 'adjust') = (job_id IS NULL)));
CREATE UNIQUE INDEX ledger_one_refund ON ledger (refund_of);
CREATE INDEX ledger_job ON ledger (job_id);
ALTER TABLE ledger ADD CONSTRAINT ledger_job_fk FOREIGN KEY (job_id) REFERENCES jobs(id)
    DEFERRABLE INITIALLY DEFERRED;

-- What an account's pool knows: the balance the upstream last reported (it wins until its own refill), the refill the
-- upstream named, and the pool's rest. Spend is not stored: it is computed from account_charges.
CREATE TABLE account_pools (
    account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT, pool text NOT NULL,
    reported_left int NULL, reported_at timestamptz NULL, refill_at timestamptz NULL,
    rest_until timestamptz NULL, rest_reason text NULL, PRIMARY KEY (account_id, pool));
CREATE TABLE account_charges (
    id bigserial PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    job_id uuid NOT NULL REFERENCES jobs(id), pool text NOT NULL, amount int NOT NULL CHECK (amount >= 0),
    at timestamptz NOT NULL DEFAULT clock_timestamp(), refunded_at timestamptz NULL, UNIQUE (job_id, pool));
CREATE INDEX account_charges_window ON account_charges (account_id, pool, at) WHERE refunded_at IS NULL;

-- A clamp (a refund that would have taken a counter below 0), a drift (a wallet whose counters disagree with its
-- ledger) or a cost mismatch (an actual upstream cost that is not the expected one).
CREATE TABLE billing_alerts (
    id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT clock_timestamp(),
    alert text NOT NULL CHECK (alert IN ('clamp', 'drift', 'cost_mismatch')),
    key_id uuid NULL, scope text NULL, ledger_id bigint NULL REFERENCES ledger(id),
    amount int NULL, used_day int NULL, used_total int NULL,
    job_id uuid NULL, provider text NULL, price_key text NULL, expected int NULL, actual int NULL,
    detail text NULL, acknowledged_at timestamptz NULL, acknowledged_by text NULL,
    CHECK (alert = 'cost_mismatch' OR (key_id IS NOT NULL AND scope IS NOT NULL AND amount IS NOT NULL
                                       AND used_day IS NOT NULL AND used_total IS NOT NULL)),
    CHECK (alert <> 'clamp' OR ledger_id IS NOT NULL),
    CHECK (alert <> 'cost_mismatch' OR (job_id IS NOT NULL AND provider IS NOT NULL AND price_key IS NOT NULL
                                        AND expected IS NOT NULL AND actual IS NOT NULL)));
CREATE INDEX billing_alerts_at ON billing_alerts (at);

-- 6. Grants (0006 / 0009 / 0012, unchanged in spirit). The sequences are named: a blanket grant would hand the api the
--    sequence of unclear_replies, which 0015 keeps from it. The api sets an account's tier and cycle start (a tier the
--    upstream cannot be read for is set by the admin); the time it was last read is the worker's.
GRANT SELECT, INSERT, UPDATE, DELETE ON key_wallets TO relay_api;
GRANT SELECT, UPDATE ON key_wallets TO relay_worker;
GRANT SELECT, INSERT ON ledger TO relay_api, relay_worker;
GRANT SELECT ON account_pools, account_charges TO relay_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON account_pools, account_charges TO relay_worker;
GRANT SELECT, INSERT ON billing_alerts TO relay_api, relay_worker;
GRANT UPDATE (acknowledged_at, acknowledged_by) ON billing_alerts TO relay_api;
GRANT UPDATE (tier, cycle_start) ON accounts TO relay_api;
GRANT USAGE, SELECT ON SEQUENCE ledger_id_seq, billing_alerts_id_seq TO relay_api, relay_worker;
GRANT USAGE, SELECT ON SEQUENCE account_charges_id_seq TO relay_worker;

-- 7. Every key keeps a points wallet and a cap row per provider the deployment knows (an account or a setting names
--    it), all CLOSED: the owner opens each key again in the admin. `relay serve` adds the rows of a provider that
--    registers later (billing.ensure_wallets).
INSERT INTO key_wallets (key_id, scope, day_limit)
SELECT k.id, s.scope, 0
  FROM api_keys k
 CROSS JOIN (SELECT '*' AS scope
              UNION SELECT provider FROM accounts
              UNION SELECT split_part(key, '.', 1) FROM settings_kv WHERE key LIKE '%.%') s;

-- 8. The daily budget settings are replaced by `<p>.pools`. Stored prices stay (owner data).
DELETE FROM settings_kv WHERE key ~ '\.(video|image)_budget$';
