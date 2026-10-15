#!/usr/bin/env bash
# The browser tier INSIDE the image (run by scripts/check-in-container.sh from the tree root, mounted read-only):
# every browser test, `pytest -m browser`, with a line per test. It prints how many tests the Mac tier selects
# (`browser and not box`) and how many only the box can run (`browser and box`), and fails unless exactly that many
# passed. pytest's own status counts: a run that prints every PASSED line and then fails in teardown fails the tier, and a
# collection that fails or lists a different number of tests than the marks add up to cannot give a usable count.
# Every test has a time limit (scripts/hang_guard_plugin.py, 300 s): a hung test fails with its node id and a traceback.
# test_graceful_stop.py drives the docker CLI and runs from the host. PYTEST and TIER_LOG are for the test of this script.
set -euo pipefail
PYTEST=${PYTEST:-pytest}
LOG=${TIER_LOG:-/tmp/tier.log}
skip=--ignore=tests/browser/test_graceful_stop.py

# The number of tests `pytest "$@"` selects. addopts holds -q, so --collect-only -q prints "file: N" lines.
count() {
  local out
  out=$($PYTEST "$@" $skip --collect-only -q -p no:cacheprovider) \
    || { echo "browser tier: FAILED, the collection of '$*' failed" >&2; exit 1; }
  awk -F": " '/^[^ ]+: [0-9]+$/ {n += $2} END {print n + 0}' <<< "$out"
}

# Narrowed run: `browser-tier.sh PATH_OR_NODE_ID...` runs only those browser tests. The exact-count rule of the whole
# tier does not apply (it is not a gate); what stays: something must be selected and every selected test must pass.
if [ $# -gt 0 ]; then
  selected=$(count -m browser "$@") || exit 1
  [ "$selected" -gt 0 ] || { echo "browser tier: FAILED, no browser test selected by: $*" >&2; exit 1; }
  echo "browser tier: PARTIAL (narrowed): $selected test(s) selected by: $*"
  $PYTEST -m browser "$@" $skip -p short_tmp_plugin -p hang_guard_plugin -p no:cacheprovider -vv | tee "$LOG"
  passed=$(grep -c " PASSED" "$LOG" || true)
  [ "$passed" = "$selected" ] \
    || { echo "browser tier: FAILED, $passed passed of $selected selected (a skip or a failure)" >&2; exit 1; }
  echo "browser tier in the image: $passed passed (PARTIAL: narrowed)"
  exit 0
fi

mac=$(count -m "browser and not box") || exit 1
only=$(count -m "browser and box") || exit 1
every=$(count -m browser) || exit 1
echo "browser tier: $mac test(s) of the Mac tier (browser and not box) + $only box-only = $((mac + only))"
[ "$((mac + only))" -gt 0 ] || { echo "browser tier: FAILED, no browser test selected" >&2; exit 1; }
[ "$every" = "$((mac + only))" ] \
  || { echo "browser tier: FAILED, -m browser selects $every test(s), the two parts add up to $((mac + only))" >&2; exit 1; }

# pipefail: pytest's status survives tee. -vv nets to one -v with addopts' -q: a line per test.
$PYTEST -m browser $skip -p short_tmp_plugin -p hang_guard_plugin -p no:cacheprovider -vv | tee "$LOG"
passed=$(grep -c " PASSED" "$LOG" || true)
[ "$passed" = "$((mac + only))" ] \
  || { echo "browser tier: FAILED, $passed passed of $((mac + only)) selected (a skip or a failure)" >&2; exit 1; }
echo "browser tier in the image: $passed passed"
