-- Expand only (D96). The time of the newest read that the engine really answered (a poll that said "not ready" or a
-- reconcile that found nothing), so the reaper fails a job past its render deadline only after a read made after the
-- deadline that was answered, or at the backstop. A poll or reconcile that failed or ran out of time writes nothing.
-- A reconcile that finds the order resets it to NULL: the job then needs a poll after the deadline. NULL on a job
-- that was never read. It is the time the read STARTED, never the time its result was applied.
-- jobs.late_seen_at: the first time a running worker's reaper saw the sent job past its render deadline (stamped again
-- when a Re-check moves the deadline past it). The one-hour backstop counts from here, not from the deadline, so a
-- worker that was down for hours still reads a late job before it can fail. NULL until then. The tables' UPDATE grant on `jobs` is table-level (0006), so the worker keeps writing it.
-- jobs.found_late_at: when the final reconcile found the order after the render deadline. While it is newer than the
-- render clock (a Re-check restarts the clock), a poll that says "still rendering" is not recorded as the final read:
-- only a video or the one-hour backstop (counted from the next late_seen_at) ends the job.
ALTER TABLE jobs ADD COLUMN answered_at timestamptz NULL, ADD COLUMN late_seen_at timestamptz NULL;
ALTER TABLE jobs ADD COLUMN found_late_at timestamptz NULL;
