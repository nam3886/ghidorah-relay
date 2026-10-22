"""A throwaway Postgres database per test session.

The admin DSN comes only from RELAY_TEST_PG_DSN, which `scripts/check.sh` points at its own
disposable container. The database is created with a `relay_test_` name and dropped on exit, even
when the test fails, so a test run can never write into a real database. (A test run once left 1432
test schemas in production because its test path could reach it.)
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

PREFIX = "relay_test_"


def admin_dsn() -> str:
    dsn = os.environ.get("RELAY_TEST_PG_DSN", "")
    if not dsn:
        raise RuntimeError("RELAY_TEST_PG_DSN is not set: run the tests through scripts/check.sh")
    return dsn


@contextmanager
def disposable_database(dsn: str | None = None) -> Iterator[str]:
    """Create an empty database, yield its DSN, drop it afterwards."""
    admin = dsn or admin_dsn()
    name = PREFIX + uuid.uuid4().hex[:12]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("CREATE DATABASE %s" % name)
    try:
        yield make_conninfo(**{**conninfo_to_dict(admin), "dbname": name})
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % name)
