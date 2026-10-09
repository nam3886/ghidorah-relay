"""Leases (contracts 4.3, 5.2, 7.1; I2, I14): a hard bound on every purpose, the batched heartbeat, the fence by lease
id under `clock_timestamp()`, expiry and reclaim, and a lost lease cancelling its task. Short injected TTLs, no long
sleeps."""

import asyncio
import uuid

import pytest

from relay_core import failpoints, leases
from relay_core.db import open_pool, tx
from relay_core.jobs.worker import boot_owner
from relay_core.leases import LeaseLost
from relay_core.testing.checker import violations
from relay_core.testing.mutation import mutation

L = "packages/relay-core/src/relay_core/leases.py"
CHECKER = "packages/relay-core/src/relay_core/testing/checker.py"


def run(db, scenario, max_size=10):
    async def main():
        pool = await open_pool(db.dsn, max_size=max_size)
        try:
            return await scenario(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


def account(db) -> uuid.UUID:
    account_id = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc) VALUES (%s, 'p1', '\\x01')", account_id)
    return account_id


async def lease(pool, account_id, purpose="check", owner="w1", hard_s=60, ttl_s=60, **kw):
    async with tx(pool) as conn:
        return await leases.acquire(conn, account_id=account_id, purpose=purpose, owner=owner, hard_s=hard_s,
                                    ttl_s=ttl_s, **kw)


def row(db, lease_id):
    return db.one("SELECT until, hard_until, owner FROM leases WHERE id = %s", lease_id)


def submitting_job(db):
    job = db.job(db.key(("p1", "video", None, None)))
    db.run("UPDATE jobs SET status = 'submitting' WHERE id = %s", job)
    return job


# ---- hard_until on every purpose -----------------------------------------------------------------------------
def test_every_purpose_gets_a_hard_until_that_caps_its_ttl(db):
    acct = account(db)
    job = submitting_job(db)

    async def scenario(pool):
        ids = [await lease(pool, acct, "job", hard_s=120, ttl_s=60, job_id=job),
               await lease(pool, acct, "check", hard_s=leases.CHECK_HARD_S, ttl_s=999),
               await lease(pool, acct, "reconcile", hard_s=leases.RECONCILE_HARD_S, ttl_s=999),
               await lease(pool, acct, "command", hard_s=leases.command_hard_s("recover"), ttl_s=999)]
        return ids

    ids = run(db, scenario)
    for lease_id in ids:
        until, hard_until, _ = row(db, lease_id)
        assert hard_until is not None and until <= hard_until
    # a ttl longer than the bound is cut to the bound
    assert row(db, ids[1])[0] == row(db, ids[1])[1]
    db.run("DELETE FROM leases")


def test_the_bounds_of_the_contract_and_an_unknown_command_kind():
    assert (leases.CHECK_HARD_S, leases.RECONCILE_HARD_S) == (180, 300)
    assert [leases.command_hard_s(k) for k in ("recover", "list_conversations", "refresh_catalog", "titles")] == [
        600, 300, 120, 120]
    with pytest.raises(ValueError, match="no lease bound"):
        leases.command_hard_s("anything")


def test_a_lease_needs_a_known_purpose_and_a_positive_bound(db):
    acct = account(db)

    async def scenario(pool):
        for kw in (dict(purpose="poll"), dict(hard_s=0), dict(ttl_s=0)):
            with pytest.raises(ValueError):
                await lease(pool, acct, **kw)

    run(db, scenario)


# ---- expiry and reclaim ---------------------------------------------------------------------------------------
def test_an_expired_lease_frees_the_account_and_is_reaped(db):
    acct = account(db)

    async def scenario(pool):
        first = await lease(pool, acct, hard_s=0.2, max_live=1)
        assert await lease(pool, acct, max_live=1) is None            # held: no second lease
        await asyncio.sleep(0.3)
        assert await lease(pool, acct, max_live=1) is not None        # expired rows do not count
        async with tx(pool) as conn:
            gone = await leases.reap_expired(conn)
        assert [g.id for g in gone] == [first] and gone[0].purpose == "check"

    run(db, scenario)
    db.run("DELETE FROM leases")


