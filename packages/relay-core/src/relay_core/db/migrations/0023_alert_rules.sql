-- Expand only. The admin's switch and mute per alert kind (Alerts > Alert rules). A kind with no row is on and not
-- muted; nothing else reads or writes the table. `source` says where the kind comes from: 'stored' (rows of
-- billing_alerts or unclear_replies, the hub's groups) or 'live' (a condition /admin/stats computes); the two share
-- one kind name (drift), so the pair is the key. Off hides the kind from the hub and the counts; muted keeps it
-- listed with its end time but out of the counts until `muted_until`. Neither stops a writer from storing a row.
CREATE TABLE alert_rules (
    source      text NOT NULL CHECK (source IN ('stored', 'live')),
    kind        text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    enabled     boolean NOT NULL DEFAULT true,
    muted_until timestamptz NULL,
    updated_by  text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source, kind)
);

-- The api reads and writes it (the admin routes); the worker has no use of it.
GRANT SELECT, INSERT, UPDATE ON alert_rules TO relay_api;
