#!/usr/bin/env bash
# The browser tier of `scripts/check.sh --full`. On the box it builds the `browser` image,
# then runs two things:
#   1. EVERY browser test, `pytest -m browser`, INSIDE the image under xvfb (tini + xvfb-exec), on the compose network
#      of throwaway Postgres + MinIO from deploy/compose.check.yml: what the Mac tier runs (`browser and not box`)
#      plus the tests only the box can run (`browser and box`; test_graceful_stop.py is run from here, 2.). The
#      counts are printed and the tier fails unless exactly that many tests passed (a skip or a miss is a failure);
#   2. tests/browser/test_graceful_stop.py from here, through DOCKER_HOST=ssh://<box>: it starts a stand-in worker
#      in the image, stops it and checks the worker got the grace period with its display alive.
#
# Isolation: every run has a token (epoch.pid). Everything it creates is named or labelled with it: the temp dir
# /tmp/relay-check.<token>, compose project relay-check-<token>, container relay-check-run-<token>, the image
# tag relay-check-browser:<token> and the label relay.check=<token>. Teardown removes exactly those, also on
# failure and on Ctrl-C, so concurrent runs cannot touch each other and nothing else on the box is touched. The
# test container joins the compose network (no --network host); no published ports are used (ephemeral
# loopback ones, unused). No prune of any kind: images are removed by label, containers by label or name.
#
# Where it runs: from the Mac it reaches the box over ssh (RELAY_BOX). On the box itself (scripts/box.sh sets
# RELAY_ON_BOX=1) it uses the local Docker and a local copy, with no ssh to itself.
#
# Narrowed run: `scripts/check-in-container.sh PATH_OR_NODE_ID...` (on the box: `scripts/box.sh --tree NAME --
# scripts/check-in-container.sh tests/admin_ui/test_e2e_jobs.py`) runs only those browser tests, in the same image, with
# the same token, isolation and teardown. It skips the exact-count rule and the graceful-stop test, prints
# `browser tier: PARTIAL (narrowed)` and still fails when no test is selected or one does not pass. Flags are refused.
# Without arguments nothing changes: the whole tier, which scripts/check.sh --full runs.
#
# Screenshots: with RELAY_SCREENSHOTS_DIR=PATH set on the command line (`scripts/box.sh --tree NAME --
# RELAY_SCREENSHOTS_DIR=shots scripts/check-in-container.sh tests/admin_ui/test_e2e_sweep.py`) the container gets that
# variable as /shots (a mounted folder anyone may write) and, when the run ends (failed ones too), what it wrote is
# copied to PATH, relative to the folder the script runs in. Unset: nothing is mounted and nothing is copied.
set -euo pipefail
cd "$(dirname "$0")/.."
for arg in "$@"; do
  case "$arg" in -*) echo "check-in-container.sh: only test paths or node ids are accepted, not '$arg'" >&2; exit 2 ;; esac
done
narrowed=$#
tier_args=; [ "$#" -eq 0 ] || tier_args=$(printf '%q ' "$@")   # printf with no argument still prints one empty one ('')
# RELAY_SCREENSHOTS_DIR (read here, never forwarded as is): a folder the run's screenshots are copied to afterwards.
# A relative path is relative to where this script is run (the tree's folder on the box); the container writes to /shots.
shots_dest=${RELAY_SCREENSHOTS_DIR:-}

BOX="${RELAY_BOX:-phuongnam@192.168.18.227}"
TOKEN="$(date +%s)-$$"
DIR="/tmp/relay-check.$TOKEN"
PROJECT="relay-check-$TOKEN"
IMAGE="relay-browser-image:$TOKEN"          # what scripts/browser-image.sh builds for TOKEN
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
       (cd $DIR 2>/dev/null && scripts/browser-image.sh remove $TOKEN >/dev/null 2>&1)
       docker rmi relay-check-browser:local >/dev/null 2>&1
       rm -rf $DIR; true" || true
}
trap cleanup EXIT

SYNC_EXCLUDES=(--exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache
  --exclude='deploy/*.env' --exclude='._*' --exclude=.DS_Store --exclude=node_modules)
