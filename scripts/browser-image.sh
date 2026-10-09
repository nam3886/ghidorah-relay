#!/usr/bin/env bash
# The image the browser tests run in, for the mutation stage (scripts/mutate.py, MUTATE_BROWSER_IMAGE) and for the
# browser tier (scripts/check-in-container.sh): the `browser` target of deploy/Dockerfile plus the dev dependencies
# (pytest). Everything it builds carries the label
# relay.browser-image=TOKEN, so `remove` deletes exactly that and nothing else. Nothing is installed on the host.
# usage: browser-image.sh build TOKEN   prints the tag     browser-image.sh remove TOKEN
set -euo pipefail
cd "$(dirname "$0")/.."
cmd=${1:?build or remove} token=${2:?token}
case "$cmd" in
  build)
    base="relay-browser-image-base:$token"
    tag="relay-browser-image:$token"
    docker build -q --label "relay.browser-image=$token" -f deploy/Dockerfile --target browser -t "$base" . >&2
    printf 'FROM %s\nRUN uv sync --frozen --all-packages\n' "$base" \
      | docker build -q --label "relay.browser-image=$token" -t "$tag" - >&2
    echo "$tag"
    ;;
  remove)
    imgs=$(docker images -q --filter "label=relay.browser-image=$token")
    # shellcheck disable=SC2086
    [ -z "$imgs" ] || docker rmi -f $imgs > /dev/null 2>&1 || true
    ;;
  *) echo "usage: browser-image.sh build|remove TOKEN" >&2; exit 2 ;;
esac
