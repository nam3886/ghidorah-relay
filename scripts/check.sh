#!/usr/bin/env bash
# The commit gate (contracts §14.8, D18, v1.3 #36): fast and browser-less, so it runs on the Mac.
#   scripts/check.sh           ruff + every test except the `browser` tier, against real Postgres 16 and MinIO
#   scripts/check.sh --full    the same, then scripts/mutate.py (every @mutation must turn its test red),
#                              scripts/soak.py (200 seeds; SOAK_SEEDS=n changes it, 0 skips it) and the browser tier
#                              inside the box container (scripts/check-in-container.sh)
#   scripts/check.sh --full --local   the same without the box (D59): the browser tier runs on this Mac with the
#                              installed Google Chrome (D60); the pinned Linux Chrome, Xvfb and the container entry
#                              are proven only by the box run.
#   MUTATE_ONLY="test_x test_y" scripts/check.sh --full --local   mutate only the markers of those tests/files
#   scripts/check.sh --soak [soak.py args]   only the soak (its own database and bucket), e.g. `--soak --seed 13 --verbose`
#                              to replay a failing seed; not a gate: it never prints CHECK: GREEN
# Postgres and MinIO come from deploy/compose.check.yml and are removed afterwards.
# A run is partial, says why and ends "CHECK: PARTIAL" when it covered less than the gate means: MUTATE_ONLY set,
# SOAK_SEEDS under 200 (0 skips the soak), or any pytest argument (scripts/check-verdict.sh decides).
# Commit only after the last line reads "CHECK: GREEN" (reading `tail` is not verification).
set -euo pipefail
cd "$(dirname "$0")/.."

full=0
local_only=0
soak_only=0
args=()
nargs=0
for a in "$@"; do
  case "$a" in
    --full) full=1 ;;
    --local) local_only=1 ;;
    --soak) soak_only=1 ;;
    *) args+=("$a"); nargs=$((nargs + 1)) ;;
  esac
done
verdict=""
mutlog=/dev/null
if [ "$soak_only" != 1 ]; then verdict=$(scripts/check-verdict.sh "$full" "$nargs"); fi   # a bad SOAK_SEEDS stops here

# Stale .pyc files make a mutation or a fresh edit run the old code; keep none.
export PYTHONDONTWRITEBYTECODE=1

# On the box (32 cores, 62 GB) the slow stages run in parallel by default (D80, calibrated in the box-runner report):
# MUTATE_JOBS private copies in the mutation stage, SOAK_JOBS processes in the soak, PYTEST_JOBS xdist workers in the
# main suite. A variable that is set wins; `MUTATE_JOBS=1 SOAK_JOBS=1 PYTEST_JOBS=` is the serial gate. Elsewhere the
# stages stay serial unless the variable is set.
if [ "${RELAY_ON_BOX:-}" = 1 ]; then
  if [ "${RELAY_GATE_CAPPED:-}" = 1 ]; then      # box.sh: the production stack runs, the gate has 12 cores (D95)
    MUTATE_JOBS=${MUTATE_JOBS-12}
    SOAK_JOBS=${SOAK_JOBS-8}
    PYTEST_JOBS=${PYTEST_JOBS-4}
  else
    MUTATE_JOBS=${MUTATE_JOBS-24}
    SOAK_JOBS=${SOAK_JOBS-16}
    PYTEST_JOBS=${PYTEST_JOBS-8}
  fi
fi
export MUTATE_JOBS SOAK_JOBS PYTEST_JOBS

# One compose project and ephemeral loopback ports per run: two runs at once (parallel lanes) used to share
# `relay-check` on fixed ports, and the first to finish tore down the other's Postgres (01/10).
project="relay-check-$(date +%s)-$$"
compose=(docker compose -p "$project" -f deploy/compose.check.yml)
image_token="$(date +%s)-$$"
trap '"${compose[@]}" down -v >/dev/null 2>&1 || true; [ "$mutlog" = /dev/null ] || rm -f "$mutlog"
      scripts/browser-image.sh remove "$image_token" >/dev/null 2>&1 || true' EXIT
if ! out=$(CHECK_PG_PORT="" CHECK_MINIO_PORT="" "${compose[@]}" up -d --wait --quiet-pull 2>&1); then
  echo "$out"; exit 1
fi
pg_port=$("${compose[@]}" port postgres 5432 | sed "s/.*://")
minio_port=$("${compose[@]}" port minio 9000 | sed "s/.*://")

