# relay

One API for dola, vibes and meta. It replaces dola2api_v4, vibesai2api_v2 and meta2api.

The authority is `../plans/260930-1635-relay-platform/contracts.md`. The phase files there say what gets built next.

## Develop

```bash
uv sync --all-packages
./scripts/check.sh          # the commit gate: ruff + browser-less tests against throwaway Postgres 16 and MinIO (own compose project, ephemeral ports per run)
./scripts/check.sh --full   # phase gates: + scripts/mutate.py + the browser tier on the box (check-in-container.sh)
```

`check.sh` needs Docker and ffmpeg; it needs no Chrome. `--full` also needs ssh to the box (`RELAY_BOX`), where the
`browser` image runs the Chrome tests on a virtual display (Xvfb, started by `deploy/xvfb-exec`). Pinned images, the Chrome version and the off-box copies are
in `docs/operations.md`.

The repo is local only: it has no remote, and nothing is pushed until the owner asks.

## Layout

| Path | What |
|---|---|
| `packages/relay-core` | keys, wallets, jobs, account pools, API, admin, media; no browser and no provider imports. Phase 1 is built: `db/` (migrations 0001-0010, `relay migrate`), billing, keys, settings, errors, redact, storage, crypto, net, txguard |
| `packages/relay-browser` | the only package that may import patchright |
| `packages/provider-{dola,vibes,meta}` | pure modules (the API imports them) and `engine/` + `e2e.py` (worker only) |
| `apps/relay` | the `relay` command: `serve`, `worker --provider X`, `migrate`, `admin`, `e2e --provider X` |
| `deploy/` | Dockerfile (`http`, `browser`), `compose.yml` for the box (per-service env files; profiles `app`, `dola`, `vibes`, `meta`; every command needs `GIT_SHA`), `compose.check.yml` for the checks |
| `fixtures/` | redacted captures made by `scripts/redact-fixtures.py` |
| `scripts/` | `check.sh`, `check-in-container.sh`, `restore-images.sh` (pinned images from the off-box tarballs), `mutate.py` (runs every `@mutation` marker in a private copy of the tree; `--only PATTERN`, one run at a time), `redact-fixtures.py` |
| `tests/` | the guards (import boundaries, no `.route(`, launch only in `open_session`, English identifiers, no `_demo(`, no provider name or provider code in the core outside its registry module, no task started inside a `tx()` block), the deploy isolation test, and the `browser` tier |
