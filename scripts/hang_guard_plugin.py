"""pytest plugin for the browser tier (scripts/browser-tier.sh): a per-test time limit, so no hang holds a gate.

pytest-timeout is not a dependency, so this is the smallest equivalent: SIGALRM, armed around the setup, the call and
the teardown of each test with what is left of one budget (TEST_TIMEOUT_S, default 300 s). The handler raises in the
main thread, so the hung line is where the traceback ends; the test fails with its node id in the message and the run
goes on with the next test. A fixture or a teardown that hangs is covered too. A hang inside C code that never returns
to the interpreter is not interrupted (nothing in the tier blocks that way: Playwright waits in Python).
"""

import os
import signal
import time

import pytest

LIMIT_S = float(os.environ.get("TEST_TIMEOUT_S", "300"))


class HungTest(BaseException):
    """BaseException, so a broad `except Exception` in a fixture or in Playwright cannot swallow it."""


def _phase(item, label):
    deadline = getattr(item, "_timeout_deadline", None)
    if deadline is None:
        deadline = item._timeout_deadline = time.monotonic() + LIMIT_S
    left = max(deadline - time.monotonic(), 0.001)

    def expired(signum, frame):
        raise HungTest("%s: no result %.0f s after the test started (limit %.0f s), hung in its %s"
                          % (item.nodeid, LIMIT_S - (deadline - time.monotonic()), LIMIT_S, label))

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, left)
    return previous


def _wrap(label):
    @pytest.hookimpl(hookwrapper=True)
    def hook(item):
        previous = _phase(item, label)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    return hook


pytest_runtest_setup = _wrap("setup")
pytest_runtest_call = _wrap("call")
pytest_runtest_teardown = _wrap("teardown")