@mutation(L, "WHERE until <= clock_timestamp() AND purpose = ANY(%s) ", "WHERE purpose = ANY(%s) ",
          expect="assert await leases.reap_expired(conn) == []")
def test_the_reaper_never_deletes_a_live_lease_or_a_job_lease(db):
    acct = account(db)
    job = submitting_job(db)

    async def scenario(pool):
        live = await lease(pool, acct, hard_s=60)
        dead_job = await lease(pool, acct, "job", hard_s=0.1, job_id=job)
        await asyncio.sleep(0.2)
        async with tx(pool) as conn:
            assert await leases.reap_expired(conn) == []
        return live, dead_job

    live, dead_job = run(db, scenario)
    assert row(db, live) is not None and row(db, dead_job) is not None   # the job's own transition takes it
    db.run("DELETE FROM leases")


def test_release_deletes_one_lease_by_id_and_says_when_it_was_already_gone(db):
    acct = account(db)

    async def scenario(pool):
        a = await lease(pool, acct, owner="w1")
        other = await lease(pool, acct, owner="w1")
        async with tx(pool) as conn:
            assert await leases.release(conn, a) is True
            assert await leases.release(conn, a) is False
        return a, other

    a, other = run(db, scenario)
    assert row(db, a) is None and row(db, other) is not None
    db.run("DELETE FROM leases")


@mutation(L, "    if not await lock_account(conn, account_id, skip_locked=skip_locked):\n        return None\n",
          "    pass\n",
          expect="the live-lease limit of 1 was passed by 6 callers")
def test_concurrent_acquirers_cannot_both_pass_the_live_count(db):
    acct = account(db)

    async def slow() -> None:
        await asyncio.sleep(0.2)          # widen the gap between the count and the insert

    async def scenario(pool):
        failpoints.arm("lease.acquire.counted", slow)
        try:
            return await asyncio.gather(*[lease(pool, acct, owner="w%d" % i, max_live=1) for i in range(6)])
        finally:
            failpoints.disarm("lease.acquire.counted")

    got = [i for i in run(db, scenario) if i is not None]
    assert len(got) == 1, "the live-lease limit of 1 was passed by %d callers" % len(got)
    db.run("DELETE FROM leases")


# ---- the heartbeat ---------------------------------------------------------------------------------------------
LOCK_END = "FOR UPDATE SKIP LOCKED) RETURNING id"
HB = ("WHERE owner = %s AND id = ANY(%s) AND until > clock_timestamp() "
    "FOR UPDATE SKIP LOCKED) RETURNING id")
HB_NO_OWNER = ("WHERE %s IS NOT NULL AND id = ANY(%s) AND until > clock_timestamp() "
    "FOR UPDATE SKIP LOCKED) RETURNING id")
HB_NO_IDS = ("WHERE owner = %s AND %s IS NOT NULL AND until > clock_timestamp() "
    "FOR UPDATE SKIP LOCKED) RETURNING id")
HB_NO_UNTIL = "WHERE owner = %s AND id = ANY(%s) FOR UPDATE SKIP LOCKED) RETURNING id"


@mutation(L, HB, HB_NO_OWNER,
          expect="IndeterminateDatatype")
def test_the_heartbeat_extends_only_its_owners_leases(db):
    """Two workers of one provider: one's heartbeat never touches the other's leases, even listed as held."""
    acct = account(db)

    async def scenario(pool):
        mine = await lease(pool, acct, owner="w1/a", hard_s=60, ttl_s=0.5)
        theirs = await lease(pool, acct, owner="w1/b", hard_s=60, ttl_s=0.5)
        before = row(db, theirs)[0]
        assert await leases.heartbeat(pool, "w1/a", {mine, theirs}, ttl_s=30) == {mine}
        return mine, theirs, before

    mine, theirs, before = run(db, scenario)
    assert row(db, theirs)[0] == before, "w1/a's heartbeat extended w1/b's lease"
    assert row(db, mine)[0] > before
    db.run("DELETE FROM leases")


@mutation(L, HB, HB_NO_IDS,
          expect="assert await leases.heartbeat(pool, \"w1/a\", {held}, ttl_s=30) == {held}")
