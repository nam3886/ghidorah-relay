import os
import sys

import pytest

from relay_core.budget import BrowserBudget, Purpose

if sys.platform == "darwin":
    # The Mac tier must not raise windows. Elsewhere (the box) the variable stays as the environment has it, so a run
    # there is headful under Xvfb: the kit's default.
    os.environ.setdefault("RELAY_BROWSER_HEADLESS", "1")


@pytest.fixture
def token():
    """A live token from the core's real budget, for the submit purpose."""
    t = BrowserBudget(4).try_acquire(Purpose.SUBMIT)
    yield t
    t.release()
