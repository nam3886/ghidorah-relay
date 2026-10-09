"""The command runner (contracts 4.4, 5.1; invariants I14, I24, I28): the claim, the fenced hard-bounded lease, the
redacted failures, the engine kinds (recover, catalog refresh), the catalog scheduler and the loop."""

import asyncio
import time

import psycopg
import pytest
from accountkit import (
    KitEngine,
    KitSpec,
    account_row,
    beating,
    command,
    done_job,
    keys,
    make_env,
    new_account,
    new_command,
    object_store,
    run_as,
)
from enginekit import until

from relay_core.budget import BrowserBudget, Purpose
from relay_core.db import tx
from relay_core.jobs import commands
from relay_core.jobs.commands import CommandLoop, recover_slot, run_one, schedule_catalog_refresh
from relay_core.providers import Output
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/jobs/commands.py"
FETCHER = "packages/relay-core/src/relay_core/jobs/fetcher.py"
SNAPSHOTS = {"snapshots": {"templates": {"items": [1, 2]}}}
PROBED = "probed = await probe(path, out.mime)"
NOT_PROBED = "probed = Probed(1, 1, 1.0)"
KEEP_OBJECTS = "await _remove_unreferenced(env, stored)\n        raise"
KEEP_OBJECTS_MUTATED = "pass\n        raise"
KEEP_WINNERS_ONLY = "remove what no row names\n    await _remove_unreferenced(env, stored)\n"
KEEP_LOSERS_TOO = "remove what no row names\n"
REQUEUE = "WHERE id = %s AND status = 'running' AND attempts < %s"
REQUEUE_ALWAYS = "WHERE id = %s AND status = 'running' AND %s > 0"
NO_RETRY = "except _RETRYABLE as e:"
NEVER_RETRY = "except () as e:"
RECOVERY_CAUGHT = "    except Exception as e:\n        log.error(\"command %s (%s): the recovery write failed"
RECOVERY_UNCAUGHT = "    except ZeroDivisionError as e:\n        log.error(\"command %s (%s): the recovery write failed"
SETTLE = "            settle()\n            await shielded"
NO_SETTLE = "            await shielded"


@pytest.fixture
def store():
    with object_store() as s:
        yield s


def worker(db, scenario, **kw):
    return run_as(db.worker_dsn, scenario, **kw)


def snapshots(db):
    return db.all("SELECT name FROM provider_snapshots ORDER BY name")


@mutation(SRC, '"started_at = clock_timestamp() "', '"result = NULL "',
          expect="assert db.one(\"SELECT status, started_at IS NOT NULL FROM commands WHERE id = %s")
def test_a_claimed_command_records_when_it_started(db):
    a = new_account(db, checked=True)
    cid = new_command(db, "wake", account_id=a)
    assert db.one("SELECT started_at FROM commands WHERE id = %s", cid)[0] is None

    async def scenario(p):
        return await run_one(make_env(p, KitEngine()))

    assert worker(db, scenario)
    assert db.one("SELECT status, started_at IS NOT NULL FROM commands WHERE id = %s", cid) == ("done", True)


def test_a_command_runs_once_under_a_command_lease_with_a_hard_bound_and_ends_done(db):
    a = new_account(db, checked=True)
    cid = new_command(db, "refresh_catalog", account_id=a)
    seen = {}

    async def scenario(p):
        engine = KitEngine()
        engine.results["refresh_catalog"] = SNAPSHOTS

        async def spy(ctx, kind, args):
            seen["lease"] = db.one(
                "SELECT l.purpose, l.hard_until IS NOT NULL, c.status, c.attempts, c.lease_id = l.id "
                "FROM leases l, commands c WHERE c.id = %s", cid)
        engine.on_command = spy
        return await run_one(make_env(p, engine))

    assert worker(db, scenario) is True
    assert seen["lease"] == ("command", True, "running", 1, True)
    row = command(db, cid)
    assert row["status"] == "done" and row["result"] == {"snapshots": ["templates"]}
    assert snapshots(db) == [("templates",)]
    assert db.one("SELECT count(*) FROM leases")[0] == 0


def test_concurrent_runners_never_run_a_command_twice(db):
    ids = [new_command(db, "list_conversations", account_id=new_account(db, checked=True)) for _ in range(3)]

    async def scenario(p):
        engine = KitEngine()
        env = make_env(p, engine)
        got = await asyncio.gather(*(run_one(env) for _ in range(8)))
        return got, engine.commanded

    got, commanded = worker(db, scenario, max_size=10)
    assert sum(got) == 3 and len(commanded) == 3
    assert [command(db, i)["attempts"] for i in ids] == [1, 1, 1]
    assert {command(db, i)["status"] for i in ids} == {"done"}


