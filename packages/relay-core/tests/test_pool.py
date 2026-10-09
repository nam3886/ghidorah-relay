"""Account pools (contracts 4.3, 5.3, 7.2; I6, I7): capacity and the day rollover, affordability, pool accounting by
`account_cost` with its once-only same-day refund, the drift check, and the account effects (rest and cooldown by
GREATEST, one strike counter, Dead and Revive around a disabled account, the Balance rules)."""

import asyncio
import time
import uuid
from contextvars import Context
from datetime import UTC, datetime, timedelta

import pytest

from relay_core import failpoints, pool
from relay_core.db import open_pool, tx
from relay_core.pool import PoolConfig
from relay_core.providers import (
    Balance,
    Caps,
    Cooldown,
    Dead,
    PoolSpec,
    ProxyBad,
    Rest,
    Revive,
    Success,
    Suspect,
)
from relay_core.testing.mutation import mutation
from relay_core.txguard import NotInTransaction

P = "packages/relay-core/src/relay_core/pool.py"
ACCOUNTS = "packages/relay-core/src/relay_core/db/accounts.py"
# the account lock made weaker than the capacity locks: it no longer conflicts with another writer
WEAK_ACCOUNT_LOCK = '    mode = "FOR UPDATE" if delete else "FOR NO KEY UPDATE"\n'
KEY_SHARE_ONLY = '    mode = "FOR UPDATE" if delete else "FOR KEY SHARE"\n'
WEST, EAST = "Etc/GMT+12", "Pacific/Kiritimati"       # UTC-12 and UTC+14: their calendar dates always differ
CFG = PoolConfig("video", "UTC", 4)
POOLS = {"video": CFG, "image": PoolConfig("image", "UTC", None)}
CAPS = Caps(execution="http", wallet_kinds=("video",), pools=(PoolSpec("video", "UTC", "x.b"),
            PoolSpec("image", "UTC")), account_slots=1, max_attempts=3, suspect_threshold=3,
            strike_effect=Dead("signed_out"))
FUTURE = datetime.now(UTC) + timedelta(hours=6)


def run(db, scenario, max_size=10):
    async def main():
        p = await open_pool(db.dsn, max_size=max_size)
        try:
            return await scenario(p)
        finally:
            await p.close()

    return asyncio.run(main())


def account(db, state="available") -> uuid.UUID:
    account_id = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state) VALUES (%s, 'p1', '\\x01', %s)",
           account_id, state)
    return account_id


def left(db, account_id, p="video"):
    return db.one("SELECT left_n, source FROM account_capacity WHERE account_id = %s AND pool = %s",
                  account_id, p)


async def locked(p, account_id, cfg=CFG):
    async with tx(p) as conn:
        return await pool.lock_capacity(conn, account_id, cfg)


async def charge(p, account_id, job, amount=2, cfg=CFG):
    async with tx(p) as conn:
        return await pool.charge_account(conn, account_id, job, cfg, amount)


async def refund(p, job, pools=None):
    async with tx(p) as conn:
        return await pool.refund_job(conn, job, pools or POOLS)


async def effects(p, account_id, *effs, caps=CAPS):
    async with tx(p) as conn:
        await pool.apply_effects(conn, account_id, effs, caps, POOLS)


def a_job(db):
    return db.job(db.key(("p1", "video", None, None)))


# ---- capacity and the day rollover -----------------------------------------------------------------------------
def test_a_first_lock_creates_the_row_from_the_budget_and_none_means_unknown(db):
    a = account(db)

    async def scenario(p):
        got = await locked(p, a)
        none = await locked(p, a, PoolConfig("image", "UTC", None))
        return got, none

    got, none = run(db, scenario)
    assert (got.left_n, got.source) == (4, "reset") and none.left_n is None
    assert left(db, a, "image") == (None, "reset")


@mutation(P, "    if row[-1]:\n", "    if False:\n", expect="assert (got.left_n, got.source) == (4, \"reset\")")
def test_a_new_pool_day_resets_left_n_to_the_budget_whatever_it_was(db):
    a = account(db)
    db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) "
           "VALUES (%s, 'video', 1, 'reported', current_date - 1)", a)

    got = run(db, lambda p: locked(p, a))
    assert (got.left_n, got.source) == (4, "reset")
    assert left(db, a) == (4, "reset")


