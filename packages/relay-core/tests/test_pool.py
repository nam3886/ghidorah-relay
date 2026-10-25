"""Account pools (I6, I7): the window arithmetic (pure), capacity read from the charges, affordability, pool accounting
by `account_cost` with its once-only refund, the reported balance that wins until its own refill, the `<p>.pools`
setting, and the account effects (rest and cooldown by GREATEST, one strike counter, Dead and Revive around a disabled
account, the Balance rules)."""

import asyncio
import dataclasses
import uuid
from contextvars import Context
from datetime import UTC, date, datetime, time, timedelta

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
FIGURE_UNTIL = "    return report_ends if report_ends is not None and now < report_ends else next_refill("
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
    assert pool.window_span(UTC_DAY, at) == (datetime(2030, 3, 1, 0, 0, tzinfo=UTC),
                                             datetime(2030, 3, 2, 0, 0, tzinfo=UTC))
    assert pool.window_span(SAIGON_DAY, at) == (datetime(2030, 3, 1, 17, 0, tzinfo=UTC),     # 06:30 on 2 March there
                                                datetime(2030, 3, 2, 17, 0, tzinfo=UTC))


@mutation(P, "        return _calendar_span(Window(\"none\", \"day\", NO_WINDOW_REFILL_TZ), now, None)[1]\n",
          "        return now\n", expect="NO_WINDOW")
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


@mutation(P, "    return [w.budget[tier] if tier in w.budget else w.budget.get(DEFAULT_TIER) for w in cfg.windows]",
          "    return [w.budget.get(tier) for w in cfg.windows]", expect="unlimited")
def test_a_tier_with_no_budget_takes_the_free_budget_and_only_a_pool_with_no_budget_at_all_is_unknown():
    ultra = pool.standing(CFG, "ultra", [(NOON, 3)], NOON)              # CFG budgets free 4 and pro 10 only
    assert (ultra.left, ultra.source) == (1, "computed"), "a tier with no budget became unlimited: %s" % (ultra,)
    assert not pool.can_afford(ultra.left, 0, 2)
    pro_only = PoolConfig("video", "jobs", "sum", (Window("day", "day", "UTC", {"pro": 10}),))
    assert pool.left_of(pro_only, "free", [], NOON) is None             # no budget for the tier nor for free
    assert pool.left_of(NO_WINDOW, "free", [(NOON, 3)], NOON) is None
    assert pool.can_afford(None, 0, 99)


@mutation(P, "    if reported is not None and now < _refill_of_report(cfg, reported, charges, cycle_start):\n",
          "    if reported is not None:\n",
          expect="assert pool.left_of(CFG, \"free\", [], NOON + 13 * HOUR, report) == 4")
@mutation(P, "    if reported is not None and now < _refill_of_report(cfg, reported, charges, cycle_start):\n",
          "    if False:\n", expect="assert (same_day.left, same_day.source) == (0, \"reported\")")
def test_a_reported_balance_wins_until_the_pools_next_refill_and_then_the_budget_applies():
    report = Reported(1, NOON)
    # the charge made before the report is already in it; the one after is not
    same_day = pool.standing(CFG, "free", [(NOON - HOUR, 2), (NOON + 10 * MINUTE, 1)], NOON + 30 * MINUTE, report)
    assert (same_day.left, same_day.source) == (0, "reported")
    assert pool.left_of(CFG, "free", [], NOON + 13 * HOUR, report) == 4     # past the next midnight: the budget again
    assert pool.left_of(CFG, "free", [], NOON + 13 * HOUR, None) == 4


@mutation(P, "            span = window_span(w, at, cycle_start, anchors[i])\n",
          "            span = window_span(w, now, cycle_start, anchors[i])\n", expect="assert state.spent == 1")
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


# ---- window kinds: week, month, rolling; how they add up -----------------------------------------------------------
WEEK = Window("week", "week", "UTC", {"free": 5})
MONTH = Window("month", "month", "UTC", {"free": 5})
BERLIN = {kind: Window(kind, kind, "Europe/Berlin", {"free": 5}) for kind in ("day", "week", "month")}
ROLL = Window("5h", "rolling", "UTC", {"free": 3}, 5)
BLOCK = Window("5h", "rolling", "UTC", {"free": 3}, 5, "reset")
# flow's video: 50 a day from the first use on every plan, and 1000 a month on pro; the day's credits go first
FLOWLIKE = PoolConfig("video", "credits", "sum", (Window("24h", "rolling", "UTC", {"free": 50, "pro": 50}, 24),
                                                  Window("month", "month", "UTC", {"pro": 1000})))


def t(day, hour=0, minute=0):
    return datetime(2030, 3, day, hour, minute, tzinfo=UTC)


