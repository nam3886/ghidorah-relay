"""Live slot count and restart back-off of the browser semaphore (contracts 3, D20): lowering retires idle slots
only, raising serves waiters, and after an abnormal restart the submit share starts at half and doubles each healthy
minute. A fake clock, no sleeps."""

import asyncio

from relay_core.budget import BrowserBudget, Purpose
from relay_core.testing.mutation import mutation

B = "packages/relay-core/src/relay_core/budget.py"
S, R = Purpose.SUBMIT, Purpose.RECONCILE


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_lowering_the_slots_retires_idle_ones_and_never_revokes_a_running_browser():
    b = BrowserBudget(6)
    tokens = [b.try_acquire(S) for _ in range(3)] + [b.try_acquire(R)]
    assert b.busy == 4
    b.resize(2)
    assert b.size == 2 and b.busy == 4 and not any(t.released for t in tokens)   # nothing was cut off
    assert b.try_acquire(S) is None and b.try_acquire(R) is None                 # and nothing new is granted
    tokens[0].release()
    tokens[1].release()
    assert b.busy == 2 and b.try_acquire(S) is None                              # at the new size: still full
    tokens[2].release()
    assert b.try_acquire(S) is not None                                          # one slot is now free


@mutation(B, "        self._size = slots\n        self._grant()\n", "        self._size = slots\n",
           expect="return await asyncio.wait_for(waiter, 1)")
def test_raising_the_slots_serves_the_waiters_at_once():
    async def scenario():
        b = BrowserBudget(1)
        b.try_acquire(S)
        waiter = asyncio.create_task(b.acquire(R))
        await asyncio.sleep(0.01)
        assert not waiter.done()
        b.resize(4)
        return await asyncio.wait_for(waiter, 1)

    assert asyncio.run(scenario()).purpose == R


@mutation(B, "max(1, full // 2)", "full", expect="assert 5 == 2")
def test_after_a_restart_the_submit_share_starts_at_half():
    clock = Clock()
    b = BrowserBudget(8, clock=clock)                   # submit share 5
    assert b.submit_cap() == 5
    b.start_ramp()
    assert b.submit_cap() == 2
    got = []
    while (t := b.try_acquire(S)) is not None:
        got.append(t)
    assert len(got) == 2


@mutation(B, "* 2 ** min(steps, 30)", "* 1", expect="assert [5, 5, 5, 5] == [5, 10, 11, 11]")
def test_the_share_doubles_each_healthy_minute_until_it_is_whole():
    clock = Clock()
    b = BrowserBudget(14, clock=clock)                  # submit share 11, half 5
    b.start_ramp()
    seen = []
    for _ in range(3):
        seen.append(b.submit_cap())
        clock.now += 60
    seen.append(b.submit_cap())
    assert seen == [5, 10, 11, 11]
    assert b._ramp_started is None                      # whole again: the ramp is over


def test_a_waiter_held_back_by_the_ramp_is_served_when_the_clock_moves_and_tick_runs():
    async def scenario():
        clock = Clock()
        b = BrowserBudget(8, clock=clock)
        b.start_ramp()
        held = [b.try_acquire(S), b.try_acquire(S)]
        assert b.try_acquire(S) is None
        waiter = asyncio.create_task(b.acquire(S))
        await asyncio.sleep(0.01)
        assert not waiter.done()
        clock.now += 60
        b.tick()
        token = await asyncio.wait_for(waiter, 1)
        return held, token

    held, token = asyncio.run(scenario())
    assert token.purpose == S


def test_the_ramp_never_gives_reconcile_or_check_less_than_their_share():
    b = BrowserBudget(8, clock=Clock())
    b.start_ramp()
    for _ in range(3):
        b.try_acquire(R)
    assert b.busy_by(R) == 2
