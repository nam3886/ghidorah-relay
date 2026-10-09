"""pytest plugin for the browser container (scripts/mutate.py): a `tmp_path` with a short name.

Chrome's singleton socket lives under the session's temp directory and its path must stay under 108 bytes. pytest's own
`tmp_path` is /tmp/pytest-of-root/pytest-0/<test name>0 and, for two tests of relay-browser, that alone is too long.
The directory is made under SHORT_TMP_DIR (default "/", the container's root) and removed afterwards.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="t", dir=os.environ.get("SHORT_TMP_DIR", "/")))
    yield path
    shutil.rmtree(path, ignore_errors=True)
