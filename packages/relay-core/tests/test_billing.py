"""Charge and refund: the money rules in SQL (I3 transaction part, I4, I5, I10, I25, I29).

A key has one points wallet (`*`) and a cap per provider; a charge takes both or neither and writes one ledger row per
scope. Tests run the real statements against Postgres. The concurrent ones use separate pooled connections and a
barrier at a failpoint, so the callers really overlap on the server.
"""

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from contextvars import Context
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import JOB_INSERT, job_args

from relay_core import billing, failpoints
from relay_core.billing import KeyDisabled, QuotaExceeded, WalletMissing
from relay_core.db import open_pool, tx
from relay_core.testing.mutation import mutation

PO = "packages/relay-core/src/relay_core/txguard.py"
B = "packages/relay-core/src/relay_core/billing.py"
MONEY_SQL = "packages/relay-core/src/relay_core/db/migrations/0020_money_v2.sql"
WEST, EAST = "Etc/GMT+12", "Pacific/Kiritimati"   # UTC-12 and UTC+14: their calendar dates always differ
CHARGE_UPDATE = "(w.day_limit > 0 AND {_DAY_BASE} + %(points)s <= w.day_limit)"


def run(db, scenario, max_size=10):
    async def main():
        pool = await open_pool(db.dsn, max_size=max_size)
        try:
            return await scenario(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


async def charge(pool, key, job, points, *, provider="p1"):
    async with tx(pool) as conn:
        return await billing.charge(conn, key, provider, points, job)


def star_id(charged) -> int:
    """The ledger row of the points wallet in a charge (the first; the provider's cap is the second)."""
    return charged.ledger_ids[0]


def new_key(db, where, day=None, total=None, **kw):
    """A key whose limit sits on `where`; the other scope is unlimited. The cap row of `p1` always exists."""
    caps = [("p1", day, total)] if where == "p1" else [("p1", None, None)]
    star = (day, total) if where == "*" else (None, None)
    return db.key(*caps, star=star, **kw)


async def refund(pool, charge_id):
    async with tx(pool) as conn:
        return await billing.refund(conn, charge_id)


def ledger_rows(db, key, scope="*"):
    return db.all("SELECT entry, amount FROM ledger WHERE key_id = %s AND scope = %s ORDER BY id", key, scope)


# ---- limits --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["*", "p1"])
def test_the_day_limit_blocks_the_charge_that_would_pass_it(db, where):
    key = new_key(db, where, day=10)
    jobs = [db.job(key) for _ in range(3)]

    async def scenario(pool):
        await charge(pool, key, jobs[0], 6)
        with pytest.raises(QuotaExceeded):
            await charge(pool, key, jobs[1], 5)
        await charge(pool, key, jobs[2], 4)          # exactly at the limit is allowed

    run(db, scenario)
    assert db.wallet(key) == (10, 10) and db.wallet(key, "p1") == (10, 10)
    for scope in ("*", "p1"):
        assert ledger_rows(db, key, scope) == [("charge", 6), ("charge", 4)]      # the refused charge left no row


@pytest.mark.parametrize("where", ["*", "p1"])
def test_the_total_limit_blocks_across_days_and_another_provider_is_not_blocked_by_a_cap(db, where):
    key = db.key(("p1", None, 10 if where == "p1" else None), ("p2", None, None),
                 star=(None, 10 if where == "*" else None))
    jobs = [db.job(key) for _ in range(3)]

    async def scenario(pool):
        db.pin(WEST)
        await charge(pool, key, jobs[0], 8)
        db.pin(EAST)
        with pytest.raises(QuotaExceeded):
            await charge(pool, key, jobs[1], 3)              # another billing day: the total still stops it
        if where == "p1":                                    # an exhausted cap of one provider does not block another
            await charge(pool, key, jobs[2], 10, provider="p2")

    run(db, scenario)
    assert db.wallet(key)[1] == (18 if where == "p1" else 8)


def test_a_charge_takes_the_star_wallet_and_the_provider_cap_together_or_neither(db):
    capped = db.key(("p1", 5, None))                 # the cap refuses, the points wallet would take it
    pointed = db.key(("p1", None, None), star=(5, None))     # the points wallet refuses, the cap would take it
    jobs = [db.job(capped), db.job(capped), db.job(pointed), db.job(pointed)]

    async def scenario(pool):
        for key, a, b in ((capped, jobs[0], jobs[1]), (pointed, jobs[2], jobs[3])):
            done = await charge(pool, key, a, 3)
            assert len(done.ledger_ids) == 2, "one ledger row per scope"
            with pytest.raises(QuotaExceeded):
                await charge(pool, key, b, 3)

    run(db, scenario)
    for key in (capped, pointed):
        assert db.wallet(key) == (3, 3) and db.wallet(key, "p1") == (3, 3), "neither scope moved for the refused charge"
        for scope in ("*", "p1"):
            assert ledger_rows(db, key, scope) == [("charge", 3)]


mutation(B, "if len(rows) < len(scopes):", "if len(rows) < 1:")(
    test_a_charge_takes_the_star_wallet_and_the_provider_cap_together_or_neither)


@mutation(B, "async with conn.transaction():", "if True:", expect="assert (3, 3) == (0, 0)")
def test_a_refused_charge_that_the_caller_survives_leaves_no_trace_in_its_transaction(db):
    # `recheck` catches QuotaExceeded and goes on in the same transaction: the savepoint of the charge must undo the
    # points wallet's half of the statement
    key = db.key(("p1", 0, None))
    job = db.job(key)

    async def scenario(pool):
        async with tx(pool) as conn:
            with pytest.raises(QuotaExceeded):
                await billing.charge(conn, key, "p1", 3, job)

    run(db, scenario)
    assert db.wallet(key) == (0, 0) and ledger_rows(db, key) == []


def test_a_disabled_key_cannot_be_charged(db):
    key = db.key(("p1", None, None), enabled=False)
    job = db.job(key)

    async def scenario(pool):
        with pytest.raises(KeyDisabled) as err:
            await charge(pool, key, job, 0)
        assert err.value.code == "auth.key_disabled"

    run(db, scenario)
    assert ledger_rows(db, key) == [] and db.wallet(key) == (0, 0)


mutation(B, "AND k.id = w.key_id AND k.enabled", "AND k.id = w.key_id")(test_a_disabled_key_cannot_be_charged)


@mutation(B, "(w.day_limit > 0 AND ", "(", expect="DID NOT RAISE QuotaExceeded")
@pytest.mark.parametrize("where", ["*", "p1"])
def test_a_day_limit_of_zero_blocks_even_a_zero_point_charge(db, where):
    key = new_key(db, where, day=0)
    job = db.job(key)

    async def scenario(pool):
        for points in (0, 1):
            with pytest.raises(QuotaExceeded):
                await charge(pool, key, job, points)

    run(db, scenario)


@mutation(B, "(w.total_limit > 0 AND ", "(", expect="DID NOT RAISE QuotaExceeded")
@pytest.mark.parametrize("where", ["*", "p1"])
def test_a_total_limit_of_zero_blocks_even_a_zero_point_charge(db, where):
    key = new_key(db, where, total=0)
    job = db.job(key)

    async def scenario(pool):
        for points in (0, 1):
            with pytest.raises(QuotaExceeded):
                await charge(pool, key, job, points)

    run(db, scenario)


def test_an_unlimited_wallet_takes_a_zero_point_job(db):
    key = db.key(("p1", None, None))
    job = db.job(key)
    run(db, lambda pool: charge(pool, key, job, 0))
    assert ledger_rows(db, key) == [("charge", 0)] and ledger_rows(db, key, "p1") == [("charge", 0)]


def test_a_missing_wallet_is_an_internal_error_not_a_quota_answer(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        with pytest.raises(WalletMissing):
            await charge(pool, key, job, 1, provider="nobody")          # no cap row for the provider
        with pytest.raises(WalletMissing):
            await charge(pool, uuid.uuid4(), job, 1)
        with pytest.raises(ValueError):
            await charge(pool, key, job, -1)
        with pytest.raises(ValueError):
            await charge(pool, key, job, 1, provider="*")

    run(db, scenario)
    assert db.wallet(key) == (0, 0), "the refused charge to a provider with no cap moved nothing"


# ---- wallets of new providers (I29) ----------------------------------------------------------------------------------
@mutation(B, "SELECT id, %s, 0 FROM api_keys", "SELECT id, %s, NULL FROM api_keys",
          expect="DID NOT RAISE QuotaExceeded")
def test_a_new_provider_is_closed_on_existing_keys(db):
    key = db.key(("p1", None, None))
    job = db.job(key, provider="p2")

    async def scenario(pool):
        async with tx(pool) as conn:
            await billing.ensure_wallets(conn, "p2")
            await billing.ensure_wallets(conn, "p2")                         # registering twice changes nothing
        for points in (0, 5):
            with pytest.raises(QuotaExceeded):
                await charge(pool, key, job, points, provider="p2")
        await charge(pool, key, db.job(key), 5)                              # the key's old wallets still work
        db.one("UPDATE key_wallets SET day_limit = NULL WHERE key_id = %s AND scope = 'p2' RETURNING 1", key)
        await charge(pool, key, job, 5, provider="p2")

    run(db, scenario)
    assert db.one("SELECT count(*) FROM key_wallets WHERE key_id = %s", key)[0] == 3         # *, p1, p2
    assert db.wallet(key) == (10, 10) and db.wallet(key, "p2") == (5, 5)


def test_registering_a_provider_gives_a_key_without_a_points_wallet_a_closed_one(db):
    key = uuid.uuid4()
    db.one("INSERT INTO api_keys (id, key_hash, key_prefix) VALUES (%s, 'h', 'rk-x') RETURNING 1", key)

    async def scenario(pool):
        async with tx(pool) as conn:
            await billing.ensure_wallets(conn, "p1")

    run(db, scenario)
    assert db.all("SELECT scope, day_limit FROM key_wallets WHERE key_id = %s ORDER BY scope", key) == [
        ("*", 0), ("p1", 0)]


def test_a_new_keys_wallets_carry_the_limits_chosen_for_it(db):
    key = uuid.uuid4()
    db.one("INSERT INTO api_keys (id, key_hash, key_prefix) VALUES (%s, 'h', 'rk-x') RETURNING 1", key)

    async def scenario(pool):
        async with tx(pool) as conn:
            await billing.create_wallets(conn, key, {"*": (7, 100), "p1": (None, None), "p2": (0, None)})

    run(db, scenario)
    got = db.all("SELECT scope, day_limit, total_limit FROM key_wallets WHERE key_id = %s ORDER BY 1", key)
    assert got == [("*", 7, 100), ("p1", None, None), ("p2", 0, None)]


# ---- refunds -------------------------------------------------------------------------------------------------------
@mutation(B, "if inserted is None:", "if False:", expect="assert 8 == 1")
@mutation(B, "ON CONFLICT (refund_of) DO NOTHING RETURNING id",
          "ON CONFLICT (refund_of) DO UPDATE SET refund_of = EXCLUDED.refund_of RETURNING id", expect="assert 8 == 1")
@mutation(MONEY_SQL, "CREATE UNIQUE INDEX ledger_one_refund ON ledger (refund_of);",
          "CREATE INDEX ledger_one_refund ON ledger (refund_of);",
           expect="no unique or exclusion constraint matching the ON CONFLICT specification")
def test_eight_concurrent_refunders_give_one_refund_row_and_one_decrement(db):
    key = db.key(("p1", None, None))
    victim, other = db.job(key), db.job(key)

    async def scenario(pool):
        cid = star_id(await charge(pool, key, victim, 10))
        await charge(pool, key, other, 70)
        barrier = asyncio.Barrier(8)

        async def meet():
            await barrier.wait()

        failpoints.arm("billing.refund.start", meet)
        try:
            return await asyncio.gather(*(refund(pool, cid) for _ in range(8)))
        finally:
            failpoints.disarm()

    results = run(db, scenario)
    assert sum(r.refunded for r in results) == 1
    assert db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 1
    assert db.wallet(key) == (70, 70) and db.wallet(key, "p1") == (80, 80), "only the star row was refunded"


@mutation(B, "same_day = charged_day == today", "same_day = True", expect="assert (5, 5) == (15, 5)")
def test_a_refund_of_a_charge_from_before_the_reset_touches_only_the_total(db):
    key = db.key(("p1", None, None))
    old, new = db.job(key), db.job(key)

    async def scenario(pool):
        db.pin(WEST)
        old_id = star_id(await charge(pool, key, old, 10))
        new_id = star_id(await charge(pool, key, new, 5))
        db.pin(EAST)                                                 # EAST is already on the next billing day
        assert (await refund(pool, old_id)).refunded
        assert db.wallet(key) == (15, 5)
        db.pin(WEST)
        assert (await refund(pool, new_id)).refunded                 # a charge of the current day moves both
        assert db.wallet(key) == (10, 0)

    run(db, scenario)


@mutation(B, "used_total = GREATEST(used_total - %(a)s, 0), ", "used_total = used_total - %(a)s, ",
           expect="violates check constraint \"key_wallets_used_total_check\"")
@mutation(B, "if clamped:", "if False:",
           expect="[r.getMessage() for r in caplog.records if \"ALERT billing clamp\" in r.getMessage()]")
def test_a_refund_never_takes_a_counter_below_zero_and_raises_the_alert(db, caplog):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        cid = star_id(await charge(pool, key, job, 5))
        db.one("UPDATE key_wallets SET used_day = 2, used_total = 2 WHERE key_id = %s AND scope = '*' RETURNING 1",
               key)                                                                                       # drift
        with caplog.at_level(logging.ERROR, logger="relay_core.billing"):
            result = await refund(pool, cid)
        assert result.refunded and result.clamped
        assert db.wallet(key) == (0, 0)

    run(db, scenario)
    assert [r.getMessage() for r in caplog.records if "ALERT billing clamp" in r.getMessage()]
    # the alert is data the admin alerts read, written with the refund
    alert = db.one("SELECT alert, key_id, scope, amount, used_day, used_total FROM billing_alerts")
    assert alert == ("clamp", key, "*", 5, 2, 2)


def test_a_refund_that_clamps_nothing_raises_no_alert(db, caplog):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        cid = star_id(await charge(pool, key, job, 5))
        with caplog.at_level(logging.ERROR, logger="relay_core.billing"):
            assert (await refund(pool, cid)) == billing.Refund(True, False)

    run(db, scenario)
    assert caplog.records == []


@mutation(MONEY_SQL, "CREATE INDEX ledger_job ON ledger (job_id);",
          "CREATE UNIQUE INDEX ledger_job ON ledger (job_id) WHERE entry = 'refund';",
           expect="violates unique constraint \"ledger_job\"")
def test_a_charge_made_again_after_a_refund_can_itself_be_refunded(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        first = star_id(await charge(pool, key, job, 10))
        assert (await refund(pool, first)).refunded
        second = star_id(await charge(pool, key, job, 10))           # Re-check or Recover charges again
        assert db.wallet(key) == (10, 10)
        assert (await refund(pool, second)).refunded
        assert not (await refund(pool, first)).refunded              # each charge row is refunded once
        assert not (await refund(pool, second)).refunded

    run(db, scenario)
    assert db.wallet(key) == (0, 0)
    assert [e for e, _ in ledger_rows(db, key)] == ["charge", "refund", "charge", "refund"]


def test_refunding_every_row_of_a_charge_gives_back_the_star_wallet_and_the_cap(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        done = await charge(pool, key, job, 7)
        assert db.wallet(key) == (7, 7) and db.wallet(key, "p1") == (7, 7)
        for row in done.ledger_ids:
            assert (await refund(pool, row)).refunded

    run(db, scenario)
    assert db.wallet(key) == (0, 0) and db.wallet(key, "p1") == (0, 0)
    assert db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 2


def test_only_a_charge_row_can_be_refunded(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        cid = star_id(await charge(pool, key, job, 3))
        await refund(pool, cid)
        refund_row = db.one("SELECT id FROM ledger WHERE entry = 'refund'")[0]
        for bad in (refund_row, 999_999):
            with pytest.raises(ValueError, match="not a charge"):
                await refund(pool, bad)

    run(db, scenario)


# ---- the billing day -----------------------------------------------------------------------------------------------
def tz_with_midnight_in(seconds: float) -> str:
    """A POSIX zone whose local midnight falls `seconds` from now; lets a test cross a day boundary in real time."""
    now = datetime.now(UTC)
    second_of_day = now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1e6
    offset = round(-(second_of_day + seconds)) % 86400
    if offset > 43200:
        offset -= 86400
    east = offset                                  # POSIX writes an east offset with a minus sign
    sign, mag = ("-", east) if east >= 0 else ("+", -east)
    return "UTC%s%02d:%02d:%02d" % (sign, mag // 3600, mag % 3600 // 60, mag % 60)


@mutation(B, "(clock_timestamp() AT TIME ZONE %(tz)s)::date", "(now() AT TIME ZONE %(tz)s)::date",
           expect="after.billing_day == before.billing_day + timedelta(days=1)")
@mutation(B, "THEN 0 ELSE w.used_day END", "THEN w.used_day ELSE w.used_day END", expect="assert (7, 7) == (3, 7)")
def test_a_transaction_that_crosses_midnight_charges_the_new_day_and_resets_the_day_counter(db):
    key = db.key(("p1", None, None))
    first_job, second_job = db.job(key), db.job(key)
    db.pin(tz_with_midnight_in(3))

    async def pause_past_midnight():
        await asyncio.sleep(4)

    async def scenario(pool):
        before = await charge(pool, key, first_job, 4)
        failpoints.arm("billing.charge.before_update", pause_past_midnight)
        try:
            async with tx(pool) as conn:
                await conn.execute("SELECT 1")             # the transaction is open: now() is frozen from here
                after = await billing.charge(conn, key, "p1", 3, second_job)
        finally:
            failpoints.disarm()
        return before, after

    before, after = run(db, scenario)
    assert after.billing_day == before.billing_day + timedelta(days=1)
    assert db.wallet(key) == (3, 7)
    assert db.one("SELECT day_anchor FROM key_wallets WHERE key_id = %s", key)[0] == after.billing_day
    assert [d for (d,) in db.all("SELECT billing_day FROM ledger WHERE scope = '*' ORDER BY id")] == [
        before.billing_day, after.billing_day]


# ---- the charge commits with its job (I3 transaction part) -----------------------------------------------------------
async def create_job(pool, key, job_id, points):
    """The stub of job creation: charge, then insert the job row, in one transaction."""
    async with tx(pool) as conn:
        await billing.charge(conn, key, "p1", points, job_id)
        await failpoints.hit("create.after_charge")
        await conn.execute(JOB_INSERT, job_args(job_id, key, price=points))


def test_a_charge_and_its_job_commit_together(db):
    key = db.key(("p1", None, None))
    job = uuid.uuid4()
    run(db, lambda pool: create_job(pool, key, job, 5))
    assert db.wallet(key) == (5, 5)
    assert db.one("SELECT count(*) FROM jobs WHERE id = %s", job)[0] == 1
    assert ledger_rows(db, key) == [("charge", 5)]


@mutation("packages/relay-core/src/relay_core/db/pool.py", "async with conn.transaction():", "if True:",
           expect="violates foreign key constraint \"ledger_job_fk\"")
def test_a_failure_between_the_charge_and_the_job_insert_leaves_no_charge_and_no_ledger_row(db):
    key = db.key(("p1", None, None))

    async def scenario(pool):
        failpoints.arm("create.after_charge", lambda: _raise(failpoints.FailpointError("between charge and insert")))
        try:
            with pytest.raises(failpoints.FailpointError):
                await create_job(pool, key, uuid.uuid4(), 5)
        finally:
            failpoints.disarm()

    run(db, scenario)
    assert db.wallet(key) == (0, 0)
    assert ledger_rows(db, key) == []
    assert db.one("SELECT count(*) FROM jobs")[0] == 0


async def _raise(error):
    raise error


CRASH_SCRIPT = """
import asyncio, sys
from relay_core import failpoints
from relay_core.billing import charge
from relay_core.db import open_pool, tx

async def main(dsn, key, job):
    pool = await open_pool(dsn)
    async with tx(pool) as conn:
        await charge(conn, key, "p1", 5, job)
        await failpoints.hit("create.after_charge")
        raise SystemExit("the failpoint did not fire")

asyncio.run(main(*sys.argv[1:]))
"""


def test_a_process_killed_between_the_charge_and_the_job_insert_leaves_no_trace(db):
    key = db.key(("p1", None, None))
    env = {**os.environ, "RELAY_FAILPOINTS": "create.after_charge=crash", "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, db.dsn, str(key), str(uuid.uuid4())],
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 137, done.stderr
    assert db.wallet(key) == (0, 0)
    assert ledger_rows(db, key) == []


# ---- failpoints are env-gated ----------------------------------------------------------------------------------------
def test_failpoints_cannot_be_armed_without_the_environment_variable(monkeypatch):
    monkeypatch.delenv("RELAY_FAILPOINTS")
    with pytest.raises(RuntimeError, match="failpoints are off"):
        failpoints.arm("x", lambda: _raise(RuntimeError()))
    asyncio.run(failpoints.hit("x"))               # and a hit is a no-op


def test_a_failpoint_named_in_the_environment_fires(monkeypatch):
    monkeypatch.setenv("RELAY_FAILPOINTS", "a=raise,b=sleep:0")
    with pytest.raises(failpoints.FailpointError):
        asyncio.run(failpoints.hit("a"))
    asyncio.run(failpoints.hit("b"))
    asyncio.run(failpoints.hit("unnamed"))
    monkeypatch.setenv("RELAY_FAILPOINTS", "a=explode")
    with pytest.raises(ValueError, match="unknown failpoint action"):
        asyncio.run(failpoints.hit("a"))
    failpoints.disarm()


# ---- review fixes ----------------------------------------------------------------------------------------------------
@mutation(B, "same_day = charged_day == today and anchor == charged_day", "same_day = charged_day == today",
           expect="clamped=True) == Refund(refund")
def test_a_refund_that_waits_across_midnight_does_not_touch_the_new_days_counter(db):
    key = db.key(("p1", None, None))
    old, new = db.job(key), db.job(key)
    db.pin(tz_with_midnight_in(3))

    async def scenario(pool):
        old_id = star_id(await charge(pool, key, old, 10))
        extra_pool = await open_pool(db.dsn, max_size=2)

        async def new_day_charge_commits_meanwhile():
            await asyncio.sleep(4)                       # the refund has read "today" and holds its ledger row
            # another request: its own task and context, so it is not "inside" the refund's transaction
            await asyncio.get_running_loop().create_task(charge(extra_pool, key, new, 5), context=Context())

        failpoints.arm("billing.refund.before_counters", new_day_charge_commits_meanwhile)
        try:
            result = await refund(pool, old_id)
        finally:
            failpoints.disarm()
            await extra_pool.close()
        return result

    result = run(db, scenario)
    assert result == billing.Refund(True, False)
    assert db.wallet(key) == (5, 5)                      # the new day keeps its 5; only the total took the refund


@mutation(B, "_require_tx(conn)\n    if points < 0:", "if points < 0:", expect="DID NOT RAISE NotInTransaction")
def test_charge_outside_a_transaction_is_refused_and_changes_nothing(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        async with pool.connection() as conn:             # autocommit: every statement would commit on its own
            with pytest.raises(billing.NotInTransaction):
                await billing.charge(conn, key, "p1", 5, job)

    run(db, scenario)
    assert db.wallet(key) == (0, 0)
    assert ledger_rows(db, key) == []


@mutation(B, '_require_tx(conn)\n    await failpoints.hit("billing.refund.start")',
          'await failpoints.hit("billing.refund.start")', expect="DID NOT RAISE NotInTransaction")
def test_refund_outside_a_transaction_is_refused_and_changes_nothing(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        cid = star_id(await charge(pool, key, job, 5))
        async with pool.connection() as conn:
            with pytest.raises(billing.NotInTransaction):
                await billing.refund(conn, cid)

    run(db, scenario)
    assert db.wallet(key) == (5, 5)
    assert ledger_rows(db, key) == [("charge", 5)]


@mutation(B, "WHERE key_id = %s AND scope = %s FOR NO KEY UPDATE", "WHERE key_id = %s AND scope = %s FOR UPDATE",
          expect="deadlock detected")
def test_two_refunds_of_different_charges_on_one_wallet_do_not_deadlock(db):
    key = db.key(("p1", None, None))
    jobs = [db.job(key), db.job(key)]

    async def scenario(pool):
        ids = [star_id(await charge(pool, key, j, 3)) for j in jobs]
        barrier = asyncio.Barrier(2)

        async def meet():
            await barrier.wait()                         # both hold their ledger row, neither has the wallet yet

        failpoints.arm("billing.refund.before_counters", meet)
        try:
            return await asyncio.gather(*(refund(pool, i) for i in ids))
        finally:
            failpoints.disarm()

    assert run(db, scenario) == [billing.Refund(True, False)] * 2
    assert db.wallet(key) == (0, 0)


@mutation(B, "ORDER BY (scope <> '*'), scope FOR NO KEY UPDATE)", "ORDER BY (scope = '*'), scope FOR NO KEY UPDATE)",
          expect="deadlock detected")
def test_a_charge_locks_the_wallets_in_the_order_a_failed_jobs_refunds_take_them(db):
    """A failed job refunds its rows in charge order (points wallet, then cap) inside one transaction. A charge that
    took the cap first would wait for the points wallet that refund holds while the refund waits for the cap."""
    key = db.key(("p1", None, None))
    first, second = db.job(key), db.job(key)

    async def scenario(pool):
        ids = (await charge(pool, key, second, 3)).ledger_ids
        holding = asyncio.Event()

        async def refunds_both_rows():
            async with tx(pool) as conn:
                await billing.refund(conn, ids[0])
                holding.set()
                await asyncio.sleep(0.6)                 # the points wallet is held; the cap row comes after
                await billing.refund(conn, ids[1])

        async def charges():
            await holding.wait()
            await asyncio.sleep(0.2)
            return await charge(pool, key, first, 3)

        return (await asyncio.gather(refunds_both_rows(), charges()))[1]

    assert isinstance(run(db, scenario), billing.Charge)
    assert db.wallet(key) == (3, 3) and db.wallet(key, "p1") == (3, 3)


@mutation(B, "{_DAY_BASE} + %(points)s <= w.day_limit",
          "(SELECT x.used_day FROM key_wallets x WHERE x.key_id = w.key_id AND x.scope = w.scope) "
          "+ %(points)s <= w.day_limit", expect="assert 8 == 5")
def test_eight_concurrent_charges_never_pass_the_star_day_limit(db):
    key = db.key(("p1", None, None), star=(10, None))
    jobs = [db.job(key) for _ in range(8)]

    async def scenario(pool):
        barrier = asyncio.Barrier(8)

        async def meet():
            await barrier.wait()

        failpoints.arm("billing.charge.before_update", meet)
        try:
            return await asyncio.gather(*(charge(pool, key, j, 2) for j in jobs), return_exceptions=True)
        finally:
            failpoints.disarm()

    results = run(db, scenario)
    assert sum(isinstance(r, billing.Charge) for r in results) == 5, results
    assert all(isinstance(r, QuotaExceeded) for r in results if not isinstance(r, billing.Charge))
    assert db.wallet(key) == (10, 10) and db.wallet(key, "p1") == (10, 10), "the cap moved with the points wallet"


@mutation(B, "ON CONFLICT (refund_of) DO NOTHING RETURNING id", "ON CONFLICT DO NOTHING RETURNING id",
           expect="DID NOT RAISE UniqueViolation")
def test_a_unique_violation_other_than_the_refund_of_one_is_an_error_not_an_already_refunded_answer(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        cid = star_id(await charge(pool, key, job, 5))
        db.run("SELECT setval('ledger_id_seq', %s, false)", cid)       # the next ledger id is one that exists
        with pytest.raises(psycopg.errors.UniqueViolation):
            await refund(pool, cid)

    run(db, scenario)
    assert db.wallet(key) == (5, 5)
    assert ledger_rows(db, key) == [("charge", 5)]


def test_a_charge_in_a_pipeline_inside_tx_is_accepted_even_with_a_command_in_flight(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        async with tx(pool) as conn:
            async with conn.pipeline():
                await conn.execute("SELECT 1")                        # a command in flight: status ACTIVE
                await billing.charge(conn, key, "p1", 2, job)

    run(db, scenario)
    assert db.wallet(key) == (2, 2)
    assert ledger_rows(db, key) == [("charge", 2)]


def test_a_failed_transaction_reports_its_own_error_not_a_missing_transaction(db):
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        with pytest.raises(psycopg.errors.InFailedSqlTransaction):
            async with tx(pool) as conn:
                with pytest.raises(psycopg.errors.DivisionByZero):
                    await conn.execute("SELECT 1/0")
                await billing.charge(conn, key, "p1", 1, job)

    run(db, scenario)
    assert db.wallet(key) == (0, 0)


def test_a_failure_right_after_the_ledger_insert_leaves_no_charge_and_no_ledger_row(db):
    key = db.key(("p1", None, None))
    job = db.job(key)
    fired = []

    async def die():
        fired.append(db.one("SELECT 1")[0])                                # the point is reached after the insert
        raise failpoints.FailpointError("after the ledger insert")

    async def scenario(pool):
        failpoints.arm("billing.charge.after_ledger", die)
        try:
            with pytest.raises(failpoints.FailpointError):
                await charge(pool, key, job, 5)
        finally:
            failpoints.disarm()

    run(db, scenario)
    assert fired == [1]
    assert db.wallet(key) == (0, 0)
    assert ledger_rows(db, key) == []


@mutation(B, "return check_pin(row[0] if row else None, None)", "return (row[0] if row else None) or DEFAULT_TZ",
           expect="DID NOT RAISE BillingTzMismatch")
def test_billing_refuses_when_no_timezone_is_pinned_instead_of_assuming_utc(db):
    key = db.key(("p1", None, None))
    job = db.job(key)
    db.run("DELETE FROM billing_pin")

    async def scenario(pool):
        with pytest.raises(billing.BillingTzMismatch, match="relay migrate"):
            await charge(pool, key, job, 1)

    run(db, scenario)
    assert db.wallet(key) == (0, 0)
    assert ledger_rows(db, key) == []


@mutation(PO, "    if conn not in _open:\n", "    if conn.autocommit and conn not in _open:\n",
           expect="DID NOT RAISE NotInTransaction")
@pytest.mark.parametrize("shape", ["autocommit pipeline with a command in flight", "explicit block outside tx",
                                   "connection without autocommit"])
def test_a_charge_on_any_connection_outside_tx_is_refused_and_changes_nothing(db, shape):
    # a pipeline does not make autocommit statements one transaction (and reads ACTIVE with a command in flight);
    # an explicit block or a non-autocommit connection is atomic only as long as nobody else commits it. One
    # sanctioned path keeps the rule checkable: the connection of an open tx() block, nothing else.
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        if shape == "connection without autocommit":
            async with await psycopg.AsyncConnection.connect(db.dsn, options="-c search_path=relay") as conn:
                with pytest.raises(billing.NotInTransaction):
                    await billing.charge(conn, key, "p1", 3, job)
            return
        async with pool.connection() as conn:
            with pytest.raises(billing.NotInTransaction):
                if shape == "explicit block outside tx":
                    async with conn.transaction():
                        await billing.charge(conn, key, "p1", 3, job)
                else:
                    async with conn.pipeline():
                        await conn.execute("SELECT 1")
                        await billing.charge(conn, key, "p1", 3, job)

    run(db, scenario)
    assert db.wallet(key) == (0, 0)
    assert ledger_rows(db, key) == []


@mutation(PO, "    _open.discard(conn)\n", "    pass\n", expect="DID NOT RAISE NotInTransaction")
def test_a_connection_is_refused_once_its_tx_block_has_ended_even_in_a_task_started_inside_it(db):
    # a task created inside the block keeps a copy of its context; the connection goes back to the pool at the
    # end of the block and must not be accepted afterwards
    key = db.key(("p1", None, None))
    job = db.job(key)

    async def scenario(pool):
        release = asyncio.Event()

        async def late(conn):
            await release.wait()
            await billing.charge(conn, key, "p1", 3, job)

        async with tx(pool) as conn:
            task = asyncio.create_task(late(conn))
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(billing.NotInTransaction):
            await task

    run(db, scenario)
    assert db.wallet(key) == (0, 0)
    assert ledger_rows(db, key) == []
