"""Job events: the timeline of a job. Every stored text goes through redact_log (I24, R8)."""

from psycopg import AsyncConnection

from relay_core.redact import redact_log

SOURCES = ("upstream", "engine", "system")


async def add_event(conn: AsyncConnection, job_id, source: str, stage: str, text: str) -> None:
    """One event row in the caller's transaction. A value over 2000 characters is cut before it is scanned."""
    if source not in SOURCES:
        raise ValueError("event source must be one of %s" % (SOURCES,))
    await conn.execute("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, %s, %s, %s)",
                       (job_id, source, stage, redact_log(text or "")))
