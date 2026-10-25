-- Expand only: a pinned job. A request that works on an output of the customer's own earlier job (an extend, an edit)
-- names that job (`source_job_id`, a finished job of the same key) and runs only on the account that made it
-- (`pin_account_id`, copied from the source at creation). Both NULL for every other job, so an old build reads and
-- writes jobs as before; only a pinned job (made by the new api) is refused to an old worker's claim by
-- `jobs_pinned_account` below: deploy the workers with or before the api.
-- `source_idx` is which output of the source it works on (0 = the first). The three are the columns 0003 reserved.
ALTER TABLE jobs ADD COLUMN source_job_id uuid NULL REFERENCES jobs(id),
                 ADD COLUMN source_idx int NULL CHECK (source_idx IS NULL OR source_idx >= 0),
                 ADD COLUMN pin_account_id uuid NULL REFERENCES accounts(id);
ALTER TABLE jobs ADD CONSTRAINT jobs_pin_has_source
    CHECK ((source_job_id IS NULL) = (pin_account_id IS NULL) AND (source_job_id IS NULL) = (source_idx IS NULL));
-- the last guard: a pinned job never runs on any other account, whatever a claim does
ALTER TABLE jobs ADD CONSTRAINT jobs_pinned_account
    CHECK (pin_account_id IS NULL OR account_id IS NULL OR account_id = pin_account_id);
-- the claim's pinned heads and the sweep that ends a pinned job whose account can no longer run it
CREATE INDEX jobs_pinned_queued ON jobs (provider, created_at, id) WHERE status = 'queued' AND pin_account_id IS NOT NULL;
