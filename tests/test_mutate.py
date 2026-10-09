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
