"""Helpers for the engine tests: a claim loop wired to the fake provider with short timings, and polling waits."""

import asyncio
import contextlib

from jobkit import SPEC

from relay_core.jobs.claim import Claim
from relay_core.jobs.engine import ClaimLoop
from relay_core.providers import AccountView
from relay_core.testing.fake_provider import FakeEngine, FakeSettings


async def load_account(claim: Claim) -> AccountView:
    return AccountView(str(claim.account_id), "acct", b"", 1, None, {}, {})


def make_loop(p, engine: FakeEngine | None = None, *, settings=None, spec=SPEC, owner="w1", **kw) -> ClaimLoop:
    """A loop with test timings: 20 ms tick, 100 ms lease heartbeat, a worker heartbeat row every 50 ms."""
    timings = dict(tick_s=0.02, lease_beat_s=0.1, beat_s=0.05, lag_check_s=0.05)
    timings.update(kw)
    return ClaimLoop(p, spec, engine or FakeEngine(), settings or FakeSettings(spec), owner=owner,
                     load_account=load_account, **timings)


async def within(awaitable, seconds: float, *, what: str):
    """`asyncio.wait_for` whose timeout names what was awaited (an AssertionError, like `until`)."""
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except TimeoutError:
        raise AssertionError("timed out waiting for %s" % what) from None


async def until(condition, *, timeout_s: float = 5.0, what: str = "the condition") -> None:
    """Poll a sync or async predicate every 20 ms; fail with `what` after `timeout_s`."""
    async def met() -> bool:
        got = condition()
        return bool(await got) if asyncio.iscoroutine(got) else bool(got)

    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    while not await met():
        if loop.time() > end:
            raise AssertionError("timed out waiting for %s" % what)
        await asyncio.sleep(0.02)


@contextlib.asynccontextmanager
async def running(loop: ClaimLoop, **run_kw):
    """Run the loop as a task; on exit stop it and wait for it. Yields the task (its result is the exit code)."""
    task = asyncio.create_task(loop.run(**run_kw))
    try:
        yield task
    finally:
        loop.stop()
        await asyncio.wait_for(task, 15)


def job_status(db, job_id) -> str:
    return db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0]


def in_status(db, job_id, *wanted: str):
    """A predicate for `until`: the job has reached one of the statuses."""
    return lambda: job_status(db, job_id) in wanted


def lease_count(db) -> int:
    return db.one("SELECT count(*) FROM leases")[0]