@mutation(SRC, "            await leases.fence(conn, c.lease_id)\n        if apply is not None:",
          "            pass\n        if apply is not None:",
          expect="assert row[\"status\"] == \"failed\" and \"lease\" in row[\"result\"][\"error\"]")
def test_a_command_whose_lease_is_gone_is_failed_and_its_result_is_not_applied(db):
    a = new_account(db, checked=True)
    cid = new_command(db, "refresh_catalog", account_id=a)

    async def scenario(p):
        engine = KitEngine()
        engine.results["refresh_catalog"] = SNAPSHOTS

        async def reaped(ctx, kind, args):
            async with tx(p) as conn:
                await conn.execute("DELETE FROM leases")
        engine.on_command = reaped
        return await run_one(make_env(p, engine))

    assert worker(db, scenario) is True
    row = command(db, cid)
    assert row["status"] == "failed" and "lease" in row["result"]["error"]
    assert snapshots(db) == []


@mutation(SRC, "async with env.keeper.holding(c.lease_id) as settle:", "if (settle := lambda: None):",
          expect="the command result was dropped though its lease could have been kept")
def test_a_command_result_applied_late_still_finds_its_lease_because_the_keeper_holds_it_until_then(db, monkeypatch):
    a = new_account(db, checked=True)
    cid = new_command(db, "refresh_catalog", account_id=a)
    from relay_core.jobs import commands
    original = commands._finish_leased

    async def late(*args, **kw):                           # the apply starts well after the lease's TTL
        await asyncio.sleep(2.0)
        return await original(*args, **kw)
    monkeypatch.setattr(commands, "_finish_leased", late)

    async def scenario(p):
        engine = KitEngine()
        engine.results["refresh_catalog"] = SNAPSHOTS
        env = make_env(p, engine, ttl_s=1.5)
        async with beating(env):
            return await run_one(env)

    assert worker(db, scenario) is True
    assert command(db, cid)["status"] == "done", "the command result was dropped though its lease could have been kept"
    assert snapshots(db) == [("templates",)] and db.one("SELECT count(*) FROM leases")[0] == 0


@mutation(SRC, SETTLE, NO_SETTLE, expect="a command that was applied was logged as a lost lease")
def test_a_heartbeat_between_the_commit_and_the_return_of_a_command_write_does_not_cancel_the_holder(
        db, monkeypatch, caplog):
    a = new_account(db, checked=True)
    cid = new_command(db, "refresh_catalog", account_id=a)
    original = commands._done
    holder = []

    async def beat_after_commit(*args, **kw):             # the commit deleted the lease; a beat runs before the return
        await original(*args, **kw)
        await holder[0].keeper.beat()
        await asyncio.sleep(0)
    monkeypatch.setattr(commands, "_done", beat_after_commit)

    async def scenario(p):
        engine = KitEngine()
        engine.results["refresh_catalog"] = SNAPSHOTS
        env = make_env(p, engine)
        holder.append(env)
        return await run_one(env)

    assert worker(db, scenario) is True
    assert command(db, cid)["status"] == "done"
    assert not [r for r in caplog.records if "lost" in r.getMessage()], \
        "a command that was applied was logged as a lost lease"


@mutation(SRC, KEEP_WINNERS_ONLY, KEEP_LOSERS_TOO, expect="an object that no row names was left under the job")
def test_two_recovers_that_return_the_same_output_leave_one_object_and_it_is_the_one_a_row_names(db, store):
    a = new_account(db, checked=True)
    b = new_account(db, checked=True)
    k = db.key(("fake", "video", None, None))
    job = done_job(db, k, a, ref="r1", upstream_id="up-old")

    async def scenario(p):
        engine = KitEngine()
        arrived, both = [], asyncio.Event()

        async def barrier(ctx, kind, args):                # both read the job's outputs before either commits
            arrived.append(kind)
            if len(arrived) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 5)
        engine.on_command = barrier
        engine.results["recover"] = {"outputs": [Output("fake://same", "video/mp4", 0, "up-same")]}
        env = make_env(p, engine, store=store)
        cmds = [new_command(db, "recover", account_id=acct, job_id=job) for acct in (a, b)]
        await asyncio.gather(run_one(env), run_one(env))
        return cmds

    before = set(keys(store))
    cmds = worker(db, scenario)
    assert [command(db, c)["status"] for c in cmds] == ["done", "done"]
    named = {r[0] for r in db.all("SELECT object_key FROM job_media WHERE job_id = %s", job)}
    left = [x for x in keys(store) if x.startswith(str(job) + "/output/")]
    assert len(db.all("SELECT 1 FROM job_media WHERE job_id = %s AND upstream_id = 'up-same'", job)) == 1
    assert [x for x in left if x not in named] == [], "an object that no row names was left under the job"
    assert len([x for x in left if x not in before]) == 1


