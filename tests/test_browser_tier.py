"""scripts/browser-tier.sh, the browser tier inside the image, keeps pytest's own status and a usable count.

pytest is replaced by a fake that answers a collection with the numbers of the environment and a run with that many
PASSED lines and the exit status it is told; the real script text runs under bash."""

import os
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


def tier(tmp_path: Path, **env) -> subprocess.CompletedProcess:
    fake = tmp_path / "pytest"
    fake.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    base = {"FAKE_MAC": "3", "FAKE_ONLY": "2", "FAKE_EVERY": "5", "FAKE_PASSED": "5"}
    return subprocess.run(["bash", str(REPO / TIER)], capture_output=True, text=True, cwd=tmp_path,
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
