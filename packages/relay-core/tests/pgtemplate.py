"""A migrated throwaway database for a test session, cut from a template when RELAY_TEST_PG_TEMPLATE is set.

Several pytest sessions share one Postgres (mutation workers, xdist workers, the split soak). The two service roles are
cluster-wide and `run_migrations` sets their passwords at every start, so sessions that start together fail with
"tuple concurrently updated" (3 of 8 and 12 of 24, measured). With RELAY_TEST_PG_TEMPLATE=<prefix> the first session
builds a migrated template database under a lock and every session copies it: no second `ALTER ROLE`, and 65 ms
instead of about 380 ms. The run that sets the variable owns the templates (the check cluster is thrown away with
them). Without the variable the database is created and migrated as before.
"""

import contextlib
import hashlib
import os
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import relay_core.db
from relay_core.db import run_migrations
from relay_core.testing.pg import PREFIX, admin_dsn, disposable_database

LOCK_KEY = 0x7E571A7E       # advisory lock on the admin database: one mutex for the sessions that may build a template


def schema_fingerprint() -> str:
    """Hash of the db package (the migrations and the code that applies them) as THIS process imports it, so a template
    never outlives a change to a migration: the mutation stage mutates the SQL and every mutant needs its own."""
    digest = hashlib.sha1()
    for path in sorted(Path(relay_core.db.__file__).parent.rglob("*")):
        if path.is_file() and path.suffix in (".sql", ".py"):
            digest.update(path.name.encode() + path.read_bytes())
    return digest.hexdigest()[:10]


def dsn_of(admin: str, name: str) -> str:
    return make_conninfo(**{**conninfo_to_dict(admin), "dbname": name})


@contextlib.contextmanager
def migrated_database(passwords: dict[str, str], tag: str):
    """Yield the DSN of an empty migrated database with its own name; drop it afterwards. `tag` names the template
    (its passwords differ per user: the tests, the soak); the template name also holds `schema_fingerprint()`."""
    prefix = os.environ.get("RELAY_TEST_PG_TEMPLATE", "")
    if not prefix:
        with disposable_database() as dsn:
            run_migrations(dsn, passwords=passwords, billing_tz="UTC")
            yield dsn
        return
    admin, name = admin_dsn(), PREFIX + uuid.uuid4().hex[:12]
    template = "%s_%s_%s" % (prefix, tag, schema_fingerprint())
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (template,)).fetchone() is None:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(template)))
                run_migrations(dsn_of(admin, template), passwords=passwords, billing_tz="UTC")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template)))
    try:
        yield dsn_of(admin, name)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