def test_a_row_held_by_another_claim_is_skipped(db):
    a = account(db)

    async def scenario(p):
        async with tx(p) as holder:
            await pool.lock_capacity(holder, a, CFG)
            other = asyncio.create_task(locked(p, a), context=Context())    # another claim, not nested
            assert await other is None
        assert await locked(p, a) is not None

    run(db, scenario)


@mutation(P, 'return await _account(conn, account_id, "cooldown_until = NULL, cooldown_reason = NULL") == 1',
          "return True",
          expect="assert db.one(\"SELECT cooldown_until FROM accounts WHERE id = %s\", a) == (None,)")
def test_wake_reports_whether_the_account_exists_and_clears_cooldown_and_rests(db):
    a = account(db)
    db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '1 hour' WHERE id = %s", a)
    run(db, lambda p: effects(p, a, Rest("video", FUTURE, "out of credit")))

    async def wake(p, account_id):
        async with tx(p) as conn:
            return await pool.wake_account(conn, account_id)

    async def scenario(p):
        return await wake(p, a), await wake(p, uuid.uuid4())

    assert run(db, scenario) == (True, False)
    assert db.one("SELECT cooldown_until FROM accounts WHERE id = %s", a) == (None,)
    assert db.one("SELECT rest_until FROM account_capacity WHERE account_id = %s", a) == (None,)


def test_next_pool_reset_is_the_next_local_midnight():
    at = datetime(2030, 3, 1, 23, 30, tzinfo=UTC)
    assert pool.next_pool_reset(CFG, at) == datetime(2030, 3, 2, 0, 0, tzinfo=UTC)
    assert pool.next_pool_reset(PoolConfig("x", "Asia/Ho_Chi_Minh", 1), at) == datetime(2030, 3, 2, 17, 0, tzinfo=UTC)


# ---- affordability (I6) ---------------------------------------------------------------------------------------
@mutation(P, "return left_n is None or left_n - reserved_n >= cost", "return True",
          expect="assert pool.can_afford(left_n, reserved_n, cost) is ok")
@pytest.mark.parametrize("left_n, reserved_n, cost, ok", [
    (None, 99, 4, True), (4, 0, 4, True), (4, 1, 4, False), (2, 0, 4, False), (5, 1, 4, True), (0, 0, 0, True),
])
def test_can_afford_follows_i6(left_n, reserved_n, cost, ok):
    assert pool.can_afford(left_n, reserved_n, cost) is ok


def test_reserved_counts_uncharged_job_leases_of_the_pool_only(db):
    a = account(db)
    key = db.key(("p1", "video", None, None))
    jobs = [db.job(key) for _ in range(3)]
    for j, cost in zip(jobs, (2, 3, 5), strict=True):
        db.run("UPDATE jobs SET account_cost = %s, status = 'submitting' WHERE id = %s", cost, j)
        db.run("INSERT INTO leases (account_id, job_id, purpose, pool, owner, until, hard_until) VALUES "
               "(%s, %s, 'job', 'video', 'w', now() + interval '1 min', now() + interval '1 min')", a, j)

    async def scenario(p):
        async with tx(p) as conn:
            assert await pool.reserved(conn, a, "video") == 10
            assert await pool.reserved(conn, a, "image") == 0
            await pool.charge_account(conn, a, jobs[0], CFG, 2)      # charged: no longer reserved
            assert await pool.reserved(conn, a, "video") == 8

    run(db, scenario)
    db.run("DELETE FROM leases")


