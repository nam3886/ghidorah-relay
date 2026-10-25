"""Account pools: capacity, rests and cooldowns, strikes, and pool accounting (invariants I6, I7, I29).

Pool accounting counts `account_cost` (what an order costs the upstream account, in the pool's unit), never the price a
client pays. Spend is not a stored counter: it is computed from `account_charges` against the pool's WINDOWS (the
`<p>.pools` setting) for the account's tier. `left_n` is None when the pool has no budget for the tier (unknown), and
unknown allows. A balance the upstream REPORTED wins until its own refill (the refill the upstream named, else the
pool's next window boundary after the report); it is never replaced by a computed value before that. Every write that
spans rows takes the caller's transaction (`require_tx`). The ACCOUNT ROW is locked before any pool row of the account
(`lock_account`; after the job and lease rows); pool rows are then locked or created in any order, because everything is
already serialised per account. A claim takes the account row `SKIP LOCKED` and never waits.

No provider is named here: a pool's unit and windows arrive in a `PoolConfig` built from the provider's `Caps` and its
`<p>.pools` setting.
"""

import logging
from calendar import monthrange
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from math import floor
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg import AsyncConnection

from relay_core import failpoints, history
from relay_core.db import lock_account
from relay_core.providers import (
    TIERS,
    AccountEffect,
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
from relay_core.redact import redact_log
from relay_core.settings import Rule, Spec
from relay_core.txguard import require_tx

log = logging.getLogger(__name__)

DEFAULT_TIER = "free"            # an account with no tier is a free one
# A window is a calendar `day`, `week` (from Monday) or `month` in its `tz` (a month from the account's cycle start
# day, else the 1st), or `rolling`: `hours` long. A rolling window's `anchor` says where it starts: `first_use` (the
# first spend after the previous one ended) is counted as a sliding window, the charges of the last `hours`: never less
# than any first-use window ending now, and no stored anchor; `reset` runs in back-to-back blocks placed by a reset the
# upstream reported for that window by name (sliding until one is known).
CALENDAR_KINDS = ("day", "week", "month")
WINDOW_KINDS = (*CALENDAR_KINDS, "rolling")
ANCHORS = ("first_use", "reset")
COMBINES = ("sum", "all")
NO_WINDOW_REFILL_TZ = "UTC"      # a reported balance of a pool with no window (no refill named) ends at its midnight
REST_REASON = "no balance left: rests until the pool refills"
REFILL_PAST_S = 300              # an upstream refill at most this far in the past is believed (skew, a late reset)
REFILL_AHEAD_S = 40 * 86400      # an upstream refill further ahead than this is not (a month's cycle plus margin)
REPEAT_QUIET_S = 86400           # a checkpoint this long after the last one of the account rests the base again
MAX_ROLLING_HOURS = REFILL_AHEAD_S // 3600


# ---- the pool's windows: the `<p>.pools` setting ------------------------------------------------------------------
@dataclass(frozen=True)
class Window:
    name: str
    kind: str                    # one of WINDOW_KINDS
    tz: str = "UTC"              # the zone of a calendar window (a rolling one ignores it)
    budget: Mapping[str, int | None] = field(default_factory=dict)   # tier -> units it may spend; None: not bound
    hours: int = 0               # the length of a rolling window
    anchor: str = "first_use"    # where a rolling window starts: one of ANCHORS


@dataclass(frozen=True)
class PoolConfig:
    name: str
    unit: str                    # credits, jobs or uses: what the budget and `account_cost` count
    combine: str = "sum"         # `sum`: the windows' budgets add up; `all`: every window must hold
    windows: tuple[Window, ...] = ()
    drift_alert: int | None = None   # units a reported balance may stray from the computed one unalerted; None: off


def _window_problem(w: Any, where: str) -> str | None:
    if type(w) is not dict:
        return "%s: a window is an object" % where
    unknown = set(w) - {"name", "kind", "tz", "budget", "hours", "anchor"}
    if unknown:
        return "%s: unknown fields %s" % (where, ", ".join(sorted(map(repr, unknown))))
    if type(w.get("name")) is not str or not w["name"]:
        return "%s: name must be a non-empty string" % where
    if w.get("kind") not in WINDOW_KINDS:
        return "%s: kind must be one of %s" % (where, ", ".join(WINDOW_KINDS))
    if w["kind"] in CALENDAR_KINDS or "tz" in w:
        try:
            ZoneInfo(w.get("tz"))     # type: ignore[arg-type]
        except (ZoneInfoNotFoundError, ValueError, TypeError, KeyError):
            return "%s: tz must be an IANA time zone" % where
    if w["kind"] == "rolling":
        hours = w.get("hours")
        if type(hours) is not int or not 1 <= hours <= MAX_ROLLING_HOURS:
            return "%s: a rolling window needs hours, a whole number from 1 to %d" % (where, MAX_ROLLING_HOURS)
        if w.get("anchor", "first_use") not in ANCHORS:
            return "%s: anchor must be one of %s" % (where, ", ".join(ANCHORS))
    elif "hours" in w or "anchor" in w:
        return "%s: hours and anchor are for a rolling window only" % where
    budget = w.get("budget")
    if type(budget) is not dict:
        return "%s: budget is an object of tier -> whole number" % where
    for tier, n in budget.items():
        if tier not in TIERS:
            return "%s: budget tier %r is not one of %s" % (where, tier, ", ".join(TIERS))
        if n is not None and (type(n) is not int or n < 0):
            return "%s: the budget of %s is not a whole number of 0 or more, nor null" % (where, tier)
    return None


def pools_setting(provider: str, pool_specs: Iterable[PoolSpec], default: Mapping[str, Any]) -> Spec:
    """`<provider>.pools`: per pool the way its windows combine and the windows themselves, each with a budget per tier.
    A pool the provider does not declare is refused on a write and dropped on load; a declared pool the stored value
    does not name keeps its default, so a pool added by a later build never loses its rule."""
    names = tuple(p.name for p in pool_specs)
    key = provider + ".pools"

    def check(v: dict) -> None:
        for name, cfg in v.items():
            if name not in names:
                raise ValueError("%r is not a pool of this provider (%s)" % (name, ", ".join(names)))
            if type(cfg) is not dict or set(cfg) - {"combine", "windows"}:
                raise ValueError("%s: an object with combine and windows" % name)
            if cfg.get("combine", "sum") not in COMBINES:
                raise ValueError("%s: combine must be one of %s" % (name, ", ".join(COMBINES)))
            windows = cfg.get("windows", [])
            if type(windows) is not list:
                raise ValueError("%s: windows is a list" % name)
            for i, w in enumerate(windows):
                problem = _window_problem(w, "%s window %d" % (name, i + 1))
                if problem:
                    raise ValueError(problem)
            if len({w["name"] for w in windows}) < len(windows):
                raise ValueError("%s: two windows have one name" % name)

    def upgrade(v: Any) -> Any:
        if type(v) is not dict:
            return v                        # validate refuses it
        stored = {n: cfg for n, cfg in v.items() if n in names}
        retired = [n for n in v if n not in names]
        if retired:
            log.error("stored setting %s: dropped pools the provider does not declare: %s", key,
                      ", ".join(map(repr, retired)))
        return {**default, **stored}

    return Spec(key, dict, dict(default),
                "How each account pool counts its spend: per pool its windows (a calendar day, week or month in a "
                "time zone, or a rolling number of hours) and the units an account may spend in each, per tier (free, "
                "pro, ultra; a tier not named takes free's, null: the window does not bind the tier). A rolling window "
                "starts at the first spend, or (anchor reset) in blocks placed by the reset the upstream reports. The "
                "windows' budgets add up (sum) or must all hold (all). A pool with no window is unknown: it allows. A "
                "balance the upstream reports wins until its own refill.",
                rule=Rule("an object of pool -> {combine: sum|all, windows: [{name, kind: day|week|month|rolling, "
                          "tz (calendar kinds), hours and anchor: first_use|reset (rolling), "
                          "budget: {tier: units or null}}]}", check), on_load=upgrade)


def day_pool(budget: Mapping[str, int], tz: str = "UTC") -> dict[str, Any]:
    """One `<p>.pools` entry: a single calendar-day window in `tz` with `budget` units per tier (the common default)."""
    return {"combine": "sum", "windows": [{"name": "day", "kind": "day", "tz": tz, "budget": dict(budget)}]}


def pool_configs(provider: str, caps: Caps, get_setting: Callable[[str], Any]) -> dict[str, PoolConfig]:
    """The configs of a provider's pools, with each rule read now from `<provider>.pools`."""
    stored = get_setting(provider + ".pools")
    drift = get_setting("alert_pool_drift")
    out = {}
    for p in caps.pools:
        cfg = stored.get(p.name, {})
        out[p.name] = PoolConfig(p.name, p.unit, cfg.get("combine", "sum"), tuple(
            Window(w["name"], w["kind"], w.get("tz", "UTC"), dict(w["budget"]), w.get("hours", 0),
                   w.get("anchor", "first_use"))
            for w in cfg.get("windows", [])), drift)
    return out


# ---- the window arithmetic: pure ----------------------------------------------------------------------------------
Span = tuple[datetime, datetime]


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, zone).astimezone(UTC)


