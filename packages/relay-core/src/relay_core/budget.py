"""The browser semaphore (invariant I28): one per browser worker, for every browser purpose.

A browser opens only with a `Token` from this object; `open_session` refuses to launch without one. The size is the
live setting `<p>.browser_slots`, bounded by the provider's spec. Lowering it retires idle
slots only: tokens in use are never revoked, and nothing new is granted until enough of them come back.

Shares (so submits cannot starve the rest, and the rest cannot starve submits):
    reconcile        at most 2 tokens at once
    check + command  at most 1 token at once, together
    submit           the rest: `size - 3`, never below 1
The total never passes `size`. A waiter is served in arrival order and only when its own share and the total allow.

Restart back-off: after an abnormal exit the worker calls `start_ramp()`; the submit share then starts at half and
doubles every healthy minute until it is whole again.

Pure asyncio, no I/O. The clock is injected (`time.monotonic` by default); with an injected clock, call `tick()`
after moving it so waiters blocked by the ramp are looked at again.
"""

import asyncio
import time
from collections.abc import Callable
from enum import StrEnum


class Purpose(StrEnum):
    SUBMIT = "submit"
    RECONCILE = "reconcile"
    CHECK = "check"
    COMMAND = "command"


RECONCILE_SHARE = 2
OTHER_SHARE = 1             # check and command together
RAMP_PERIOD_S = 60.0        # after an abnormal restart the submit share doubles each period
_GROUP = {Purpose.SUBMIT: "submit", Purpose.RECONCILE: "reconcile", Purpose.CHECK: "other",
          Purpose.COMMAND: "other"}


class Token:
    """Proof that a browser may be open. Release it when the browser is closed (idempotent)."""

    def __init__(self, budget: "BrowserBudget", purpose: Purpose):
        self._budget, self.purpose, self.released = budget, purpose, False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self._budget._give_back(self)

    def __enter__(self) -> "Token":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class BrowserBudget:
    def __init__(self, slots: int, *, clock: Callable[[], float] = time.monotonic,
                 ramp_period_s: float = RAMP_PERIOD_S):
        if slots < 0:
            raise ValueError("slots must be >= 0")
        self._size, self._clock, self._period = slots, clock, ramp_period_s
        self._busy = {"submit": 0, "reconcile": 0, "other": 0}
        self._by_purpose = dict.fromkeys(Purpose, 0)
        self._waiters: list[tuple[Purpose, asyncio.Future[Token]]] = []
        self._ramp_started: float | None = None

    # ---- state -------------------------------------------------------------------------------------------------
    @property
    def size(self) -> int:
        return self._size

    @property
    def busy(self) -> int:
        return sum(self._busy.values())

    def busy_by(self, purpose: Purpose) -> int:
        return self._by_purpose[purpose]

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    def resize(self, slots: int) -> None:
        """Follow the live setting. Lowering never revokes a token: `busy` may exceed `size` until tokens return,
        and nothing is granted meanwhile. Raising serves waiters at once."""
        if slots < 0:
            raise ValueError("slots must be >= 0")
        self._size = slots
        self._grant()

    def start_ramp(self) -> None:
        """Call after an abnormal restart: the submit share starts at half and doubles each healthy minute."""
        self._ramp_started = self._clock()

    def submit_cap(self) -> int:
        full = max(1, self._size - RECONCILE_SHARE - OTHER_SHARE)
        if self._ramp_started is None:
            return full
        steps = int((self._clock() - self._ramp_started) // self._period)
        cap = min(full, max(1, full // 2) * 2 ** min(steps, 30))
        if cap >= full:
            self._ramp_started = None
        return cap

    def _cap(self, group: str) -> int:
        return {"submit": self.submit_cap(), "reconcile": RECONCILE_SHARE, "other": OTHER_SHARE}[group]

    def _eligible(self, purpose: Purpose) -> bool:
        group = _GROUP[purpose]
        return self.busy < self._size and self._busy[group] < self._cap(group)

    # ---- acquiring ---------------------------------------------------------------------------------------------
    def _take(self, purpose: Purpose) -> Token:
        self._busy[_GROUP[purpose]] += 1
        self._by_purpose[purpose] += 1
        return Token(self, purpose)

    def _give_back(self, token: Token) -> None:
        self._busy[_GROUP[token.purpose]] -= 1
        self._by_purpose[token.purpose] -= 1
        self._grant()

    def _grant(self) -> None:
        """Serve waiters in arrival order; one that is not eligible yet does not block a later one that is."""
        for entry in list(self._waiters):
            purpose, fut = entry
            if fut.done():
                self._waiters.remove(entry)
            elif self._eligible(purpose):
                self._waiters.remove(entry)
                fut.set_result(self._take(purpose))

    def tick(self) -> None:
        """Look at the waiters again (after the clock moved: the ramp may have opened room)."""
        self._grant()

    def try_acquire(self, purpose: Purpose) -> Token | None:
        """A token now, or None. The claim loop uses this for submits: none free means nothing is claimed."""
        self._grant()
        return self._take(purpose) if self._eligible(purpose) else None

    async def acquire(self, purpose: Purpose) -> Token:
        """Wait for a token. A caller never opens a browser without one."""
        token = self.try_acquire(purpose)
        if token is not None:
            return token
        fut: asyncio.Future[Token] = asyncio.get_running_loop().create_future()
        entry = (purpose, fut)
        self._waiters.append(entry)
        try:
            return await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                fut.result().release()        # granted in the same instant it was cancelled
            if entry in self._waiters:
                self._waiters.remove(entry)
            raise
