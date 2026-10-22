-- Accounts, pools and leases. Not here: credential_version and slots, which belong to the
-- members that use them and arrive by an expand migration.
CREATE TABLE accounts (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider          text NOT NULL,
    label             text NOT NULL DEFAULT '',
    identity          text NULL,
    display_name      text NULL,
    credential_enc    bytea NOT NULL,
    credential_gen    int NOT NULL DEFAULT 1,
    proxy_host        text NULL,
    proxy_port        int NULL,
    proxy_auth_enc    bytea NULL,
    proxy_ok          boolean NULL,
    proxy_checked_at  timestamptz NULL,
    state             text NOT NULL DEFAULT 'available' CHECK (state IN ('available', 'dead', 'disabled')),
    state_reason      text NULL,
    strikes           int NOT NULL DEFAULT 0,
    cooldown_until    timestamptz NULL,
    cooldown_reason   text NULL,
    provider_state    jsonb NOT NULL DEFAULT '{}',
    last_error        text NULL,
    last_used_at      timestamptz NULL,
    last_checked_at   timestamptz NULL,
    check_due_at      timestamptz NULL,
    created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (provider, identity)
);

ALTER TABLE jobs ADD CONSTRAINT jobs_account_fk FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL;

CREATE TABLE account_capacity (
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    pool        text NOT NULL,
    left_n      int NULL,
    known_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    source      text NOT NULL CHECK (source IN ('reported', 'counted', 'reset')),
    day_anchor  date NULL,
    rest_until  timestamptz NULL,
    rest_reason text NULL,
    PRIMARY KEY (account_id, pool)
);

CREATE TABLE account_charges (
    id          bigserial PRIMARY KEY,
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    job_id      uuid NOT NULL REFERENCES jobs(id),
    pool        text NOT NULL,
    amount      int NOT NULL CHECK (amount >= 0),
    account_day date NOT NULL,
    refunded_at timestamptz NULL,
    UNIQUE (job_id, pool)
);

-- id is the fencing token (monotonic). hard_until is set for every purpose except a poll-held job lease.
CREATE TABLE leases (
    id           bigserial PRIMARY KEY,
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    job_id       uuid NULL REFERENCES jobs(id),
    purpose      text NOT NULL CHECK (purpose IN ('job', 'check', 'command', 'reconcile')),
    pool         text NULL,
    owner        text NOT NULL,
    until        timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    hard_until   timestamptz NULL
) WITH (fillfactor = 70);
CREATE UNIQUE INDEX leases_one_job_lease ON leases (job_id) WHERE purpose = 'job';
CREATE INDEX leases_account ON leases (account_id);
CREATE INDEX leases_owner ON leases (owner);

CREATE TABLE account_shots (
    account_id uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    object_key text NOT NULL,
    taken_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE admin_login_attempts (
    ip inet NOT NULL,
    at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX admin_login_attempts_ip ON admin_login_attempts (ip, at);
