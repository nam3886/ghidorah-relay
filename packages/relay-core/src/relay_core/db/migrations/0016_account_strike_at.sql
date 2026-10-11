-- Expand only (D91). The time of the last strike that was counted, so a strike can be spaced from the one before it:
-- a doubt read inside the gap counts once. NULL while the account has no counted strike. Written by the worker only
-- (the api's column-level UPDATE grant on accounts does not name it, and a new column gets no grant by default).
ALTER TABLE accounts ADD COLUMN strike_at timestamptz NULL;

-- Expand only (D92). How many times a job was put back in the queue by a failure that is not the order's fault and
-- uses no attempt (a code whose rule has `free_retries`). It is the job's own count: a requeue replaces the job's
-- provider state, so it cannot live there; nothing resets it. A constant default is metadata-only. The tables' UPDATE
-- grant on `jobs` is table-level, so the worker (and the api) keep writing it without a new grant.
ALTER TABLE jobs ADD COLUMN fault_retries int NOT NULL DEFAULT 0;
