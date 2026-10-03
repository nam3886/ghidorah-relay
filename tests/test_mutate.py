"""scripts/mutate.py fails on a stale anchor and on a surviving mutation, and always restores the file."""

import fcntl
import importlib.util
import os
import subprocess
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


@mutation(S, "if run.returncode == 1:", "if run.returncode in (0, 1):",
           expect="assert 'mutation survived' in 'FAILED   tests/test_m.py::test_f [tests")
def test_a_surviving_mutation_fails_the_run(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', "assert True")
    assert mutate.run(tmp_path) == 1
    assert "mutation survived" in capsys.readouterr().out
    assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == MODULE


@mutation(S, "if hits != 1:", "if hits == 0:",
           expect="assert 'anchor ambiguous' in 'FAILED   tests/test_m.py::test_f [tests/")
@pytest.mark.parametrize("original, why", [("return 9", "anchor not found"), ("return", "anchor ambiguous")])
def test_a_stale_or_ambiguous_anchor_fails_the_run(tmp_path, capsys, original, why):
    module = MODULE + "def g():\n    return 1\n"
    planted_repo(tmp_path, '@mutation("tests/m.py", "%s", "return 2")' % original)
    (tmp_path / "tests" / "m.py").write_text(module, encoding="utf-8")
    assert mutate.run(tmp_path) == 1
    assert why in capsys.readouterr().out
    assert (tmp_path / "tests" / "m.py").read_text(encoding="utf-8") == module


@mutation(S, "target.write_bytes(raw)", "pass",
           expect='FAILED   tests/test_z.py::test_z [tests/m.py -> return 2]')
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


@mutation(S, "return _run_locked(copy_tree(root, Path(tmp)), only)", "return _run_locked(root, only)",
           expect="assert 'def f():\\n    return 2\\n' == 'def f():\\n    return 1\\n'")
def test_the_working_tree_is_never_mutated_even_mid_run(tmp_path):
    # the planted test records what the WORKING tree's m.py holds while its mutation is in flight
    seen = tmp_path / "seen.txt"
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")',
                 "open(%r, 'w').write(open(%r).read())\n    assert m.f() == 1"
                 % (str(seen), str(tmp_path / "tests" / "m.py")))
    assert mutate.run(tmp_path) == 0
    assert seen.read_text(encoding="utf-8") == MODULE


@mutation(S, "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "pass",
           expect='assert 0 == 2')
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


@mutation(S, "if not only or any(p in m[0] or p in m[1] for p in only)", "if True",
           expect="assert ('1/1 killed' in 'red      tests/test_m.py::test_f [tests/m.py")
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


@mutation(S, "        if baselines[node_id]:\n", "        if False:\n",
           expect='assert 0 == 1')
def test_a_test_that_fails_unmutated_does_not_count_as_a_kill(tmp_path, capsys):
    # setup trouble (here: a plain failing test) would turn every mutation "red" for the wrong reason
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', "assert m.f() == 99")
    assert mutate.run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "baseline not green" in out and "0/1 killed" in out


@mutation(S, "if expect is not None and expect not in run.stdout:", "if False:", expect="assert 0 == 1")
def test_a_mutation_that_fails_its_test_the_wrong_way_does_not_count_as_killed(tmp_path, capsys):
    # the test turns red (assert m.f() == 1 fails), but not with the message the marker demands
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2", expect="no such message")')
    assert mutate.run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "not as expected" in out and "0/1 killed" in out


