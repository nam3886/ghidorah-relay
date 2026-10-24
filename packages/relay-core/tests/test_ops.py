"""Operator actions (invariants I1, I9): drain and resume, the restore mark."""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from jobkit import SPEC, account, claim, create, key, run

from relay_core import failpoints, pool
from relay_core.db import tx
from relay_core.jobs import ops
from relay_core.settings import REGISTRY, Registry, Settings
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/jobs/ops.py"

MARK_UNKNOWN = "UPDATE jobs SET status = 'unknown', sent_at"
MARK_QUEUED = "UPDATE jobs SET status = 'queued', sent_at"
UNSENT_RULE = "AND NOT (status = 'queued' AND claims = 0 AND sent_at IS NULL)"
UNSENT_IGNORED = "AND TRUE"
FORGET = '"UPDATE account_pools SET reported_left = NULL, reported_at = NULL, refill_at = NULL"'
KEEP = '"UPDATE account_pools SET rest_until = rest_until"'
DUE = "UPDATE accounts SET check_due_at = clock_timestamp()"
NOT_DUE = "UPDATE accounts SET updated_at = updated_at"
WORKER_GUARD = "        if live:"
NO_WORKER_GUARD = "        if False:"
ACCOUNTS_BEFORE_CAPACITY = """        await conn.execute("UPDATE accounts SET check_due_at = clock_timestamp()")
        # a reported balance of the dump may be stale: it is forgotten, and the checks report the real ones
        await conn.execute("UPDATE account_pools SET reported_left = NULL, reported_at = NULL, refill_at = NULL")
"""
CAPACITY_BEFORE_ACCOUNTS = (
    "        await conn.execute(\"UPDATE account_pools SET reported_left = NULL, reported_at = NULL, "
    "refill_at = NULL\")\n"
    "        await conn.execute(\"UPDATE accounts SET check_due_at = clock_timestamp()\")\n")


def real_settings(p, db) -> Settings:
    registry = Registry(*REGISTRY.specs.values(), *SPEC.settings().values())
    return Settings(p, db.dsn, registry=registry)


def test_drain_stops_claims_and_resume_restores_them(db):
    async def scenario(p):
        settings = real_settings(p, db)
        k = key(db)
        account(db)
        await create(p, k, "one")
        assert await ops.drain(settings, "fake") is True and settings.get("fake.claims_enabled") is False
        assert await claim(p, settings=settings) is None
        assert await ops.resume(settings, "fake") is True
        return await claim(p, settings=settings)

    assert run(db, scenario) is not None


SETTINGS = "packages/relay-core/src/relay_core/settings.py"
SAME_AS_STORED = "changed = row is None or row[0]"
ALWAYS_CHANGED = "changed = True"
ONLY_ABSENT = "changed = row is None"
RETURNS_VALUE = "return await settings.set_changed(provider + \".claims_enabled\", False, by=by)"
RETURNS_WRITTEN = "return await settings.set(provider + \".claims_enabled\", False, by=by)"


@mutation(SETTINGS, SAME_AS_STORED, ALWAYS_CHANGED, expect="a repeated drain said it changed something")
@mutation(SETTINGS, SAME_AS_STORED, ONLY_ABSENT, expect="a drain after a resume did not say it changed something")
@mutation(SRC, RETURNS_VALUE, RETURNS_WRITTEN, expect="a first drain did not say it changed something")
def test_drain_and_resume_say_whether_the_stored_value_changed(db):
    async def scenario(p):
        settings = real_settings(p, db)
        first = await ops.drain(settings, "fake")                     # no stored row yet
        again = await ops.drain(settings, "fake")
        back = await ops.resume(settings, "fake")
        drained = await ops.drain(settings, "fake")
        await ops.resume(settings, "fake")
        return first, again, back, drained, await ops.resume(settings, "fake")

    first, again, back, drained, resumed_twice = run(db, scenario)
    assert first is True, "a first drain did not say it changed something"
    assert again is False, "a repeated drain said it changed something"
    assert back is True and drained is True, "a drain after a resume did not say it changed something"
    assert resumed_twice is False


