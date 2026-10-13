"""The poller (contracts 7.1 "Poller"; invariants I17, I23) and the loop machinery the fetcher and the reconciler share.

A tick claims `waiting` jobs whose `next_poll_at` is due: each gets a fresh `poll_token` and a visibility time, in one
`SKIP LOCKED` statement, so two pollers never take the same job and a claim that is never applied (a crash) simply
becomes due again. `engine.poll` runs with NO connection held, under a bound; the result is applied through
`store.apply_poll`, which drops it when the token is no longer the job's (a Re-check or a newer claim took over).

Polling reads only, so it runs for jobs on disabled accounts too, and drain mode (`<p>.claims_enabled`) does not stop
it: open jobs still finish. A poll that fails or runs out of time is not a result: the job stays `waiting` and is
polled again after `error_retry_s`; the render deadline (reaper) bounds how long that can go on.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

from psycopg_pool import AsyncConnectionPool

from relay_core import pool
from relay_core.budget import BrowserBudget, Purpose, Token
from relay_core.db import tx
from relay_core.jobs import store
from relay_core.jobs.apply import StartupGrace
from relay_core.jobs.claim import Visible, claim_visible
from relay_core.jobs.events import add_event
from relay_core.jobs.runctx import EngineSource, EvidenceSink
from relay_core.leases import fence_once
from relay_core.providers import AccountView, JobView, Pending, PollResult, ProviderEngine, ProviderSpec

log = logging.getLogger(__name__)

TICK_S = 1.0
POLL_VISIBILITY_S = 300.0       # contracts 7.1: a claimed poll is hidden this long
POLL_BOUND_S = 120.0
ERROR_RETRY_S = 60.0
MAX_INFLIGHT = 16

LoadAccount = Callable[[Any], Awaitable[AccountView]]      # account id -> the decrypted view


class ReadCtx:
    """The RunCtx of a read-only run (a poll, a fetch, a reconcile): there is no job lease to move, so `mark_sent`
    and `report_ref` refuse. `fence()` checks the reconcile lease when there is one and is a no-op otherwise."""

    def __init__(self, db: AsyncConnectionPool, job_id, *, account: AccountView, settings: Any, provider: str,
                 browser_token: Token | None = None, lease_id: int | None = None,
                 evidence_sink: EvidenceSink | None = None):
        self._db, self._job_id, self._lease_id, self._evidence = db, job_id, lease_id, evidence_sink
        self.account, self.browser_token, self.settings = account, browser_token, settings
        self.log = logging.getLogger("relay.engine.%s" % provider)

    async def fence(self) -> None:
        if self._lease_id is not None:
            await fence_once(self._db, self._lease_id)

    async def mark_sent(self) -> None:
        raise RuntimeError("a read-only run never sends")

    async def report_ref(self, ref: str, state: Mapping[str, Any]) -> None:
        raise RuntimeError("a read-only run never reports a reference")

    async def event(self, text: str, stage: str, source: str) -> None:
        if source not in EngineSource:
            raise ValueError("an engine event source is one of %s" % (EngineSource,))
        async with tx(self._db) as conn:
            await add_event(conn, self._job_id, source, stage, text)

    async def evidence(self, name: str, data: bytes, mime: str) -> None:
        if self._evidence is None:
            self.log.warning("evidence %r dropped: no evidence sink is wired", name)
            return
        await self._evidence(name, data, mime)


class JobLoop:
    """Tick, spawn one task per claimed item, wait for them on stop. Subclasses define `_claim` and `_work`."""

    def __init__(self, db: AsyncConnectionPool, spec: ProviderSpec, engine: ProviderEngine, settings: Any, *,
                 owner: str, load_account: LoadAccount, evidence_sink: EvidenceSink | None = None,
                 tick_s: float = TICK_S, max_inflight: int = MAX_INFLIGHT, grace: StartupGrace | None = None):
        self.db, self.spec, self.caps, self.engine, self.settings = db, spec, spec.caps, engine, settings
        self.owner, self.load_account, self.evidence_sink = owner, load_account, evidence_sink
        self.tick_s, self.max_inflight, self.grace = tick_s, max_inflight, grace
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task] = set()
        self._applying: set[asyncio.Future] = set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()

    def _pools(self) -> dict[str, pool.PoolConfig]:
        return pool.pool_configs(self.caps, self.settings.get)

    async def _claim(self, n: int) -> list[Any]:
        raise NotImplementedError

    async def _work(self, item: Any) -> None:
        raise NotImplementedError

    async def tick(self) -> int:
        """Claim what is due (up to the free slots) and start it; returns how many started. A stopping loop
        claims nothing."""
        if self.stopping:
            return 0
        free = self.max_inflight - len(self._inflight)
        if free <= 0:
            return 0
        items = await self._claim(free)
        for item in items:
            task = asyncio.create_task(self._guard(item), name="%s-%s" % (type(self).__name__, item))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        return len(items)

    async def _guard(self, item: Any) -> None:
        try:
            await self._work(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s: %s could not be handled", type(self).__name__, item)

    async def idle(self) -> None:
        """Wait for every started task and every shielded write."""
        while self._inflight or self._applying:
            inflight, applying = list(self._inflight), list(self._applying)
            await asyncio.gather(*inflight, *applying, return_exceptions=True)
            # The sets are emptied by done-callbacks, which run only when the loop gets control, and `gather` of tasks
            # that are all finished returns without giving it: a drain that started in the pass in which a task ended
            # would spin forever. So the entries just waited for are removed here.
            self._inflight.difference_update(inflight)
            self._applying.difference_update(applying)

    async def _shielded(self, work: Coroutine) -> Any:
        """Run `work` to its end even when the caller is cancelled (a result that arrived is applied or dropped by
        its fence, never half-applied); `idle()` and `run()` wait for it."""
        inner = asyncio.ensure_future(work)
        self._applying.add(inner)
        inner.add_done_callback(self._applying.discard)
        return await asyncio.shield(inner)

    async def run(self) -> None:
        """Tick until `stop()`, then wait for what is in flight. A failing tick is logged and tried again."""
        try:
            while not self.stopping:
                try:
                    await self.tick()
                except Exception:
                    log.exception("%s: tick failed", type(self).__name__)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), self.tick_s)
        finally:
            await self.idle()


class Poller(JobLoop):
    """Polls `waiting` jobs. `needs_browser(job)` says whether a poll of that job needs a browser (most do not);
    when it does, a token from the reconcile share is held for the poll (contracts 3: a read takes the share a
    reconcile takes)."""

    def __init__(self, db, spec, engine, settings, *, owner: str, load_account: LoadAccount,
                 budget: BrowserBudget | None = None, needs_browser: Callable[[JobView], bool] | None = None,
                 visibility_s: float = POLL_VISIBILITY_S, bound_s: float = POLL_BOUND_S,
                 error_retry_s: float = ERROR_RETRY_S, **kw):
        if not spec.caps.has_poll:
            raise ValueError("%s declares no poll" % spec.name)
        if needs_browser is not None and budget is None:
            raise ValueError("a poll that needs a browser needs the browser budget")
        super().__init__(db, spec, engine, settings, owner=owner, load_account=load_account, **kw)
        self.budget, self.needs_browser = budget, needs_browser
        self.visibility_s, self.bound_s, self.error_retry_s = visibility_s, bound_s, error_retry_s

    async def _claim(self, n: int) -> list[Visible]:
        return await claim_visible(self.db, provider=self.spec.name, status="waiting",
                                   visibility_s=self.visibility_s, limit=n)

    async def _poll(self, c: Visible) -> PollResult | None:
        """The engine's answer, with no connection held. None: it did not answer (error or out of time)."""
        token = None
        try:
            async with asyncio.timeout(self.bound_s):
                view = await store.job_view(self.db, c.job_id)
                if self.needs_browser is not None and self.needs_browser(view):
                    token = await self.budget.acquire(Purpose.RECONCILE)
                ctx = ReadCtx(self.db, c.job_id, account=await self.load_account(c.account_id),
                              settings=self.settings, provider=self.spec.name, browser_token=token,
                              evidence_sink=self.evidence_sink)
                return await self.engine.poll(ctx, view)
        except TimeoutError:
            log.warning("poll of job %s ran past %.0f s", c.job_id, self.bound_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll of job %s failed", c.job_id)
        finally:
            if token is not None:
                token.release()
        return None

    async def _work(self, c: Visible) -> None:
        result = await self._poll(c) or PollResult(Pending("the poll did not answer", self.error_retry_s, {}, False))
        status = await self._shielded(store.apply_poll(self.db, c.job_id, c.token, result, caps=self.caps,
                                                       pools=self._pools(), read_started_at=c.claimed_at))
        if status is None:
            log.info("poll result of job %s dropped: its token is no longer current", c.job_id)
