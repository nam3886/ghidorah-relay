"""The worker process of one provider: every loop of the provider, one stop signal.

It composes the claim loop, the poller, the fetcher, the reconciler, the reaper and the command runner (which also
runs the account checks, the title sweep and the catalog refresh). Each loop is its own task; they share the pool,
the settings, the browser budget and the lease owner. The owner is one id per BOOT (`<worker id>/<short uuid>`, see
`serve`): a restarted process never keeps a dead one's leases alive and two workers never touch each other's.
One `LeaseKeeper` serves the checks and the commands. The claim loop and the reconciler build their own keeper inside
(their classes take none), so a worker runs three heartbeat statements; each extends only the leases its own keeper
holds (a task registers its lease with `holding()` for as long as it needs it), so a lease nobody holds expires
within its TTL.

Start: a database with no billing pin (`relay migrate` writes it) refuses to start (exit 2). The heartbeat row is the
first write, before any loop starts: a restore mark holds the heartbeat table, so a booting worker waits for it as a
whole (N17). The heartbeat row is `worker-<provider>`: one worker process per provider (the reaper derives the worker
id from a lease owner by removing the final "/<suffix>").

Stop (SIGTERM, SIGINT, `stop()`), in this order:
  1. claims, checks and commands stop (no new one starts);
  2. in-flight submits finish or are fenced (the claim loop applies `grace_s` itself); commands finish or are
     cancelled at the deadline;
  3. the poller, fetcher, reconciler and reaper finish the item they are on, in what is left of ONE deadline
     (`grace_s` from the stop) that bounds steps 2 and 3 together (R14);
  4. the leases of this owner are released, except that of a job still `submitting`: the reaper moves that job from
     its lease, so deleting the lease would leave a job nobody can move (I1). In the same transaction a command that
     was running under a released lease is failed, so the admin can run it again (R11);
  5. the exit code: 0, or 1 after the claim loop's error limit or a loop that died.
"""

import asyncio
import contextlib
import logging
import os
import signal
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from psycopg.errors import LockNotAvailable
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from relay_core import cgroup, crypto, leases, pool
from relay_core.billing import BillingTzMismatch, verify_billing_tz
from relay_core.budget import BrowserBudget
from relay_core.db import open_pool, tx
from relay_core.jobs.apply import STARTUP_GRACE_S, StartupGrace
from relay_core.jobs.checks import Checks, Env
from relay_core.jobs.commands import CommandLoop
from relay_core.jobs.engine import BEAT_S, MAX_ERRORS, ClaimLoop
from relay_core.jobs.evidence import job_sink
from relay_core.jobs.fetcher import Fetcher
from relay_core.jobs.poller import Poller
from relay_core.jobs.reaper import INTERVAL_S, Reaper
from relay_core.jobs.reconcile import Reconciler
from relay_core.jobs.runctx import EvidenceSink, load_account_view
from relay_core.jobs.titles import SWEEP_EVERY_S
from relay_core.providers import AccountView, ProviderEngine, ProviderRegistry, ProviderSpec
from relay_core.settings import REGISTRY, Registry, Settings
from relay_core.storage import ObjectStore

log = logging.getLogger(__name__)

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
REGISTER_RETRY_S = 1.0         # between tries of the first heartbeat write while a restore mark holds its table
DEFAULT_GRACE_S = 720.0        # must stay below compose's stop_grace_period (780 s) so Docker never kills first

LoadAccount = Callable[[Any], Awaitable[AccountView]]

# Every lease of this owner goes, except one that still holds a `submitting` job (see the module docstring).
_RELEASE = ("DELETE FROM leases l WHERE l.owner = %s AND NOT EXISTS "
            "(SELECT 1 FROM jobs j WHERE j.id = l.job_id AND j.status = 'submitting') RETURNING l.id")
_FAIL_RUNNING = ("UPDATE commands SET status = 'failed', finished_at = clock_timestamp(), result = %s "
                 "WHERE lease_id = ANY(%s) AND status = 'running'")


