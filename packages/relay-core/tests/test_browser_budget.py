"""The browser semaphore's shares and total (I28): whatever mix of submit, reconcile, check and
command is pending, never more browsers than its size, and each purpose within its share. Pure asyncio, no database."""

import asyncio
import random

import pytest

from relay_core.budget import BrowserBudget, Purpose
from relay_core.testing.mutation import mutation

B = "packages/relay-core/src/relay_core/budget.py"
ELIGIBLE = "return self.busy < self._size and self._busy[group] < self._cap(group)"
S, R, C, K = Purpose.SUBMIT, Purpose.RECONCILE, Purpose.CHECK, Purpose.COMMAND


def fill(budget, purpose):
    """Take tokens of one purpose until none is left; return them."""
    out = []
    while (t := budget.try_acquire(purpose)) is not None:
        out.append(t)
    return out


@mutation(B, ELIGIBLE, "return self.busy < self._size", expect="assert 8 == 5")
def test_each_purpose_stops_at_its_share():
    b = BrowserBudget(8)
    assert len(fill(b, S)) == 5                      # submit gets the rest: 8 - 2 - 1
    assert len(fill(b, R)) == 2
    assert len(fill(b, C)) == 1
    assert b.try_acquire(K) is None                  # check and command share one token
    assert b.busy == 8 and b.busy_by(S) == 5


@mutation(B, "max(1, self._size - RECONCILE_SHARE - OTHER_SHARE)", "self._size", expect="assert 8 == 5")
def test_a_flood_of_submits_leaves_room_for_reconcile_and_check():
    b = BrowserBudget(8)
    assert len(fill(b, S)) == 5
    assert b.try_acquire(R) is not None
    assert b.try_acquire(C) is not None


@mutation(B, ELIGIBLE, "return self._busy[group] < self._cap(group)", expect="assert (4 <= 1)")
@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_the_total_never_passes_the_size_even_when_the_shares_add_up_to_more(size):
    b = BrowserBudget(size)
    taken = [t for p in (S, R, R, C, K) for t in fill(b, p)]
    assert len(taken) <= size and b.busy <= size
    if b.busy == size:
        assert all(b.try_acquire(p) is None for p in (S, R, C, K))


@mutation(B, ELIGIBLE, "return self._busy[group] < self._cap(group)", expect="assert 4 <= 3")
def test_i28_a_crowd_of_unknown_jobs_checks_and_submits_never_exceeds_the_semaphore():
    """N reconciles + checks + commands + a full set of submits, all waiting at once on a size-3 semaphore."""
    async def scenario():
        b = BrowserBudget(3)
        peak = {"all": 0, "reconcile": 0, "other": 0, "submit": 0}
        rng = random.Random(7)

        async def worker(purpose):
            with await b.acquire(purpose):
                peak["all"] = max(peak["all"], b.busy)
                peak["reconcile"] = max(peak["reconcile"], b.busy_by(R))
                peak["other"] = max(peak["other"], b.busy_by(C) + b.busy_by(K))
                peak["submit"] = max(peak["submit"], b.busy_by(S))
                await asyncio.sleep(rng.random() * 0.005)

        mix = [R] * 20 + [C] * 15 + [K] * 15 + [S] * 40
        rng.shuffle(mix)
        await asyncio.gather(*[worker(p) for p in mix])
        assert b.busy == 0 and b.waiting == 0
        return peak

    peak = asyncio.run(scenario())
    assert peak["all"] <= 3, peak
    assert peak["reconcile"] <= 2 and peak["other"] <= 1, peak


def test_a_waiter_gets_no_token_until_one_is_returned_and_is_served_in_arrival_order():
    async def scenario():
        b = BrowserBudget(1)
        held = b.try_acquire(S)
        order = []

        async def waiter(name, purpose):
            with await b.acquire(purpose):
                order.append(name)

        first = asyncio.create_task(waiter("reconcile", R))
        await asyncio.sleep(0)
        second = asyncio.create_task(waiter("submit", S))
        await asyncio.sleep(0.01)
        assert order == [] and b.waiting == 2 and b.busy == 1       # nobody opened anything without a token
        held.release()
        await asyncio.gather(first, second)
        return order

    assert asyncio.run(scenario()) == ["reconcile", "submit"]


@mutation(B, "for entry in list(self._waiters):", "for entry in reversed(list(self._waiters)):",
           expect="assert ['c', 'b', 'a'] == ['a', 'b', 'c']")
def test_the_oldest_waiter_is_served_first():
    async def scenario():
        b = BrowserBudget(1)
        held = b.try_acquire(S)
        got = []

        async def waiter(name):
            token = await b.acquire(S)
            got.append(name)
            await asyncio.sleep(0)
            token.release()

        tasks = []
        for name in ("a", "b", "c"):
            tasks.append(asyncio.create_task(waiter(name)))
            await asyncio.sleep(0)
        held.release()
        await asyncio.gather(*tasks)
        return got

    assert asyncio.run(scenario()) == ["a", "b", "c"]


def test_a_cancelled_waiter_leaks_nothing_and_a_double_release_is_harmless():
    async def scenario():
        b = BrowserBudget(1)
        held = b.try_acquire(S)
        waiting = asyncio.create_task(b.acquire(S))
        await asyncio.sleep(0.01)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert b.waiting == 0
        held.release()
        held.release()
        assert b.busy == 0 and b.busy_by(S) == 0
        assert b.try_acquire(S) is not None

    asyncio.run(scenario())