def test_the_heartbeat_extends_only_the_leases_its_keeper_holds(db):
    """A lease whose task ended without releasing it (or that nobody ever held) is not kept alive: it expires."""
    acct = account(db)

    async def scenario(pool):
        held = await lease(pool, acct, owner="w1/a", hard_s=60, ttl_s=0.5)
        orphan = await lease(pool, acct, owner="w1/a", hard_s=60, ttl_s=0.5)
        before = row(db, orphan)[0]
        assert await leases.heartbeat(pool, "w1/a", {held}, ttl_s=30) == {held}
        assert await leases.heartbeat(pool, "w1/a", set(), ttl_s=30) == set()
        return orphan, before

    orphan, before = run(db, scenario)
    assert row(db, orphan)[0] == before, "a lease nobody holds was extended"
    db.run("DELETE FROM leases")


@mutation(L, HB, HB_NO_IDS,
          expect="assert db.one(\"SELECT until <= clock_timestamp() FROM leases WHERE id = %s\", dea")
def test_a_restarted_worker_does_not_keep_a_dead_processs_lease_alive(db):
    """Even under the same owner id (the old naming), a restarted worker's heartbeat extends only what its keeper
    holds: the dead process's lease runs out its TTL and the account is free again within about a TTL."""
    acct = account(db)

    async def scenario(pool):
        dead = await lease(pool, acct, owner="w1", hard_s=60, ttl_s=1.5)
        keeper = leases.LeaseKeeper(pool, "w1", ttl_s=1.5, heartbeat_s=0.2)       # a beat may be 1.3 s late
        stop = asyncio.Event()
        beating = asyncio.create_task(keeper.run(stop))
        mine = await lease(pool, acct, owner="w1", hard_s=60, ttl_s=1.5)
        try:
            async with keeper.holding(mine):
                await asyncio.sleep(2.0)                     # past the TTL, several beats
                await leases.fence_once(pool, mine)          # the held lease is alive
        finally:
            stop.set()
            await beating
        return dead

    dead = run(db, scenario)
    assert row(db, dead)[0] < row(db, dead)[1], "the dead process's lease was extended by the restarted worker"
    assert db.one("SELECT until <= clock_timestamp() FROM leases WHERE id = %s", dead)[0]
    db.run("DELETE FROM leases")


@mutation("packages/relay-core/src/relay_core/jobs/worker.py", "uuid.uuid4().hex[:8]", "'0'",
          expect="assert first != second and first.startswith(\"worker-p1/\") and len(first) > len(\"")
def test_every_boot_gets_its_own_owner_id():
    first, second = boot_owner("worker-p1"), boot_owner("worker-p1")
    assert first != second and first.startswith("worker-p1/") and len(first) > len("worker-p1/") + 4


@mutation(L, HB, HB_NO_UNTIL,
          expect="assert await leases.heartbeat(pool, \"w1\", {gone}, ttl_s=30) == set()")
def test_the_heartbeat_does_not_revive_an_expired_lease(db):
    acct = account(db)

    async def scenario(pool):
        gone = await lease(pool, acct, owner="w1", hard_s=0.1)
        await asyncio.sleep(0.2)
        assert await leases.heartbeat(pool, "w1", {gone}, ttl_s=30) == set()
        return gone

    gone = run(db, scenario)
    assert row(db, gone)[0] <= row(db, gone)[1]
    db.run("DELETE FROM leases")


@mutation(L, "COALESCE(hard_until, 'infinity')", "'infinity'", expect="assert until <= hard_until")
def test_the_heartbeat_never_extends_past_the_hard_bound(db):
    acct = account(db)

    async def scenario(pool):
        lease_id = await lease(pool, acct, owner="w1", hard_s=5, ttl_s=1)
        await leases.heartbeat(pool, "w1", {lease_id}, ttl_s=3600)
        return lease_id

    until, hard_until, _ = row(db, run(db, scenario))
    assert until <= hard_until
    db.run("DELETE FROM leases")