@mutation(P, "        first = d - timedelta(days=d.weekday())\n", "        first = d\n", expect="a week from Monday")
def test_a_week_window_runs_monday_to_monday_and_refills_at_its_end():
    assert pool.window_span(WEEK, t(10, 23, 59)) == (t(4), t(11)), "a week from Monday"     # Sunday 10 March 2030
    assert pool.window_span(WEEK, t(11)) == (t(11), t(18)), "a week from Monday: Monday midnight opens the next"
    cfg = PoolConfig("video", "jobs", "sum", (WEEK,))
    charges = [(t(4, 0, 1), 2), (t(10, 22), 1)]
    assert pool.left_of(cfg, "free", charges, t(10, 23)) == 2
    assert pool.left_of(cfg, "free", charges, t(11, 0, 1)) == 5
    assert pool.next_refill(cfg, t(10, 23)) == t(11)


@mutation(P, "        back = 0 if _cycle_day(d.year, d.month, anchor) <= d else 1\n", "        back = 0\n",
          expect="the cycle that began last month")
@mutation(P, "    return date(year, month, min(day, monthrange(year, month)[1]))\n",
          "    return date(year, month, min(day, 28))\n", expect="the 31st")
def test_a_month_window_runs_from_the_cycle_start_day_clamped_to_the_months_length_else_from_the_first():
    assert pool.window_span(MONTH, t(5)) == (t(1), datetime(2030, 4, 1, tzinfo=UTC)), "no cycle: from the 1st"
    on_15 = date(2030, 1, 15)
    assert pool.window_span(MONTH, t(14, 23), on_15) == (datetime(2030, 2, 15, tzinfo=UTC), t(15)), \
        "the cycle that began last month"
    assert pool.window_span(MONTH, t(15), on_15) == (t(15), datetime(2030, 4, 15, tzinfo=UTC))
    on_31 = date(2030, 1, 31)                     # renews on the last day of a shorter month, and on the 31st again
    assert pool.window_span(MONTH, datetime(2030, 2, 27, tzinfo=UTC), on_31) == (
        datetime(2030, 1, 31, tzinfo=UTC), datetime(2030, 2, 28, tzinfo=UTC)), "the 31st, clamped in February"
    assert pool.window_span(MONTH, t(30), on_31) == (datetime(2030, 2, 28, tzinfo=UTC), t(31)), "the 31st"
    assert pool.window_span(MONTH, datetime(2030, 12, 31, 12, tzinfo=UTC), on_31) == (
        datetime(2030, 12, 31, tzinfo=UTC), datetime(2031, 1, 31, tzinfo=UTC))
    cfg = PoolConfig("video", "credits", "sum", (MONTH,))
    charges = [(datetime(2030, 2, 20, tzinfo=UTC), 4), (t(16), 1)]
    assert pool.left_of(cfg, "free", charges, t(20), None, on_15) == 4, "the charge of the last cycle is not spent"
    assert pool.left_of(cfg, "free", charges, t(14), None, on_15) == 1


@mutation(P, "    return _midnight(first, zone), _midnight(after, zone)\n",
          "    return _midnight(first, zone), _midnight(first, zone) + (after - first)\n", expect="hours long")
@mutation(P, "        first, after = d, d + timedelta(days=1)\n", "        first, after = d, d + timedelta(days=2)\n",
          expect="hours long")
def test_calendar_windows_follow_the_clock_change_of_their_own_zone():
    """Each window resets at the midnight of its own `tz` (the `<p>.pools` setting), not of the dashboard's zone:
    in Berlin the clocks go forward on 31 March 2030 and back on 27 October 2030."""
    def hours(kind, at):
        start, end = pool.window_span(BERLIN[kind], at)
        return start, end, (end - start) / HOUR

    spring = datetime(2030, 3, 31, 12, tzinfo=UTC)
    assert hours("day", spring) == (datetime(2030, 3, 30, 23, tzinfo=UTC), datetime(2030, 3, 31, 22, tzinfo=UTC), 23), \
        "a day 23 hours long"
    assert hours("week", spring) == (datetime(2030, 3, 24, 23, tzinfo=UTC), datetime(2030, 3, 31, 22, tzinfo=UTC),
                                     167), "a week 167 hours long"
    assert hours("month", spring) == (datetime(2030, 2, 28, 23, tzinfo=UTC), datetime(2030, 3, 31, 22, tzinfo=UTC),
                                      31 * 24 - 1), "a month 743 hours long"
    autumn = datetime(2030, 10, 27, 12, tzinfo=UTC)
    assert hours("day", autumn) == (datetime(2030, 10, 26, 22, tzinfo=UTC), datetime(2030, 10, 27, 23, tzinfo=UTC),
                                    25), "a day 25 hours long"
    assert hours("week", autumn)[2] == 169 and hours("month", autumn)[2] == 31 * 24 + 1, "hours long"
    # a charge just after the clocks went forward belongs to that short day; the next day starts at its midnight
    cfg = PoolConfig("video", "jobs", "sum", (BERLIN["day"],))
    assert pool.left_of(cfg, "free", [(datetime(2030, 3, 31, 1, 30, tzinfo=UTC), 2)], spring) == 3
    assert pool.left_of(cfg, "free", [(datetime(2030, 3, 31, 1, 30, tzinfo=UTC), 2)],
                        datetime(2030, 3, 31, 22, tzinfo=UTC)) == 5


