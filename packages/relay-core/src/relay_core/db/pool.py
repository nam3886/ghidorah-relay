"""One async pool per process and the transaction helper (contracts §4.5).

A connection is held only for one short transaction and never across an await of foreign I/O (an engine, a
browser, an HTTP call, MinIO). Three things enforce it: `tx` cancels a body that runs longer than `hold_s`; a
second `tx` inside a `tx` is refused (it would hold one connection while waiting for another); and code that
does foreign I/O calls `assert_no_tx()` first, which fails while a transaction is open in the current task.
The server backs this up: `idle_in_transaction_session_timeout` kills a session that sits idle in a transaction.
"""

import asyncio
import contextvars
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection, IsolationLevel
from psycopg_pool import AsyncConnectionPool

from relay_core import txguard

SCHEMA = "relay"
TX_HOLD_S = 10.0          # one transaction is a few statements; longer means it is waiting on something foreign
GETCONN_TIMEOUT_S = 5.0

_in_tx: contextvars.ContextVar[bool] = contextvars.ContextVar("relay_in_tx", default=False)


def session_options(*, statement_timeout_s: float = 5, lock_timeout_s: float = 3, idle_in_tx_s: float = 15) -> str:
    """The libpq `options` string every connection starts with (contracts §4.5)."""
    values = {
        "statement_timeout": int(statement_timeout_s * 1000),
        "lock_timeout": int(lock_timeout_s * 1000),
        "idle_in_transaction_session_timeout": int(idle_in_tx_s * 1000),
        "timezone": "UTC",
        "search_path": SCHEMA,
    }
    return " ".join("-c %s=%s" % kv for kv in values.items())


async def _read_committed(conn: AsyncConnection) -> None:
    await conn.set_isolation_level(IsolationLevel.READ_COMMITTED)


async def open_pool(dsn: str, *, max_size: int = 10, options: str | None = None, name: str = "relay"
                    ) -> AsyncConnectionPool:
    """Open the process's pool: autocommit connections (every transaction is an explicit `tx`), READ COMMITTED."""
    pool = AsyncConnectionPool(
        dsn, min_size=1, max_size=max_size, max_waiting=4 * max_size, timeout=GETCONN_TIMEOUT_S, name=name,
        kwargs={"autocommit": True, "options": options or session_options()},
        configure=_read_committed, open=False)
    try:
        await pool.open(wait=True, timeout=GETCONN_TIMEOUT_S)
    except BaseException:
        await pool.close()
        raise
    return pool


def assert_no_tx() -> None:
    """Called before foreign I/O: a transaction open in this task would be held across it."""
    if _in_tx.get():
        raise RuntimeError("foreign I/O inside a database transaction")


@asynccontextmanager
async def tx(pool: AsyncConnectionPool, *, hold_s: float = TX_HOLD_S) -> AsyncIterator[AsyncConnection]:
    """One short transaction on one pooled connection: commit on a clean exit, roll back on any error."""
    if _in_tx.get():
        raise RuntimeError("nested database transaction")
    token = _in_tx.set(True)
    try:
        async with asyncio.timeout(hold_s), pool.connection() as conn:
            async with conn.transaction():
                txguard.enter(conn)
                try:
                    yield conn
                finally:
                    txguard.leave(conn)
    finally:
        _in_tx.reset(token)