# ---- the fence --------------------------------------------------------------------------------------------------
def test_fence_passes_a_live_lease_and_refuses_an_expired_or_missing_one(db):
    acct = account(db)

    async def scenario(pool):
        live = await lease(pool, acct, hard_s=60)
        await leases.fence_once(pool, live)
        short = await lease(pool, acct, hard_s=0.1)
        await asyncio.sleep(0.2)
        for lease_id in (short, live + 1000):
            with pytest.raises(LeaseLost) as err:
                await leases.fence_once(pool, lease_id)
            assert err.value.lease_id == lease_id

    run(db, scenario)
    db.run("DELETE FROM leases")


@mutation(L, "WHERE id = %s AND until > clock_timestamp() FOR SHARE",
          "WHERE owner = (SELECT owner FROM leases WHERE id = %s) AND until > clock_timestamp() FOR SHARE",
          expect="Failed: DID NOT RAISE LeaseLost")
def test_fence_is_by_lease_id_not_by_owner(db):
    """An expired lease of a worker that holds a newer live lease on the account is still lost."""
    acct = account(db)

    async def scenario(pool):
        old = await lease(pool, acct, owner="w1", hard_s=0.1)
        await asyncio.sleep(0.2)
        await lease(pool, acct, owner="w1", hard_s=60)          # the same worker, a newer lease
        with pytest.raises(LeaseLost):
            await leases.fence_once(pool, old)

    run(db, scenario)
    db.run("DELETE FROM leases")


@mutation(L, "AND until > clock_timestamp() FOR SHARE", "AND until > now() FOR SHARE",
          expect="Failed: DID NOT RAISE LeaseLost")
def test_fence_reads_the_clock_not_the_transaction_start(db):
    """A transaction that began before the lease expired still sees it expired: now() is frozen, clock_timestamp()
    is not."""
    acct = account(db)

    async def scenario(pool):
        lease_id = await lease(pool, acct, hard_s=0.4)
        async with tx(pool) as conn:
            await conn.execute("SELECT now()")                 # freezes now() at this moment
            await asyncio.sleep(0.6)
            with pytest.raises(LeaseLost):
                await leases.fence(conn, lease_id)

    run(db, scenario)
    db.run("DELETE FROM leases")


@mutation(L, LOCK_END, "FOR UPDATE) RETURNING id",
          expect="a lease row held by a fenced write made the heartbeat wait")
def test_a_lease_row_held_by_a_fenced_write_is_skipped_by_the_heartbeat_not_waited_for(db):
    acct = account(db)

    async def scenario(pool):
        held = await lease(pool, acct, owner="w1", hard_s=60, ttl_s=1)
        other = await lease(pool, acct, "reconcile", owner="w1", hard_s=60, ttl_s=1)
        before = row(db, other)[0]
        async with tx(pool) as conn:
            await leases.fence(conn, held)
            try:
                got = await asyncio.wait_for(leases.heartbeat(pool, "w1", {held, other}, ttl_s=30), 1)
            except TimeoutError:
                raise AssertionError("a lease row held by a fenced write made the heartbeat wait") from None
        assert got == {other}, "the locked lease is skipped, the other one is extended"
        assert row(db, other)[0] > before
        assert await leases.heartbeat(pool, "w1", {held, other}, ttl_s=30) == {held, other}   # retried next beat

    run(db, scenario)
    db.run("DELETE FROM leases")


@mutation(L, "gone -= {i for i, live in rows.items() if live}", "pass",
          expect="a lease skipped because a fenced write holds it was judged lost")
def test_a_skipped_lease_inside_its_ttl_is_not_lost_and_does_not_cost_the_others(db):
    acct = account(db)

    async def scenario(pool):
        keeper = leases.LeaseKeeper(pool, "w1", ttl_s=30, heartbeat_s=1)
        held = await lease(pool, acct, owner="w1", hard_s=60, ttl_s=30)
        other = await lease(pool, acct, "reconcile", owner="w1", hard_s=60, ttl_s=30)
        outcomes = {}

        async def holder(lease_id):
            try:
                async with keeper.holding(lease_id):
                    await asyncio.sleep(5)
                outcomes[lease_id] = "done"
            except LeaseLost:
                outcomes[lease_id] = "lost"

        tasks = [asyncio.create_task(holder(i)) for i in (held, other)]
        await asyncio.sleep(0.1)
        async with tx(pool) as conn:
            await leases.fence(conn, held)
            lost = await keeper.beat()
        await asyncio.sleep(0.1)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert lost == set(), "a lease skipped because a fenced write holds it was judged lost"
        assert outcomes == {}

    run(db, scenario)
    db.run("DELETE FROM leases")


