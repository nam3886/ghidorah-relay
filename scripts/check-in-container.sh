#!/usr/bin/env bash
# The browser tier of `scripts/check.sh --full` (contracts §14, D33). On the box it builds the `browser` image,
# then runs two things:
#   1. `pytest -m browser tests/browser/test_chrome.py` INSIDE the image under xvfb (tini + xvfb-exec), on the
#      compose network of throwaway Postgres + MinIO from deploy/compose.check.yml;
#   2. tests/browser/test_graceful_stop.py from here, through DOCKER_HOST=ssh://<box>: it starts a stand-in worker
#      in the image, stops it and checks the worker got the grace period with its display alive.
#
# Isolation: every run has a token (epoch.pid). Everything it creates is named or labelled with it: the temp dir
# /tmp/relay-check.<token>, compose project relay-check-<token>, container relay-check-run-<token>, the image
# tag relay-check-browser:<token> and the label relay.check=<token>. Teardown removes exactly those, also on
# failure and on Ctrl-C, so concurrent runs cannot touch each other and nothing of dola2api_v4 is touched. The
# test container joins the compose network (no --network host); no published ports are used (ephemeral
# loopback ones, unused). No prune of any kind: images are removed by label, containers by label or name.
#
# Where it runs: from the Mac it reaches the box over ssh (RELAY_BOX). On the box itself (scripts/box.sh sets
# RELAY_ON_BOX=1) it uses the local Docker and a local copy, with no ssh to itself.
set -euo pipefail
cd "$(dirname "$0")/.."

BOX="${RELAY_BOX:-phuongnam@192.168.18.227}"
TOKEN="$(date +%s)-$$"
DIR="/tmp/relay-check.$TOKEN"
PROJECT="relay-check-$TOKEN"
IMAGE="relay-check-browser:$TOKEN"
RUNNER="relay-check-run-$TOKEN"
LABEL="relay.check=$TOKEN"

on_box() { [ "${RELAY_ON_BOX:-}" = 1 ]; }
box() { if on_box; then bash -c "$*"; else ssh -o BatchMode=yes "$BOX" "$@"; fi; }

cleanup() {
  # `relay-check-browser:local` is the tag older versions of this script left behind (one-off cleanup).
  box "docker rm -f $RUNNER >/dev/null 2>&1
       ids=\$(docker ps -aq --filter label=$LABEL); [ -z \"\$ids\" ] || docker rm -f \$ids >/dev/null 2>&1
       cd $DIR 2>/dev/null && docker compose -p $PROJECT -f deploy/compose.check.yml down -v >/dev/null 2>&1
       imgs=\$(docker images -q --filter label=$LABEL); [ -z \"\$imgs\" ] || docker rmi -f \$imgs >/dev/null 2>&1
       docker rmi relay-check-browser:local >/dev/null 2>&1
       rm -rf $DIR; true" || true
}
trap cleanup EXIT

SYNC_EXCLUDES=(--exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache
  --exclude='deploy/*.env' --exclude='._*' --exclude=.DS_Store --exclude=fixtures)
if on_box; then
  mkdir -p "$DIR" && rsync -a "${SYNC_EXCLUDES[@]}" ./ "$DIR/"
else
  rsync -a --rsync-path="mkdir -p $DIR && rsync" "${SYNC_EXCLUDES[@]}" ./ "$BOX:$DIR/"
fi

box "DIR=$DIR PROJECT=$PROJECT IMAGE=$IMAGE RUNNER=$RUNNER LABEL=$LABEL bash -s" <<'REMOTE'
set -euo pipefail
cd "$DIR"
export CHECK_PG_PORT= CHECK_MINIO_PORT=   # empty = publish an ephemeral loopback port; the tests use the network

docker build --label "$LABEL" -f deploy/Dockerfile --target browser -t "$IMAGE" . >build.log 2>&1 \
  || { tail -40 build.log; exit 1; }
docker compose -p "$PROJECT" -f deploy/compose.check.yml up -d --wait --quiet-pull

# The image's entrypoint is `tini -- xvfb-exec`, so the command below runs on the virtual screen.
# Test code is mounted read-only; dev dependencies (pytest) are installed into the container's venv.
docker run --rm --name "$RUNNER" --label "$LABEL" --network "${PROJECT}_default" --shm-size=1g \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e RELAY_TEST_PG_DSN="postgresql://relay:relay-check@postgres:5432/postgres" \
  -e RELAY_TEST_MINIO="http://minio:9000" \
  -v "$DIR/tests:/app/tests:ro" \
  "$IMAGE" sh -c 'uv sync --frozen --all-packages --quiet \
    && echo "Chrome: $(/opt/google/chrome/chrome --version)" \
    && pytest -m browser -p no:cacheprovider tests/browser/test_chrome.py'
REMOTE

uv sync --all-packages --frozen --quiet
if on_box; then unset DOCKER_HOST; else export DOCKER_HOST="ssh://$BOX"; fi
RELAY_STOP_TEST_IMAGE="$IMAGE" RELAY_CHECK_TOKEN="$TOKEN" PYTHONDONTWRITEBYTECODE=1 \
  uv run pytest -m browser -p no:cacheprovider tests/browser/test_graceful_stop.py
echo "browser tier: GREEN"
