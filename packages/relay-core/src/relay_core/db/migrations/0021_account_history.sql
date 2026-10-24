-- Expand only. What the account detail shows beyond the account row: its health by hour, its past errors, and what
-- the check measured of its proxy. Everything here is written by the worker; the api reads it.
--
-- account_health: one row per account and UTC hour, holding the worst health seen in that hour (0 usable, 1 asleep,
-- 2 dead or proxy down), recorded from check and job outcomes. Rows older than 15 days are pruned by the writer, so
-- an account holds at most 15 x 24 rows. account_errors: the newest errors of an account (the writer keeps 20), with
-- the job they came from when a job did. Neither references accounts: an insert then takes no lock on the account
-- row (the outcome transactions lock it in their own order); `delete_unused_account` removes both with the account.
CREATE TABLE account_health (
    account_id uuid NOT NULL,
    hour       timestamptz NOT NULL,
    worst      smallint NOT NULL CHECK (worst BETWEEN 0 AND 2),
    PRIMARY KEY (account_id, hour)
);

CREATE TABLE account_errors (
    id         bigserial PRIMARY KEY,
    account_id uuid NOT NULL,
    at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    code       text NOT NULL,
    job_id     uuid NULL
);
CREATE INDEX account_errors_account_idx ON account_errors (account_id, id);

-- The proxy's exit address and the time one request through it took, measured by the check (NULL without a proxy or
-- when the request failed), and when; `check_failed_at`: the last check that could not read the account (a time
-- limit, an engine error), cleared by the next check that reads it. Worker columns: the api's column-level UPDATE
-- grant on accounts does not name them.
ALTER TABLE accounts ADD COLUMN proxy_exit_ip text NULL;
ALTER TABLE accounts ADD COLUMN proxy_latency_ms int NULL;
ALTER TABLE accounts ADD COLUMN proxy_exit_at timestamptz NULL;
ALTER TABLE accounts ADD COLUMN check_failed_at timestamptz NULL;

GRANT SELECT ON account_health, account_errors TO relay_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON account_health, account_errors TO relay_worker;
GRANT USAGE ON SEQUENCE account_errors_id_seq TO relay_worker;
