"""Reconciling `unknown` jobs (contracts 7.1 "Unknown"; invariants I1, I2, I14, I27, I28).

A job whose send outcome is unknown is never re-sent. With `after_sent=reconcile` this loop reads the upstream
account to find out, at a growing interval (60 s doubling to 30 min, kept in `next_poll_at` by the store), until the
render deadline, when the reaper fails it, once a read made after the deadline has answered (this loop still
makes that final read).

Each reconcile runs under a `reconcile` lease on the account (hard bound 300 s, kept alive by a `LeaseKeeper`; a
lease that cannot be extended cancels the read) and, for a browser provider, a token from the reconcile share of the
semaphore (at most 2 at once). `engine.reconcile` runs with no connection held. The result is applied through
`store.apply_reconciled`, fenced by the lease id in the same transaction, which also deletes the lease: a reconcile
that lost its lease writes nothing. A read that fails or runs out of time counts as `StillUnknown`: the job keeps
waiting and is never moved by a failure. A job past its render deadline is still reconciled until a read after
it has answered.
"""

import asyncio
import contextlib
import logging

from relay_core import leases
from relay_core.budget import BrowserBudget, Purpose
from relay_core.jobs import store
from relay_core.jobs.claim import ReconcileClaim, claim_reconcile
from relay_core.jobs.poller import JobLoop, LoadAccount, ReadCtx
from relay_core.leases import LeaseKeeper, LeaseLost
from relay_core.providers import Reconciled, StillUnknown

log = logging.getLogger(__name__)

BOUND_SHARE = 0.9         # the read gets this much of the lease's hard bound, so its result can still be applied


class Reconciler(JobLoop):
    def __init__(self, db, spec, engine, settings, *, owner: str, load_account: LoadAccount,
                 budget: BrowserBudget | None = None, hard_s: float = leases.RECONCILE_HARD_S,
                 lease_ttl_s: float = leases.LEASE_TTL_S, lease_beat_s: float = leases.HEARTBEAT_S, **kw):
        if not spec.caps.has_reconcile:
            raise ValueError("%s declares no reconcile" % spec.name)
        if spec.caps.execution == "browser" and budget is None:
            raise ValueError("a browser provider needs the browser budget to reconcile")
        super().__init__(db, spec, engine, settings, owner=owner, load_account=load_account, **kw)
        self.budget, self.hard_s, self.lease_ttl_s = budget, hard_s, lease_ttl_s
        self._keeper = LeaseKeeper(db, owner, ttl_s=lease_ttl_s, heartbeat_s=lease_beat_s)

    async def _claim(self, n: int) -> list[ReconcileClaim]:
        claims: list[ReconcileClaim] = []
        deadline_s = self.settings.get(self.spec.name + ".render_deadline_s")
        while len(claims) < n:
            token = self.budget.try_acquire(Purpose.RECONCILE) if self.budget is not None else None
            if self.budget is not None and token is None:
                break                                   # the reconcile share is in use
            try:
                claim = await claim_reconcile(self.db, provider=self.spec.name, caps=self.caps,
                                              deadline_s=deadline_s, owner=self.owner, hard_s=self.hard_s,
                                              ttl_s=self.lease_ttl_s, token=token, grace=self.grace)
            except BaseException as e:
                if token is not None:
                    token.release()
                if claims and isinstance(e, Exception):
                    # The claims already committed hold leases: work them rather than drop them (a lease nobody
                    # holds is not extended, so it would only block its account until the TTL runs out).
                    log.warning("a reconcile claim failed after %d succeeded: %s", len(claims), e)
                    break
                raise
            if claim is None:
                if token is not None:
                    token.release()
                break
            claims.append(claim)
        return claims

    async def _read(self, claim: ReconcileClaim) -> Reconciled:
        """What the engine found, with no connection held. A failure or an out-of-time read is StillUnknown."""
        try:
            async with asyncio.timeout(claim.hard_s * BOUND_SHARE):
                view = await store.job_view(self.db, claim.job_id)
                ctx = ReadCtx(self.db, claim.job_id, account=await self.load_account(claim.account_id),
                              settings=self.settings, provider=self.spec.name, browser_token=claim.token,
                              lease_id=claim.lease_id, evidence_sink=self.evidence_sink)
                return await self.engine.reconcile(ctx, view)
        except (LeaseLost, asyncio.CancelledError):
            raise
        except TimeoutError:
            log.warning("reconcile of job %s ran out of time", claim.job_id)
        except Exception:
            log.exception("reconcile of job %s failed", claim.job_id)
        return StillUnknown()

    async def _work(self, claim: ReconcileClaim) -> None:
        try:
            async with self._keeper.holding(claim.lease_id) as settle:    # the read AND the fenced write: no gap
                result = await self._read(claim)
                settle()
                status = await self._shielded(store.apply_reconciled(
                    self.db, claim.job_id, result, read_started_at=claim.read_started_at, caps=self.caps,
                    pools=self._pools(), lease_id=claim.lease_id, release_lease=True,
                    render_deadline_s=self.settings.get(self.spec.name + ".render_deadline_s")))
            log.info("job %s reconciled: now %s", claim.job_id, status)
        except LeaseLost:
            log.warning("reconcile of job %s: lease %s lost, result dropped", claim.job_id, claim.lease_id)
        finally:
            if claim.token is not None:
                claim.token.release()

    async def run(self) -> None:
        """Run the loop with the lease heartbeat beside it."""
        keeper_stop = asyncio.Event()
        beat = asyncio.create_task(self._keeper.run(keeper_stop))
        try:
            await super().run()
        finally:
            keeper_stop.set()
            await beat
            with contextlib.suppress(Exception):
                await self.idle()
