"""scripts/mutate.py fails on a stale anchor and on a surviving mutation, and always restores the file."""

import fcntl
import importlib.util
import os
import shutil
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


@mutation(S, "return _run_locked(copy_tree(root, Path(tmp)), only, jobs)", "return _run_locked(root, only, jobs)",
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


def run_check_raw(tmp_path, *args, no_browser_tests=False, **env):
    """scripts/check.sh with docker, uv and uvx replaced by no-ops (uv exits 5, "nothing selected", for the browser
    tier when `no_browser_tests`): only its last line and its exit code are under test."""
    stubs = tmp_path / "bin"
    stubs.mkdir(parents=True)
    script = '#!/bin/sh\ncase "$*" in *" port "*) echo 0.0.0.0:5555;; esac\n'
    if no_browser_tests:
        script += 'case "$*" in *"browser and not box"*) exit 5;; esac\n'
    for name in ("docker", "uv", "uvx"):
        (stubs / name).write_text(script, encoding="utf-8")
        (stubs / name).chmod(0o755)
    return subprocess.run(["bash", str(REPO / CHECK), *args], capture_output=True, text=True,
                          env={"PATH": "%s:%s" % (stubs, os.environ["PATH"]), **env})


def run_check(tmp_path, *args, **env):
    run = run_check_raw(tmp_path, *args, **env)
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()[-1]


@mutation(CHECK, 'if [ "$rc" = 5 ] && [ "$nargs" = 0 ]; then', 'if false; then',
          expect="a whole gate ended GREEN with no browser test")
@mutation(CHECK, 'elif [ "$rc" = 5 ]; then', 'elif false; then', expect="a narrowed run must only note")
def test_a_whole_gate_with_no_browser_test_selected_fails_and_a_narrowed_run_only_notes_it(tmp_path):
    whole = run_check_raw(tmp_path / "a", "--full", "--local", no_browser_tests=True)
    assert whole.returncode != 0 and "CHECK: GREEN" not in whole.stdout, \
        "a whole gate ended GREEN with no browser test: %r" % whole.stdout[-200:]
    assert "no browser test was selected" in whole.stderr
    narrow = run_check_raw(tmp_path / "b", "--full", "--local", "some_test.py", no_browser_tests=True)
    assert narrow.returncode == 0, "a narrowed run must only note that no browser test was selected: %r" % narrow.stderr
    assert "browser tier: no browser test selected" in narrow.stdout and narrow.stdout.splitlines()[-1] == NARROW


@mutation(CHECK, 'if [ -n "$verdict" ]; then', "if false; then",
          expect="- CHECK: PARTIAL (not a commit gate: pytest arguments narrow the run)")
@mutation(CHECK, 'verdict=$(scripts/check-verdict.sh "$full" "$nargs")', 'verdict=""',
          expect="- CHECK: PARTIAL (not a commit gate: pytest arguments narrow the run)")
def test_check_ends_green_only_when_nothing_was_left_out(tmp_path):
    assert run_check(tmp_path / "a", "--full", "--local") == "CHECK: GREEN"
    assert run_check(tmp_path / "b", "--full", "--local", "some_test.py") == NARROW
    assert run_check(tmp_path / "c", "--full", "--local", MUTATE_ONLY="test_x", SOAK_SEEDS="0") \
        == "CHECK: PARTIAL (not a commit gate: mutate only: test_x;soak seeds: 0 (the gate needs 200))"


# ---- a baseline that fails once is run again; a mutant never is ---------------------------------------------------
def counted(tmp_path: Path, extra: str = "") -> str:
    """A test body that counts its own runs in a file and then checks m.f(); `extra` is a line run after the count."""
    counter = tmp_path / "runs.txt"
    path = str(counter)
    return ("import os\n    n = len(open(%r).read() if os.path.exists(%r) else '') + 1\n"
            "    open(%r, 'a').write('x')\n    %s\n    assert m.f() == 1" % (path, path, path, extra))


@mutation(S, "    if not second:\n        return \"\", first", "    if False:\n        return \"\", first",
          expect="a flaky baseline failed the run")
def test_a_baseline_that_fails_once_and_then_passes_is_flaky_and_its_mutation_still_runs(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', counted(tmp_path, "assert n >= 2"))
    assert mutate.run(tmp_path) == 0, "a flaky baseline failed the run"
    out = capsys.readouterr().out
    first_failure = "assert 1 >= 2"
    assert "FLAKY    tests/test_m.py::test_f [tests/m.py -> return 2]" in out and first_failure in out, out
    assert "1/1 killed, 0 failed, 1 flaky baseline(s)" in out and "red      tests/test_m.py::test_f" in out, out


@mutation(S, "    return \"%s; second run: %s\" % (first, second), \"\"", "    return first, \"\"",
          expect="the second failure line was not printed")
@mutation(S, "    if not second:\n        return \"\", first", "    if True:\n        return \"\", first",
          expect="a baseline that fails twice did not fail the run")
def test_a_baseline_that_fails_twice_is_failed_and_prints_both_failure_lines(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', counted(tmp_path, "assert n == 99"))
    assert mutate.run(tmp_path) == 1, "a baseline that fails twice did not fail the run"
    out = capsys.readouterr().out
    assert "baseline not green" in out and "second run: baseline not green" in out, \
        "the second failure line was not printed: " + out
    assert "0/1 killed, 1 failed, 0 flaky baseline(s)" in out and "FLAKY" not in out, out
    assert (tmp_path / "runs.txt").read_text() == "xx", "a baseline that fails runs exactly twice"


@mutation(S, "        else:\n            ok, msg = mutate_once(root, node_id, file, original, replacement, expect)",
          "        else:\n            ok, msg = mutate_once(root, node_id, file, original, replacement, expect)\n"
          "            if not ok:\n"
          "                ok, msg = mutate_once(root, node_id, file, original, replacement, expect)",
          expect="a surviving mutation was run again")
def test_a_surviving_mutation_is_run_once_and_still_fails_the_run(tmp_path, capsys):
    planted_repo(tmp_path, '@mutation("tests/m.py", "return 1", "return 2")', counted(tmp_path, "assert True").replace(
        "assert m.f() == 1", "assert True"))
    assert mutate.run(tmp_path) == 1
    assert "mutation survived" in capsys.readouterr().out
    assert (tmp_path / "runs.txt").read_text() == "xx", "a surviving mutation was run again"


@mutation(S, "        if node_id not in baselines:\n", "        if True:\n",
          expect="a green baseline ran more than once")
def test_a_green_baseline_runs_once_however_many_markers_the_test_has(tmp_path, capsys):
    markers = '@mutation("tests/m.py", "return 1", "return 2")\n@mutation("tests/m.py", "def f", "def g")'
    planted_repo(tmp_path, markers, counted(tmp_path))
    assert mutate.run(tmp_path) == 0
    assert "2/2 killed, 0 failed, 0 flaky baseline(s)" in capsys.readouterr().out
    assert (tmp_path / "runs.txt").read_text() == "xxx", "a green baseline ran more than once (1 baseline + 2 mutants)"


FLAKY_LOG = ("red      tests/test_a.py::test_a [x.py -> y]\n      E boom\n"
             "FLAKY    tests/test_b.py::test_b [x.py -> y]\n      E   assert 1 >= 2\n"
             "mutate: 2/2 killed, 0 failed, 1 flaky baseline(s)\n")


FLAKY_SH = "scripts/check-flaky.sh"


def flaky(log):
    return subprocess.run(["bash", str(REPO / FLAKY_SH), str(log)], capture_output=True, text=True)


@mutation(FLAKY_SH, "grep -A1 '^FLAKY ' \"$log\" | grep -v '^--$'", "true", expect="the flaky lines were not repeated")
def test_check_flaky_repeats_the_flaky_lines_and_prints_nothing_without_them(tmp_path):
    log = tmp_path / "mutate.log"
    log.write_text(FLAKY_LOG, encoding="utf-8")
    run = flaky(log)
    assert run.returncode == 0 and "FLAKY    tests/test_b.py::test_b" in run.stdout and "assert 1 >= 2" in run.stdout, \
        "the flaky lines were not repeated: %r" % run.stdout
    assert "test_a" not in run.stdout
    log.write_text("red      tests/test_a.py::test_a [x.py -> y]\nmutate: 1/1 killed, 0 failed, 0 flaky baseline(s)\n")
    quiet = flaky(log)
    assert (quiet.returncode, quiet.stdout) == (0, "")


@mutation(FLAKY_SH, 'if [ ! -f "$log" ] || [ ! -r "$log" ]; then', "if false; then",
          expect="a log that cannot be read was not refused")
def test_check_flaky_refuses_a_missing_an_unreadable_and_a_non_file_log(tmp_path):
    unreadable = tmp_path / "unreadable.log"
    unreadable.write_text(FLAKY_LOG, encoding="utf-8")
    unreadable.chmod(0)
    folder = tmp_path / "folder"
    folder.mkdir()
    try:
        for what, path in (("missing", tmp_path / "none.log"), ("unreadable", unreadable), ("a directory", folder)):
            run = flaky(path)
            assert run.returncode == 2 and "cannot read the mutation log" in run.stderr and run.stdout == "", \
                "a log that cannot be read was not refused (%s): %d %r" % (what, run.returncode, run.stderr)
        no_path = subprocess.run(["bash", str(REPO / FLAKY_SH)], capture_output=True, text=True)
        assert no_path.returncode == 2 and "cannot read the mutation log" in no_path.stderr
    finally:
        unreadable.chmod(0o600)


def test_check_sh_feeds_the_mutation_output_to_check_flaky_before_its_verdict():
    text = (REPO / CHECK).read_text(encoding="utf-8")
    call = text.index('scripts/check-flaky.sh "$mutlog"')
    assert 'scripts/check-mutate.sh "$mutlog"' in text and call < text.index('echo "CHECK: PARTIAL')


MUTATE_STAGE = "scripts/check-mutate.sh"


def stage(tmp_path, exit_code):
    fake = tmp_path / "fake-mutate.sh"
    fake.write_text("#!/usr/bin/env bash\necho 'FAILED   tests/test_a.py::test_a [x.py -> y]'\nexit %d\n" % exit_code)
    fake.chmod(0o755)
    log = tmp_path / "mutate.log"
    run = subprocess.run(["bash", str(REPO / MUTATE_STAGE), str(log)], capture_output=True, text=True,
                         env={"PATH": os.environ["PATH"], "MUTATE_CMD": str(fake)})
    return run, log


@mutation(MUTATE_STAGE, "set -euo pipefail", "set -eu", expect="a red mutation stage ended with status 0")
@mutation(MUTATE_STAGE, '"$@" | tee "$log"', '"$@" | tee "$log" || true',
          expect="a red mutation stage ended with status 0")
def test_a_red_mutation_stage_stops_the_gate_and_a_green_one_does_not(tmp_path):
    red, log = stage(tmp_path, 1)
    assert red.returncode == 1, "a red mutation stage ended with status %d" % red.returncode
    assert "FAILED   tests/test_a.py::test_a" in red.stdout and "FAILED" in log.read_text()
    green, _ = stage(tmp_path, 0)
    assert green.returncode == 0


@mutation(CHECK, 'scripts/check-mutate.sh "$mutlog"', 'scripts/check-mutate.sh "$mutlog" || true',
          expect="check.sh does not stop on the status of the mutation stage")
def test_check_sh_runs_the_mutation_stage_as_a_plain_command_under_errexit_and_pipefail():
    text = (REPO / CHECK).read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "scripts/check-mutate.sh" in ln and not ln.strip().startswith("#"))
    stops = "||" not in line and "set -euo pipefail" in text
    assert stops, "check.sh does not stop on the status of the mutation stage"


def test_check_sh_calls_the_flaky_check_only_when_the_mutation_stage_ran():
    text = (REPO / CHECK).read_text(encoding="utf-8")
    start = text.index('scripts/check-flaky.sh "$mutlog"')
    assert text[:start].rstrip().endswith('if [ "$full" = 1 ]; then      # the last screen of a gate shows the '
                                          'baselines that needed a second run'), "a /dev/null log would be refused"


@mutation(CHECK, "env -u MUTATE_CMD scripts/check-mutate.sh", "scripts/check-mutate.sh",
          expect="an inherited MUTATE_CMD reached the mutation stage")
def test_a_mutate_cmd_inherited_by_check_sh_does_not_replace_the_mutation_stage(tmp_path):
    """Runs the very line check.sh uses for the stage, with a MUTATE_CMD from the caller that would pass, and `uv`
    replaced by a stub that stands for the real command: the stage must run the stub and its status must win."""
    line = next(ln.strip() for ln in (REPO / CHECK).read_text(encoding="utf-8").splitlines()
                if "scripts/check-mutate.sh" in ln and not ln.strip().startswith("#"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/usr/bin/env bash\necho REAL-COMMAND-RAN\nexit 1\n")
    uv.chmod(0o755)
    green = tmp_path / "green.sh"
    green.write_text("#!/usr/bin/env bash\necho CALLER-COMMAND-RAN\nexit 0\n")
    green.chmod(0o755)
    run = subprocess.run(["bash", "-c", "mutlog=%s; only=(); %s" % (tmp_path / "mutate.log", line)], cwd=REPO,
                         capture_output=True, text=True,
                         env={"PATH": "%s:%s" % (bin_dir, os.environ["PATH"]), "MUTATE_CMD": str(green)})
    assert "REAL-COMMAND-RAN" in run.stdout and "CALLER-COMMAND-RAN" not in run.stdout and run.returncode == 1, \
        "an inherited MUTATE_CMD reached the mutation stage: %d %r" % (run.returncode, run.stdout)


# ---- --jobs N: the same result as the serial run, from private copies working at once ------------------------------
MODULE3 = "def f():\n    return 1\n\n\ndef g():\n    return 2\n\n\ndef h():\n    return 3\n"


def planted_trio(root: Path, extra_test: str = "") -> Path:
    """tests a (one marker), b (two), c (one), in that file order; `extra_test` is more test source."""
    (root / "tests").mkdir()
    (root / "tests" / "m.py").write_text(MODULE3, encoding="utf-8")
    (root / "tests" / "test_m.py").write_text(textwrap.dedent("""\
        import os
        import time
        from relay_core.testing.mutation import mutation
        import m


        def note(text):
            with open(os.environ.get("NOTES", os.devnull), "a") as f:
                f.write(text + "\\n")


        @mutation("tests/m.py", "return 1", "return 0")
        def test_a():
            time.sleep(0.2)
            note("end a")
            assert m.f() == 1


        @mutation("tests/m.py", "return 2", "return 0")
        @mutation("tests/m.py", "def g", "def gg")
        def test_b():
            time.sleep(0.2)
            note("end b")
            assert m.g() == 2


        @mutation("tests/m.py", "return 3", "return 0")
        def test_c():
            time.sleep(0.2)
            note("end c")
            assert m.h() == 3
        """) + textwrap.dedent(extra_test), encoding="utf-8")
    return root


def output_of(capsys, root: Path, jobs: int) -> tuple[int, str]:
    code = mutate.run(root, jobs=jobs)
    return code, capsys.readouterr().out


@mutation(S, "for node_id, file, _original, replacement, _expect in marks:",
          "for node_id, file, _original, replacement, _expect in sorted(marks, key=lambda m: m[0], reverse=True):",
          expect="the parallel output is not in marker order")
def test_a_parallel_run_prints_what_the_serial_run_prints_in_the_same_order(tmp_path, capsys):
    planted_trio(tmp_path)
    serial = output_of(capsys, tmp_path, 1)
    parallel = output_of(capsys, tmp_path, 3)
    assert serial[0] == 0 and "4/4 killed, 0 failed, 0 flaky baseline(s)" in serial[1], serial
    assert parallel == serial, "the parallel output is not in marker order: " + parallel[1]


@mutation(S, "key=lambda kv: -len(kv[1])", "key=lambda kv: len(kv[1])", expect="the lightest test was first")
def test_tests_are_scheduled_heaviest_first_and_ties_keep_the_marker_order(tmp_path):
    planted_trio(tmp_path)
    ids = [node_id.split("::")[1] for node_id, _ in mutate._units(mutate.collect(tmp_path))]
    assert ids == ["test_b", "test_a", "test_c"], "the lightest test was first: %s" % ids


FLAKY_TEST = """

def test_x():
    # fails in its first run, passes in every later one, and notes when it runs
    path = os.environ["NOTES"] + ".x"
    first = not os.path.exists(path)
    open(path, "a").write("x")
    if not first:
        note("run x")
    assert not first
    assert m.f() == 1
"""


@mutation(S, "                first = baseline(copy, node_id)\n",
          "                first = baseline_checked(copy, node_id)[0]\n",
          expect="the failed baseline was repeated inside the queue")
@mutation(S, "            if second:\n", "            if not second:\n",
          expect="a recovered baseline was counted as failed")
def test_a_failed_baseline_is_repeated_alone_after_the_queue_is_empty_and_a_green_second_run_is_flaky(
        tmp_path, capsys, monkeypatch):
    planted_trio(tmp_path, FLAKY_TEST.replace("def test_x", '@mutation("tests/m.py", "def f", "def ff")\ndef test_x'))
    notes = tmp_path / "notes.txt"
    monkeypatch.setenv("NOTES", str(notes))
    code, out = output_of(capsys, tmp_path, 4)
    lines = notes.read_text().splitlines()
    # the three other tests had all finished before the repeat of the failed baseline began
    last_end = max(i for i, ln in enumerate(lines) if ln.startswith("end "))
    assert lines.index("run x") > last_end, "the failed baseline was repeated inside the queue: %s" % lines
    assert code == 0 and "5/5 killed, 0 failed, 1 flaky baseline(s)" in out, \
        "a recovered baseline was counted as failed: " + out
    assert "FLAKY    tests/test_m.py::test_x" in out, out


@mutation(S, "                if first:\n                    with lock:",
          "                if False:\n                    with lock:",
          expect="a baseline that fails twice was not reported FAILED")
def test_a_baseline_that_fails_twice_is_failed_in_a_parallel_run_too_and_prints_both_failure_lines(tmp_path, capsys):
    planted_trio(tmp_path, '''

@mutation("tests/m.py", "def f", "def ff")
def test_y():
    assert False, "always"
''')
    code, out = output_of(capsys, tmp_path, 4)
    assert code == 1 and "4/5 killed, 1 failed, 0 flaky baseline(s)" in out, \
        "a baseline that fails twice was not reported FAILED: " + out
    assert "baseline not green" in out and "second run: baseline not green" in out, out


# ---- browser tests run in the browser image, with a queue of their own ---------------------------------------------
FAKE_DOCKER = """#!{python}
import os, subprocess, sys
args = sys.argv[1:]
open(os.environ["DOCKER_LOG"], "a").write(" ".join(args) + "\\n")
if args[:1] != ["run"]:
    sys.exit(0)
mount = next(a for a in args if a.endswith(":/work:ro")).split(":")[0]
env = dict(os.environ, PYTHONPATH=next(a for a in args if a.startswith("PYTHONPATH=")).split("=", 1)[1]
           .replace("/work", mount))
rest = args[args.index("pytest") + 1:]
env["SHORT_TMP_DIR"] = os.environ["SHORT_TMP_DIR"]
sys.exit(subprocess.run([sys.executable, "-m", "pytest", *rest], cwd=mount, env=env).returncode)
"""

BROWSER_TEST = """

@mutation("tests/m.py", "def g", "def gg")
@pytest.mark.browser
def test_chrome():
    assert m.g() == 2
"""


def with_fake_docker(tmp_path: Path, monkeypatch) -> Path:
    import sys
    bins = tmp_path / "bin"
    bins.mkdir()
    (bins / "docker").write_text(FAKE_DOCKER.format(python=sys.executable), encoding="utf-8")
    (bins / "docker").chmod(0o755)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("SHORT_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "%s:%s" % (bins, os.environ["PATH"]))
    monkeypatch.setenv("MUTATE_BROWSER_IMAGE", "relay-mutate-browser:test")
    return log


@mutation(S, 'any(isinstance(d, ast.Attribute) and d.attr == "browser" for d in fn.decorator_list)',
          'any(isinstance(d, ast.Attribute) and d.attr == "box" for d in fn.decorator_list)',
          expect="the browser test was not recognised")
def test_the_tests_marked_browser_are_found_by_their_mark(tmp_path):
    planted_trio(tmp_path, BROWSER_TEST.replace("@pytest.mark.browser", "@pytest.mark.browser")
                 .replace("\n\n@mutation", "\n\nimport pytest\n\n\n@mutation", 1))
    plain = tmp_path / "packages" / "relay-browser" / "tests"
    plain.mkdir(parents=True)
    (plain / "test_plain.py").write_text("def test_p():\n    pass\n", encoding="utf-8")
    assert mutate.browser_tests(tmp_path) == {"tests/test_m.py::test_chrome",
                                              "packages/relay-browser/tests/test_plain.py::test_p"}, \
        "the browser test was not recognised"


@mutation(S, "if IMAGE and node_id in BROWSER:", "if False:", expect="a browser test ran outside the image")
@mutation(S, '"-p", "short_tmp_plugin", *cmd[3:]]', '*cmd[3:]]', expect="the browser container got no short tmp_path")
def test_a_browser_test_runs_in_the_image_and_no_other_test_does_and_the_result_is_unchanged(
        tmp_path, capsys, monkeypatch):
    planted_trio(tmp_path, BROWSER_TEST).joinpath("tests", "conftest.py").write_text("", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_m.py"
    test_file.write_text("import pytest\n" + test_file.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "short_tmp_plugin.py", tmp_path / "scripts")
    plain = output_of(capsys, tmp_path, 2)                # no image: everything runs here
    log = with_fake_docker(tmp_path, monkeypatch)
    for jobs in (1, 3):
        log.write_text("")
        assert output_of(capsys, tmp_path, jobs) == plain, "the result changed with the image (jobs=%d)" % jobs
        runs = [ln for ln in log.read_text().splitlines() if ln.startswith("run ")]
        assert len(runs) == 2 and all("tests/test_m.py::test_chrome" in ln for ln in runs), \
            "a browser test ran outside the image or a plain test ran in it: %s" % runs
        assert all("--shm-size=1g" in ln and ":/work:ro" in ln and "relay-mutate-browser:test" in ln for ln in runs)
        assert all("pytest -p short_tmp_plugin " in ln for ln in runs), \
            "the browser container got no short tmp_path: %s" % runs


# ---- the knobs of the fast gate: MUTATE_JOBS, SOAK_JOBS, PYTEST_JOBS (all default to the serial run) ---------------
MUTATE_SH = "scripts/check-mutate.sh"


@mutation(MUTATE_SH, '${MUTATE_JOBS:+--jobs "$MUTATE_JOBS"}', "", expect="MUTATE_JOBS did not reach mutate.py")
@mutation(MUTATE_SH, '${MUTATE_JOBS:+--jobs "$MUTATE_JOBS"}', "--jobs 1",
          expect="the mutation stage is not serial by default")
def test_the_mutation_stage_runs_mutate_py_with_jobs_only_when_mutate_jobs_is_set(tmp_path):
    def args(**env):
        base = {k: v for k, v in os.environ.items() if k != "MUTATE_JOBS"}      # a gate on the box sets it
        run = subprocess.run(["bash", str(REPO / MUTATE_SH), str(tmp_path / "log"), "--only", "x"], capture_output=True,
                             text=True, env={**base, "MUTATE_CMD": "echo", **env})
        return run.stdout.strip()
    assert args() == "--only x", "the mutation stage is not serial by default"
    assert args(MUTATE_JOBS="5") == "--jobs 5 --only x", "MUTATE_JOBS did not reach mutate.py"


@mutation(CHECK, "  MUTATE_JOBS=${MUTATE_JOBS-24}\n", "  MUTATE_JOBS=${MUTATE_JOBS:-1}\n",
          expect="the box default of MUTATE_JOBS is not parallel")
@mutation(CHECK, "  SOAK_JOBS=${SOAK_JOBS-16}\n", "  SOAK_JOBS=${SOAK_JOBS:-1}\n",
          expect="the box default of SOAK_JOBS is not parallel")
@mutation(CHECK, "  PYTEST_JOBS=${PYTEST_JOBS-8}\n", "  PYTEST_JOBS=${PYTEST_JOBS:-8}\n",
          expect="an empty PYTEST_JOBS no longer turns xdist off")
@mutation(CHECK, "  PYTEST_JOBS=${PYTEST_JOBS-8}\n", "  PYTEST_JOBS=${PYTEST_JOBS-1}\n",
          expect="the box default of PYTEST_JOBS is not parallel")
def test_on_the_box_the_slow_stages_default_to_parallel_and_an_explicit_value_wins():
    # the part of check.sh that sets the defaults, run in a shell with RELAY_ON_BOX=1 (and without it)
    text = (REPO / CHECK).read_text(encoding="utf-8")
    start = text.index('if [ "${RELAY_ON_BOX:-}" = 1 ]; then')
    block = text[start:text.index("export MUTATE_JOBS SOAK_JOBS PYTEST_JOBS")]

    def settled(env: dict) -> str:
        script = block + 'echo "[$MUTATE_JOBS][$SOAK_JOBS][$PYTEST_JOBS]"'
        run = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], **env})
        return run.stdout.strip()

    box = settled({"RELAY_ON_BOX": "1"})
    assert box.startswith("[24]"), "the box default of MUTATE_JOBS is not parallel: " + box
    assert box.startswith("[24][16]"), "the box default of SOAK_JOBS is not parallel: " + box
    assert box == "[24][16][8]", "the box default of PYTEST_JOBS is not parallel: " + box
    assert settled({}) == "[][][]"
    serial = settled({"RELAY_ON_BOX": "1", "MUTATE_JOBS": "1", "SOAK_JOBS": "1", "PYTEST_JOBS": ""})
    assert serial == "[1][1][]", "an empty PYTEST_JOBS no longer turns xdist off: " + serial
    assert settled({"RELAY_ON_BOX": "1", "SOAK_JOBS": "3"}) == "[24][3][8]", \
        "the box default of SOAK_JOBS is not parallel"


@mutation(CHECK, '${SOAK_JOBS:+--jobs "$SOAK_JOBS"}', "", expect="SOAK_JOBS did not reach soak.py")
@mutation(CHECK, '${PYTEST_JOBS:+-n "$PYTEST_JOBS" --dist loadfile}', "", expect="PYTEST_JOBS did not reach pytest")
@mutation(CHECK, '--dist loadfile}', "--dist load}", expect="a test file was split over workers")
def test_check_sh_passes_soak_jobs_and_pytest_jobs_on_and_keeps_each_test_file_on_one_worker():
    text = (REPO / CHECK).read_text(encoding="utf-8")
    assert '${SOAK_JOBS:+--jobs "$SOAK_JOBS"}' in text, "SOAK_JOBS did not reach soak.py"
    assert '${PYTEST_JOBS:+-n "$PYTEST_JOBS" --dist' in text, "PYTEST_JOBS did not reach pytest"
    assert '--dist loadfile}' in text, "a test file was split over workers"
