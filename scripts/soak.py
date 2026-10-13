#!/usr/bin/env python3
"""A seeded soak of the job engine against the FakeProvider (contracts §14.2, §14.3).

One seed is one random but replayable workload: a few accounts, a dozen jobs in both completion modes, and a script
per job for what the "upstream" does at each step (accept, complete, refuse, hang, die after the send, vanish and
turn up in a reconcile, finish or fail on a poll). The core loops run composed in this small harness, with short
timings: ClaimLoop, Poller, Fetcher, Reconciler, Reaper, the CommandLoop (checks and commands) and the webhook sender.

While they run, a chaos task keeps interfering: warps the clocks of waiting jobs, ages unknown jobs, expires leases,
re-checks jobs, restarts the claim loop in the middle of submits (a cancellation), arms failpoints that raise, drains
and resumes, signs accounts out and queues commands. Then everything is let alone until every job is terminal (a
deadline is forced when a job needs it), and the seed is judged:

  - the strict tier of the invariant checker finds nothing (ledger, charges, leases, tokens, terminal rows);
  - no job was sent twice unless a NotTaken or a NotSent that the core
    really requeued on (apply_reconciled returned "queued") allowed it (I1);
  - no job is stuck, no lease is left, no loop crashed;
  - a browser provider never ran more browsers than its semaphore (I28).

A failing seed prints its number. The workload and the chaos are derived from it, so a replay is the same workload;
the interleaving of the asyncio tasks against the database is not fixed, so a replay may need a few runs.

    scripts/check.sh --soak [--seeds 200] [--start 0] [--seed N] [--verbose]     the DSNs and MinIO come from check.sh
    scripts/check.sh --full                                                      runs 200 seeds after the mutations

--jobs N runs the seeds in N processes at once (seed i in process i % N), each with its own database (cut from a
migrated template: RELAY_TEST_PG_TEMPLATE) and its own bucket, so seeds do not see each other. A failing seed is
printed with its number and the replay command exactly as in a serial run; the seed lines come in the order the
processes finish them.
"""

import argparse
import asyncio
import contextlib
import dataclasses
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import types
import unittest.mock
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg_pool import PoolTimeout

REPO = Path(__file__).resolve().parents[1]
CORE_TESTS = REPO / "packages" / "relay-core" / "tests"
sys.path.insert(0, str(CORE_TESTS))                           # the test kits: media files, the fake DNS, loaders

import hookkit  # noqa: E402
import jobkit  # noqa: E402
from enginekit import load_account as claim_account  # noqa: E402
from lifecyclekit import MediaEngine  # noqa: E402
from lifecyclekit import load_account as read_account  # noqa: E402
from pgtemplate import migrated_database  # noqa: E402

from relay_core import failpoints  # noqa: E402
from relay_core.budget import BrowserBudget  # noqa: E402
from relay_core.db import open_pool  # noqa: E402
from relay_core.jobs import apply as job_apply  # noqa: E402
from relay_core.jobs import store  # noqa: E402
from relay_core.jobs.checks import Env  # noqa: E402
from relay_core.jobs.commands import CommandLoop  # noqa: E402
from relay_core.jobs.engine import ClaimLoop  # noqa: E402
from relay_core.jobs.fetcher import Fetcher  # noqa: E402
from relay_core.jobs.poller import Poller  # noqa: E402
from relay_core.jobs.reaper import Reaper  # noqa: E402
from relay_core.jobs.recheck import JobRefused, recheck_job  # noqa: E402
from relay_core.jobs.reconcile import Reconciler  # noqa: E402
from relay_core.leases import LeaseKeeper  # noqa: E402
from relay_core.providers import (  # noqa: E402
    Accepted,
    Ambiguous,
    Completed,
    Done,
    Fail,
    Failed,
    Found,
    Health,
    NotSent,
    NotTaken,
    Pending,
    PollResult,
    Retry,
    StillUnknown,
    SubmitResult,
)
from relay_core.storage import ObjectStore  # noqa: E402
from relay_core.testing.checker import violations  # noqa: E402
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, FakeSpec, sent, unsent  # noqa: E402

PROVIDER = "fake"
API_PASSWORD = "api-soak-password"
WORKER_PASSWORD = "worker-soak-password"
FAILPOINTS = ("fake.submit.start", "fake.submit.sent", "fake.poll", "fake.reconcile", "fetch.rows_written",
              "job.fail.refund", "billing.refund.start", "webhook.claim.locked",
              "lease.acquire.counted")
