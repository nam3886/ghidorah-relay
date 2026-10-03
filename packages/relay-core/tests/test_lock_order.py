"""The key wallet and the command rows in the one lock order (lease, job, account, capacity, account charges, key
wallet; a command row after its lease). Two real sessions each, no raw SQL: the pauses are the repo's own failpoints,
or a wrapper that waits before calling the real function. A cycle shows as `DeadlockDetected` in one session."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from accountkit import KitEngine, command, make_env, new_account, new_command, run_as
from jobkit import CAPS, POOLS, PROVIDER, account, key, run
from lifecyclekit import age, claim_new, due, reaper, row

from relay_core import failpoints, leases
from relay_core.db import delete_unused_account, tx
from relay_core.jobs import commands, store
from relay_core.providers import Accepted, Cooldown, Fail, Failed, PollResult, SubmitResult
from relay_core.testing.mutation import mutation

FAIL = "packages/relay-core/src/relay_core/jobs/fail.py"
APPLY = "packages/relay-core/src/relay_core/jobs/apply.py"
COMMANDS = "packages/relay-core/src/relay_core/jobs/commands.py"

ACCOUNT_BEFORE_WALLET = """    if not order_completed:
        await pool.refund_job(conn, job_id, pools)
    for (charge_id,) in charges:
        await billing.refund(conn, charge_id)
"""
WALLET_BEFORE_ACCOUNT = """    for (charge_id,) in charges:
        await billing.refund(conn, charge_id)
    if not order_completed:
        await pool.refund_job(conn, job_id, pools)
"""
SUBMIT_LOCK = "await _lock_effect_account(conn, job, result.account)"
POLL_LOCK = "await _lock_effect_account(conn, job, result.effects)"
LEASE_FIRST = """    if lease_id is not None:                            # lease row before command row
        await leases.release(conn, lease_id)
    await conn.execute(
        "UPDATE commands SET status = 'failed', result = %s, finished_at = clock_timestamp() "
        "WHERE id = %s AND status IN ('queued', 'running')", (Jsonb({"error": redact_log(message)}), command_id))
"""
COMMAND_FIRST = """    await conn.execute(
        "UPDATE commands SET status = 'failed', result = %s, finished_at = clock_timestamp() "
        "WHERE id = %s AND status IN ('queued', 'running')", (Jsonb({"error": redact_log(message)}), command_id))
    if lease_id is not None:
        await leases.release(conn, lease_id)
"""
REQUEUE_LEASE_FIRST = """        if c.lease_id is not None:                      # lease row before command row
            await leases.release(conn, c.lease_id)
        cur = await conn.execute(
            "UPDATE commands SET status = 'queued', lease_id = NULL, started_at = NULL "
            "WHERE id = %s AND status = 'running' AND attempts < %s", (c.id, MAX_DEADLOCK_ATTEMPTS))
        return cur.rowcount == 1
"""
REQUEUE_COMMAND_FIRST = """        cur = await conn.execute(
            "UPDATE commands SET status = 'queued', lease_id = NULL, started_at = NULL "
            "WHERE id = %s AND status = 'running' AND attempts < %s", (c.id, MAX_DEADLOCK_ATTEMPTS))
        if cur.rowcount != 1:
            return False
        if c.lease_id is not None:
            await leases.release(conn, c.lease_id)
        return True
