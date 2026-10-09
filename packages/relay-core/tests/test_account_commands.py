"""The account commands (contracts 4.4, 7.6, D37, D39): delete of an unused account with its screenshot objects, wake,
check, check all and proxy re-check, all run by the worker role. The api role cannot write what they write."""

import asyncio
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import jobkit
import psycopg
import pytest
from accountkit import (
    KitEngine,
    KitSpec,
    account_row,
    children,
    command,
    keys,
    make_env,
    minio_settings,
    new_account,
    new_command,
    object_store,
    put_shot,
    run_as,
)

from relay_core import failpoints
from relay_core.jobs.commands import run_one
from relay_core.providers import Health, ProxyVerdict
from relay_core.testing.mutation import mutation

COMMANDS = "packages/relay-core/src/relay_core/jobs/commands.py"
CHECKS = "packages/relay-core/src/relay_core/jobs/checks.py"
TESTS = Path(__file__).resolve().parent


@pytest.fixture
def store():
    with object_store() as s:
        yield s


def worker(db, scenario):
    return run_as(db.worker_dsn, scenario)


def delete_command(db, target, **kw):
    return new_command(db, "delete_account", args={"account_id": str(target)}, **kw)


def run_delete(db, store, *, engine=None):
    async def scenario(p):
        return await run_one(make_env(p, engine or KitEngine(), store=store))

    return worker(db, scenario)


def fill(db, store, account):
    """An account with every by-product a delete has to clean up."""
    shot = put_shot(db, store, account)
    db.run("INSERT INTO account_capacity (account_id, pool, source) VALUES (%s, 'video', 'reported')", account)
    db.run("INSERT INTO account_capacity (account_id, pool, source) VALUES (%s, 'image', 'reported')", account)
    db.run("INSERT INTO leases (account_id, purpose, owner, until) VALUES (%s, 'check', 'w', now() - interval '1 h')",
           account)
    old = [new_command(db, "check", account_id=account), new_command(db, "wake", account_id=account)]
    db.run("UPDATE commands SET status = 'done', finished_at = now() WHERE account_id = %s", account)
    return shot, *old


# ---- delete: an unused account ------------------------------------------------------------------------------------
@mutation(CHECKS, "    for key in keys:\n        await env.store.remove(key)", "    for key in keys:\n        pass",
          expect="assert keys(store) == [keep_shot]                                   # the other")
def test_delete_removes_an_unused_account_with_its_shots_objects_commands_and_capacity_rows(db, store):
    a = new_account(db, checked=True)
    other = new_account(db, checked=True)
    fill(db, store, a)
    keep_shot = put_shot(db, store, other)
    cid = delete_command(db, a)
    assert run_delete(db, store) is True
    assert account_row(db, a) is None
    assert children(db, a) == dict.fromkeys(children(db, a), 0)
    assert keys(store) == [keep_shot]                                   # the other account's object stays
    row = command(db, cid)
    assert row["status"] == "done" and row["account_id"] is None and row["result"]["deleted"] == str(a)
    assert account_row(db, other) is not None


def test_delete_of_an_account_that_is_already_gone_ends_done(db, store):
    cid = delete_command(db, "00000000-0000-0000-0000-000000000009")
    run_delete(db, store)
    assert command(db, cid)["status"] == "done" and command(db, cid)["result"]["already_gone"] is True


# ---- delete: a used account ---------------------------------------------------------------------------------------
def make_used(db, account, how):
    if how == "job":
        job = db.job(db.key(("fake", "video", None, None)), provider="fake")
        db.run("UPDATE jobs SET account_id = %s, status = 'failed', error = 'x', finished_at = now() WHERE id = %s",
               account, job)
    elif how == "charge":
        job = db.job(db.key(("fake", "video", None, None)), provider="fake")
        db.run("UPDATE jobs SET taken_at = now() WHERE id = %s", job)
        db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) "
               "VALUES (%s, %s, 'video', 1, current_date)", account, job)
    else:
        db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, 'command', 'x', "
               "clock_timestamp() + interval '5 min', clock_timestamp() + interval '5 min')", account)