SLOTS = 4
FORCE_AFTER_S = 1.5        # a reconcile that never answers, or a queue no account can serve, is ended by a deadline
# What chaos may legitimately run into: its own failpoint, a lock or statement timeout, a busy pool. Anything else is
# a bug in the harness and stops the seed.
TRANSIENT = (failpoints.FailpointError, psycopg.OperationalError, psycopg.errors.QueryCanceled,
             psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected, PoolTimeout, TimeoutError)
LOGGERS = ("relay_core", "psycopg")
for _name in LOGGERS:                                       # the chaos makes the loops log errors on purpose
    logging.getLogger(_name).setLevel(logging.WARNING)
    logging.getLogger(_name).propagate = False


class LogTail(logging.Handler):
    """The loops' own error log, kept for the failure text (the chaos makes them log on purpose, so it is not shown)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info:
            text += " | " + repr(record.exc_info[1])
        self.lines.append("%s: %s" % (record.name.removeprefix("relay_core."), text))


@dataclasses.dataclass(frozen=True)
class Dsns:
    """One DSN per database role: the owner seeds and judges, the api creates and re-checks jobs, the worker runs the
    loops (so a missing grant shows up as a failing seed, like on the box)."""
    owner: str
    api: str
    worker: str


class _Counted:
    """The ctx the engine sees: remembers that `mark_sent` really happened in THIS submit (a cancelled attempt can still
    be running while the job is queued and sent again by a new one, so the flag cannot be per job)."""

    def __init__(self, ctx):
        self._ctx = ctx
        self.sent = False

    async def mark_sent(self) -> None:
        await self._ctx.mark_sent()
        self.sent = True

    def __getattr__(self, name):
        return getattr(self._ctx, name)


class SoakEngine(MediaEngine):
    """The scripted upstream. Submit steps come from `FakeEngine.script_submit`; polls and reconciles follow a plan per
    job; every browser-using call overlaps with the others for a moment so the semaphore is really exercised."""

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self.rng = rng
        self.polls: dict[str, list] = {}           # job -> remaining Pending count, final outcome
        self.reconciles: dict[str, list] = {}      # job -> results; the last one repeats
        self.sends: Counter = Counter()            # job -> submits during which mark_sent really ran
        self.allowed: Counter = Counter()          # job -> NotTaken results and requeues that license a re-send
        self.t0 = time.monotonic()
        self.trace: dict = {}                      # job -> what each submit did, for the failure text
        self.pattern: dict = {}                    # job -> the name of its scripted behaviour (for the failure text)
        self.live = self.peak = 0

    async def _hold(self, call):
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(self.rng.uniform(0, 0.04))
            return await call()
        finally:
            self.live -= 1

    async def submit(self, ctx, job):
        counted = _Counted(ctx)

        async def go():
            note = "?"
            try:
                result = await super(SoakEngine, self).submit(counted, job)
                note = type(result.job).__name__
            except BaseException as e:
                note = type(e).__name__
                raise
            finally:
                if counted.sent:
                    self.sends[job.id] += 1
                self.trace.setdefault(job.id, []).append("%.2fs %s%s" % (
                    time.monotonic() - self.t0, "SENT:" if counted.sent else "", note))
            if isinstance(result.job, NotTaken):
                self.allowed[job.id] += 1
            return result

        return await self._hold(go)

    async def poll(self, ctx, job):
        self.calls.append(("poll", str(job.id)))
        await failpoints.hit("fake.poll")
        plan = self.polls.get(str(job.id))
        if plan is None:
            return PollResult(Done(self.outputs(job)))
        if plan[0] > 0:
            plan[0] -= 1
            return PollResult(Pending("rendering", 0))
        return plan[1](job)

    async def reconcile(self, ctx, job):
        async def go():
            self.calls.append(("reconcile", str(job.id)))
            await failpoints.hit("fake.reconcile")
            plan = self.reconciles.get(str(job.id), [StillUnknown(answered=True)])
            result = plan.pop(0) if len(plan) > 1 else plan[0]
            return result

        return await self._hold(go)

    async def check_account(self, ctx):
        return await self._hold(lambda: FakeEngine.check_account(self, ctx))

    async def run_command(self, ctx, kind, args):
        return await self._hold(lambda: FakeEngine.run_command(self, ctx, kind, args))


async def job_status(db, job_id) -> str:
    async with db.connection() as conn:
        row = await (await conn.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))).fetchone()
    return row[0] if row else ""


@contextlib.contextmanager
def licence_requeues(engine: SoakEngine):
    """A NotSent licenses a second send only when the core acted on it: the job was `unknown` and `apply_reconciled`
    made it `queued`. A refused one (too early, or not covering the send window) leaves it `unknown`, and a NotSent
    for a job that is already queued changes nothing."""
    real = store.apply_reconciled

    async def counting(db, job_id, result, **kw):
        before = await job_status(db, job_id) if isinstance(result, NotSent) else ""
        status = await real(db, job_id, result, **kw)
        if isinstance(result, NotSent) and before == "unknown" and status == "queued":
            engine.allowed[str(job_id)] += 1      # the engine keys its counters by the str id
        return status

    store.apply_reconciled = counting
    try:
        yield
    finally:
        store.apply_reconciled = real


def script_world(rng: random.Random, engine: SoakEngine, jobs: list[tuple]) -> None:
    """Give every job its upstream behaviour. `jobs` is (job_id, mime) in creation order."""
    for job_id, mime in jobs:
        view = types.SimpleNamespace(id=job_id, params={"mime": mime})
        n = rng.choice((1, 1, 2, 3))
        ref = "found-%s" % job_id
        first = rng.choices(
            ["default", "completed", "not_taken", "retry", "ambiguous", "dies_sent", "boom_unsent", "hang_sent",
             "hang_unsent", "fail", "untaken_accept"], weights=[22, 8, 8, 6, 8, 6, 5, 10, 4, 7, 6])[0]
        engine.pattern[str(job_id)] = first
        steps = {
            "default": [],
            "completed": [sent(SubmitResult(Completed(engine.outputs(view, n))))],
            "not_taken": [sent(SubmitResult(NotTaken("pool.account_throttled"))), None],
            "retry": [unsent(SubmitResult(Retry("pool.account_logged_out"))), None],
            "ambiguous": [sent(SubmitResult(Ambiguous("no answer")))],
            "dies_sent": [sent(ConnectionError("the socket died after the send"))],
            "boom_unsent": [unsent(RuntimeError("the browser crashed before the send")), None],
            "hang_sent": [sent(SubmitResult(Accepted("never-%s" % job_id)), delay_s=3)],
            "hang_unsent": [unsent(SubmitResult(Accepted("never-%s" % job_id)), delay_s=3), None],
            "fail": [sent(SubmitResult(Fail("account.prompt_mismatch", "upstream said no")))],
            "untaken_accept": [sent(SubmitResult(Accepted("ref-%s" % job_id, taken=False)))],
        }[first]
        for step in steps:
            if step is None:
                break
            engine.script_submit(job_id, step)
        if steps and steps[-1] is None:                       # a failure first, then the ordinary behaviour
            engine.script_submit(job_id, sent(SubmitResult(Accepted("ref-%s" % job_id))))
        final = rng.choice(("done", "done", "done", "failed"))
        if final == "done":
            engine.polls[str(job_id)] = [rng.randint(0, 2), lambda job, n=n: PollResult(Done(engine.outputs(job, n)))]
        else:
            taken = rng.random() < 0.5
            engine.polls[str(job_id)] = [rng.randint(0, 2), lambda job, t=taken: PollResult(
                Failed("account.dead_session", "the session died", taken=t))]
        ending = rng.choice((Found(ref, taken=True), Found(ref, taken=False), NotSent(True), NotSent(False),
                             StillUnknown(answered=True)))
        engine.reconciles[str(job_id)] = [StillUnknown(answered=True)] * rng.randint(0, 2) + [ending]


async def _raise() -> None:
    raise failpoints.FailpointError("chaos")


class World:
    """One seed's running system: the loops, the engine and the chaos that interferes with them."""

    def __init__(self, pool, api_pool, rng: random.Random, engine: SoakEngine, objects: ObjectStore, browser: bool,
                 slots: int):
        self.pool, self.api_pool, self.rng, self.engine, self.objects = pool, api_pool, rng, engine, objects
        self.spec = FakeSpec(PROVIDER, execution="browser" if browser else "http", account_slots=slots)
        self.settings = FakeSettings(self.spec, fake__render_deadline_s=9000)
        self.budget = BrowserBudget(SLOTS) if browser else None
        self.hook_rng = random.Random(rng.random())
        self.stopping = asyncio.Event()
        self.claim: ClaimLoop | None = None
        self.claim_task: asyncio.Task | None = None
        self.loops: list = []
        self.tasks: dict[str, asyncio.Task] = {}
        self.armed: dict[str, int] = {}
        self.restarts = 0
        self.t_db = 0.0
        self.baseline = asyncio.all_tasks()          # what runs before the loops start: this task, the pools' workers

    # ---- the loops, with short timings -------------------------------------------------------------------------
    def new_claim_loop(self) -> ClaimLoop:
        return ClaimLoop(self.pool, self.spec, self.engine, self.settings, owner="w1", load_account=claim_account,
                         budget=self.budget, browser_ceiling=SLOTS if self.budget else None, tick_s=0.04,
                         lease_ttl_s=0.6, lease_beat_s=0.1, beat_s=0.2, lag_check_s=0.2, lag_warn_s=60,
                         max_errors=10**9, grace_s=0.05)

    def start(self) -> None:
        common = dict(owner="w1", load_account=read_account, tick_s=0.05)
        poller = Poller(self.pool, self.spec, self.engine, self.settings, error_retry_s=0.2, bound_s=2.0, **common)
        fetcher = Fetcher(self.pool, self.spec, self.engine, self.settings, objects=self.objects, retry_s=0.2,
                          bound_s=20.0, **common)
        recon = Reconciler(self.pool, self.spec, self.engine, self.settings, budget=self.budget, lease_ttl_s=0.6,
                           lease_beat_s=0.1, **common)
        reaper = Reaper(self.pool, self.spec, self.settings, interval_s=0.1)
        env = Env(db=self.pool, spec=self.spec, engine=self.engine, settings=self.settings, owner="w1",
                  keeper=LeaseKeeper(self.pool, "w1", ttl_s=60, heartbeat_s=15), load_account=read_account,
                  store=self.objects, budget=self.budget, check_every_s=0.4, retry_s=0.3)
        self.commands_stop = asyncio.Event()
        self.loops = [poller, fetcher, recon, reaper]
        self.claim = self.new_claim_loop()
        self.claim_task = asyncio.create_task(self.claim.run())
        for name, loop in zip(("poller", "fetcher", "reconciler", "reaper"), self.loops, strict=True):
            self.tasks[name] = asyncio.create_task(loop.run())
        self.tasks["commands"] = asyncio.create_task(
            CommandLoop(env, housekeeping_s=0.3).run(self.commands_stop, 0.1))
        self.tasks["webhooks"] = asyncio.create_task(self.send_webhooks())

    async def send_webhooks(self) -> None:
        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(self.hook_rng.choice((200, 200, 200, 500, 502, 404)), content=b"")

        while not self.stopping.is_set():
            try:
                await store.deliver_due(self.pool, resolver=hookkit.resolver, transport=httpx.MockTransport(answer))
            except Exception:
                pass                                                        # a failpoint or a lock timeout
            await asyncio.sleep(0.1)

    async def bounce(self) -> None:
        """A cancellation: stop the claim loop in the middle of its submits (they are cancelled after the short
        grace) and start a new one."""
        self.claim.stop()
        await self.claim_task
        self.restarts += 1
        self.claim = self.new_claim_loop()
        self.claim_task = asyncio.create_task(self.claim.run())

    def leftover_tasks(self) -> list[asyncio.Task]:
        """Tasks this seed started that are still running (the pools' own workers are not ours)."""
        return [t for t in asyncio.all_tasks() if t not in self.baseline and not t.done()]

    async def stop(self) -> list[str]:
        """Stop everything; the names of the loops that died of an exception."""
        self.stopping.set()
        self.commands_stop.set()
        self.claim.stop()
        for loop in self.loops:
            loop.stop()
        crashed = []
        everything = {"claim": self.claim_task, **self.tasks}
        done, pending = await asyncio.wait(everything.values(), timeout=20)
        for task in pending:
            task.cancel()
            crashed.append("a loop did not stop")
        for name, task in everything.items():
            if task in done and not task.cancelled() and task.exception() is not None:
                crashed.append("%s crashed: %r" % (name, task.exception()))
        return crashed

    # ---- chaos -------------------------------------------------------------------------------------------------
    async def sql(self, query: str, *params):
        """One statement on its own connection; a deadlock with a loop's transaction is retried (a bulk UPDATE here
        locks rows in another order than the loops do)."""
        for attempt in range(4):
            try:
                async with self.pool.connection() as conn:
                    cur = await conn.execute(query, params)
                    return await cur.fetchall() if cur.description else []
            except psycopg.errors.DeadlockDetected:
                if attempt == 3:
                    raise
                await asyncio.sleep(0.05)

    async def chaos_op(self) -> None:
        rng = self.rng
        op = rng.choices(
            ("warp", "age", "recheck", "bounce", "failpoint", "expire", "drain", "health", "command", "rest"),
            weights=(26, 8, 10, 6, 12, 10, 4, 4, 6, 14))[0]
        for name in list(self.armed):                                       # a failpoint stays armed a few ops
            self.armed[name] -= 1
            if self.armed[name] <= 0:
                failpoints.disarm(name)
                del self.armed[name]
        if op == "warp":
            await self.warp()
        elif op == "age":
            await self.sql("UPDATE jobs SET sent_at = sent_at - interval '200 s' WHERE status = 'unknown'")
        elif op == "recheck":
            rows = await self.sql("SELECT id FROM jobs WHERE status IN ('failed', 'waiting', 'unknown') "
                                  "AND external_ref IS NOT NULL")
            if rows:
                try:
                    await recheck_job(self.api_pool, rng.choice(rows)[0])
                except JobRefused:
                    pass
        elif op == "bounce":
            await self.bounce()
        elif op == "failpoint":
            name = rng.choice(FAILPOINTS)
            failpoints.arm(name, _raise)
            self.armed[name] = rng.randint(1, 3)
        elif op == "expire":
            await self.sql("UPDATE leases SET until = clock_timestamp() - interval '1 second' "
                           "WHERE purpose IN ('job', 'reconcile')")
        elif op == "drain":
            self.settings.set("fake.claims_enabled", not self.settings.get("fake.claims_enabled"))
        elif op == "health":
            self.engine.health = Health(state="signed_out" if self.engine.health.state == "alive" else "alive")
        elif op == "command":
            await self.queue_command()

    async def warp(self) -> None:
        """Time passes for every job that waits for a poll, a fetch or a reconcile. A poll or fetch claim that is still
        young is in flight and keeps its visibility time: re-claiming it every tick would replace its token before
        its result could be applied (a livelock the real 300 s and 900 s visibility times cannot cause)."""
        await self.sql("UPDATE jobs SET next_poll_at = clock_timestamp() - interval '1 second' "
                       "WHERE status IN ('waiting', 'fetching', 'unknown') "
                       "AND (poll_token IS NULL OR updated_at < clock_timestamp() - interval '5 seconds')")

    async def queue_command(self) -> None:
        rows = await self.sql("SELECT id FROM accounts")
        if not rows:
            return
        kind = self.rng.choice(("check", "wake", "check_all"))
        target = self.rng.choice(rows)[0]
        args = '{"account_id": "%s"}' % target if kind != "check_all" else "{}"
        await self.sql("INSERT INTO commands (id, provider, kind, account_id, args) VALUES (%s, %s, %s, %s, %s) "
                       "RETURNING id", uuid.uuid4(), PROVIDER, kind, None if kind != "wake" else target, args)

    async def chaos(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                await self.chaos_op()
            except TRANSIENT:                                               # a chaos step may hit its own failpoint
                pass
            await asyncio.sleep(self.rng.uniform(0.01, 0.06))

    # ---- let it settle -----------------------------------------------------------------------------------------
    async def open_jobs(self) -> int:
        return (await self.sql("SELECT count(*) FROM jobs WHERE status NOT IN ('done', 'failed')"))[0][0]

    async def settle(self, seconds: float) -> int:
        """No more chaos. Returns how many jobs needed a forced deadline (a reconcile that never answers, a queue no
        account could serve)."""
        failpoints.disarm()
        self.armed.clear()
        self.settings.set("fake.claims_enabled", True)
        self.engine.health = Health(state="alive")
        await self.sql("UPDATE accounts SET check_due_at = clock_timestamp()")
        start, forced = time.monotonic(), 0
        while await self.open_jobs():
            elapsed = time.monotonic() - start
            if elapsed > seconds:
                break
            if not forced and elapsed > FORCE_AFTER_S:
                forced = await self.open_jobs()
                self.settings.set("fake.render_deadline_s", 0)
                self.settings.set("unsent_deadline_s", 0)
            await self.warp()
            await asyncio.sleep(0.1)
        return forced


# ---- one seed ---------------------------------------------------------------------------------------------------
async def seed_data(owner_dsn: str, rng: random.Random, n_accounts: int) -> uuid.UUID:
    """The key with open wallets and the accounts (checked, available, some with a known balance)."""
    async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True,
                                                     options="-c search_path=relay") as c:
        key_id = uuid.uuid4()
        await c.execute("INSERT INTO api_keys (id, key_hash, key_prefix, enabled) VALUES (%s, %s, 'rk-soak', true)",
                        (key_id, key_id.hex))
        for kind in ("video", "image"):
            await c.execute("INSERT INTO key_wallets (key_id, provider, kind) VALUES (%s, %s, %s)",
                            (key_id, PROVIDER, kind))
        for _ in range(n_accounts):
            account_id = uuid.uuid4()
            await c.execute("INSERT INTO accounts (id, provider, credential_enc, state, last_checked_at) "
                            "VALUES (%s, %s, %s, 'available', clock_timestamp())",
                            (account_id, PROVIDER, b"\x01"))
            left = rng.choice((None, None, 3, 6, 12))
            if left is not None:
                await c.execute("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) "
                                "VALUES (%s, 'video', %s, 'reported', (clock_timestamp() AT TIME ZONE 'UTC')::date)",
                                (account_id, left))
    return key_id


