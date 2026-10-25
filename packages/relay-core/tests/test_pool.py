"""Account pools (I6, I7): the window arithmetic (pure), capacity read from the charges, affordability, pool accounting
by `account_cost` with its once-only refund, the reported balance that wins until its own refill, the `<p>.pools`
setting, and the account effects (rest and cooldown by GREATEST, one strike counter, Dead and Revive around a disabled
account, the Balance rules)."""

import asyncio
import uuid
from contextvars import Context
from datetime import UTC, date, datetime, timedelta

import pytest

from relay_core import failpoints, pool
from relay_core.db import open_pool, tx
from relay_core.pool import PoolConfig, Reported, Window, day_pool
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
    Tier,
)
from relay_core.settings import SettingsError
from relay_core.testing.mutation import mutation
from relay_core.txguard import NotInTransaction

P = "packages/relay-core/src/relay_core/pool.py"
ACCOUNTS = "packages/relay-core/src/relay_core/db/accounts.py"
# the account lock made weaker than the pool-row locks: it no longer conflicts with another writer
WEAK_ACCOUNT_LOCK = '    mode = "FOR UPDATE" if delete else "FOR NO KEY UPDATE"\n'
KEY_SHARE_ONLY = '    mode = "FOR UPDATE" if delete else "FOR KEY SHARE"\n'
UTC_DAY = Window("day", "day", "UTC", {"free": 4, "pro": 10})
SAIGON_DAY = Window("day", "day", "Asia/Ho_Chi_Minh", {"free": 10})       # its midnight is 17:00 UTC
CFG = PoolConfig("video", "jobs", "sum", (UTC_DAY,))
SAIGON = PoolConfig("video", "jobs", "sum", (SAIGON_DAY,))
NO_WINDOW = PoolConfig("image", "jobs")
POOLS = {"video": CFG, "image": NO_WINDOW}
CAPS = Caps(execution="http", pools=(PoolSpec("video", "jobs"), PoolSpec("image", "jobs")), account_slots=1,
            max_attempts=3, suspect_threshold=3, strike_effect=Dead("signed_out"))
FUTURE = datetime.now(UTC) + timedelta(hours=6)
NOON = datetime(2030, 3, 5, 12, 0, tzinfo=UTC)
HOUR, MINUTE = timedelta(hours=1), timedelta(minutes=1)


def run(db, scenario, max_size=10):
    async def main():
        p = await open_pool(db.dsn, max_size=max_size)
        try:
            return await scenario(p)
        finally:
            await p.close()

    return asyncio.run(main())


def account(db, state="available", tier=None) -> uuid.UUID:
    account_id = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state, tier) VALUES (%s, 'p1', '\\x01', %s, %s)",
           account_id, state, tier)
    return account_id


def report(db, account_id, left, *, ago_s=0.0, refill_in_s=None, pool_name="video"):
    """A balance the upstream reported `ago_s` seconds ago (a refill `refill_in_s` seconds from now, or none)."""
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at, refill_at) "
           "VALUES (%s, %s, %s, clock_timestamp() - make_interval(secs => %s), "
           "CASE WHEN %s::float8 IS NULL THEN NULL ELSE clock_timestamp() + make_interval(secs => %s::float8) END) "
           "ON CONFLICT (account_id, pool) DO UPDATE SET reported_left = excluded.reported_left, "
           "reported_at = excluded.reported_at, refill_at = excluded.refill_at",
           account_id, pool_name, left, ago_s, refill_in_s, refill_in_s)


def pool_row(db, account_id, pool_name="video"):
    return db.one("SELECT reported_left, rest_until, rest_reason FROM account_pools "
                  "WHERE account_id = %s AND pool = %s", account_id, pool_name)


async def locked(p, account_id, cfg=CFG):
    async with tx(p) as conn:
        return await pool.lock_capacity(conn, account_id, cfg)


def left(db, account_id, cfg=CFG):
    """What the next claim sees as left in the pool (None = unknown)."""
    return run(db, lambda p: locked(p, account_id, cfg)).left_n


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
    return db.job(db.key(("p1", None, None)))