"""


def _pause_first(name: str):
    """Arm `name` so its FIRST hit sets `reached` and waits for `go`; later hits pass."""
    reached, go, hits = asyncio.Event(), asyncio.Event(), []

    async def action():
        hits.append(1)
        if len(hits) == 1:
            reached.set()
            await go.wait()
    failpoints.arm(name, action)
    return reached, go


async def _lock_waiters(p) -> int:
    async with tx(p) as c:
        return (await (await c.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()"
        )).fetchone())[0]


async def _release_when_someone_waits(p, go: asyncio.Event, limit_s: float = 0.6) -> None:
    """Let the paused session go once another session waits for a lock, or after `limit_s`: in the right order the
    other session may finish without ever waiting."""
    t = time.monotonic()
    while await _lock_waiters(p) < 1 and time.monotonic() - t < limit_s:
        await asyncio.sleep(0.02)
    go.set()


def _errors(out) -> list:
    return [r for r in out if isinstance(r, BaseException)]


# ---- the key wallet comes after the account rows ------------------------------------------------------------------
@pytest.mark.parametrize("p2_path", ["reaper_render_deadline", "fail_job"])
@mutation(FAIL, ACCOUNT_BEFORE_WALLET, WALLET_BEFORE_ACCOUNT, expect="deadlock detected")
def test_a_poll_failure_beside_the_failure_of_a_charged_job_takes_the_account_before_the_key_wallet(db, p2_path):
    """P2 fails a job already charged on the account (the reaper's render deadline, or `fail_job`) and is paused
    before its refunds; P1 is a poll `Failed(taken=True)` of another job of the same key on the same account: it
    charges the account and then refunds the key wallet. Both must finish: no session takes the wallet and then
    the account."""
    k = key(db)
    account(db, left=100)

    async def scenario(p):
        j2 = await claim_new(p, k, "j2")
        await store.apply_submit(p, j2, SubmitResult(Accepted("r2")), caps=CAPS, pools=POOLS)   # taken: charged
        j1 = await claim_new(p, k, "j1")
        await store.apply_submit(p, j1, SubmitResult(Accepted("r1", taken=False)), caps=CAPS, pools=POOLS)
        due(db, j1.job_id)
        [vis] = await store.claim_visible(p, provider=PROVIDER, status="waiting", visibility_s=300, limit=1)
        assert vis.job_id == j1.job_id
        if p2_path == "reaper_render_deadline":
            age(db, j2.job_id, "sent_at", 10 ** 6)
            db.run("UPDATE jobs SET render_since = NULL WHERE id = %s", j2.job_id)
        reached, go = _pause_first("pool.refund.listed")

        async def p2():
            if p2_path == "fail_job":
                return await store.fail_job(p, j2.job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
            return await reaper(p)._fail_one("COALESCE(render_since, sent_at)", ("waiting", "unknown"), 1.0,
                                             lambda _t, _s: ("render.deadline", "late"), j2.job_id)

        async def p1():
            await reached.wait()
            return await store.apply_poll(p, j1.job_id, vis.token,
                                          PollResult(Failed("render.watermarked_only", "x")), caps=CAPS, pools=POOLS)

        async def release():
            await reached.wait()
            await _release_when_someone_waits(p, go)

        out = await asyncio.gather(p2(), p1(), release(), return_exceptions=True)
        failpoints.disarm()
        return j1.job_id, j2.job_id, out

    j1, j2, out = run(db, scenario)
    assert not _errors(out), out
    assert (row(db, j1, "status"), row(db, j2, "status")) == (("failed",), ("failed",))
    assert db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 2


@pytest.mark.parametrize("p2_path", ["submit_fail", "poll_failed_not_taken"])
@mutation(APPLY, SUBMIT_LOCK, "pass", expect="deadlock detected")
@mutation(APPLY, POLL_LOCK, "pass", expect="deadlock detected")
def test_a_failure_that_carries_an_account_effect_locks_the_account_before_it_refunds_the_key_wallet(db, p2_path):
    """P2 fails its job with a Cooldown effect and no account charge (a submit `Fail`, or a poll `Failed(taken=False)`)
    and is paused after its refunds; P1 is a poll `Failed(taken=True)` on the same account and key. The effect's account
    lock must come with the job, before the wallet."""
    k = key(db)
    account(db, left=100)
    effect = (Cooldown("throttled", datetime.now(UTC) + timedelta(minutes=5)),)

    async def scenario(p):
        j1 = await claim_new(p, k, "j1")
        await store.apply_submit(p, j1, SubmitResult(Accepted("r1", taken=False)), caps=CAPS, pools=POOLS)
        j2 = await claim_new(p, k, "j2")
        if p2_path == "poll_failed_not_taken":
            await store.apply_submit(p, j2, SubmitResult(Accepted("r2", taken=False)), caps=CAPS, pools=POOLS)
        due(db, j1.job_id)
        due(db, j2.job_id)
        visible = {v.job_id: v for v in await store.claim_visible(
            p, provider=PROVIDER, status="waiting", visibility_s=300, limit=2)}
        reached, go = _pause_first("job.fail.refunded")

        async def p2():
            if p2_path == "submit_fail":
                fail = SubmitResult(Fail("render.deadline", "rejected"), account=effect)
                return await store.apply_submit(p, j2, fail, caps=CAPS, pools=POOLS)
            return await store.apply_poll(p, j2.job_id, visible[j2.job_id].token, PollResult(
                Failed("render.deadline", "rejected", taken=False), effects=effect), caps=CAPS, pools=POOLS)

        async def p1():
            await reached.wait()
            return await store.apply_poll(p, j1.job_id, visible[j1.job_id].token,
                                          PollResult(Failed("render.watermarked_only", "x")), caps=CAPS, pools=POOLS)

        async def release():
            await reached.wait()
            await _release_when_someone_waits(p, go)

        out = await asyncio.gather(p2(), p1(), release(), return_exceptions=True)
        failpoints.disarm()
        return j1.job_id, j2.job_id, out

    j1, j2, out = run(db, scenario)
    assert not _errors(out), out
    assert (row(db, j1, "status"), row(db, j2, "status")) == (("failed",), ("failed",))
    assert db.one("SELECT cooldown_until IS NOT NULL FROM accounts")[0]


# ---- a command row comes after its lease ---------------------------------------------------------------------------
@pytest.mark.parametrize("finisher", ["fail", "requeue"])
@pytest.mark.parametrize("other", ["delete_account", "reaper_other_leases"])
@mutation(COMMANDS, LEASE_FIRST, COMMAND_FIRST, expect="deadlock detected")
@mutation(COMMANDS, REQUEUE_LEASE_FIRST, REQUEUE_COMMAND_FIRST, expect="deadlock detected")
def test_a_leased_command_that_ends_on_an_expired_lease_takes_the_lease_before_its_command_row(
        db, monkeypatch, finisher, other):
    """The command's lease expired. Its owner records the end (`_fail`, or `_requeue` after a database abort) and is
    paused at the lease release, while the delete of its account, or the reaper's lease pass, runs. Neither may
    wait for a command row that the finisher holds while it waits for the lease."""
    x = new_account(db, checked=True)
    cid = new_command(db, "refresh_catalog", account_id=x)
    real_release = leases.release
    gate = {"on": False}
    holds, go = asyncio.Event(), asyncio.Event()

    async def release(conn, lease_id):
        if gate["on"]:
            holds.set()
            await go.wait()
        return await real_release(conn, lease_id)
    monkeypatch.setattr(leases, "release", release)

    async def scenario(p):
        env = make_env(p, KitEngine())
        claimed = await commands.claim_command(env)
        assert claimed is not None and claimed.lease_id is not None
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", claimed.lease_id)
        gate["on"] = True

        async def f():
            if finisher == "fail":
                await commands._fail(env, claimed, "the lease was lost before the result could be applied")
            else:
                await commands._requeue(env, claimed)
            return "ok"

        async def o():
            await holds.wait()
            if other == "delete_account":
                async with tx(p) as c:
                    await delete_unused_account(c, x)
            else:
                await reaper(p)._other_leases()
            return "ok"

        async def rel():
            await holds.wait()
            await _release_when_someone_waits(p, go)

        return await asyncio.gather(f(), o(), rel(), return_exceptions=True)

    out = run_as(db.worker_dsn, scenario)
    assert not _errors(out), out
    if other == "delete_account":
        assert db.one("SELECT count(*) FROM accounts WHERE id = %s", x)[0] == 0 and command(db, cid) is None
    else:
        assert command(db, cid)["status"] in ("failed", "queued")
    assert db.one("SELECT count(*) FROM leases")[0] == 0
