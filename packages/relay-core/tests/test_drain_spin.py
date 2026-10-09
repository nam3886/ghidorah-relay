"""The stop drains (`CommandLoop.wait`, `JobLoop.idle`, `Checks.wait`) end for a set that already holds finished tasks.

The sets are emptied by done-callbacks, which run only when the event loop gets control, and `gather` of tasks that are
all finished returns without giving it. A drain that starts in the pass in which a task ended therefore saw the same
finished task forever and spun without ever yielding. Nothing inside the event loop can time such a spin out, so the
guard here is a timer signal that raises in the main thread."""

import asyncio
import contextlib
import signal
from types import SimpleNamespace

from relay_core.jobs.checks import Checks
from relay_core.jobs.commands import CommandLoop
from relay_core.jobs.poller import JobLoop
from relay_core.testing.mutation import mutation

COMMANDS = "packages/relay-core/src/relay_core/jobs/commands.py"
POLLER = "packages/relay-core/src/relay_core/jobs/poller.py"
CHECKS = "packages/relay-core/src/relay_core/jobs/checks.py"
LIMIT_S = 10


@contextlib.contextmanager
def starvation_guard(message: str):
    """Raises AssertionError(message) in the main thread after LIMIT_S, also inside a loop that never yields."""
    def fire(signum, frame):
        raise AssertionError(message)

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, LIMIT_S)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


async def finished_task() -> asyncio.Task:
    """A task that has ended, in the pass in which it is used: no done-callback has had the loop yet."""
    async def nothing():
        return None

    task = asyncio.ensure_future(nothing())
    await asyncio.sleep(0)
    assert task.done()
    return task


def command_loop() -> CommandLoop:
    return CommandLoop(None, checks=SimpleNamespace(wait=_nothing))      # type: ignore[arg-type]


async def _nothing() -> None:
    return None


def job_loop() -> JobLoop:
    return JobLoop(None, SimpleNamespace(caps=None), None, None, owner="t", load_account=None)    # type: ignore[arg-type]


def run_guarded(coro, message: str) -> None:
    with starvation_guard(message):
        asyncio.run(coro)


@mutation(COMMANDS, "            self._tasks.difference_update(waited)", "            pass",
          expect="the command drain spun on a finished task")
def test_the_command_drain_ends_when_a_finished_task_is_still_in_the_set():
    async def go():
        loop = command_loop()
        loop._tasks.add(await finished_task())
        await loop.wait()
        assert not loop._tasks

    run_guarded(go(), "the command drain spun on a finished task")


@mutation(POLLER, "            self._inflight.difference_update(inflight)\n", "            pass\n",
          expect="the job-loop drain spun on a finished in-flight task")
def test_the_job_loop_drain_ends_when_a_finished_task_is_still_in_flight():
    async def go():
        loop = job_loop()
        loop._inflight.add(await finished_task())
        await loop.idle()
        assert not loop._inflight

    run_guarded(go(), "the job-loop drain spun on a finished in-flight task")


@mutation(POLLER, "            self._applying.difference_update(applying)\n", "            pass\n",
          expect="the job-loop drain spun on a finished shielded write")
def test_the_job_loop_drain_ends_when_a_finished_write_is_still_applying():
    async def go():
        loop = job_loop()
        loop._applying.add(await finished_task())
        await loop.idle()
        assert not loop._applying

    run_guarded(go(), "the job-loop drain spun on a finished shielded write")


@mutation(CHECKS, "if self._tasks.get(account_id) is task:", "if False:",
          expect="the checks drain spun on a task that ended without running")
def test_the_checks_drain_ends_for_a_task_cancelled_before_its_first_step():
    """Such a task never ran its own `finally`, so it never removed itself from the dict."""
    async def go():
        checks = Checks(None)       # type: ignore[arg-type]

        async def never_runs():
            raise AssertionError("it must not start")

        task = asyncio.ensure_future(never_runs())
        task.cancel()
        checks._tasks["a"] = task       # type: ignore[index]
        await asyncio.gather(task, return_exceptions=True)
        await checks.wait()
        assert not checks._tasks

    run_guarded(go(), "the checks drain spun on a task that ended without running")


async def late_task_is_waited_for(drain, members: set) -> None:
    done = []

    async def second():
        await asyncio.sleep(0.05)
        done.append("second")

    async def first():
        await asyncio.sleep(0.05)
        late = asyncio.ensure_future(second())
        members.add(late)
        late.add_done_callback(members.discard)

    early = asyncio.ensure_future(first())
    members.add(early)
    early.add_done_callback(members.discard)
    await drain()
    assert done == ["second"], "%s did not wait for a task added during the drain" % drain.__qualname__


def test_a_task_added_while_the_drains_wait_is_waited_for_too():
    async def go():
        commands, jobs = command_loop(), job_loop()
        await late_task_is_waited_for(commands.wait, commands._tasks)
        await late_task_is_waited_for(jobs.idle, jobs._inflight)

    run_guarded(go(), "a drain did not end")
