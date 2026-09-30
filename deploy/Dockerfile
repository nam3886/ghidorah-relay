# Two targets (contracts §3): `http` for api, migrate and worker-vibes; `browser` for worker-dola and
# worker-meta. Built on the box itself; nothing is pushed to a registry (D18).
FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages packages
COPY apps apps
# ponytail: one install for both targets, so the http image also carries the patchright wheel (no
# browser binary); split the lock per target if image size ever matters.
RUN uv sync --frozen --no-dev --all-packages

FROM base AS http
CMD ["relay", "serve"]

FROM base AS browser
# Xvfb gives the headful Chrome a screen; xauth is needed by xvfb-run; tini reaps Chrome's children.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth tini \
    && rm -rf /var/lib/apt/lists/* \
    && patchright install --with-deps chrome
ENTRYPOINT ["tini", "--", "xvfb-run", "-a", "--server-args=-screen 0 1440x900x24"]
CMD ["relay", "worker", "--provider", "dola"]