@mutation(COMMANDS, "    if reason is not None:", "    if False:", expect="assert keys(store) == [shot]")
@pytest.mark.parametrize("how, why", [("job", "it has a job"), ("charge", "it has an account charge"),
                                      ("lease", "it holds a live lease")])
def test_delete_refuses_a_used_account_and_leaves_its_row_and_its_screenshot_object(db, store, how, why):
    a = new_account(db, checked=True)
    shot, *_ = fill(db, store, a)
    make_used(db, a, how)
    cid = delete_command(db, a)
    run_delete(db, store)
    row = command(db, cid)
    assert row["status"] == "failed" and why in row["result"]["error"]
    assert account_row(db, a) is not None
    assert keys(store) == [shot]
    assert db.one("SELECT count(*) FROM account_shots WHERE account_id = %s", a)[0] == 1


# ---- delete: its own row and lease ---------------------------------------------------------------------------------
@mutation(COMMANDS, 'await conn.execute("UPDATE commands SET account_id = NULL WHERE id = %s", (c.id,))',
          'await conn.execute("SELECT 1 WHERE %s IS NOT NULL", (c.id,))',
          expect="assert row is not None and row[\"status\"] == \"done\" and row[\"account_id\"] is None")
def test_delete_takes_no_lease_and_neither_blocks_on_nor_deletes_its_own_command_row(db, store):
    a = new_account(db, checked=True)
    fill(db, store, a)
    during = {}

    async def spy():
        during["leases"] = db.one("SELECT count(*) FROM leases WHERE purpose = 'command'")[0]
        during["own"] = db.one("SELECT status, lease_id FROM commands WHERE kind = 'delete_account'")

    # a row that names the account it deletes (the api should leave it NULL, but a misshapen row must not wedge it)
    cid = new_command(db, "delete_account", account_id=a, args={"account_id": str(a)})
    failpoints.arm("account.delete.removed", spy)
    try:
        run_delete(db, store)
    finally:
        failpoints.disarm()
    assert during == {"leases": 0, "own": ("running", None)}
    row = command(db, cid)
    assert row is not None and row["status"] == "done" and row["account_id"] is None
    assert account_row(db, a) is None


# ---- delete: a crash never leaves a screenshot object --------------------------------------------------------------
CHILD = """
import asyncio, os
from accountkit import KitEngine, make_env
from relay_core.db import open_pool
from relay_core.jobs.commands import claim_command, execute
from relay_core.storage import ObjectStore

async def main():
    p = await open_pool(os.environ["T_DSN"], max_size=3)
    store = ObjectStore(os.environ["T_MINIO"], os.environ["T_AK"], os.environ["T_SK"], os.environ["T_BUCKET"])
    env = make_env(p, KitEngine(), store=store)
    c = await claim_command(env)
    await execute(env, c)
    await p.close()

asyncio.run(main())
"""


def crash_child(db, store, point):
    endpoint, access, secret = minio_settings()
    env = {**os.environ, "T_DSN": db.worker_dsn, "T_MINIO": endpoint, "T_AK": access, "T_SK": secret,
           "T_BUCKET": store.bucket, "PYTHONPATH": str(TESTS), "RELAY_FAILPOINTS": point + "=crash"}
    return subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, timeout=90).returncode


def test_a_crash_right_after_the_commit_leaves_no_screenshot_object(db, store):
    a = new_account(db, checked=True)
    fill(db, store, a)
    cid = delete_command(db, a)
    assert crash_child(db, store, "account.delete.committed") == 137      # os._exit: the process dies, no cleanup
    assert account_row(db, a) is None and children(db, a) == dict.fromkeys(children(db, a), 0)
    assert keys(store) == []
    assert command(db, cid)["status"] == "running"                        # left for the reaper


