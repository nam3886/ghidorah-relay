"""Key wallets and the ledger: charge and refund (D2invariants I4, I5, I10, I25, I29).

A key has ONE points wallet (scope `*`) and, per provider, an optional cap (scope = the provider's name) on top of it. A
charge takes the points wallet and the provider's cap together or neither. Every guard is in SQL: the charge is ONE
conditional UPDATE over both rows, the refund is one insert protected by a unique index and the counters move only when
that insert returned a row. Nothing derives quota from the ledger; the ledger is the audit trail the invariant checker
compares with the counters. The functions take the caller's connection, so a charge commits or rolls back with the job
row that goes with it (see `relay_core.db.tx`).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import NoReturn

from psycopg import AsyncConnection

from relay_core import failpoints
from relay_core.txguard import NotInTransaction, require_tx  # noqa: F401  (re-exported for callers)

log = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Ho_Chi_Minh"   # what the first `relay migrate` pins in billing_pin; the pin never moves
STAR = "*"           # the scope of a key's points wallet; any other scope is a provider's cap
_PIN = "SELECT tz FROM billing_pin"

# The billing day is read from the database clock inside the statement, never from the caller, in the pinned tz.
_TODAY = "(clock_timestamp() AT TIME ZONE %(tz)s)::date"
# A new day starts the day counter again; the anchor is the day the counter belongs to.
_DAY_BASE = "CASE WHEN w.day_anchor IS DISTINCT FROM d.today THEN 0 ELSE w.used_day END"

# The rows are locked first, in one fixed order (the points wallet, then the cap), so two charges of one key never wait
# for each other crosswise; the UPDATE then moves the counters of every row that passes its limits. A row that fails
# its limit is not updated, so fewer rows than scopes come back and `charge` rolls the whole statement back.
_CHARGE = f"""
WITH d AS (SELECT {_TODAY} AS today),
     locked AS MATERIALIZED (
         SELECT key_id, scope FROM key_wallets WHERE key_id = %(key)s AND scope = ANY(%(scopes)s)
          ORDER BY (scope <> '*'), scope FOR NO KEY UPDATE)
UPDATE key_wallets w
   SET used_day = {_DAY_BASE} + %(points)s,
       used_total = w.used_total + %(points)s,
       day_anchor = d.today
  FROM d, locked l, api_keys k
 WHERE w.key_id = l.key_id AND w.scope = l.scope
   AND k.id = w.key_id AND k.enabled
   AND (w.day_limit IS NULL OR (w.day_limit > 0 AND {_DAY_BASE} + %(points)s <= w.day_limit))
   AND (w.total_limit IS NULL OR (w.total_limit > 0 AND w.used_total + %(points)s <= w.total_limit))
