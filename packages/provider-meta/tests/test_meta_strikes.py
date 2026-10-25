"""meta's signed-out reads through the real account effects of the core: one read is a strike, never a dead account;
the third spaced read reaches meta's strike threshold and ends in its strike effect. A checkpoint is a rest with no
strike, longer on each repeat, and never ends the account."""

import asyncio
import uuid
from datetime import UTC, datetime

from relay_core import pool
from relay_core.db import open_pool, tx
from relay_core.pool import PoolConfig
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_meta.engine.check import checkpoint_rest, signed_out_effects
from relay_meta.spec import SPEC

CONSTANTS = "packages/provider-meta/src/relay_meta/constants.py"
POOLS = {"image": PoolConfig("image", "UTC", 40), "video": PoolConfig("video", "UTC", 20)}


def row(db, a):
    return db.one("SELECT state, state_reason, strikes FROM accounts WHERE id = %s", a)


def age(db, a, minutes=11):
    """Make the last counted strike `minutes` old: past the gap, as the recheck after a rest is."""
    db.run("UPDATE accounts SET strike_at = clock_timestamp() - make_interval(mins => %s) WHERE id = %s", minutes, a)


@mutation(CONSTANTS, "SIGNED_OUT_GAP_S = 540 ", "SIGNED_OUT_GAP_S = 3600 ", expect="the second spaced read")
def test_one_signed_out_read_is_a_strike_and_the_third_spaced_one_ends_the_account(db):
    a = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state) VALUES (%s, 'meta', '\\x01', 'available')", a)

    async def scenario():
        p = await open_pool(db.dsn, max_size=2)

        async def read():
            async with tx(p) as conn:
                await pool.apply_effects(conn, a, signed_out_effects(datetime.now(UTC)), SPEC.caps, POOLS)

        try:
            await read()
            assert row(db, a) == ("available", None, 1), "one signed-out read: %r" % (row(db, a),)
            await read()                                    # inside the gap: the same strike
            assert row(db, a) == ("available", None, 1), "a read inside the gap: %r" % (row(db, a),)
            age(db, a)
            await read()
            assert row(db, a) == ("available", None, 2), "the second spaced read: %r" % (row(db, a),)
            age(db, a)
            await read()
        finally:
            await p.close()

    asyncio.run(scenario())
    assert row(db, a) == ("dead", "signed_out", 3), "threshold reached but not dead: %r" % (row(db, a),)


def test_repeated_checkpoints_rest_the_account_longer_with_no_strike_and_never_end_it(db):
    a = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state) VALUES (%s, 'meta', '\\x01', 'available')", a)
    settings = FakeSettings(SPEC)

    async def scenario():
        p = await open_pool(db.dsn, max_size=2)
        try:
            for hours in (0.5, 1, 2, 4, 6, 6):
                async with tx(p) as conn:
                    rest = checkpoint_rest(datetime.now(UTC), settings, a, "seen by a check")
                    await pool.apply_effects(conn, a, (rest,), SPEC.caps, POOLS)
                assert row(db, a) == ("available", None, 0), "a checkpoint ended or struck the account: %r" % (
                    row(db, a),)
                assert db.one("SELECT cooldown_until BETWEEN now() + make_interval(secs => %s) "
                              "AND now() + make_interval(secs => %s) FROM accounts WHERE id = %s",
                              hours * 3600 - 60, hours * 3600 + 60, a)[0], "the account rests %g h" % hours
        finally:
            await p.close()

    asyncio.run(scenario())