class Worker:
    def __init__(self, db: AsyncConnectionPool, spec: ProviderSpec, engine: ProviderEngine, settings: Any, *,
                 owner: str, load_account: LoadAccount, store: ObjectStore, budget: BrowserBudget | None = None,
                 container: Mapping[str, Any] | None = None, evidence_sink: EvidenceSink | None = None,
                 grace_s: float | None = DEFAULT_GRACE_S, max_errors: int = MAX_ERRORS,
                 tick_s: float = 1.0, lease_ttl_s: float = leases.LEASE_TTL_S,
                 lease_beat_s: float = leases.HEARTBEAT_S, beat_s: float = BEAT_S,
                 reaper_interval_s: float = INTERVAL_S, housekeeping_s: float = SWEEP_EVERY_S,
                 worker_id: str | None = None,
                 startup_grace_s: float = STARTUP_GRACE_S):
        self.db, self.spec, self.owner, self.grace_s = db, spec, owner, grace_s
        self.tick_s, self._housekeeping_s = tick_s, housekeeping_s
        self.grace = grace = StartupGrace(startup_grace_s)
        caps = spec.caps
        common = dict(owner=owner, load_account=load_account, evidence_sink=evidence_sink, grace=grace)
        self.keeper = leases.LeaseKeeper(db, owner, ttl_s=lease_ttl_s, heartbeat_s=lease_beat_s)
        self.claims = ClaimLoop(
            db, spec, engine, settings, owner=owner, load_account=lambda claim: load_account(claim.account_id),
            budget=budget, container=container, evidence_sink=evidence_sink, tick_s=tick_s,
            lease_ttl_s=lease_ttl_s, lease_beat_s=lease_beat_s, beat_s=beat_s, max_errors=max_errors,
            grace_s=grace_s, worker_id=worker_id)
        self.poller = Poller(db, spec, engine, settings, budget=budget, tick_s=tick_s, **common) \
            if caps.has_poll else None
        self.fetcher = Fetcher(db, spec, engine, settings, objects=store, tick_s=tick_s, **common)
        self.reconciler = Reconciler(db, spec, engine, settings, budget=budget, tick_s=tick_s,
                                     lease_ttl_s=lease_ttl_s, lease_beat_s=lease_beat_s, **common) \
            if caps.has_reconcile else None
        self.reaper = Reaper(db, spec, settings, interval_s=reaper_interval_s,
                             owner_gone_s=3 * beat_s, grace=grace)
        env = Env(db=db, spec=spec, engine=engine, settings=settings, owner=owner, keeper=self.keeper,
                  load_account=load_account, store=store, budget=budget, ttl_s=lease_ttl_s)
        self.commands = CommandLoop(env, housekeeping_s=housekeeping_s, checks=Checks(env))
        self._stop = asyncio.Event()
        self._stop_commands = asyncio.Event()
        self.exit_code = EXIT_OK

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        """The one stop signal."""
        self._stop.set()

    async def _verify_billing(self) -> None:
        async with tx(self.db) as conn:
            await verify_billing_tz(conn)

    # ---- run ---------------------------------------------------------------------------------------------------
    async def run(self, *, install_signals: bool = False) -> int:
        """Run until `stop()` or a signal, then shut down in order. Returns the exit code (see the module docstring)."""
        try:
            await self._verify_billing()
        except BillingTzMismatch as e:
            log.critical("the worker refuses to start: %s", e)
            return EXIT_REFUSED
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT) if install_signals else ()
        for sig in signals:
            loop.add_signal_handler(sig, self.stop)
        def drop_signals() -> None:
            for sig in signals:
                loop.remove_signal_handler(sig)

        try:
            # The heartbeat row is the first write of the process, before any loop starts: a restore mark holds the
            # heartbeat table (SHARE), so a worker that boots during it waits here, whole, not only its claim loop.
            registered = await self._register_when_free()
        except BaseException:
            drop_signals()
            raise
        if not registered:           # stopped while the mark still held the table: nothing was started
            drop_signals()
            return self.exit_code
        keeper_stop = asyncio.Event()
        keeper = asyncio.create_task(self.keeper.run(keeper_stop), name="lease-keeper")
        tasks = self._start()
        stop_wait = asyncio.create_task(self._stop.wait(), name="stop-signal")
        try:
            done, _ = await asyncio.wait({stop_wait, *tasks.values()}, return_when=asyncio.FIRST_COMPLETED)
            for name, task in tasks.items():
                if task in done:
                    self._ended_early(name, task)
        finally:
            stop_wait.cancel()
            self.stop()
            await self._shutdown(tasks)
            keeper_stop.set()
            await asyncio.gather(keeper, return_exceptions=True)
            await self._release()
            for sig in signals:
                loop.remove_signal_handler(sig)
        return self.exit_code

    async def _register_when_free(self) -> bool:
        """The first heartbeat write, retried until it succeeds: a restore mark holds the heartbeat table longer than
        the session's `lock_timeout`, and the worker waits for it (N17) instead of exiting. False when `stop()` came
        first."""
        while not self.stopping:
            try:
                await self.claims._register()
                return True
            except LockNotAvailable:
                log.warning("waiting: a restore mark holds the heartbeat table")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), REGISTER_RETRY_S)
        return False

    def _start(self) -> dict[str, asyncio.Task]:
        self.grace.restart()          # counted from the loops starting, not from the object being made
        runs: dict[str, Callable[[], Awaitable]] = {
            "claims": self.claims.run, "fetcher": self.fetcher.run, "reaper": self.reaper.run,
            "commands": lambda: self.commands.run(self._stop_commands, self.tick_s)}
        if self.poller is not None:
            runs["poller"] = self.poller.run
        if self.reconciler is not None:
            runs["reconciler"] = self.reconciler.run
        return {name: asyncio.create_task(run(), name="worker-" + name) for name, run in runs.items()}

    def _ended_early(self, name: str, task: asyncio.Task) -> None:
        """A loop that ends before the stop signal is a failure, never an idle worker. The claim loop ends by
        itself at its error limit; `_shutdown` reads its exit code."""
        if name == "claims" and not task.cancelled() and task.exception() is None:
            return
        why = "cancelled" if task.cancelled() else task.exception()
        log.critical("worker loop %s ended on its own: %s", name, why)
        self.exit_code = EXIT_FAILED

    # ---- shutdown ----------------------------------------------------------------------------------------------
    async def _shutdown(self, tasks: dict[str, asyncio.Task]) -> None:
        """One deadline, `grace_s` from the stop, bounds the whole shutdown (R14); a loop still running at it is
        cancelled."""
        loop = asyncio.get_running_loop()
        deadline = None if self.grace_s is None else loop.time() + self.grace_s

        def remaining() -> float | None:
            return None if deadline is None else max(0.0, deadline - loop.time())

        # 1. no new claim, check or command; submits finish or are fenced, inside the same deadline (the claim loop
        # bounds its own drain by it; a loop that outlasts it, a blocked write included, is cancelled)
        self.claims.stop()
        self._stop_commands.set()
        await asyncio.gather(self._wait(tasks["claims"], remaining()), self._wait(tasks["commands"], remaining()))
        # 2. the other loops finish the item they are on, in what is left of the deadline
        for loop_ in (self.poller, self.fetcher, self.reconciler, self.reaper):
            if loop_ is not None:
                loop_.stop()
        await asyncio.gather(*(self._wait(t, remaining()) for name, t in tasks.items()
                               if name not in ("claims", "commands")))
        self.exit_code = max(self.exit_code, self.claims.exit_code)      # also when the loop was cancelled mid-drain

    async def _wait(self, task: asyncio.Task, timeout: float | None) -> None:
        if not task.done():
            _, pending = await asyncio.wait({task}, timeout=timeout)
            for t in pending:
                log.warning("worker loop %s did not stop within %.0f s: cancelled", t.get_name(), timeout or 0)
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if not task.cancelled() and task.exception() is not None:
            log.error("worker loop %s failed: %r", task.get_name(), task.exception())
            self.exit_code = EXIT_FAILED

    async def _release(self) -> None:
        try:
            async with tx(self.db) as conn:
                ids = [r[0] for r in await (await conn.execute(_RELEASE, (self.owner,))).fetchall()]
                failed = 0
                if ids:
                    failed = (await conn.execute(_FAIL_RUNNING, (
                        Jsonb({"error": "the worker stopped before the command finished"}), ids))).rowcount
            log.info("worker %s stopped, %d leases released, %d running commands failed", self.owner, len(ids), failed)
        except Exception as e:
            log.error("worker %s could not release its leases (they expire by themselves): %s", self.owner, e)
            self.exit_code = max(self.exit_code, EXIT_FAILED)