@mutation(P, "        return now - lengths[i] < at <= now if span is None else span[0] <= at < span[1]\n",
          "        return at <= now if span is None else span[0] <= at < span[1]\n", expect="the last 5 h")
@mutation(P, "            out.append(sum(n for t, n in slid[i] if now - lengths[i] < t))\n",
          "            out.append(sum(n for t, n in slid[i]))\n", expect="the last 5 h")
@mutation(P, "    return min((at for at, _ in charges if now - length < at <= now), default=now) + length\n",
          "    return now + length\n", expect="refills 5 h after the oldest")
def test_a_first_use_window_counts_its_last_hours_so_it_never_holds_less_than_the_true_window():
    midnight = t(5)
    # one charge every 4 h: the true first-use windows are [0, 5), [8, 13), [16, 21); at 20:30 the open one holds the
    # charges of 16:00 and 20:00, and so do the last 5 h
    charges = [(midnight + k * HOUR, 1) for k in (0, 4, 8, 12, 16, 20)]
    now = midnight + 20 * HOUR + 30 * MINUTE
    for combine in ("sum", "all"):
        got = pool.standing(PoolConfig("video", "jobs", combine, (ROLL,)), "free", charges, now)
        assert (got.windows[0].spent, got.left) == (2, 1), "the last 5 h hold 16:00 and 20:00 (%s): %s" % (combine, got)
        assert got.windows[0].refill_at == midnight + 21 * HOUR, "refills 5 h after the oldest charge it holds"
    cfg = PoolConfig("video", "jobs", "sum", (ROLL,))
    at_21 = pool.standing(cfg, "free", charges, midnight + 21 * HOUR)
    assert (at_21.windows[0].spent, at_21.windows[0].refill_at) == (1, midnight + 25 * HOUR), \
        "the last 5 h: 16:00 leaves at 21:00 sharp"
    idle = pool.standing(cfg, "free", charges, midnight + 26 * HOUR)
    assert (idle.left, idle.windows[0].spent, idle.windows[0].refill_at) == (3, 0, midnight + 31 * HOUR), \
        "the last 5 h hold nothing: a full budget"
    assert pool.next_refill(cfg, now, charges) == midnight + 21 * HOUR


@mutation(P, "    if reported is None or reported.window != w.name:\n", "    if reported is None:\n",
          expect="leaves the week sliding")
def test_a_reported_reset_anchors_only_the_reset_window_it_names():
    noon = t(5, 12)
    week = Window("week", "rolling", "UTC", {"free": 9}, 168, "reset")
    both = PoolConfig("video", "uses", "all", (BLOCK, week))
    charges = [(noon - HOUR, 1)]
    now = noon + 4 * HOUR

    def refills(report, cfg=both):
        return [w.refill_at for w in pool.standing(cfg, "free", charges, now, report).windows]

    assert refills(Reported(0, noon, noon + 3 * HOUR, "5h")) == [noon + 8 * HOUR, noon + 167 * HOUR], \
        "the 5 h reset places the 5 h blocks and leaves the week sliding"
    assert refills(Reported(0, noon, noon + 100 * HOUR, "week")) == [noon + 9 * HOUR, noon + 100 * HOUR], \
        "the week's reset places the week and never the 5 h block"
    assert refills(Reported(0, noon, noon + 3 * HOUR)) == [noon + 9 * HOUR, noon + 167 * HOUR], \
        "a refill that names no window anchors none"
    first_use = PoolConfig("video", "uses", "all", (ROLL,))
    assert refills(Reported(0, noon, noon + 3 * HOUR, "5h"), first_use) == [noon + 9 * HOUR], \
        "a first-use window is never placed by a reset"


@mutation(P, "    return _block(w, at, anchor) if anchor is not None and w.anchor == \"reset\" else None\n",
          "    return None\n", expect="blocks")
def test_a_reset_anchored_window_runs_in_blocks_placed_by_the_reported_reset():
    noon = t(5, 12)
    report = Reported(0, noon, noon + 3 * HOUR, "5h")
    cfg = PoolConfig("video", "uses", "all", (BLOCK,))
    assert [pool.window_span(BLOCK, noon + h * HOUR, None, noon + 3 * HOUR) for h in (4, 9)] == [
        (noon + 3 * HOUR, noon + 8 * HOUR), (noon + 8 * HOUR, noon + 13 * HOUR)], "blocks"
    idle = pool.standing(cfg, "free", [], noon + 4 * HOUR, report)
    assert (idle.left, idle.windows[0].refill_at) == (3, noon + 8 * HOUR), "blocks: the next one ends 5 h later"
    used = pool.standing(cfg, "free", [(noon + 8 * HOUR + 30 * MINUTE, 2)], noon + 9 * HOUR, report)
    assert (used.left, used.windows[0].spent, used.windows[0].refill_at) == (1, 2, noon + 13 * HOUR), "blocks"
    early = pool.standing(cfg, "free", [(noon - 4 * HOUR, 1)], noon - 3 * HOUR,
                          Reported(0, noon - HOUR, noon + 3 * HOUR, "5h"))
    assert early.windows[0].refill_at == noon - 2 * HOUR, "blocks before the reset line up with it too"
    unanchored = pool.standing(cfg, "free", [(noon, 1)], noon + HOUR)
    assert unanchored.windows[0].refill_at == noon + 5 * HOUR, "no reset known: the last 5 h"


