"""Each guard is green on the repo and red on a planted violation (contracts §14.6)."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import guards


def plant(root: Path, rel: str, src: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    return p


@pytest.mark.parametrize("check", [guards.import_violations, guards.route_violations,
                                   guards.launch_violations, guards.identifier_violations])
def test_repo_is_clean(check):
    assert check() == []


@pytest.mark.parametrize("rel, src, why", [
    ("packages/relay-core/src/relay_core/x.py", "import relay_dola\n", "relay_core imports no provider"),
    ("packages/relay-core/src/relay_core/x.py", "from relay_browser import net\n", "relay_core imports no provider"),
    ("packages/provider-vibes/src/relay_vibes/x.py", "from relay_meta.spec import SPEC\n", "never import each other"),
    ("packages/provider-vibes/src/relay_vibes/engine/x.py", "import patchright\n", "patchright only inside"),
    ("packages/provider-dola/src/relay_dola/spec.py", "from relay_dola.engine import run\n", "a spec is pure"),
    ("packages/provider-dola/src/relay_dola/spec.py", "import relay_browser\n", "a spec is pure"),
])
def test_import_rule_catches(tmp_path, rel, src, why):
    plant(tmp_path, rel, src)
    found = guards.import_violations(tmp_path)
    assert found and why in found[0], found


def test_route_guard_catches(tmp_path):
    plant(tmp_path, "packages/relay-browser/src/relay_browser/net.py",
          "async def f(page):\n    await page.route('**/*', lambda r: r.continue_())\n")
    assert guards.route_violations(tmp_path) == ["packages/relay-browser/src/relay_browser/net.py:2 .route("]


def test_launch_guard_catches_outside_session_only(tmp_path):
    src = "async def f(pw):\n    return await pw.chromium.launch_persistent_context('p')\n"
    plant(tmp_path, "packages/provider-dola/src/relay_dola/engine/submit.py", src)
    plant(tmp_path, guards.SESSION_FILE, src)
    assert guards.launch_violations(tmp_path) == [
        "packages/provider-dola/src/relay_dola/engine/submit.py:2 .launch_persistent_context("]


def test_launch_guard_ignores_database_connect(tmp_path):
    plant(tmp_path, "packages/relay-core/src/relay_core/db.py", "def f(pool):\n    return pool.connect()\n")
    assert guards.launch_violations(tmp_path) == []


@pytest.mark.parametrize("src", [
    "def hoan(x):\n    return x\n",
    "số_dư = 1\n",
    "def f():\n    raise ValueError('không đủ tiền')\n",
])
def test_language_guard_catches(tmp_path, src):
    plant(tmp_path, "packages/relay-core/src/relay_core/x.py", src)
    (tmp_path / "tests").mkdir()
    assert guards.identifier_violations(tmp_path)


def test_language_guard_allows_vietnamese_comments(tmp_path):
    plant(tmp_path, "packages/relay-core/src/relay_core/x.py",
          "# hoàn tiền đúng một lần\ndef refund_once():\n    '''Hoàn tiền.'''\n    return 1\n")
    (tmp_path / "tests").mkdir()
    assert guards.identifier_violations(tmp_path) == []


def run_py(code: str) -> str:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()


LOADED = ("import sys; print(sorted(m for m in sys.modules if m.split('.')[0] in "
          "('relay_browser', 'patchright', 'relay_dola', 'relay_vibes', 'relay_meta') and "
          "(m.endswith('.engine') or m.split('.')[0] in ('relay_browser', 'patchright'))))")


def test_serve_loads_no_engine_and_no_browser():
    assert run_py("from relay_cli import main; main.load_specs(); import relay_core; " + LOADED) == "[]"


def test_http_worker_loads_no_browser():
    assert run_py("from relay_cli import main; main.load_engine('vibes'); " + LOADED) == "['relay_vibes.engine']"


def test_browser_worker_loads_only_its_own_engine():
    assert run_py("from relay_cli import main; main.load_engine('dola'); " + LOADED) == \
        "['relay_browser', 'relay_dola.engine']"