def test_a_crash_after_the_objects_went_but_before_the_commit_is_finished_by_a_rerun(db, store):
    a = new_account(db, checked=True)
    fill(db, store, a)
    cid = delete_command(db, a)
    assert crash_child(db, store, "account.delete.removed") == 137
    assert account_row(db, a) is not None and keys(store) == []           # nothing but a row whose object is gone
    db.run("UPDATE commands SET status = 'queued' WHERE id = %s", cid)    # the reaper's retry
    run_delete(db, store)
    assert account_row(db, a) is None and command(db, cid)["status"] == "done"
    assert keys(store) == []


# ---- wake, and the api cannot write what the worker writes ---------------------------------------------------------
def test_wake_clears_the_cooldown_and_every_rest_as_the_worker_role(db):
    a = new_account(db, checked=True)
    db.run("UPDATE accounts SET cooldown_until = now() + interval '1 h', cooldown_reason = 'busy' WHERE id = %s", a)
    db.run("INSERT INTO account_capacity (account_id, pool, source, rest_until, rest_reason) "
           "VALUES (%s, 'video', 'reported', now() + interval '1 h', 'out')", a)
    cid = new_command(db, "wake", args={"account_id": str(a)})

    async def scenario(p):
        await run_one(make_env(p, KitEngine()))

    worker(db, scenario)
    assert command(db, cid)["status"] == "done"
    assert account_row(db, a)["cooldown_until"] is None
    assert db.one("SELECT rest_until FROM account_capacity WHERE account_id = %s", a)[0] is None


POOL_SRC = "packages/relay-core/src/relay_core/pool.py"
ACCOUNTS = "packages/relay-core/src/relay_core/db/accounts.py"
WAKE_LOCK = "    if not await lock_account(conn, account_id):\n        return False\n"
WAKE_CAPACITY_FIRST = ('    await conn.execute("SELECT 1 FROM account_capacity WHERE account_id = %s ORDER BY pool '
                       'FOR UPDATE", (account_id,))\n')


@mutation(POOL_SRC, WAKE_LOCK, WAKE_CAPACITY_FIRST,
          expect="assert command(db, cid)[\"status\"] == \"done\", command(db, cid)")
def test_the_wake_command_beside_a_claim_holding_the_account_row_does_not_deadlock(db):
    """A claim locks the account row, then a capacity row. The wake command takes them in the same order: taking a
    capacity row first deadlocks with that claim and Postgres aborts one of them."""
    a = new_account(db, checked=True)
    db.run("INSERT INTO account_capacity (account_id, pool, source, rest_until, rest_reason) "
           "VALUES (%s, 'video', 'reported', now() + interval '1 h', 'out')", a)
    cid = new_command(db, "wake", args={"account_id": str(a)})

    async def scenario(p):
        async with await psycopg.AsyncConnection.connect(db.worker_dsn, options="-c search_path=relay") as claim:
            async with claim.transaction():                    # the claim's order: account, then capacity
                await claim.execute("SELECT 1 FROM accounts WHERE id = %s FOR NO KEY UPDATE", (a,))
                waking = asyncio.create_task(run_one(make_env(p, KitEngine())))
                await asyncio.sleep(0.5)       # the wake command is now waiting on a lock; under Postgres's 1 s
                                               # deadlock_timeout, so it, not the claim, is the deadlock victim
                await claim.execute("SELECT 1 FROM account_capacity WHERE account_id = %s FOR UPDATE", (a,))
            await waking

    worker(db, scenario)
    assert command(db, cid)["status"] == "done", command(db, cid)
    assert db.one("SELECT rest_until FROM account_capacity WHERE account_id = %s", a)[0] is None


DELETE_LOCK = "    if not await lock_account(conn, account_id, delete=True):\n"
DELETE_CAPACITY_FIRST = ('    await conn.execute("SELECT 1 FROM account_capacity WHERE account_id = %s ORDER BY pool '
                         'FOR UPDATE", (account_id,))\n'
                         "    if not await lock_account(conn, account_id, delete=True):\n")


