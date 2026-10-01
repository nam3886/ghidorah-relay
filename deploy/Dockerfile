# Two targets (contracts §3): `http` for api, migrate and worker-vibes; `browser` for worker-dola and
# worker-meta. Built on the box itself; nothing is pushed to a registry (D18).
# Base images are pinned by their multi-arch index digest (D23); the digests, the Chrome version and the
# off-box copies are recorded in docs/operations.md. The browser target is linux/amd64 only (Chrome .deb).
#
# Stage order keeps the slow Chrome layer independent of the code: `xvfb` and `chrome` come from `base`,
# the code is installed in `build`, and the final targets copy the result.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS base
COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

FROM base AS build
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages packages
COPY apps apps
# ponytail: one install for both targets, so the http image also carries the patchright wheel (no
# browser binary); split the lock per target if image size ever matters.
RUN uv sync --frozen --no-dev --all-packages

# Graceful stop (contracts §7.7): tini is PID 1 WITHOUT -g and signals only its child. With -g, or with
# `xvfb-run` as that child, a stop kills Xvfb and the worker at once and `stop_grace_period` is never used.
# xvfb-exec runs Xvfb in its own session, forwards SIGTERM to the worker only and stops Xvfb after it.
# tests/browser/test_graceful_stop.py proves this against the real image.
# xauth is only needed by `xvfb-run`, which tests/browser/test_graceful_stop.py uses as its negative control.
FROM base AS xvfb
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=755 deploy/xvfb-exec /usr/local/bin/xvfb-exec
ENTRYPOINT ["tini", "--", "/usr/local/bin/xvfb-exec"]

# Google Chrome from a versioned .deb checked by sha256 (v1.3 #23): `apt install google-chrome-stable` would
# float. Google's pool serves only the current stable, so an archived copy in deploy/vendor/ (gitignored;
# copy it from ~/Archives/relay-images/) is used first when present, with no download. Either way the
# sha256 is checked. patchright drives it as channel="chrome" from /opt/google/chrome.
# A newer Chrome is a deliberate edit of the two ARGs + docs/operations.md.
FROM xvfb AS chrome
ARG CHROME_VERSION=154.0.8037.92-1
ARG CHROME_SHA256=69e3f6ac0a4811f4689ca23f1fd5cc5b2c01550881928b3e196a97131165c4a2
ARG CHROME_URL=https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}_amd64.deb
COPY deploy/vendor/ /tmp/vendor/
RUN deb="/tmp/vendor/google-chrome-stable_${CHROME_VERSION}_amd64.deb"; \
    if [ ! -f "$deb" ]; then deb=/tmp/chrome.deb; curl -fsSL -o "$deb" "$CHROME_URL"; fi \
    && echo "${CHROME_SHA256}  $deb" | sha256sum -c - \
    && apt-get update \
    && apt-get install -y --no-install-recommends "$deb" \
    && rm -rf /tmp/chrome.deb /tmp/vendor /var/lib/apt/lists/*

FROM base AS http
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["tini", "--"]
CMD ["relay", "serve"]

FROM chrome AS browser
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
WORKDIR /app
CMD ["relay", "worker", "--provider", "dola"]