# ---- the window arithmetic (pure) ------------------------------------------------------------------------------
def test_a_day_window_starts_and_ends_at_the_local_midnight():
    at = datetime(2030, 3, 1, 23, 30, tzinfo=UTC)
    assert pool.window_start(UTC_DAY, at) == datetime(2030, 3, 1, 0, 0, tzinfo=UTC)
    assert pool.window_end(UTC_DAY, at) == datetime(2030, 3, 2, 0, 0, tzinfo=UTC)
    assert pool.window_start(SAIGON_DAY, at) == datetime(2030, 3, 1, 17, 0, tzinfo=UTC)    # 06:30 on 2 March there
    assert pool.window_end(SAIGON_DAY, at) == datetime(2030, 3, 2, 17, 0, tzinfo=UTC)


@mutation(P, "    return min(ends) if ends else window_end(Window(\"none\", \"day\", NO_WINDOW_REFILL_TZ, {}), now)\n",
          "    return min(ends) if ends else now\n", expect="NO_WINDOW")
def test_the_next_refill_is_the_next_window_boundary_and_the_next_utc_midnight_without_a_window():
    at = datetime(2030, 3, 1, 23, 30, tzinfo=UTC)
    assert pool.next_refill(CFG, at) == datetime(2030, 3, 2, 0, 0, tzinfo=UTC)
    assert pool.next_refill(SAIGON, at) == datetime(2030, 3, 2, 17, 0, tzinfo=UTC)
    assert pool.next_refill(NO_WINDOW, at) == datetime(2030, 3, 2, 0, 0, tzinfo=UTC), "NO_WINDOW: UTC midnight"


def test_the_ceiling_is_the_largest_budget_of_any_tier():
    assert pool.ceiling(CFG) == 10 and pool.ceiling(SAIGON) == 10 and pool.ceiling(NO_WINDOW) is None
    assert pool.ceiling(PoolConfig("v", "jobs", "sum", (Window("day", "day", "UTC", {}),))) is None


def test_left_is_the_budget_of_the_tier_less_what_was_spent_in_the_window():
    charges = [(NOON - HOUR, 2), (NOON - 30 * HOUR, 5)]          # the second one is from yesterday
    free = pool.standing(CFG, "free", charges, NOON)
    assert (free.left, free.source) == (2, "computed")
    assert (free.windows[0].budget, free.windows[0].spent) == (4, 2)
    assert pool.left_of(CFG, "pro", charges, NOON) == 8
    assert pool.left_of(CFG, None, charges, NOON) == 2, "no tier is a free account"
    assert pool.left_of(CFG, "free", [], NOON) == 4


def test_a_tier_with_no_budget_and_a_pool_with_no_window_are_unknown_and_unknown_allows():
    assert pool.standing(CFG, "ultra", [(NOON, 3)], NOON).source == "unknown"
    assert pool.left_of(CFG, "ultra", [], NOON) is None
    assert pool.left_of(NO_WINDOW, "free", [(NOON, 3)], NOON) is None
    assert pool.can_afford(None, 0, 99)


@mutation(P, "    if reported is not None and now < _refill_of_report(cfg, reported):\n",
          "    if reported is not None:\n",
          expect="assert pool.left_of(CFG, \"free\", [], NOON + 13 * HOUR, report) == 4")
@mutation(P, "    if reported is not None and now < _refill_of_report(cfg, reported):\n",
          "    if False:\n", expect="assert (same_day.left, same_day.source) == (0, \"reported\")")
def test_a_reported_balance_wins_until_the_pools_next_refill_and_then_the_budget_applies():
    report = Reported(1, NOON)
    # the charge made before the report is already in it; the one after is not
    same_day = pool.standing(CFG, "free", [(NOON - HOUR, 2), (NOON + 10 * MINUTE, 1)], NOON + 30 * MINUTE, report)
    assert (same_day.left, same_day.source) == (0, "reported")
    assert pool.left_of(CFG, "free", [], NOON + 13 * HOUR, report) == 4     # past the next midnight: the budget again
    assert pool.left_of(CFG, "free", [], NOON + 13 * HOUR, None) == 4


@mutation(P, "if at >= window_start(w, now)", "if True", expect="assert state.spent == 1")
def test_a_window_counts_only_the_charges_since_its_own_start_even_when_an_older_report_loaded_more():
    report = Reported(5, NOON - 30 * HOUR)                   # read yesterday: the older charges load too
    charges = [(NOON - 26 * HOUR, 2), (NOON - HOUR, 1)]      # one yesterday, one today
    state = pool.standing(CFG, "free", charges, NOON, report).windows[0]
    assert state.spent == 1, "the window of today holds today's charge only"


