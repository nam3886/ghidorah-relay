"""scripts/mutate.py fails on a stale anchor and on a surviving mutation, and always restores the file."""

import fcntl
import importlib.util
import os
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
def test_a_broken_mutation_still_restores_the_file_for_the_next_one(tmp_path, capsys):
    # the first marker breaks m.py (collection error, not red); the second must still find its anchor
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return (")')
    (tmp_path / "tests" / "test_z.py").write_text(textwrap.dedent("""\
        from relay_core.testing.mutation import mutation
        import m

        @mutation("tests/m.py", "return 1", "return 2")
        def test_z():
            assert m.f() == 1
        """), encoding="utf-8")
    assert mutate.run(tmp_path) == 1
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "test_z.py::test_z" in ln)
    assert line.startswith("red"), line
    assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == MODULE


@mutation(S, "return _run_locked(copy_tree(root, Path(tmp)), only)", "return _run_locked(root, only)")
def test_the_working_tree_is_never_mutated_even_mid_run(tmp_path):
    # the planted test records what the WORKING tree's m.py holds while its mutation is in flight
    seen = tmp_path / "seen.txt"
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")',
                 "open(%r, 'w').write(open(%r).read())\n    assert m.f() == 1"
                 % (str(seen), str(tmp_path / "tests" / "m.py")))
    assert mutate.run(tmp_path) == 0
    assert seen.read_text(encoding="utf-8") == MODULE


@mutation(S, "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "pass")
def test_a_second_run_is_refused_while_another_holds_the_lock(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")')
    fd = os.open(tmp_path / ".mutate.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert mutate.run(tmp_path) == 2
        assert "another run holds" in capsys.readouterr().err
        assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == MODULE
    finally:
        os.close(fd)
    assert mutate.run(tmp_path) == 0          # free again once the holder closes it


@mutation(S, "if not only or any(p in m[0] or p in m[1] for p in only)", "if True")
def test_only_selects_the_matching_markers(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")')
    (tmp_path / "tests" / "test_n.py").write_text(textwrap.dedent("""\
        from relay_core.testing.mutation import mutation
        import m

        @mutation("tests/m.py", "return 1", "return 3")
        def test_n():
            assert m.f() == 1
        """), encoding="utf-8")
    assert mutate.run(tmp_path, only=("test_n",)) == 0
    out = capsys.readouterr().out
    assert "1/1 killed" in out and "test_n.py::test_n" in out and "test_m.py" not in out


@mutation(S, "        if baselines[node_id]:\n", "        if False:\n")
def test_a_test_that_fails_unmutated_does_not_count_as_a_kill(tmp_path, capsys):
    # setup trouble (here: a plain failing test) would turn every mutation "red" for the wrong reason
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', "assert m.f() == 99")
    assert mutate.run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "baseline not green" in out and "0/1 killed" in out
