# Two targets: `http` for api, migrate, worker-vibes, worker-flow, worker-gemini and worker-muse; `browser` for worker-dola,
# worker-meta and worker-qwen (its Chrome only mints qwen's anti-bot pair). Built on the box itself; nothing is
# pushed to a registry.
# Base images are pinned by their multi-arch index digest; the digests, the Chrome version and the
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
COPY apps/relay apps/relay
# ponytail: one install for both targets, so the http image also carries the patchright wheel (no
# browser binary); split the lock per target if image size ever matters.
RUN uv sync --frozen --no-dev --all-packages

# Graceful stop: tini is PID 1 WITHOUT -g and signals only its child. With -g, or with
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

# Google Chrome from a versioned .deb checked by sha256: `apt install google-chrome-stable` would
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

# The admin web (Vue + Vite), built only on demand (`docker build --target web-...`, driven by scripts/web.sh): never
# part of the http or browser chain, so those images carry no Node and no web source. The layout under /web mirrors
# the repo, so the app builds with the same relative paths as in the tree and the output is the same bytes on every
# run (Linux amd64 on the box is the canonical platform). Dependencies come from the lockfile alone: `npm ci`, and
# apps/admin-web/.npmrc turns install scripts off.
FROM node:24-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS web-base
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false CI=1
WORKDIR /web/apps/admin-web
COPY apps/admin-web/package.json apps/admin-web/.npmrc apps/admin-web/package-lock.jso[n] ./

# `web.sh lock`: the lockfile regenerated from package.json, no scripts run.
FROM web-base AS web-lock
RUN npm install --package-lock-only --ignore-scripts
FROM scratch AS web-lock-out
COPY --from=web-lock /web/apps/admin-web/package-lock.json /

FROM web-base AS web-deps
RUN npm ci

# The whole app, on top of its dependencies. This is also what the TypeScript mutation runs use (scripts/mutate.py):
# the mutated src/ and tests/ are mounted over these, so no rebuild is needed.
FROM web-deps AS web-src
COPY apps/admin-web ./

# `web.sh check`: a build first (it generates components.d.ts and auto-imports.d.ts in this scratch copy, so check
# never depends on the committed ones), then types, lint, unit tests. A failing step fails the build.
FROM web-src AS web-check
ENV NO_COLOR=1 FORCE_COLOR=0
RUN mkdir -p /web/packages/relay-core/src/relay_core/admin/static \
    && npm run build \
    && npm run typecheck && echo "WEBCHECK vue-tsc: no type errors" \
    && npx eslint . && echo "WEBCHECK ESLint: no problems" \
    && npm run format:check && echo "WEBCHECK Prettier: all files formatted" \
    && npm run test

# `web.sh test SPEC...`: only the given Vitest spec files, no build, types or lint.
FROM web-src AS web-test
ARG SPECS
ENV NO_COLOR=1 FORCE_COLOR=0
RUN npx vitest run $SPECS

# `web.sh format`: Prettier over the app; the result (without node_modules) is what web.sh compares with the tree.
FROM web-src AS web-format
RUN npm run format && mkdir /out && tar -C /web/apps/admin-web --exclude=./node_modules -cf - . | tar -C /out -xf -
FROM scratch AS web-format-out
COPY --from=web-format /out /

# `web.sh build` and `verify`: the dist and the two generated declaration files.
FROM web-src AS web-build
RUN mkdir -p /web/packages/relay-core/src/relay_core/admin/static && npm run build
FROM scratch AS web-out
COPY --from=web-build /web/packages/relay-core/src/relay_core/admin/static/app /static/app
COPY --from=web-build /web/apps/admin-web/components.d.ts /web/apps/admin-web/auto-imports.d.ts /
