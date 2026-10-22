"""The relay-core database fixtures (`db`, and the invariant check after each test that used it), for the meta tests
that run account effects through the real core. Loaded the way tests/admin_ui/conftest.py loads them."""

import importlib.util
import sys
from pathlib import Path

CORE_TESTS = Path(__file__).resolve().parents[2] / "relay-core" / "tests"
sys.path.insert(0, str(CORE_TESTS))         # the core conftest imports its own helpers (pgtemplate)

# The core fixtures live in a conftest of another directory; load that file once under its own name.
_name = "relay_core_tests_conftest"
_core = sys.modules.get(_name)
if _core is None:
    _spec = importlib.util.spec_from_file_location(_name, CORE_TESTS / "conftest.py")
    _core = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _core
    _spec.loader.exec_module(_core)
_migrated = _core._migrated
db = _core.db
_after_each_db_test = _core._after_each_db_test
pytest_configure = _core.pytest_configure      # registers the marker the invariant checker reads
