"""Share links on the database (contracts 9; invariant I12): the `ShareTable` that `storage.resolve_share` reads, the
revoke action, and the expiry sweep for input links.

A link is the `share_token` of a `job_media` row. Revoking or expiring clears the token, so the link is simply unknown
afterwards; the row, the object and its `share_expires_at` stay. Debug media never carry a token.
"""

from psycopg_pool import AsyncConnectionPool

from relay_core.db import tx
from relay_core.storage import ShareRecord


class PgShareTable:
    """`storage.ShareTable` over `job_media`."""

    def __init__(self, db: AsyncConnectionPool):
        self._db = db

    async def find(self, token: str) -> ShareRecord | None:
        async with tx(self._db) as conn:
            row = await (await conn.execute(
                "SELECT object_key, role, share_expires_at FROM job_media WHERE share_token = %s", (token,))
            ).fetchone()
        return None if row is None else ShareRecord(*row)

    async def clear_job(self, job_id: str) -> int:
        """Revoke: every link of the job stops working. Returns how many tokens were cleared."""
        async with tx(self._db) as conn:
            return (await conn.execute(
                "UPDATE job_media SET share_token = NULL WHERE job_id = %s AND share_token IS NOT NULL "
                "AND role IN ('input', 'output')", (job_id,))).rowcount


async def expire_input_links(db: AsyncConnectionPool) -> int:
    """Sweep: clear the token of every input link past its expiry. `resolve_share` already refuses them; this
    makes the token unknown too. Returns how many were cleared."""
    async with tx(db) as conn:
        return (await conn.execute(
            "UPDATE job_media SET share_token = NULL WHERE role = 'input' AND share_token IS NOT NULL "
            "AND share_expires_at <= clock_timestamp()")).rowcount
