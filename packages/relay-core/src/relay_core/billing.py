"""Key wallets and the ledger: charge and refund (contracts §4.1, D2, D14, D17; invariants I4, I5, I10, I25, I29).

Every guard is in SQL: the charge is ONE conditional UPDATE, the refund is one insert protected by a unique
index and the counters move only when that insert returned a row. Nothing derives quota from the ledger; the
ledger is the audit trail the invariant checker compares with the counters. The functions take the caller's
connection, so a charge commits or rolls back with the job row that goes with it (see `relay_core.db.tx`).
"""

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from psycopg import AsyncConnection

from relay_core import failpoints
from relay_core.txguard import NotInTransaction, require_tx  # noqa: F401  (re-exported for callers)

log = logging.getLogger(__name__)

DEFAULT_TZ = "UTC"   # D17: the billing day is 00:00 UTC; `relay migrate` pins BILLING_TZ in billing_pin
_PIN = "SELECT tz FROM billing_pin"

# The billing day is read from the database clock inside the statement, never from the caller, in the pinned tz.
_TODAY = "(clock_timestamp() AT TIME ZONE %(tz)s)::date"
# A new day starts the day counter again; the anchor is the day the counter belongs to.
_DAY_BASE = "CASE WHEN w.day_anchor IS DISTINCT FROM d.today THEN 0 ELSE w.used_day END"

