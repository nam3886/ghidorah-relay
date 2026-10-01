"""scripts/mutate.py fails on a stale anchor and on a surviving mutation, and always restores the file."""

import importlib.util
import textwrap
from pathlib import Path

import pytest

from relay_core.testing.mutation import mutation

from guards import REPO

_spec = importlib.util.spec_from_file_location("mutate", REPO / "scripts" / "mutate.py")
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)

S = "scripts/mutate.py"
MODULE = "def f():\n    return 1\n"


def planted_repo(root: Path, marker: str, test_body: str = "assert m.f() == 1") -> Path:
    (root / "tests").mkdir()
    (root / "tests" / "m.py").write_text(MODULE, encoding="utf-8")
    (root / "tests" / "test_m.py").write_text(textwrap.dedent("""\
        from relay_core.testing.mutation import mutation
        import m

        %s
        def test_f():
            %s
        """) % (marker, test_body), encoding="utf-8")
    return root


def test_a_mutation_that_turns_its_test_red_passes_and_the_file_is_restored(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")')
    assert mutate.run(tmp_path) == 0
    assert "1/1 killed" in capsys.readouterr().out
    assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == MODULE


@mutation(S, "if run.returncode == 1:", "if run.returncode in (0, 1):")
def test_a_surviving_mutation_fails_the_run(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', "assert True")
    assert mutate.run(tmp_path) == 1
    assert "mutation survived" in capsys.readouterr().out
    assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == MODULE


@mutation(S, "if hits != 1:", "if hits == 0:")
@pytest.mark.parametrize("original, why", [("return 9", "anchor not found"), ("return", "anchor ambiguous")])
def test_a_stale_or_ambiguous_anchor_fails_the_run(tmp_path, capsys, original, why):
    module = MODULE + "def g():\n    return 1\n"
    planted_repo(tmp_path, '@mutation("tests/m.py", "%s", "return 2")' % original)
    (tmp_path / "tests" / "m.py").write_text(module, encoding="utf-8")
    assert mutate.run(tmp_path) == 1
    assert why in capsys.readouterr().out
    assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == module


@mutation(S, "target.write_bytes(raw)", "pass")
def test_a_broken_mutation_still_restores_the_file(tmp_path):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return (")')   # collection error, not red
    assert mutate.run(tmp_path) == 1
    assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == MODULE