@mutation(P, "    if reported.refill_at is not None:\n        return reported.refill_at\n",
          "    if False:\n        return reported.refill_at\n",
          expect="assert pool.left_of(SAIGON, \"free\", [], at + 17 * HOUR + 30 * MINUTE, named) == 3")
def test_a_reported_balance_survives_utc_midnight_until_its_own_refill():
    # a pool whose day is Saigon's (it refills at 17:00 UTC): a balance read at 23:59 UTC must not be replaced by the
    # budget at UTC midnight (the old code rolled every pool at the midnight of its zone and dropped the report)
    at = datetime(2030, 3, 5, 23, 59, tzinfo=UTC)
    reported = Reported(3, at)
    for now in (at + MINUTE, at + 30 * MINUTE, at + 16 * HOUR):
        assert pool.left_of(SAIGON, "free", [], now, reported) == 3, now
    assert pool.left_of(SAIGON, "free", [], at + 17 * HOUR + MINUTE, reported) == 10, "its own refill replaces it"
    # a refill the upstream named wins over the window boundary
    named = Reported(3, at, refill_at=datetime(2030, 3, 6, 20, 0, tzinfo=UTC))
    assert pool.left_of(SAIGON, "free", [], at + 17 * HOUR + 30 * MINUTE, named) == 3
    assert pool.left_of(SAIGON, "free", [], datetime(2030, 3, 6, 20, 1, tzinfo=UTC), named) == 10


def test_a_pool_with_no_window_lets_a_reported_balance_stand_until_the_next_utc_midnight():
    reported = Reported(0, NOON)
    assert pool.left_of(NO_WINDOW, "free", [], NOON + 11 * HOUR, reported) == 0
    assert pool.left_of(NO_WINDOW, "free", [], NOON + 13 * HOUR, reported) is None


# ---- the `<p>.pools` setting ---------------------------------------------------------------------------------------
SPECS = (PoolSpec("video", "credits"), PoolSpec("image", "jobs"))
DEFAULTS = {"video": day_pool({"free": 4}), "image": {"combine": "sum", "windows": []}}


def setting():
    return pool.pools_setting("p1", SPECS, DEFAULTS)


def test_the_pools_setting_reads_into_one_config_per_declared_pool():
    spec = setting()
    assert spec.key == "p1.pools" and spec.validate(spec.default) == DEFAULTS
    cfgs = pool.pool_configs("p1", Caps(execution="http", pools=SPECS, account_slots=1, max_attempts=3,
                                        suspect_threshold=3, strike_effect=Dead("x")), {"p1.pools": DEFAULTS}.get)
    assert cfgs == {"video": PoolConfig("video", "credits", "sum", (Window("day", "day", "UTC", {"free": 4}),)),
                    "image": PoolConfig("image", "jobs", "sum", ())}


@pytest.mark.parametrize("bad, why", [
    ({"nope": day_pool({"free": 1})}, "not a pool of this provider"),
    ({"video": {"combine": "both", "windows": []}}, "combine must be one of"),
    ({"video": {"combine": "sum", "windows": "x"}}, "windows is a list"),
    ({"video": {"windows": [day_pool({"free": 1})["windows"][0]] * 2}}, "one window per pool"),
    ({"video": {"windows": [{"name": "d", "kind": "month", "tz": "UTC", "budget": {}}]}}, "kind must be one of day"),
    ({"video": {"windows": [{"name": "d", "kind": "day", "tz": "Nowhere/Land", "budget": {}}]}}, "IANA time zone"),
    ({"video": {"windows": [{"name": "d", "kind": "day", "tz": "UTC", "budget": {"gold": 1}}]}}, "is not one of"),
    ({"video": {"windows": [{"name": "d", "kind": "day", "tz": "UTC", "budget": {"free": -1}}]}}, "whole number"),
    ({"video": {"windows": [{"name": "d", "kind": "day", "tz": "UTC", "budget": {"free": 1.5}}]}}, "whole number"),
    ({"video": {"windows": [{"name": "", "kind": "day", "tz": "UTC", "budget": {}}]}}, "name must be"),
    ({"video": {"windows": [{"name": "d", "kind": "day", "tz": "UTC", "budget": {}, "extra": 1}]}}, "unknown fields"),
])
def test_a_pools_value_that_is_not_a_rule_is_refused_naming_the_fault(bad, why):
    with pytest.raises(SettingsError, match=why):
        setting().validate(bad)


