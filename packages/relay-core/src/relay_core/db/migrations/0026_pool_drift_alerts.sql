-- Expand only. A `pool_drift` alert: an upstream reported a pool balance (a Balance effect) further from the relay's
-- computed one than the `alert_pool_drift` setting. It names the account and the pool (`scope`), the provider, the
-- computed balance (`expected`) and the reported one (`actual`); no key, no ledger row, no job. The checks of 0020 are
-- widened to let it in; an old build never writes it and reads it like any other row.
ALTER TABLE billing_alerts ADD COLUMN account_id uuid NULL;
ALTER TABLE billing_alerts DROP CONSTRAINT billing_alerts_alert_check;
ALTER TABLE billing_alerts ADD CONSTRAINT billing_alerts_alert_check
    CHECK (alert IN ('clamp', 'drift', 'cost_mismatch', 'pool_drift'));
-- 0020's unnamed check that makes a wallet alert name its key and counters: found by its text, not by a guessed name.
DO $$
DECLARE wallet_check text;
BEGIN
    SELECT conname INTO STRICT wallet_check FROM pg_constraint
     WHERE conrelid = 'billing_alerts'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%used_total%';
    EXECUTE format('ALTER TABLE billing_alerts DROP CONSTRAINT %I', wallet_check);
END $$;
ALTER TABLE billing_alerts ADD CONSTRAINT billing_alerts_wallet_names_key
    CHECK (alert IN ('cost_mismatch', 'pool_drift') OR (key_id IS NOT NULL AND scope IS NOT NULL AND amount IS NOT NULL
                                                       AND used_day IS NOT NULL AND used_total IS NOT NULL));
ALTER TABLE billing_alerts ADD CONSTRAINT billing_alerts_pool_drift_names_account
    CHECK (alert <> 'pool_drift' OR (account_id IS NOT NULL AND scope IS NOT NULL AND provider IS NOT NULL
                                     AND expected IS NOT NULL AND actual IS NOT NULL));
-- the once-per-window lookup of a new pool drift
CREATE INDEX billing_alerts_pool_drift ON billing_alerts (account_id, scope, at) WHERE alert = 'pool_drift';
