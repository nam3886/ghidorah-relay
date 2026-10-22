"""Failing a job (invariants I7, I21, I24).

Every transition to `failed` is ONE transaction: the compare-and-set on the status, the redacted error, the key
refund of every charge row, the account-pool refund and the callback fields. A failed job always has its key
charge refunded: the refund does not depend on the code.
"""

from collections.abc import Collection, Mapping

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from relay_core import billing, failpoints, pool
from relay_core.db import tx
from relay_core.errors import rule
from relay_core.jobs.events import add_event
from relay_core.redact import redact_log
from relay_core.txguard import require_tx

# The callback fields are set in the same statement: a terminal event with a callback url is delivered later.
_MARK_FAILED = """
UPDATE jobs SET status = 'failed', error_code = %(code)s, error = %(error)s, finished_at = clock_timestamp(),
       updated_at = clock_timestamp(), poll_token = NULL, next_poll_at = NULL,
       callback_status = CASE WHEN callback_url IS NULL THEN callback_status ELSE 'pending' END,
       callback_attempts = CASE WHEN callback_url IS NULL THEN callback_attempts ELSE 0 END,
       callback_next_at = CASE WHEN callback_url IS NULL THEN callback_next_at ELSE clock_timestamp() END,
       callback_for = CASE WHEN callback_url IS NULL THEN callback_for ELSE clock_timestamp() END
 WHERE id = %(id)s AND status = ANY(%(expect)s)
"""


async def _mark_failed(conn: AsyncConnection, job_id, expect: Collection[str], code: str, detail: str) -> bool:
    """The status CAS with the error text. The stored text is the redacted engine detail (or the rule's message when
    there is none); a client is shown the rule's message by code, never this text."""
    r = rule(code)
    cur = await conn.execute(_MARK_FAILED, {"id": job_id, "code": code, "expect": list(expect),
                                            "error": redact_log(detail) if detail else r.message})
    if cur.rowcount != 1:
        return False
    await add_event(conn, job_id, "system", "failed", "failed: %s - %s" % (code, r.message))
    return True


# Upstream completed the order when the job row records its outputs: a fetch failure, or any later failure of a job
# that was re-checked after one. The row decides, so every failure path agrees.
_ORDER_COMPLETED = ("SELECT CASE WHEN jsonb_typeof(provider_state -> 'outputs') = 'array' "
                    "THEN jsonb_array_length(provider_state -> 'outputs') > 0 ELSE false END FROM jobs WHERE id = %s")


async def _refund(conn: AsyncConnection, job_id, pools: Mapping[str, pool.PoolConfig]) -> None:
    """Return the account charges still refundable today (not when upstream completed the order: the account really
    spent that credit), then every unrefunded key charge of the job: the account rows come before the key wallet in
    the one lock order."""
    await failpoints.hit("job.fail.refund")
    charges = await (await conn.execute("SELECT id FROM ledger WHERE job_id = %s AND entry = 'charge' ORDER BY id",
                                        (job_id,))).fetchall()
    order_completed = (await (await conn.execute(_ORDER_COMPLETED, (job_id,))).fetchone())[0]
    if not order_completed:
        await pool.refund_job(conn, job_id, pools)
    for (charge_id,) in charges:
        await billing.refund(conn, charge_id)
    await failpoints.hit("job.fail.refunded")


async def fail_in_tx(conn: AsyncConnection, job_id, expect: Collection[str], code: str, detail: str,
                     pools: Mapping[str, pool.PoolConfig]) -> bool:
    """For a caller that already holds the transaction (a result application). The key is always refunded; the
    account pool is not when the job row records upstream outputs. False: the status was not one of `expect`,
    nothing changed."""
    require_tx(conn)
    if not await _mark_failed(conn, job_id, expect, code, detail):
        return False
    await _refund(conn, job_id, pools)
    return True


async def fail_job(db: AsyncConnectionPool, job_id, *, expect: Collection[str], code: str, detail: str = "",
                   pools: Mapping[str, pool.PoolConfig]) -> bool:
    """Fail a job whose status is one of `expect`: status, error, refunds and callback in one transaction (I21).
    False when the status was something else (another transition won)."""
    async with tx(db) as conn:
        return await fail_in_tx(conn, job_id, expect, code, detail, pools)
