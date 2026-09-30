#!/usr/bin/env bash
# The only commit gate (contracts §14.8, D18): ruff + the full pytest suite against real services.
# Postgres 16 and MinIO come from deploy/compose.check.yml and are removed afterwards.
# Commit only after the last line reads "CHECK: GREEN" (reading `tail` is not verification).
set -euo pipefail
cd "$(dirname "$0")/.."

compose=(docker compose -f deploy/compose.check.yml)
trap '"${compose[@]}" down -v >/dev/null 2>&1 || true' EXIT
if ! out=$("${compose[@]}" up -d --wait --quiet-pull 2>&1); then echo "$out"; exit 1; fi

export RELAY_TEST_PG_DSN="postgresql://relay:relay-check@127.0.0.1:55432/postgres"
export RELAY_TEST_MINIO="http://127.0.0.1:59000"

uv sync --all-packages --frozen --quiet
uv run ruff check .
uv run pytest "$@"
echo "CHECK: GREEN"