def test_a_stored_pools_value_is_read_over_the_defaults_and_drops_a_pool_that_is_gone():
    spec = setting()
    stored = {"video": day_pool({"free": 7}), "retired": day_pool({"free": 1})}
    assert spec.on_load(stored) == {"video": day_pool({"free": 7}), "image": DEFAULTS["image"]}
    assert spec.on_load("garbage") == "garbage"                  # `validate` refuses it, the default then applies


# ---- capacity read from the charges -----------------------------------------------------------------------------
def test_a_first_lock_creates_the_row_and_the_budget_is_what_is_left_and_none_means_unknown(db):
    a = account(db)

    async def scenario(p):
        return await locked(p, a), await locked(p, a, NO_WINDOW)

    got, none = run(db, scenario)
    assert got.left_n == 4 and none.left_n is None
    assert [r[0] for r in db.all("SELECT pool FROM account_pools WHERE account_id = %s ORDER BY pool", a)] == [
        "image", "video"]


def test_left_is_the_day_budget_minus_unrefunded_charges_of_the_day(db):
    a, one, two, old = account(db), a_job(db), a_job(db), a_job(db)

    async def scenario(p):
        await charge(p, a, one, 1)
        await charge(p, a, two, 2)
        await charge(p, a, old, 3)
        db.run("UPDATE account_charges SET at = clock_timestamp() - interval '2 days' WHERE job_id = %s", old)
        assert (await locked(p, a)).left_n == 1, "yesterday's charge is not today's spend"
        await refund(p, two)
        return (await locked(p, a)).left_n

    assert run(db, scenario) == 3, "a refunded charge is no longer spent"
    assert left(db, account(db, tier="pro")) == 10, "a pro account has the pro budget"
    assert left(db, account(db, tier="ultra")) is None, "no budget for a tier is unknown"


def test_a_reported_balance_wins_over_the_budget_until_its_refill_and_a_charge_after_it_is_taken_off(db):
    a, job = account(db), a_job(db)
    report(db, a, 9, refill_in_s=3600)
    assert left(db, a) == 9, "the report, not the budget of 4"

    async def scenario(p):
        await charge(p, a, job, 2)                             # made after the report: taken off it
        return (await locked(p, a)).left_n

    assert run(db, scenario) == 7


def test_a_reported_balance_read_before_utc_midnight_is_not_rolled_back_to_the_budget_at_midnight(db):
    # reported 30 h ago, its refill still ahead: the old code reset it when the UTC day changed
    a = account(db)
    report(db, a, 1, ago_s=30 * 3600, refill_in_s=3600)
    assert left(db, a) == 1
    report(db, a, 1, ago_s=30 * 3600, refill_in_s=-60)         # its refill has passed: the budget applies
    assert left(db, a) == 4


def test_a_row_held_by_another_claim_is_skipped(db):
    a = account(db)

    async def scenario(p):
        async with tx(p) as holder:
            await pool.lock_capacity(holder, a, CFG)
            other = asyncio.create_task(locked(p, a), context=Context())    # another claim, not nested
            assert await other is None
        assert await locked(p, a) is not None

    run(db, scenario)


WAKE_RETURN = ('return await _account(conn, account_id,\n'
               '                          "cooldown_until = NULL, cooldown_reason = NULL, '
               'strikes = 0, strike_at = NULL") == 1')


