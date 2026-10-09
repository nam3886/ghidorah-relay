#!/usr/bin/env bash
# The mutation stage of a gate: shows scripts/mutate.py's output and keeps it in LOG for check-flaky.sh. The exit
# status is mutate.py's own (pipefail), so a red stage stops the gate; nothing here may swallow it.
# usage: check-mutate.sh LOG [mutate.py args...]   (MUTATE_CMD replaces the command: the test of this script uses it)
set -euo pipefail
cd "$(dirname "$0")/.."
log=$1
shift
${MUTATE_CMD:-uv run python scripts/mutate.py} ${MUTATE_JOBS:+--jobs "$MUTATE_JOBS"} "$@" | tee "$log"
