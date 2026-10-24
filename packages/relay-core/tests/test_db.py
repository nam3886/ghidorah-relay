"""The database session layer: session options, isolation, kill of an idle transaction, and a
transaction that cannot be held across foreign I/O."""

import asyncio

import psycopg
import pytest

from relay_core.db import assert_no_tx, open_pool, session_options, tx
from relay_core.testing.mutation import mutation

POOL = "packages/relay-core/src/relay_core/db/pool.py"


def run_with_pool(db, scenario, **pool_args):
    async def main():
        pool = await open_pool(db.dsn, **pool_args)
        try:
            return await scenario(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


def test_the_session_options_and_isolation_are_in_force(db):
    async def scenario(pool):
        async with tx(pool) as conn:
            names = ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout", "TimeZone",
                     "search_path", "transaction_isolation")
            return [(await (await conn.execute("SHOW " + n)).fetchone())[0] for n in names]

    assert run_with_pool(db, scenario) == ["5s", "3s", "15s", "UTC", "relay", "read committed"]


def test_a_transaction_left_idle_past_the_limit_is_killed(db):
    async def scenario(pool):
        with pytest.raises(psycopg.Error) as err:
            async with tx(pool) as conn:
                await conn.execute("SELECT 1")
                await asyncio.sleep(2.5)       # the server drops the session after 1 s idle in a transaction
                await conn.execute("SELECT 1")
        async with tx(pool) as conn:           # the pool replaces the dead connection
            assert (await (await conn.execute("SELECT 1")).fetchone())[0] == 1
        return str(err.value)

    message = run_with_pool(db, scenario, options=session_options(idle_in_tx_s=1))
    assert "idle-in-transaction" in message or "terminating connection" in message, message


@mutation(POOL, "async with conn.transaction():", "if True:", expect="assert 'half' == ''")
def test_a_failed_transaction_leaves_nothing_and_a_clean_one_commits(db):
    key = db.key(("p1", None, None))

    async def scenario(pool):
        with pytest.raises(RuntimeError, match="boom"):
            async with tx(pool) as conn:
                await conn.execute("UPDATE api_keys SET label = 'half' WHERE id = %s", (key,))
                raise RuntimeError("boom")
        assert db.one("SELECT label FROM api_keys WHERE id = %s", key)[0] == ""
        async with tx(pool) as conn:
            await conn.execute("UPDATE api_keys SET label = 'done' WHERE id = %s", (key,))
        assert db.one("SELECT label FROM api_keys WHERE id = %s", key)[0] == "done"

    run_with_pool(db, scenario)


def test_a_transaction_cannot_be_held_across_foreign_io(db):
    async def scenario(pool):
        assert_no_tx()                              # outside a transaction foreign I/O is fine
        async with tx(pool):
            with pytest.raises(RuntimeError, match="foreign I/O"):
                assert_no_tx()
            with pytest.raises(RuntimeError, match="nested"):
                async with tx(pool):
                    pass
        assert_no_tx()                              # and the flag is gone after the block
        with pytest.raises(TimeoutError):           # a body that waits on something foreign is cancelled
            async with tx(pool, hold_s=0.2) as conn:
                await conn.execute("UPDATE api_keys SET label = 'held' WHERE false")
                await asyncio.sleep(2)
        async with tx(pool) as conn:                # its connection came back to the pool
            await conn.execute("SELECT 1")

    run_with_pool(db, scenario, max_size=1)


def test_the_pool_is_bounded_and_waits_are_limited(db):
    async def scenario(pool):
        assert pool.max_size == 3 and pool.max_waiting == 12
        assert pool.timeout == 5

    run_with_pool(db, scenario, max_size=3)
