"""Each guard is green on the repo and red on a planted violation (contracts §14.6).

Every guard test carries `@mutation` markers: scripts/mutate.py breaks the guard in the named way and the
test must turn red, which proves the test checks the guard rather than just running it.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from relay_core.testing.mutation import mutation

import guards

G = "tests/guards.py"
M = "apps/relay/src/relay_cli/main.py"
CORE = "packages/relay-core/src/relay_core/"


def plant(root: Path, rel: str, src: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    return p


@pytest.mark.parametrize("check", [guards.import_violations, guards.route_violations, guards.launch_violations,
                                   guards.identifier_violations, guards.demo_violations,
                                   guards.core_provider_violations, guards.tx_task_violations])
def test_repo_is_clean(check):
    assert check() == []


@mutation(G, 'if me == "relay_core" and (top in PROVIDER_MODULES or top == "relay_browser"):', "if False:",
           expect="found and why in found[0], found")
@mutation(G, "if me in PROVIDER_MODULES and top in PROVIDER_MODULES and top != me:", "if False:",
           expect="found and why in found[0], found")
@mutation(G, 'if top in BROWSER_ONLY and me != "relay_browser":', "if False:",
           expect="found and why in found[0], found")
@mutation(G, "if is_pure(root, path) and (top in BROWSER_ONLY", "if False and (top in BROWSER_ONLY",
           expect="found and why in found[0], found")
@pytest.mark.parametrize("rel, src, why", [
    ("packages/relay-core/src/relay_core/x.py", "import relay_dola\n", "relay_core imports no provider"),
    ("packages/relay-core/src/relay_core/x.py", "from relay_browser import net\n", "relay_core imports no provider"),
    ("packages/provider-vibes/src/relay_vibes/x.py", "from relay_meta.spec import SPEC\n", "never import each other"),
    ("packages/provider-vibes/src/relay_vibes/engine/x.py", "import patchright\n", "patchright only inside"),
    ("packages/provider-dola/src/relay_dola/spec.py", "from relay_dola.engine import run\n", "a pure provider module"),
    ("packages/provider-dola/src/relay_dola/spec.py", "import relay_browser\n", "a pure provider module"),
])
def test_import_rule_catches(tmp_path, rel, src, why):
    plant(tmp_path, rel, src)
    found = guards.import_violations(tmp_path)
    assert found and why in found[0], found


@mutation(G, 'inner[0] != "engine" and inner != ("e2e.py",)', 'inner == ("spec.py",)',
           expect="any(\"a pure provider module\" in v for v in found), found")
@pytest.mark.parametrize("rel, src", [
    ("catalog.py", "from relay_dola.engine.run import go\n"),             # not spec.py, still pure
    ("classify.py", "from . import engine\n"),                             # relative import of the engine
    ("conflicts.py", "from .engine import run\n"),
    ("sub/helper.py", "from ..engine.run import go\n"),                   # one package deeper
    ("catalog.py", "import patchright\n"),
    ("__init__.py", "from relay_browser import net\n"),
])
def test_every_pure_provider_module_is_checked(tmp_path, rel, src):
    plant(tmp_path, "packages/provider-dola/src/relay_dola/" + rel, src)
    found = guards.import_violations(tmp_path)
    assert any("a pure provider module" in v for v in found), found


@mutation(G, 'inner[0] != "engine" and inner != ("e2e.py",)', "True",
           expect="imports relay_browser (a pure provider module")
def test_engine_and_e2e_may_import_the_browser(tmp_path):
    for rel in ("engine/run.py", "engine/__init__.py", "e2e.py"):
        plant(tmp_path, "packages/provider-dola/src/relay_dola/" + rel,
              "import relay_browser\nfrom relay_dola.engine import run\nfrom . import engine\n")
    assert guards.import_violations(tmp_path) == []


@mutation(G, "if attr in ROUTE_CALLS]", "if attr in ()]", expect="relay_browser/net.py:2 .route(")
def test_route_guard_catches(tmp_path):
    plant(tmp_path, "packages/relay-browser/src/relay_browser/net.py",
          "async def f(page):\n    await page.route('**/*', lambda r: r.continue_())\n")
    assert guards.route_violations(tmp_path) == ["packages/relay-browser/src/relay_browser/net.py:2 .route("]


@mutation(G, "if p.relative_to(root).as_posix() == SESSION_FILE:", "if True:",
           expect="engine/submit.py:2 .launch_persistent_context(")
@mutation(G, "if attr in LAUNCH_CALLS]", "if attr in ()]", expect="engine/submit.py:2 .launch_persistent_context(")
def test_launch_guard_catches_outside_session_only(tmp_path):
    src = "async def f(pw):\n    return await pw.chromium.launch_persistent_context('p')\n"
    plant(tmp_path, "packages/provider-dola/src/relay_dola/engine/submit.py", src)
    plant(tmp_path, guards.SESSION_FILE, src)
    assert guards.launch_violations(tmp_path) == [
        "packages/provider-dola/src/relay_dola/engine/submit.py:2 .launch_persistent_context("]


@mutation(G, 'if not re.search(r"\\b(chromium|firefox|webkit|patchright|playwright)\\b", src):', "if False:",
           expect="relay_core/db.py:2 .connect(")
def test_launch_guard_ignores_database_connect(tmp_path):
    plant(tmp_path, "packages/relay-core/src/relay_core/db.py", "def f(pool):\n    return pool.connect()\n")
    assert guards.launch_violations(tmp_path) == []


@mutation(G, 'n.name == "_demo"', 'n.name == "_demo_"', expect="guards.demo_violations(tmp_path)")
@mutation(G, '"")) == "_demo"', '"")) == "_demo_"', expect="guards.demo_violations(tmp_path)")
@pytest.mark.parametrize("src", [
    "def _demo():\n    assert True\n",
    "async def _demo():\n    pass\n",
    "def run():\n    _demo()\n",
    "def run(m):\n    m._demo()\n",
])
def test_demo_guard_catches(tmp_path, src):
    plant(tmp_path, "packages/provider-dola/src/relay_dola/x.py", src)
    assert guards.demo_violations(tmp_path)


def test_demo_guard_ignores_similar_names(tmp_path):
    plant(tmp_path, "packages/relay-core/src/relay_core/x.py", "def demo_mode():\n    return _demo_data\n")
    assert guards.demo_violations(tmp_path) == []


@mutation(G, "if literal and any(map(_is_provider_ref, operands)):", "if False:",
           expect="found and \"compares a provider name\" in found[0], found")
@mutation(G, 'return _is_str(n.slice) and n.slice.value in PROVIDER_VARS', "return False",
           expect="found and \"compares a provider name\" in found[0], found")
@mutation(G, "and n.args[1].value in PROVIDER_VARS)", "and False)",
           expect="found and \"compares a provider name\" in found[0], found")
@pytest.mark.parametrize("src", [
    "def f(provider):\n    return provider == 'fake'\n",
    "def f(job):\n    return 'x' != job.provider\n",
    "def f(provider_name):\n    return provider_name in ('a', 'b')\n",
    "def f(row):\n    return row['provider'] == 'fake'\n",
    "def f(job):\n    return getattr(job, 'provider') == 'fake'\n",
    "def f(job):\n    return job.provider in {'a'}\n",
    "def f(provider):\n    return provider == 'meta'\n",
    "def f(row):\n    return row['provider'] in ('meta', 'dola')\n",
    "def f(job):\n    return getattr(job, 'provider') == 'meta'\n",
])
def test_core_guard_catches_provider_comparison(tmp_path, src):
    plant(tmp_path, CORE + "jobs.py", src)
    found = guards.core_provider_violations(tmp_path)
    assert found and "compares a provider name" in found[0], found


@mutation(G, "elif isinstance(n, ast.Match) and _is_provider_ref(n.subject):", "elif False:",
           expect="found and \"matches a provider name\" in found[0], found")
def test_core_guard_catches_match_on_provider(tmp_path):
    plant(tmp_path, CORE + "jobs.py",
          "def f(provider):\n    match provider:\n        case 'fake':\n            return 1\n")
    found = guards.core_provider_violations(tmp_path)
    assert found and "matches a provider name" in found[0], found


@mutation(G, "elif _is_str(n) and id(n) not in docstrings and (", "elif False and id(n) not in docstrings and (",
           expect="any(\"names a provider\" in v for v in found), found")
@mutation(G, "n.value.startswith(CODE_PREFIXES) or any(", "any(",
           expect="any(\"names a provider\" in v for v in found), found")
@pytest.mark.parametrize("src", [
    "def f(spec):\n    return spec.name == 'dola'\n",
    "def f(p):\n    return {'dola': 1, 'vibes': 2}[p]\n",
    "CODE = 'meta.refused'\n",
    "NAME = 'relay_meta'\n",
    "def f(p):\n    return p.startswith('vibes')\n",
    "CODE = 'dola.likeness_refused'\n",
    "import importlib\nmod = importlib.import_module('relay_dola')\n",
    "NAME = 'relay_vibes.engine'\n",
    "def f(row):\n    return row['provider'] == 'dola'\n",
    "def f(p):\n    match p:\n        case 'vibes':\n            return 1\n",
])
def test_core_guard_catches_a_provider_name_or_code_anywhere(tmp_path, src):
    plant(tmp_path, CORE + "providers_x.py", src)
    found = guards.core_provider_violations(tmp_path)
    assert any("names a provider" in v for v in found), found


@mutation(G, "and id(n) not in docstrings and (", "and True and (", expect="names a provider: 'dola.x'")
def test_core_guard_allows_provider_names_in_docstrings(tmp_path):
    plant(tmp_path, CORE + "jobs.py",
          '"""Jobs of dola, vibes and meta."""\n\n\nclass A:\n    """dola.x"""\n\n    def f(self):\n'
          '        """meta"""\n')
    assert guards.core_provider_violations(tmp_path) == []


@mutation(G, 'LITERAL_TOKENS = (PROVIDER_NAMES - {"meta"})', "LITERAL_TOKENS = (PROVIDER_NAMES)",
           expect="names a provider: 'meta'")
def test_core_guard_allows_the_output_meta_field_name(tmp_path):
    plant(tmp_path, CORE + "fetcher.py",
          "def rows(o, row, cur):\n"
          "    cur.execute('UPDATE t SET meta = %(meta)s', {'meta': o.meta})\n"
          "    return {'ref': o.ref, 'meta': o.meta, 'seen': row['meta']}\n")
    assert guards.core_provider_violations(tmp_path) == []


@mutation(G, 'or rel.as_posix() == REGISTRY_FILE', "or False", expect="names a provider: 'relay_dola'")
def test_core_guard_exempts_only_the_registry(tmp_path):
    src = "PROVIDERS = ['relay_dola', 'dola', 'dola.x']\n\ndef f(provider):\n    return provider == 'dola'\n"
    plant(tmp_path, guards.REGISTRY_FILE, src)
    assert guards.core_provider_violations(tmp_path) == []
    plant(tmp_path, CORE + "other.py", src)
    found = guards.core_provider_violations(tmp_path)
    assert found and all("other.py" in v for v in found), found


@mutation(G, 'owner(root, p) != "relay_core"', 'owner(root, p) == ""',
           expect="relay_dola/spec.py:2 compares a provider name with a string literal")
def test_core_guard_leaves_providers_and_unrelated_code_alone(tmp_path):
    plant(tmp_path, "packages/provider-dola/src/relay_dola/spec.py",
          "def f(provider):\n    return provider == 'dola'\n")
    plant(tmp_path, CORE + "jobs.py",
          "def f(provider, other, x, out):\n"
          "    return provider == other or x == 'foo' or provider is None or out.meta\n")
    assert guards.core_provider_violations(tmp_path) == []


@mutation(G, "if name and (not name.isascii() or VIET_WORD.match(name)):", "if False:",
           expect="guards.identifier_violations(tmp_path)")
@mutation(G, "VIET_MARKS & set(c.value.lower())", "False", expect="guards.identifier_violations(tmp_path)")
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
          "(m.endswith('.engine') or m.endswith('.e2e') or m.split('.')[0] in ('relay_browser', 'patchright'))))")


def loaded_after(*argv: str) -> str:
    return run_py("from relay_cli import main; main.main(%r); " % list(argv) + LOADED)


@mutation(M, 'importlib.import_module(pkg + ".spec")', 'importlib.import_module(pkg + ".engine")',
           expect="ibes.engine']\" == '[]'")
@mutation(M, 'importlib.import_module("relay_core")', 'importlib.import_module("relay_browser")',
           expect="\"['relay_browser']\" == '[]'")
def test_serve_loads_no_engine_and_no_browser():
    assert loaded_after("serve") == "[]"


@mutation(M, "def load_engine(provider: str) -> ModuleType:\n",
          'def load_engine(provider: str) -> ModuleType:\n    importlib.import_module("relay_browser")\n',
           expect="loaded_after(\"worker\", \"--provider\", \"vibes\") == \"['relay_vibes.engine']\"")
def test_http_worker_loads_no_browser():
    assert loaded_after("worker", "--provider", "vibes") == "['relay_vibes.engine']"


@mutation(M, 'return importlib.import_module(PROVIDERS[provider] + ".engine")',
          'return importlib.import_module(PROVIDERS[provider] + ".spec")', expect="'[]' == \"['relay_brow")
def test_browser_worker_loads_only_its_own_engine():
    assert loaded_after("worker", "--provider", "dola") == "['relay_browser', 'relay_dola.engine']"


@mutation(M, "        load_e2e(args.provider)", "        load_engine(args.provider)",
           expect="loaded_after(\"e2e\", \"--provider\", provider) == expected")
@mutation(M, 'return importlib.import_module(PROVIDERS[provider] + ".e2e")',
          'return importlib.import_module(PROVIDERS[provider] + ".spec")',
           expect="loaded_after(\"e2e\", \"--provider\", provider) == expected")
@pytest.mark.parametrize("provider, expected", [
    ("dola", "['relay_browser', 'relay_dola.e2e', 'relay_dola.engine']"),
    ("vibes", "['relay_vibes.e2e', 'relay_vibes.engine']"),
])
def test_e2e_loads_its_provider_engine_and_e2e_module(provider, expected):
    assert loaded_after("e2e", "--provider", provider) == expected


@mutation(G, "            if not opens_tx:\n                continue\n",
          "            if True:\n                continue\n",
           expect="len(found) == 1 and \"inside a tx() block\" in found[0], found")
@pytest.mark.parametrize("spawn", [
    "asyncio.create_task(late(conn))",
    "await asyncio.gather(a(conn), b(conn))",
    "tg = asyncio.TaskGroup()",
    "asyncio.ensure_future(late(conn))",
])
def test_tx_block_guard_catches_a_task_started_inside_it(tmp_path, spawn):
    plant(tmp_path, CORE + "jobs.py",
          "async def f(pool):\n    async with tx(pool) as conn:\n        %s\n" % spawn)
    found = guards.tx_task_violations(tmp_path)
    assert len(found) == 1 and "inside a tx() block" in found[0], found


def test_tx_block_guard_allows_tasks_outside_the_block_and_inline_awaits_inside(tmp_path):
    plant(tmp_path, CORE + "jobs.py", """\
        async def f(pool, db):
            asyncio.create_task(other())
            async with db.tx(pool) as conn:
                await charge(conn)
            await asyncio.gather(a(), b())
        """)
    assert guards.tx_task_violations(tmp_path) == []


@mutation(G, 'if "tests" in p.relative_to(root).parts:', "if False:",
           expect="guards.tx_task_violations(tmp_path) == []")
def test_tx_block_guard_leaves_test_files_alone(tmp_path):
    plant(tmp_path, "packages/relay-core/tests/test_x.py",
          "async def f(pool):\n    async with tx(pool) as conn:\n        asyncio.create_task(late(conn))\n")
    assert guards.tx_task_violations(tmp_path) == []