async def create_jobs(pool, rng: random.Random, key_id, settings) -> list[tuple]:
    made = []
    for i in range(rng.randint(6, 14)):
        video = rng.random() < 0.8
        options = dict(completion=rng.choice(("hold", "detach")), account_cost=rng.choice((1, 1, 2)) if video else 1,
                       price=rng.choice((0, 1, 5, 10)), submit_timeout_s=1)
        new = jobkit.new_job(key_id, "job-%d" % i, model=PROVIDER + ("-video" if video else "-image"), **options)
        if rng.random() < 0.3:
            new = dataclasses.replace(new, callback_url=hookkit.URL)
        created = await store.create_job(pool, new, settings)
        made.append((created.job_id, new.plan.params["mime"]))
    return made


def _strict(dsn: str) -> list[str]:
    with psycopg.connect(dsn, autocommit=True, options="-c search_path=relay") as conn:
        return violations(conn, strict=True)


async def verify(w: World, owner_dsn: str, crashed: list[str]) -> list[str]:
    problems = list(crashed)
    problems += ["task %s is still running after the stop" % t.get_name() for t in w.leftover_tasks()]
    await Reaper(w.pool, w.spec, w.settings, interval_s=0.05).tick()
    for job_id, status in await w.sql("SELECT id, status FROM jobs WHERE status NOT IN ('done', 'failed')"):
        problems.append("job %s never finished: it is %s" % (job_id, status))
    left = (await w.sql("SELECT count(*) FROM leases"))[0][0]
    if left:
        problems.append("%d lease(s) left behind" % left)
    problems += await asyncio.to_thread(_strict, owner_dsn)
    for job_id, n in w.engine.sends.items():
        if n > 1 + w.engine.allowed[job_id]:
            events = ["%s@%.2f: %s" % (r[0], r[2], r[1][:50]) for r in await w.sql(
                "SELECT stage, text, extract(epoch FROM at) - %s FROM job_events WHERE job_id = %s ORDER BY id",
                w.t_db, job_id)]
            problems.append("I1 job %s (%s) was sent %d times with %d legitimate re-send(s); submits: %s; timeline: %s"
                            % (job_id, w.engine.pattern.get(job_id), n, w.engine.allowed[job_id],
                               ", ".join(w.engine.trace.get(job_id, [])), " > ".join(events)))
    for (n,) in await w.sql("SELECT count(*) FROM jobs WHERE status = 'done' AND taken_at IS NULL"):
        if n:
            problems.append("%d done job(s) were never proven taken" % n)
    for (n,) in await w.sql(
            "SELECT count(*) FROM account_charges c JOIN jobs j ON j.id = c.job_id WHERE j.status = 'failed' "
            "AND c.refunded_at IS NULL AND c.account_day = (clock_timestamp() AT TIME ZONE 'UTC')::date"):
        if n:
            problems.append("I7 %d failed job(s) kept an unrefunded account charge" % n)
    if w.budget is not None and w.engine.peak > SLOTS:
        problems.append("I28 %d browsers ran at once, the semaphore allows %d" % (w.engine.peak, SLOTS))
    return problems


