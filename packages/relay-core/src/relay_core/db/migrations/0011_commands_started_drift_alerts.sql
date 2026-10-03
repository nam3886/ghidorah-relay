-- Expand only.
-- commands.started_at: when a worker claimed the command; the reaper fails a lease-less command that has been
-- running past its bound (a crash mid-run would otherwise leave it running forever). Worker-owned, like status.
ALTER TABLE commands ADD COLUMN started_at timestamptz NULL;

-- The daily ledger check stores a counter that disagrees with the ledger as an alert row (contracts 4.1, 15), next to
-- the clamp alerts. A drift names no single ledger entry, so ledger_id is optional for it and stays required for a
-- clamp; detail says which counter and the two values.
ALTER TABLE billing_alerts DROP CONSTRAINT billing_alerts_alert_check;
ALTER TABLE billing_alerts ADD CONSTRAINT billing_alerts_alert_check CHECK (alert IN ('clamp', 'drift'));
ALTER TABLE billing_alerts ALTER COLUMN ledger_id DROP NOT NULL;
ALTER TABLE billing_alerts ADD CONSTRAINT billing_alerts_clamp_names_ledger
    CHECK (alert <> 'clamp' OR ledger_id IS NOT NULL);
ALTER TABLE billing_alerts ADD COLUMN detail text NULL;
