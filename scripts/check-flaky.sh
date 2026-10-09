#!/usr/bin/env bash
# Repeats the FLAKY lines of a scripts/mutate.py run (a baseline that failed once unmutated and passed on the second
# run), so that the last screen of a gate shows them. Prints nothing when there are none. A log that is not a readable
# regular file is an error (exit 2): the gate must not end green without having looked. usage: check-flaky.sh LOG
set -euo pipefail
log=${1:-}
if [ ! -f "$log" ] || [ ! -r "$log" ]; then
  echo "check-flaky: cannot read the mutation log: ${log:-(no path given)}" >&2
  exit 2
fi
found=0
grep -q '^FLAKY ' "$log" || found=$?
if [ "$found" = 0 ]; then
  echo "FLAKY baselines (failed once unmutated, green on the second run; the mutation stage above has them):"
  grep -A1 '^FLAKY ' "$log" | grep -v '^--$'
elif [ "$found" != 1 ]; then
  echo "check-flaky: could not search the mutation log: $log" >&2
  exit 2
fi
