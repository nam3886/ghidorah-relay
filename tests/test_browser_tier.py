"""scripts/browser-tier.sh, the browser tier inside the image, keeps pytest's own status and a usable count.

pytest is replaced by a fake that answers a collection with the numbers of the environment and a run with that many
PASSED lines and the exit status it is told; the real script text runs under bash."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from relay_core.testing.mutation import mutation

from guards import REPO

TIER = "scripts/browser-tier.sh"

FAKE = """#!{python}
import os, sys
args = sys.argv[1:]
env = os.environ
if "--collect-only" in args:
    expr = args[args.index("-m") + 1]
    key = {{"browser and not box": "MAC", "browser and box": "ONLY", "browser": "EVERY"}}[expr]
    print("tests/a.py: " + env["FAKE_" + key])
    sys.exit(int(env.get("FAKE_COLLECT_RC_" + key, "0")))
for n in range(int(env["FAKE_PASSED"])):
    print("tests/a.py::test_%d PASSED [100%%]" % n)
sys.exit(int(env.get("FAKE_RC", "0")))
"""


def tier(tmp_path: Path, *args: str, **env) -> subprocess.CompletedProcess:
    fake = tmp_path / "pytest"
    fake.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    base = {"FAKE_MAC": "3", "FAKE_ONLY": "2", "FAKE_EVERY": "5", "FAKE_PASSED": "5"}
    return subprocess.run(["bash", str(REPO / TIER), *args], capture_output=True, text=True, cwd=tmp_path,
                          env={"PATH": os.environ["PATH"], "PYTEST": str(fake), "TIER_LOG": str(tmp_path / "tier.log"),
                               **base, **{k: str(v) for k, v in env.items()}})


def test_a_tier_whose_tests_all_pass_is_green_and_prints_both_counts(tmp_path):
    run = tier(tmp_path)
    assert run.returncode == 0 and "3 test(s) of the Mac tier (browser and not box) + 2 box-only = 5" in run.stdout, \
        run.stdout + run.stderr
    assert "browser tier in the image: 5 passed" in run.stdout


@mutation(TIER, "set -euo pipefail", "set -eu", expect="pytest's failing status was lost behind tee")
def test_a_run_that_prints_every_passed_line_and_then_exits_nonzero_fails_the_tier(tmp_path):
    run = tier(tmp_path, FAKE_RC=1)
    assert run.returncode != 0, "pytest's failing status was lost behind tee: " + run.stdout


@mutation(TIER, "    || { echo \"browser tier: FAILED, the collection of '$*' failed\" >&2; exit 1; }", "    || true",
          expect="a failed collection gave a usable count")
def test_a_collection_that_fails_cannot_give_a_usable_count(tmp_path):
    for key in ("MAC", "ONLY", "EVERY"):
        run = tier(tmp_path, **{"FAKE_COLLECT_RC_" + key: 2})
        assert run.returncode != 0 and "the collection of" in run.stderr, \
            "a failed collection gave a usable count (%s): %s" % (key, run.stdout + run.stderr)


@mutation(TIER, '[ "$every" = "$((mac + only))" ] \\\n', "true \\\n", expect="a partial collection gave a usable count")
def test_a_collection_that_lists_other_than_the_two_parts_add_up_to_fails(tmp_path):
    run = tier(tmp_path, FAKE_EVERY=4)
    assert run.returncode != 0 and "-m browser selects 4" in run.stderr, \
        "a partial collection gave a usable count: " + run.stdout + run.stderr


@mutation(TIER, '[ "$passed" = "$((mac + only))" ] \\\n', "true \\\n", expect="a skipped test went unnoticed")
def test_fewer_passed_lines_than_selected_tests_fail_the_tier(tmp_path):
    run = tier(tmp_path, FAKE_PASSED=4)
    assert run.returncode != 0 and "4 passed of 5 selected" in run.stderr, \
        "a skipped test went unnoticed: " + run.stdout + run.stderr


@mutation(TIER, '[ "$((mac + only))" -gt 0 ] || ', "true || ", expect="an empty selection counted as green")
def test_a_tier_that_selects_no_test_fails(tmp_path):
    run = tier(tmp_path, FAKE_MAC=0, FAKE_ONLY=0, FAKE_EVERY=0, FAKE_PASSED=0)
    assert run.returncode != 0, "an empty selection counted as green: " + run.stdout + run.stderr


NARROW_ENV = {"FAKE_MAC": 3, "FAKE_ONLY": 2, "FAKE_EVERY": 2, "FAKE_PASSED": 2}   # the parts do NOT add up to the whole


@mutation(TIER, "if [ $# -gt 0 ]; then", "if false; then", expect="a narrowed run was held to the exact counts")
def test_a_narrowed_run_skips_the_exact_count_rule_says_partial_and_runs_only_what_it_was_given(tmp_path):
    run = tier(tmp_path, "tests/admin_ui/test_e2e_jobs.py", **NARROW_ENV)
    assert run.returncode == 0, "a narrowed run was held to the exact counts: " + run.stdout + run.stderr
    assert "browser tier: PARTIAL (narrowed): 2 test(s) selected by: tests/admin_ui/test_e2e_jobs.py" in run.stdout
    assert "2 passed (PARTIAL: narrowed)" in run.stdout
    # the negative control: with the same numbers and no argument the whole tier refuses
    whole = tier(tmp_path, **NARROW_ENV)
    assert whole.returncode != 0 and "-m browser selects 2" in whole.stderr and "PARTIAL" not in whole.stdout, \
        whole.stdout + whole.stderr


@mutation(TIER, '[ "$selected" -gt 0 ] || ', "true || ", expect="a narrowed run that selects nothing was green")
@mutation(TIER, '    || { echo "browser tier: FAILED, $passed passed of $selected selected', '    || true # {',
          expect="a narrowed run with a failed test was green")
def test_a_narrowed_run_that_selects_nothing_or_whose_tests_do_not_all_pass_fails(tmp_path):
    none = tier(tmp_path, "tests/admin_ui/test_e2e_none.py", **{**NARROW_ENV, "FAKE_EVERY": 0, "FAKE_PASSED": 0})
    assert none.returncode != 0 and "no browser test selected" in none.stderr, \
        "a narrowed run that selects nothing was green: " + none.stdout + none.stderr
    short = tier(tmp_path, "tests/admin_ui/test_e2e_jobs.py", **{**NARROW_ENV, "FAKE_PASSED": 1})
    assert short.returncode != 0 and "1 passed of 2 selected" in short.stderr, \
        "a narrowed run with a failed test was green: " + short.stdout + short.stderr
    bad = tier(tmp_path, "tests/admin_ui/test_e2e_jobs.py", **{**NARROW_ENV, "FAKE_COLLECT_RC_EVERY": 4})
    assert bad.returncode != 0 and "the collection of" in bad.stderr, bad.stdout + bad.stderr


IN_CONTAINER = "scripts/check-in-container.sh"


@mutation(IN_CONTAINER, 'case "$arg" in -*)', 'case "$arg" in -X*)', expect="a pytest flag was accepted")
def test_the_container_runner_accepts_test_paths_and_node_ids_only_and_passes_them_to_the_tier():
    refused = subprocess.run(["bash", str(REPO / IN_CONTAINER), "tests/a.py", "-m", "not browser"],
                             capture_output=True, text=True, timeout=60)
    assert refused.returncode == 2 and "only test paths or node ids" in refused.stderr, \
        "a pytest flag was accepted: %r" % refused.stderr
    text = (REPO / IN_CONTAINER).read_text(encoding="utf-8")
    assert 'exec scripts/browser-tier.sh "$@"\' bash "$@"' in text and 'eval "set -- $TIER_ARGS"' in text
    assert 'echo "browser tier: PARTIAL (narrowed)"' in text
    assert text.index("PARTIAL (narrowed)") < text.index("uv sync"), "the graceful-stop test is not skipped"


FAKE_DOCKER = """#!{python}
import json, os, stat, sys
args = sys.argv[1:]
if args[:1] == ["run"]:
    rec = {{"args": args}}
    mounts = [a.split(":")[0] for a in args if a.endswith(":/shots")]
    if mounts:      # what the browser tests do inside the container: write into the mounted folder
        rec["mode"] = oct(stat.S_IMODE(os.stat(mounts[0]).st_mode))
        with open(os.path.join(mounts[0], "light-login.png"), "w") as f:
            f.write("png")
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps(rec) + "\\n")
    sys.exit(int(os.environ.get("FAKE_RUN_RC", "0")))
