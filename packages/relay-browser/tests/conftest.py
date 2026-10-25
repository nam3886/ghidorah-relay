import pytest

from relay_core.budget import BrowserBudget, Purpose


@pytest.fixture
def token():
    """A live token from the core's real budget, for the submit purpose."""
    t = BrowserBudget(4).try_acquire(Purpose.SUBMIT)
    yield t
    t.release()