@mutation(P, "take = amount if k == len(here) - 1 else min(amount, max(0, budgets[i] - used))", "take = amount",
          expect="the day's credits first")
@mutation(P, "here.sort(key=lambda h: (h[0], h[1]))", "here.sort(key=lambda h: (-h[0].timestamp(), h[1]))",
          expect="the day's credits first")
def test_sum_windows_spend_the_window_that_refills_first_and_add_up_their_rooms():
    noon = t(5, 12)
    today = [(noon, 30), (noon + HOUR, 30)]
    pro = pool.standing(FLOWLIKE, "pro", today, noon + 2 * HOUR)
    assert [(w.budget, w.spent) for w in pro.windows] == [(50, 50), (1000, 10)], "the day's credits first"
    assert (pro.left, pro.budget) == (990, 1050)
    yesterday = [(noon - 30 * HOUR, 30), (noon - 29 * HOUR, 30)]
    later = pool.standing(FLOWLIKE, "pro", yesterday, noon)
    assert [w.spent for w in later.windows] == [0, 10] and later.left == 1040, \
        "yesterday's day absorbed 50 of yesterday's 60; the month keeps only the rest"
    free = pool.standing(FLOWLIKE, "free", today[:1], noon + 2 * HOUR)
    assert [w.budget for w in free.windows] == [50, None] and (free.left, free.budget) == (20, 50), \
        "the month does not bind a free account"
    over = pool.standing(FLOWLIKE, "pro", [(noon, 1100)], noon + HOUR)
    assert [w.spent for w in over.windows] == [50, 1050] and over.left == 0, "the excess stays on the last window"
    assert pool.ceiling(FLOWLIKE) == 1050
    renewal = noon + timedelta(days=14)                         # a paid plan's refill is its monthly renewal
    assert pool.left_of(FLOWLIKE, "pro", today, noon + 2 * HOUR, Reported(1037, noon - HOUR, renewal)) == 977, \
        "the reported balance leads (over 50 a day): less only what was spent after it"


@mutation(P, "    computed = None if not rooms else min(rooms) if cfg.combine == \"all\" else sum(",
          "    computed = None if not rooms else max(rooms) if cfg.combine == \"all\" else sum(",
          expect="the tightest window")
@mutation(P, "    return None if not known else min(known) if cfg.combine == \"all\" else sum(known)\n",
          "    return None if not known else max(known) if cfg.combine == \"all\" else sum(known)\n",
          expect="the budget is the tightest window's")
@mutation(P, "    plain = [sum(n for at, n in charges if holds_now(i, at)) for i in range(len(cfg.windows))]\n",
          "    plain = [sum(n for at, n in charges) for i in range(len(cfg.windows))]\n", expect="the week counts")
def test_all_windows_must_each_hold_and_a_null_budget_does_not_bind_the_tier():
    noon = t(5, 12)                                                    # a Tuesday
    gemini_like = PoolConfig("video", "uses", "all", (
        Window("day", "day", "UTC", {"free": 15, "pro": None}), Window("5h", "rolling", "UTC", {"pro": 5}, 5),
        Window("week", "week", "UTC", {"pro": 102})))
    charges = [(t(4, 1), 40), (t(4, 2), 58), (t(3, 12), 50), (noon, 1), (noon + HOUR, 2)]  # 3 March is last week
    pro = pool.standing(gemini_like, "pro", charges, noon + 2 * HOUR)
    assert [w.spent for w in pro.windows] == [3, 3, 101], "the week counts this week's charges only"
    assert pro.left == 1, "the tightest window: %s" % (pro,)
    assert pro.budget == 5 and pro.windows[0].budget is None, "the budget is the tightest window's"
    many = [(noon + MINUTE * i, 1) for i in range(16)]
    assert pool.left_of(gemini_like, "pro", many, noon + HOUR) == -11, "the day guard of free does not bind pro"
    assert pool.left_of(gemini_like, "free", many, noon + HOUR) == -1, "free: its day guard alone"
    assert pool.left_of(gemini_like, "ultra", [], noon) == 15, "a tier not named takes free's"
    assert pool.ceiling(gemini_like) == 15, "the ceiling: pro holds 5 at most, free 15"
    assert pool.ceiling(PoolConfig("v", "uses", "all", (Window("d", "day", "UTC", {"pro": None}),))) is None


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
    ({"video": {"windows": [day_pool({"free": 1})["windows"][0]] * 2}}, "two windows have one name"),
    ({"video": {"windows": [{"name": "d", "kind": "year", "tz": "UTC", "budget": {}}]}}, "kind must be one of day"),
    ({"video": {"windows": [{"name": "d", "kind": "month", "budget": {}}]}}, "IANA time zone"),
    ({"video": {"windows": [{"name": "r", "kind": "rolling", "budget": {}}]}}, "needs hours"),
    ({"video": {"windows": [{"name": "r", "kind": "rolling", "hours": 0, "budget": {}}]}}, "needs hours"),
    ({"video": {"windows": [{"name": "r", "kind": "rolling", "hours": 961, "budget": {}}]}}, "needs hours"),
    ({"video": {"windows": [{"name": "r", "kind": "rolling", "hours": 5, "anchor": "x", "budget": {}}]}},
     "anchor must be one of"),
    ({"video": {"windows": [{"name": "d", "kind": "day", "tz": "UTC", "hours": 5, "budget": {}}]}},
     "rolling window only"),
    ({"video": {"windows": [{"name": "d", "kind": "week", "tz": "UTC", "anchor": "reset", "budget": {}}]}},
     "rolling window only"),
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


