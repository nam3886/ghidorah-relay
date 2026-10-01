"""The one guard for money and multi-row writes: they run only on the connection of an open `relay_core.db.tx()`.

`tx()` is the single sanctioned way to write several rows together: one pooled connection, one explicit
transaction, commit on a clean exit. It registers its connection here on entry and removes it on exit, and
`require_tx` accepts only a registered connection. Nothing is inferred from the driver's transaction status
(a pipeline reads ACTIVE whether or not a transaction is open) or from the calling task's context (a child task
keeps a copy of the context after the block has ended), so a connection is accepted exactly while its block is
open.

The module lives apart from `relay_core.db` so billing, keys and the account helpers can import it without
pulling in the db package (whose `migrate` imports billing).
"""

import weakref

from psycopg import AsyncConnection

_open: "weakref.WeakSet[AsyncConnection]" = weakref.WeakSet()


class NotInTransaction(RuntimeError):
    """A multi-statement write was called on a connection that is not inside an open `relay_core.db.tx()` block:
    its statements could commit separately, and a failure between the counter UPDATE and the ledger insert would
    leave a charge without a ledger row."""


def enter(conn: AsyncConnection) -> None:
    """`tx()` calls this once its transaction is open."""
    _open.add(conn)


def leave(conn: AsyncConnection) -> None:
    """`tx()` calls this before its transaction ends, on every exit path."""
    _open.discard(conn)


def require_tx(conn: AsyncConnection) -> None:
    if conn not in _open:
        raise NotInTransaction("this must run inside `relay_core.db.tx`")
