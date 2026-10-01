"""Deleting an account that was never used (contracts D37). Runs as `relay_worker`: the api has no DELETE on
accounts, and the foreign keys from every account-owned table are RESTRICT, so nothing cascades by accident."""

from psycopg import AsyncConnection

from relay_core.txguard import require_tx


class AccountError(Exception):
    pass


class AccountNotFound(AccountError):
    pass


class AccountInUse(AccountError):
    """The account has a job, an account charge or a live lease: disable it instead."""


# (reason, statement). The account row is locked first, so a job or lease that references it waits (the foreign
# key takes KEY SHARE on the row) and cannot slip in between the checks and the delete.
_USED = (
    ("it has a job", "SELECT EXISTS (SELECT 1 FROM jobs WHERE account_id = %s)"),
    ("it has an account charge", "SELECT EXISTS (SELECT 1 FROM account_charges WHERE account_id = %s)"),
    ("it holds a live lease",
     "SELECT EXISTS (SELECT 1 FROM leases WHERE account_id = %s AND until > clock_timestamp())"),
)


async def delete_unused_account(conn: AsyncConnection, account_id) -> list[str]:
    """Delete the account and its by-products in the caller's transaction; return the object-store keys of its
    screenshots, which the caller removes from the store once the transaction has committed."""
    require_tx(conn)
    found = await (await conn.execute("SELECT 1 FROM accounts WHERE id = %s FOR UPDATE", (account_id,))).fetchone()
    if found is None:
        raise AccountNotFound("no account %s" % account_id)
    for reason, query in _USED:
        if (await (await conn.execute(query, (account_id,))).fetchone())[0]:
            raise AccountInUse("account %s cannot be deleted: %s" % (account_id, reason))
    await conn.execute("DELETE FROM leases WHERE account_id = %s", (account_id,))        # expired rows only
    keys = [r[0] for r in await (await conn.execute(
        "DELETE FROM account_shots WHERE account_id = %s RETURNING object_key", (account_id,))).fetchall()]
    await conn.execute("DELETE FROM commands WHERE account_id = %s", (account_id,))
    await conn.execute("DELETE FROM account_capacity WHERE account_id = %s", (account_id,))
    await conn.execute("DELETE FROM accounts WHERE id = %s", (account_id,))
    return sorted(keys)