def test_a_mutation_that_fails_its_test_the_expected_way_counts(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2", expect="assert 2 == 1")')
    assert mutate.run(tmp_path) == 0
    assert "1/1 killed" in capsys.readouterr().out


@mutation(S, 'ln.startswith(("FAILED ", "ERROR ")) and node_id in ln', "True",
           expect='assert 0 == 1')
def test_a_red_run_in_which_the_marked_test_did_not_fail_is_not_a_kill(tmp_path, capsys):
    # the marked test passes, yet pytest exits 1 (a hook failure outside the test)
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', "assert True")
    (tmp_path / "tests" / "conftest.py").write_text(textwrap.dedent("""\
        def pytest_sessionfinish(session, exitstatus):
            if "return 2" in open(__file__.replace("conftest.py", "m.py")).read():
                session.exitstatus = 1
        """), encoding="utf-8")
    assert mutate.run(tmp_path) == 1
    assert "did not fail" in capsys.readouterr().out


def test_expect_must_be_a_string_constant(tmp_path):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2", expect=3)')
    with pytest.raises(SystemExit):
        mutate.collect(tmp_path)


@mutation(S, '(replacement.strip().splitlines() or [""])[0]', "replacement.strip().splitlines()[0]",
           expect='IndexError: list index out of range')
def test_an_empty_replacement_deletes_the_anchor_and_is_reported_as_removed(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "    return 1\\n", "")')
    (tmp_path / "tests" / "m.py").write_text("def f():\n    pass\n    return 1\n", encoding="utf-8")
    assert mutate.run(tmp_path) == 0
    assert "(removed)" in capsys.readouterr().out


def test_an_expression_as_an_argument_is_refused_naming_the_file_and_line(tmp_path):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 1".replace("1", "2"))')
    with pytest.raises(SystemExit) as refused:
        mutate.collect(tmp_path)
    assert "tests/test_m.py:4" in str(refused.value)


@mutation(S, "if m[4] is None]", "if m[4] is None or True]", expect="assert ['tests/")
def test_every_marker_in_the_repo_names_the_failure_it_must_cause():
    assert mutate.unmarked() == [], "a @mutation without expect= counts a red for any failure, even a helper timeout"


@mutation(S, "if m[4] is None]", "if False]", expect="assert [] == ['tests/test_m.py::test_f']")
def test_a_marker_without_expect_is_listed(tmp_path):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")')
    assert mutate.unmarked(tmp_path) == ["tests/test_m.py::test_f"]


VERDICT = "scripts/check-verdict.sh"
CHECK = "scripts/check.sh"
NARROW = "CHECK: PARTIAL (not a commit gate: pytest arguments narrow the run)"


def verdict(full, nargs, **env):
    run = subprocess.run(["bash", str(REPO / VERDICT), str(full), str(nargs)], capture_output=True, text=True,
                         env={"PATH": os.environ["PATH"], **env})
    return run.returncode, run.stdout.splitlines()


@mutation(VERDICT, 'if [ "$seeds" -lt 200 ]; then', 'if [ "$seeds" -lt 0 ]; then',
          expect="At index 1 diff: [] != ['soak seeds: 199 (the gate needs 200)']")
@mutation(VERDICT, 'if [ -n "${MUTATE_ONLY:-}" ]; then', "if false; then",
          expect="At index 1 diff: [] != ['mutate only: test_x']")
@mutation(VERDICT, 'if [ "$nargs" != 0 ]; then', "if false; then",
          expect="At index 1 diff: [] != ['pytest arguments narrow the run']")
@mutation(VERDICT, "'' | *[!0-9]*) echo", "'') echo", expect="assert 0 == 2")
@mutation(VERDICT, 'if [ "$full" = 1 ]; then', "if true; then",
          expect="At index 1 diff: ['mutate only: test_x', 'soak seeds: 0 (the gate needs 200)'] != []")
def test_the_verdict_names_what_a_run_left_out():
    assert verdict(1, 0) == (0, [])                                       # a whole run: nothing left out
    assert verdict(1, 0, SOAK_SEEDS="200") == (0, [])
    assert verdict(1, 0, SOAK_SEEDS="199") == (0, ["soak seeds: 199 (the gate needs 200)"])
    assert verdict(1, 0, MUTATE_ONLY="test_x") == (0, ["mutate only: test_x"])
    assert verdict(1, 2) == (0, ["pytest arguments narrow the run"])
    assert verdict(1, 0, SOAK_SEEDS="abc")[0] == 2
    # a plain run never mutates or soaks, so those settings do not make it partial
    assert verdict(0, 0, MUTATE_ONLY="test_x", SOAK_SEEDS="0") == (0, [])


def run_check(tmp_path, *args, **env):
    """scripts/check.sh with docker, uv and uvx replaced by no-ops: only its last line is under test."""
    stubs = tmp_path / "bin"
    stubs.mkdir(parents=True)
    for name in ("docker", "uv", "uvx"):
        (stubs / name).write_text('#!/bin/sh\ncase "$*" in *" port "*) echo 0.0.0.0:5555;; esac\n', encoding="utf-8")
        (stubs / name).chmod(0o755)
    run = subprocess.run(["bash", str(REPO / CHECK), *args], capture_output=True, text=True,
                         env={"PATH": "%s:%s" % (stubs, os.environ["PATH"]), **env})
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()[-1]


@mutation(CHECK, 'if [ -n "$verdict" ]; then', "if false; then",
          expect="- CHECK: PARTIAL (not a commit gate: pytest arguments narrow the run)")
@mutation(CHECK, 'verdict=$(scripts/check-verdict.sh "$full" "$nargs")', 'verdict=""',
          expect="- CHECK: PARTIAL (not a commit gate: pytest arguments narrow the run)")
def test_check_ends_green_only_when_nothing_was_left_out(tmp_path):
    assert run_check(tmp_path / "a", "--full", "--local") == "CHECK: GREEN"
    assert run_check(tmp_path / "b", "--full", "--local", "some_test.py") == NARROW
    assert run_check(tmp_path / "c", "--full", "--local", MUTATE_ONLY="test_x", SOAK_SEEDS="0") \
        == "CHECK: PARTIAL (not a commit gate: mutate only: test_x;soak seeds: 0 (the gate needs 200))"
