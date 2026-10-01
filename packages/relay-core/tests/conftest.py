"""Database fixtures for the relay-core tests (contracts §14.2, §14.3).

One migrated disposable database per session; every test that asks for `db` gets it empty. After each such
test the invariant checker runs over what the test left behind, then every table is emptied.
"""

import os
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from relay_core.db import run_migrations
from relay_core.testing.checker import violations
from relay_core.testing.pg import disposable_database

# Failpoints are inert unless the variable exists (relay_core.failpoints); the tests arm them in process.
os.environ.setdefault("RELAY_FAILPOINTS", "")

API_PASSWORD = "api-test-password"
WORKER_PASSWORD = "worker-test-password"

JOB_COLUMNS = ("id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, after_sent, "
               "submit_timeout_s, wallet_kind, price")


def job_args(job_id, key_id, *, provider="p1", kind="video", price=0) -> tuple:
    """Parameters for `INSERT INTO jobs (JOB_COLUMNS)`: the smallest valid job row."""
    return (job_id, provider, key_id, "generate", kind, "m", "a prompt", "marker-" + str(job_id)[:8], "queued",
            "detach", "reconcile", 60, kind, price)


JOB_INSERT = "INSERT INTO jobs (%s) VALUES (%s)" % (JOB_COLUMNS, ", ".join(["%s"] * 14))


@dataclass
class Db:
    dsn: str          # the owner role
    api_dsn: str
    worker_dsn: str

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True, options="-c search_path=relay")

    def key(self, *wallets: tuple[str, str, int | None, int | None], enabled: bool = True) -> uuid.UUID:
        """An api key with wallets (provider, kind, day_limit, total_limit); None = unlimited."""
        key_id = uuid.uuid4()
        with self.connect() as c:
            c.execute("INSERT INTO api_keys (id, key_hash, key_prefix, enabled) VALUES (%s, %s, %s, %s)",
                      (key_id, key_id.hex, "rk-test", enabled))
            for provider, kind, day, total in wallets:
                c.execute("INSERT INTO key_wallets (key_id, provider, kind, day_limit, total_limit) "
                          "VALUES (%s, %s, %s, %s, %s)", (key_id, provider, kind, day, total))
        return key_id

    def job(self, key_id, **kw) -> uuid.UUID:
        job_id = uuid.uuid4()
        with self.connect() as c:
            c.execute(JOB_INSERT, job_args(job_id, key_id, **kw))
        return job_id

    def one(self, sql: str, *params):
        with self.connect() as c:
            return c.execute(sql, params).fetchone()

    def run(self, sql: str, *params) -> None:
        with self.connect() as c:
            c.execute(sql, params)

    def all(self, sql: str, *params):
        with self.connect() as c:
            return c.execute(sql, params).fetchall()

    def wallet(self, key_id, provider="p1", kind="video") -> tuple[int, int]:
        """(used_day, used_total)"""
        return self.one("SELECT used_day, used_total FROM key_wallets WHERE key_id=%s AND provider=%s AND kind=%s",
                        key_id, provider, kind)

    def pin(self, tz: str = "UTC") -> None:
        """The test seam for the billing day: production code cannot choose a timezone, a test moves the pin."""
        with self.connect() as c:
            c.execute("DELETE FROM billing_pin")
            c.execute("INSERT INTO billing_pin (tz) VALUES (%s)", (tz,))

    def reset(self) -> None:
        with self.connect() as c:
            names = [r[0] for r in c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'relay' "
                "AND tablename NOT IN ('schema_migrations', 'billing_pin')")]
            c.execute("TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(names))
        self.pin("UTC")


@pytest.fixture(scope="session")
def _migrated():
    with disposable_database() as dsn:
        run_migrations(dsn, passwords={"relay_api": API_PASSWORD, "relay_worker": WORKER_PASSWORD})
        yield Db(dsn,
                 make_conninfo(dsn, user="relay_api", password=API_PASSWORD),
                 make_conninfo(dsn, user="relay_worker", password=WORKER_PASSWORD))


@pytest.fixture
def db(_migrated) -> Db:
    return _migrated


@pytest.fixture(autouse=True)
def _after_each_db_test(request):
    """The invariant checker runs after every test that used `db`, then the tables are emptied."""
    yield
    used = request.node.funcargs.get("db")
    if used is None:
        return
    try:
        with used.connect() as c:
            found = violations(c)
    finally:
        used.reset()
    assert not found, "invariants broken by this test: %s" % found
