# Ghidorah Relay

One HTTP API for AI image and video generation. Relay takes jobs from API clients, runs them on provider accounts
(dola, vibes, meta) through headless Chrome sessions, bills each API key per job, and stores the results.

- **OpenAI- and Gemini-compatible endpoints**: `/v1/chat/completions`, `/v1/images/generations`,
  `/v1/video/generations`, `/v1beta/models/{model}:generateContent` and their status, content and model routes.
- **Per-key wallets** with day limits, refunds on provider failures, and an allow-list of models.
- **An account pool per provider** with health checks, cooldowns, proxies and capacity tracking.
- **An admin web app** (`/manage`) for jobs, accounts, API keys, request logs, alerts, prompt frames and settings.
- **API reference** at `/reference` (OpenAPI at `/openapi.json`), liveness at `/healthz`.

## Architecture

```
clients ──HTTP──▶ api (relay serve) ──▶ Postgres (jobs, keys, wallets, accounts, settings)
                     │                 └▶ MinIO (images, videos)
                     └─ /manage admin app
workers (relay worker --provider X) ──▶ claim jobs ──▶ Chrome session on a provider account ──▶ fetch results
```

The api never opens a browser. Each provider has its own worker process; workers claim jobs from Postgres, submit them
in Chrome, poll and fetch the outputs, and settle the charge or the refund.

## Repository layout

| Path | Contents |
|---|---|
| `packages/relay-core` | Keys, wallets, jobs, account pools, the public and admin API, media storage, migrations. No browser and no provider code. |
| `packages/relay-browser` | Chrome session handling (the only package that drives a browser). |
| `packages/provider-dola`, `provider-vibes`, `provider-meta` | One package per provider: request specs, reply classification, and the worker engine. |
| `apps/relay` | The `relay` command-line tool. |
| `apps/admin-web` | The admin web app (Vue 3, Element Plus, Vite). Its build is committed under `packages/relay-core/src/relay_core/admin/static/app/`. |
| `deploy/` | Dockerfile, `compose.yml` for production, `compose.check.yml` for test runs, env file templates. |
| `fixtures/` | Redacted provider captures used by the tests. |
| `scripts/` | Checks, mutation testing, the soak test, remote test runs, fixture redaction. |
| `tests/` | Repository guards, admin end-to-end tests and browser tests. Package tests live in `packages/*/tests`. |
| `docs/` | Operations runbook and error codes. |

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker (tests use throwaway Postgres 16 and MinIO containers; the admin web app builds in a container)
- ffmpeg
- SSH access to the build and test host for browser tests and admin web checks (`scripts/box.sh`, `scripts/web.sh`)

## Getting started

```bash
uv sync --all-packages     # install every workspace package
uv run relay --help        # the command-line tool
```

Run the services with Docker Compose. Copy each `deploy/<service>.env.example` to `deploy/<service>.env`, fill in the
values, then:

```bash
cd deploy
export GIT_SHA=$(git rev-parse --short HEAD)
docker compose -p relay up -d                                   # Postgres and MinIO
docker compose -p relay --profile app run --rm migrate          # roles and migrations
docker compose -p relay --profile app up -d api                 # the API and the admin app
docker compose -p relay --profile dola up -d worker-dola        # a provider worker
```

Create the first admin user and an API key:

```bash
docker compose -p relay --profile app run --rm api relay admin create-user <name>
docker compose -p relay --profile app run --rm api relay key create --label <label> --wallet dola:video=10
```

See [docs/operations.md](docs/operations.md) for the full deploy order, drain mode and rollback.

## Command-line tool

| Command | Purpose |
|---|---|
| `relay serve` | Run the public and admin API |
| `relay worker --provider <name>` | Run the worker of one provider |
| `relay migrate` | Apply the SQL migrations |
| `relay admin create-user <name>` | Create an admin user (password read from the terminal) |
| `relay keygen` | Generate a credential key pair |
| `relay key create` / `relay key disable <id>` | Create or disable an API key |
| `relay provider drain <name>` / `resume <name>` | Stop or restart claiming new jobs for a provider |
| `relay jobs restore-mark --dump-time <iso>` | Mark jobs after a database restore |
| `relay e2e --provider <name>` | Live end-to-end run against a provider |

## Testing

| Command | What it runs |
|---|---|
| `scripts/check.sh` | Lint (ruff), type checks (mypy) and every non-browser test against throwaway Postgres and MinIO |
| `scripts/check.sh <paths>` | The same, limited to the given tests |
| `scripts/check-in-container.sh <paths> [-k EXPR]` | Browser tests (admin end-to-end, Chrome sessions) in the browser image; `-k` picks one page's tests; refuses a stale admin bundle (run `web.sh build`) |
| `scripts/web.sh check` | Admin web app: type check, ESLint, Prettier and Vitest |
| `scripts/web.sh test <spec...>` | Only the given Vitest spec files (no build, types or lint), for fast iteration |
| `scripts/web.sh build` | Build the admin web app into `relay_core/admin/static/app/` |
| `scripts/check.sh --full` | The full gate: all of the above, every mutation marker and the 200-seed soak test |

Tests and builds run on the test host through `scripts/box.sh` (for example
`scripts/box.sh --tree <folder> -- scripts/check.sh`). A full run ends with `CHECK: GREEN`; a narrowed run ends with
`CHECK: PARTIAL`. Every `@mutation` marker on a test must make that test fail when its code change is applied
(`scripts/mutate.py`).

## Documentation

- [docs/operations.md](docs/operations.md): configuration, deploy, drain mode, rollback, gates and the browser setup
- [docs/error-codes.md](docs/error-codes.md): every error code the API returns (generated)

## Contributing

- One branch per change, merged into `main` after its checks pass.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).
- Never commit secrets: env files under `deploy/` are ignored, and fixtures are redacted.