@mutation(SRC, REQUEUE, REQUEUE_ALWAYS, expect="the command must fail once it has used its claims")
def test_a_command_that_loses_a_deadlock_is_queued_again_keeping_its_attempts_and_fails_after_the_bound(
        db, monkeypatch):
    a = new_account(db, checked=True)
    cid = new_command(db, "delete_account", args={"account_id": str(a)})
    real, losses = commands.delete_unused_account, []

    async def always_loses(conn, *args, **kw):
        if len(losses) < commands.MAX_DEADLOCK_ATTEMPTS + 5:
            losses.append(1)
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return await real(conn, *args, **kw)
    monkeypatch.setattr(commands, "delete_unused_account", always_loses)

    async def scenario(p):
        env = make_env(p, KitEngine())
        seen = []
        for _ in range(commands.MAX_DEADLOCK_ATTEMPTS + 2):
            if not await run_one(env):
                break
            seen.append(command(db, cid))
        return seen

    seen = worker(db, scenario)
    assert [(s["status"], s["attempts"]) for s in seen] == (
        [("queued", n) for n in range(1, commands.MAX_DEADLOCK_ATTEMPTS)] + [("failed", commands.MAX_DEADLOCK_ATTEMPTS)]
    ), "the command must fail once it has used its claims"
    assert "deadlock" in seen[-1]["result"]["error"] and account_row(db, a) is not None


@mutation(SRC, NO_RETRY, NEVER_RETRY, expect="a command aborted by a deadlock must go back to the queue")
def test_a_delete_that_loses_one_deadlock_is_queued_again_and_then_succeeds(db, monkeypatch):
    a = new_account(db, checked=True)
    cid = new_command(db, "delete_account", args={"account_id": str(a)})
    real, losses = commands.delete_unused_account, []

    async def loses_once(conn, *args, **kw):
        if not losses:
            losses.append(1)
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return await real(conn, *args, **kw)
    monkeypatch.setattr(commands, "delete_unused_account", loses_once)

    async def scenario(p):
        env = make_env(p, KitEngine())
        first = (await run_one(env), command(db, cid))
        second = (await run_one(env), command(db, cid))
        return first, second

    first, second = worker(db, scenario)
    assert first[1]["status"] == "queued", "a command aborted by a deadlock must go back to the queue"
    assert (first[1]["attempts"], first[1]["lease_id"]) == (1, None)
    assert second[1]["status"] == "done" and second[1]["attempts"] == 2
    assert account_row(db, a) is None


@mutation(SRC, RECOVERY_CAUGHT, RECOVERY_UNCAUGHT, expect="recovery boom")
def test_a_recovery_write_that_fails_is_logged_redacted_and_the_loop_survives(db, monkeypatch, caplog):
    a = new_account(db, checked=True)
    cid = new_command(db, "delete_account", args={"account_id": str(a)})

    async def loses(conn, *args, **kw):
        raise psycopg.errors.DeadlockDetected("deadlock detected")

    async def broken(env, c):
        raise RuntimeError("recovery boom sessionid=abcdef0123456789abcdef0123456789")
    monkeypatch.setattr(commands, "delete_unused_account", loses)
    monkeypatch.setattr(commands, "_requeue", broken)

    async def scenario(p):
        env = make_env(p, KitEngine())
        with caplog.at_level("ERROR"):
            return await run_one(env)

    assert worker(db, scenario) is True                       # the runner came back, nothing escaped
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "recovery write failed" in text and str(cid) in text and "recovery boom" in text
    assert "abcdef0123456789abcdef0123456789" not in text
    assert command(db, cid)["status"] == "running"            # left to the reaper's bound, which fails it


