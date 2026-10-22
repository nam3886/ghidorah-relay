-- Commands, settings, admin, logs, heartbeats and provider snapshots.
-- schema_migrations is created by the migration runner itself (it must exist before the first file runs).
CREATE TABLE commands (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider    text NOT NULL,
    kind        text NOT NULL,
    account_id  uuid NULL REFERENCES accounts(id) ON DELETE CASCADE,
    job_id      uuid NULL REFERENCES jobs(id),
    args        jsonb NOT NULL DEFAULT '{}',
    status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    result      jsonb NULL,
    attempts    int NOT NULL DEFAULT 0,
    lease_id    bigint NULL,
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz NULL
);
CREATE INDEX commands_queued ON commands (provider, created_at) WHERE status = 'queued';

CREATE TABLE settings_kv (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_by text NULL
);

CREATE TABLE admin_users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE admin_sessions (
    token_hash text PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);
CREATE INDEX admin_sessions_expires ON admin_sessions (expires_at);

-- No foreign keys: the log outlives the key and the job it names.
CREATE TABLE request_logs (
    id                bigserial PRIMARY KEY,
    at                timestamptz NOT NULL DEFAULT clock_timestamp(),
    key_id            uuid NULL,
    provider          text NULL,
    route             text NOT NULL,
    status_code       int NOT NULL,
    job_id            uuid NULL,
    duration_ms       int NOT NULL,
    error_code        text NULL,
    body_redacted     jsonb NOT NULL DEFAULT '{}',
    response_redacted jsonb NULL
);
CREATE INDEX request_logs_at ON request_logs (at);

CREATE TABLE worker_heartbeats (
    worker_id          text PRIMARY KEY,
    provider           text NOT NULL,
    started_at         timestamptz NOT NULL,
    at                 timestamptz NOT NULL,
    loop_lag_ms        int NOT NULL DEFAULT 0,
    slots_busy         int NOT NULL DEFAULT 0,
    consecutive_errors int NOT NULL DEFAULT 0
);

CREATE TABLE provider_snapshots (
    provider   text NOT NULL,
    name       text NOT NULL,
    data       jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (provider, name)
);