async def run_seed(dsns: Dsns, seed: int, objects: ObjectStore, *, chaos_s: float = 1.2, settle_s: float = 30.0,
                   sabotage=None, crash_note: str = "") -> tuple[list[str], dict]:
    """Run one seed on an empty database. Returns (problems, stats); no problems means the seed passed.
    `sabotage(world)` runs after the system has stopped and before the verdict: the tests use it to plant a violation
    and prove each rule of the verdict can fail; `crash_note` plays a loop that died."""
    os.environ.setdefault(failpoints.ENV, "")
    tail = LogTail()
    for name in LOGGERS:
        logging.getLogger(name).addHandler(tail)
    rng = random.Random(seed)
    pool = await open_pool(dsns.worker, max_size=24)
    api_pool = await open_pool(dsns.api, max_size=4)
    world = None
    licensing = contextlib.ExitStack()
    try:
        engine = SoakEngine(random.Random(rng.random()))
        licensing.enter_context(licence_requeues(engine))
        # A requeued job waits REQUEUE_PAUSE_S (30 s) before it can be claimed again; a seed lives seconds, so
        # shorten it or requeues would never run twice. `reclaimed` in the stats shows they still do.
        licensing.enter_context(unittest.mock.patch.object(job_apply, "REQUEUE_PAUSE_S", 0.2))
        browser, slots = rng.random() < 0.3, rng.choice((1, 2))
        world = World(pool, api_pool, rng, engine, objects, browser, slots)
        key_id = await seed_data(dsns.owner, rng, rng.randint(2, 4))
        script_world(rng, engine, await create_jobs(api_pool, rng, key_id, world.settings))
        world.t_db = (await world.sql("SELECT extract(epoch FROM clock_timestamp())"))[0][0]
        engine.t0 = time.monotonic()
        world.start()
        try:
            await world.chaos(chaos_s)
            forced = await world.settle(settle_s)
        finally:
            failpoints.disarm()
            crashed = await world.stop() + ([crash_note] if crash_note else [])
        if sabotage is not None:
            await sabotage(world)
        problems = await verify(world, dsns.owner, crashed)
        if problems:
            problems += ["log: " + line[:300] for line in tail.lines[-12:]]
        counts = Counter(r[0] for r in await world.sql("SELECT status FROM jobs"))
        return problems, dict(browser=browser, restarts=world.restarts, forced=forced,
                              reclaimed=sum(1 for n in engine.sends.values() if n > 1), **counts)
    finally:
        for name in LOGGERS:
            logging.getLogger(name).removeHandler(tail)
        licensing.close()
        failpoints.disarm()
        if world is not None:
            for task in world.leftover_tasks():
                task.cancel()
            await asyncio.sleep(0)
        await api_pool.close()
        await pool.close()