def test_a_hung_command_is_cut_at_its_bound_and_frees_the_account(db):
    a = new_account(db, checked=True)
    cid = new_command(db, "list_conversations", account_id=a)

    async def scenario(p):
        engine = KitEngine()

        async def hang(ctx, kind, args):
            await asyncio.sleep(60)
        engine.on_command = hang
        env = make_env(p, engine, command_hard_s=lambda kind: 1.0)
        started = time.monotonic()
        await run_one(env)
        return time.monotonic() - started

    assert worker(db, scenario) < 2.0
    row = command(db, cid)
    assert row["status"] == "failed" and "time limit" in row["result"]["error"]
    assert db.one("SELECT count(*) FROM leases")[0] == 0


def test_a_failure_text_over_2000_characters_is_stored_as_omitted_len(db):
    cid = new_command(db, "list_conversations", account_id=new_account(db, checked=True))
    secret = "sk-" + "A1b2C3d4" * 300

    async def scenario(p):
        engine = KitEngine()

        async def boom(ctx, kind, args):
            raise RuntimeError(secret)
        engine.on_command = boom
        await run_one(make_env(p, engine))

    worker(db, scenario)
    assert command(db, cid)["result"] == {"error": "[omitted len=%d]" % len(secret)}


def test_a_command_on_a_busy_account_waits_in_the_queue_until_the_account_is_free(db):
    a = new_account(db, checked=True)
    cid = new_command(db, "list_conversations", account_id=a)
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES "
           "(%s, 'job', 'x', clock_timestamp() + interval '1 min', clock_timestamp() + interval '1 min')", a)

    async def scenario(p):
        env = make_env(p, KitEngine())
        first = await run_one(env)
        db.run("DELETE FROM leases")
        return first, await run_one(env)

    assert worker(db, scenario) == (False, True)
    assert command(db, cid)["status"] == "done"


def test_a_catalog_refresh_without_an_account_picks_an_available_checked_one(db):
    new_account(db, state="dead", checked=True)
    new_account(db, checked=False)
    good = new_account(db, checked=True)
    cid = new_command(db, "refresh_catalog")
    seen = []

    async def scenario(p):
        engine = KitEngine()
        engine.results["refresh_catalog"] = SNAPSHOTS

        async def spy(ctx, kind, args):
            seen.append(ctx.account.id)
        engine.on_command = spy
        return await run_one(make_env(p, engine))

    assert worker(db, scenario) is True and seen == [str(good)]
    assert command(db, cid)["status"] == "done"


def test_a_command_with_an_unknown_kind_or_a_missing_account_is_failed_not_left_queued(db):
    odd = new_command(db, "no_such_kind")
    ghost = new_command(db, "list_conversations", args={"account_id": "00000000-0000-0000-0000-000000000001"})

    async def scenario(p):
        env = make_env(p, KitEngine())
        return [await run_one(env), await run_one(env)]

    worker(db, scenario)
    assert command(db, odd)["status"] == "failed" and "unknown command kind" in command(db, odd)["result"]["error"]
    assert command(db, ghost)["status"] == "failed" and "no such account" in command(db, ghost)["result"]["error"]


def test_a_drained_provider_claims_no_command(db):
    cid = new_command(db, "check_all")

    async def scenario(p):
        env = make_env(p, KitEngine(), settings=FakeSettings(KitSpec(), fake__claims_enabled=False))
        return await run_one(env)

    assert worker(db, scenario) is False
    assert command(db, cid)["status"] == "queued"


def test_a_browser_command_holds_a_command_token_and_waits_without_one(db):
    a = new_account(db, checked=True)
    cid = new_command(db, "list_conversations", account_id=a)

    async def scenario(p):
        budget = BrowserBudget(4)
        engine = KitEngine()
        during = {}

        async def spy(ctx, kind, args):
            during["busy"] = budget.busy_by(Purpose.COMMAND)
            during["token"] = ctx.browser_token
        engine.on_command = spy
        env = make_env(p, engine, spec=KitSpec(execution="browser"), budget=budget)
        check = budget.try_acquire(Purpose.CHECK)                    # check + command <= 1 together
        blocked = await run_one(env)
        check.release()
        ran = await run_one(env)
        return blocked, ran, during, budget.busy

    blocked, ran, during, after = worker(db, scenario)
    assert (blocked, ran, after) == (False, True, 0)
    assert during["busy"] == 1 and during["token"] is not None
    assert command(db, cid)["status"] == "done"


# ---- recover -------------------------------------------------------------------------------------------------------
def recover_setup(db):
    a = new_account(db, checked=True)
    key = db.key(("fake", "video", None, None))
    job = done_job(db, key, a, ref="r1", upstream_id="up-old")
    return a, job


