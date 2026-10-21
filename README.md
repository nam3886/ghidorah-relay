# relay

One API for dola, vibes and meta. It replaces dola2api_v4, vibesai2api_v2 and meta2api.

The authority is `plans/260930-1635-relay-platform/contracts.md`. The phase files there say what gets built next.

## Develop

```bash
uv sync --all-packages
./scripts/check.sh                  # the commit gate: ruff + mypy (providers.py) + browser-less tests against throwaway Postgres 16 and MinIO (own compose project, ephemeral ports per run)
./scripts/check.sh --full           # phase gate: + every @mutation (scripts/mutate.py) + the 200-seed soak (scripts/soak.py) + the browser tier on the box
./scripts/check.sh --full --local   # the same without the box: the browser tier is skipped, and the output says so
./scripts/check.sh --soak --seed 13 --verbose   # replay one soak seed (its own database and bucket); never prints CHECK: GREEN
```

`check.sh` needs Docker and ffmpeg; it needs no Chrome. Commit only after the last line reads `CHECK: GREEN`.
`--full` (without `--local`) also needs ssh to the box (`RELAY_BOX`), where the `browser` image runs the Chrome tests on
a virtual display (Xvfb, started by `deploy/xvfb-exec`). Use `--local` for a phase that changes no browser code.

A run that covered less than the gate ends `CHECK: PARTIAL (reason)` and is not a commit gate: `MUTATE_ONLY="test_x"`
(mutate only those tests or files), `SOAK_SEEDS` under 200 (`0` skips the soak), or any pytest argument
(`./scripts/check.sh tests -q`). Every `@mutation` marker carries `expect=`; the details are under Gates in
`docs/operations.md`. Pinned images, the Chrome version and the off-box copies are in the same file.

The repo is local only: it has no remote, and nothing is pushed until the owner asks.

## Layout

| Path | What |
|---|---|
| `packages/relay-core` | keys, wallets, jobs, account pools, API, admin, media; no browser and no provider imports. Built through phase 2: `db/` (migrations 0001-0011, `relay migrate`), billing, keys, settings, errors, redact, storage, crypto, net, txguard, and `jobs/` (claim loop, poller, fetcher, reconciler, reaper, command runner, `worker.py`, `ops.py`) |
| `packages/relay-browser` | the only package that may import patchright |
| `packages/provider-{dola,vibes,meta}` | pure modules (the API imports them) and `engine/` + `e2e.py` (worker only) |
| `apps/relay` | the `relay` command. Wired: `worker --provider X`, `migrate`. `serve`, `admin` and `e2e` print "not built yet" (exit 2); `restore-mark` and `provider drain|resume` do not exist yet (phase 3) |
| `deploy/` | Dockerfile (`http`, `browser`), `compose.yml` for the box (per-service env files; profiles `app`, `dola`, `vibes`, `meta`; every command needs `GIT_SHA`), `compose.check.yml` for the checks |
| `fixtures/` | redacted captures made by `scripts/redact-fixtures.py` |
| `scripts/` | `check.sh` (+ `check-verdict.sh`), `check-in-container.sh`, `soak.py` (seeded chaos workload), `restore-images.sh` (pinned images from the off-box tarballs), `mutate.py` (runs every `@mutation` marker in a private copy of the tree; `--only PATTERN`, one run at a time), `redact-fixtures.py` |
| `tests/` | the guards (import boundaries, no `.route(`, launch only in `open_session`, English identifiers, no `_demo(`, no provider name or provider code in the core outside its registry module, no task started inside a `tx()` block), the deploy isolation test, and the `browser` tier |
