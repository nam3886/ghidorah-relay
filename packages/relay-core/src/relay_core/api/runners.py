"""Background loops of the api process: the webhook sender and the global sweeps.

`start_runners(...)` starts them, `await runners.stop()` ends them (`relay serve` calls both). Each loop takes one
step, logs a failure and goes on: a bad pass never ends a loop. Every step is a CAS update or a SKIP LOCKED claim,
so a second api process running the same loops is safe.

  webhooks     `jobs.webhooks.deliver_due`; a full batch is followed at once by the next one.
  housekeeping `jobs.sweeps.run_housekeeping`: retention, orphan media (only when the `media_orphan_sweep` setting
               is on), expired admin sessions and expired input links. Settings are read on every pass.
  ledger       `jobs.sweeps.ledger_check`, daily.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from psycopg_pool import AsyncConnectionPool

from relay_core import net
from relay_core.jobs import sweeps, webhooks
from relay_core.settings import Settings
from relay_core.storage import ObjectStore

log = logging.getLogger("relay.api.runners")

WEBHOOK_EVERY_S = 5.0
HOUSEKEEPING_EVERY_S = 900.0
LEDGER_EVERY_S = 86_400.0
STOP_WAIT_S = 5.0          # inside the container's stop grace, with the pool close still to come


class Runners:
    def __init__(self, tasks: list[asyncio.Task], stopping: asyncio.Event):
        self._tasks, self._stopping = tasks, stopping

    async def stop(self) -> None:
        """Ask every loop to end after its current step and wait for them at most `STOP_WAIT_S`; a step still running
        then is cancelled (every step is a CAS update or a SKIP LOCKED claim, so a cut one is picked up again)."""
        self._stopping.set()
        _, pending = await asyncio.wait(self._tasks, timeout=STOP_WAIT_S)
        for task in pending:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


async def _loop(name: str, every_s: float, step: Callable[[], Awaitable[bool | None]], stopping: asyncio.Event):
    """Run `step` now and then every `every_s`. A step that returns True has more to do and runs again at once."""
    while not stopping.is_set():
        more = False
        try:
            more = bool(await step())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("runner %s: step failed", name)
        if not more:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), every_s)


def start_runners(pool: AsyncConnectionPool, settings: Settings, store: ObjectStore, *,
                  webhook_every_s: float = WEBHOOK_EVERY_S, housekeeping_every_s: float = HOUSEKEEPING_EVERY_S,
                  ledger_every_s: float = LEDGER_EVERY_S, resolver: net.Resolver = net.system_resolver,
                  transport=None) -> Runners:
    """Start the loops on the running event loop. `resolver` and `transport` go to the webhook sender (tests)."""
    stopping = asyncio.Event()

    async def send_webhooks() -> bool:
        return len(await webhooks.deliver_due(pool, resolver=resolver, transport=transport)) >= webhooks.BATCH

    async def housekeeping() -> None:
        await sweeps.run_housekeeping(pool, store, retention_days=settings.get("log_retention_days"),
                                      orphan_age_h=settings.get("media_orphan_age_h"),
                                      orphans_enabled=settings.get("media_orphan_sweep"))

    async def ledger() -> None:
        await sweeps.ledger_check(pool)

    loops = (("webhooks", webhook_every_s, send_webhooks), ("housekeeping", housekeeping_every_s, housekeeping),
             ("ledger", ledger_every_s, ledger))
    return Runners([asyncio.create_task(_loop(n, e, s, stopping), name="relay-api-" + n) for n, e, s in loops],
                   stopping)