def test_every_window_kind_reads_into_its_config_and_a_null_budget_is_kept():
    spec = setting()
    value = {"video": {"combine": "all", "windows": [
        {"name": "d", "kind": "day", "tz": "Europe/Berlin", "budget": {"free": 3, "pro": None}},
        {"name": "w", "kind": "week", "tz": "UTC", "budget": {}},
        {"name": "m", "kind": "month", "tz": "UTC", "budget": {"pro": 9}},
        {"name": "5h", "kind": "rolling", "hours": 5, "anchor": "reset", "budget": {"pro": 5}},
        {"name": "24h", "kind": "rolling", "hours": 24, "budget": {"free": 1}}]}}
    assert spec.validate(value) == value
    caps = Caps(execution="http", pools=SPECS, account_slots=1, max_attempts=3, suspect_threshold=3,
                strike_effect=Dead("x"))
    got = pool.pool_configs("p1", caps, {"p1.pools": value}.get)["video"]
    assert got == PoolConfig("video", "credits", "all", (
        Window("d", "day", "Europe/Berlin", {"free": 3, "pro": None}), Window("w", "week", "UTC", {}),
        Window("m", "month", "UTC", {"pro": 9}), Window("5h", "rolling", "UTC", {"pro": 5}, 5, "reset"),
        Window("24h", "rolling", "UTC", {"free": 1}, 24, "first_use")))


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
    assert left(db, account(db, tier="ultra")) == 4, "no budget for a tier is the free budget, never unknown"


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
@mutation(P, "            until = refill\n", "            until = None\n", expect="assert until == named")
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


MONTHLY = PoolConfig("video", "credits", "sum", (Window("month", "month", "UTC", {"free": 10}),))


@mutation(P, "_reported(row[0], row[1], row[2], row[8]), row[5], row[6], row[7]\n",
          "_reported(row[0], row[1], row[2], row[8]), row[5], row[6], None\n", expect="the claim reads")
@mutation(P, "    tier, cycle = (None, None) if row is None else row\n",
          "    tier, cycle = (None, None) if row is None else (row[0], None)\n", expect="an engine is told")
@mutation(P, "                                                cycles.get(a)), reports.get((a, name)), row[3], row[4])",
          "                                                None), reports.get((a, name)), row[3], row[4])",
          expect="the admin shows")
@pytest.mark.fabricated_rows("plants an account charge that is not the job account_cost", "account_cost")
def test_a_month_window_starts_on_the_accounts_cycle_day(db):
    now = db.one("SELECT clock_timestamp()")[0]
    today = now.astimezone(UTC).date()
    if today.day >= 2:      # the cycle renewed today: a charge just before midnight is the last cycle's
        cycle, at = date(2000, 1, today.day), datetime.combine(today, time.min, UTC) - MINUTE
    else:                   # on the 1st a cycle from the 2nd still holds yesterday's charge
        cycle, at = date(2000, 1, 2), datetime.combine(today, time.min, UTC) - 12 * HOUR
    # a report long past loads the older charges too, so only the window decides what counts
    stale = Reported(0, now - timedelta(days=40), now - timedelta(days=39))
    want = pool.left_of(MONTHLY, None, [(at, 3)], now, stale, cycle)
    assert want != pool.left_of(MONTHLY, None, [(at, 3)], now, stale), "the case tells a cycle day from the 1st"
    a, job = account(db), a_job(db)
    db.run("UPDATE accounts SET cycle_start = %s WHERE id = %s", cycle, a)
    report(db, a, 0, ago_s=40 * 86400, refill_in_s=-39 * 86400)

    async def scenario(p):
        await charge(p, a, job, 3, MONTHLY)
        db.run("UPDATE account_charges SET at = %s WHERE job_id = %s", at, job)
        async with tx(p) as conn:
            told = (await pool.left_map(conn, a, {"video": MONTHLY}))[0]
            _, shown = await pool.pool_states(conn, {a: (None, {"video": MONTHLY})})
        return (await locked(p, a, MONTHLY)).left_n, told["video"], shown[(a, "video")].standing.left

    claim, engine, admin = run(db, scenario)
    assert claim == want, "the claim reads the account's cycle start"
    assert engine == want, "an engine is told the cycle's balance"
    assert admin == want, "the admin shows the cycle's balance"


