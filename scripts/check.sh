#!/usr/bin/env bash
# The commit gate (contracts §14.8, D18, v1.3 #36): fast and browser-less, so it runs on the Mac.
#   scripts/check.sh           ruff + every test except the `browser` tier, against real Postgres 16 and MinIO
#   scripts/check.sh --full    the same, then scripts/mutate.py (every @mutation must turn its test red)
#                              and the browser tier inside the box container (scripts/check-in-container.sh)
# Postgres and MinIO come from deploy/compose.check.yml and are removed afterwards.
# Commit only after the last line reads "CHECK: GREEN" (reading `tail` is not verification).
set -euo pipefail
cd "$(dirname "$0")/.."

full=0
args=()
for a in "$@"; do
  if [ "$a" = "--full" ]; then full=1; else args+=("$a"); fi
done

# Stale .pyc files make a mutation or a fresh edit run the old code; keep none.
export PYTHONDONTWRITEBYTECODE=1

# One compose project and ephemeral loopback ports per run: two runs at once (parallel lanes) used to share
# `relay-check` on fixed ports, and the first to finish tore down the other's Postgres (01/10).
project="relay-check-$(date +%s)-$$"
compose=(docker compose -p "$project" -f deploy/compose.check.yml)
trap '"${compose[@]}" down -v >/dev/null 2>&1 || true' EXIT
if ! out=$(CHECK_PG_PORT="" CHECK_MINIO_PORT="" "${compose[@]}" up -d --wait --quiet-pull 2>&1); then
  echo "$out"; exit 1
fi
pg_port=$("${compose[@]}" port postgres 5432 | sed "s/.*://")
minio_port=$("${compose[@]}" port minio 9000 | sed "s/.*://")

export RELAY_TEST_PG_DSN="postgresql://relay:relay-check@127.0.0.1:${pg_port}/postgres"
export RELAY_TEST_MINIO="http://127.0.0.1:${minio_port}"
export RELAY_TEST_MINIO_ACCESS_KEY="relay"            # the throwaway MINIO_ROOT_USER/PASSWORD of compose.check.yml
export RELAY_TEST_MINIO_SECRET_KEY="relay-check"

uv sync --all-packages --frozen --quiet
uv run ruff check .
uv run pytest -m "not browser" ${args[@]+"${args[@]}"}
if [ "$full" = 1 ]; then
  uv run python scripts/mutate.py
  scripts/check-in-container.sh
fi
echo "CHECK: GREEN"