# ---- charge and refund (I7, D14) ------------------------------------------------------------------------------
def test_a_charge_counts_account_cost_once_per_job_and_a_reported_value_stays_reported(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        assert await charge(p, a, job, 2) is True
        assert left(db, a) == (2, "counted")
        assert await charge(p, a, job, 2) is False                       # the same job again: nothing moves
        db.run("UPDATE account_capacity SET source = 'reported', left_n = 9 WHERE account_id = %s "
               "AND pool = 'video'", a)
        assert await charge(p, a, a_job(db), 3) is True
        return await charge(p, a, a_job(db), 1, PoolConfig("image", "UTC", None))

    assert run(db, scenario) is True
    assert left(db, a) == (6, "reported")
    assert left(db, a, "image") == (None, "reset")                       # unknown stays unknown
    assert db.one("SELECT count(*) FROM account_charges WHERE account_id = %s", a)[0] == 3


@mutation(ACCOUNTS, WEAK_ACCOUNT_LOCK, KEY_SHARE_ONLY, expect="deadlock")
def test_a_charge_then_a_rest_of_another_pool_beside_a_check_does_not_deadlock(db):
    """The soak's seed-7 interleaving: a submit result charges the video pool, then rests the image pool, while a check
    takes the account row and then its capacity rows in pool order (image before video). The charge takes the account
    row first too, so the check waits at the account instead of meeting the submit between two capacity rows."""
    a, job = account(db), a_job(db)
    for name in ("image", "video"):
        db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) VALUES "
               "(%s, %s, 4, 'reset', (clock_timestamp() AT TIME ZONE 'UTC')::date)", a, name)

    cost = db.one("SELECT account_cost FROM jobs WHERE id = %s", job)[0]

    async def scenario(p):
        charged, go_on = asyncio.Event(), asyncio.Event()

        async def submit_result():
            async with tx(p) as conn:
                await pool.charge_account(conn, a, job, CFG, cost)
                charged.set()
                await go_on.wait()
                await pool.apply_effects(conn, a, [Rest("image", FUTURE, "out")], CAPS, POOLS)

        async def check():
            await charged.wait()
            async with tx(p) as conn:
                await conn.execute("SELECT 1 FROM accounts WHERE id = %s FOR NO KEY UPDATE", (a,))
                await conn.execute("SELECT 1 FROM account_capacity WHERE account_id = %s ORDER BY pool FOR UPDATE",
                                   (a,))

        tasks = [asyncio.create_task(submit_result()), asyncio.create_task(check())]
        await asyncio.sleep(1.0)                              # the check now waits on the account row
        go_on.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = run(db, scenario)
    assert not [r for r in results if isinstance(r, BaseException)], "deadlock: %r" % (results,)
    assert left(db, a, "video")[0] == 4 - cost


@mutation(ACCOUNTS, WEAK_ACCOUNT_LOCK, KEY_SHARE_ONLY, expect="deadlock detected")
def test_two_transactions_that_apply_effects_on_two_pools_with_no_rows_yet_do_not_deadlock(db):
    """Image then video against video then image on an account that has no capacity row. If each effect inserted its
    own pool's row, each transaction would hold the row the other inserts next. The account row is locked
    before the first insert, so the second transaction waits for the first instead."""
    a = account(db)
    both = (PoolConfig("image", "UTC", None), PoolConfig("video", "UTC", 4))

    async def scenario(p):
        async def apply(order):
            async with tx(p) as conn:
                for name in order:
                    await pool.apply_effects(conn, a, [Rest(name, FUTURE, "out")], CAPS, {c.name: c for c in both})
                    await asyncio.sleep(1.0)             # the other transaction has now done its first effect

        return await asyncio.gather(apply(("image", "video")), apply(("video", "image")), return_exceptions=True)

    results = run(db, scenario)
    assert not [r for r in results if isinstance(r, BaseException)], "deadlock detected: %r" % (results,)
    assert [db.one("SELECT rest_until IS NOT NULL FROM account_capacity WHERE account_id = %s AND pool = %s", a, n)
            for n in ("image", "video")] == [(True,), (True,)]


@mutation(P, "    if row is None:\n        return False\n    await conn.execute(\n"
             '        "UPDATE account_capacity SET left_n = left_n - %(n)s, "',
          "    if False:\n        return False\n    await conn.execute(\n"
          '        "UPDATE account_capacity SET left_n = left_n - %(n)s, "', expect="assert left(db, a)[0] == 2")
