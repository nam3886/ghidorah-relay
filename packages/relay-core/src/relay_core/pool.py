"""Account pools: capacity, rests and cooldowns, strikes, and pool accounting
(invariants I6, I7, I29).

Pool accounting counts `account_cost` (what an order costs the upstream account), never the price a client pays.
`left_n IS NULL` means unknown, and unknown allows. A reported balance always beats a counted one. Every write that
spans rows takes the caller's transaction (`require_tx`). The ACCOUNT ROW is locked before any capacity row of the
account (`lock_account`; after the job and lease rows); capacity rows are then locked or
created in any order, because everything is already serialised per account. A claim takes the account row
`SKIP LOCKED` and never waits.

No provider is named here: a pool's timezone and budget arrive in a `PoolConfig` built from the provider's Caps.
"""

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from relay_core import failpoints
from relay_core.db import lock_account
from relay_core.providers import (
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
)
from relay_core.redact import redact_log
from relay_core.txguard import require_tx

log = logging.getLogger(__name__)

# The pool's calendar day, read from the database clock inside the statement, in the pool's timezone.
_TODAY = "(clock_timestamp() AT TIME ZONE %(tz)s)::date"


@dataclass(frozen=True)
class PoolConfig:
    name: str
    tz: str                 # a spec constant
    budget: int | None      # the live daily budget setting; None = no budget (left_n stays unknown)


def pool_configs(caps: Caps, get_setting: Callable[[str], int | None]) -> dict[str, PoolConfig]:
    """The configs of a provider's pools, with each budget read now from the settings."""
    return {p.name: PoolConfig(p.name, p.tz, None if p.budget_setting is None else get_setting(p.budget_setting))
            for p in caps.pools}


def next_pool_reset(spec: PoolSpec | PoolConfig, at: datetime) -> datetime:
    """The next local midnight of the pool's timezone after `at`: when an out-of-credit rest should end."""
    zone = ZoneInfo(spec.tz)
    local = at.astimezone(zone)
    nxt = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone)
    return nxt.astimezone(at.tzinfo)


@dataclass(frozen=True)
class Capacity:
    account_id: object
    pool: str
    left_n: int | None
    source: str
    day_anchor: date | None
    rest_until: datetime | None
    rest_reason: str | None


_COLS = "account_id, pool, left_n, source, day_anchor, rest_until, rest_reason"


async def today(conn: AsyncConnection, tz: str) -> date:
    return (await (await conn.execute("SELECT " + _TODAY, {"tz": tz})).fetchone())[0]


_INSERT_ROW = ("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) "
               "VALUES (%(a)s, %(p)s, %(b)s, 'reset', " + _TODAY + ") ON CONFLICT (account_id, pool) DO NOTHING")


async def ensure_rows(conn: AsyncConnection, account_id, pools: Mapping[str, PoolConfig]) -> None:
    """Every declared pool's capacity row of the account exists. Call it after `lock_account`: an insert takes a key
    share on the account row, which a delete (FOR UPDATE) would otherwise race. Rows that exist are not touched."""
    for name in sorted(pools):
        cfg = pools[name]
        await conn.execute(_INSERT_ROW, {"a": account_id, "p": name, "b": cfg.budget, "tz": cfg.tz})


async def _lock(conn: AsyncConnection, account_id, cfg: PoolConfig, *, skip_locked: bool) -> Capacity | None:
    """Lock the account row first, then create the capacity row when missing, lock it, and roll it to today when its
    day is over: `left_n` goes back to the budget (source `reset`), or to unknown when the pool has no budget. None
    when `skip_locked` and another transaction holds the account (or, a drift pass, the row)."""
    if not await lock_account(conn, account_id, skip_locked=skip_locked) and skip_locked:
        return None
    params = {"a": account_id, "p": cfg.name, "b": cfg.budget, "tz": cfg.tz}
    select = ("SELECT " + _COLS + ", day_anchor IS DISTINCT FROM " + _TODAY + " FROM account_capacity "
              "WHERE account_id = %(a)s AND pool = %(p)s FOR UPDATE" + (" SKIP LOCKED" if skip_locked else ""))
    row = await (await conn.execute(select, params)).fetchone()
    if row is None:
        await conn.execute(_INSERT_ROW, params)         # nobody else inserts: this transaction holds the account
        row = await (await conn.execute(select, params)).fetchone()
    if row is None:
        return None
    if row[-1]:
        row = await (await conn.execute(
            "UPDATE account_capacity SET left_n = %(b)s, source = 'reset', known_at = clock_timestamp(), "
            "day_anchor = " + _TODAY + " WHERE account_id = %(a)s AND pool = %(p)s RETURNING " + _COLS + ", false",
            params)).fetchone()
    return Capacity(*row[:-1])