def _cycle_day(year: int, month: int, day: int) -> date:
    """Day `day` of the month, clamped to its length (a cycle from the 31st renews on the last day of February)."""
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return date(year, month, min(day, monthrange(year, month)[1]))


def _calendar_span(w: Window, at: datetime, cycle_start: date | None) -> Span:
    """The calendar window `at` is in: from a local midnight of its zone to the one that ends it, so a day across a
    change of the clocks lasts 23 or 25 hours."""
    zone = ZoneInfo(w.tz)
    d = at.astimezone(zone).date()
    if w.kind == "day":
        first, after = d, d + timedelta(days=1)
    elif w.kind == "week":
        first = d - timedelta(days=d.weekday())
        after = first + timedelta(days=7)
    else:
        anchor = cycle_start.day if cycle_start is not None else 1
        back = 0 if _cycle_day(d.year, d.month, anchor) <= d else 1
        first = _cycle_day(d.year, d.month - back, anchor)
        after = _cycle_day(d.year, d.month - back + 1, anchor)
    return _midnight(first, zone), _midnight(after, zone)


def _block(w: Window, at: datetime, anchor: datetime) -> Span:
    """The block of a `reset` window `at` is in: blocks of `w.hours` back to back, one of them ending at `anchor`."""
    length = timedelta(hours=w.hours)
    k = floor((at - anchor) / length)
    return anchor + k * length, anchor + (k + 1) * length


def window_span(w: Window, at: datetime, cycle_start: date | None = None, anchor: datetime | None = None
                ) -> Span | None:
    """The window `at` is in when it has fixed bounds: a calendar one, or the block of a `reset` window placed by
    `anchor`. None for a rolling window that slides (a `first_use` one, or a `reset` one with no reset known)."""
    if w.kind != "rolling":
        return _calendar_span(w, at, cycle_start)
    return _block(w, at, anchor) if anchor is not None and w.anchor == "reset" else None