@mutation(P, "                until = next_refill(cfg, row[6], charges, row[7])\n",
          "                until = next_refill(cfg, row[6])\n", expect="the open window's end")
@pytest.mark.fabricated_rows("plants an account charge that is not the job account_cost", "account_cost")
def test_a_zero_balance_with_no_named_refill_rests_until_the_open_rolling_window_ends(db):
    rolling = {"video": PoolConfig("video", "jobs", "sum", (ROLL,)), "image": NO_WINDOW}
    a, job = account(db), a_job(db)

    async def scenario(p):
        await charge(p, a, job, 1, rolling["video"])
        db.run("UPDATE account_charges SET at = clock_timestamp() - interval '2 hours' WHERE job_id = %s", job)
        async with tx(p) as conn:
            await pool.apply_effects(conn, a, (Balance("video", 0),), CAPS, rolling)
        return await locked(p, a, rolling["video"])

    got = run(db, scenario)
    opened = db.one("SELECT at FROM account_charges WHERE job_id = %s", job)[0]
    assert abs((got.rest_until - (opened + 5 * HOUR)).total_seconds()) < 1, "the open window's end: %s" % (got,)


@mutation(P, "            window = _window_tag(e.window, refill) if named else None\n",
          "            window = None\n", expect="stored with the window it names")
@mutation(P, " and refill is not None and refill == e.refill_at\n", " and refill is not None\n",
          expect="an adjusted refill names no window")
@mutation(P, "    return window if window and tag == _window_tag(window, refill_at) else None\n",
          "    return window or None\n", expect="a tag of another refill")
@mutation(P, "reports = {key: _reported(r[0], r[1], r[2], r[5])", "reports = {key: _reported(r[0], r[1], r[2], None)",
          expect="the named reset places the block")
def test_a_reported_refill_is_stored_with_the_window_it_names_and_places_its_blocks(db):
    named, plain, late = account(db), account(db), account(db)
    pools = {"video": PoolConfig("video", "uses", "all", (BLOCK,)), "image": NO_WINDOW}
    reset = datetime.now(UTC) + 2 * HOUR

    async def scenario(p):
        async with tx(p) as conn:
            await pool.apply_effects(conn, named, (Balance("video", 0, reset, "5h"),), CAPS, pools)
            await pool.apply_effects(conn, plain, (Balance("video", 0, reset),), CAPS, pools)
            await pool.apply_effects(conn, late, (Balance("video", 0, datetime.now(UTC) - MINUTE, "5h"),), CAPS, pools)
        async with tx(p) as conn:
            return (await pool.pool_states(conn, {named: (None, pools), plain: (None, pools)}))[1]

    states = run(db, scenario)
    stored = {a: db.one("SELECT refill_window FROM account_pools WHERE account_id = %s AND pool = 'video'", a)[0]
              for a in (named, plain, late)}
    assert (stored[named], stored[plain]) == (pool._window_tag("5h", reset), None), \
        "stored with the window it names: %s" % (stored,)
    assert stored[late] is None, "an adjusted refill names no window (it is the next check, not the reset)"
    assert states[(named, "video")].standing.windows[0].refill_at == reset, "the named reset places the block"
    assert states[(plain, "video")].standing.windows[0].refill_at != reset
    # a build without the tag (a rollback) writes a newer refill and leaves the tag: it no longer names a window
    later = reset + HOUR
    db.run("UPDATE account_pools SET refill_at = %s WHERE account_id = %s AND pool = 'video'", later, named)

    async def reread(p):
        async with tx(p) as conn:
            return (await pool.pool_states(conn, {named: (None, pools)}))[1][(named, "video")]

    stale = run(db, reread)
    assert stale.reported.window is None and stale.standing.windows[0].refill_at != later, \
        "a tag of another refill names no window: %s" % (stale,)


def test_a_window_tag_names_its_window_only_beside_the_refill_it_was_written_with():
    reset = t(5, 12, 7)
    tag = pool._window_tag("5h", reset)
    assert pool._tagged_window(tag, reset) == "5h"
    assert pool._tagged_window(tag, reset + timedelta(microseconds=1)) is None, "a tag of another refill"
    assert pool._tagged_window(tag, None) is None and pool._tagged_window(None, reset) is None
    assert pool._tagged_window("5h", reset) is None, "an untimed tag names no window"


@pytest.mark.parametrize("refunded_first", [False, True])
def test_a_refunded_charge_was_never_spent_so_a_later_charge_takes_the_days_credits_it_left(refunded_first):
    """An account charge is refunded only when upstream did not complete the order (`jobs.fail`), so the account never
    spent it: upstream would have taken the later 10 credits from the day's 50, and so do the windows."""
    noon = t(5, 12)
    first, later = (noon - 30 * HOUR, 50), (noon - 29 * HOUR, 10)
    charges = [later] if refunded_first else [first, later]
    got = pool.standing(FLOWLIKE, "pro", charges, noon)
    assert [w.spent for w in got.windows] == ([0, 0] if refunded_first else [0, 10]) and got.left == (
        1050 if refunded_first else 1040)


