-- Expand only. The change history of the live settings: who changed which key, from what stored value to what, and
-- when. A NULL value is "no stored value" (the default applied before, or applies again after a reset). A secret
-- setting's values are never kept: its row has `secret` true and both values NULL ("changed"). Written in the same
-- transaction as the settings_kv write it describes; the writer keeps the newest 1000 rows.
CREATE TABLE settings_history (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    key       text NOT NULL,
    by        text NULL,
    old_value jsonb NULL,
    new_value jsonb NULL,
    secret    boolean NOT NULL DEFAULT false,
    CHECK (NOT secret OR (old_value IS NULL AND new_value IS NULL))
);

GRANT SELECT, INSERT, DELETE ON settings_history TO relay_api, relay_worker;
GRANT USAGE ON SEQUENCE settings_history_id_seq TO relay_api, relay_worker;