COMPARE = "SELECT value IS DISTINCT FROM %s::jsonb FROM settings_kv"
COMPARE_INVERTED = "SELECT value IS NOT DISTINCT FROM %s::jsonb FROM settings_kv"


@mutation(SETTINGS, COMPARE, COMPARE_INVERTED, expect="a stored 1 rewritten to true was not a change")
def test_a_stored_json_one_rewritten_as_true_is_a_change(db):
    async def scenario(p):
        settings = real_settings(p, db)
        db.run("INSERT INTO settings_kv (key, value) VALUES ('fake.claims_enabled', '1'::jsonb)")
        first = await settings.set_changed("fake.claims_enabled", True)       # 1 is not true to Postgres
        return first, await settings.set_changed("fake.claims_enabled", True)

    assert run(db, scenario) == (True, False), "a stored 1 rewritten to true was not a change"


def test_a_resume_with_no_stored_row_changes_the_store_although_the_default_is_the_same(db):
    async def scenario(p):
        settings = real_settings(p, db)
        return await ops.resume(settings, "fake"), await ops.resume(settings, "fake")

    assert run(db, scenario) == (True, False)       # the default (True) already holds, but the runbook wants it stored


# ---- restore mark --------------------------------------------------------------------------------------------------
def jobs_for_restore(db):
    """One job of each shape, keyed by name."""
    k = db.key()
    j = {n: db.job(k) for n in ("fresh", "requeued", "submitting", "waiting", "done", "late")}
    db.run("UPDATE jobs SET claims = 1 WHERE id = %s", j["requeued"])
    acct = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state) VALUES (%s, 'p1', '\\x01', 'available')", acct)
    db.run("UPDATE jobs SET status = 'submitting', claims = 1, account_id = %s WHERE id = %s", acct, j["submitting"])
    db.run("INSERT INTO leases (account_id, job_id, purpose, owner, until, hard_until) VALUES "
           "(%s, %s, 'job', 'w', clock_timestamp() + interval '1 minute', clock_timestamp() + interval '2 minutes')",
           acct, j["submitting"])
    db.run("UPDATE jobs SET status = 'waiting', claims = 1, external_ref = 'ref', "
           "sent_at = clock_timestamp() - interval '10 minutes' WHERE id = %s", j["waiting"])
    db.run("UPDATE jobs SET status = 'failed', error_code = 'render.deadline', "
           "finished_at = clock_timestamp() WHERE id = %s", j["done"])
    return j


def state(db, job_id):
    return db.one("SELECT status, sent_at IS NOT NULL, claims FROM jobs WHERE id = %s", job_id)


@mutation(SRC, MARK_UNKNOWN, MARK_QUEUED, expect="assert ('queued', True, 1) == ('unknown', True, 1)")
def test_restore_marks_possibly_sent_jobs_unknown_never_queued(db):
    j = jobs_for_restore(db)
    dump = datetime.now(UTC) + timedelta(hours=1)
    db.run("UPDATE jobs SET created_at = %s WHERE id = %s", dump + timedelta(days=1), j["late"])
    assert run(db, lambda p: ops.restore_mark(p, dump)) == 3
    assert state(db, j["requeued"]) == ("unknown", True, 1)
    assert state(db, j["submitting"]) == ("unknown", True, 1)
    assert state(db, j["waiting"]) == ("unknown", True, 1)
    assert db.one("SELECT sent_at FROM jobs WHERE id = %s", j["requeued"])[0] == dump
    assert db.one("SELECT external_ref FROM jobs WHERE id = %s", j["waiting"])[0] == "ref"
    assert db.one("SELECT count(*) FROM leases WHERE job_id = %s", j["submitting"])[0] == 0
    assert db.one("SELECT count(*) FROM job_events WHERE stage = 'restore'")[0] == 3
    assert db.one("SELECT count(*) FROM jobs WHERE status = 'queued' AND (sent_at IS NOT NULL OR claims > 0)")[0] == 0