RETURNING w.scope, d.today
"""


class BillingError(Exception):
    """Base of the billing refusals; `code` is the platform error code the API maps it to."""

    code = "internal.unexpected"


class KeyDisabled(BillingError):
    code = "auth.key_disabled"

    def __init__(self, *args, key_id=None):
        super().__init__(*args)
        self.key_id = key_id      # set by keys.lookup once the hash matched, so the refusal is logged under that key


class QuotaExceeded(BillingError):
    code = "quota.key_exceeded"


class WalletMissing(BillingError):
    """A wallet row that should exist does not: a bug, never a quota answer."""


@dataclass(frozen=True)
class Charge:
    ledger_ids: tuple[int, ...]    # one charge row per scope the charge took: the points wallet first, then the cap
    billing_day: date


@dataclass(frozen=True)
class Refund:
    refunded: bool        # False: this charge was already refunded, nothing moved
    clamped: bool = False  # True: a counter would have gone below 0 and was floored (an alert)


class BillingTzMismatch(RuntimeError):
    """No billing timezone is pinned, or a migrate was asked for another one than the pin."""


def check_pin(pinned: str | None, wanted: str | None = None) -> str:
    """The pinned timezone, or a refusal when it is missing or `wanted` (given to `run_migrations`) differs from it."""
    if pinned is None:
        raise BillingTzMismatch("no billing timezone is pinned: run `relay migrate`")
    if wanted and wanted != pinned:
        raise BillingTzMismatch("billing timezone %s asked, but the billing day was pinned to %s when `relay migrate` "
                                "first ran; it never changes" % (wanted, pinned))
    return pinned


async def verify_billing_tz(conn: AsyncConnection) -> str:
    """Process start: refuse to run when no billing timezone is pinned. The pin is the only source: every charge reads
    it from the database."""
    row = await (await conn.execute(_PIN)).fetchone()
    return check_pin(row[0] if row else None)


_require_tx = require_tx   # local name kept: the money paths call it first, before any SQL


async def _tz(conn: AsyncConnection) -> str:
    """The timezone of the billing day: the pinned one, read in the caller's transaction. There is no override a
    caller could pass; tests move the day by changing the pin row. A missing pin refuses: billing never guesses."""
    row = await (await conn.execute(_PIN)).fetchone()
    return check_pin(row[0] if row else None, None)


class _Short(Exception):
    """Fewer rows moved than scopes asked for: the savepoint is rolled back and the refusal explained."""


async def charge(conn: AsyncConnection, key_id, provider: str, points: int, job_id) -> Charge:
    """Charge `points` to the key's points wallet and to its cap for `provider`, and write one ledger row per scope, in
    the caller's transaction. Both counters move or neither does: the statement runs in a savepoint of its own, so a
    refusal that the caller catches (a Re-check whose charge is refused) never leaves the points wallet half charged.

    A disabled key, a closed wallet (limit 0, even for a 0-point job) and a day or total limit that the charge would
    pass on either scope update fewer rows than there are scopes and raise. `job_id` may name a job row that the same
    transaction inserts next.
    """
    _require_tx(conn)
    if points < 0:
        raise ValueError("points must be >= 0")
    if provider == STAR:
        raise ValueError("a provider cannot be named %r" % STAR)
    await failpoints.hit("billing.charge.before_update")
    scopes = [STAR, provider]
    params = {"key": key_id, "scopes": scopes, "points": points, "tz": await _tz(conn)}
    try:
        async with conn.transaction():
            rows = await (await conn.execute(_CHARGE, params)).fetchall()
            if len(rows) < len(scopes):
                raise _Short()
            today = rows[0][1]
            ids = []
            for scope in scopes:
                ledger = await (await conn.execute(
                    "INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day) "
                    "VALUES (%s, %s, %s, %s, 'charge', %s, %s) RETURNING id",
                    (key_id, scope, job_id, provider, points, today))).fetchone()
                ids.append(ledger[0])
            await failpoints.hit("billing.charge.after_ledger")
    except _Short:
        await _explain_refusal(conn, key_id, provider)
    return Charge(tuple(ids), today)


async def _explain_refusal(conn: AsyncConnection, key_id, provider: str) -> NoReturn:
    key = await (await conn.execute("SELECT enabled FROM api_keys WHERE id = %s", (key_id,))).fetchone()
    have = {r[0] for r in await (await conn.execute(
        "SELECT scope FROM key_wallets WHERE key_id = %s AND scope = ANY(%s)", (key_id, [STAR, provider]))
    ).fetchall()}
    missing = [s for s in (STAR, provider) if s not in have]
    if key is None or missing:
        raise WalletMissing("no wallet for key %s, scope %s" % (key_id, ", ".join(missing) or "(the key)"))
    if not key[0]:
        raise KeyDisabled("key %s is disabled" % key_id)
    raise QuotaExceeded("key %s: the points wallet or the %s cap is closed or its limit is reached"
                        % (key_id, provider))


async def refund(conn: AsyncConnection, charge_id: int) -> Refund:
    """Refund one charge row, at most once (I4), in the caller's transaction.

    The counters move only when the refund row was inserted. `used_day` moves only for a charge of today; a
    charge from an earlier billing day reduces `used_total` alone (I5). That is decided under the wallet lock,
    from the wallet's own `day_anchor`: a charge of a newer day may have committed while this refund waited.
    A floor that clamped is an alert, stored in `billing_alerts` with the refund. Always the full amount: the charge
    row is the one unit, and a failed job refunds each of its rows (`jobs.fail`).
    """
    _require_tx(conn)
    await failpoints.hit("billing.refund.start")
    charged = await (await conn.execute(
        "SELECT key_id, job_id, provider, scope, amount, billing_day, " + _TODAY + " FROM ledger "
        "WHERE id = %(id)s AND entry = 'charge'", {"id": charge_id, "tz": await _tz(conn)})).fetchone()
    if charged is None:
        raise ValueError("ledger row %s is not a charge" % charge_id)
    key_id, job_id, provider, scope, amount, charged_day, today = charged
    inserted = await (await conn.execute(
        "INSERT INTO ledger (key_id, scope, job_id, provider, entry, amount, billing_day, refund_of) "
        "VALUES (%s, %s, %s, %s, 'refund', %s, %s, %s) ON CONFLICT (refund_of) DO NOTHING RETURNING id",
        (key_id, scope, job_id, provider, amount, today, charge_id))).fetchone()
    if inserted is None:
        return Refund(False)
    await failpoints.hit("billing.refund.before_counters")
    # NO KEY UPDATE: the ledger insert above holds KEY SHARE on this row; a stronger lock would deadlock two
    # refunds of different charges on one wallet.
    used_day, used_total, anchor = await (await conn.execute(
        "SELECT used_day, used_total, day_anchor FROM key_wallets "
        "WHERE key_id = %s AND scope = %s FOR NO KEY UPDATE", (key_id, scope))).fetchone()
    same_day = charged_day == today and anchor == charged_day
    clamped = used_total < amount or (same_day and used_day < amount)
    await conn.execute(
        "UPDATE key_wallets SET used_total = GREATEST(used_total - %(a)s, 0), "
        "used_day = CASE WHEN %(same_day)s THEN GREATEST(used_day - %(a)s, 0) ELSE used_day END "
        "WHERE key_id = %(key)s AND scope = %(scope)s",
        {"a": amount, "same_day": same_day, "key": key_id, "scope": scope})
    if clamped:
        await conn.execute(
            "INSERT INTO billing_alerts (alert, key_id, scope, ledger_id, amount, used_day, used_total) "
            "VALUES ('clamp', %s, %s, %s, %s, %s, %s)", (key_id, scope, inserted[0], amount, used_day, used_total))
        log.error("ALERT billing clamp: refund of ledger %s (%s points) would take a counter of key %s %s below 0",
                  charge_id, amount, key_id, scope)
    return Refund(True, clamped)


Limits = tuple[int | None, int | None]       # (day_limit, total_limit): None = unlimited, 0 = closed


async def create_wallets(conn: AsyncConnection, key_id, scopes: Mapping[str, Limits]) -> None:
    """A key's wallet rows with the limits chosen when it is made, scope `*` and the provider caps. None = unlimited,
    set only on purpose; 0 = closed."""
    for scope, (day_limit, total_limit) in scopes.items():
        await conn.execute(
            "INSERT INTO key_wallets (key_id, scope, day_limit, total_limit) VALUES (%s, %s, %s, %s)",
            (key_id, scope, day_limit, total_limit))


async def ensure_wallets(conn: AsyncConnection, provider: str) -> None:
    """A provider that registers after keys exist is CLOSED on them until the admin opens it (I29): every key gets a
    closed cap row for it. A key that has no points wallet gets a closed one too."""
    for scope in (STAR, provider):
        await conn.execute(
            "INSERT INTO key_wallets (key_id, scope, day_limit) SELECT id, %s, 0 FROM api_keys "
            "ON CONFLICT (key_id, scope) DO NOTHING", (scope,))
