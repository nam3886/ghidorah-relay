-- Expand only. The replies a provider could not classify, as data the admin alerts hub reads.
-- Written by the core only, in the transaction that applies a submit result or fails a job at its render deadline.
-- Kept forever: no role may delete a row.
--
-- job_id references jobs with no ON DELETE action: both writers hold the job row FOR UPDATE, and nothing deletes jobs.
-- account_id has no foreign key, so the six RESTRICT keys of an account stay six.
CREATE TABLE unclear_replies (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL DEFAULT clock_timestamp(),
    provider        text NOT NULL,
    job_id          uuid NOT NULL REFERENCES jobs(id),
    account_id      uuid NULL,
    text            text NOT NULL,
    wording         text NOT NULL,
    group_id        text NOT NULL,
    acknowledged_at timestamptz NULL,
    acknowledged_by text NULL,
    CHECK ((acknowledged_at IS NULL) = (acknowledged_by IS NULL)),
    -- One row per job and wording. The worker has INSERT only, so the writers cannot look first: they insert with
    -- ON CONFLICT DO NOTHING and this index decides. It also serves the lookups by job.
    UNIQUE (job_id, group_id)
);
CREATE INDEX unclear_replies_group ON unclear_replies (group_id, id);

-- The worker appends; the api reads and stamps the two acknowledge columns, as for billing_alerts (0012). Neither role
-- may delete (kept forever), the api has no use of the sequence.
GRANT INSERT ON unclear_replies TO relay_worker;
GRANT USAGE ON SEQUENCE unclear_replies_id_seq TO relay_worker;
GRANT SELECT ON unclear_replies TO relay_api;
GRANT UPDATE (acknowledged_at, acknowledged_by) ON unclear_replies TO relay_api;

-- A stamp is final, for every role: once acknowledged_at is set nothing in the row changes again.
CREATE FUNCTION unclear_replies_stamp() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.acknowledged_at IS NOT NULL THEN
        RAISE EXCEPTION 'an acknowledged unclear reply never changes'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER unclear_replies_stamp BEFORE UPDATE ON unclear_replies
    FOR EACH ROW EXECUTE FUNCTION unclear_replies_stamp();
