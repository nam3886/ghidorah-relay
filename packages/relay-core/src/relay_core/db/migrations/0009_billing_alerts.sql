-- Alerts raised by the money code, as data the admin alerts (§15) read: a refund whose counter floor clamped.
-- Written in the refund's own transaction, so it exists exactly when the refund does.
CREATE TABLE billing_alerts (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    alert      text NOT NULL CHECK (alert IN ('clamp')),
    key_id     uuid NOT NULL,
    provider   text NOT NULL,
    kind       text NOT NULL,
    ledger_id  bigint NOT NULL REFERENCES ledger(id),
    amount     int NOT NULL,
    used_day   int NOT NULL,
    used_total int NOT NULL
);
CREATE INDEX billing_alerts_at ON billing_alerts (at);
GRANT SELECT, INSERT ON billing_alerts TO relay_api, relay_worker;
GRANT USAGE, SELECT ON SEQUENCE billing_alerts_id_seq TO relay_api, relay_worker;
