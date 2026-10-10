-- Expand only (contracts 4.2, 4.7, 5.1). Nothing is backfilled: a job made before this migration keeps
-- sent_text and frame_id NULL, which every engine reads as "cannot tell".
--
-- A job that is still in flight when this runs would be left without sent_text for good (the trigger below forbids a
-- backfill), and a worker could neither send nor reconcile it. So the migration refuses to run until every job has ended.
-- The table lock comes first: it waits for every open transaction that wrote a job and blocks new writers until this
-- migration commits, so the check sees every job that will ever exist without sent_text.
LOCK TABLE jobs IN SHARE ROW EXCLUSIVE MODE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM jobs WHERE status NOT IN ('done', 'failed')) THEN
        RAISE EXCEPTION '0014 refused: jobs are still in flight; drain the workers and let every job finish before migrating'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
END
$$;

-- prompt_frames: one row per saved frame version, never updated or deleted. A model's current version is its newest
-- row; a row with model NULL is the kind's default. created_by_id has no foreign key: a referential action would
-- rewrite append-only rows. The CHECK keeps every stored text inside the placeholder grammar of relay_core.frames
-- (the invisible-character rule is the one thing it cannot see; the grammar refuses those at save and in the plan step).
CREATE TABLE prompt_frames (
    id            bigserial PRIMARY KEY,
    provider      text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('video', 'image')),
    model         text NULL,
    text          text NULL,
    restored_from bigint NULL REFERENCES prompt_frames(id),
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_by_id uuid NULL,
    created_by    text NOT NULL,
    CHECK (model IS NOT NULL OR text IS NOT NULL),
    CHECK (text IS NULL OR (
        char_length(text) BETWEEN 1 AND 1000
        AND text !~ '[\r\n]'
        AND length(text) - length(replace(text, '{{client_prompt}}', '')) = length('{{client_prompt}}')
        AND length(text) - length(replace(text, '{{tracking_code}}', '')) = length('{{tracking_code}}')
        AND replace(replace(text, '{{client_prompt}}', ''), '{{tracking_code}}', '') !~ '[{][{]|[}][}]'))
);
CREATE INDEX prompt_frames_target ON prompt_frames (provider, kind, model, id);

-- prompt_frame_locks: one row per keep-own toggle, never updated or deleted, so every toggle keeps who and when. The
-- current lock of a model is its newest row; no row means unlocked. A separate table keeps prompt_frames a pure
-- version history, so the effective-frame lookup never meets a lock row.
CREATE TABLE prompt_frame_locks (
    id            bigserial PRIMARY KEY,
    provider      text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('video', 'image')),
    model         text NOT NULL,
    locked        boolean NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_by_id uuid NULL,
    created_by    text NOT NULL
);
CREATE INDEX prompt_frame_locks_target ON prompt_frame_locks (provider, kind, model, id);

-- The api reads and appends; the worker never reads either table (it gets the rendered text with the job).
GRANT SELECT, INSERT ON prompt_frames TO relay_api;
GRANT SELECT, INSERT ON prompt_frame_locks TO relay_api;
GRANT USAGE, SELECT ON SEQUENCE prompt_frames_id_seq, prompt_frame_locks_id_seq TO relay_api;

-- jobs: the text relay sends, rendered once at insert, and the frame version it came from.
ALTER TABLE jobs ADD COLUMN sent_text text NULL, ADD COLUMN frame_id bigint NULL REFERENCES prompt_frames(id);

-- These four columns are written once, at insert. 0006's table-wide UPDATE grant on jobs stays; the trigger is the
-- guard, for every role. marker and prompt are frozen too: reconcile identifies a job by them.
CREATE FUNCTION jobs_frozen_columns() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.sent_text IS DISTINCT FROM OLD.sent_text OR NEW.frame_id IS DISTINCT FROM OLD.frame_id
       OR NEW.marker IS DISTINCT FROM OLD.marker OR NEW.prompt IS DISTINCT FROM OLD.prompt THEN
        RAISE EXCEPTION 'jobs.sent_text, frame_id, marker and prompt never change after the insert'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER jobs_frozen_columns BEFORE UPDATE OF sent_text, frame_id, marker, prompt ON jobs
    FOR EACH ROW EXECUTE FUNCTION jobs_frozen_columns();