@mutation(ACCOUNTS, DELETE_LOCK, DELETE_CAPACITY_FIRST, expect="deadlock detected")
def test_the_delete_command_beside_a_claim_holding_the_account_row_does_not_deadlock(db):
    """A claim locks the account row, then a capacity row. The delete takes the account row before the capacity rows
    too: taking a capacity row first deadlocks with that claim and Postgres aborts one of them."""
    a = new_account(db, checked=True)
    db.run("INSERT INTO account_capacity (account_id, pool, source) VALUES (%s, 'video', 'reset')", a)
    cid = delete_command(db, a)

    async def scenario(p):
        async with await psycopg.AsyncConnection.connect(db.worker_dsn, options="-c search_path=relay") as claim:
            async with claim.transaction():                    # the claim's order: account, then capacity
                await claim.execute("SELECT 1 FROM accounts WHERE id = %s FOR NO KEY UPDATE", (a,))
                deleting = asyncio.create_task(run_one(make_env(p, KitEngine())))
                await asyncio.sleep(1.5)                       # the delete command is now waiting on a lock
                await claim.execute("SELECT 1 FROM account_capacity WHERE account_id = %s FOR UPDATE", (a,))
            await deleting

    worker(db, scenario)
    assert command(db, cid)["status"] == "done", command(db, cid)
    assert account_row(db, a) is None


@pytest.mark.parametrize("assignment", ["cooldown_until = NULL", "cooldown_reason = NULL", "check_due_at = now()",
                                        "proxy_ok = true", "proxy_checked_at = now()", "strikes = 0"])
def test_the_api_role_cannot_write_a_column_the_worker_commands_write(db, assignment):
    a = new_account(db, checked=True)
    with psycopg.connect(db.api_dsn, autocommit=True, options="-c search_path=relay") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE accounts SET %s WHERE id = %%s" % assignment, (a,))
        c.execute("INSERT INTO commands (provider, kind, args) VALUES ('fake', 'wake', %s::jsonb)",
                  ('{"account_id": "%s"}' % a,))                        # what the api can do: queue it
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM accounts WHERE id = %s", (a,))
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'wake'")[0] == 1


def test_the_commands_run_under_the_worker_role(db):
    cid = new_command(db, "check_all")

    async def scenario(p):
        async with p.connection() as conn:
            who = (await (await conn.execute("SELECT current_user")).fetchone())[0]
        await run_one(make_env(p, KitEngine()))
        return who

    assert worker(db, scenario) == "relay_worker" and command(db, cid)["status"] == "done"


# ---- check, check all, proxy re-check ------------------------------------------------------------------------------
def test_the_check_command_runs_one_check_and_writes_the_schedule_columns(db):
    a = new_account(db, checked=False, due_s=None)
    cid = new_command(db, "check", args={"account_id": str(a)})

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")
        await run_one(make_env(p, engine))

    worker(db, scenario)
    row = command(db, cid)
    assert row["status"] == "done" and row["result"]["outcome"] == "checked"
    acct = account_row(db, a)
    assert acct["last_checked_at"] is not None and acct["identity"] == "who@x" and acct["check_due_at"] is not None


def test_the_check_command_fails_on_a_busy_or_disabled_account_and_follows_a_merge(db, store):
    busy = new_account(db, checked=True)
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, 'command', 'x', "
           "clock_timestamp() + interval '1 min', clock_timestamp() + interval '1 min')", busy)
    off = new_account(db, state="disabled", checked=True)
    old = new_account(db, identity="who@x", checked=True)
    new = new_account(db, checked=False)
    c_busy = new_command(db, "check", account_id=busy)
    c_off = new_command(db, "check", account_id=off)
    c_new = new_command(db, "check", account_id=new)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")
        env = make_env(p, engine, store=store)
        for _ in range(3):
            await run_one(env)

    worker(db, scenario)
    assert command(db, c_busy)["status"] == "failed" and "busy" in command(db, c_busy)["result"]["error"]
    assert command(db, c_off)["status"] == "failed" and "skipped" in command(db, c_off)["result"]["error"]
    merged = command(db, c_new)
    assert merged["status"] == "done" and merged["result"]["outcome"] == "merged" and merged["account_id"] == old


