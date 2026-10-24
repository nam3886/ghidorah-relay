"""Deleting an unused account is a worker action: refused for each way an account is used, and
otherwise removes the account with its by-products and hands back the screenshot object keys."""

import asyncio
import uuid

import psycopg
import pytest

from relay_core.db import AccountInUse, AccountNotFound, delete_unused_account, open_pool, tx
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/db/accounts.py"


def account(db) -> uuid.UUID:
    account_id = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01')", account_id)
    return account_id


def delete_as(dsn, account_id):
    async def scenario():
        pool = await open_pool(dsn, max_size=2)
        try:
            async with tx(pool) as conn:
                return await delete_unused_account(conn, account_id)
        finally:
            await pool.close()

    return asyncio.run(scenario())


def counts(db, account_id):
    return {t: db.one("SELECT count(*) FROM %s WHERE %s = %%s" % (t, "id" if t == "accounts" else "account_id"),
                      account_id)[0]
            for t in ("accounts", "leases", "account_charges", "account_shots", "commands", "account_pools")}


def by_product_rows(db, account_id):
    db.run("INSERT INTO account_shots (account_id, object_key) VALUES (%s, 'shots/b.png')", account_id)
    db.run("INSERT INTO commands (provider, kind, account_id) VALUES ('p1', 'check', %s)", account_id)
    db.run("INSERT INTO account_pools (account_id, pool) VALUES (%s, 'video')", account_id)
    db.run("INSERT INTO leases (account_id, purpose, owner, until) VALUES (%s, 'check', 'w', now() - interval '1 h')",
           account_id)


@mutation(SRC, '("it has a job", "SELECT EXISTS (SELECT 1 FROM jobs WHERE account_id = %s)"),',
          '("it has a job", "SELECT false WHERE %s IS NULL"),',
           expect="return await delete_unused_account(conn, account_id)")
def test_an_account_with_a_job_is_refused(db):
    key = db.key(("p1", None, None))
    account_id = account(db)
    db.run("UPDATE jobs SET account_id = %s WHERE id = %s", account_id, db.job(key))
    with pytest.raises(AccountInUse, match="has a job"):
        delete_as(db.worker_dsn, account_id)
    assert counts(db, account_id)["accounts"] == 1


@mutation(SRC, '("it has an account charge", "SELECT EXISTS (SELECT 1 FROM account_charges WHERE account_id = %s)"),',
          '("it has an account charge", "SELECT false WHERE %s IS NULL"),',
           expect="return await delete_unused_account(conn, account_id)")
def test_an_account_with_a_charge_is_refused(db):
    key = db.key(("p1", None, None))
    account_id = account(db)
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount) "
           "VALUES (%s, %s, 'video', 2)", account_id, db.job(key))      # the job itself is unassigned
    with pytest.raises(AccountInUse, match="account charge"):
        delete_as(db.worker_dsn, account_id)
    assert counts(db, account_id)["account_charges"] == 1


@mutation(SRC, "AND until > clock_timestamp())", "AND false)", expect="DID NOT RAISE AccountInUse")
def test_an_account_with_a_live_lease_is_refused(db):
    account_id = account(db)
    db.run("INSERT INTO leases (account_id, purpose, owner, until) VALUES (%s, 'check', 'w', now() + interval '1 min')",
           account_id)
    with pytest.raises(AccountInUse, match="live lease"):
        delete_as(db.worker_dsn, account_id)
    assert counts(db, account_id)["leases"] == 1


@mutation(SRC, "(SELECT id FROM leases WHERE account_id = %s FOR UPDATE SKIP LOCKED)",
          "(SELECT id FROM leases WHERE account_id = %s FOR UPDATE)",
          expect="canceling statement due to lock timeout")
def test_an_expired_lease_row_a_fenced_write_holds_is_skipped_never_waited_for_and_the_account_stays(db):
    """That writer waits for the account row the delete holds: waiting for its lease row would be a cycle."""
    account_id = account(db)
    by_product_rows(db, account_id)                                   # includes one expired check lease
    with psycopg.connect(db.dsn, options="-c search_path=relay") as holder:
        holder.execute("SELECT 1 FROM leases WHERE account_id = %s FOR UPDATE", (account_id,))
        with pytest.raises(AccountInUse, match="a lease is being written"):
            delete_as(db.worker_dsn, account_id)
    assert counts(db, account_id)["accounts"] == 1 and counts(db, account_id)["leases"] == 1


@mutation(SRC, '"DELETE FROM account_shots WHERE account_id = %s RETURNING object_key"',
          '"DELETE FROM account_shots WHERE account_id = %s AND false RETURNING object_key"',
           expect="violates foreign key constraint \"account_shots_account_id_fkey\"")
def test_an_unused_account_is_deleted_with_its_by_products_and_the_shot_keys_come_back(db):
    account_id = account(db)
    by_product_rows(db, account_id)
    db.run("INSERT INTO account_shots (account_id, object_key) VALUES (%s, 'shots/a.png') "
           "ON CONFLICT (account_id) DO UPDATE SET object_key = 'shots/a.png'", account_id)
    other = account(db)
    by_product_rows(db, other)
    assert delete_as(db.worker_dsn, account_id) == ["shots/a.png"]
    assert set(counts(db, account_id).values()) == {0}
    assert counts(db, other)["commands"] == 1 and counts(db, other)["accounts"] == 1     # another account untouched


def test_an_unknown_account_is_reported(db):
    with pytest.raises(AccountNotFound):
        delete_as(db.worker_dsn, uuid.uuid4())


def test_a_refusal_changes_nothing_and_the_api_role_cannot_run_the_function_at_all(db):
    account_id = account(db)
    by_product_rows(db, account_id)
    before = counts(db, account_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        delete_as(db.api_dsn, account_id)
    assert counts(db, account_id) == before