_CHARGE = f"""
WITH d AS (SELECT {_TODAY} AS today)
UPDATE key_wallets w
   SET used_day = {_DAY_BASE} + %(points)s,
       used_total = w.used_total + %(points)s,
       day_anchor = d.today
  FROM d, api_keys k
 WHERE w.key_id = %(key)s AND w.provider = %(provider)s AND w.kind = %(kind)s
   AND k.id = w.key_id AND k.enabled
   AND (w.day_limit IS NULL OR (w.day_limit > 0 AND {_DAY_BASE} + %(points)s <= w.day_limit))
   AND (w.total_limit IS NULL OR (w.total_limit > 0 AND w.used_total + %(points)s <= w.total_limit))
RETURNING d.today
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
    ledger_id: int
    billing_day: date


@dataclass(frozen=True)
class Refund:
    refunded: bool        # False: this charge was already refunded, nothing moved
    clamped: bool = False  # True: a counter would have gone below 0 and was floored (an alert)


class BillingTzMismatch(RuntimeError):
    """The process environment names a different billing timezone than the one pinned at the first migrate."""


def check_pin(pinned: str | None, env_tz: str | None) -> str:
    """The pinned timezone, or a refusal when it is missing or `env_tz` (BILLING_TZ, when set) differs from it."""
    if pinned is None:
        raise BillingTzMismatch("no billing timezone is pinned: run `relay migrate`")
    if env_tz and env_tz != pinned:
        raise BillingTzMismatch("BILLING_TZ=%s but the billing day was pinned to %s when `relay migrate` first ran; "
                                "it never changes" % (env_tz, pinned))
    return pinned


async def verify_billing_tz(conn: AsyncConnection, env_tz: str | None = None) -> str:
    """Process start: refuse to run when BILLING_TZ is set to something other than the pinned value. An unset
    variable asserts nothing, since every charge reads the pin from the database."""
    row = await (await conn.execute(_PIN)).fetchone()
    return check_pin(row[0] if row else None, os.environ.get("BILLING_TZ") if env_tz is None else env_tz)


_require_tx = require_tx   # local name kept: the money paths call it first, before any SQL


async def _tz(conn: AsyncConnection) -> str:
    """The timezone of the billing day: the pinned one, read in the caller's transaction. There is no override a
    caller could pass; tests move the day by changing the pin row. A missing pin refuses: billing never guesses."""
    row = await (await conn.execute(_PIN)).fetchone()
    return check_pin(row[0] if row else None, None)


async def charge(conn: AsyncConnection, key_id, provider: str, kind: str, points: int, job_id) -> Charge:
    """Charge `points` to the key's (provider, kind) wallet and write the ledger row, in the caller's transaction.

    A disabled key, a closed wallet (limit 0, even for a 0-point job) and a day or total limit that the charge
    would pass all update zero rows and raise. `job_id` may name a job row that the same transaction inserts next.
    """
    _require_tx(conn)
    if points < 0:
        raise ValueError("points must be >= 0")
    await failpoints.hit("billing.charge.before_update")
    params = {"key": key_id, "provider": provider, "kind": kind, "points": points, "tz": await _tz(conn)}
    row = await (await conn.execute(_CHARGE, params)).fetchone()
    if row is None:
        await _explain_refusal(conn, key_id, provider, kind)
    today = row[0]
    ledger = await (await conn.execute(
        "INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
        "VALUES (%s, %s, %s, %s, 'charge', %s, %s) RETURNING id",
        (key_id, job_id, provider, kind, points, today))).fetchone()
    await failpoints.hit("billing.charge.after_ledger")
    return Charge(ledger[0], today)


async def _explain_refusal(conn: AsyncConnection, key_id, provider: str, kind: str):
    row = await (await conn.execute(
        "SELECT k.enabled, w.key_id IS NOT NULL FROM api_keys k "
        "LEFT JOIN key_wallets w ON w.key_id = k.id AND w.provider = %s AND w.kind = %s WHERE k.id = %s",
        (provider, kind, key_id))).fetchone()
    if row is None or not row[1]:
        raise WalletMissing("no wallet for key %s, %s/%s" % (key_id, provider, kind))
    if not row[0]:
        raise KeyDisabled("key %s is disabled" % key_id)
    raise QuotaExceeded("key %s: %s/%s limit reached" % (key_id, provider, kind))


async def refund(conn: AsyncConnection, charge_id: int) -> Refund:
    """Refund one charge row, at most once (I4), in the caller's transaction.

    The counters move only when the refund row was inserted. `used_day` moves only for a charge of today; a
    charge from an earlier billing day reduces `used_total` alone (D14, I5). That is decided under the wallet lock,
    from the wallet's own `day_anchor`: a charge of a newer day may have committed while this refund waited.
    A floor that clamped is an alert, stored in `billing_alerts` with the refund.
    """
    _require_tx(conn)
    await failpoints.hit("billing.refund.start")
    charged = await (await conn.execute(
        "SELECT key_id, job_id, provider, kind, amount, billing_day, " + _TODAY + " FROM ledger "
        "WHERE id = %(id)s AND entry = 'charge'", {"id": charge_id, "tz": await _tz(conn)})).fetchone()
    if charged is None:
        raise ValueError("ledger row %s is not a charge" % charge_id)
    key_id, job_id, provider, kind, amount, charged_day, today = charged
    inserted = await (await conn.execute(
        "INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day, refund_of) "
        "VALUES (%s, %s, %s, %s, 'refund', %s, %s, %s) ON CONFLICT (refund_of) DO NOTHING RETURNING id",
        (key_id, job_id, provider, kind, amount, today, charge_id))).fetchone()
    if inserted is None:
        return Refund(False)
    await failpoints.hit("billing.refund.before_counters")
    # NO KEY UPDATE: the ledger insert above holds KEY SHARE on this row; a stronger lock would deadlock two
    # refunds of different charges on one wallet.
    used_day, used_total, anchor = await (await conn.execute(
        "SELECT used_day, used_total, day_anchor FROM key_wallets "
        "WHERE key_id = %s AND provider = %s AND kind = %s FOR NO KEY UPDATE",
        (key_id, provider, kind))).fetchone()
    same_day = charged_day == today and anchor == charged_day
    clamped = used_total < amount or (same_day and used_day < amount)
    await conn.execute(
        "UPDATE key_wallets SET used_total = GREATEST(used_total - %(a)s, 0), "
        "used_day = CASE WHEN %(same_day)s THEN GREATEST(used_day - %(a)s, 0) ELSE used_day END "
        "WHERE key_id = %(key)s AND provider = %(provider)s AND kind = %(kind)s",
        {"a": amount, "same_day": same_day, "key": key_id, "provider": provider, "kind": kind})
    if clamped:
        await conn.execute(
            "INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, used_total) "
            "VALUES ('clamp', %s, %s, %s, %s, %s, %s, %s)",
            (key_id, provider, kind, inserted[0], amount, used_day, used_total))
        log.error("ALERT billing clamp: refund of ledger %s (%s points) would take a counter of key %s %s/%s below 0",
                  charge_id, amount, key_id, provider, kind)
    return Refund(True, clamped)


async def create_wallets(conn: AsyncConnection, key_id, pairs: Iterable[tuple[str, str]], *,
                         day_limit: int | None = 0, total_limit: int | None = None) -> None:
    """A key's wallet rows with the limits chosen when it is made. None = unlimited, set only on purpose; 0 = closed."""
    for provider, kind in pairs:
        await conn.execute(
            "INSERT INTO key_wallets (key_id, provider, kind, day_limit, total_limit) VALUES (%s, %s, %s, %s, %s)",
            (key_id, provider, kind, day_limit, total_limit))


async def ensure_wallets(conn: AsyncConnection, provider: str, kinds: Iterable[str]) -> None:
    """A provider or kind that registers after keys exist is CLOSED on them until the admin opens it (I29)."""
    for kind in kinds:
        await conn.execute(
            "INSERT INTO key_wallets (key_id, provider, kind, day_limit) SELECT id, %s, %s, 0 FROM api_keys "
            "ON CONFLICT (key_id, provider, kind) DO NOTHING", (provider, kind))