def test_check_all_makes_every_account_but_a_disabled_one_due_now(db):
    a = new_account(db, checked=True, due_s=3600)
    b = new_account(db, state="dead", checked=True, due_s=7200)
    off = new_account(db, state="disabled", checked=True, due_s=3600)
    new_account(db, provider="other", checked=True, due_s=3600)
    cid = new_command(db, "check_all")

    async def scenario(p):
        await run_one(make_env(p, KitEngine()))

    worker(db, scenario)
    assert command(db, cid)["result"] == {"scheduled": 2}
    for acct in (a, b):
        assert db.one("SELECT check_due_at <= clock_timestamp() FROM accounts WHERE id = %s", acct)[0]
    assert db.one("SELECT check_due_at > clock_timestamp() + interval '50 min' FROM accounts WHERE id = %s", off)[0]
    assert db.one("SELECT count(*) FROM accounts WHERE provider = 'other' AND check_due_at > now()")[0] == 1


def test_proxy_recheck_probes_stores_the_verdict_and_makes_the_account_due(db):
    ok = new_account(db, checked=True, due_s=3600, proxy=("10.0.0.1", 8080))
    bad = new_account(db, checked=True, due_s=3600, proxy=("10.0.0.2", 8080))
    c_ok = new_command(db, "proxy_recheck", account_id=ok)
    c_bad = new_command(db, "proxy_recheck", account_id=bad)

    async def scenario(p):
        engine = KitEngine()
        env = make_env(p, engine)
        engine.verdict = ProxyVerdict(True)
        await run_one(env)
        engine.verdict = ProxyVerdict(False, "timed out")
        await run_one(env)

    worker(db, scenario)
    assert command(db, c_ok)["status"] == "done" and command(db, c_bad)["status"] == "done"
    good, broken = account_row(db, ok), account_row(db, bad)
    assert good["proxy_ok"] is True and good["proxy_checked_at"] is not None
    assert (broken["proxy_ok"], broken["last_error"]) == (False, "timed out")
    for acct in (ok, bad):
        assert db.one("SELECT check_due_at <= clock_timestamp() FROM accounts WHERE id = %s", acct)[0]


@mutation("packages/relay-core/src/relay_core/jobs/commands.py", "proxy_bad_effects(env, verdict[\"reason\"])",
          "[]", expect="no claim goes to an account behind a dead proxy")
def test_a_failed_proxy_recheck_rests_the_account_on_every_pool(db):
    a = new_account(db, checked=True, proxy=("10.0.0.2", 8080))
    cid = new_command(db, "proxy_recheck", account_id=a)
    k = jobkit.key(db)
    jobkit.run(db, lambda p: jobkit.create(p, k, "x"))

    async def scenario(p):
        engine = KitEngine()
        engine.verdict = ProxyVerdict(False, "timed out")
        spec = KitSpec()
        spec.caps = dataclasses.replace(spec.caps, proxy_rest_s=900)
        assert await run_one(make_env(p, engine, spec=spec))
        return await jobkit.claim(p)

    assert worker(db, scenario) is None, "no claim goes to an account behind a dead proxy"
    assert command(db, cid)["status"] == "done"
    assert db.all("SELECT pool FROM account_capacity WHERE account_id = %s AND rest_until > clock_timestamp() "
                  "+ interval '800 s' ORDER BY pool", a) == [("image",), ("video",)]


def test_proxy_recheck_fails_without_a_proxy_or_without_a_probe(db):
    plain = new_account(db, checked=True)
    proxied = new_account(db, checked=True, proxy=("10.0.0.1", 1))
    c_plain = new_command(db, "proxy_recheck", account_id=plain)
    c_noprobe = new_command(db, "proxy_recheck", account_id=proxied)

    async def scenario(p):
        await run_one(make_env(p, KitEngine()))
        await run_one(make_env(p, KitEngine(), spec=KitSpec(probe=False)))

    worker(db, scenario)
    assert "no proxy" in command(db, c_plain)["result"]["error"]
    assert "no proxy probe" in command(db, c_noprobe)["result"]["error"]
