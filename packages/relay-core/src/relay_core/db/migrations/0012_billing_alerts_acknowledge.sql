-- Expand only. An operator acknowledges a billing alert (a clamp or a drift) from the admin: the row stays as the
-- audit trail, it only stops being listed as open. The api may update these two columns and nothing else of the table.
ALTER TABLE billing_alerts ADD COLUMN acknowledged_at timestamptz NULL;
ALTER TABLE billing_alerts ADD COLUMN acknowledged_by text NULL;
GRANT UPDATE (acknowledged_at, acknowledged_by) ON billing_alerts TO relay_api;