# ---- the script ---------------------------------------------------------------------------------------------------
def _reset(owner_dsn: str) -> None:
    with psycopg.connect(owner_dsn, autocommit=True, options="-c search_path=relay") as c:
        names = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'relay' "
                                         "AND tablename NOT IN ('schema_migrations', 'billing_pin')")]
        c.execute("TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(names))
        c.execute("DELETE FROM billing_pin")
        c.execute("INSERT INTO billing_pin (tz) VALUES ('UTC')")


def run_split(seeds: list[int], jobs: int, verbose: bool) -> int:
    """The seeds in `jobs` child processes of this script; their lines are shown as they come. The stage is green only
    when every requested seed was seen to run exactly once and no seed, process or reader failed: what the parent was
    asked to run is not evidence that it ran."""
    os.environ.setdefault("RELAY_TEST_PG_TEMPLATE", "relay_tpl_%d_%d" % (time.time(), os.getpid()))
    slices = [seeds[i::jobs] for i in range(min(jobs, len(seeds)))]
    began, failed, broken = time.monotonic(), [], set()
    ran = Counter()                                   # seed -> how many result lines the parent saw
    lock = threading.Lock()

    def drive(index: int, mine: list[int]) -> None:
        cmd = [sys.executable, __file__, "--seed-list", ",".join(map(str, mine))] + (["--verbose"] if verbose else [])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for text in proc.stdout:
            if text.startswith("soak: "):                     # the child's own summary; the parent prints one
                continue
            with lock:
                print(text, end="", flush=True)
                found = re.match(r"\s+SOAK FAILED seed (\d+) ", text)
                if found:
                    failed.append(int(found.group(1)))
                result = re.match(r"seed (\d+)\s+(ok|FAIL)\s", text)
                if result:
                    ran[int(result.group(1))] += 1
        if proc.wait() != 0 and not any(s in failed for s in mine):
            with lock:
                broken.add(index)
                print("   SOAK FAILED process %d (seeds %s) exited with status %d" % (index, mine, proc.returncode))

    with ThreadPoolExecutor(max_workers=len(slices)) as pool:
        futures = [pool.submit(drive, i, mine) for i, mine in enumerate(slices)]
    for index, future in enumerate(futures):              # an exception in a worker reaches the parent here
        try:
            future.result()
        except Exception as e:
            broken.add(index)
            print("   SOAK FAILED process %d (seeds %s) could not be run or read: %r" % (index, slices[index], e))
    skipped = {s for i in broken for s in slices[i]}      # a process already reported does not report its seeds again
    missing = [s for s in seeds if ran[s] == 0 and s not in skipped]
    twice = [s for s in seeds if ran[s] > 1]
    unasked = sorted(s for s in ran if s not in seeds)
    for text, which in (("never ran", missing), ("ran more than once", twice), ("ran but was not asked for", unasked)):
        if which:
            print("   SOAK FAILED seeds %s %s" % (which, text))
    failed.sort()
    bad = len(failed) + len(broken) + len(missing) + len(twice) + len(unasked)
    names = " (%s)" % ", ".join(map(str, failed)) if failed else ""
    print("soak: %d seed(s), %d failed%s, %.0fs" % (len(seeds), bad, names, time.monotonic() - began))
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="soak.py", description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=200, help="how many seeds to run (default 200)")
    ap.add_argument("--start", type=int, default=0, help="the first seed (default 0)")
    ap.add_argument("--seed", type=int, help="run this one seed alone (a replay)")
    ap.add_argument("--jobs", type=int, default=1, help="processes running seeds at once (default 1)")
    ap.add_argument("--seed-list", help=argparse.SUPPRESS)       # the seeds of one child process of --jobs
    ap.add_argument("--verbose", action="store_true", help="show the errors the loops log (chaos makes plenty)")
    args = ap.parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.WARNING, format="    log %(name)s: %(message)s")
        for name in LOGGERS:
            logging.getLogger(name).propagate = True
    seeds = [args.seed] if args.seed is not None else list(range(args.start, args.start + args.seeds))
    if args.seed_list:
        seeds = [int(n) for n in args.seed_list.split(",")]
    endpoint = os.environ.get("RELAY_TEST_MINIO", "")
    if not endpoint:
        print("RELAY_TEST_MINIO is not set: run the soak through scripts/check.sh --full", file=sys.stderr)
        return 2
    if args.jobs > 1 and not args.seed_list and len(seeds) > 1:
        return run_split(seeds, args.jobs, args.verbose)
    store_ = ObjectStore(endpoint, os.environ["RELAY_TEST_MINIO_ACCESS_KEY"], os.environ["RELAY_TEST_MINIO_SECRET_KEY"],
                         bucket="relay-soak-" + uuid.uuid4().hex[:10])
    asyncio.run(store_.ensure_bucket())
    failed, began = [], time.monotonic()
    try:
        with migrated_database({"relay_api": API_PASSWORD, "relay_worker": WORKER_PASSWORD}, "soak") as dsn:
            dsns = Dsns(dsn, make_conninfo(dsn, user="relay_api", password=API_PASSWORD),
                        make_conninfo(dsn, user="relay_worker", password=WORKER_PASSWORD))
            for seed in seeds:
                _reset(dsn)
                t0 = time.monotonic()
                problems, stats = asyncio.run(run_seed(dsns, seed, store_))
                print("seed %-4d %-4s %5.1fs  %s" % (seed, "ok" if not problems else "FAIL", time.monotonic() - t0,
                                                     " ".join("%s=%s" % kv for kv in sorted(stats.items()))),
                      flush=True)
                if problems:
                    failed.append(seed)
                    print("   SOAK FAILED seed %d (replay: scripts/soak.py --seed %d)" % (seed, seed))
                    for p in problems[:30]:
                        print("   - " + p)
    finally:
        for o in store_._client.list_objects(store_.bucket, recursive=True):
            store_._client.remove_object(store_.bucket, o.object_name)
        store_._client.remove_bucket(store_.bucket)
    print("soak: %d seed(s), %d failed%s, %.0fs" % (len(seeds), len(failed),
                                                      " (%s)" % ", ".join(map(str, failed)) if failed else "",
                                                      time.monotonic() - began))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
