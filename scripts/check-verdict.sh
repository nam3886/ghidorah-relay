#!/usr/bin/env bash
# Whether a scripts/check.sh run covered everything its "CHECK: GREEN" promises. Prints one reason per line when it did
# not; no output means complete. usage: check-verdict.sh FULL NARGS   (FULL is 1 for --full, NARGS the number of
# pytest arguments). Reads MUTATE_ONLY and SOAK_SEEDS. Exit 2 on a SOAK_SEEDS that is not a whole number.
set -euo pipefail
full=$1
nargs=$2
seeds="${SOAK_SEEDS:-200}"
if [ "$full" = 1 ]; then
  case "$seeds" in
    '' | *[!0-9]*) echo "SOAK_SEEDS must be a whole number, got '$seeds'" >&2; exit 2 ;;
  esac
  if [ -n "${MUTATE_ONLY:-}" ]; then echo "mutate only: $MUTATE_ONLY"; fi
  if [ "$seeds" -lt 200 ]; then echo "soak seeds: $seeds (the gate needs 200)"; fi
fi
if [ "$nargs" != 0 ]; then echo "pytest arguments narrow the run"; fi
exit 0