@mutation(P, WAKE_RETURN, "return True",
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
    assert db.one("SELECT rest_until FROM account_pools WHERE account_id = %s", a) == (None,)


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
    key = db.key(("p1", None, None))
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


# ---- charge and refund (I7) ------------------------------------------------------------------------------
@mutation(P, '"ON CONFLICT (job_id, pool) DO NOTHING RETURNING id"',
          '"ON CONFLICT (job_id, pool) DO UPDATE SET amount = EXCLUDED.amount RETURNING id"',
          expect="assert await charge(p, a, job, 2) is False")
def test_a_charge_inserts_one_row_per_job_and_pool_and_a_second_try_changes_nothing(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        assert await charge(p, a, job, 2) is True
        assert (await locked(p, a)).left_n == 2
        assert await charge(p, a, job, 2) is False                       # the same job again: nothing moves
        assert await charge(p, a, job, 9) is False                       # not even with another amount
        assert await charge(p, a, a_job(db), 1) is True
        return await charge(p, a, a_job(db), 1, NO_WINDOW)               # another pool of the same account

    assert run(db, scenario) is True
    assert db.all("SELECT pool, amount FROM account_charges WHERE account_id = %s ORDER BY id", a) == [
        ("video", 2), ("video", 1), ("image", 1)]
    assert left(db, a) == 1 and left(db, a, NO_WINDOW) is None           # unknown stays unknown


@mutation(ACCOUNTS, WEAK_ACCOUNT_LOCK, KEY_SHARE_ONLY, expect="deadlock")
def test_a_charge_then_a_rest_of_another_pool_beside_a_check_does_not_deadlock(db):
    """The soak's seed-7 interleaving: a submit result charges the video pool, then rests the image pool, while a check
    takes the account row and then its pool rows in pool order (image before video). The charge takes the account row
    first too, so the check waits at the account instead of meeting the submit between two pool rows."""
    a, job = account(db), a_job(db)
    for name in ("image", "video"):
        db.run("INSERT INTO account_pools (account_id, pool) VALUES (%s, %s)", a, name)

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
                await conn.execute("SELECT 1 FROM account_pools WHERE account_id = %s ORDER BY pool FOR UPDATE",
                                   (a,))

        tasks = [asyncio.create_task(submit_result()), asyncio.create_task(check())]
        await asyncio.sleep(1.0)                              # the check now waits on the account row
        go_on.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = run(db, scenario)
    assert not [r for r in results if isinstance(r, BaseException)], "deadlock: %r" % (results,)
    assert left(db, a) == 4 - cost


@mutation(ACCOUNTS, WEAK_ACCOUNT_LOCK, KEY_SHARE_ONLY, expect="deadlock detected")
def test_two_transactions_that_apply_effects_on_two_pools_with_no_rows_yet_do_not_deadlock(db):
    """Image then video against video then image on an account that has no pool row. If each effect inserted its own
    pool's row, each transaction would hold the row the other inserts next. The account row is locked before the first
    insert, so the second transaction waits for the first instead."""
    a = account(db)
    both = {"image": NO_WINDOW, "video": CFG}

    async def scenario(p):
        async def apply(order):
            async with tx(p) as conn:
                for name in order:
                    await pool.apply_effects(conn, a, [Rest(name, FUTURE, "out")], CAPS, both)
                    await asyncio.sleep(1.0)             # the other transaction has now done its first effect

        return await asyncio.gather(apply(("image", "video")), apply(("video", "image")), return_exceptions=True)

    results = run(db, scenario)
    assert not [r for r in results if isinstance(r, BaseException)], "deadlock detected: %r" % (results,)
    assert [db.one("SELECT rest_until IS NOT NULL FROM account_pools WHERE account_id = %s AND pool = %s", a, n)
            for n in ("image", "video")] == [(True,), (True,)]


def test_money_calls_refuse_a_connection_outside_tx(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        async with p.connection() as conn:
            for call in (pool.charge_account(conn, a, job, CFG, 1), pool.refund_job(conn, job, POOLS)):
                with pytest.raises(NotInTransaction):
                    await call

    run(db, scenario)


def test_a_refund_marks_the_charge_refunded_gives_back_the_exact_amount_once(db):
    a, job = account(db), a_job(db)

    async def scenario(p):
        await charge(p, a, job, 3)
        assert (await locked(p, a)).left_n == 1
        assert await refund(p, job) == [("video", 3)]
        assert await refund(p, job) == []                                   # once
        return (await locked(p, a)).left_n

    assert run(db, scenario) == 4
    assert db.one("SELECT refunded_at IS NOT NULL FROM account_charges WHERE account_id = %s", a)[0] is True


@mutation(P, '"UPDATE account_charges SET refunded_at = clock_timestamp() WHERE id = %s AND refunded_at IS NULL "',
          '"UPDATE account_charges SET refunded_at = clock_timestamp() WHERE id = %s "',
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
    assert left(db, a) == 4


def test_a_refund_of_yesterdays_charge_changes_nothing_today(db):
    a, old, today = account(db), a_job(db), a_job(db)

    async def scenario(p):
        await charge(p, a, old, 3)
        await charge(p, a, today, 1)
        db.run("UPDATE account_charges SET at = clock_timestamp() - interval '2 days' WHERE job_id = %s", old)
        before = (await locked(p, a)).left_n
        assert await refund(p, old) == [("video", 3)]
        return before, (await locked(p, a)).left_n

    assert run(db, scenario) == (3, 3), "today's spend is the one charge of today, refunded or not"


def test_a_refund_of_a_job_with_no_charge_does_nothing(db):
    a = account(db)
    assert run(db, lambda p: refund(p, a_job(db))) == []
    assert db.one("SELECT count(*) FROM account_pools WHERE account_id = %s", a)[0] == 0


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
    until, reason = db.one("SELECT rest_until, rest_reason FROM account_pools "
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
    assert db.one("SELECT rest_reason FROM account_pools WHERE account_id = %s AND pool = 'video'",
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
    assert db.all("SELECT rest_until FROM account_pools WHERE account_id = %s", a) == [(None,), (None,)]


@mutation(P, '"last_error = CASE WHEN %(s)s THEN last_error ELSE %(r)s END"', '"last_error = %(r)s"',
          expect="assert last_error(\"not signed in (RotateCookies answered 401)\")")
def test_a_strike_keeps_its_reason_over_the_cooldown_that_comes_with_it(db):
    a = account(db)

    def last_error(want):
        return db.one("SELECT last_error FROM accounts WHERE id = %s", a)[0] == want

    async def scenario(p):
        await effects(p, a, Suspect("not signed in (RotateCookies answered 401)"),
                      Cooldown("signed-out read, to be checked again", FUTURE, recheck=True))
        assert last_error("not signed in (RotateCookies answered 401)")
        await effects(p, a, Cooldown("throttled", FUTURE))    # a cooldown alone still writes its reason

    run(db, scenario)
    assert last_error("throttled")


@mutation(P, "any(isinstance(e, (Suspect, ProxyBad)) for e in effects)", "any(isinstance(e, Suspect) for e in effects)",
          expect="assert db.one(\"SELECT last_error FROM accounts WHERE id = %s\", a)[0] == \"region blocked: CN\"")
def test_a_bad_proxy_keeps_its_reason_over_the_cooldown_that_comes_with_it(db):
    a = account(db)
    run(db, lambda p: effects(p, a, ProxyBad("region blocked: CN"), Cooldown("region blocked", FUTURE)))
    assert db.one("SELECT last_error FROM accounts WHERE id = %s", a)[0] == "region blocked: CN"


# ---- effects: strikes, Dead, Revive ----------------------------------------------------------------------------
def state(db, a):
    return db.one("SELECT state, state_reason, strikes FROM accounts WHERE id = %s", a)


@mutation(P, "            if counted and strikes >= caps.suspect_threshold:\n", "            if False:\n",
          expect="assert state(db, a) == (\"dead\", \"signed_out\", 3)")
def test_the_threshold_strike_kills_the_account_with_the_strike_effects_reason(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, Suspect("not sure"), Suspect("again"))
        assert state(db, a) == ("available", None, 2)
        await effects(p, a, Suspect("third"))

    run(db, scenario)
    assert state(db, a) == ("dead", "signed_out", 3)


@mutation(P, '"strikes = 0, strike_at = NULL, last_error = NULL"', '"last_error = NULL"',
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
@mutation(P, "                until = e.refill_at or next_refill(cfg, row[6])\n",
          "                until = next_refill(cfg, row[6])\n", expect="assert until == named")
def test_a_zero_balance_rests_the_pool_until_the_next_refill(db):
    a, b, c = account(db), account(db), account(db)
    named = datetime.now(UTC) + timedelta(hours=3, minutes=7)

    async def scenario(p):
        await effects(p, a, Balance("video", 0))                         # no refill named: the pool's next midnight
        await effects(p, b, Balance("video", 0, named))                  # the refill the upstream named
        await effects(p, c, Balance("image", 0))                         # a pool with no window: UTC midnight
        return await locked(p, a), await locked(p, b), await locked(p, c, NO_WINDOW)

    got_a, got_b, got_c = run(db, scenario)
    midnight = pool.next_refill(CFG, datetime.now(UTC))
    assert abs((got_a.rest_until - midnight).total_seconds()) < 5 and got_a.left_n == 0
    until = got_b.rest_until
    assert until == named and got_b.left_n == 0
    assert abs((got_c.rest_until - midnight).total_seconds()) < 5 and got_c.left_n == 0
    assert pool_row(db, a)[0] == 0 and "no balance left" in pool_row(db, a)[2]


@mutation(P, "rest_until = CASE WHEN %(n)s > 0 THEN NULL ELSE GREATEST(rest_until, %(u)s) END ",
          "rest_until = CASE WHEN %(n)s > 0 THEN rest_until ELSE GREATEST(rest_until, %(u)s) END ",
          expect="[0] is None")
def test_a_reported_positive_balance_wakes_a_resting_pool_and_a_zero_one_does_not(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, Rest("video", FUTURE, "out of credit"), Balance("video", 0))
        assert db.one("SELECT rest_until IS NOT NULL FROM account_pools "
                      "WHERE account_id = %s AND pool = 'video'", a)[0]
        await effects(p, a, Balance("video", 5))

    run(db, scenario)
    assert pool_row(db, a)[0] == 5
    assert db.one("SELECT rest_until FROM account_pools WHERE account_id = %s AND pool = 'video'", a)[0] is None


def test_a_later_report_replaces_an_earlier_one_and_a_zero_never_shortens_a_longer_rest(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, Balance("video", 6))
        await effects(p, a, Balance("video", 2))
        assert (await locked(p, a)).left_n == 2
        await effects(p, a, Rest("video", FUTURE + timedelta(days=3), "long"), Balance("video", 0))

    run(db, scenario)
    until, reason = db.one("SELECT rest_until, rest_reason FROM account_pools WHERE account_id = %s AND pool = 'video'",
                           a)
    assert until > FUTURE + timedelta(days=2) and reason == "long"


# ---- effects: Tier ----------------------------------------------------------------------------------------------
def tier_row(db, account_id):
    return db.one("SELECT tier, tier_read_at IS NOT NULL, cycle_start FROM accounts WHERE id = %s", account_id)


@mutation(P, '"cycle_start = COALESCE(%(c)s, cycle_start)"', '"cycle_start = %(c)s"', expect="the stored cycle")
@mutation(P, '"tier = %(t)s, tier_read_at = clock_timestamp(), "', '"tier = %(t)s, "', expect="marked read")
def test_an_upstream_tier_overwrites_an_admin_tier_and_marks_it_read_and_a_cycle_start_is_kept_when_not_named(db):
    a = account(db, tier="ultra")                                       # set by the admin, never read
    assert tier_row(db, a) == ("ultra", False, None)
    run(db, lambda p: effects(p, a, Tier("pro", date(2026, 10, 8))))
    assert tier_row(db, a) == ("pro", True, date(2026, 10, 8)), "not marked read: %s" % (tier_row(db, a),)
    run(db, lambda p: effects(p, a, Tier("pro")))
    assert tier_row(db, a)[2] == date(2026, 10, 8), "the stored cycle start was dropped"
    with pytest.raises(ValueError):
        Tier("gold")


def test_a_pro_account_gets_the_pro_budgets_once_its_tier_is_read(db):
    a = account(db)
    assert left(db, a) == 4                                             # no tier: free
    run(db, lambda p: effects(p, a, Tier("pro")))
    assert left(db, a) == 10
    run(db, lambda p: effects(p, a, Tier("ultra")))
    assert left(db, a) is None                                          # no ultra budget: unknown, allows


def test_effects_are_applied_pool_first_and_an_unknown_pool_or_effect_is_refused(db):
    a = account(db)

    async def scenario(p):
        with pytest.raises(ValueError, match="nope"):
            await effects(p, a, Rest("nope", FUTURE, "x"))
        with pytest.raises(TypeError):
            await effects(p, a, object())

    run(db, scenario)
