-- Keys and the two-wallet billing model (contracts §4.1). Money is integer points.
CREATE TABLE api_keys (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash       text NOT NULL UNIQUE,
    key_prefix     text NOT NULL,
    label          text NOT NULL DEFAULT '',
    enabled        boolean NOT NULL DEFAULT true,
    models         text[] NOT NULL DEFAULT '{*}',
    max_unfinished int NULL CHECK (max_unfinished IS NULL OR max_unfinished >= 0),
    created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at     timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX api_keys_prefix ON api_keys (key_prefix);

-- NULL limit = unlimited (set explicitly only); 0 = closed.
CREATE TABLE key_wallets (
    key_id      uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    provider    text NOT NULL,
    kind        text NOT NULL,
    day_limit   int NULL CHECK (day_limit IS NULL OR day_limit >= 0),
    total_limit int NULL CHECK (total_limit IS NULL OR total_limit >= 0),
    used_day    int NOT NULL DEFAULT 0 CHECK (used_day >= 0),
    used_total  int NOT NULL DEFAULT 0 CHECK (used_total >= 0),
    day_anchor  date NULL,
    PRIMARY KEY (key_id, provider, kind)
);

-- Append-only (no role can UPDATE or DELETE it). job_id is deferrable so a charge can precede its job row in the
-- same transaction; `adjust` rows have no job.
CREATE TABLE ledger (
    id          bigserial PRIMARY KEY,
    key_id      uuid NOT NULL,
    job_id      uuid NULL,
    provider    text NOT NULL,
    kind        text NOT NULL,
    entry       text NOT NULL CHECK (entry IN ('charge', 'refund', 'adjust')),
    amount      int NOT NULL,
    billing_day date NOT NULL,
    refund_of   bigint NULL REFERENCES ledger(id),
    at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (key_id, provider, kind) REFERENCES key_wallets (key_id, provider, kind),
    CHECK ((entry = 'refund') = (refund_of IS NOT NULL)),
    CHECK (entry = 'adjust' OR amount >= 0),
    CHECK ((entry = 'adjust') = (job_id IS NULL))
);
CREATE UNIQUE INDEX ledger_one_refund ON ledger (refund_of);
CREATE INDEX ledger_job ON ledger (job_id);
