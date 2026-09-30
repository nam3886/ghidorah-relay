# relay

One API for dola, vibes and meta. It replaces dola2api_v4, vibesai2api_v2 and meta2api.

The authority is `../plans/260930-2221-relay-platform/contracts.md`. The phase files there say what gets built next.

## Develop

```bash
uv sync --all-packages
./scripts/check.sh        # ruff + all tests against throwaway Postgres 16 and MinIO; the only commit gate
```

`check.sh` needs Docker, ffmpeg and Google Chrome (`uv run patchright install chrome`).

The repo is local only: it has no remote, and nothing is pushed until the owner asks.

## Layout

| Path | What |
|---|---|
| `packages/relay-core` | keys, wallets, jobs, account pools, API, admin, media; no browser and no provider imports |
| `packages/relay-browser` | the only package that may import patchright |
| `packages/provider-{dola,vibes,meta}` | `spec.py` (pure; the API imports it) and `engine/` (worker only) |
| `apps/relay` | the `relay` command: `serve`, `worker --provider X`, `migrate`, `admin` |
| `deploy/` | Dockerfile (`http`, `browser`), `compose.yml` for the box, `compose.check.yml` for the check |
| `fixtures/` | redacted captures made by `scripts/redact-fixtures.py` |
| `tests/` | the guards: import boundaries, no `.route(`, browser launch only in `open_session`, English identifiers |
