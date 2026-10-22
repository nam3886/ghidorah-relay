-- Jobs, their events and media. The derived-operation columns (source_job_id, source_idx,
-- pin_account_id) are not here: they are added by an expand migration with the members that use them.
CREATE TABLE jobs (
    id                uuid PRIMARY KEY,
    provider          text NOT NULL,
    key_id            uuid NOT NULL REFERENCES api_keys(id),
    idem_key          text NULL,
    idem_body_hash    text NULL,
    operation         text NOT NULL,
    media_type        text NOT NULL,
    model             text NOT NULL,
    params            jsonb NOT NULL DEFAULT '{}',
    prompt            text NOT NULL,
    title             text NULL,
    marker            text NOT NULL,
    status            text NOT NULL CHECK (status IN
                          ('queued', 'submitting', 'unknown', 'waiting', 'fetching', 'done', 'failed')),
    completion        text NOT NULL CHECK (completion IN ('hold', 'detach')),
    after_sent        text NOT NULL CHECK (after_sent IN ('reconcile', 'fail_refund')),
    submit_timeout_s  int NOT NULL,
    wallet_kind       text NOT NULL,
    price             int NOT NULL CHECK (price >= 0),
    pool              text NULL,
    account_cost      int NOT NULL DEFAULT 0 CHECK (account_cost >= 0),
    account_id        uuid NULL,
    tried             uuid[] NOT NULL DEFAULT '{}',
    attempts          int NOT NULL DEFAULT 0,
    claims            int NOT NULL DEFAULT 0,
    sent_at           timestamptz NULL,
    accepted_at       timestamptz NULL,
    taken_at          timestamptz NULL,
    render_since      timestamptz NULL,
    next_poll_at      timestamptz NULL,
    poll_token        uuid NULL,
    external_ref      text NULL,
    provider_state    jsonb NOT NULL DEFAULT '{}',
    upstream_price    int NULL,
    upstream_model    text NULL,
    error_code        text NULL,
    error             text NULL,
    warnings          jsonb NOT NULL DEFAULT '[]',
    callback_url      text NULL,
    callback_status   text NULL CHECK (callback_status IN ('pending', 'sent', 'failed')),
    callback_attempts int NOT NULL DEFAULT 0,
    callback_next_at  timestamptz NULL,
    callback_for      timestamptz NULL,
    callback_last     text NULL,
    created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at       timestamptz NULL,
    UNIQUE (provider, external_ref),
    UNIQUE (key_id, idem_key)
);
CREATE INDEX jobs_queued ON jobs (provider, pool, created_at, id) WHERE status = 'queued';
CREATE INDEX jobs_poll ON jobs (provider, next_poll_at) WHERE status IN ('waiting', 'fetching', 'unknown');
CREATE INDEX jobs_callback ON jobs (callback_next_at) WHERE callback_status = 'pending';
CREATE INDEX jobs_unfinished ON jobs (key_id) WHERE status NOT IN ('done', 'failed');

-- Charges and account charges may precede the job row inside one transaction.
ALTER TABLE ledger ADD CONSTRAINT ledger_job_fk FOREIGN KEY (job_id) REFERENCES jobs(id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE job_events (
    id     bigserial PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id),
    at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    source text NOT NULL CHECK (source IN ('upstream', 'engine', 'system')),
    stage  text NOT NULL,
    text   text NOT NULL
);
CREATE INDEX job_events_job ON job_events (job_id, at);

CREATE TABLE job_media (
    job_id           uuid NOT NULL REFERENCES jobs(id),
    role             text NOT NULL CHECK (role IN ('input', 'output', 'debug')),
    idx              int NOT NULL,
    object_key       text NOT NULL,
    mime             text NOT NULL,
    size             bigint NOT NULL,
    width            int NULL,
    height           int NULL,
    duration_s       real NULL,
    upstream_id      text NULL,
    share_token      text NULL UNIQUE,
    share_expires_at timestamptz NULL,
    created_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (job_id, role, idx),
    UNIQUE (job_id, role, upstream_id)
);