sys.exit(0)
"""


def container_run(tmp_path: Path, *args: str, **env) -> tuple[subprocess.CompletedProcess, list[dict]]:
    """The real scripts/check-in-container.sh on the box path (RELAY_ON_BOX=1) over a small tree, with docker and uv
    replaced by fakes that record the `docker run` they are given and write a screenshot as the container would."""
    tree, bin_dir = tmp_path / "tree", tmp_path / "bin"
    (tree / "scripts").mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy(REPO / IN_CONTAINER, tree / IN_CONTAINER)
    (tree / "scripts/browser-image.sh").write_text(
        '#!/bin/bash\n[ "$1" = build ] && echo "relay-browser-image:$2"\nexit 0\n')
    (bin_dir / "docker").write_text(FAKE_DOCKER.format(python=sys.executable), encoding="utf-8")
    (bin_dir / "uv").write_text("#!/bin/bash\nexit 0\n")
    for f in [tree / IN_CONTAINER, tree / "scripts/browser-image.sh", bin_dir / "docker", bin_dir / "uv"]:
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "docker.log"
    base = {"PATH": "%s:%s" % (bin_dir, os.environ["PATH"]), "HOME": str(tmp_path), "RELAY_ON_BOX": "1",
            "FAKE_LOG": str(log)}
    run = subprocess.run(["bash", str(tree / IN_CONTAINER), *args], capture_output=True, text=True, cwd=tree,
                         timeout=120, env={**base, **{k: str(v) for k, v in env.items()}})
    return run, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@mutation(IN_CONTAINER, '[ "$#" -eq 0 ] || tier_args=', "tier_args=", expect="an empty argument reached the tier")
def test_a_whole_tier_run_hands_the_container_no_argument_so_the_tier_never_says_partial(tmp_path):
    """`printf '%q ' "$@"` with no argument prints one empty argument; it reached browser-tier.sh as a narrowing
    argument and a plain `check.sh --full` printed `PARTIAL (narrowed)` with an empty list."""
    run, runs = container_run(tmp_path)
    assert run.returncode == 0 and "browser tier: GREEN" in run.stdout and "PARTIAL" not in run.stdout, \
        run.stdout + run.stderr
    assert len(runs) == 1 and runs[0]["args"][-1] == "bash", \
        "an empty argument reached the tier: %r" % runs[0]["args"][-2:]
    narrowed, runs = container_run(tmp_path / "n", "tests/admin_ui/test_e2e_jobs.py", "tests/a.py::t[x y]")
    assert narrowed.returncode == 0 and "browser tier: PARTIAL (narrowed)" in narrowed.stdout, narrowed.stdout
    assert runs[0]["args"][-3:] == ["bash", "tests/admin_ui/test_e2e_jobs.py", "tests/a.py::t[x y]"], \
        runs[0]["args"][-3:]


@mutation(IN_CONTAINER, 'shots=(-e RELAY_SCREENSHOTS_DIR=/shots -v "$DIR/shots:/shots")',
          'shots=(-v "$DIR/shots:/shots")', expect="the container was not told where the screenshots go")
@mutation(IN_CONTAINER, "mkdir -m 777 shots", "mkdir shots",
          expect="the shots folder is not writable by the container user")
@mutation(IN_CONTAINER, 'cp -R "$DIR/shots/." "$shots_dest/"', "true", expect="no screenshot was copied back")
def test_a_narrowed_run_with_a_screenshots_folder_mounts_it_and_copies_what_the_container_wrote_back(tmp_path):
    dest = tmp_path / "out" / "screenshots"
    run, runs = container_run(tmp_path, "tests/admin_ui/test_e2e_sweep.py", RELAY_SCREENSHOTS_DIR=dest)
    assert run.returncode == 0, run.stdout + run.stderr
    args = runs[0]["args"]
    assert "RELAY_SCREENSHOTS_DIR=/shots" in args and args[args.index("RELAY_SCREENSHOTS_DIR=/shots") - 1] == "-e", \
        "the container was not told where the screenshots go: %r" % args
    assert runs[0]["mode"] == "0o777", "the shots folder is not writable by the container user: %s" % runs[0]["mode"]
    assert (dest / "light-login.png").read_text() == "png", "no screenshot was copied back"
    assert "screenshots: copied to %s" % dest in run.stdout


@mutation(IN_CONTAINER, '[ "$rc" -eq 0 ] || exit "$rc"', "true", expect="a failed run was reported green")
def test_the_screenshots_of_a_failed_run_are_copied_and_the_run_still_fails_with_its_status(tmp_path):
    dest = tmp_path / "shots-out"
    run, _ = container_run(tmp_path, "tests/admin_ui/test_e2e_sweep.py", RELAY_SCREENSHOTS_DIR=dest, FAKE_RUN_RC=3)
    assert run.returncode == 3, "a failed run was reported green: %s %s" % (run.returncode, run.stdout + run.stderr)
    assert (dest / "light-login.png").is_file(), "the screenshots of a failed run were lost"


def test_without_a_screenshots_folder_nothing_is_mounted_and_nothing_is_copied(tmp_path):
    run, runs = container_run(tmp_path, "tests/admin_ui/test_e2e_sweep.py")
    assert run.returncode == 0 and "screenshots:" not in run.stdout, run.stdout + run.stderr
    assert not any("RELAY_SCREENSHOTS_DIR" in a or a.endswith(":/shots") for a in runs[0]["args"]), runs[0]["args"]


STOP_TEST = "tests/browser/test_graceful_stop.py"


@mutation(STOP_TEST, 'STANDIN_LABELS = ["--label", "com.docker.compose.project=relay-standin"',
          'STANDIN_LABELS = ["--label", "com.docker.compose.project=relay"',
          expect="the stand-in carries the production project label")
@mutation(STOP_TEST, "--label\", label, *STANDIN_LABELS, *prefix", "--label\", label, *prefix",
          expect="the stand-in is started without the label override")
def test_the_stand_in_worker_is_started_with_labels_that_hide_it_from_the_production_project():
    """The stop test runs a docker-compose-built image directly; the image's labels say project=relay."""
    import ast
    import re

    text = (REPO / STOP_TEST).read_text(encoding="utf-8")
    labels = next(ast.literal_eval(n.value) for n in ast.walk(ast.parse(text))
                  if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "STANDIN_LABELS" for t in n.targets))
    projects = [v.split("=", 1)[1] for v in labels if v.startswith("com.docker.compose.project=")]
    assert projects and "relay" not in projects, "the stand-in carries the production project label: %r" % projects
    assert re.search(r'docker\("run", "-d", "--name", name, "--label", label, \*STANDIN_LABELS, \*prefix', text), \
        "the stand-in is started without the label override"
