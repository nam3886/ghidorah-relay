-- Expand only.
-- jobs.request: what the client sent (endpoint, model, prompt, duration, ratio, n, options, title, callback_url; images
-- as references to the job's input media by index, never bytes). Regenerate plans from it, not from params.
-- jobs.request_hash: the hash of that request, images as sent; a facade retry finds its earlier job by it.
-- Both stay NULL on a job made before this migration. Table-level grants (0006) already cover the new columns.
ALTER TABLE jobs ADD COLUMN request jsonb NULL, ADD COLUMN request_hash text NULL;
CREATE INDEX jobs_key_request_hash_idx ON jobs (key_id, request_hash, created_at) WHERE request_hash IS NOT NULL;