@mutation(SRC, UNSENT_RULE, UNSENT_IGNORED, expect="assert ('unknown', True, 0) == ('queued', False, 0)")
def test_restore_leaves_unsent_terminal_and_later_jobs_alone(db):
    j = jobs_for_restore(db)
    dump = datetime.now(UTC) + timedelta(hours=1)
    db.run("UPDATE jobs SET created_at = %s WHERE id = %s", dump + timedelta(days=1), j["late"])
    run(db, lambda p: ops.restore_mark(p, dump))
    assert state(db, j["fresh"]) == ("queued", False, 0)            # never claimed: it cannot have been sent
    assert state(db, j["done"])[0] == "failed"
    assert state(db, j["late"]) == ("queued", False, 0)             # created after the dump window


def test_restore_fails_queued_commands_and_refuses_a_naive_time(db):
    db.run("INSERT INTO commands (provider, kind) VALUES ('p1', 'check')")
    db.run("INSERT INTO commands (provider, kind, status, finished_at) VALUES ('p1', 'check', 'done', now())")
    run(db, lambda p: ops.restore_mark(p, datetime.now(UTC)))
    assert sorted(r[0] for r in db.all("SELECT status FROM commands")) == ["done", "failed"]
    try:
        run(db, lambda p: ops.restore_mark(p, datetime.now()))
    except ValueError:
        return
    raise AssertionError("a naive dump time must be refused")


@mutation(SRC, FORGET, KEEP, expect="got == {")
@mutation(SRC, DUE, NOT_DUE, expect="assert 2 == 0")
def test_restore_forgets_every_reported_balance_keeps_the_rests_and_makes_every_account_due(db):
    ids = {n: uuid.uuid4() for n in ("a", "b")}
    for i in ids.values():
        db.run("INSERT INTO accounts (id, provider, credential_enc, state, check_due_at) VALUES "
               "(%s, 'p1', '\\x01', 'available', clock_timestamp() + interval '1 day')", i)
    for pool_name in ("video", "image"):
        db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at, refill_at, rest_until) "
               "VALUES (%s, %s, 7, clock_timestamp(), clock_timestamp() + interval '1 day', "
               "clock_timestamp() + interval '1 hour')", ids["a"], pool_name)
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at) "
           "VALUES (%s, 'video', 9, clock_timestamp())", ids["b"])
    run(db, lambda p: ops.restore_mark(p, datetime.now(UTC)))
    got = {(r[0], r[1]): r[2:] for r in db.all(
        "SELECT account_id, pool, reported_left, reported_at, refill_at, rest_until IS NOT NULL FROM account_pools")}
    assert got == {(ids["a"], "video"): (None, None, None, True), (ids["a"], "image"): (None, None, None, True),
                   (ids["b"], "video"): (None, None, None, False)}
    assert db.one("SELECT count(*) FROM accounts WHERE check_due_at > clock_timestamp()")[0] == 0


def heartbeat(db, worker_id, age_s):
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
           "(%s, 'p1', clock_timestamp() - interval '1 hour', clock_timestamp() - make_interval(secs => %s))",
           worker_id, age_s)


@mutation(SRC, WORKER_GUARD, NO_WORKER_GUARD, expect="DID NOT RAISE")
def test_restore_mark_refuses_while_a_worker_beats_and_marks_nothing(db):
    j = jobs_for_restore(db)
    heartbeat(db, "worker-p1", 5)
    dump = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(ops.WorkersRunning, match="worker-p1"):
        run(db, lambda p: ops.restore_mark(p, dump))
    assert state(db, j["submitting"]) == ("submitting", False, 1)
    assert db.one("SELECT count(*) FROM job_events WHERE stage = 'restore'")[0] == 0
    db.run("UPDATE jobs SET next_poll_at = clock_timestamp() WHERE status = 'waiting'")  # the invariant checker


