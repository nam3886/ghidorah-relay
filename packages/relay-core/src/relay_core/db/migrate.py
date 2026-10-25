"""`relay migrate`: the only DDL path. Nothing runs DDL on connect.

Numbered `migrations/NNNN_name.sql` files run in order, each in its own transaction with the version row, under
one advisory lock, so a second `relay migrate` is a no-op and two at once do not interleave. Migrations are
expand/contract: the previous build keeps working on the new schema, so a file only adds, never drops
what a running build still reads.
0020 is the one owner-approved exception: a contract migration that wipes the jobs and the money tables and runs with
every service stopped (plans/money-v2.md section 8).
"""

import re
from collections.abc import Mapping
from pathlib import Path

import psycopg
from psycopg import sql

from relay_core.billing import DEFAULT_TZ, check_pin
from relay_core.db.pool import SCHEMA

MIGRATIONS = Path(__file__).parent / "migrations"
ROLES = ("relay_api", "relay_worker")
LOCK_KEY = 0x72656C6179      # "relay"
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def migration_files() -> list[tuple[int, Path]]:
    """(version, path) in order; the numbering must be 1, 2, 3, ... with no gap or duplicate."""
    found = sorted((int(m.group(1)), p) for p in MIGRATIONS.iterdir() if (m := _NAME.match(p.name)))
    if [v for v, _ in found] != list(range(1, len(found) + 1)):
        raise RuntimeError("migrations are not numbered 0001..NNNN without gaps: %s" % [p.name for _, p in found])
    return found


def _pin_row(conn: psycopg.Connection):
    """The pinned timezone row, or None when the table does not exist yet or holds no row."""
    if conn.execute("SELECT to_regclass('billing_pin')").fetchone()[0] is None:
        return None
    return conn.execute("SELECT tz FROM billing_pin").fetchone()


def run_migrations(dsn: str, *, passwords: Mapping[str, str] | None = None, billing_tz: str | None = None
                   ) -> list[int]:
    """Apply every pending migration; return the versions applied now. The DSN is the owner role's.

    `passwords` maps a service role to the password it gets (and LOGIN); a role with no entry is left as it is.
    `billing_tz` is validated, then pinned in `billing_pin` by the first migrate (billing.DEFAULT_TZ when not given;
    `relay migrate` never gives one, a test does); a later migrate that names another timezone is refused, since the
    billing day never changes. No environment variable sets it.
    """
    files = migration_files()
    unknown = set(passwords or ()) - set(ROLES)
    if unknown:
        raise ValueError("not service roles: %s" % sorted(unknown))
    applied_now = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        if billing_tz:
            conn.execute("SELECT now() AT TIME ZONE %s", (billing_tz,))
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)))
        conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(SCHEMA)))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        # A pin that disagrees with `billing_tz` refuses the run BEFORE any pending file is applied.
        row = _pin_row(conn)
        if row is not None:
            check_pin(row[0], billing_tz)
        for version, path in files:
            if version in done:
                continue
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = 0")
                conn.execute("SET LOCAL lock_timeout = '30s'")
                conn.execute(path.read_text(encoding="utf-8").encode())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied_now.append(version)
        if row is None:
            conn.execute("INSERT INTO billing_pin (tz) VALUES (%s)", (billing_tz or DEFAULT_TZ,))
        for role, password in (passwords or {}).items():
            if password:
                conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)))
    return applied_now