if on_box; then
  mkdir -p "$DIR" && rsync -a "${SYNC_EXCLUDES[@]}" ./ "$DIR/"
else
  rsync -a --rsync-path="mkdir -p $DIR && rsync" "${SYNC_EXCLUDES[@]}" ./ "$BOX:$DIR/"
fi

rc=0
box "DIR=$DIR PROJECT=$PROJECT IMAGE=$IMAGE RUNNER=$RUNNER LABEL=$LABEL TOKEN=$TOKEN SHOTS=${shots_dest:+1} TIER_ARGS=$(printf %q "$tier_args") bash -s" <<'REMOTE' || rc=$?
set -euo pipefail
cd "$DIR"
shots=()
if [ -n "${SHOTS:-}" ]; then      # the container user is not the box user: the folder must be writable by anyone
  mkdir -m 777 shots; shots=(-e RELAY_SCREENSHOTS_DIR=/shots -v "$DIR/shots:/shots")
fi
eval "set -- $TIER_ARGS"          # the narrowing arguments (test paths or node ids), if any
export CHECK_PG_PORT= CHECK_MINIO_PORT=   # empty = publish an ephemeral loopback port; the tests use the network

# The browser image plus the dev dependencies (scripts/browser-image.sh, shared with the mutation stage); it carries
# the label relay.browser-image=$TOKEN and is removed by `browser-image.sh remove`.
[ "$(scripts/browser-image.sh build "$TOKEN" 2>build.log)" = "$IMAGE" ] || { tail -40 build.log; exit 1; }
docker compose -p "$PROJECT" -f deploy/compose.check.yml up -d --wait --quiet-pull

# The image's entrypoint is `tini -- xvfb-exec`, so the command below runs on the virtual screen.
# The whole tree is mounted read-only (tests, fixtures, scripts); its sources go first on PYTHONPATH. Each test gets a
# short tmp_path (scripts/short_tmp_plugin.py: Chrome's socket path must stay under 108 bytes).
envargs=(); while IFS= read -r line; do envargs+=(-e "$line"); done < <(scripts/browser-image.sh env)
docker run --rm --name "$RUNNER" --label "$LABEL" --network "${PROJECT}_default" --shm-size=1g ${RELAY_GATE_CPUSET:+--cpuset-cpus "$RELAY_GATE_CPUSET"} \
  -e PYTHONDONTWRITEBYTECODE=1 "${envargs[@]}" \
  -e PYTHONPATH="$(cd "$DIR" && printf '/work/%s:' packages/*/src apps/*/src)/work/scripts" \
  -v "$DIR:/work:ro" ${shots[@]+"${shots[@]}"} -w /work \
  "$IMAGE" bash -euo pipefail -c 'echo "Chrome: $(/opt/google/chrome/chrome --version)"; exec scripts/browser-tier.sh "$@"' bash "$@"
REMOTE

# The screenshots are copied also when the run failed (a failing state is the one to look at), before the cleanup.
if [ -n "$shots_dest" ]; then
  mkdir -p "$shots_dest"
  if on_box; then cp -R "$DIR/shots/." "$shots_dest/"; else rsync -a "$BOX:$DIR/shots/" "$shots_dest/"; fi \
    && echo "screenshots: copied to $shots_dest" || echo "screenshots: the copy to $shots_dest failed" >&2
fi
[ "$rc" -eq 0 ] || exit "$rc"

if [ "$narrowed" -gt 0 ]; then
  echo "browser tier: PARTIAL (narrowed)"     # not a gate: the exact counts and the graceful-stop test are skipped
  exit 0
fi
uv sync --all-packages --frozen --quiet
if on_box; then unset DOCKER_HOST; else export DOCKER_HOST="ssh://$BOX"; fi
RELAY_STOP_TEST_IMAGE="$IMAGE" RELAY_CHECK_TOKEN="$TOKEN" PYTHONDONTWRITEBYTECODE=1 \
  uv run pytest -m browser -p no:cacheprovider tests/browser/test_graceful_stop.py
echo "browser tier: GREEN"
