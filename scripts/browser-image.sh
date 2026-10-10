#!/usr/bin/env bash
# The image the browser tests run in, for the mutation stage (scripts/mutate.py, MUTATE_BROWSER_IMAGE) and for the
# browser tier (scripts/check-in-container.sh): the `browser` target of deploy/Dockerfile plus the dev dependencies
# (pytest). Everything it builds carries the label
# relay.browser-image=TOKEN, so `remove` deletes exactly that and nothing else. Nothing is installed on the host.
# usage: browser-image.sh build TOKEN   prints the tag     browser-image.sh remove TOKEN     browser-image.sh env
# `env` prints, one NAME=value per line, what a test in the image needs to reach the check run's Postgres and MinIO by
# their service names on the compose network: the throwaway values of deploy/compose.check.yml that scripts/check.sh
# exports for the host. The browser tier (check-in-container.sh) and the mutation queue (mutate.py) both pass them.
set -euo pipefail
cd "$(dirname "$0")/.."
cmd=${1:?build, remove or env} token=${2:-}
case "$cmd" in
  env)
    echo "RELAY_TEST_PG_DSN=postgresql://relay:relay-check@postgres:5432/postgres"
    echo "RELAY_TEST_MINIO=http://minio:9000"
    echo "RELAY_TEST_MINIO_ACCESS_KEY=relay"
    echo "RELAY_TEST_MINIO_SECRET_KEY=relay-check"
    ;;
  build)
    : "${token:?token}"
    base="relay-browser-image-base:$token"
    tag="relay-browser-image:$token"
    docker build -q --label "relay.browser-image=$token" -f deploy/Dockerfile --target browser -t "$base" . >&2
    printf 'FROM %s\nRUN uv sync --frozen --all-packages\n' "$base" \
      | docker build -q --label "relay.browser-image=$token" -t "$tag" - >&2
    echo "$tag"
    ;;
  remove)
    : "${token:?token}"
    imgs=$(docker images -q --filter "label=relay.browser-image=$token")
    # shellcheck disable=SC2086
    [ -z "$imgs" ] || docker rmi -f $imgs > /dev/null 2>&1 || true
    ;;
  *) echo "usage: browser-image.sh build|remove TOKEN | env" >&2; exit 2 ;;
esac