BELIEVED = "    if not now - timedelta(seconds=REFILL_PAST_S) <= refill <= now + timedelta(seconds=REFILL_AHEAD_S):\n"


@mutation(P, BELIEVED, "    if False:\n", expect="not believed")
@mutation(P, "    return refill if refill > now else now + timedelta(seconds=check_s)\n", "    return refill\n",
          expect="until the next check")
def test_a_named_refill_is_believed_only_inside_the_plausible_range():
    check_s = 300
    assert pool.believed_refill(NOON + HOUR, NOON, check_s) == NOON + HOUR
    assert pool.believed_refill(NOON + timedelta(days=39), NOON, check_s) == NOON + timedelta(days=39)
    assert pool.believed_refill(NOON + timedelta(days=41), NOON, check_s) is None, "a far refill was not believed"
    assert pool.believed_refill(NOON - HOUR, NOON, check_s) is None, "a refill long past was not believed"
    assert pool.believed_refill(NOON - MINUTE, NOON, check_s) == NOON + timedelta(seconds=check_s), \
        "a reset just past must hold until the next check"
    assert pool.believed_refill(None, NOON, check_s) is None


def test_a_zero_balance_with_an_unbelievable_refill_rests_until_the_pools_window_and_one_just_past_until_the_next_check(
        db):
    far, past, recent = account(db), account(db), account(db)
    now = datetime.now(UTC)

    async def scenario(p):
        await effects(p, far, Balance("video", 0, now + timedelta(days=400)))
        await effects(p, past, Balance("video", 0, now - timedelta(days=2)))
        await effects(p, recent, Balance("video", 0, now - MINUTE))
        return [await locked(p, a) for a in (far, past, recent)]

    got_far, got_past, got_recent = run(db, scenario)
    midnight = pool.next_refill(CFG, datetime.now(UTC))
    for got in (got_far, got_past):
        assert abs((got.rest_until - midnight).total_seconds()) < 5 and got.left_n == 0, got
    assert db.one("SELECT refill_at FROM account_pools WHERE account_id = %s", far)[0] is None
    next_check = datetime.now(UTC) + timedelta(seconds=CAPS.check_interval_s)
    assert abs((got_recent.rest_until - next_check).total_seconds()) < 5 and got_recent.left_n == 0, \
        "a reset just past reopened the pool"


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


# ---- effects: Balance far from the computed one (pool_drift) -----------------------------------------------------
DRIFTING = {"video": dataclasses.replace(CFG, drift_alert=2), "image": dataclasses.replace(NO_WINDOW, drift_alert=2)}


async def reports(p, account_id, *effs, pools=None):
    async with tx(p) as conn:
        await pool.apply_effects(conn, account_id, effs, CAPS, pools or DRIFTING)


def drifts(db, account_id):
    return db.all("SELECT scope, provider, expected, actual, detail FROM billing_alerts WHERE alert = 'pool_drift' "
                  "AND account_id = %s ORDER BY id", account_id)


@mutation(P, "            if computed is None or abs(reported_left - computed) <= cfg.drift_alert:\n",
          "            if computed is None:\n", expect="within the threshold")
@mutation(P, "AND b.scope = %(p)s AND b.at >= %(since)s)", "AND b.scope = %(p)s AND false)", expect="once per window")
@mutation(P, "            await _drift_alert(conn, account_id, cfg, row,\n",
          "            (conn, account_id, cfg, row,\n", expect="no drift alert")
def test_a_reported_balance_far_from_the_computed_one_raises_one_pool_drift_per_account_pool_and_window(db):
    far, near, no_window, off = account(db), account(db), account(db), account(db)

    async def scenario(p):
        await reports(p, far, Balance("video", 0))            # free budget 4, nothing spent: 4 away (> 2)
        await reports(p, far, Balance("video", 9))            # the same window: no second alert
        await reports(p, near, Balance("video", 2))           # 2 away: within the threshold
        await reports(p, no_window, Balance("image", 0))      # no window: nothing to compare with
        await reports(p, off, Balance("video", 0), pools=POOLS)      # the alert is off (no threshold)

    run(db, scenario)
    assert drifts(db, far) == [("video", "p1", 4, 0, "the upstream reported 0 jobs left, the relay computed 4")], \
        "no drift alert, or not once per window: %s" % drifts(db, far)
    assert drifts(db, near) == [], "a report within the threshold raised an alert"
    assert drifts(db, no_window) == [] and drifts(db, off) == [], "an alert turned off was raised"
    assert pool_row(db, far)[0] == 9, "the report itself is recorded as before"


DRIFT_BLOCK = Window("5h", "rolling", "UTC", {"free": 10}, 5, "reset")
BLOCKED = {"video": PoolConfig("video", "jobs", "all", (DRIFT_BLOCK,), drift_alert=2), "image": NO_WINDOW}