def _anchor(w: Window, reported: "Reported | None") -> datetime | None:
    """The reset that places a `reset` window's blocks: the refill the upstream reported, only when it named this
    window (`window_span` places blocks for a `reset` window only). The stored reset was believed on its way in
    (`believed_refill`). Any other refill anchors nothing."""
    if reported is None or reported.window != w.name:
        return None
    return reported.refill_at


def _refill(w: Window, now: datetime, cycle_start: date | None, anchor: datetime | None,
            charges: Sequence[tuple[datetime, int]]) -> datetime:
    """When the window `now` is in refills: the end of its fixed bounds; for a sliding window, a length after the
    oldest charge it still holds (a length after now when it holds none)."""
    span = window_span(w, now, cycle_start, anchor)
    if span is not None:
        return span[1]
    length = timedelta(hours=w.hours)
    return min((at for at, _ in charges if now - length < at <= now), default=now) + length


@dataclass(frozen=True)
class Reported:
    """What the upstream last said the account has left in a pool, when, the refill it named (None: the pool's) and
    the window that refill ends (None: none named)."""
    left: int
    at: datetime
    refill_at: datetime | None = None
    window: str | None = None


def _refill_of_report(cfg: PoolConfig, reported: Reported, charges: Sequence[tuple[datetime, int]],
                      cycle_start: date | None) -> datetime:
    """When a reported balance stops being the account's number: the refill the upstream named, else the pool's next
    window boundary after the report."""
    if reported.refill_at is not None:
        return reported.refill_at
    return next_refill(cfg, reported.at, charges, cycle_start)


def next_refill(cfg: PoolConfig, now: datetime, charges: Sequence[tuple[datetime, int]] = (),
                cycle_start: date | None = None) -> datetime:
    """When the pool next refills: the earliest window boundary after `now` (a sliding window's from the charges it
    holds); for a pool with no window, the next midnight in `NO_WINDOW_REFILL_TZ` (a reported zero must end some
    day)."""
    if not cfg.windows:
        return _calendar_span(Window("none", "day", NO_WINDOW_REFILL_TZ), now, None)[1]
    return min(_refill(w, now, cycle_start, None, charges) for w in cfg.windows)


def believed_refill(refill: datetime | None, now: datetime, check_s: float) -> datetime | None:
    """The refill an upstream named, if it is believable: from `REFILL_PAST_S` ago to `REFILL_AHEAD_S` ahead. One a
    little in the past (a window that should have reset but still reads empty) holds until the next check reads the
    account again; one outside the range is dropped (None: the pool's own window decides)."""
    if refill is None:
        return None
    if not now - timedelta(seconds=REFILL_PAST_S) <= refill <= now + timedelta(seconds=REFILL_AHEAD_S):
        return None
    return refill if refill > now else now + timedelta(seconds=check_s)


def _tier_budgets(cfg: PoolConfig, tier: str) -> list[int | None]:
    """Each window's budget for the tier: a tier the window does not name takes free's (never unlimited); None when
    neither is named or the tier's is null, and that window does not bind the account."""
    return [w.budget[tier] if tier in w.budget else w.budget.get(DEFAULT_TIER) for w in cfg.windows]


def _total(cfg: PoolConfig, budgets: Iterable[int | None]) -> int | None:
    """What an account may spend across the windows that bind it: their sum, or under `all` the smallest."""
    known = [b for b in budgets if b is not None]
    return None if not known else min(known) if cfg.combine == "all" else sum(known)


def ceiling(cfg: PoolConfig) -> int | None:
    """The most an account can ever hold in this pool: the largest total of any tier; None when no tier has one."""
    totals = [t for t in (_total(cfg, _tier_budgets(cfg, tier)) for tier in TIERS) if t is not None]
    return max(totals) if totals else None


@dataclass(frozen=True)
class WindowState:
    name: str
    kind: str
    budget: int | None           # None: no budget for the account's tier (the window does not bind it)
    spent: int
    refill_at: datetime


@dataclass(frozen=True)
class Standing:
    """Where a pool of an account stands: `left` (None = unknown), its `source` (`reported`, `computed` or `unknown`),
    each window's budget and spend, `budget`, what the account may spend across them (None = unknown), and
    `computed`: what the windows alone leave, whatever was reported (None = unknown)."""
    left: int | None
    source: str
    windows: tuple[WindowState, ...] = ()
    budget: int | None = None
    computed: int | None = None

    @property
    def drift(self) -> int | None:
        """The reported balance less the computed one; None unless a report is the balance and the windows know."""
        return self.left - self.computed if self.source == "reported" and self.computed is not None else None


