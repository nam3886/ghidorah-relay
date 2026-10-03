# Operations

Pinned inputs of the relay images, the worker and the restore runbook. Contracts §3, §7.7, §15, §17 (D23, v1.3 #23).
Plan phase 7 extends this file with the deploy and backup runbooks and drills the restore.

## Image digests

Pinned by the multi-arch **index** digest (`docker buildx imagetools inspect <ref>`), so the same pin resolves to
the right image on the Mac (arm64) and on the box (amd64). Recorded 2026-10-01.

| Image | Where it is pinned | Index digest |
|---|---|---|
| `postgres:16-alpine` | `deploy/compose.yml`, `deploy/compose.check.yml` | `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` |
| `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` | `deploy/compose.yml`, `deploy/compose.check.yml` | `sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372` |
| `python:3.13-slim` | `deploy/Dockerfile` | `sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b` |
| `ghcr.io/astral-sh/uv:0.9` | `deploy/Dockerfile` | `sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4` |

`tests/test_deploy.py` fails on any image in these files that has no digest (images built here, `relay-*`, excepted).
A pin moves only by a deliberate edit of every place above, of `scripts/restore-images.sh` and of this table.

dola2api_v4 keeps using its own cached `quay.io/minio/minio` image. relay never shares it.

## Google Chrome

The `browser` image installs Chrome from a versioned `.deb` checked by sha256 (`ARG CHROME_VERSION`,
`ARG CHROME_SHA256` in `deploy/Dockerfile`), not from the floating apt repository.

| | |
|---|---|
| Version | `154.0.8037.92-1` (stable, amd64) |
| sha256 | `69e3f6ac0a4811f4689ca23f1fd5cc5b2c01550881928b3e196a97131165c4a2` |
| URL | `https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_154.0.8037.92-1_amd64.deb` |

`scripts/check-in-container.sh` prints the installed version on every run. Upgrading Chrome = new version and
sha256 in the Dockerfile and here, then `scripts/check.sh --full`.

The Chrome layer sits before the code in the Dockerfile (stages `xvfb` and `chrome`), so a code change does not
rebuild or re-download it. Google's pool serves only the current stable: when this version is superseded, put the
archived `.deb` in `deploy/vendor/` (gitignored) and build as usual. A file named
`google-chrome-stable_<CHROME_VERSION>_amd64.deb` there is used instead of the download, and its sha256 is still
checked:

```bash
cp ~/Archives/relay-images/google-chrome-stable_154.0.8037.92-1_amd64.deb deploy/vendor/
```

## Off-box copies (Mac, `~/Archives/relay-images/`)

The pool URL of an old Chrome or an upstream image can disappear (the MinIO images already did, 30/09). These files
are the fallback; `shasum -a 256` proves they are the pinned content. Each image tarball is the **full multi-arch
index saved by digest** (`docker save <ref>@<index digest>`, amd64 and arm64 content), so the pinned digest survives
a restore. A tarball saved with `--platform` would get a new index digest and would not satisfy the pins.

| File | sha256 |
|---|---|
| `google-chrome-stable_154.0.8037.92-1_amd64.deb` | `69e3f6ac0a4811f4689ca23f1fd5cc5b2c01550881928b3e196a97131165c4a2` |
| `postgres-16-alpine.tar` | `35561e195340951c8e742682ab2bebc58e93efb9b001a10df98473882e397d27` |
| `pgsty-minio-RELEASE.2026-08-04T00-00-00Z.tar` | `74ece87ab37e7052ea6eb8067245c9a9f8895b477e1038ce0eeda0dffc68ee7a` |
| `python-3.13-slim.tar` | `fb95760e65cf783d6a73a4eb67313855fc1d241bd87e6b0cb67078073703d2e5` |
| `uv-0.9.tar` | `47cd642d6387c60a768f7b31fad059d27bdc3940295a85276864971842c509a2` |

### Restore with no registry

```bash
scripts/restore-images.sh [DIR]     # DIR defaults to ~/Archives/relay-images; ends "RESTORE: GREEN"
docker compose -f deploy/compose.yml ... up --pull never
docker build --pull=false ...
```

`docker load` leaves the image nameless, and a nameless image does not satisfy `name@sha256:...`. The script gives
each loaded index ONE relay-owned tag in its own repository, `<repo>:relay-pin-<first 8 hex of the digest>` (for
example `postgres:relay-pin-721873c3`), and then checks that the exact pinned reference resolves. The pinned
references in the compose files and the Dockerfile are not changed. It never creates, moves or removes a floating
tag another project may use (`postgres:16-alpine`, `python:3.13-slim`, ...), and it refuses when a relay-pin tag
already points at a different image. It needs a daemon with the containerd image store (Docker Desktop and
Docker 29 default) and fails loudly otherwise.

Drilled on the Mac on 2026-10-01 (cycle 2): the four floating tags and the pinned references removed, then
`restore-images.sh` printed `RESTORE: GREEN` and created no floating tag; `docker compose -f
deploy/compose.check.yml up --no-start --pull never` created both containers; a build of
`FROM python@sha256:... + COPY --from=uv@sha256:...` ran with `--pull=false`; a relay-pin tag pointed at another
image made the script exit 1 with "refusing to retag". **The restore has not been run on the box.** The box drill
(which image store it uses, and that dola2api_v4's images stay untouched) happens in plan phase 7; the script is
safe to run there because it adds only `relay-pin-*` tags.

## Object store exit

relay's MinIO is `pgsty/minio` (a single-maintainer community build; upstream MinIO archived its repository in
February 2026 and no longer publishes images). It is pinned by digest and saved off the box. The documented exit is
**Garage** (or SeaweedFS), which serves the same S3 subset relay uses (the `minio` SDK: put, get, presigned or
streamed reads, list, delete). Leave when there is no pgsty release for 6 months, an unpatched CVE, or a fresh-box
pull fails (contracts §17).

## Compose, profiles and credentials

- Every compose command needs `GIT_SHA` (and, for postgres and minio, their two passwords) in the environment or in
  `deploy/.env`: compose interpolates the services of inactive profiles too. `export GIT_SHA=$(git rev-parse --short HEAD)`.
- Profiles: `app` = `migrate` and `api`; `dola`, `vibes`, `meta` = the provider workers. A bare `docker compose up`
  starts only Postgres and MinIO. `docker compose --profile app --profile dola up -d` starts the api and the dola
  worker; enabling a provider later changes no file.
- Every service has its own env file (`deploy/<service>.env`, gitignored), copied from `deploy/<service>.env.example`
  (variable names only). There is no shared `env_file`.
- `CREDENTIAL_SEAL_PUBLIC_KEY` is in `api.env` only. Any `*PRIVATE_KEY*` variable (`CREDENTIAL_SEAL_PRIVATE_KEY`
  today) may appear only for `worker-<provider>` services; `api` and `migrate` never see one (I20).
  `tests/test_deploy.py` enforces both.
- `migrate.env` holds `DATABASE_URL` (the owner role), `BILLING_TZ` and the passwords `relay_api` and `relay_worker`
  get (`RELAY_API_DB_PASSWORD`, `RELAY_WORKER_DB_PASSWORD`). `relay migrate` applies the SQL migrations, sets those
  passwords, and pins `BILLING_TZ` on the first run (UTC when unset); a service whose `BILLING_TZ` differs from the
  pin refuses to start. `MINIO_REGION` is read by every service that talks to MinIO (api and the workers).
- Images are named `relay-<service>:${GIT_SHA}`, so a rollback is a retag.

## Graceful stop of the browser workers

`docker stop` must give an in-flight submit the whole `stop_grace_period` (780 s = the 720 s video submit timeout
plus a minute). `tini -g`, or `xvfb-run` as tini's child, cannot do that: the signal kills Xvfb and the worker at
once and the container ends in about a second. The browser image therefore runs `tini -- xvfb-exec <worker>`
(`deploy/xvfb-exec`): tini is PID 1 without `-g`, the script keeps Xvfb in its own session, forwards SIGTERM and
SIGINT to the worker only, waits for it and then stops Xvfb.

A container killed without a grace period (SIGKILL after the grace, a daemon restart, a reboot) is restarted with
the same writable `/tmp`, so the dead Xvfb's lock and socket are still there. `xvfb-exec` removes them unless a live
Xvfb owns them, starts Xvfb, waits until the display socket accepts a connection and Xvfb is alive, and otherwise
exits non-zero with a message: the worker never starts without a display.

`tests/browser/test_graceful_stop.py` proves all of this on the real image: a graceful stop, a kill followed by a
start, and a negative control that runs the old entrypoint (`tini -g -- xvfb-run`, which is why `xauth` stays in the
image) and must be cut off in under 2 s, so the timing assertion cannot pass vacuously.

## Gates

- `scripts/check.sh`: the commit gate. Browser-less, on the Mac. Ends `CHECK: GREEN`. Each run has its own compose
  project (`relay-check-<epoch>-<pid>`) and ephemeral loopback ports, so parallel runs do not share services.
- `scripts/check.sh --full`: adds `scripts/mutate.py`, the 200-seed `scripts/soak.py` and the browser tier, which
  `scripts/check-in-container.sh` runs on the box (`RELAY_BOX`, default `phuongnam@192.168.18.227`). Per run, with a token `<epoch>.<pid>`: temp dir
  `/tmp/relay-check.<token>`, image `relay-check-browser:<token>` (label `relay.check=<token>`), compose project
  `relay-check-<token>`, container `relay-check-run-<token>` on that project's network (no host networking). It runs
  the Chrome tests in the image, then the three graceful-stop tests (stop, kill-then-start, negative control) from the Mac through `DOCKER_HOST=ssh://<box>`. It
  removes what carries its token on exit and never prunes or touches another project.
- `scripts/check.sh --full --local`: the same without the box. The browser tier is skipped and the output says
  `browser tier: SKIPPED (--local ...)`. For phases that change no browser code or image.
- `scripts/check.sh --soak [--seed N] [--verbose]`: only the soak, on its own database and bucket. `--soak --seed N
  --verbose` replays one seed (a failing seed prints its number). It is not a gate and never prints `CHECK: GREEN`.
- `CHECK: GREEN` means the whole gate ran. A run that left something out ends `CHECK: PARTIAL (...)` with the reason
  (`scripts/check-verdict.sh`): `MUTATE_ONLY` set, `SOAK_SEEDS` under 200 (`0` skips the soak), or pytest arguments
  that narrow the run (`mutate: PARTIAL` is also printed when `MUTATE_ONLY` is set). `SOAK_SEEDS` must be a whole
  number. Only the last line `CHECK: GREEN` allows a commit.
- `scripts/mutate.py [ROOT] [--only PATTERN ...]`: mutates in a private copy of the tree, under an exclusive lock
  (`.mutate.lock`; a second run exits 2). A marked test is first run unmutated: one that fails then is reported as
  "baseline not green", not as a kill. DB tests need the check services, so run it through `check.sh --full`.
  Every `@mutation` carries `expect="..."`, a substring of the failure the mutation must cause (the test's own
  assertion text, not a helper timeout); `tests/test_mutate.py` fails when a marker has none.

## The worker (`relay worker --provider X`)

One process per provider (`dola`, `vibes`, `meta`; the compose services `worker-<provider>`). It loads
`relay_<p>.spec.SPEC` and `relay_<p>.engine.build_engine`, then runs these loops as tasks of one process:

| Loop | Does |
|---|---|
| claims | takes the oldest queued job across pools (D43) and submits it |
| poller | polls running jobs (only for providers with `has_poll`) |
| fetcher | downloads finished output into MinIO |
| reconciler | proves `unknown` jobs against the account (only for providers with `has_reconcile`) |
| reaper | requeues or fails jobs whose lease expired; the daily ledger drift pass |
| commands | runs queued worker commands, the account checks, the title sweep and the catalog refresh |

Environment (read at boot; the full list is in the per-service env files):

| Variable | Meaning |
|---|---|
| `DATABASE_URL`, `BILLING_TZ`, the credential private keys, MinIO settings | required; a missing or malformed value exits 2 before any connection |
| `WORKER_ID` | default `worker-<provider>`. Names the heartbeat row, so it must be **unique per worker process** and contain **no `/`** (else exit 2). Two processes sharing an id share one row. Each boot appends its own `/<suffix>` to form the lease owner |
| `WORKER_GRACE_S` | default 720. One deadline for the whole stop; keep it below the compose `stop_grace_period` (780 s) so Docker never kills first |

Start: the worker refuses (exit 2) when `BILLING_TZ` differs from the value pinned by `relay migrate`.

Exit codes: 0 clean stop; 1 after 5 consecutive unexpected errors in the claim loop, or a loop that died; 2 refused
to start. A submit stopped by the core's own timeout does not count toward the 5 (D42). Docker restarts the
container on 1.

Graceful stop (SIGTERM or SIGINT; the entry script is under "Graceful stop of the browser workers"): no new claim,
check or command starts; in-flight submits finish or are fenced inside `WORKER_GRACE_S`; the other loops finish the
item they are on within what is left of the same deadline; leases are released, except that of a job still
`submitting` (the reaper moves it); a command that was running is failed so the admin can run it again. A job
requeued by a graceful stop is claimed again at once by another worker.

### Drain mode

Setting `<p>.claims_enabled` (default true). While false, that provider's worker claims no job, runs no account
check and no command, and does no catalog maintenance. **Still running:** poller, fetcher, reconciler, reaper, the
title sweep and webhooks, so open jobs finish, fail, refund and notify. Rollback of a provider go-live is drain mode
(there is no fallback system, D36).

**Not wired yet (phase 3):** `relay provider drain|resume X` and `relay jobs restore-mark --dump-time T` are store
functions today (`relay_core.jobs.ops.drain`, `resume`, `restore_mark`); the `relay` command does not have them yet.
Until phase 3, drain mode is the setting `<p>.claims_enabled` itself.

## Restore runbook (contracts §7.7; drilled in phase 7, not yet on the box)

Order matters: `restore_mark` writes whole tables and a running worker would claim jobs the dump knows nothing about.

1. Restore the database from the dump and start the api. Set the global setting `media_orphan_sweep` to `false` first
   (restoring MinIO from the nightly mirror happens later; a sweep now would delete files whose rows are not back).
2. Start **no worker**.
3. Run restore-mark with the dump time `T`. It refuses (`WorkersRunning`) while any worker heartbeat is fresher than
   45 s. It locks the heartbeat table, so a worker that boots meanwhile has its claim loop wait, but its other loops
   do not: still start none. It marks every non-terminal job created before `T` plus the longest submit timeout as
   `unknown` (never `queued`; reconcile proves each one), except a `queued` job never claimed. A job that was claimed,
   requeued and has no account fails with refund at its render deadline and is never re-sent (D41). Queued commands
   fail, `left_n` is rebuilt from reported balances, every account is due for a check.
4. Start the workers in drain mode (`<p>.claims_enabled` false), let the reconciler and the checks settle, then
   `resume`.
5. Reconcile MinIO and the rows, then set `media_orphan_sweep` back to `true`.

## What an operator must know

- **A job that keeps killing its worker fails (D46).** Each time the reaper requeues an unsent job whose worker died
  it counts an attempt; at the provider's `max_attempts` the job fails with refund. The count covers every unsent job
  the dead worker held, so an innocent job sharing every crash fails and is refunded with it.
- **Upstream completed means the account keeps the charge (D47).** A failed job whose order upstream finished (for
  example the fetch failed) keeps its account-pool charge, because the upstream account spent the credit. The client's
  key is refunded as for every failed job.
- **A dead proxy rests the account (D44).** A failed proxy probe rests the account on every pool for the provider's
  proxy rest, 30 minutes (1 800 s) by default (`Caps.proxy_rest_s`); no browser opens through it.
- **The account check interval is a provider constant** (`Caps.check_interval_s`, 21 600 s by default), not a setting.
- **Admin account actions are worker commands** (wake, check, check all, proxy re-check, delete): the api queues
  them, the provider's worker runs them, so they wait while the worker is drained or stopped. A command aborted by a
  database deadlock or serialization failure is re-queued, a bounded number of times; other errors fail it.
- **Browser shares stay reserved (D45):** reconcile up to 2 and check plus command 1, even when idle. More submit
  slots mean a higher `<p>.browser_slots`, within the env ceiling.
