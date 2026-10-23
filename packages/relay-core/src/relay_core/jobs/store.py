"""The job store's public surface: creation, claim, result application, failing, events.

The pieces live in `create`, `claim`, `apply`, `fail` and `events`; callers import from here. Every function takes
the process's pool and opens its own transaction (money and multi-row writes only run inside `relay_core.db.tx`);
none starts a task or does foreign I/O.
"""

from psycopg_pool import AsyncConnectionPool

from relay_core.db import tx
from relay_core.jobs.apply import (
    apply_fetch_failed,
    apply_fetched,
    apply_interrupted,
    apply_poll,
    apply_reconciled,
    apply_submit,
    mark_sent,
    report_ref,
)
from relay_core.jobs.claim import Claim, ReconcileClaim, Visible, claim_one, claim_reconcile, claim_visible
from relay_core.jobs.create import (
    Created,
    CreateRefused,
    FrameUnusable,
    IdempotencyConflict,
    InputMedia,
    NewJob,
    TooManyQueued,
    body_hash,
    create_job,
)
from relay_core.jobs.events import add_event
from relay_core.jobs.fail import fail_in_tx, fail_job
from relay_core.jobs.ops import drain, restore_mark, resume
from relay_core.jobs.recheck import (
    BulkRecheck,
    JobRefused,
    Recovered,
    Skipped,
    StoredOutput,
    recheck_bulk,
    recheck_job,
    recover_in_tx,
    recover_job,
    regenerate_job,
)
from relay_core.jobs.shares import PgShareTable, expire_input_links
from relay_core.jobs.sweeps import (
    expire_admin_sessions,
    ledger_check,
    run_housekeeping,
    sweep_orphans,
    sweep_retention,
)
from relay_core.jobs.webhooks import deliver_due, resend_failed
from relay_core.leases import LeaseLost
from relay_core.providers import JobView, MediaRef

__all__ = [
    "BulkRecheck", "Claim", "CreateRefused", "Created", "FrameUnusable", "IdempotencyConflict", "InputMedia",
    "JobRefused", "LeaseLost", "NewJob", "PgShareTable", "ReconcileClaim", "Recovered", "Skipped", "StoredOutput",
    "TooManyQueued", "Visible",
    "add_event", "apply_fetch_failed", "apply_fetched", "apply_interrupted", "apply_poll", "apply_reconciled",
    "apply_submit", "body_hash", "claim_one", "claim_reconcile", "claim_visible", "create_job", "deliver_due", "drain",
    "expire_admin_sessions", "expire_input_links", "fail_in_tx", "fail_job", "job_view", "ledger_check", "mark_sent",
    "recheck_bulk", "recheck_job", "recover_in_tx", "recover_job", "regenerate_job", "report_ref", "resend_failed",
    "restore_mark", "resume", "run_housekeeping", "sweep_orphans", "sweep_retention",
]

_VIEW = ("SELECT id, provider, operation, model, params, prompt, marker, sent_text, render_since, sent_at, "
         "external_ref, provider_state, frame_id FROM jobs WHERE id = %s")


async def job_view(db: AsyncConnectionPool, job_id) -> JobView:
    """The read-only view an engine gets of a job. Its inputs are loaded lazily."""
    async with tx(db) as conn:
        row = await (await conn.execute(_VIEW, (job_id,))).fetchone()
    if row is None:
        raise LookupError("no job %s" % job_id)

    async def inputs() -> list[MediaRef]:
        async with tx(db) as conn:
            rows = await (await conn.execute(
                "SELECT idx, object_key, mime, size FROM job_media WHERE job_id = %s AND role = 'input' ORDER BY idx",
                (job_id,))).fetchall()
        return [MediaRef(*r) for r in rows]

    return JobView(str(row[0]), *row[1:-1], inputs, frame_id=row[-1])
