#!/usr/bin/env bash
# Restore the pinned base images from the off-box tarballs, with no registry (docs/operations.md).
#   scripts/restore-images.sh [DIR]      DIR defaults to ~/Archives/relay-images
# Each tarball is the full multi-arch image index saved by digest, so the index digest survives. `docker load`
# leaves it nameless; the script gives it ONE relay-owned tag in the image's own repository,
# `<repo>:relay-pin-<first 8 hex of the digest>`, and then proves that the exact pinned references
# (`postgres:16-alpine@sha256:...`) resolve locally. Floating tags that other projects use (`postgres:16-alpine`,
# `python:3.13-slim`, ...) are never created, moved or removed. A relay-pin tag that already points at a different
# image is refused. Afterwards use `docker compose ... up --pull never` and `docker build --pull=false`.
# Needs a daemon with the containerd image store (Docker Desktop and Docker 29 default): the pin check fails loudly
# otherwise. Drilled on the Mac.
set -euo pipefail

DIR="${1:-$HOME/Archives/relay-images}"

# file | repository | pinned reference (the same values as deploy/compose*.yml and deploy/Dockerfile)
IMAGES=(
  "postgres-16-alpine.tar|postgres|postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
  "pgsty-minio-RELEASE.2026-08-04T00-00-00Z.tar|pgsty/minio|pgsty/minio:RELEASE.2026-08-04T00-00-00Z@sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372"
  "python-3.13-slim.tar|python|python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b"
  "uv-0.9.tar|ghcr.io/astral-sh/uv|ghcr.io/astral-sh/uv:0.9@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4"
)

for entry in "${IMAGES[@]}"; do
  IFS='|' read -r file repo pinned <<<"$entry"
  digest="${pinned#*@}"
  hex="${digest#sha256:}"
  tag="$repo:relay-pin-${hex:0:8}"
  [ -f "$DIR/$file" ] || { echo "restore-images: $DIR/$file is missing" >&2; exit 1; }
  existing="$(docker image inspect -f '{{.Id}}' "$tag" 2>/dev/null || true)"
  if [ -n "$existing" ] && [ "$existing" != "$digest" ]; then
    echo "restore-images: $tag already exists with image $existing, not $digest; refusing to retag" >&2
    exit 1
  fi
  docker load -q -i "$DIR/$file" >/dev/null
  docker tag "$digest" "$tag"
  docker image inspect "$pinned" >/dev/null 2>&1 \
    || { echo "restore-images: $pinned does not resolve after load (this daemon needs the containerd image store)" >&2; exit 1; }
  echo "restored $pinned (tag $tag)"
done
echo "RESTORE: GREEN"