export RELAY_TEST_PG_DSN="postgresql://relay:relay-check@127.0.0.1:${pg_port}/postgres"
export RELAY_TEST_MINIO="http://127.0.0.1:${minio_port}"
export RELAY_TEST_MINIO_ACCESS_KEY="relay"            # the throwaway MINIO_ROOT_USER/PASSWORD of compose.check.yml
export RELAY_TEST_MINIO_SECRET_KEY="relay-check"
# Test sessions of this run cut their databases from one migrated template (packages/relay-core/tests/pgtemplate.py):
# sessions that start together would otherwise race on the cluster-wide roles. The cluster goes away with the run.
export RELAY_TEST_PG_TEMPLATE="relay_tpl_$(date +%s)_$$"

uv sync --all-packages --frozen --quiet
if [ "$soak_only" = 1 ]; then
  uv run python scripts/soak.py ${args[@]+"${args[@]}"}
  exit $?
fi
uv run ruff check .
# The admin web (apps/admin-web), in the Node container: lockfile audit, types, lint, Vitest; then a fresh build must
# equal the committed dist and declaration files. Neither writes into the tree. On the box only (D95).
if [ "$nargs" = 0 ]; then      # a run narrowed to some tests is PARTIAL anyway (check-verdict.sh)
  # WEB_CHECK_CMD is the test hook of tests/test_mutate.py (box.sh never passes it to a run)
  ${WEB_CHECK_CMD:-scripts/web.sh} check
  ${WEB_CHECK_CMD:-scripts/web.sh} verify
fi
# Type gate for the provider contract (the `fetch` shape drifted once). Isolated and pinned: not a project dependency.
uvx --from mypy==2.4.0 mypy packages/relay-core/src/relay_core/providers.py
# PYTEST_JOBS=N runs the files on N xdist workers (each with its own database); a file stays on one worker.
uv run pytest -m "not browser and not web_unit" ${PYTEST_JOBS:+-n "$PYTEST_JOBS" --dist loadfile} ${args[@]+"${args[@]}"}
if [ "$full" = 1 ]; then
  # MUTATE_ONLY="pat1 pat2" limits mutate.py to markers whose test id or file contains a pattern (parallel lanes).
  only=()
  for p in ${MUTATE_ONLY:-}; do only+=(--only "$p"); done
  mutlog=$(mktemp)
  # On the box the browser tests of the mutation stage run in the browser image (the box host has no Chrome libraries).
  if [ "$local_only" != 1 ] && [ "${RELAY_ON_BOX:-}" = 1 ]; then
    MUTATE_BROWSER_IMAGE=$(scripts/browser-image.sh build "$image_token")
    export MUTATE_BROWSER_IMAGE
    export MUTATE_BROWSER_NETWORK="${project}_default"   # the browser containers of the queue join this run's compose network
  fi
  # MUTATE_CMD is the test hook of check-mutate.sh: one inherited from the caller must not replace mutate.py here
  env -u MUTATE_CMD scripts/check-mutate.sh "$mutlog" ${only[@]+"${only[@]}"}
  if [ -n "${MUTATE_ONLY:-}" ]; then
    echo "mutate: PARTIAL (MUTATE_ONLY=$MUTATE_ONLY)"
  fi
  # The soak (contracts §14.2): a seeded random workload with chaos against the FakeProvider. A failing seed prints
  # its number; replay it with `scripts/check.sh --soak --seed N --verbose`.
  if [ "${SOAK_SEEDS:-200}" != 0 ]; then
    uv run python scripts/soak.py --seeds "${SOAK_SEEDS:-200}" ${SOAK_JOBS:+--jobs "$SOAK_JOBS"}
  else
    echo "soak: SKIPPED (SOAK_SEEDS=0)"
  fi
  if [ "$local_only" = 1 ]; then
    # D60: the browser suites on the Mac (patchright + the installed Google Chrome). Exit 5 = none selected: a note
    # for a narrowed run (it is PARTIAL anyway), a failure for the whole gate, which must never end GREEN without one.
    rc=0
    uv run pytest -m "browser and not box" ${args[@]+"${args[@]}"} || rc=$?
    if [ "$rc" = 5 ] && [ "$nargs" = 0 ]; then
      echo "browser tier: FAILED, no browser test was selected in a run with no pytest arguments (a mark or a collect-ignore changed?)" >&2
      exit 1
    elif [ "$rc" = 5 ]; then
      echo "browser tier: no browser test selected"
    elif [ "$rc" != 0 ]; then
      exit "$rc"
    else
      echo "browser tier: GREEN on this Mac (D60); box image tests (-m box) not run: they need the box"
    fi
  else
    scripts/check-in-container.sh
  fi
fi
if [ "$full" = 1 ]; then      # the last screen of a gate shows the baselines that needed a second run
  scripts/check-flaky.sh "$mutlog"
fi
if [ -n "$verdict" ]; then
  echo "CHECK: PARTIAL (not a commit gate: $(printf '%s' "$verdict" | tr '\n' ';'))"
else
  echo "CHECK: GREEN"
fi