def outputs(*ids):
    return {"outputs": [Output("fake://" + i, "video/mp4", n, i) for n, i in enumerate(ids)]}


def test_recover_stores_the_objects_first_attaches_only_new_outputs_and_is_idempotent(db, store):
    a, job = recover_setup(db)

    async def scenario(p):
        engine = KitEngine()
        engine.results["recover"] = outputs("up-old", "up-new")
        env = make_env(p, engine, store=store)
        first = new_command(db, "recover", account_id=a, job_id=job)
        await run_one(env)
        second = new_command(db, "recover", account_id=a, job_id=job)
        await run_one(env)
        return first, second, engine

    first, second, engine = worker(db, scenario)
    assert command(db, first)["result"]["attached"] == 1 and command(db, second)["result"]["attached"] == 0
    assert keys(store) == ["%s/output/%d.mp4" % (job, recover_slot(first, 0))]   # "up-old" was never fetched
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND role = 'output'", job)[0] == 2
    assert db.one("SELECT status, account_id FROM jobs WHERE id = %s", job) == ("done", a)
    assert db.one("SELECT width, height, duration_s > 0 FROM job_media WHERE job_id = %s AND upstream_id = 'up-new'",
                  job) == (64, 48, True), "probed like a fetched output"
    assert db.one("SELECT count(*) FROM job_events WHERE job_id = %s AND stage = 'recovered'", job)[0] == 1


SLOT = "slot = recover_slot(c.id, len(stored))"
SLOT_FROM_COUNT = "slot = len(stored) + 1"


@mutation(SRC, SLOT, SLOT_FROM_COUNT, expect="two recovers wrote the same object key")
def test_two_recovers_of_one_job_at_once_never_write_the_same_object_key(db, store):
    a, job = recover_setup(db)
    b = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()
        engine.results["recover"] = lambda args: outputs(args["which"])
        env = make_env(p, engine, store=store)
        cmds = [new_command(db, "recover", account_id=acct, job_id=job, args={"which": name})
                for acct, name in ((a, "up-a"), (b, "up-b"))]
        await asyncio.gather(run_one(env), run_one(env))
        return cmds

    cmds = worker(db, scenario, max_size=10)
    assert [command(db, c)["status"] for c in cmds] == ["done", "done"]
    media = sorted(r[0] for r in db.all("SELECT object_key FROM job_media WHERE job_id = %s AND upstream_id IN "
                                        "('up-a', 'up-b')", job))
    assert len(set(media)) == 2 and media == sorted(keys(store)), "two recovers wrote the same object key"


def test_recover_removes_the_objects_it_stored_when_the_job_refuses_it(db, store):
    a = new_account(db, checked=True)
    job = done_job(db, db.key(("fake", "video", None, None)), a, status="done")
    # a job that cannot be recovered: not finished
    db.run("UPDATE jobs SET status = 'queued', sent_at = NULL, finished_at = NULL WHERE id = %s", job)
    db.run("DELETE FROM job_media WHERE job_id = %s", job)

    async def scenario(p):
        engine = KitEngine()
        engine.results["recover"] = outputs("up-1")
        cid = new_command(db, "recover", account_id=a, job_id=job)
        await run_one(make_env(p, engine, store=store))
        return cid

    cid = worker(db, scenario)
    row = command(db, cid)
    assert row["status"] == "failed" and "finished job" in row["result"]["error"]
    assert keys(store) == [], "the object was stored first, outside any transaction, and removed with the refusal"


@mutation(SRC, KEEP_OBJECTS, KEEP_OBJECTS_MUTATED,
          expect="the object this run stored is not left under a live job's prefix")
@mutation(SRC, "            await leases.fence(conn, c.lease_id)\n        if apply is not None:",
          "            pass\n        if apply is not None:",
          expect="assert command(db, cid)[\"status\"] == \"failed\"")
def test_a_recover_whose_lease_is_gone_when_its_result_is_applied_changes_neither_the_job_nor_money(db, store):
    """The recovery is applied in the same transaction as the lease fence: once the lease is gone (hard bound passed,
    reaper took it) the job keeps its state, gets no output and no recharge, and the command fails."""
    a, job = recover_setup(db)

    async def scenario(p):
        engine = KitEngine()
        engine.results["recover"] = outputs("up-new")

        async def lose_the_lease(ctx, kind, args):
            db.run("DELETE FROM leases WHERE purpose = 'command'")
        engine.on_command = lose_the_lease
        cid = new_command(db, "recover", account_id=a, job_id=job)
        await run_one(make_env(p, engine, store=store))
        return cid

    cid = worker(db, scenario)
    assert command(db, cid)["status"] == "failed"
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND role = 'output'", job) == (1,)
    assert db.one("SELECT count(*) FROM job_events WHERE job_id = %s AND stage = 'recovered'", job) == (0,)
    assert keys(store) == [], "the object this run stored is not left under a live job's prefix"