@mutation(P, "        span = window_span(w, now, cycle_start, _anchor(w, report))\n",
          "        span = window_span(w, now, cycle_start, None)\n", expect="the new block")
def test_a_drift_in_a_new_reset_block_is_raised_though_the_last_alert_was_minutes_ago(db):
    """Codex 25/10: an alert at 04:55 for a block that resets at 05:00; a real drift at 05:01 belongs to the new block
    (placed by the reset gemini reports) and must be raised, not held until about 09:55."""
    a = account(db)
    db.run("INSERT INTO billing_alerts (alert, account_id, scope, provider, expected, actual, at) "
           "VALUES ('pool_drift', %s, 'video', 'p1', 10, 0, clock_timestamp() - interval '2 minutes')", a)
    refill = datetime.now(UTC) + timedelta(hours=5) - MINUTE          # this block began a minute ago

    async def scenario(p):
        async with tx(p) as conn:
            await pool.apply_effects(conn, a, [Balance("video", 0, refill, "5h")], CAPS, BLOCKED)

    run(db, scenario)
    assert len(drifts(db, a)) == 2, "the drift of the new block was held back: %s" % drifts(db, a)
    now = datetime(2030, 3, 5, 5, 1, tzinfo=UTC)
    report = Reported(0, now, datetime(2030, 3, 5, 10, 0, tzinfo=UTC), "5h")
    assert pool.drift_window_start(BLOCKED["video"], now, None, report) == datetime(2030, 3, 5, 5, 0, tzinfo=UTC)
    assert pool.drift_window_start(BLOCKED["video"], now, None, None) == now - timedelta(hours=5), \
        "no reset known: the block slides"


def test_the_drift_is_the_reported_balance_less_the_computed_one_and_the_threshold_is_the_setting():
    reported = pool.standing(CFG, "free", [(NOON - HOUR, 1)], NOON, Reported(0, NOON - MINUTE))
    assert (reported.left, reported.computed, reported.drift) == (0, 3, -3)
    computed = pool.standing(CFG, "free", [], NOON)
    assert (computed.source, computed.drift) == ("computed", None), "no report: no drift"
    assert pool.standing(NO_WINDOW, "free", [], NOON, Reported(2, NOON)).drift is None, "no window: no drift"
    cfgs = pool.pool_configs("p1", CAPS, {"p1.pools": {"video": day_pool({"free": 4})}, "alert_pool_drift": 7}.get)
    assert cfgs["video"].drift_alert == 7


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
    assert left(db, a) == 4                                             # no ultra budget: the free one, never unlimited


def test_effects_are_applied_pool_first_and_an_unknown_pool_or_effect_is_refused(db):
    a = account(db)

    async def scenario(p):
        with pytest.raises(ValueError, match="nope"):
            await effects(p, a, Rest("nope", FUTURE, "x"))
        with pytest.raises(TypeError):
            await effects(p, a, object())

    run(db, scenario)


@mutation(P, FIGURE_UNTIL, "    return next_refill(", expect="the refill the upstream named")
@mutation(P, FIGURE_UNTIL, "    return report_ends if report_ends is not None else next_refill(",
          expect="an old report")
def test_a_capacity_figure_holds_until_its_reports_refill_else_the_pools_next_window_boundary():
    midnight = datetime(2030, 3, 6, tzinfo=UTC)
    named = NOON + timedelta(hours=3)
    assert pool.figure_until(CFG, NOON, None) == midnight, "no report: the pool's next window boundary"
    assert pool.figure_until(CFG, NOON, Reported(3, NOON - timedelta(hours=1), named)) == named, \
        "the refill the upstream named ends the reported figure"
    assert pool.figure_until(CFG, NOON, Reported(3, NOON - timedelta(hours=1))) == midnight
    assert pool.figure_until(CFG, NOON, Reported(3, NOON - timedelta(days=1))) == midnight, \
        "an old report no longer holds: the computed figure ends at the next boundary"
    assert pool.figure_until(SAIGON, NOON, None) == datetime(2030, 3, 5, 17, 0, tzinfo=UTC)


QUIET = "count = count + 1 if last is not None and now - last < REPEAT_QUIET_S else 1"


@mutation(P, QUIET, "count = count + 1", expect="a quiet day starts again")
def test_a_repeated_checkpoint_doubles_the_rest_up_to_the_max_and_a_quiet_day_starts_it_again():
    now = [0.0]
    rests = pool.RepeatRest(lambda: now[0])
    assert [rests.next_s("a", 600, 3000) for _ in range(5)] == [600, 1200, 2400, 3000, 3000]
    assert rests.next_s("b", 600, 3000) == 600, "each account has its own streak"
    assert rests.next_s("c", 600, 100) == 600, "a max below the base rests the base"
    now[0] = pool.REPEAT_QUIET_S
    assert rests.next_s("a", 600, 3000) == 600, "a quiet day starts again from the base"