def _spent(cfg: PoolConfig, budgets: Sequence[int | None], anchors: Sequence[datetime | None],
           charges: Sequence[tuple[datetime, int]], now: datetime, cycle_start: date | None) -> list[int]:
    """What each window holds at `now`: a window with fixed bounds the charges inside them, a sliding one the charges
    of the last `hours` up to now (a superset of any first-use window ending now: never less). Under `all` every
    window counts every charge it holds. Under `sum` each charge fills, in its own time, the binding window that
    refills first (a day's credits before the month's), the next with what is left over, the last one taking any
    excess; a window that does not bind counts every charge it holds."""
    lengths = [timedelta(hours=w.hours) for w in cfg.windows]
    opened = [window_span(w, now, cycle_start, a) for w, a in zip(cfg.windows, anchors, strict=True)]

    def holds_now(i: int, at: datetime) -> bool:
        span = opened[i]
        return now - lengths[i] < at <= now if span is None else span[0] <= at < span[1]

    plain = [sum(n for at, n in charges if holds_now(i, at)) for i in range(len(cfg.windows))]
    if cfg.combine == "all":
        return plain
    in_span: dict[tuple[int, Span], int] = {}                  # a window with fixed bounds: units per span
    slid: list[list[tuple[datetime, int]]] = [[] for _ in cfg.windows]   # a sliding window: (at, units) per charge
    for at, amount in charges:
        here = []
        for i, w in enumerate(cfg.windows):
            if budgets[i] is None:
                continue
            span = window_span(w, at, cycle_start, anchors[i])
            if span is not None:
                here.append((span[1], i, span, in_span.get((i, span), 0)))
            else:
                here.append((at + lengths[i], i, None, sum(n for t, n in slid[i] if at - lengths[i] < t)))
        here.sort(key=lambda h: (h[0], h[1]))
        for k, (_, i, span, used) in enumerate(here):
            take = amount if k == len(here) - 1 else min(amount, max(0, budgets[i] - used))   # type: ignore[operator]
            if span is not None:
                in_span[(i, span)] = used + take
            else:
                slid[i].append((at, take))
            amount -= take
    out = []
    for i in range(len(cfg.windows)):
        span = opened[i]
        if budgets[i] is None:
            out.append(plain[i])
        elif span is None:
            out.append(sum(n for t, n in slid[i] if now - lengths[i] < t))
        else:
            out.append(in_span.get((i, span), 0))
    return out


def standing(cfg: PoolConfig, tier: str | None, charges: Sequence[tuple[datetime, int]], now: datetime,
             reported: Reported | None = None, cycle_start: date | None = None) -> Standing:
    """The pool's state at `now`. `charges` are the unrefunded `(at, amount)` of the account in the pool; `cycle_start`
    is the account's paid cycle (the day a `month` window starts on).

    A reported balance wins while its refill has not come (`now` is before it): it is `reported.left` less the
    unrefunded charges made after the report. Otherwise the pool is computed from the windows that bind the tier:
    under `sum` the room left in each added up, under `all` the smallest room. No binding window is unknown."""
    tier = tier or DEFAULT_TIER
    past = sorted((at, n) for at, n in charges if at <= now)
    anchors = [_anchor(w, reported) for w in cfg.windows]
    budgets = _tier_budgets(cfg, tier)
    spent = _spent(cfg, budgets, anchors, past, now, cycle_start)
    states = tuple(WindowState(w.name, w.kind, budgets[i], spent[i], _refill(w, now, cycle_start, anchors[i], past))
                   for i, w in enumerate(cfg.windows))
    budget = _total(cfg, budgets)
    rooms = [s.budget - s.spent for s in states if s.budget is not None]
    computed = None if not rooms else min(rooms) if cfg.combine == "all" else sum(max(0, room) for room in rooms)
    if reported is not None and now < _refill_of_report(cfg, reported, charges, cycle_start):
        return Standing(reported.left - sum(amount for at, amount in charges if at > reported.at), "reported", states,
                        budget, computed)
    if computed is None:
        return Standing(None, "unknown", states)
    return Standing(computed, "computed", states, budget, computed)


def left_of(cfg: PoolConfig, tier: str | None, charges: Sequence[tuple[datetime, int]], now: datetime,
            reported: Reported | None = None, cycle_start: date | None = None) -> int | None:
    return standing(cfg, tier, charges, now, reported, cycle_start).left


def _since(cfg: PoolConfig, now: datetime, reported: Reported | None, cycle_start: date | None) -> datetime:
    """The oldest charge `standing` needs. What a window holds now starts at the earliest of: a calendar window's
    start, a rolling window's `hours` back, the report. Under `sum` a charge is placed against what each window held
    in its own time, so the read reaches back further: to the start of the calendar window that oldest moment falls
    in, and a rolling window's `hours` before it."""
    base = min([now, *(_calendar_span(w, now, cycle_start)[0] if w.kind != "rolling"
                       else now - timedelta(hours=w.hours) for w in cfg.windows),
                *([reported.at] if reported is not None else [])])
    if cfg.combine == "all":
        return base
    starts = [base, *(_calendar_span(w, base, cycle_start)[0] for w in cfg.windows if w.kind != "rolling")]
    return min(starts) - timedelta(hours=max((w.hours for w in cfg.windows if w.kind == "rolling"), default=0))


# ---- the database side --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Capacity:
    account_id: object
    pool: str
    left_n: int | None          # the effective balance: reported, else computed from the charges; None = unknown
    rest_until: datetime | None
    rest_reason: str | None


_POOL_ROW = ("SELECT p.reported_left, p.reported_at, p.refill_at, p.rest_until, p.rest_reason, a.tier, "
             "clock_timestamp(), a.cycle_start, p.refill_window FROM account_pools p JOIN accounts a "
             "ON a.id = p.account_id "
             "WHERE p.account_id = %s AND p.pool = %s FOR UPDATE OF p")
_CHARGES = ("SELECT at, amount FROM account_charges WHERE account_id = %s AND pool = %s AND refunded_at IS NULL "
            "AND at >= %s ORDER BY at")
_INSERT_ROW = "INSERT INTO account_pools (account_id, pool) VALUES (%s, %s) ON CONFLICT (account_id, pool) DO NOTHING"


