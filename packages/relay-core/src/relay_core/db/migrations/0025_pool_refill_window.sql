-- Expand only: the <p>.pools window a reported refill ends (Balance.window), so a rolling window anchored on the
-- upstream's reset places its blocks on that reset alone. NULL: the refill named no window and anchors none.
ALTER TABLE account_pools ADD COLUMN refill_window text NULL;
