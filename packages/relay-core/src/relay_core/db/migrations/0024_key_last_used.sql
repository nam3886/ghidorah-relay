-- Expand only: when a key last authenticated a public /v1 request and from which address (the API's client_ip).
-- Written at most once a minute per key (keys.touch); NULL for a key never used since this migration.
ALTER TABLE api_keys ADD COLUMN last_used_at timestamptz, ADD COLUMN last_used_ip text;