async def lock_capacity(conn: AsyncConnection, account_id, cfg: PoolConfig) -> Capacity | None:
    """For the claim: lock the candidate's account row `SKIP LOCKED`, then its capacity row, after the day rollover.
    None means another transaction holds the account; pick another one. The account stays locked for the claim."""
    require_tx(conn)
    return await _lock(conn, account_id, cfg, skip_locked=True)


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
    """Record that the account took this job's order: one `account_charges` row for `amount` (the job's
    `account_cost`) on the pool's current day, and `left_n` down by `amount` when it is known. Once per
    (job, pool): False when the job was already charged. A reported `left_n` stays reported."""
    require_tx(conn)
    if amount < 0:
        raise ValueError("amount must be >= 0")
    await _lock(conn, account_id, cfg, skip_locked=False)
    row = await (await conn.execute(
        "INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) "
        "VALUES (%(a)s, %(j)s, %(p)s, %(n)s, " + _TODAY + ") ON CONFLICT (job_id, pool) DO NOTHING RETURNING id",
        {"a": account_id, "j": job_id, "p": cfg.name, "n": amount, "tz": cfg.tz})).fetchone()
    if row is None:
        return False
    await conn.execute(
        "UPDATE account_capacity SET left_n = left_n - %(n)s, "
        "source = CASE WHEN source = 'reported' THEN source ELSE 'counted' END, "
        "known_at = CASE WHEN source = 'reported' THEN known_at ELSE clock_timestamp() END "
        "WHERE account_id = %(a)s AND pool = %(p)s AND left_n IS NOT NULL",
        {"a": account_id, "p": cfg.name, "n": amount})
    return True


async def refund_job(conn: AsyncConnection, job_id, pools: Mapping[str, PoolConfig]) -> list[tuple[str, int]]:
    """Give back the account charges of a failed job: the exact amount, once per charge, and only while the
    charge's account day is still the pool's current day (a refund after the reset would credit a day that
    already started from the budget). A refund that leaves `left_n` above 0 wakes the pool. Returns the
    (pool, amount) pairs refunded. Only a charge exists for a job whose order was taken, so nothing else is
    checked here."""
    require_tx(conn)
    charges = await (await conn.execute(
        "SELECT id, account_id, pool FROM account_charges WHERE job_id = %s AND refunded_at IS NULL "
        "ORDER BY account_id, pool",
        (job_id,))).fetchall()
    await failpoints.hit("pool.refund.listed")
    done = []
    for charge_id, account_id, pool in charges:
        cfg = pools[pool]
        await _lock(conn, account_id, cfg, skip_locked=False)   # serialises refund and charge
        got = await (await conn.execute(
            "UPDATE account_charges SET refunded_at = clock_timestamp() "
            "WHERE id = %(id)s AND refunded_at IS NULL AND account_day = " + _TODAY + " RETURNING amount",
            {"id": charge_id, "tz": cfg.tz})).fetchone()
        if got is None:
            continue
        await conn.execute(
            "UPDATE account_capacity SET left_n = left_n + %(n)s, "
            "rest_until = CASE WHEN left_n + %(n)s > 0 THEN NULL ELSE rest_until END, "
            "rest_reason = CASE WHEN left_n + %(n)s > 0 THEN NULL ELSE rest_reason END "
            "WHERE account_id = %(a)s AND pool = %(p)s AND left_n IS NOT NULL AND day_anchor = " + _TODAY,
            {"n": got[0], "a": account_id, "p": pool, "tz": cfg.tz})
        done.append((pool, got[0]))
    return done


@dataclass(frozen=True)
class Drift:
    account_id: object
    pool: str
    found: int
    expected: int


# (account, pool, found value) -> when it was logged (monotonic); an entry older than a pool day is forgotten, so the
# set stays bounded and a value that comes back a day later is logged again.
_logged_drift: dict[tuple, float] = {}
DRIFT_LOG_TTL_S = 24 * 3600.0


async def check_drift(conn: AsyncConnection, provider: str, pools: Mapping[str, PoolConfig]) -> list[Drift]:
    """Every 5 minutes: for pools with a budget, compare each counted or reset `left_n` with the budget minus the
    unrefunded charges of the pool day, correct the difference and log it once per value. A reported value is the
    upstream's own number and is never touched. Rows another transaction holds are skipped until the next run."""
    require_tx(conn)
    out = []
    now = time.monotonic()
    for key in [k for k, at in _logged_drift.items() if now - at > DRIFT_LOG_TTL_S]:
        del _logged_drift[key]
    for cfg in pools.values():
        if cfg.budget is None:
            continue
        rows = await (await conn.execute(
            "SELECT c.account_id, c.left_n, (SELECT COALESCE(sum(ch.amount), 0) FROM account_charges ch "
            " WHERE ch.account_id = c.account_id AND ch.pool = c.pool AND ch.refunded_at IS NULL "
            " AND ch.account_day = c.day_anchor) "
            "FROM account_capacity c JOIN accounts a ON a.id = c.account_id "
            "WHERE a.provider = %(prov)s AND c.pool = %(p)s AND c.source IN ('counted', 'reset') "
            "AND c.left_n IS NOT NULL AND c.day_anchor = " + _TODAY + " FOR UPDATE OF c SKIP LOCKED",
            {"prov": provider, "p": cfg.name, "tz": cfg.tz})).fetchall()
        for account_id, left_n, spent in rows:
            expected = cfg.budget - spent
            if left_n == expected:
                continue
            await conn.execute("UPDATE account_capacity SET left_n = %s, known_at = clock_timestamp() "
                               "WHERE account_id = %s AND pool = %s", (expected, account_id, cfg.name))
            out.append(Drift(account_id, cfg.name, left_n, expected))
            if (account_id, cfg.name, left_n) not in _logged_drift:
                _logged_drift[(account_id, cfg.name, left_n)] = now
                log.warning("pool drift: account %s pool %s had %s, charges say %s; corrected",
                            account_id, cfg.name, left_n, expected)
    return out