def test_charging_the_same_job_twice_decrements_once(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        await charge(p, a, job, 2)
        await charge(p, a, job, 2)

    run(db, scenario)
    assert left(db, a)[0] == 2


def test_money_calls_refuse_a_connection_outside_tx(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        async with p.connection() as conn:
            for call in (pool.charge_account(conn, a, job, CFG, 1), pool.refund_job(conn, job, POOLS)):
                with pytest.raises(NotInTransaction):
                    await call

    run(db, scenario)


def test_a_refund_restores_the_exact_amount_once_and_wakes_the_pool(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        await charge(p, a, job, 3)
        await effects(p, a, Rest("video", FUTURE, "out of credit"))
        assert await refund(p, job) == [("video", 3)]
        assert await refund(p, job) == []                                   # once

    run(db, scenario)
    assert left(db, a) == (4, "counted")
    assert db.one("SELECT rest_until FROM account_capacity WHERE account_id = %s", a)[0] is None
    assert db.one("SELECT refunded_at IS NOT NULL FROM account_charges WHERE account_id = %s", a)[0] is True


@mutation(P, '"WHERE id = %(id)s AND refunded_at IS NULL AND account_day = "', '"WHERE id = %(id)s AND account_day = "',
          expect="assert sum(len(r) for r in results) == 1")
def test_eight_concurrent_refunds_of_one_charge_restore_it_once(db):
    a, job = account(db), a_job(db)

    async def pause() -> None:
        await asyncio.sleep(1.0)           # every caller lists the charge before any of them commits

    async def scenario(p):
        await charge(p, a, job, 3)
        failpoints.arm("pool.refund.listed", pause)
        try:
            return await asyncio.gather(*[refund(p, job) for _ in range(8)])
        finally:
            failpoints.disarm("pool.refund.listed")

    results = run(db, scenario)
    assert sum(len(r) for r in results) == 1
    assert left(db, a)[0] == 4


@mutation(P, 'AND account_day = " + _TODAY + " RETURNING amount"', 'AND true " + " RETURNING amount"',
          expect="assert await refund(p, job, east) == []                              # EAST is a")
def test_a_refund_after_the_pool_day_rolled_credits_nothing(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        await charge(p, a, job, 3, PoolConfig("video", WEST, 4))             # charged on the WEST calendar day
        east = {"video": PoolConfig("video", EAST, 4)}
        assert await refund(p, job, east) == []                              # EAST is already the next day
        return left(db, a)

    assert run(db, scenario) == (4, "reset")        # the rollover reset it; no refund was added on top
    assert db.one("SELECT refunded_at FROM account_charges WHERE account_id = %s", a)[0] is None


def test_a_refund_of_a_job_with_no_charge_does_nothing(db):
    a = account(db)
    assert run(db, lambda p: refund(p, a_job(db))) == []
    assert db.one("SELECT count(*) FROM account_capacity WHERE account_id = %s", a)[0] == 0


# ---- drift (7.2) ----------------------------------------------------------------------------------------------
async def drift(p, provider="p1"):
    async with tx(p) as conn:
        return await pool.check_drift(conn, provider, POOLS)


def test_drift_corrects_a_counted_value_and_returns_what_it_found(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        await charge(p, a, job, 3)
        assert await drift(p) == []                                         # consistent: nothing to do
        db.run("UPDATE account_capacity SET left_n = 0 WHERE account_id = %s", a)
        return await drift(p)

    found = run(db, scenario)
    assert [(d.found, d.expected) for d in found] == [(0, 1)]
    assert left(db, a)[0] == 1


LOG_FORGET = "        del _logged_drift[key]"


@mutation(P, LOG_FORGET, "        pass",
          expect="assert old not in pool._logged_drift and fresh in pool._logged_drift")
def test_the_log_once_set_forgets_entries_older_than_a_pool_day(db):
    a = account(db)
    old, fresh = (a, "video", 98), (a, "video", 99)
    pool._logged_drift[old] = time.monotonic() - pool.DRIFT_LOG_TTL_S - 1
    pool._logged_drift[fresh] = time.monotonic()
    try:
        run(db, lambda p: locked(p, a))
        run(db, drift)
        assert old not in pool._logged_drift and fresh in pool._logged_drift
    finally:
        pool._logged_drift.pop(old, None)
        pool._logged_drift.pop(fresh, None)


@mutation(P, "c.source IN ('counted', 'reset')", "c.source IN ('counted', 'reset', 'reported')",
          expect="assert run(db, scenario) == []")
def test_drift_never_overwrites_a_reported_value(db):
    a = account(db)

    async def scenario(p):
        await locked(p, a)
        await effects(p, a, Balance("video", 7, "reported"))
        return await drift(p)

    assert run(db, scenario) == []
    assert left(db, a) == (7, "reported")


@mutation(P, "            if left_n == expected:\n", "            if False:\n", expect="assert run(db, drift) == []")
def test_drift_leaves_a_matching_row_alone(db):
    a = account(db)
    run(db, lambda p: locked(p, a))
    assert run(db, drift) == []


def test_drift_skips_pools_without_a_budget_and_other_providers(db):
    a = account(db)
    other = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p2', '\\x01')", other)
    run(db, lambda p: locked(p, a))
    run(db, lambda p: locked(p, other))
    db.run("UPDATE account_capacity SET left_n = 0")

    assert [d.account_id for d in run(db, drift)] == [a]                    # p1 only; the image pool has no budget
    assert left(db, other)[0] == 0


# ---- effects: rest and cooldown are GREATEST, never shortened -------------------------------------------------
@mutation(P, "rest_until = GREATEST(rest_until, %(u)s)", "rest_until = %(u)s",
          expect="assert abs((until - FUTURE).total_seconds()) < 1 and reason == \"out of credit\"")
def test_a_shorter_rest_never_shortens_a_longer_one(db):
    a = account(db)
    soon = datetime.now(UTC) + timedelta(seconds=30)

    async def scenario(p):
        await effects(p, a, Rest("video", FUTURE, "out of credit"))
        await effects(p, a, Rest("video", soon, "account gap"))

    run(db, scenario)
    until, reason = db.one("SELECT rest_until, rest_reason FROM account_capacity "
                           "WHERE account_id = %s AND pool = 'video'", a)
    assert abs((until - FUTURE).total_seconds()) < 1 and reason == "out of credit"


@mutation(P, "cooldown_until = GREATEST(cooldown_until, %(u)s)", "cooldown_until = %(u)s",
          expect="assert abs((until - FUTURE).total_seconds()) < 1 and reason == \"throttled\"")
def test_a_shorter_cooldown_never_shortens_a_longer_one(db):
    a = account(db)
    soon = datetime.now(UTC) + timedelta(seconds=30)

    async def scenario(p):
        await effects(p, a, Cooldown("throttled", FUTURE))
        await effects(p, a, Cooldown("proxy", soon))

    run(db, scenario)
    until, reason = db.one("SELECT cooldown_until, cooldown_reason FROM accounts WHERE id = %s", a)
    assert abs((until - FUTURE).total_seconds()) < 1 and reason == "throttled"


def test_a_longer_rest_or_cooldown_replaces_a_shorter_one(db):
    a = account(db)
    soon = datetime.now(UTC) + timedelta(seconds=30)

    async def scenario(p):
        await effects(p, a, Rest("video", soon, "gap"), Cooldown("proxy", soon))
        await effects(p, a, Rest("video", FUTURE, "out of credit"), Cooldown("throttled", FUTURE))

    run(db, scenario)
    assert db.one("SELECT rest_reason FROM account_capacity WHERE account_id = %s AND pool = 'video'",
                  a)[0] == "out of credit"
    assert db.one("SELECT cooldown_reason FROM accounts WHERE id = %s", a)[0] == "throttled"


def test_wake_clears_the_cooldown_and_every_rest(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, Cooldown("throttled", FUTURE), Rest("video", FUTURE, "x"), Rest("image", FUTURE, "y"))
        async with tx(p) as conn:
            await pool.wake_account(conn, a)

    run(db, scenario)
    assert db.one("SELECT cooldown_until FROM accounts WHERE id = %s", a)[0] is None
    assert db.all("SELECT rest_until FROM account_capacity WHERE account_id = %s", a) == [(None,), (None,)]


# ---- effects: strikes, Dead, Revive ----------------------------------------------------------------------------
def state(db, a):
    return db.one("SELECT state, state_reason, strikes FROM accounts WHERE id = %s", a)


@mutation(P, "            if strikes >= caps.suspect_threshold:\n", "            if False:\n",
          expect="assert state(db, a) == (\"dead\", \"signed_out\", 3)")
def test_the_threshold_strike_kills_the_account_with_the_strike_effects_reason(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, Suspect("not sure"), Suspect("again"))
        assert state(db, a) == ("available", None, 2)
        await effects(p, a, Suspect("third"))

    run(db, scenario)
    assert state(db, a) == ("dead", "signed_out", 3)


@mutation(P, '"strikes = 0, last_error = NULL"', '"last_error = NULL"',
          expect="assert state(db, a) == (\"available\", None, 1)")
def test_success_clears_the_strike_counter(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, Suspect("x"), Suspect("y"), Success())
        await effects(p, a, Suspect("z"))

    run(db, scenario)
    assert state(db, a) == ("available", None, 1)


@mutation(P, """where="AND state <> 'disabled'",""", "where='',", expect="assert state(db, a)[0] == \"disabled\"")
def test_dead_and_the_strike_threshold_never_change_a_disabled_account(db):
    a = account(db, "disabled")

    async def scenario(p):
        await effects(p, a, Dead("signed out"))
        await effects(p, a, Suspect("a"), Suspect("b"), Suspect("c"))

    run(db, scenario)
    assert state(db, a)[0] == "disabled"


@mutation(P, """where="AND state = 'dead'")""", "where='')", expect="assert state(db, off)[0] == \"disabled\"")
def test_revive_wakes_a_dead_account_and_is_ignored_for_a_disabled_one(db):
    dead, off = account(db, "dead"), account(db, "disabled")
    run(db, lambda p: effects(p, dead, Revive("alive")))
    run(db, lambda p: effects(p, off, Revive("alive")))
    assert state(db, dead)[:2] == ("available", None)
    assert state(db, off)[0] == "disabled"


@mutation(P, "return redact_log(text)[:200]", "return text[:200]",
          expect="assert state(db, a)[1] == \"[omitted len=2500]\"")
def test_a_long_reason_is_cut_and_redacted_before_it_is_stored(db):
    a = account(db)
    run(db, lambda p: effects(p, a, Dead("x" * 2500)))
    assert state(db, a)[1] == "[omitted len=2500]"
    assert db.one("SELECT last_error FROM accounts WHERE id = %s", a)[0] == "[omitted len=2500]"


def test_a_bad_proxy_is_recorded(db):
    a = account(db)
    run(db, lambda p: effects(p, a, ProxyBad("tunnel failed")))
    ok, checked, err = db.one("SELECT proxy_ok, proxy_checked_at, last_error FROM accounts WHERE id = %s", a)
    assert ok is False and checked is not None and err == "tunnel failed"


# ---- effects: Balance -------------------------------------------------------------------------------------------
@mutation(P, "rest_until = CASE WHEN %(s)s = 'reported' AND %(n)s > 0 THEN NULL ELSE rest_until END",
          "rest_until = rest_until",
          expect="assert db.one(\"SELECT rest_until FROM account_capacity WHERE account_id = %s AND")
def test_a_reported_positive_balance_wakes_a_resting_pool_and_a_zero_one_does_not(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, Rest("video", FUTURE, "out of credit"), Balance("video", 0))
        assert db.one("SELECT rest_until IS NOT NULL FROM account_capacity "
                      "WHERE account_id = %s AND pool = 'video'", a)[0]
        await effects(p, a, Balance("video", 5))

    run(db, scenario)
    assert left(db, a) == (5, "reported")
    assert db.one("SELECT rest_until FROM account_capacity WHERE account_id = %s AND pool = 'video'", a)[0] is None


def test_a_counted_balance_does_not_wake_a_rest_and_a_reported_one_overrides_a_counted_one(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        await charge(p, a, job, 3)                                           # counted: 1 left
        await effects(p, a, Rest("video", FUTURE, "out"), Balance("video", 2, "counted"))
        assert db.one("SELECT rest_until IS NOT NULL FROM account_capacity "
                      "WHERE account_id = %s AND pool = 'video'", a)[0]
        await effects(p, a, Balance("video", 6, "reported"))

    run(db, scenario)
    assert left(db, a) == (6, "reported")


def test_effects_are_applied_pool_first_and_an_unknown_pool_or_effect_is_refused(db):
    a = account(db)

    async def scenario(p):
        with pytest.raises(ValueError, match="nope"):
            await effects(p, a, Rest("nope", FUTURE, "x"))
        with pytest.raises(TypeError):
            await effects(p, a, object())

    run(db, scenario)