@mutation(FETCHER, PROBED, NOT_PROBED,
          expect="assert row[\"status\"] == \"failed\" and \"render.file_unreadable\" in row[\"result\"][\"")
def test_recover_refuses_a_file_that_is_not_what_its_mime_says(db, store):
    a, job = recover_setup(db)

    async def scenario(p):
        engine = KitEngine()
        engine.mode("garbage")
        engine.results["recover"] = outputs("up-new")
        cid = new_command(db, "recover", account_id=a, job_id=job)
        await run_one(make_env(p, engine, store=store))
        return cid

    row = command(db, worker(db, scenario))
    assert row["status"] == "failed" and "render.file_unreadable" in row["result"]["error"]
    assert keys(store) == [] and db.one("SELECT count(*) FROM job_media WHERE job_id = %s", job) == (1,)


def test_recover_without_outputs_or_an_upstream_id_fails_the_command(db, store):
    a, job = recover_setup(db)

    async def scenario(p):
        engine = KitEngine()
        engine.results["recover"] = {"outputs": []}
        cid = new_command(db, "recover", account_id=a, job_id=job)
        await run_one(make_env(p, engine, store=store))
        return cid

    assert command(db, worker(db, scenario))["status"] == "failed"


# ---- the catalog scheduler -----------------------------------------------------------------------------------------
def test_the_catalog_refresh_is_queued_once_per_interval_and_not_while_drained(db):
    async def scenario(p):
        env = make_env(p, KitEngine(), spec=KitSpec(catalog_refresh_s=3600))
        first = await schedule_catalog_refresh(env)
        second = await schedule_catalog_refresh(env)                  # one is waiting
        db.run("UPDATE commands SET status = 'done', finished_at = clock_timestamp()")
        third = await schedule_catalog_refresh(env)                   # done a moment ago: not due
        db.run("UPDATE commands SET created_at = created_at - interval '2 hours'")
        fourth = await schedule_catalog_refresh(env)                  # due again
        db.run("UPDATE commands SET status = 'done', finished_at = clock_timestamp(), "
               "created_at = created_at - interval '2 hours'")
        drained = make_env(p, KitEngine(), spec=KitSpec(catalog_refresh_s=3600),
                           settings=FakeSettings(KitSpec(), fake__claims_enabled=False))
        none = KitSpec(catalog_refresh_s=None)
        return first, second, third, fourth, await schedule_catalog_refresh(drained), \
            await schedule_catalog_refresh(make_env(p, KitEngine(), spec=none))

    assert worker(db, scenario) == (True, False, False, True, False, False)
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'refresh_catalog'")[0] == 2


def test_concurrent_schedulers_queue_one_refresh(db):
    async def scenario(p):
        env = make_env(p, KitEngine(), spec=KitSpec(catalog_refresh_s=3600))
        return await asyncio.gather(*(schedule_catalog_refresh(env) for _ in range(6)))

    assert sum(worker(db, scenario)) == 1


# ---- the loop ------------------------------------------------------------------------------------------------------
def test_the_loop_runs_queued_commands_queues_the_refresh_and_starts_due_checks(db):
    good = new_account(db, checked=True, due_s=-5)
    waiting = new_account(db, checked=True, due_s=3600)
    cid = new_command(db, "list_conversations", account_id=waiting)

    async def scenario(p):
        engine = KitEngine()
        engine.results["refresh_catalog"] = SNAPSHOTS
        env = make_env(p, engine, spec=KitSpec(catalog_refresh_s=3600, titles=False))
        loop, stop = CommandLoop(env, housekeeping_s=0.05), asyncio.Event()
        task = asyncio.create_task(loop.run(stop, tick_s=0.02))
        try:
            await until(lambda: command(db, cid)["status"] == "done" and snapshots(db) and engine.checked,
                        what="the command, the catalog refresh and a check")
        finally:
            stop.set()
            await asyncio.wait_for(task, 10)
        return engine

    engine = worker(db, scenario)
    assert str(good) in engine.checked and account_row(db, good)["last_checked_at"] is not None