# ---- account effects --------------------------------------------------------------------------------------------
def _reason(text: str) -> str:
    return redact_log(text)[:200]


async def apply_effects(conn: AsyncConnection, account_id, effects: Iterable[AccountEffect], caps: Caps,
                        pools: Mapping[str, PoolConfig]) -> None:
    """Apply a result's account effects in the caller's transaction, with the job transition they belong to.

    - `Rest` and `Cooldown` never shorten a longer one (GREATEST); there is no rank.
    - `Suspect` adds a strike (not when it comes within its `min_gap_s` of the last counted one); at
      `caps.suspect_threshold` the account dies with the strike effect's reason. `Success` clears the counter and the
      time of the last counted strike.
    - `Dead` and `Revive` never change a `disabled` account: a person's decision wins over a check.
    - `Balance` writes the capacity; a reported value above 0 ends that pool's rest.
    The account row is locked first, whatever the effects touch."""
    require_tx(conn)
    await lock_account(conn, account_id)
    for e in effects:
        if isinstance(e, Balance):
            cfg = _cfg(pools, e.pool)
            await _lock(conn, account_id, cfg, skip_locked=False)
            await conn.execute(
                "UPDATE account_capacity SET left_n = %(n)s, source = %(s)s, known_at = clock_timestamp(), "
                "day_anchor = " + _TODAY + ", "
                "rest_until = CASE WHEN %(s)s = 'reported' AND %(n)s > 0 THEN NULL ELSE rest_until END, "
                "rest_reason = CASE WHEN %(s)s = 'reported' AND %(n)s > 0 THEN NULL ELSE rest_reason END "
                "WHERE account_id = %(a)s AND pool = %(p)s",
                {"n": e.left_n, "s": e.source, "a": account_id, "p": e.pool, "tz": cfg.tz})
        elif isinstance(e, Rest):
            cfg = _cfg(pools, e.pool)
            await _lock(conn, account_id, cfg, skip_locked=False)
            await conn.execute(
                "UPDATE account_capacity SET "
                "rest_reason = CASE WHEN %(u)s > COALESCE(rest_until, '-infinity') THEN %(r)s ELSE rest_reason END, "
                "rest_until = GREATEST(rest_until, %(u)s) WHERE account_id = %(a)s AND pool = %(p)s",
                {"u": e.until, "r": _reason(e.reason), "a": account_id, "p": e.pool})
        elif isinstance(e, Cooldown):
            await _account(conn, account_id,
                           "cooldown_reason = CASE WHEN %(u)s > COALESCE(cooldown_until, '-infinity') THEN %(r)s "
                           "ELSE cooldown_reason END, cooldown_until = GREATEST(cooldown_until, %(u)s), "
                           "check_due_at = CASE WHEN %(rc)s AND state <> 'dead' THEN %(u)s ELSE check_due_at END, "
                           "last_error = %(r)s", u=e.until, r=_reason(e.reason), rc=e.recheck)
        elif isinstance(e, Suspect):
            # The account row is locked (above), so reading whether the strike counts and writing it are one step.
            counted = (await (await conn.execute(
                "SELECT (strike_at IS NULL OR %(g)s <= 0 "
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
        elif isinstance(e, Success):
            await _account(conn, account_id, "strikes = 0, strike_at = NULL, last_error = NULL")
        elif isinstance(e, ProxyBad):
            await _account(conn, account_id, "proxy_ok = false, proxy_checked_at = clock_timestamp(), "
                           "last_error = %(r)s", r=_reason(e.reason))
        else:
            raise TypeError("unknown account effect %r" % (e,))


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
    await conn.execute("UPDATE account_capacity SET rest_until = NULL, rest_reason = NULL WHERE account_id = %s",
                       (account_id,))
    return await _account(conn, account_id,
                          "cooldown_until = NULL, cooldown_reason = NULL, strikes = 0, strike_at = NULL") == 1
