"""The billing day timezone is fixed when `relay migrate` first runs and never changes (Q2). The pin is its only
source: no environment variable names it (owner, 25/10)."""

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from relay_core import billing
from relay_core.billing import BillingTzMismatch
from relay_core.db import open_pool, run_migrations, tx
from relay_core.testing.mutation import mutation
from relay_core.testing.pg import disposable_database

M = "packages/relay-core/src/relay_core/db/migrate.py"
B = "packages/relay-core/src/relay_core/billing.py"


def pinned(dsn):
    with psycopg.connect(dsn) as c:
        return [r[0] for r in c.execute("SELECT tz FROM relay.billing_pin")]


@mutation(B, 'DEFAULT_TZ = "Asia/Ho_Chi_Minh"', 'DEFAULT_TZ = "UTC"', expect="the default pin")
def test_the_first_migrate_pins_the_timezone_and_defaults_to_ho_chi_minh():
    with disposable_database() as a, disposable_database() as b:
        run_migrations(a, billing_tz="Asia/Tokyo")
        run_migrations(b)
        assert pinned(a) == ["Asia/Tokyo"] and pinned(b) == ["Asia/Ho_Chi_Minh"], "the default pin"
        with psycopg.connect(a) as c, pytest.raises(psycopg.errors.IntegrityError):    # one row, ever
            c.execute("INSERT INTO relay.billing_pin (tz, only_row) VALUES ('UTC', false)")


@mutation(M, "check_pin(row[0], billing_tz)", "pass", expect="DID NOT RAISE BillingTzMismatch")
def test_a_later_migrate_with_another_timezone_is_refused_and_changes_nothing():
    with disposable_database() as dsn:
        run_migrations(dsn, billing_tz="Asia/Ho_Chi_Minh")
        assert run_migrations(dsn, billing_tz="Asia/Ho_Chi_Minh") == []      # the same value is a no-op
        assert run_migrations(dsn) == []                                     # unset asserts nothing
        with pytest.raises(BillingTzMismatch, match=r"pinned to Asia/Ho_Chi_Minh"):
            run_migrations(dsn, billing_tz="UTC")
        assert pinned(dsn) == ["Asia/Ho_Chi_Minh"]


def test_the_migrate_command_reads_no_environment_timezone_and_keeps_the_pin(monkeypatch, capsys):
    from relay_cli.main import main

    with disposable_database() as dsn:
        run_migrations(dsn, billing_tz="Asia/Tokyo")
        monkeypatch.setenv("DATABASE_URL", dsn)
        monkeypatch.setenv("BILLING_TZ", "Asia/Seoul")                 # a leftover variable is ignored
        assert main(["migrate"]) == 0
        assert pinned(dsn) == ["Asia/Tokyo"]


@mutation(B, "    return check_pin(row[0] if row else None)", "    return \"UTC\"",
          expect="the pin is not what the process read")
def test_a_process_reads_the_pin_and_refuses_to_start_without_one(monkeypatch):
    async def start(dsn):
        async with await psycopg.AsyncConnection.connect(dsn, options="-c search_path=relay") as conn:
            return await billing.verify_billing_tz(conn)

    monkeypatch.setenv("BILLING_TZ", "UTC")                            # names nothing any more
    with disposable_database() as dsn:
        run_migrations(dsn, billing_tz="Asia/Tokyo")
        assert asyncio.run(start(dsn)) == "Asia/Tokyo", "the pin is not what the process read"
        with psycopg.connect(dsn) as c:
            c.execute("DELETE FROM relay.billing_pin")
        with pytest.raises(BillingTzMismatch, match="run `relay migrate`"):
            asyncio.run(start(dsn))


@mutation(B, '_PIN = "SELECT tz FROM billing_pin"', '_PIN = "SELECT NULL::text"',
           expect="no billing timezone is pinned: run `relay migrate`")
def test_a_charge_takes_its_billing_day_from_the_pinned_timezone(db):
    # whichever of UTC+14 and UTC-12 has another calendar date than UTC right now
    now = datetime.now(UTC)
    tz = next(z for z in ("Pacific/Kiritimati", "Etc/GMT+12") if now.astimezone(ZoneInfo(z)).date() != now.date())
    key = db.key(("p1", None, None))
    job = db.job(key)
    with db.connect() as c:
        c.execute("DELETE FROM billing_pin")           # the session database was pinned to UTC by its migrate
        c.execute("INSERT INTO billing_pin (tz) VALUES (%s)", (tz,))

    async def scenario():
        pool = await open_pool(db.dsn)
        try:
            async with tx(pool) as conn:
                return (await billing.charge(conn, key, "p1", 0, job)).billing_day
        finally:
            await pool.close()

    try:
        day = asyncio.run(scenario())
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM billing_pin")
    assert day == datetime.now(ZoneInfo(tz)).date() and day != now.date()