async def ensure_rows(conn: AsyncConnection, account_id, pools: Mapping[str, PoolConfig]) -> None:
    """Every declared pool's row of the account exists. Call it after `lock_account`: an insert takes a key share on the
    account row, which a delete (FOR UPDATE) would otherwise race on. Rows that exist are not touched."""
    for name in sorted(pools):
        await conn.execute(_INSERT_ROW, (account_id, name))


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _window_tag(window: str, refill_at: datetime) -> str:
    """`refill_window` as stored: the window with the refill it belongs to (`5h@<refill epoch in microseconds>`), so
    a build that writes `refill_at` without the tag (an older one after a rollback) leaves a tag that no longer
    matches."""
    return "%s@%d" % (window, (refill_at - _EPOCH) // timedelta(microseconds=1))


def _tagged_window(tag: str | None, refill_at: datetime | None) -> str | None:
    """The window a stored tag names, only while it belongs to the stored refill; anything else names none."""
    if tag is None or refill_at is None:
        return None
    window, _, _ = tag.rpartition("@")
    return window if window and tag == _window_tag(window, refill_at) else None


def _reported(left: int | None, at: datetime | None, refill_at: datetime | None, tag: str | None
              ) -> Reported | None:
    return None if left is None or at is None else Reported(left, at, refill_at, _tagged_window(tag, refill_at))


async def _lock(conn: AsyncConnection, account_id, pool: str, *, skip_locked: bool) -> tuple | None:
    """Lock the account row first, then create the pool row when missing and lock it. Returns the pool row (reported
    balance, rest, the account's tier, the database clock, the account's cycle start and the window the reported
    refill ends) or None when `skip_locked`
    and another transaction holds the account."""
    if not await lock_account(conn, account_id, skip_locked=skip_locked) and skip_locked:
        return None
    row = await (await conn.execute(_POOL_ROW + (" SKIP LOCKED" if skip_locked else ""), (account_id, pool))).fetchone()
    if row is None:
        await conn.execute(_INSERT_ROW, (account_id, pool))   # nobody else inserts: this transaction holds the account
        row = await (await conn.execute(_POOL_ROW + (" SKIP LOCKED" if skip_locked else ""),
                                        (account_id, pool))).fetchone()
    return row


async def lock_capacity(conn: AsyncConnection, account_id, cfg: PoolConfig) -> Capacity | None:
    """For the claim: lock the candidate's account row `SKIP LOCKED`, then its pool row, and read what the next job can
    spend: the reported balance, or the window's budget for the account's tier less the unrefunded charges. None means
    another transaction holds the account; pick another one. The account stays locked for the claim."""
    require_tx(conn)
    row = await _lock(conn, account_id, cfg.name, skip_locked=True)
    if row is None:
        return None
    reported, tier, now, cycle = _reported(row[0], row[1], row[2], row[8]), row[5], row[6], row[7]
    charges = await (await conn.execute(_CHARGES, (account_id, cfg.name, _since(cfg, now, reported, cycle)))).fetchall()
    return Capacity(account_id, cfg.name, left_of(cfg, tier, charges, now, reported, cycle), row[3], row[4])


def figure_until(cfg: PoolConfig, now: datetime, reported: Reported | None,
                 charges: Sequence[tuple[datetime, int]] = (), cycle_start: date | None = None) -> datetime:
    """When the balance `standing` gives at `now` stops holding: the refill of the report it came from, else the pool's
    next window boundary. A balance read before this moment and one read after it are not of the same window."""
    report_ends = None if reported is None else _refill_of_report(cfg, reported, charges, cycle_start)
    return report_ends if report_ends is not None and now < report_ends else next_refill(cfg, now, charges, cycle_start)


async def left_map(conn: AsyncConnection, account_id, pools: Mapping[str, PoolConfig]
                   ) -> tuple[dict[str, int | None], dict[str, datetime]]:
    """Each pool's effective balance as an engine is told it (`AccountView.capacity`), and when each stops holding
    (`AccountView.capacity_until`): a plain read, no lock."""
    out: dict[str, int | None] = {}
    until: dict[str, datetime] = {}
    now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
    row = await (await conn.execute("SELECT tier, cycle_start FROM accounts WHERE id = %s", (account_id,))).fetchone()
    tier, cycle = (None, None) if row is None else row
    rows = {r[0]: r[1:] for r in await (await conn.execute(
        "SELECT pool, reported_left, reported_at, refill_at, refill_window FROM account_pools WHERE account_id = %s",
        (account_id,))).fetchall()}
    for name, cfg in pools.items():
        reported = _reported(*rows[name]) if name in rows else None
        charges = await (await conn.execute(_CHARGES, (account_id, name, _since(cfg, now, reported, cycle)))).fetchall()
        out[name] = left_of(cfg, tier, charges, now, reported, cycle)
        until[name] = figure_until(cfg, now, reported, charges, cycle)
    return out, until


@dataclass(frozen=True)
class PoolState:
    """One pool of one account as the admin shows it: its standing now, what the upstream last reported (None: nothing)
    and its rest."""
    standing: Standing
    reported: Reported | None
    rest_until: datetime | None
    rest_reason: str | None


async def pool_states(conn: AsyncConnection, accounts: Mapping[Any, tuple[str | None, Mapping[str, PoolConfig]]]
                      ) -> tuple[datetime, dict[tuple[Any, str], PoolState]]:
    """The state of every pool of the given accounts at the database clock, read without a lock: the same arithmetic as
    `lock_capacity` (so the admin shows what the next claim will see). `accounts` maps an account id to its tier and
    its provider's pool configs. A pool the account has no row for yet is computed from its charges alone. Returns
    the clock and `{(account id, pool): state}`."""
    now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
    ids = list(accounts)
    rows = {(a, p): r for a, p, *r in await (await conn.execute(
        "SELECT account_id, pool, reported_left, reported_at, refill_at, rest_until, rest_reason, refill_window "
        "FROM account_pools WHERE account_id = ANY(%s)", (ids,))).fetchall()}
    reports = {key: _reported(r[0], r[1], r[2], r[5]) for key, r in rows.items()}
    cycles = dict(await (await conn.execute("SELECT id, cycle_start FROM accounts WHERE id = ANY(%s)",
                                            (ids,))).fetchall())
    since = min((_since(cfg, now, reports.get((a, name)), cycles.get(a)) for a, (_, cfgs) in accounts.items()
                 for name, cfg in cfgs.items()), default=now)
    charges: dict[tuple[Any, str], list[tuple[datetime, int]]] = {}
    for a, p, at, amount in await (await conn.execute(
            "SELECT account_id, pool, at, amount FROM account_charges WHERE account_id = ANY(%s) "
            "AND refunded_at IS NULL AND at >= %s ORDER BY at", (ids, since))).fetchall():
        charges.setdefault((a, p), []).append((at, amount))
    out = {}
    for a, (tier, cfgs) in accounts.items():
        for name, cfg in cfgs.items():
            row = rows.get((a, name), (None, None, None, None, None))
            out[(a, name)] = PoolState(standing(cfg, tier, charges.get((a, name), []), now, reports.get((a, name)),
                                                cycles.get(a)), reports.get((a, name)), row[3], row[4])
    return now, out


async def reserved(conn: AsyncConnection, account_id, pool: str) -> int:
    """The `account_cost` of jobs holding a job lease on the account in this pool that are not yet charged to it.
    Expired but unreaped leases count: the job may still be sending."""
    return (await (await conn.execute(
        "SELECT COALESCE(sum(j.account_cost), 0) FROM leases l JOIN jobs j ON j.id = l.job_id "
        "WHERE l.account_id = %s AND l.purpose = 'job' AND l.pool = %s "
        "AND NOT EXISTS (SELECT 1 FROM account_charges c WHERE c.job_id = j.id AND c.pool = l.pool)",
        (account_id, pool))).fetchone())[0]


def can_afford(left_n: int | None, reserved_n: int, cost: int) -> bool:
    """I6: unknown allows; otherwise what is left after the reservations must cover the cost."""
    return left_n is None or left_n - reserved_n >= cost


# ---- pool accounting -------------------------------------------------------------------------------------------
async def charge_account(conn: AsyncConnection, account_id, job_id, cfg: PoolConfig, amount: int) -> bool:
    """Record that the account took this job's order: one `account_charges` row for `amount` (the job's `account_cost`),
    stamped by the database clock. Once per (job, pool): False when the job was already charged. Nothing else moves:
    what is left is computed from the charges."""
    require_tx(conn)
    if amount < 0:
        raise ValueError("amount must be >= 0")
    await _lock(conn, account_id, cfg.name, skip_locked=False)
    row = await (await conn.execute(
        "INSERT INTO account_charges (account_id, job_id, pool, amount) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (job_id, pool) DO NOTHING RETURNING id", (account_id, job_id, cfg.name, amount))).fetchone()
    return row is not None


async def refund_job(conn: AsyncConnection, job_id, pools: Mapping[str, PoolConfig]) -> list[tuple[str, int]]:
    """Give back the account charges of a failed job: the exact amount, once per charge (`refunded_at`). It is called
    only when upstream did not complete the order (`jobs.fail`: no outputs recorded), so a refunded charge is one the
    account never spent: the windows are worked out as if it had not been made, and a later charge it had pushed into
    the month falls back into the day's credits, as upstream itself would have taken it. Returns the
    (pool, amount) pairs refunded. Only a charge exists for a job whose order was taken, so nothing else is checked
    here."""
    require_tx(conn)
    charges = await (await conn.execute(
        "SELECT id, account_id, pool FROM account_charges WHERE job_id = %s AND refunded_at IS NULL "
        "ORDER BY account_id, pool", (job_id,))).fetchall()
    await failpoints.hit("pool.refund.listed")
    done = []
    for charge_id, account_id, pool in charges:
        await _lock(conn, account_id, pool, skip_locked=False)   # serialises refund and charge
        got = await (await conn.execute(
            "UPDATE account_charges SET refunded_at = clock_timestamp() WHERE id = %s AND refunded_at IS NULL "
            "RETURNING amount", (charge_id,))).fetchone()
        if got is not None:
            done.append((pool, got[0]))
    return done


# ---- account effects --------------------------------------------------------------------------------------------
def _reason(text: str) -> str:
    return redact_log(text)[:200]


async def apply_effects(conn: AsyncConnection, account_id, effects: Iterable[AccountEffect], caps: Caps,
                        pools: Mapping[str, PoolConfig], *, job_id=None) -> None:
    """Apply a result's account effects in the caller's transaction, with the job transition they belong to.

    - `Rest` and `Cooldown` never shorten a longer one (GREATEST); there is no rank.
    - `Suspect` adds a strike (not when it comes within its `min_gap_s` of the last counted one, nor on a `dead`
      account); at
      `caps.suspect_threshold` the account dies with the strike effect's reason. `Success` clears the counter and the
      time of the last counted strike. A `Cooldown` writes its reason to `last_error`, except beside a `Suspect` or a
      `ProxyBad`: their reason is kept.
    - `Dead` and `Revive` never change a `disabled` account: a person's decision wins over a check.
    - `Balance` records the pool's reported balance and the refill with the window it names; a balance above 0 ends
      that pool's rest, a balance of 0 rests the pool until the refill the upstream named, else the pool's next window
      boundary.
      A reported balance far from the computed one raises a `pool_drift` alert (`_drift_alert`).
    - `Tier` writes the account's tier, marks it read now, and its cycle start when named (else the stored one stays).
    The account row is locked first, whatever the effects touch. The result's error (a strike's, a bad proxy's or a
    death's reason, else a cooldown's) is kept in the account's errors with `job_id`, the job it came from."""
    require_tx(conn)
    exists = await lock_account(conn, account_id)
    effects = tuple(effects)
    # a strike's or a bad proxy's reason is the cause; a cooldown in the same result is only what follows from it
    cause = any(isinstance(e, (Suspect, ProxyBad)) for e in effects)
    error = next((e.reason for e in effects if isinstance(e, (Suspect, ProxyBad, Dead))),
                 next((e.reason for e in effects if isinstance(e, Cooldown)), None))
    if error and exists:
        await history.record_error(conn, account_id, error, job_id)
    for e in effects:
        if isinstance(e, Balance):
            cfg = _cfg(pools, e.pool)
            row = await _lock(conn, account_id, cfg.name, skip_locked=False)
            assert row is not None
            refill = believed_refill(e.refill_at, row[6], caps.check_interval_s)
            # the window a refill ends anchors that window only when the refill stands as the upstream named it
            named = e.window and refill is not None and refill == e.refill_at
            window = _window_tag(e.window, refill) if named else None
            until = refill
            if e.left_n <= 0 and refill is None:
                since = _since(cfg, row[6], None, row[7])
                charges = await (await conn.execute(_CHARGES, (account_id, cfg.name, since))).fetchall()
                until = next_refill(cfg, row[6], charges, row[7])
            await _drift_alert(conn, account_id, cfg, row, e.left_n)
            await conn.execute(
                "UPDATE account_pools SET reported_left = %(n)s, reported_at = clock_timestamp(), refill_at = %(r)s, "
                "refill_window = %(w)s, "
                "rest_reason = CASE WHEN %(n)s > 0 THEN NULL WHEN %(u)s > COALESCE(rest_until, '-infinity') "
                "THEN %(why)s ELSE rest_reason END, "
                "rest_until = CASE WHEN %(n)s > 0 THEN NULL ELSE GREATEST(rest_until, %(u)s) END "
                "WHERE account_id = %(a)s AND pool = %(p)s",
                {"n": e.left_n, "r": refill, "w": window, "u": until, "why": REST_REASON, "a": account_id,
                 "p": e.pool})
        elif isinstance(e, Rest):
            cfg = _cfg(pools, e.pool)
            await _lock(conn, account_id, cfg.name, skip_locked=False)
            await conn.execute(
                "UPDATE account_pools SET "
                "rest_reason = CASE WHEN %(u)s > COALESCE(rest_until, '-infinity') THEN %(r)s ELSE rest_reason END, "
                "rest_until = GREATEST(rest_until, %(u)s) WHERE account_id = %(a)s AND pool = %(p)s",
                {"u": e.until, "r": _reason(e.reason), "a": account_id, "p": e.pool})
        elif isinstance(e, Cooldown):
            await _account(conn, account_id,
                           "cooldown_reason = CASE WHEN %(u)s > COALESCE(cooldown_until, '-infinity') THEN %(r)s "
                           "ELSE cooldown_reason END, cooldown_until = GREATEST(cooldown_until, %(u)s), "
                           "check_due_at = CASE WHEN %(rc)s AND state <> 'dead' THEN %(u)s ELSE check_due_at END, "
                           "last_error = CASE WHEN %(s)s THEN last_error ELSE %(r)s END",
                           u=e.until, r=_reason(e.reason), rc=e.recheck, s=cause)
        elif isinstance(e, Suspect):
            # The account row is locked (above), so reading whether the strike counts and writing it are one step.
            # A dead account counts no strike: it is already out, and its rare rechecks must not climb the count.
            counted = (await (await conn.execute(
                "SELECT state <> 'dead' AND (strike_at IS NULL OR %(g)s <= 0 "
                "OR strike_at <= clock_timestamp() - make_interval(secs => %(g)s)) FROM accounts WHERE id = %(a)s",
                {"g": e.min_gap_s, "a": account_id})).fetchone() or (False,))[0]
            strikes = (await (await conn.execute(
                "UPDATE accounts SET strikes = strikes + %(n)s, "
                "strike_at = CASE WHEN %(c)s THEN clock_timestamp() ELSE strike_at END, "
                "last_error = %(r)s, updated_at = clock_timestamp() WHERE id = %(a)s RETURNING strikes",
                {"n": 1 if counted else 0, "c": counted, "r": _reason(e.reason), "a": account_id})).fetchone())[0]
            # Only a strike that counted can reach the threshold: a doubt inside its gap never kills.
            if counted and strikes >= caps.suspect_threshold:
                await _die(conn, account_id, caps.strike_effect.reason)
        elif isinstance(e, Dead):
            await _die(conn, account_id, e.reason)
        elif isinstance(e, Revive):
            await _account(conn, account_id, "state = 'available', state_reason = NULL", where="AND state = 'dead'")
        elif isinstance(e, Tier):
            await _account(conn, account_id, "tier = %(t)s, tier_read_at = clock_timestamp(), "
                           "cycle_start = COALESCE(%(c)s, cycle_start)", t=e.tier, c=e.cycle_start)
        elif isinstance(e, Success):
            await _account(conn, account_id, "strikes = 0, strike_at = NULL, last_error = NULL")
        elif isinstance(e, ProxyBad):
            await _account(conn, account_id, "proxy_ok = false, proxy_checked_at = clock_timestamp(), "
                           "last_error = %(r)s", r=_reason(e.reason))
        else:
            raise TypeError("unknown account effect %r" % (e,))


# One per account, pool and window: a drift already raised since the pool's first-refilling window began is not
# raised again.
_DRIFT_ALERT = (
    "INSERT INTO billing_alerts (alert, account_id, scope, provider, expected, actual, detail) "
    "SELECT 'pool_drift', a.id, %(p)s, a.provider, %(c)s, %(n)s, %(d)s FROM accounts a WHERE a.id = %(a)s "
    "AND NOT EXISTS (SELECT 1 FROM billing_alerts b WHERE b.alert = 'pool_drift' AND b.account_id = %(a)s "
    "AND b.scope = %(p)s AND b.at >= %(since)s)")


async def _drift_alert(conn: AsyncConnection, account_id, cfg: PoolConfig, row: tuple, reported_left: int) -> None:
    """A reported balance more than `cfg.drift_alert` units from the computed one (the window's budget for the
    account's tier less its unrefunded charges) raises a `pool_drift` alert. Nothing when the pool cannot be computed
    (no window, no budget for the tier) or the alert is off. In a savepoint of its own: a failing alert is logged and
    never costs the result it rides on. The account row is locked, so the once-per-window check cannot race."""
    if cfg.drift_alert is None or not cfg.windows:
        return
    now, cycle_start = row[6], row[7]
    since = _since(cfg, now, None, cycle_start)
    # the window that refills first: its current start (a calendar window's, or a rolling one's `hours` back)
    window_start = max(_calendar_span(w, now, cycle_start)[0] if w.kind != "rolling"
                       else now - timedelta(hours=w.hours) for w in cfg.windows)
    try:
        async with conn.transaction():
            charges = await (await conn.execute(_CHARGES, (account_id, cfg.name, since))).fetchall()
            computed = standing(cfg, row[5], charges, now, None, cycle_start).computed
            if computed is None or abs(reported_left - computed) <= cfg.drift_alert:
                return
            await conn.execute(_DRIFT_ALERT, {
                "a": account_id, "p": cfg.name, "c": computed, "n": reported_left, "since": window_start,
                "d": "the upstream reported %d %s left, the relay computed %d" % (reported_left, cfg.unit, computed)})
    except Exception as exc:     # noqa: BLE001 - an alert never costs its result
        log.error("pool drift alert not stored: account=%s pool=%s error=%s sqlstate=%s", account_id, cfg.name,
                  type(exc).__name__, getattr(exc, "sqlstate", None) or "unknown")


def _cfg(pools: Mapping[str, PoolConfig], name: str) -> PoolConfig:
    try:
        return pools[name]
    except KeyError:
        raise ValueError("an effect names the pool %r, which the provider does not declare" % name) from None


async def _account(conn: AsyncConnection, account_id, assignments: str, where: str = "", **params) -> int:
    """Update the account row; returns the rows changed (0 when there is no such account or `where` excludes it)."""
    return (await conn.execute("UPDATE accounts SET " + assignments + ", updated_at = clock_timestamp() "
                               "WHERE id = %(id)s " + where, {"id": account_id, **params})).rowcount


async def _die(conn: AsyncConnection, account_id, reason: str) -> None:
    await _account(conn, account_id, "state = 'dead', state_reason = %(r)s, last_error = %(r)s",
                   where="AND state <> 'disabled'", r=_reason(reason))


async def wake_account(conn: AsyncConnection, account_id) -> bool:
    """The admin wake: clear the account cooldown, every pool rest and the strike count with the time of its last
    strike (a woken account starts fresh, so an enabled one does not die on its next doubt). False when there is no
    such account."""
    require_tx(conn)
    if not await lock_account(conn, account_id):
        return False
    await failpoints.hit("pool.wake.account_locked")
    await conn.execute("UPDATE account_pools SET rest_until = NULL, rest_reason = NULL WHERE account_id = %s",
                       (account_id,))
    return await _account(conn, account_id,
                          "cooldown_until = NULL, cooldown_reason = NULL, strikes = 0, strike_at = NULL") == 1


class RepeatRest:
    """How long an account rests after an upstream's bot checkpoint: `base_s` the first time, doubled by each repeat
    that comes less than `REPEAT_QUIET_S` after the account's last one, never more than `max_s` (nor less than
    `base_s`). A checkpoint is never a strike and never ends the account; only the rest grows.
    ponytail: in this process; a restarted worker starts the account again from `base_s` (the database would hold the
    streak if one rest too short ever matters)."""

    def __init__(self, clock: Callable[[], float] = monotonic):
        self._seen: dict[Any, tuple[int, float]] = {}
        self.clock = clock

    def next_s(self, account_id: Any, base_s: int, max_s: int) -> int:
        now = self.clock()
        count, last = self._seen.get(account_id, (0, None))
        count = count + 1 if last is not None and now - last < REPEAT_QUIET_S else 1
        self._seen[account_id] = (count, now)
        return max(base_s, min(max_s, base_s * 2 ** min(count - 1, 20)))