def boot_owner(worker_id: str) -> str:
    """The lease owner of one worker boot: a restart gets a new one, so it never inherits a dead process's leases."""
    return "%s/%s" % (worker_id, uuid.uuid4().hex[:8])


# ---- the process: environment in, exit code out ---------------------------------------------------------------
def provider_settings(spec: ProviderSpec) -> Registry:
    return Registry(*REGISTRY.specs.values(), *spec.settings().values())


async def serve(spec: ProviderSpec, build_engine: Callable[[ProviderSpec, Settings], ProviderEngine],
                env: Mapping[str, str] = os.environ, *, install_signals: bool = True) -> int:
    """Everything a worker process needs, from the environment: DSN, the private keys of the credential seal and the
    object store; a browser provider's size is its `<p>.browser_slots` setting, the stop grace is DEFAULT_GRACE_S and
    the heartbeat row `worker-<provider>`. The worker also reads its own container limits once and reports them.
    Returns the exit code."""
    dsn = env.get("DATABASE_URL", "")
    if not dsn:
        log.critical("DATABASE_URL is not set")
        return EXIT_REFUSED
    try:
        keys = crypto.private_keys_from_env(env)
        # The keys live in this process's memory from here on (the list is all a rotation needs). Nothing that runs
        # later, an engine, a browser or any child process, can read them from the environment.
        for source in (os.environ, env):
            if isinstance(source, MutableMapping):
                source.pop(crypto.PRIVATE_KEY_VAR, None)
        store = ObjectStore.from_env(env)
        browser = spec.caps.execution == "browser"
        worker_id = "worker-" + spec.name
        container = cgroup.own_limits()
        ProviderRegistry().register(spec)        # a spec that does not declare the settings the core reads is refused
    except Exception as e:                       # a missing or malformed variable: refuse before any connection
        log.critical("the worker cannot start: %s", e)
        return EXIT_REFUSED
    db = await open_pool(dsn, name="worker-" + spec.name)
    settings = Settings(db, dsn, registry=provider_settings(spec), open_keys=keys)     # opens the secret settings
    try:
        await settings.start()
        budget = None
        if browser:
            budget = BrowserBudget(settings.get(spec.name + ".browser_slots"))

        async def load_account(account_id) -> AccountView:
            return await load_account_view(db, account_id, keys, pool.pool_configs(spec.name, spec.caps, settings.get))

        worker = Worker(db, spec, build_engine(spec, settings), settings, worker_id=worker_id,
                        owner=boot_owner(worker_id), load_account=load_account, store=store,
                        budget=budget, container=container, evidence_sink=job_sink(db, store),
                        grace_s=DEFAULT_GRACE_S)
        return await worker.run(install_signals=install_signals)
    finally:
        await settings.stop()
        with contextlib.suppress(Exception):
            await db.close()