# ---- a lost lease cancels its task ---------------------------------------------------------------------------
@mutation(L, "self._held[lease_id].cancel()", "pass",
          expect="assert asyncio.get_running_loop().time() - started < 2.5")
@pytest.mark.parametrize("purpose", ["check", "reconcile", "command"])
def test_a_hung_holder_is_cancelled_within_its_bound_and_frees_the_account(db, purpose):
    acct = account(db)

    async def scenario(pool):
        keeper = leases.LeaseKeeper(pool, "w1", ttl_s=0.2, heartbeat_s=0.1)
        stop = asyncio.Event()
        beating = asyncio.create_task(keeper.run(stop))
        lease_id = await lease(pool, acct, purpose, owner="w1", hard_s=0.5, ttl_s=0.2, max_live=1)

        async def hung():
            async with keeper.holding(lease_id):
                await asyncio.sleep(30)

        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(LeaseLost):
                await asyncio.wait_for(hung(), 3)
        finally:
            stop.set()
            await beating
        assert asyncio.get_running_loop().time() - started < 2.5
        assert await lease(pool, acct, purpose, owner="w2", max_live=1) is not None    # the account is free again

    run(db, scenario)
    db.run("DELETE FROM leases")


def test_a_healthy_holder_keeps_its_lease_past_the_ttl_and_an_outside_cancel_stays_a_cancel(db):
    acct = account(db)

    async def scenario(pool):
        keeper = leases.LeaseKeeper(pool, "w1", ttl_s=1.5, heartbeat_s=0.2)     # a beat may be 1.3 s late
        stop = asyncio.Event()
        beating = asyncio.create_task(keeper.run(stop))
        lease_id = await lease(pool, acct, owner="w1", hard_s=60, ttl_s=1.5)
        async with keeper.holding(lease_id):
            await asyncio.sleep(2.0)                 # well past the ttl: only the heartbeat keeps it alive
            await leases.fence_once(pool, lease_id)

        async def waiting():
            async with keeper.holding(lease_id):
                await asyncio.sleep(30)

        task = asyncio.create_task(waiting())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        stop.set()
        await beating

    run(db, scenario)
    db.run("DELETE FROM leases")


# ---- checker rules for leases ----------------------------------------------------------------------------------
def planted(db, sql, *params):
    db.run(sql, *params)
    with db.connect() as c:
        found = violations(c)
    db.reset()                                  # the autouse checker must not see the planted row
    return found


@mutation(CHECKER, "if hard_until is not None and until > hard_until:", "if False:",
          expect="assert len(found) == 1 and \"past\" in found[0], found")
def test_the_checker_reports_a_lease_that_runs_past_its_bound(db):
    found = planted(db, "INSERT INTO leases (account_id, purpose, owner, until, hard_until) "
                        "VALUES (%s, 'check', 'w', now() + interval '1 h', now())", account(db))
    assert len(found) == 1 and "past" in found[0], found


@mutation(CHECKER, 'if live and purpose == "job" and job_status != "submitting":', "if False:",
          expect="assert len(found) == 1 and \"queued\" in found[0], found")
def test_the_checker_reports_a_live_job_lease_on_a_job_that_is_not_submitting(db):
    acct = account(db)
    job = db.job(db.key(("p1", "video", None, None)))               # queued
    found = planted(db, "INSERT INTO leases (account_id, job_id, purpose, owner, until, hard_until) "
                        "VALUES (%s, %s, 'job', 'w', now() + interval '1 h', now() + interval '1 h')", acct, job)
    assert len(found) == 1 and "queued" in found[0], found


def test_the_checker_accepts_a_live_job_lease_on_a_submitting_job(db):
    acct = account(db)
    job = submitting_job(db)
    found = planted(db, "INSERT INTO leases (account_id, job_id, purpose, owner, until, hard_until) "
                        "VALUES (%s, %s, 'job', 'w', now() + interval '1 h', now() + interval '1 h')", acct, job)
    assert found == []
