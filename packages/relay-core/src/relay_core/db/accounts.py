"""Deleting an account that was never used. Runs as `relay_worker`: the api has no DELETE on
accounts, and the foreign keys from every account-owned table are RESTRICT, so nothing cascades by accident."""

from collections.abc import Collection

from psycopg import AsyncConnection

from relay_core.txguard import require_tx


class AccountError(Exception):
    pass


class AccountNotFound(AccountError):
    pass


class AccountInUse(AccountError):
    """The account has a job, an account charge or a live lease: disable it instead."""


# (reason, statement). The account row is locked FOR UPDATE first, so a job or lease that references the account
# waits (the foreign key takes KEY SHARE on the row) and cannot slip in between the checks and the delete.
_USED = (
    ("it has a job", "SELECT EXISTS (SELECT 1 FROM jobs WHERE account_id = %s)"),
    ("it has an account charge", "SELECT EXISTS (SELECT 1 FROM account_charges WHERE account_id = %s)"),
    ("it holds a live lease",
     "SELECT EXISTS (SELECT 1 FROM leases WHERE account_id = %s AND until > clock_timestamp())"),
)


async def lock_account(conn: AsyncConnection, account_id, *, skip_locked: bool = False, delete: bool = False) -> bool:
    """The account row lock, taken after the job/lease rows and before capacity, charges and the key wallet
    (lock order):
    `FOR NO KEY UPDATE` for a writer, `FOR UPDATE` for the delete that removes the row. With `skip_locked` (a claim,
    which never waits) False also means another transaction holds the row; without it False means the account is
    gone. Capacity rows are locked or created after this, in any order: everything is serialised per account.
    The order of every transaction: lease, job, account, capacity rows, account charge, key wallet; a command row
    comes after its lease. A path that holds nothing before the account (a claim, a wake, a delete, a check's lease
    insert) may take it first; a claim only ever skips."""
    mode = "FOR UPDATE" if delete else "FOR NO KEY UPDATE"
    skip = " SKIP LOCKED" if skip_locked else ""
    return (await (await conn.execute("SELECT 1 FROM accounts WHERE id = %s " + mode + skip, (account_id,))
                   ).fetchone()) is not None


async def account_in_use(conn: AsyncConnection, account_id) -> str | None:
    """Why the account cannot be deleted ("it has a job", ...), or None when it is unused. A plain read: only
    `delete_unused_account`, which holds the row lock, decides."""
    for reason, query in _USED:
        if (await (await conn.execute(query, (account_id,))).fetchone())[0]:
            return reason
    return None


async def delete_unused_account(conn: AsyncConnection, account_id, own_leases: Collection[int] = ()) -> list[str]:
    """Delete the account and its by-products in the caller's transaction; return the object-store keys of its
    screenshots, which the caller removes from the store (the delete command removes them BEFORE the transaction).
    `own_leases` are lease ids the caller holds on this account (a check that merges its own account away): they
    are dropped first, inside this transaction, so they never make the account "in use"; a refusal rolls that back."""
    require_tx(conn)
    if not await lock_account(conn, account_id, delete=True):
        raise AccountNotFound("no account %s" % account_id)
    if own_leases:
        await conn.execute("DELETE FROM leases WHERE account_id = %s AND id = ANY(%s)", (account_id, list(own_leases)))
    reason = await account_in_use(conn, account_id)
    if reason is not None:
        raise AccountInUse("account %s cannot be deleted: %s" % (account_id, reason))
    # expired rows only. One that a fenced write holds is skipped, never waited for (that writer waits for the account
    # row we hold: a cycle), and is left in place: the account is then still in use.
    await conn.execute("DELETE FROM leases WHERE id IN "
                       "(SELECT id FROM leases WHERE account_id = %s FOR UPDATE SKIP LOCKED)", (account_id,))
    if await (await conn.execute("SELECT 1 FROM leases WHERE account_id = %s", (account_id,))).fetchone():
        raise AccountInUse("account %s cannot be deleted: a lease is being written" % account_id)
    keys = [r[0] for r in await (await conn.execute(
        "DELETE FROM account_shots WHERE account_id = %s RETURNING object_key", (account_id,))).fetchall()]
    await conn.execute("DELETE FROM commands WHERE account_id = %s", (account_id,))
    await conn.execute("DELETE FROM account_capacity WHERE account_id = %s", (account_id,))
    await conn.execute("DELETE FROM accounts WHERE id = %s", (account_id,))
    return sorted(keys)
