"""The invariant checker: ledger = counters, one refund per charge, run after every DB test."""

import asyncio

from relay_core import billing
from relay_core.db import open_pool, tx
from relay_core.testing.checker import violations
from relay_core.testing.mutation import mutation

CHECKER = "packages/relay-core/src/relay_core/testing/checker.py"


def charge_once(db, key, points=7, job=None):
    job = job or db.job(key)

    async def scenario():
        pool = await open_pool(db.dsn, max_size=2)
        try:
            async with tx(pool) as conn:
                return (await billing.charge(conn, key, "p1", points, job)).ledger_ids[0]
        finally:
            await pool.close()

    return asyncio.run(scenario())


def check(db):
    with db.connect() as c:
        return violations(c)


def test_a_consistent_ledger_passes(db):
    charge_once(db, db.key(("p1", None, None)))
    assert check(db) == []


@mutation(CHECKER, "GROUP BY j.id, j.price, l.scope) s ", "GROUP BY j.id, j.price) s ", expect="I8 job")
def test_a_job_charged_on_the_points_wallet_and_its_cap_is_not_counted_twice(db):
    key = db.key(("p1", None, None))
    charge_once(db, key, 7, db.job(key, price=7))
    assert check(db) == []


@mutation(CHECKER, " OR count(DISTINCT net) > 1", "", expect="['I8 job")
def test_a_job_refunded_on_its_cap_but_not_on_its_points_wallet_is_reported(db):
    key = db.key(("p1", None, None))
    job = db.job(key, price=7)
    charge_once(db, key, 7, job)
    db.one("INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day, refund_of) "
           "SELECT key_id, scope, job_id, provider, 'refund', amount, billing_day, id FROM ledger "
           "WHERE job_id = %s AND scope = 'p1' AND entry = 'charge' RETURNING 1", job)
    db.run("UPDATE key_wallets SET used_total = 0, used_day = 0 WHERE key_id = %s AND scope = 'p1'", key)
    found = check(db)
    db.reset()
    assert any(line.startswith("I8 job") for line in found), found


@mutation(CHECKER, "if used_total != ledger_total:", "if False:", expect="len(found) == 1")
def test_a_total_that_drifted_from_the_ledger_is_reported(db):
    key = db.key(("p1", None, None))
    charge_once(db, key)
    db.one("UPDATE key_wallets SET used_total = used_total + 1 WHERE key_id = %s AND scope = '*' RETURNING 1", key)
    found = check(db)
    db.reset()                                     # the autouse checker must not see the planted drift
    assert len(found) == 1 and "I25" in found[0] and "used_total" in found[0], found


@mutation(CHECKER, "if used_day != ledger_day:", "if False:", expect="len(found) == 1")
def test_a_day_counter_that_drifted_from_the_ledger_is_reported(db):
    key = db.key(("p1", None, None))
    charge_once(db, key)
    db.one("UPDATE key_wallets SET used_day = 0 WHERE key_id = %s AND scope = 'p1' RETURNING 1", key)
    found = check(db)
    db.reset()
    assert len(found) == 1 and "used_day" in found[0], found


@mutation(CHECKER, "for refund_id, charge_id in conn.execute(_BAD_REFUNDS):", "for refund_id, charge_id in ():",
           expect="['I25 wallet")
def test_a_refund_that_does_not_undo_its_charge_is_reported(db):
    key = db.key(("p1", None, None))
    charge_id = charge_once(db, key, 7)
    db.one("INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day, refund_of) "
           "SELECT key_id, scope, job_id, provider, 'refund', 3, billing_day, id FROM ledger WHERE id = %s "
           "RETURNING 1", charge_id)
    found = check(db)
    db.reset()
    assert any(line.startswith("I4 refund row") for line in found), found


def test_the_checker_runs_after_every_database_test_without_being_asked(request, db):
    assert "_after_each_db_test" in request.fixturenames