@mutation(SRC, '        await conn.execute("LOCK TABLE worker_heartbeats IN SHARE MODE")\n', "",
          expect="a worker that boots during the mark must wait for it")
def test_a_worker_that_boots_during_the_restore_mark_waits_until_the_mark_has_committed(db):
    jobs_for_restore(db)
    dump = datetime.now(UTC) + timedelta(hours=1)

    async def scenario(p):
        checked, go_on = asyncio.Event(), asyncio.Event()

        async def pause():
            checked.set()
            await go_on.wait()

        failpoints.arm("ops.restore.checked", pause)
        try:
            marking = asyncio.create_task(ops.restore_mark(p, dump))
            await asyncio.wait_for(checked.wait(), 5)
            booted = True
            try:                                          # what a booting worker's first heartbeat write does
                async with tx(p) as conn:
                    await conn.execute("SET LOCAL lock_timeout = '300ms'")
                    await conn.execute("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) "
                                       "VALUES ('worker-p1', 'p1', clock_timestamp(), clock_timestamp())")
            except psycopg.errors.LockNotAvailable:
                booted = False
            go_on.set()
            await marking
            return booted
        finally:
            failpoints.disarm("ops.restore.checked")
            go_on.set()

    booted_during_mark = run(db, scenario)
    db.run("DELETE FROM worker_heartbeats")
    db.run("UPDATE jobs SET next_poll_at = clock_timestamp() WHERE status = 'waiting'")  # the invariant checker
    assert not booted_during_mark, "a worker that boots during the mark must wait for it"


def test_restore_mark_runs_when_every_heartbeat_is_older_than_a_dead_worker_bound(db):
    j = jobs_for_restore(db)
    heartbeat(db, "worker-p1", 3600)
    dump = datetime.now(UTC) + timedelta(hours=1)
    db.run("UPDATE jobs SET created_at = %s WHERE id = %s", dump + timedelta(days=1), j["late"])
    assert run(db, lambda p: ops.restore_mark(p, dump)) == 3


@mutation(SRC, ACCOUNTS_BEFORE_CAPACITY, CAPACITY_BEFORE_ACCOUNTS, expect="deadlock detected")
def test_restore_mark_locks_the_account_rows_before_the_pool_rows(db):
    """A transaction that holds an account row and then updates its pool rows (a wake) must not meet the restore
    head on: the restore waits for the account first and holds no pool row while it does."""
    acct = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state) VALUES (%s, 'p1', '\\x01', 'available')", acct)
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at) "
           "VALUES (%s, 'video', 5, clock_timestamp())", acct)

    async def scenario(p):
        reached, go, hits = asyncio.Event(), asyncio.Event(), []

        async def pause():
            hits.append(1)
            reached.set()
            await go.wait()
        failpoints.arm("pool.wake.account_locked", pause)

        async def wake():
            async with tx(p) as conn:
                return await pool.wake_account(conn, acct)

        async def restore():
            await reached.wait()
            return await ops.restore_mark(p, datetime.now(UTC))

        async def release():
            await reached.wait()
            t = time.monotonic()
            async with tx(p) as c:
                while time.monotonic() - t < 0.6 and (await (await c.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                        "AND datname = current_database()")).fetchone())[0] < 1:
                    await asyncio.sleep(0.02)
            go.set()

        out = await asyncio.gather(wake(), restore(), release(), return_exceptions=True)
        failpoints.disarm()
        return out

    out = run(db, scenario)
    errors = [r for r in out if isinstance(r, psycopg.Error)]
    assert not errors, errors
    assert db.one("SELECT reported_left FROM account_pools")[0] is None
