-- Expand only. The indexes the admin stats read: the jobs of an account, the jobs by creation time, and the
-- charges of an account in a pool on a day. Built plainly (no CONCURRENTLY) because the tables are empty before the
-- first deploy; later, a CREATE INDEX in a migration transaction would lock writes on a full table.
CREATE INDEX jobs_account_idx ON jobs (account_id);
CREATE INDEX jobs_created_at_idx ON jobs (created_at);
CREATE INDEX account_charges_account_pool_day_idx ON account_charges (account_id, pool, account_day);
