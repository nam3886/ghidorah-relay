# Operations

Pinned inputs of the relay images, the api and the worker, the `relay` commands, the box runbook and the restore
runbook.
Relay runs on the box and the Mac is for coding only: the Mac wording below that describes runs, accounts or
Chrome on the Mac is history.

## Image digests

Pinned by the multi-arch **index** digest (`docker buildx imagetools inspect <ref>`), so the same pin resolves to
the right image on the Mac (arm64) and on the box (amd64). Recorded 2026-10-01.

| Image | Where it is pinned | Index digest |
|---|---|---|
| `postgres:16-alpine` | `deploy/compose.yml`, `deploy/compose.check.yml` | `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` |
| `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` | `deploy/compose.yml`, `deploy/compose.check.yml` | `sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372` |
| `python:3.13-slim` | `deploy/Dockerfile` | `sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b` |
| `ghcr.io/astral-sh/uv:0.9` | `deploy/Dockerfile` | `sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4` |
| `node:24-slim` | `deploy/Dockerfile` (web stages, on demand) | `sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20` |

`tests/test_deploy.py` fails on any image in these files that has no digest (images built here, `relay-*`, excepted).
A pin moves only by a deliberate edit of every place above, of `scripts/restore-images.sh` and of this table.

Other projects on the box keep using their own cached `quay.io/minio/minio` image. relay never shares it.

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

The same version (without the `-1` package revision) is the constant `PINNED_CHROME_VERSION` in
`relay_browser/session.py`; a test keeps the two equal, so an upgrade edits both. Every browser launch logs the running
Chrome once (`browser Chrome/154.0.8037.92`) and logs a warning that names both versions when the major version or the
build number differs from the pin. It never refuses to run.

**On the Mac, run the pinned build, not the installed Google Chrome.** The installed Chrome updates itself, and
Chrome 155.0.8059.40 restarts its network service a few seconds into a dola load; the restart empties the cookie store
and the page reads as a guest. Use Chrome for Testing 154.0.8037.92 and point `RELAY_CHROME_PATH` at it (the steps are
at the top of `reports/live-run-mac.md`).

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
(which image store it uses, and that images of other projects stay untouched) is still to do; the script is
safe to run there because it adds only `relay-pin-*` tags.

## Object store exit

relay's MinIO is `pgsty/minio` (a single-maintainer community build; upstream MinIO archived its repository in
February 2026 and no longer publishes images). It is pinned by digest and saved off the box. The documented exit is
**Garage** (or SeaweedFS), which serves the same S3 subset relay uses (the `minio` SDK: put, get, presigned or
streamed reads, list, delete). Leave when there is no pgsty release for 6 months, an unpatched CVE, or a fresh-box
pull fails.

## Compose, profiles and credentials

- Every compose command needs `GIT_SHA` (and, for postgres and minio, their two passwords) in the environment or in
  `deploy/.env`: compose interpolates the services of inactive profiles too. `export GIT_SHA=$(git rev-parse --short HEAD)`.
- Profiles: `app` = `migrate` and `api`; `dola`, `vibes`, `meta`, `flow`, `gemini`, `qwen` = the provider workers. A bare `docker compose up`
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

## The browser kit (`relay-browser`)

Used by browser workers only (dola, meta). Everything is read from the environment of the worker process.

| Variable | Meaning |
|---|---|
| `RELAY_CHROME_PATH` | Chrome binary; wins over the provider's `executable_path` and over the default `channel="chrome"` (the installed Google Chrome) |
| `RELAY_BROWSER_HEADLESS` | `1` = headless; unset = headful (on the box under Xvfb). Only the Mac test tier sets it. A provider's own `headless` setting wins |
| `RELAY_PROFILE_ROOT` | Directory holding the profiles. Unset = `<system temp>/relay-profiles` (lost on restart). The compose workers mount a volume at `/profiles`; the `worker-*.env.example` files do not set this variable yet, so set `RELAY_PROFILE_ROOT=/profiles` in `deploy/worker-<provider>.env` or the cache is not kept (see below) |

- **One profile per account and credential generation**: `<root>/<account id>-g<credential_gen>`, plus a
  `<name>.lock` file. A replaced credential gets a new profile and never inherits page state. Account ids must match
  `[A-Za-z0-9_-]{1,64}`.
- **Kept between runs**: the HTTP cache (`Default/Cache`, capped per site, 150 MB), `Default/Code Cache` and
  `Default/Local Storage`. They spare the account's proxy a download of the site's scripts on every send.
- **Removed at open and again at close**: everything else (cookies, session storage, IndexedDB, login data, the
  `Singleton*` links, the open marker). The site's declared token keys are stripped from Local Storage at close,
  with the site's scripts stopped first; if the strip fails, is cancelled or Chrome had to be killed, Local Storage
  is dropped whole, and so it is at the next open after a crash (the `.relay-open` marker is still there).
  Cost: one more download of the site's UI strings, never a leaked token.
- **Sweep**: every claim deletes profiles nobody opened for more than 3 days (credential replaced, account
  deleted), skipping the current one and any whose lock another run holds. A sweep failure is only logged.
- **Network**: Chrome's own background traffic (component updates, account and time checks, push check-in, safe
  browsing) is answered 502 at the local forwarder, so it never reaches an account proxy. Without a proxy
  there is no forwarder, so nothing is refused. `www.google.com` stays open by default (reCAPTCHA needs it).
  Images and fonts are off by launch flags unless the run attaches or reads images.
- **Debug files** (Job drawer, "Debug files"): dola and meta keep an after-send JPEG once the site answered, and a PNG,
  the redacted HTML and the network log (`ui-error-<tag>-*`) when a send fails, gets no receipt, an answer is unclear,
  refused or holds no media. meta's captures of a job get at most 10 s together, and only time left before the 30 s
  close allowance ahead of the submit timeout (an answer never done gets none); a failed capture is only logged.
- **Environment**: Chrome gets an allow-list of variables (`PATH HOME USER LOGNAME LANG LANGUAGE TZ DISPLAY
  XAUTHORITY LC_* XDG_* FONTCONFIG_*`, plus its own `TMPDIR`), never the worker's environment. The worker also
  removes `CREDENTIAL_SEAL_PRIVATE_KEY` from its own environment once it has read the key, so nothing started
  later can read it; `env` inside a running worker no longer shows it, by design.
- **Troubleshooting**
  - Every run on an account gets a throwaway profile (`tmp-*` in the root, slower): another run holds
    `<name>.lock`, or a stale process does. The lock is a `flock`, released when its owner exits, so a crashed
    worker never leaves it stuck; find the holder (`lsof <root>/<name>.lock`) before touching anything. Throwaway
    profiles are deleted at close; one left by a crash is removed by the sweep.
  - Temp dirs: each session makes `relay-chrome-*` in the system temp and removes it at close, also on failure;
    leftovers after a SIGKILL of the worker are safe to delete when no worker runs. On macOS Chrome ignores
    `TMPDIR` (measured), so Chrome's own scratch files land elsewhere there; the Linux behaviour is proven in the
    box container.
  - A Chrome that will not close: the kit waits 30 s, then SIGKILLs the process named by the profile's
    `SingletonLock`, and the forwarder, profile reset, lock and temp dir are still cleaned up. A killed close is
    logged `browser did not close ... killing it` and drops Local Storage whole.
  - `BrowserLaunchError`: Chrome failed to start twice (the profile is wiped and the launch retried once). Check
    `RELAY_CHROME_PATH` and the display (`xvfb-exec` refuses to start the worker without one).

## Gates

- `scripts/check.sh`: the commit gate. Browser-less, on the Mac. Ends `CHECK: GREEN`. Each run has its own compose
  project (`relay-check-<epoch>-<pid>`) and ephemeral loopback ports, so parallel runs do not share services.
- `scripts/check.sh --full`: adds `scripts/mutate.py`, the 200-seed `scripts/soak.py` and the browser tier, which
  `scripts/check-in-container.sh` runs on the box (`RELAY_BOX`, default `phuongnam@192.168.18.227`). Per run, with a token `<epoch>.<pid>`: temp dir
  `/tmp/relay-check.<token>`, image `relay-browser-image:<token>` (the `browser` target plus the dev dependencies,
  built by `scripts/browser-image.sh`, label `relay.browser-image=<token>`), compose project
  `relay-check-<token>`, container `relay-check-run-<token>` on that project's network (no host networking). It runs
  EVERY browser test in the image, the tree mounted read-only: `pytest -m browser`, i.e. what the Mac tier selects
  (`browser and not box`, 154 tests) plus the tests only the box can run (`browser and box`, 3), with a line per test;
  it prints both counts and fails unless exactly that many passed (a skip is a failure) and pytest itself exited 0
  (`scripts/browser-tier.sh`, run in the image under `pipefail`; a collection that fails or does not add up fails the tier). Then the three graceful-stop
  tests (stop, kill-then-start, negative control), which drive the docker CLI, run from the Mac through
  `DOCKER_HOST=ssh://<box>`. It removes what carries its token on exit and never prunes or touches another project.
- **Only some browser tests, in the same image** (a developer or tester working on one area; not a gate):

  ```bash
  scripts/box.sh --tree <folder> -- scripts/check-in-container.sh tests/admin_ui/test_e2e_jobs.py
  scripts/box.sh --tree <folder> -- scripts/check-in-container.sh "tests/admin_ui/test_e2e_shell.py::test_an_unknown_hash_lands_on_jobs"
  ```

  The arguments are test paths or node ids (several allowed); flags are refused (exit 2). The run has the same image,
  token, isolation and teardown as the whole tier, but it skips the exact-count rule and the graceful-stop tests, runs
  only what was named, and ends `browser tier: PARTIAL (narrowed)`. It still fails when nothing is selected (or a path
  does not exist) and when a selected test does not pass. A narrowed run takes the shared `gate.lock` like any other
  run. `scripts/check.sh --full` calls the script with no arguments, so the whole tier is unchanged.
- **Per-test time limit** (whole tier and narrowed runs): `scripts/hang_guard_plugin.py` gives each browser test 300 s
  (`TEST_TIMEOUT_S`) across its setup, body and teardown (SIGALRM; `pytest-timeout` is not a dependency). A test that
  hangs fails with its node id and a traceback ending at the hung line, and the run goes on.
- `scripts/check.sh --full --local`: the same without the box. The browser tier runs on the Mac:
  `pytest -m "browser and not box"` with patchright driving the installed Google Chrome. An unnarrowed run
  that selects no browser test fails (`browser tier: FAILED`); a green one prints `browser tier: GREEN on this Mac`.
  Tests marked `box` prove the image itself (pinned Linux Chrome, Xvfb, the entry script, Chrome's sandbox) and run
  only in the box container (`scripts/check.sh --full`); a local run never covers them.
- `scripts/check.sh --soak [--seed N] [--verbose]`: only the soak, on its own database and bucket. `--soak --seed N
  --verbose` replays one seed (a failing seed prints its number). It is not a gate and never prints `CHECK: GREEN`.
- **Traps seen on the Mac.** Docker's VM clock drifts under load, and MinIO then answers `RequestTimeTooSkewed`: replay
  the failed seeds (`./scripts/check.sh --soak --seed N`). A sleeping Mac stalls a gate: keep it awake (for example
  `caffeinate -i ./scripts/check.sh ...`). A nearly full disk slows everything. A gate stopped with Ctrl-C can leave a
  `relay-check-*` compose project; list with `docker compose ls` and remove it with
  `docker compose -p <name> -f deploy/compose.check.yml down -v`.
- `CHECK: GREEN` means the whole gate ran. A run that left something out ends `CHECK: PARTIAL (...)` with the reason
  (`scripts/check-verdict.sh`): `MUTATE_ONLY` set, `SOAK_SEEDS` under 200 (`0` skips the soak), or pytest arguments
  that narrow the run (`mutate: PARTIAL` is also printed when `MUTATE_ONLY` is set). `SOAK_SEEDS` must be a whole
  number. Only the last line `CHECK: GREEN` allows a commit.
- `scripts/mutate.py [ROOT] [--only PATTERN ...]`: mutates in a private copy of the tree, under an exclusive lock
  (`.mutate.lock`; a second run exits 2). A marked test is first run unmutated: one that fails then is reported as
  "baseline not green", not as a kill. DB tests need the check services, so run it through `check.sh --full`.
  Every `@mutation` carries `expect="..."`, a substring of the failure the mutation must cause (the test's own
  assertion text, not a helper timeout); `tests/test_mutate.py` fails when a marker has none. A baseline that fails
  or times out is run once more: green the second time it counts and is printed `FLAKY` (`check.sh` repeats those
  lines before its verdict); red twice is `FAILED`. A mutant run is never repeated.

## Running checks on the box (`scripts/box.sh`)

Code and git stay on the Mac, and every run (tests, gates, mutation, soak, the browser tier, sweeps) happens
on the box (`RELAY_BOX`, default `phuongnam@192.168.18.227`; Docker, uv and rsync there). The Mac runs no Docker for
relay. The gate commands above are the commands to run there.

```bash
scripts/box.sh -- uv run ruff check .                      # sync, print the fingerprint, run, exit with its status
scripts/box.sh -- MUTATE_ONLY=test_x SOAK_SEEDS=0 ./scripts/check.sh --full
scripts/box.sh --detach gate-1 -- ./scripts/check.sh --full   # returns at once; survives a sleeping Mac
scripts/box.sh --wait gate-1                                  # follow the log, exit with the run's status
scripts/box.sh --log gate-1 | tail                            # the log so far
scripts/box.sh --status gate-1                                # running | exit N
scripts/box.sh --tree dev1 -- uv run ruff check .             # in the folder ~/relay-runs/dev1 instead of ~/relay-runs/relay
scripts/box.sh --status                                       # the folders and the run that holds each
scripts/box.sh --clear dev1                                   # stop the run that holds folder dev1 and free it
```

- **What is synced.** The git top-level of the current directory (so it works from `relay/` and from
  `relay-admin-ui/`) goes to `~/relay-runs/<folder>/` (the tree's directory name, or `--tree NAME`) with `rsync --delete`, so a file changed or deleted on the
  Mac changes or disappears there. Left out: `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`,
  `.mypy_cache`, `.mutate.lock`, `deploy/.env`, `deploy/*.env`, `._*`, `.DS_Store`. The box keeps its own `.venv` per
  folder. A tree that holds a real env file (`.env`, `*.env`; `*.example` is fine) is refused before anything is sent.
  Nothing outside the tree is read, `~/relay-secrets` included.
- **Fingerprint.** After the sync both sides hash the same file list (path, sha256, executable bit; links by target)
  and the run is refused (exit 3) when they differ. The printed `fingerprint=` names the tree a result belongs to.
  `scripts/box.sh --fingerprint` prints the local one without ssh.
- **Environment.** A run gets the box's own environment plus what the command line sets
  (`scripts/box.sh -- VAR=1 cmd`). Nothing of the Mac's shell is sent: ssh and rsync start with a clean environment (HOME, PATH and the agent socket),
  so no `SendEnv` line of any ssh config has anything to send; `MUTATE_CMD` is refused. Inside the run `RELAY_ON_BOX=1`, so `scripts/check-in-container.sh` uses the local Docker.
- **Detached runs.** `--detach NAME` writes `~/relay-runs/logs/NAME.{sh,log,pid,exit}`; the exit file appears when the
  command ends (`--wait` exits with it; 98 if the process died without one). Cutting ssh does not stop the run.
- **One folder per run owner.** A run holds its folder (an `flock` on `~/relay-runs/busy/<folder>.lock`, the run's name
  and process group in `<folder>.info`) from just before the fingerprint is re-checked on the box until it ends,
  detached or not. A sync into a held folder is refused (exit 76) with the name of the run; the receiving rsync itself runs
  under the same lock. The lock goes with the processes that hold it, so a run that ends or is killed frees the folder
  by itself; if a child of a killed run is left holding it, `scripts/box.sh --clear <folder>` finds every process with
  the lock open and stops their process groups (TERM, then KILL), each only after its pid and start time are read
  again just before the signal: a pid that was reused meanwhile is skipped. A detached run counts as running only
  while its pid has the start time stored beside it (`logs/NAME.pid` holds `PID STARTTIME`). `--status` with no name lists the folders as
  `free` or `busy, run=... since=...`. Use your own folder: the tester `--tree tester`, each developer its own name;
  never sync into a folder another session is using. `logs` and `busy` are not folder names. The folder name (`--tree`,
  or the directory name of the tree) is letters, digits, dash and underscore; any other directory name needs `--tree NAME`.
- **A fast gate.** On the box (`RELAY_ON_BOX=1`) `check.sh` runs its slow stages in parallel unless told otherwise:
  `MUTATE_JOBS=24` private copies of the tree in the mutation stage (`scripts/mutate.py --jobs`: one test per unit of
  work, heaviest first; a baseline that fails is repeated alone after the queue is empty; output and exit status as
  in the serial run), `SOAK_JOBS=16` processes in the soak (seed `i` in process `i % 16`, each with its own database and
  bucket; the stage is green only when every requested seed was seen to run exactly once; a failing seed prints `SOAK FAILED seed N (replay: scripts/soak.py --seed N)`), `PYTEST_JOBS=8` xdist workers in the main suite
  (`--dist loadfile`, one database per worker; 16 workers made `test_engine.py` fail once in three runs, 8 did not in
  three). A value on the command line wins; `scripts/box.sh -- MUTATE_JOBS=1 SOAK_JOBS=1 PYTEST_JOBS= ./scripts/check.sh --full`
  is the serial gate. All test sessions of a run cut their database from one migrated template
  (`RELAY_TEST_PG_TEMPLATE`, `packages/relay-core/tests/pgtemplate.py`; the template name holds a hash of the `db`
  package, so a mutated migration gets its own). The check Postgres runs with `max_connections=500` and durability off.
  The browser tests of the mutation stage (`@pytest.mark.browser` and every test of `relay-browser`) run in the browser
  image (`MUTATE_BROWSER_IMAGE`, `scripts/browser-image.sh`) on a queue of their own (`MUTATE_BROWSER_JOBS`, 3).
  The browser tier runs on `BROWSER_TIER_JOBS` xdist workers inside its container (default min(8, CPUs/3); `--dist
  loadfile`, one database per worker cut from the run's template, one Chrome per worker); `BROWSER_TIER_JOBS=1` is the
  serial tier. Its tests that are not about sign-in start signed in (`app_session`: a session row and its token in
  sessionStorage, no login form); the sign-in tests use `app.sign_in`, the real form.
- **One whole-tree gate.** `check.sh --full` with no `MUTATE_ONLY` and no test path takes `~/relay-runs/gate.lock`; a
  second one is refused at once (exit 75), whatever folder it runs in. The command may be wrapped (`bash -c '... ./scripts/check.sh --full 2>&1 | ts'`):
  `box.sh` reads each argument as shell words and ignores what follows the first operator after `check.sh`. Narrowed runs are not locked.
- **CPU budget (only while the production stack runs).** `box.sh` asks the box, before each run or detach, whether a
  container of the compose project `relay` (the production project; exact label match, so the `relay-check-*` stacks never
  count) is up (`RELAY_GATE_CAP=on|off` overrides, default `auto`). The question has two answers, exit 10 (up) and exit 11
  (none); anything else (docker or ssh failing, a non-POSIX login shell on the box, an unexpected answer) caps the run and
  says so on stderr: it fails closed. Until the deploy a run is uncapped, as it always was. While the stack
  runs, every run executes as `taskset -c $RELAY_GATE_CPUS nice -n 10 env <command>`: cores 0-11 (12 of the box's 32) at
  low priority, so the live stack and the other projects keep the rest. `taskset` does not reach the gate's own
  containers, so they are pinned to the same cores (`--cpuset-cpus` from `RELAY_GATE_CPUSET` for the browser-tier runner,
  `scripts/check-in-container.sh`, and for each mutation queue container, `scripts/mutate.py`; `CHECK_CPUSET` for the check
  Postgres and MinIO in `deploy/compose.check.yml`, empty = any core). The whole gate therefore shares one 12-core set
  instead of adding per-container quotas that sum to about 30. Short bursts stay outside it: the BuildKit work that
  builds the browser image (twice per whole-tree gate), `docker-proxy` for the check Postgres port, and the stand-ins
  of the graceful-stop test. The cap is decided once, at the start of a run. `check.sh` then takes smaller job defaults
  (`RELAY_GATE_CAPPED=1`: `MUTATE_JOBS` 12, `SOAK_JOBS` 8, `PYTEST_JOBS` 4 instead of 24, 16, 8); a value you set wins.
  The `box:` line of every run shows `capped=0|1`; a stray `RELAY_GATE_CAP=off` in a shell uncaps silently otherwise.
  The cost and the peak load of a capped gate are in the 1c report.
- **Live window.** While the file `~/relay-deploy/live-window` exists on the box, `box.sh` starts no run (exit 77), once
  before the sync and again just before the command. Reading (`--status`, `--log`, `--wait`, `--sync-only`) still works. The
  operator of a live window (a planned live check, a deadline drill, the load test, the client switch and its first
  hour, a later live measurement) creates the file when the window starts and deletes it when it ends, and writes
  both times in the go-live log. Before creating the file the operator runs `scripts/box.sh --status`: no folder may be
  busy and `gate.lock` must be free; otherwise wait for the run to end, or `--clear` it. The file only stops new runs: a
  gate that started five minutes earlier would run into the window. Outside a window a gate may run on the production
  box, under the budget above.
- **Do not.** The box also runs containers of other projects (`portfolio`, `billiards`): never stop, inspect or prune
  them, and never run `docker system prune` or `docker image prune` there. Relay's scripts only touch what carries
  their own token or label.

## The api (`relay serve`)

One process: the public API, the admin API and `/healthz` on one plain port, plus the webhook sender, housekeeping
(at start, then every 15 min) and the daily ledger check. It runs as `relay_api` and never loads an engine.

Environment (names only; the template is `deploy/api.env.example`):

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | required; the `relay_api` role |
| `CREDENTIAL_SEAL_PUBLIC_KEY` | required; account import and proxy edits seal with it. Missing or malformed refuses |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_REGION` | required object-store settings; the bucket is created at start, by `relay serve` and by `relay worker` alike (a fresh object store has none) |
| `PORT` | keep 8100 (the container mapping is fixed; the compose file publishes it as `API_PORT`, default 8100) |
| `BILLING_TZ` | must equal the value pinned by `relay migrate` (first run pins it, UTC when unset); a different value refuses. Never stored in the settings table |
| `PUBLIC_BASE_URL` | base of the media URLs in job answers; unset = built from the request |
| `TRUSTED_PROXIES` | `X-Forwarded-*` is honoured only when this is set; there is no reverse proxy in the compose file |

A status read of a `waiting` job may run one median query over recent finished jobs of its model and duration: at most one per shape every 5 minutes, in its own transaction with a 500 ms statement timeout; if it fails or is slow the answer is still served (progress by stage, no estimated finish) and the api log carries a one-line warning. It needs no index at today's volume; past about 200 000 rows in `jobs`, add a partial index on `(provider, model, finished_at DESC) WHERE status = 'done'`, built concurrently outside a migration transaction.

**Refuses to start (exit 2, one line, before it connects unless noted):**

- any variable with `PRIVATE_KEY` in its name is set and not empty (for example `CREDENTIAL_SEAL_PRIVATE_KEY`): the
  api must never be able to open a sealed credential. The message names the variable, never its value;
- `DATABASE_URL` is missing;
- `MINIO_SECRET_KEY` is empty or an example value (for example `minioadmin`): set the real secret;
- the public key is missing or malformed, or the object-store settings are missing or malformed;
- two providers declare the same model id or alias;
- after it connects: `BILLING_TZ` differs from the pin.

Then it gives every (provider, kind) wallet that the built providers declare a closed wallet on the keys that exist,
and serves. A key therefore runs nothing until `--wallet` (below) or the admin Keys page opens a wallet.

**Shutdown (SIGTERM or SIGINT), in order:** stop serving, with at most 25 s for open requests (a held facade request
can last 30 min; after 25 s it is cut and its job runs on); stop the runners (each gets 5 s to finish its step, then
is cancelled); close the database pool. The 25 s grace is under the api container's 30 s `stop_grace_period`, so the
last two steps still finish before Docker kills the container.

## The `relay` commands

Each command that touches the database reads `DATABASE_URL` (the role it names). Exit codes: 0 done, 1 failed,
2 refused (a configuration or an argument that must change first). A database or store failure prints one line, with
no trace and no secret.

| Command | Does |
|---|---|
| `relay migrate` | applies the SQL migrations as the owner (the only DDL path), sets the service role passwords, pins `BILLING_TZ` |
| `relay serve` | the api (above) |
| `relay worker --provider X` | the worker (below) |
| `relay admin create-user USERNAME` | makes an admin user |
| `relay keygen` | prints a new credential key pair |
| `relay key create`, `relay key disable ID` | API keys |
| `relay provider drain\|resume X` | drain mode (above) |
| `relay jobs restore-mark --dump-time T` | the restore runbook |
| `relay e2e --provider X` | live end-to-end run of one provider |

**`relay admin create-user USERNAME`.** The password is read twice without echo on a terminal, or as one line from a
pipe; it is never an argument. A password under 10 or over 256 characters, or an example value (`changeme`, `password`, `admin`, `minioadmin` and the placeholders of the docs and env
examples), is refused with exit 2, and so
is a user that already exists. Run it for the first admin after `relay migrate`:
`printf '%s\n' "$PW" | docker compose ... run --rm api relay admin create-user alice`.

**`relay keygen`.** Prints the key id, `CREDENTIAL_SEAL_PUBLIC_KEY=...` (for `api.env` and the admin's import tool)
and `CREDENTIAL_SEAL_PRIVATE_KEY=...` (the worker env files only). It is printed once and stored nowhere: keep a copy
of the private key off the box. Rotation is a new key pair; credentials are re-encrypted lazily.

**`relay key create`.** `[--label L] [--models M ...] [--max-unfinished N] [--wallet provider:kind=N|unlimited]...`.
Prints `created key <id>` and then the raw `rk-...` key, **once** (only its hash is stored). No `--models` = every
model. `--wallet` repeats; `N` is the day limit, `unlimited` is explicit. A wallet that no built provider declares
exits 2 and lists the declared ones. **A pair you do not name stays closed**, so a key made without `--wallet` can run
nothing (its requests are refused by the quota check; codes in `error-codes.md`).
`relay key disable ID` disables a key (a key is disabled, never deleted here); a disabled key answers 403
`auth.key_disabled`, and its requests are still logged under it.

**`relay e2e --provider X`.** Runs the provider's live end-to-end module; a provider without one exits 2 ("has no
e2e run yet"). Only dola has one (see "The dola provider"); it spends credits and refuses without `RELAY_E2E_SPEND=yes`.

## The admin API (`/admin`)

On the same port as the public API; there is no distinct admin key. Authentication is user + password, then a session
token sent as the `X-Admin-Session` header (no cookie).

- `POST /admin/login` returns the token. **Throttle:** 10 failures in 5 minutes per IP, kept in Postgres, so it
  survives an api restart. An unknown user and a wrong password answer alike (same 401 `auth.invalid_login`, same
  cost). A missing, unknown or expired session answers 401 `auth.invalid_session`.
- The session lives `session_ttl_s` (default 86 400 s); expired sessions are purged by the api sweep.
  `POST /admin/logout` ends one; **a password change (`POST /admin/password`) revokes every session**, the caller's too.
  An example password is refused here as at `create-user`.
- **Body caps** (checked before any route runs): a request under `/admin/` with no `X-Admin-Session` (login excepted)
  is refused 401 and its body is never read; a body over the cap is 413 `input.invalid`. Caps: 1 MiB, except
  `POST /admin/accounts/import` (200 items of at most 200 000 characters each, about 40 MB). Unknown fields in a body
  are 400. Password hashing runs at most 2 at a time.
- Routes: accounts (list, import, edit, screenshot), keys, jobs (list with a cursor or an `offset` (0 to 1 000 000, else 400 `input.invalid`; not both, an empty `cursor=` counts as given; `total` = jobs matching the filters; `created_from_ms` inclusive and `created_to_ms` exclusive bound `created_at`, from < to, applied to the page and `total`; `tz=<IANA zone>` adds `day_totals`, `{"YYYY-MM-DD": n}` for each local day on the page, n = every job of that day matching the same filters and window; an unknown zone is 400 `input.invalid`; without `tz` the answer has no such field), detail, rename, debug, recheck,
  regenerate, media proxies), stats (`GET /admin/stats` the cards; `GET /admin/stats/hourly?provider=` the last 24 UTC hours, zero-filled, oldest first, each `{hour_ms, delivered, failed, in_progress}` counting jobs by the hour they were submitted; `GET /admin/stats/pool?provider=` the Accounts pool panel: per pool `{pool, left, budget, unknown, drift}` over the `available` accounts only (dead and disabled are not counted; a pool with no budget has every account unknown), plus `proxy_failed` and `last_full_check_ms`, the newest finished Check all; `GET /admin/providers/summary` the Jobs page's provider figures in one call, no alerts: `{day_tz, day_start_ms, providers, hours}`, where `day_tz` is the `dashboard_tz` setting (default Asia/Ho_Chi_Minh; not the billing day, which with the pool days stays as it is) and `day_start_ms` today's midnight in it, each provider (registered ones only) is `{provider, execution, claims_enabled, running, queued, done_today, failed_today, accounts: {usable, total}, pools: [{pool, unit, left, budget}]}` (`execution` is the provider's live `execution_setting` when it declares one, dola's `dola.send_path`, else its caps; `done_today`/`failed_today` count on `finished_at` since `day_start_ms`; `total` = available + dead + disabled; `unit` is `points` or `jobs`, the pool's `PoolSpec.unit`; `left`/`budget` as the pool panel), and `hours` is every whole hour since `day_start_ms` up to the current one, zero-filled, `{h, done: {provider: n}, failed: {provider: n}}` with `h` the hour's start), settings (read, write, reset; `BILLING_TZ`, `PUBLIC_BASE_URL` and `TRUSTED_PROXIES` are
  listed read-only because they are boot-time env; see the next bullet), alerts (list, acknowledge), `GET /admin/models`
  (every model, the `/v1/models` entry plus `disabled`: paused by `<p>.disabled_models`). Times are epoch
  milliseconds (`*_ms`).
- **Settings** (`admin/routes_settings.py`): a live setting is the stored database value, else its default. No
  environment variable sets one, and a variable named like a setting is ignored (precedence in `relay_core/settings.py`).
  Each item carries `allowed`, the range or rule the value must meet, `group` (the provider of a `<p>.<name>` key, else
  `core`, as the `fixed` rows) and `basic` (true for the few an admin changes often: a provider's `claims_enabled`,
  `prices`, budgets, `disabled_models`, a browser provider's `browser_slots`, dola's `master_quality`, flow's
  `captcha_provider`; the page may fold the others away). The answer also carries `fixed` (the code
  constants that shape behaviour, each with a reason; the list is `relay_core/limits.py` and each provider's `LIMITS`)
  and `containers` (the api's and each recently reporting worker's CPU, memory, PID and `/dev/shm` limits, read from
  its cgroup at start, and when each was reported). An item no longer names an environment variable. The boot-only list
  (`env_only`) is unchanged.
- **Account actions are commands.** Delete, wake, check, proxy re-check, Check all, catalog refresh, job recover and
  fetch-title answer 202 `{command_id}`; the provider's worker runs them (see "What an operator must know").
  `GET /admin/commands/{id}` shows status and result. Editing the proxy or enabling an account that holds a live
  lease answers 409 `pool.account_busy`; a new proxy also queues a proxy re-check, and enabling a disabled account
  queues a wake.
- Credentials are never exported, and the account list has no secret column.

## The admin pages

`/manage` is the admin, a Vue single-page app built into `relay_core/admin/static/app/` (its entry is `app/index.html`,
also at `/ui/app/index.html`); `/login` and `/test` redirect into it (`/manage#/login`, `/manage#/test`), as `/` does
to `/manage`. `/reference` (the API reference) is a static page beside it. All are served on the api's port with the
admin CSP (`default-src 'self'; script-src 'self'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'`;
`blob:` for images only, the account screenshot the page fetched itself): no inline script,
handler or style, nothing loaded from another host. The files are read once when the api starts; the api refuses to
start when `app/index.html` is missing. Files under `app/assets/` carry a content hash in their name and are served
`immutable`; everything else is `no-cache`.

`/reference` alone adds `style-src 'self' 'unsafe-inline'` to that header, because the renderer writes its styles into
the page at run time; its `script-src` stays `'self'`, and its own files under `/ui/` keep the exact header. A test
fails when any other page or `/ui/` file carries the looser one.

- **The web build:** the source is `apps/admin-web/` (Vue, Vite, Element Plus). `scripts/web.sh [--tree NAME] build`
  builds it in the container (Node image pinned by digest, `npm ci` from the lockfile) and pulls back the dist
  `static/app/` and the two generated declaration files; the dist is committed, so the api image needs no Node at
  runtime. `web.sh check` (lockfile audit, `style=` grep, vue-tsc, ESLint, Prettier, Vitest) and `web.sh verify` (a
  fresh build must equal the committed dist byte for byte) run in `scripts/check.sh`; `web.sh format` runs Prettier.
  Rebuild and commit the dist after any change under `apps/admin-web/`.
- **Prompt frames:** the "Prompt frames" tab edits the text a provider sends around the customer's prompt, on the
  routes of `admin/routes_frames.py` (no route of its own). Pick a provider; each kind shows its default (who and when,
  a badge when it differs from the shipped one, and "Reset to the shipped default") and a table of models with the
  source (own or default), the effective version and the keep-own switch (who and when). Edit opens the editor: one
  line, at most 1,000 characters, `{{client_prompt}}` and `{{tracking_code}}` once each; a refusal shows its `param`,
  warnings are shown but never block the save, Preview renders the draft with a sample prompt and its length without
  sending anything, History rolls back to any version, and a model with its own frame can follow the default again.
  "Apply to all models of this kind" (on a default) lists what was applied, kept (locked) and unchanged. A save applies
  only to jobs created after it. A job's detail, section Technical, shows the frame version the job used and the exact
  text sent (`GET /admin/jobs/{id}` adds `frame_id` and `sent_text`). Frame texts and sent texts render as text.
- **Settings:** the "Settings" page has six sub-navigation entries (settings, boot-only values, measured prices,
  change password, fixed limits, containers); the source is `apps/admin-web/src/views/SettingsView.vue`. Each setting
  shows an "Allowed:" line with the range or rule from the registry. The Fixed limits card lists the code constants of
  `fixed` (`FixedLimits.vue`). The Containers card (`ContainerLimits.vue`) shows one block per service: the api,
  stamped "this process, read at start" (its limits are read when the api starts), then each worker, stamped
  "seen <ago>" (its last heartbeat). A row past the stale window gets a `stale` chip. A worker image older than
  the limits shows "Not reported by this worker version". A limit that could not be read says "unreadable", which is
  not the same as "no limit". A worker that stopped cleanly removes its heartbeat row and is not listed.
- **Alerts:** the "Alerts" tab lists every stored alert kind (billing `clamp` and `drift`, unclear provider replies) from
  `GET /admin/alerts/groups`, filtered by open / acknowledged / all and by kind, with the live conditions beside them
  (computed now, nothing to acknowledge). A group shows its severity, title, first and last seen, rows, open rows and
  `keys`; an unclear wording seen under one key only is marked as possibly one customer's own words. Details opens the
  group (`GET /admin/alerts/groups/{kind}/{group}`): for unclear replies each stored reply with its job (opens the job
  detail), account, key and outcome, and the outcomes of the group's jobs (delivered, refunded, still open, with the
  failure codes); the empty wording is shown as an API read failure. Acknowledge sends the newest row id the page
  showed, so a row that arrived later keeps the group open. The banner above the tabs counts open groups and live
  conditions. There is no phrase list to edit: new wording is a code change. Titles and replies render as text.
- **The API reference** is Scalar 1.25.122, vendored as `static/vendor/scalar.min.js` (licence beside it), pinned by SRI
  in `reference.html`; a test pins its hash and source URL.
- **The admin token** lives in `sessionStorage` and goes out as `X-Admin-Session`. Media in a job's detail comes only
  from the same-origin `/m/` share links; the admin content routes are for downloads.
- **Tests:** in the commit gate, `tests/admin_ui/test_build_guards.py` scans the committed dist for what the CSP or the
  release rules forbid, `packages/relay-core/tests/api/test_admin_pages.py` pins the serving (CSP, redirects, cache
  rules, only static files) and the Vitest specs of `apps/admin-web/tests/unit/` run in `web.sh check`. The browser
  tier (`tests/admin_ui/test_e2e_*.py`, the whole-app CSP sweep `test_e2e_sweep.py` and `test_reference.py`) drives the
  app in Chrome through `/manage`; run only those with `scripts/check-in-container.sh tests/admin_ui/`. The sweep walks
  every route's states (`tests/admin_ui/sweep_states/<route>.py`) at 1280, 768 and 390 px wide, in light and dark, under
  the CSP watch at every width (six passes, node ids such as `[1280-light]`). With `RELAY_SCREENSHOTS_DIR` set it saves
  each state as `<width>-<theme>-<route>-<state>.png`; the names must be the exact cross-product of widths x themes x
  states, and two states with one name fail the sweep.

## Logs

- **Process log.** Every line the api and the workers write, uvicorn's included, passes the redactor (cookies,
  `Authorization`, tokens, signed CDN query values, `user:pass@host`, secret labels; any string over 2 000 characters
  becomes `[omitted len=N]`). A traceback is redacted too, without being cut. The uvicorn access line is the exception
  to the trim: each of its arguments is redacted on its own and the line is not cut; it keeps the path and drops the
  query string, so a client that sends `?key=rk-...` leaves no key in it (the API refuses that form anyway).
- **Container logs across a deploy.** Each service logs to a rotated json-file (20 MB x 5, so at most 100 MB a
  container; `deploy_violations` holds every service of `deploy/compose.yml` to a rotated driver). A recreated container
  starts with an empty log, so before each `up -d` of the api or a worker, `deploy.sh` and `rollback.sh` keep the log of
  the container it replaces as `~/relay-logs/<service>/<UTC time>-<random>.log.gz` (files 0600, folders made 0700 at
  every save): kept 7 days, at most the newest 20 per service. A save takes at most 60 s; a log that cannot be kept in
  time, or old ones that cannot be removed, is a WARNING, never a stop. They hold what `docker logs` held, the
  redacted process log and nothing more. Read the lines before the last deploy with
  `zcat ~/relay-logs/worker-dola/*.log.gz | less`, the current ones with `docker compose -p relay logs worker-dola`.
  A container recreated by hand, not by the scripts, loses its log.
- **Request log** (admin "request log", table `request_logs`, kept `log_retention_days`, default 30). One row per
  request that carried a **known** API key, a disabled key included (its 403 is logged under it). Also logged under
  the key: a path or method no route serves (404, 405). **Not logged:** a successful read (GET, HEAD or OPTIONS
  answered 2xx), so a polling client does not flood it; every write and every failure is. A request with no key or
  an unknown key leaves no row. The body and the error answer are redacted; credential routes keep no body. A client
  that hangs up is status 499. A logging failure never fails the request.

## The worker (`relay worker --provider X`)

One process per provider (`dola`, `vibes`, `meta`, `flow`, `gemini`, `qwen`; the compose services `worker-<provider>`). It loads
`relay_<p>.spec.SPEC` and `relay_<p>.engine.build_engine`, then runs these loops as tasks of one process:

| Loop | Does |
|---|---|
| claims | takes the oldest queued job across pools and submits it |
| poller | polls running jobs (only for providers with `has_poll`) |
| fetcher | downloads finished output into MinIO; every provider and CDN call of an account with a proxy goes through that proxy only (a failed proxy is a failed fetch, retried later, never a direct download); an account with no proxy goes direct |
| reconciler | proves `unknown` jobs against the account (only for providers with `has_reconcile`) |
| reaper | requeues or fails jobs whose lease expired; the daily ledger drift pass |
| commands | runs queued worker commands, the account checks, the title sweep and the catalog refresh |

Environment (read at boot; the full list is in the per-service env files):

| Variable | Meaning |
|---|---|
| `DATABASE_URL`, `BILLING_TZ`, the credential private keys, MinIO settings | required; a missing or malformed value exits 2 before any connection |
| `WORKER_ID` | default `worker-<provider>`. Names the heartbeat row, so it must be **unique per worker process** and contain **no `/`** (else exit 2). Two processes sharing an id share one row. Each boot appends its own `/<suffix>` to form the lease owner |
| `WORKER_GRACE_S` | default 720. One deadline for the whole stop; keep it below the compose `stop_grace_period` (780 s) so Docker never kills first |

At start the worker reads its container limits from its own cgroup (v2: `cpu.max`, `memory.max`, `pids.max`, plus the
`/dev/shm` size) and the five boot knobs named in `BOOT_NAMES` (`relay_core/cgroup.py`; no other variable is ever
copied, so a secret cannot be reported). It writes both with its heartbeat row (`worker_heartbeats.limits`, migration
`0019_worker_limits.sql`). A `docker update` shows only after a restart.

Start: the worker refuses (exit 2) when `BILLING_TZ` differs from the value pinned by `relay migrate`.

Exit codes: 0 clean stop; 1 after 5 consecutive unexpected errors in the claim loop, or a loop that died; 2 refused
to start. A submit stopped by the core's own timeout does not count toward the 5. Docker restarts the
container on 1.

Graceful stop (SIGTERM or SIGINT; the entry script is under "Graceful stop of the browser workers"): no new claim,
check or command starts; in-flight submits finish or are fenced inside `WORKER_GRACE_S`; the other loops finish the
item they are on within what is left of the same deadline; leases are released, except that of a job still
`submitting` (the reaper moves it); a command that was running is failed so the admin can run it again. A job
requeued by a graceful stop is claimed again at once by another worker.

### Drain mode

Setting `<p>.claims_enabled` (default true). While false, that provider's worker claims no job, runs no queued
command (an admin Check or Wake waits for the resume), and does no catalog maintenance. **Still running:** the account
checks at their usual interval (owner, 23/10: they keep sessions alive, e.g. flow's cookie rotation; a dola or meta
check still opens Chrome while drained), poller, fetcher, reconciler, reaper, the title sweep and webhooks, so open
jobs finish, fail, refund and notify. To stop the checks of one account, disable it. Rollback of a provider go-live is drain mode
(there is no fallback system).

```bash
relay provider drain dola     # prints "draining dola"; "(no change)" when it already was
relay provider resume dola    # prints "resumed dola"
```

Both set `<p>.claims_enabled` through the settings store (so they take the same path as the admin Settings page) and
run as `relay_api`. An unknown provider exits 2 and lists the built ones. Drain mode is not an exit code of the worker:
the worker keeps running and picks the setting up live.

### Paused models

Setting `<p>.disabled_models` (default `[]`): a list of the provider's public model ids (the `id` of its
`/v1/models` entries, not an alias); any other value is refused 400 `input.invalid` when saved. A request for a
paused model, its aliases or its variants (`seedance-2.5-10s`) is refused 403 `model.disabled` ("this model is paused")
at the door, after the key's allow-list and before the inputs are stored, anything is charged or a job is made; the
admin Regenerate is refused the same way, and an idempotent replay of a job made before the pause still answers that job. Jobs already accepted run on.
`GET /v1/models`, `/v1/models/aliases`, `/v1beta/models` and `/models` leave a paused model out (as a model the key
may not use), `GET /models/<id>` answers 404, and `/v1/providers/<p>/options` answers 404 once every model of the
provider the key may use is paused. Live: the next request reads the new value.

## The dola provider (`relay worker --provider dola`)

Drives dola.com in Chrome, one browser session per claimed job, or sends the order over plain HTTP when
`dola.send_path` is `http` (see "Send path"). Sources: `packages/provider-dola/src/relay_dola/`.

### What the worker needs locally

- **Env file** `deploy/worker-dola.env` (gitignored), copied from `deploy/worker-dola.env.example`. Keys, names only:
  `DATABASE_URL`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_REGION`,
  `CREDENTIAL_SEAL_PRIVATE_KEY`, `BILLING_TZ`, `RELAY_PROFILE_ROOT`. The number of browser sessions is not an
  environment variable: it is the live setting `dola.browser_slots`. In the compose stack `compose.yml` sets
  `RELAY_PROFILE_ROOT=/profiles` itself.
- **Chrome** on the Mac (history: relay runs on the box now, whose image holds the same pinned build): the pinned Chrome for Testing 154.0.8037.92 through `RELAY_CHROME_PATH` (see "Google Chrome"),
  not the installed Google Chrome. Set `RELAY_PROFILE_ROOT` too, or the profile cache is not kept.
- **Accounts and proxies live outside the repo**, in `~/relay-secrets/` (the Mac, mode 0600 files; copied to the box once at intake, see "The secret tools"). They are imported
  through the admin accounts import (`scripts/import-accounts.py` on the box), which seals each credential with the public key. Never print, paste or commit
  their contents, and never copy them into `deploy/` or `tests/`.

### Live settings

Registered by `spec.py`; read and written on the admin Settings page, live.

| Setting | Meaning | Default |
|---|---|---|
| `dola.claims_enabled` | drain mode off; false = no new job is claimed (see "Drain mode") | true |
| `dola.render_deadline_s` | seconds to wait for dola to render one submission before giving up (at least 60), then one final read, see "What an operator must know" | 9000 |
| `dola.prices` | points charged per job, keyed `<model>@<N>s` for video and `<model>` for image; an option missing here is refused (`model.unpriced`), never charged a guess | table in `catalog.DEFAULT_PRICES` |
| `dola.costs` | what one job costs the account: video in dola credits, an image in image units (Seedream 4.5 1, 5.0 Pro 4); a missing option is refused (`model.uncosted`) | table in `catalog.DEFAULT_COSTS` |
| `dola.video_budget` | video credits per account per day (dola's own reported number wins) | 4 |
| `dola.image_budget` | image units per account per day (owner 24/10: 40 Seedream 4.x jobs or 10 of 5.0 Pro; dola's own "reached today's image limit" rests the account whatever this says) | 40 |
| `dola.master_quality` | deliver the H.264 master (720p, no watermark) instead of the light HEVC rendition; falls back to the rendition when the master cannot be fetched | true |
| `dola.alert_duration_mismatch_rate` | drift alert: share of the last 24 h jobs delivered with the wrong length (0 to 1) | 0.2 |
| `dola.alert_watermark_only_rate` | drift alert: share of the last 24 h jobs that failed as watermark-only (0 to 1) | 0.2 |
| `dola.alert_content_refused_rate` | drift alert: share of the last 24 h submits refused for content (0 to 1) | 0.5 |
| `dola.disabled_models` | public model ids paused for every key (see "Paused models") | `[]` |
| `dola.browser_slots` | browser sessions at once, 1 to 12 (code constants `BROWSER_SLOTS_MIN`, `BROWSER_SLOTS_MAX` in `relay_dola/constants.py`, sized to the dola container: 8 CPUs, 12 GiB); lowering retires idle slots | 4 |
| `dola.send_path` | how an order is sent: `browser` (Chrome) or `http` (one signed POST, no browser); read per job at the send; a job with reference images, an image with a style, or any video but Seedance 2.0 5 s or 10 s at 16:9 always goes by browser | browser |
| `dola.signer_alarm_accounts` | HTTP send only: this many different accounts refused (710022002/710022004) before an acknowledgement within the window = relay's signature is suspect (at least 2) | 3 |
| `dola.signer_alarm_window_s` | HTTP send only: that window, and how long HTTP sends pause after the alarm (at least 60) | 900 |
| `dola.refusal_max_accounts` | how many accounts one job may try when dola refuses to take it (710022002, the throttle 710022004, its "something went wrong" reply); each refusal rests that account and sends the job to another, the last one ends the job failed and refunded; 1 ends it at the first (1 to 9; see "Refusals") | 2 |

The range or rule of every setting is shown on the Settings page.

### Send path (`dola.send_path`)

One dola provider, two ways to send the order, chosen by this one global setting (no per-account override; default
`browser`, so nothing changes until it is set). Polls, reconcile reads and downloads are HTTP either way. The setting
is read per job at the send; flipping it never moves a job that is already sent. Sources: `engine/send_http.py`,
`signing/`; the wire format is FINDINGS.md "HTTP send spike 23/10".

- **`http`**: per account, a session is made over the account's proxy and cached 6 h (`SESSION_TTL_S`) per credential:
  the `/chat` page is fetched once to check dola's `bdms` SDK file (a new one may change the signature format), an
  anonymous web id (`get_web_anon_id`), the assistant bot id (`/alice/user/launch`, signed: it takes the account
  signer's first signature, so the paid send is never a first one). Each send: the passport login read over HTTP (a
  guest read is a login doubt, as on the browser path), the client ids are made and written to the job
  (`provider_state`: `send_path`, `local_conversation_id`, `local_message_id`, `unique_key`, `collect_id`), the job is
  marked sent, then ONE signed `POST /chat/completion`. Its stream decides:
  - `SSE_ACK` with a conversation: dola has the order; the conversation becomes the job's reference and the reply is
    judged as on the browser path (images finish in the session or by the poll, a video by the poll). There is no
    re-send over HTTP: a confirmation question stays unclear and the poll decides.
  - `STREAM_ERROR` before any acknowledgement: dola made no conversation. 710022002 and 710022004 are refusals (the
    account rests; the job goes to another account or, on the last one `dola.refusal_max_accounts` allows, ends
    refunded: see "Refusals"), 710022003 is the region; 710020702 (a replay
    of ids dola saw before: never expected, relay never re-sends) and any other code are unknown.
  - anything else (a drop, a timeout, an HTTP error, a stream with neither): unknown. Reconcile lists the account's
    conversations over HTTP and looks for the job's exact `local_message_id`; not found is a proof only by the usual
    rule (every conversation of a list that covers the send window was read, each read back to its first message).
- **Signer alarm.** dola answers a bad `a_bogus` with its throttle codes, so a broken signer would look like every
  account throttled and every prompt refused. The alarm blames relay's signature when 710022004 carries dola's
  `verify` decision (its captcha: what an unsigned send got), when `dola.signer_alarm_accounts` different accounts
  are refused before an acknowledgement within `dola.signer_alarm_window_s` with no acknowledged send in between, or
  when the session bootstrap finds a new `bdms` SDK on the page. Then that send is not taken (`dola.signer_suspect`,
  one attempt), no account rests, an ERROR line is logged (`relay_dola.send_http`) and the job's timeline says why,
  and every HTTP send of the worker is put back (`dola.signer_suspect`) until the window ends. Set `dola.send_path` back
  to `browser` and have the signer looked at. The alarm lives in the worker process; a restart clears it.
- **Before the send** nothing is ever moved to the other path: a proxy that cannot reach dola is `pool.proxy_failed`, a
  bootstrap or signer failure `dola.http_setup_failed`, both ordinary retries under the same setting.
- **Proven over HTTP (23/10, d23-05, delivered files measured):** Seedream 4.5 with no ratio (2048x2048) and at 16:9
  (2848x1600), Seedream 5.0 Pro at 9:16 (1600x2848), Seedance 2.0 5 s 16:9 (1280x720, 5.09 s); 24/10: Seedance 2.0
  10 s 16:9 (1280x720, 10.04 s). An image ratio rides in the message text as the page writes it ("Generate image:
  <prompt>, 16:9") plus the page config's `image_creation_ratio`; the key alone was ignored (16:9 asked, 2048x2048
  made). The live table is FINDINGS.md "HTTP video live check 24/10".
- **Always by browser, whatever the setting** (with a line in the timeline): reference images (i2v, i2i: the HTTP
  upload is not built), an image with a style (not tried), and every video but Seedance 2.0 5 s or 10 s at 16:9
  (`send_http.PROVEN_VIDEO`):
  - a ratio other than 16:9: Seedance 2.0 asked 9:16 in `ability_param.ratio` with the bare prompt: dola stored it,
    replied "16:9" and made 1280x720. The page's own form ("Generated video: <prompt>, 9:16" with
    `reply_message_format`) on Seedance 2.0 9:16 (24/10, d23-05, the day's first send) was acknowledged, then ended
    "I can't generate the video and didn't use any credit for this generation." (no video, no charge); on n11-07 every
    send (page form and the bare form alike) was refused 710022002 before any acknowledgement. 1:1 and 21:9: not
    tried (the day's video limit came first);
  - Seedance 2.5: untested (owner, 23/10). Two sends in the page form on d23-05 were answered "I can't generate the
    requested content.";
  - Seedance 1.0: 5 s 16:9 in the bare form (24/10) was answered at once "I can't generate the requested content.
    Try something else." (no video);
  - Seedance 2.0 15 s: not tried.

There is no setting for image templates or for extra reply phrases. Image templates are not creation parameters: a
request with `options.template` or `options.template_category` is refused 400 (no charge); a UI reads the template
list (prompt and preview) from `GET /v1/providers/dola/options` and sends the prompt as `prompt`. The words relay
recognises in dola's replies live in code (`classify.py`); new wording is a code change after a real capture, never an
admin setting.

**Prompt frames.** The text sent to dola is not a setting: it is one admin-written frame per model (or per kind: the
default), stored in `prompt_frames` with a version history and rendered once per job at creation into `jobs.sent_text`
A frame has two placeholders, `{{client_prompt}}` (the customer's words) and `{{tracking_code}}`
(relay's `[marker]`), each exactly once, on one line of at most 1,000 characters. `relay serve` seeds the shipped
defaults (the old video sentence after the prompt; nothing for images), logs a WARNING at every start when a stored
default differs from the shipped one, and never rewrites it: the admin resets it (`POST /admin/frames/dola/<kind>/reset`).
**Length limit.** Each provider declares its own `max_prompt_chars`, and the door refuses a longer prompt 400
`input.prompt_too_long` before any charge. The limit on the text sent applies to the whole text relay sends to the
provider, not to the customer's words alone: the rendered frame (the prompt plus the frame's own text and the
`[marker]`) must stay within the provider's `max_sent_chars`, else the same refusal. dola's are both 200,000 characters
(its measured edge on 12/10: 250,000 kept intact, 265,625 silently dropped; the cap leaves room below it). With the
shipped frames the longest prompt is 199,753 characters for a video (the video sentence takes 246 characters with the
marker) and 199,993 for an image; a longer frame written in the admin lowers it further. The admin preview answers
the same refusal. Clients read the cap as `limits.max_prompt_chars` of every model in `GET /v1/models`.

A video frame that lacks "single video" is saved with a warning. `dola.video_prompt_hint` is gone; a stale row of it in
`settings_kv` is ignored with an error log.

### How an order is judged

dola often creates a conversation even when it refuses, so a conversation proves nothing. The engine reads the
conversation's own messages (dola's API, through the page), never the page text, and judges each finished reply.

**Reply verdicts** (`classify.SubmitOutcome` plus "unclear"):

| dola said | Verdict | Effect |
|---|---|---|
| a known acceptance sentence ("be ready in", "is being created", ...) | accepted | order taken; balance read from that reply |
| a question or a stated length other than asked | needs confirmation | re-send in the same conversation, at most 2 times |
| copyright, safety, likeness or "cannot generate" | content refused | job fails; likeness has its own code |
| not enough credit, daily limit | out of credit | the pool rests until the UTC day boundary; a positive balance below the price is "cannot afford", the job moves |
| "something went wrong", "try again later" | temporary error | not taken; the job is tried again |
| anything else, or two readings that contradict | unclear | see rules below |

**Send-step results** (what the engine hands the core):

| Result | When |
|---|---|
| accepted, taken | dola accepted; the job waits for the poll |
| accepted, not proven | unclear or still gated after the re-sends; the conversation is kept and the poll decides |
| completed | image whose pictures are already there |
| not taken | out of credit; a refusal (temporary error, throttle 710022004, 710022002) before the last account `dola.refusal_max_accounts` allows; the job goes elsewhere or later. Also a send proven not to have left the page (below) |
| fail | content refused; a refusal on the last account allowed (`dola.refused_710022002`, else `dola.refused_on_accounts`) |
| retry | a failure before the send (nothing was sent) |
| ambiguous | a failure after the send, or no conversation appeared; reconcile reads the account's own conversation list |

Rules an operator must know:

- **One Enter per send.** The Send button is never clicked. An input still holding the prompt after the clear wait
  is ambiguous, and reconcile decides, unless the browser provably sent NOTHING that could carry the order. The
  watch covers the whole browser context from the moment it opens: any request that is not a GET, on any host, from
  any page, popup or worker the context reports (bar the conversation reads `/im/chain/single`,
  `/im/chain/recent_conv`); a GET whose full URL (query included) names a completion or holds `/chat/`; any WebSocket
  frame sent by any page. A watch that could not attach or whose listener failed counts as traffic, and so does a
  `/chat/completion` answer in the recorder. The proof is read only on a browser that no longer exists: the whole
  browser (context and process) is closed first, so every page, service worker, keep-alive request and beacon dies
  with it; the watch counts until the close has completed, and only then is it read. A send fired late, even after
  the 30 s clear wait, is seen. A close that fails (Chrome had to be killed) is no proof. The session's own exit then
  only finishes its cleanup. Only zero traffic is the proof. Then the send is proven not sent: not taken
  (`dola.ui_send_failed`, one attempt), the job goes back to the queue as a new attempt, and nothing more is pressed
  in that session (23/10: such a job sat 11 min in unknown).
- **Refusals: at most `dola.refusal_max_accounts` accounts per job (default 2).** Owner's ruling 25/10, which
  replaces the 23/10 one (710022002 ended the job at the first): 22-23/10 single jobs were re-queued across all 9
  accounts within minutes and 6 were logged out by dola's risk system right after. A refusal is dola not taking the
  order: its stream code 710022002 (dola will not take this order; 23/10: 9 of 18 tries in 3 h, all on one prompt, on
  7 accounts) or 710022004 (throttle), or its "something went wrong" reply. Each refusal rests the account as before
  and adds it to the job's `provider_state.refused_by`; the core keeps that list across every requeue (a page fault,
  an interrupted submit, a proven-not-sent order) and a restart, so nothing in between resets the count. Before the
  last allowed account the job goes back to the queue (no attempt used for 710022002 and 710022004); the refusal on
  the last one ends it failed with the full refund: `dola.refused_710022002` for 710022002, `dola.refused_on_accounts`
  otherwise. At 1 the first refusal ends the job. Not refusals, so not counted: a proxy failure, a login doubt or
  signed-out page, a page fault, out of credit, the region (710022003) and the HTTP signer alarm.
- **dola's code 710022002.** dola's `/chat/completion` stream answering a send (the first or a gated re-send) with
  710022002 is a refusal (above). dola's own message goes to the timeline, the stream is kept as
  evidence (`refused-710022002-stream.txt`), and the account rests 30 min (`THROTTLE_COOLDOWN_S`) with its next check
  due then: the code is about the prompt, but an account that just sent such a prompt is at risk, and the check finds
  a logout before a job does. The stream is
  parsed event by event: a code counts only in an error envelope (`error_code` at the top of an event or of its
  `event_data`), never inside a reply's text, and a stream that also delivered anything (an event other than a
  heartbeat, an acknowledgement, an empty object or `[DONE]`) is no error at all. When the refusal ends the job and a
  conversation had already appeared, it is kept: it becomes the job's reference
  (`provider_state.refused_conversation`, named in the timeline); a job that goes to another account only names it in
  the timeline.
  dola can refuse and still deliver about 25 min later; such a delivery is NOT fetched by itself (the job stays failed
  and refunded), but an admin Re-check of the job sends it back to waiting (the key is charged again) and the poll
  reads that conversation. Without a conversation there is nothing to re-check. On a refusal the conversation is
  still looked for up to 10 s (`REFUSAL_RECEIPT_S`) before the job fails, and storing it is best effort: if the
  reference cannot be written, the job still fails (refunded) and the id stays in `refused_conversation` and the
  timeline.
- **Re-send only on a finished question.** A re-send happens only when dola's own API read, taken right before it, shows
  a finished reply that is itself a confirmation question, and the order was not already accepted. A re-send is a new
  paid order, so a stale, partial or failed read never earns one; each re-send repeats the full composer set-up.
- **Unclassifiable or contradictory is unclear.** A reply that is none of the above, or one that holds both a refusal
  and an acceptance, is not taken, is not re-sent, moves no job and rests no account; the poll decides (a video is
  delivered, or quiet ends in a refund). A bare "0 credits left" is unclear plus a zero balance and a rest.
- **No conversation after the send** means the job is ambiguous. Page text never decides there (the sidebar holds
  titles of other chats); only dola's own throttle or region signal does, or the login read of the page: a guest page
  is a logout. The account gets what a guest read at the open gets (a spaced strike and a 600 s rest with a recheck;
  no strike when the browser lost the cookies), the run clears no strike, and the order follows the proof above:
  traffic that could carry it left the browser, so it stays unknown; none did, so it is not taken and goes back to
  the queue.
- **The customer's own words never decide.** A reply that reads differently once the quoted prompt is removed is unclear.

### Account check, catalog refresh, recover

Worker commands (queued from the admin pages, run by the dola worker; see "The admin API"):

- Account check: opens the account in Chrome, reads login state, identity and which composer the account has. It spends
  no credit and sends nothing. A read never kills an account by itself:
  - **Cookies lost in the browser.** When the page reads as a guest and an identity cookie that `inject_cookies`
    verified in this browser (`sessionid`, `uid_tt`; a jar with `sessionid` alone only has that one to lose) is no
    longer there, the browser lost it. That is a browser fault, never a strike: the result is `unknown` with the same
    600 s rest and recheck as a login doubt. A send is put back in the queue with `dola.browser_cookies_lost`, uses no
    attempt for the first three browser-fault requeues of one job (the rule's `free_retries`, counted in the job's own
    `fault_retries` column, which nothing resets); the next ones use an attempt under the same code, so `max_attempts`
    ends a job that keeps meeting the fault, and sends nothing. A browser that cannot be asked for its cookies (the read fails or takes over 5 s) is a
    fault too: no strike, a 60 s rest.
  - **Every login doubt is a spaced strike and a rest.** A guest read, a login dialog, and "no conversation and no
    name" each add one strike and rest the account 600 s, with its next check due when the rest ends. A doubt less
    than 540 s after the last counted strike adds no strike (`accounts.strike_at`, written by the worker only; the gap
    is shorter than the rest so a slow close or a clock skew cannot put the next read inside it). Only a strike that
    counted can reach the threshold. The third counted strike marks the account `signed_out`
    (strike threshold 3); the check that does it reports `signed_out` to the admin, a repeat check of an account that
    is already dead reports `unknown`. A really signed-out account is therefore out of the pool for 20 to 30 minutes
    before it is marked. A failed read (no answer) is no strike; in a send it rests the account 60 s.
  - **What clears the count and its time**: an alive check, a credential merge, a wake (which also clears the
    cooldown and the pool rests, so a disabled account that is enabled again starts fresh), an order that was taken,
    and any send that read the account logged in, whatever it ended as (a refusal, a limit, an unknown order, a retry).
  - A dead account (every provider) is checked only every `dead_check_interval_h` (core setting, default 24), whatever
    its check read (a doubt, a failure or a bad proxy does not bring the next one closer), and it counts no more
    strikes. It is revived only by a check that reads it logged in; a re-import of its credential merges into it and
    is checked at once. Wake does not revive; a manual check of a live account that reads a doubt adds a spaced strike
    and a rest (it does not stand still), and one that cannot read anything (`unknown`, no doubt) changes nothing.
- Catalog refresh: reads the image template and style lists over HTTP (no browser).
- Recover: takes the clean videos of one conversation. Another job's video is never attached: a conversation that is not
  the job's own must carry the job's text.

### Page timing

- **A cold profile waits longer for the composer.** The first run on a profile with no HTTP cache (a new credential
  generation makes a new profile; a throwaway or a wiped one too) fetches dola's whole script bundle through the proxy;
  it waits up to 30 s (`COLD_MODE_WAIT_S`) for the Create Videos / Create Images button instead of 9 s.
- **A folded composer row.** When dola's row measures itself too narrow it folds its buttons behind a "···" button
  (23/10: the row read `+ | Fast ···`). The run opens that menu and clicks the mode item there, a real click on a
  visible item only; no "···" button, no such item or no chip row after it is `dola.ui_mode_not_ready` before anything
  is sent. The opened menu was never captured live: the fallback is built from the folded row's screenshots and HTML.
- After `goto` the run waits for a quiet page before it composes: at least 4 s, then until no main-frame navigation for
  3 s, never longer than 15 s (some accounts' pages reload themselves a few seconds after loading, measured on Chrome
  154). A navigation that still hits the run before the send (a reload after the settle wait) is absorbed once inside
  the same open: the run settles again and composes once more, nothing having been sent. A second one is
  `dola.page_reloaded`: the run goes back to the queue, uses no attempt (for the first three browser-fault retries of a
  job, as above), nothing is sent, and the account rests 60 s. A
  navigation after the Enter is never retried: the order is unknown, as before.
- After the one Enter the input may take up to 30 s to empty (`CLEAR_WAIT_S`); measured through a proxy: 1.4 to 13.5 s
  for sends that all went through. Text still in the input after that is an unproven send, never a second Enter.

### Live run

`RELAY_E2E_SPEND=yes DATABASE_URL=... relay e2e --provider dola` makes a temporary key, creates one 5-second
`seedance-2.5` video through the API (`RELAY_E2E_URL`, default `http://127.0.0.1:8100`), waits up to 70 min (a 5 s render took 28 to 54 min on 12/10), checks the
length with ffprobe, and disables the key. It spends dola credits: run it only with the user's go-ahead at that moment.

### Not proven locally

The local gates use recorded and fake dola behaviour. These wait for the live run on the box:

- whether dola's conversation listing returns `has_more`;
- how long the input takes to clear after Enter;
- where dola cuts long text;
- half-written replies in full chain reads;
- a finished reply without the finish flag;
- the real wording of "is being created";
- the health of the supplied account jars;
- `relay e2e`.

## The flow provider (`relay worker --provider flow`)

Google Flow (flow.google.com) over plain HTTP: no browser. Text to image (`flow-nano-banana-2.1`,
`flow-nano-banana-pro`, `flow-nano-banana-2-lite`; ratios 1:1, 9:16, 16:9 default, 3:4, 4:3; one image per job) and
Veo 3.1 Lite text to video (`flow-veo-3.1-lite`: 8 s, 16:9 only, 10 Flow credits; built from the 23/10 capture, live
proof pending). An input image (image to image, image to video) is refused with `input.unsupported`: the upload was
never captured. A request whose input image cannot be read is refused earlier, at the door, with
`input.image_unreadable` (nothing charged either way; Codex review 24/10, accepted). The protocol and what is still
unproven are in `fixtures/flow/FINDINGS.md`.

**What the worker needs.** `deploy/worker-flow.env` (from the `.example`): the usual worker variables plus
`FLOW_YESCAPTCHA_KEY`, the YesCaptcha client key. It is a secret of this worker only: read once at start, removed from
the environment, never a setting, never logged. Without it every job is retried as `flow.captcha_failed` and fails
refunded after its attempts; checks still run. Each account should have a proxy: every call to Google, every result
download and every call to YesCaptcha's API goes through it (owner, 23/10; direct only for an account with none). The
token task itself is proxyless: the solver mints the token from its own network.

**Account.** Import a Cookie-Editor JSON export of flow.google.com (a normal browser profile, closed right after the
export; never sign that profile out: the Google account is shared with Gemini). Only google.com cookies are sealed;
`__Secure-1PSID` must be there. The first check names the account by its email and makes its Flow project
(`provider_state.project`, free).

**Session keeping.** `__Secure-1PSIDTS` ages out in about 20 min. Every run of an account (the check every 5 min, a
submit, a poll, a reconcile) first calls `RotateCookies` when `flow.rotate_interval_s` (600) has passed, and writes the
rotated jar back into the sealed credential at once (`RunCtx.renew_credential`: same key, compare-and-swap on the exact
sealed value the run read, only at the credential generation it read, the generation unchanged; when another run
renewed first, the two jars are merged cookie by cookie, the later rotation winning, and written once more), then
every cookie a page load refreshed when the run ends. The checks also run while flow is drained (`flow.claims_enabled`
false, the default), so an imported account is kept signed in before flow takes jobs. A signed-out page is one spaced strike and a rest with a recheck; three counted strikes end the account.

**An image job.** Load the project page; per try: a reCAPTCHA Enterprise token (YesCaptcha
`RecaptchaV3TaskProxylessM1`, action `IMAGE_GENERATION`, 10-17 s); a new batch id written to the job; marked sent; one
`ogiZ0b` POST, whose reply flow holds until the image is drawn (13-17 s) and which carries the signed
`flow-content.google` JPEG link (`Completed`, fetched through the proxy).

- The immediate refusal (HTTP 200, the reply item with no payload and error code 7 `PUBLIC_ERROR_UNUSUAL_ACTIVITY` or
  8 `..._TOO_MUCH_TRAFFIC`) made nothing and charged nothing (measured): the next try uses a new token and a new batch
  id after `flow.captcha_retry_gap_s` (60), up to `flow.captcha_max_tries` (5) inside the 900 s submit bound. Then the
  job goes back to the queue (`flow.captcha_refused`, one attempt).
- Any other reply that is not a drawn image is `unknown`: the reconciler reads the project listing (`Zzl0ze`) for any
  batch id the job sent. Listed is found (then `as29s` signs the link); not listed stays unknown (an image is listed
  about a minute after its reply) until `flow.render_deadline_s` (900) fails the job with its refund. It is never sent
  again.
- After any generate the account's image pool rests `flow.account_min_gap_s` (300): YesCaptcha tokens were refused at a
  fast rate and accepted after a 12-min pause, so measure the accept rate per account and per IP before raising load.

**A video job.** Load the project page; read the account's Flow credits (`nzlxg`, free): fewer than the video costs
and the job goes to another account (`pool.account_out_of_points`), nothing sent. Then ONE try: a token for
`VIDEO_GENERATION`, a batch id written to the job, marked sent, one `YhhmEf` POST. The recorded acceptance (our batch
echoed, the media record of our project) is accepted with the new credit balance; the job is then polled (`jwpduf`
status 6 queued, 2 rendering, 3 done; `as29s` gives the mp4 link). Anything else, the 7/8 envelope included (proven
on images only, so on a video it is no proof nothing was made), is `unknown`: reconciled by the batch, never re-sent,
refunded at `flow.render_deadline_s`. A status never recorded waits, is named once in a job event, and ends at the
deadline with its refund. Both pools rest after any generate.

| Setting | Default | Meaning |
|---|---|---|
| `flow.claims_enabled` | false | drain mode: no new job; the checks (and so the session upkeep) go on |
| `flow.rotate_interval_s` | 600 | between two `RotateCookies` of one account (120-900) |
| `flow.captcha_provider` | yescaptcha | the token source; only `yescaptcha` is built |
| `flow.captcha_max_tries` | 5 | tries of one job against the reCAPTCHA check |
| `flow.captcha_retry_gap_s` | 60 | between two tries of one job |
| `flow.account_min_gap_s` | 300 | the pool rest after a generate |
| `flow.render_deadline_s` | 900 | an unknown job not found by then fails, refunded |
| `flow.poll_interval_s` | 30 | between two reads of a found job |
| `flow.prices`, `flow.costs`, `flow.image_budget` | 1, 1, 1000 | points per image; one job of the image budget (no Flow credit); images per account per day (not measured) |
| `flow.prices` (video `<model>@8s`), `flow.costs`, `flow.video_budget` | 2, 10, 50 | points per 8 s video; its Flow credits; Flow credits per account per day (a cost above it is refused, `input.too_costly`) |
| `flow.disabled_models` | `[]` | model ids paused for every key (see "Paused models") |

## The gemini provider (`relay worker --provider gemini`)

Gemini web (gemini.google.com) over plain HTTP: no browser, no token service. Two models. `gemini-nano-banana-2`
(Nano Banana 2): an image from text (ratios 1:1, 9:16, 3:4, 4:3, 16:9, or none: gemini picks), or one input image edited
as the text asks (its shape kept, no ratio); one image per job, about 30 s, a 1408x768 PNG for 16:9 with Gemini's
visible watermark. `gemini-omni-video` (Gemini Omni, the web app's "Create video"): a 10 s 1280x720 MP4 with sound from
text, or from one image and text; 16:9 only (portrait is not measured); about 1-2 min. Video needs an account whose plan
includes it (AI Pro measured; a free account answers `gemini.not_entitled`, refunded). The protocol is in
`fixtures/gemini/FINDINGS.md`.

**What the worker needs.** `deploy/worker-gemini.env` (from the `.example`): the usual worker variables, no secret of its
own. Each account should have a proxy: every call to Google and every result download goes through it (owner, 23/10;
direct only for an account with none).

**Account.** Import a Cookie-Editor JSON export of gemini.google.com from a browser profile used for gemini ALONE
(owner, 24/10: one independent Google session per provider; flow has its own). Only google.com cookies are sealed;
`__Secure-1PSID` must be there. A profile signed in to several Google accounts: wrap the export in a JSON object with
the export under `cookies` and the address of the Google account meant under `email`. The check then finds that
account's `/u/N` index (the owner's 24/10 session: five accounts, the AI Pro one at `/u/4`) and keeps it with the account's default mode in the
account's provider state; every job goes there. Without `email` the jobs go to `/u/0`. An email the session does not
hold makes the check unknown with a 10-min rest and a recheck (no strike); so does a mode list that cannot be read
(the kept mode is cleared). A job on an account whose page is not that email, or that no check has resolved yet, is
retried before anything is sent (`gemini.account_unresolved`). Every read of a job (reconcile, poll, fetch) first loads
the page and checks it is that email, looking for its index again if the session's order moved; when it cannot, it
reads nothing (still unknown, or pending). The check
names the account by its email. Before the import, stop anything else
that rotates that session (the spike's keeper container `gemini-spike-keeper` on the box): two rotators end it.

**Session keeping.** As flow: every run (the check every 5 min, a submit, a poll, a reconcile, a fetch) first calls
`RotateCookies` when `gemini.rotate_interval_s` (600) has passed and writes the rotated jar back into the sealed
credential at once (compare-and-swap, merged cookie by cookie with a concurrent renewal, the later rotation winning),
then every cookie a page load refreshed when the run ends. The checks also run while gemini is drained
(`gemini.claims_enabled` false, the default). A signed-out page is one spaced strike and a rest with a recheck; three
counted strikes end the account.

**A job.** Load the page at the account's `/u/N`; for an edit or an image to video, upload the input image
(`push.clients6.google.com`, free, `?authuser=N`; not taken: `gemini.upload_failed`, retried); write the job's request
id; mark sent; ONE `StreamGenerate` POST with the image or the video tool, the account's mode and the customer's words
verbatim (a tracking code typed in the prompt was drawn into the image, so the core frame renders none). An image
answer streams until the image is drawn; a video answer says in 10-33 s that the video is being made.

- A complete answer (every line read, closed by gemini's end item) with an image is `Completed`: the image's link is
  resolved with the session (`=d-I`, a few text hops on lh3/work.fife/lh3.google.com, `authuser=N` on `/u/N`) and the
  PNG downloaded from lh3 through the proxy.
- A complete video answer with the "generating your video" item and a chat is accepted, whatever other text comes
  with it (only the two proven refusals below win over it); the poll reads the chat every
  `gemini.poll_interval_s` until the video record is there (done), and the fetch resolves its link with `=m22` to the
  720p MP4 on a googlevideo.com host, downloaded through the proxy with no cookie.
- A complete answer with a final text and no result fails the job with its refund: `gemini.not_entitled` for the
  "upgrade your subscription" answer, `gemini.video_limit` for "I can't create more videos for you today" and its
  rewordings (case and punctuation ignored; the account's video pool also rests `gemini.video_limit_rest_s`),
  `gemini.refused` for any other, the text kept. A video refusal that reads like a quota (limit, quota, today,
  tomorrow, or the same in Vietnamese, Spanish, French, German, Japanese or Chinese) also rests the video pool.
- Anything else (no answer, a timeout, a cut or unreadable answer, a status that is not 200, an error item, an answer
  that promises an image with no record) is `unknown`. The reconciler reads the chat the answer named, or else the
  chat list (`MaZiqc`, the newest 13): a chat started within 60 s before / 300 s after the send whose prompt is the job's
  exact text is the job's (found: the poll reads it, an image is done, a plain answer fails refunded). Not found stays
  unknown until `gemini.render_deadline_s` (900) fails the job with its refund (a cut send left no chat in the spike).
  It is never sent again.
- After any send the account's image and video pools rest `gemini.account_min_gap_s` (30).

| Setting | Default | Meaning |
|---|---|---|
| `gemini.claims_enabled` | false | drain mode: no new job; the checks (and so the session upkeep) go on |
| `gemini.rotate_interval_s` | 600 | between two `RotateCookies` of one account (120-900) |
| `gemini.account_min_gap_s` | 30 | the pool rest after a send (gemini's own rate limit not measured) |
| `gemini.render_deadline_s` | 900 | an unknown job not found by then fails, refunded |
| `gemini.poll_interval_s` | 30 | between two reads of a found job |
| `gemini.prices`, `gemini.costs`, `gemini.image_budget` | 1 (image), 2 (video); 1 use each; 100 | points per image or video; one use of the image or the video budget; images per account per day (not measured) |
| `gemini.video_budget` | 15 | videos per account per day (AI Pro: a video uses 453 of 2400 units per 5 h, 48384 per week) |
| `gemini.video_limit_rest_s` | 18000 | the video pool's rest after the "no more videos today" answer |
| `gemini.disabled_models` | `[]` | model ids paused for every key (see "Paused models") |

## The qwen provider (`relay worker --provider qwen`)

Qwen Chat (chat.qwen.ai) over plain HTTP; Chrome only mints the anti-bot pair. Models: `qwen-image-3.0` (text to
image and one-image edit; ratios 16:9 default, 1:1, 9:16, 4:3, 3:4, 3:2, 2:3) and `qwen-happyhorse-1.1`,
`qwen-happyhorse-1.0` (text or one image to a 720p video with sound, 5/10/15 s; ratios 16:9 default, 4:3, 1:1, 3:4,
9:16). Wan 2.7 is hidden in the web app's picker and not offered; HappyHorse 1080P is for paid plans. The protocol is
in `fixtures/qwen/FINDINGS.md`.

**What the worker needs.** The browser image (`worker-qwen`, target `browser`, profiles on `profiles-qwen`) and
`deploy/worker-qwen.env` with the usual worker variables. No key of its own.

**Account.** A Cookie-Editor JSON export of chat.qwen.ai (`refresh_token` on `.qwen.ai` is required). Each run buys a
15-min access token at `auth.qwen.ai/api/v2/auths/refresh` and reuses it for 10 min in the process. The check (every
10 min) reads the credits (`totalCredits`, the account's `video` balance) and, while `qwen.claims_enabled` is true,
mints a new pair at half `qwen.bx_max_age_s`. A refresh that says the session is gone is one spaced strike and a rest
with a recheck. Live 23/10: both spike accounts' refresh tokens came back "Token is revoked, please login again"
minutes after a working refresh (one right after a first browser mint, one with no browser run): watch for it.

**The pair.** Chrome loads chat.qwen.ai with the jar through the account's proxy, the page gets a prompt and Enter,
and its `chat/completions` is failed inside the browser (CDP Fetch, Request stage) after its headers (`bx-ua`,
`bx-umidtoken`, `bx-v`, the user agent and client hints) are kept; the page's sign-out and log-out calls are failed
too, and the browser's cookies replace the jar (written back even when the mint fails). The pair is reused by every
send until `qwen.bx_max_age_s` (1500); one browser at a time per worker.

**Send.** Free steps first (token, pair, video credit check `credits/verify`, the input image to OSS, a new chat), then
the chat id is written to the job, `mark_sent`, and ONE `chat/completions`. An image is drawn in that stream (60-110 s,
`Completed`); a video answers its task id at once (`Accepted`, then `task/status` is polled). After the send:
- the Aliyun WAF's edge block, matched exactly as recorded (HTTP 200, `bxpunish: 1`, `bxuuid`, an `x5secdata` cookie,
  none of the app's `x-request-id`/`x-actual-status-code`, the body `{"ret": ["FAIL_SYS_USER_VALIDATE", "RGV587_ERROR::…"],
  "data": {"url": "…/_____tmd_____/punish?…"}}`): the request never reached qwen, so the pair is dropped and the job
  requeued (`qwen.waf_refused`, the first two free). Anything only like it is unknown;
- "Insufficient Credits" fails the job (`qwen.out_of_credits`, refunded) and empties the video pool; a complete answer
  in words with no image (image) or no task id (video) fails it at once (`qwen.refused`, refunded);
- anything else is unknown: the reconciler reads the chat (a message of ours: found; none: still unknown, never "not
  sent", as the read may lag the send: the render deadline fails the job with its refund, it is never sent again). A found
  video whose chat holds a reply in words and no task is failed as refused (refunded) `qwen.refusal_grace_s` after the
  send; a task named meanwhile is followed. A task that ends failed is refunded at once (`qwen.render_failed`).

| Setting | Default | Meaning |
|---|---|---|
| `qwen.claims_enabled` | false | drain mode: no new job; the checks go on (no mint while drained) |
| `qwen.bx_max_age_s` | 1500 | a pair is reused this long; the check mints at half of it |
| `qwen.account_min_gap_s` | 60 | the pools' rest after a generate |
| `qwen.poll_interval_s` | 30 | between two reads of a rendering video |
| `qwen.image_lost_after_s` | 900 | an image chat still empty this long after the send fails, refunded |
| `qwen.refusal_grace_s` | 300 | a lost video answer whose chat shows words and no task is refused after this |
| `qwen.render_deadline_s` | 2700 | an unfinished job is given up then, refunded |
| `qwen.prices` | 1 per image, 1/2/3 per 5/10/15 s video | points, keyed `model` or `model@<s>s`; a missing option is refused |
| `qwen.costs` | 1 per image, the qwen price list per video (HappyHorse 1.1 30/60/90, 1.0 40/80/120) | one job of the image budget, or qwen credits |
| `qwen.image_budget`, `qwen.video_budget` | 1000, 40 | images per account per day; video credits per account per day |
| `qwen.disabled_models` | `[]` | model ids paused for every key (see "Paused models") |

## The box runbook

Relay runs on the LAN box, in its own compose project `relay`, next to projects that are not ours (`portfolio` and
`billiards`). Every production action is the owner's or follows his go-ahead. Nothing here stops, execs
into, inspects beyond metadata, or prunes anything foreign; relay removes only what it names, and never runs a prune.

### Where things live

| What | Where |
|---|---|
| Deployed tree | `~/relay-deploy/`: the committed tree (`git archive <sha>`, fingerprinted as `box.sh` does), outside `~/relay-runs` |
| Env files | `~/relay-deploy/deploy/<service>.env`, mode 0600, never committed; the templates list names only |
| Images | built on the box, `relay-<service>:<sha>` (a rollback is a retag) |
| Test and gate runs | `~/relay-runs/<folder>/` through `scripts/box.sh` (see above), under its CPU budget |
| Live-window marker | `~/relay-deploy/live-window` |
| Logs of replaced containers | `~/relay-logs/<service>/<UTC time>-<random>.log.gz`, kept by `deploy.sh`/`rollback.sh` (see "Logs") |
| Profiles of the dola worker | the named volume `profiles-dola`, mounted at `/profiles` (`RELAY_PROFILE_ROOT`) |

### Deploy and rollback

Run from the Mac, in a checkout where the commit is pushed (`git fetch` first):

```bash
scripts/deploy.sh <sha> [--workers dola,meta] [--allow-migrate]
scripts/rollback.sh [<sha>] [--workers dola,meta] [--allow-schema-mismatch]
```

`deploy.sh` does the manual steps below in order: it refuses a commit that is not an ancestor of `origin/main`, a box
with a live window (exit 77), a busy `~/relay-runs` folder or a held `gate.lock` (exit 75), and an unreadable or equal
deployed sha. It copies `git archive <sha>` into `~/relay-deploy-new` with the live tree's env files (`cp -p`, never
printed) and the new `GIT_SHA`, builds `migrate`, `api` and each named worker there, runs `migrate` only when the
migrations differ from the deployed tree's and `--allow-migrate` was given (otherwise exit 3, nothing built), then
rotates (`~/relay-deploy` becomes `~/relay-deploy-<old sha>`; older `relay-deploy-<sha>` trees and their `relay-*`
images go, unless a running container uses one). Then `up -d --no-deps api`, `/healthz` 200 and the admin bundle
served. Each `--workers` provider is drained twice (the second must print "(no change)"), waited on until no job of
it is `submitting`, restarted with `up -d --no-deps worker-<p>`, checked by a fresh heartbeat of the new process and
Chrome's version, then resumed. Without `--workers` no worker is touched. A failing step stops the run and says where
things stand; nothing is rotated before the build (and the migration) succeeded, and a worker whose check failed stays
drained.

Both scripts take `~/relay-runs/gate.lock` exclusive from their first box check to their end (one ssh session keeps
it), so no `box.sh` run, gate, second deploy or rollback starts meanwhile (exit 75). Before every step on the box the
script checks that this session is alive and still answers; if it was lost, the run stops there (exit 1) and says
where things stand. ssh keeps the connection checked (`ServerAliveInterval=15`), and the end of a run never waits
more than about 10 s for the session to close.

`rollback.sh` makes the same box checks (live window, busy folders, the lock), swaps `~/relay-deploy` with the kept tree (the only `relay-deploy-<sha>`, or the one named), restarts the
api and, with `--workers`, each worker as above. It refuses when the tree or its images are missing, and when the two
trees' migrations differ unless `--allow-schema-mismatch` (exit 3): a rollback never undoes a migration, so the older
code would run against the newer schema. It never runs `migrate`, never removes an image and never touches a volume.
The env files come back as they were in that tree. Exit codes of both: 0 done, 1 a step failed, 2 refused, 3
migrations, 75 box busy, 77 live window. `tests/test_deploy_sh.py` runs both against a fake box.

The manual steps stay below as the reference for what the scripts do.

### Ordered deploy, with a stored drain

0. **Before the first `docker compose -p relay up`** run `scripts/box.sh --status`: no folder may be busy and `gate.lock`
   must be free. A gate that started earlier decided "uncapped" at its start and would run next to production; wait for
   it or `--clear` it. (Same check as before a live window.)
1. Start `postgres` and `minio` (`docker compose -p relay up -d`), run `migrate` to completion
   (`--profile app run --rm migrate`: roles, every migration, the `BILLING_TZ` pin), then start `api`
   (`--profile app up -d api`).
2. **Store the drain:** `relay provider drain dola`. Run it a second time: it must print "(no change)". (That
   holds from the fix of the drain message on: until it is deployed the first run prints "(no change)" too, because the
   command reads the stored value as "unchanged".) The admin settings read must show `dola.claims_enabled` false with
   source `db`: that read is the check until the fix is deployed. A fresh database would claim by default and a redeployed one
   may hold "on", so no deploy relies on the default.
3. Start the worker alone, as its own command: `--profile dola up -d worker-dola`. Never `--profile app --profile dola
   up` in one command: the worker would start before step 2.
4. The drained state and Chrome are checked by the settings read above, by the worker's heartbeat row
   (`worker_heartbeats`, a fresh `at`, 0 consecutive errors; slots may be busy, since a drained worker still runs its
   account checks, and they open Chrome) and by `chrome --version` in the image
   (`docker exec relay-worker-dola-1 /opt/google/chrome/chrome --version`: 154.0.8037.92), not by the log: the worker logs
   nothing at the default level while it is idle. The worker has no HTTP port; its health is that heartbeat row. No
   job is in flight while drained: `scripts/deploy.sh` waits for no job of the provider `submitting`, not for 0 busy
   slots.
   Then `relay provider resume dola` when the accounts are imported (the account checks run while drained).
5. `migrate` runs before a new worker: the worker writes `worker_heartbeats.limits`, and before migration 0019 its
   first heartbeat fails.
6. A worker image older than the settings change must not start under this compose file: it still needs
   `DOLA_BROWSER_SLOTS_MAX`, which the compose file no longer sets.
7. After the new `worker-dola` reports on the Containers card, delete `DOLA_BROWSER_SLOTS_MAX` from `deploy/.env`.
8. Rollback to an older worker puts that line back and uses that commit's compose file.

A one-off `docker run` of a deployed image (keygen, admin create-user, a smoke test) inherits the compose labels
`com.docker.compose.project=relay` and `...service=<service>` from the image, so `docker compose -p relay ps/stop/down` and
the gate's cap detection would take it for production. Give it the two labels `com.docker.compose.project=relay-oneoff`
and `com.docker.compose.service=oneoff`, or prefer `docker compose -p relay run --rm <service> ...`, which marks it as a
one-off container of the project. Never run a drill under `-p relay` or from `~/relay-deploy`; a scratch stack has its own
`-p relay-drill-<name>` and its own directory.

Rollback: `docker compose -p relay stop` (volumes kept). A relay volume is removed only by name and only on the owner's word.

### Limits and abort thresholds

Every service starts under CPU, memory and PID limits from `deploy/compose.yml`; the variables are read from
`deploy/.env` (`<SERVICE>_CPUS`, `<SERVICE>_MEM_LIMIT`, `<SERVICE>_PIDS_LIMIT`; `DOLA_MEM_LIMIT` is the dola worker's).
The defaults hold relay to at most half of the box (32 cores, 62 GB); `tests/test_deploy.py` pins that each service has
all three. They may be lowered by the box survey and changed after the load test only through a deploy recorded in the
go-live log, and only inside the abort threshold.

| Service | CPUs | Memory | PIDs |
|---|---|---|---|
| `worker-dola` | 8 | 12 GB | 2048 |
| `postgres` | 4 (deployed: 2) | 4 GB | 512 |
| `minio` | 2 (deployed: 1.5) | 2 GB | 512 |
| `api` | 2 (deployed: 1.5) | 2 GB | 512 |
| `migrate` (one-shot) | 1 | 1 GB | 256 |
| `worker-meta` (not started) | 4 | 6 GB | 1024 |
| `worker-vibes` (not started) | 1 | 1 GB | 256 |
| `worker-flow` (not started) | 1 | 1 GB | 256 |

**Deployed values (13/10, `deploy/.env` on the box):** `DOLA_CPUS` 8, `POSTGRES_CPUS` 2, `MINIO_CPUS` 1.5, `API_CPUS` 1.5, memory
and PID limits as in the table. Why: relay's running limits plus the 12-core gate cap plus twice the foreign peak
(about 0) must stay within 80% of the 32 cores, 25.6. The defaults sum to 16 + 12 = 28. The dola worker is the only
service with real CPU load (Chrome on four slots), so it keeps 8; Postgres, MinIO and the api serve about 18 videos a
day and one uvicorn process, so they were lowered: 8 + 2 + 1.5 + 1.5 = 13, and 13 + 12 = 25 fits. The load test can move
any of them through a deploy recorded in the go-live log.

`worker-dola` also gets `RELAY_PROFILE_ROOT=/profiles` from `compose.yml` itself, so a cold profile directory cannot
come from a forgotten line in the env file; `api` stops with a 30 s grace and `worker-dola` with 780 s.

The Settings page's Containers card shows each relay service's limits as read inside the container. It is the check
after a deploy that changes them.

**Box-wide abort threshold.** At the first breach the step in progress stops: run `relay provider drain dola`, then a
graceful stop of `worker-dola`, then report. Nothing foreign is touched. A breach is any one of:
- a foreign container's id, mounts or networks differ from the recorded baseline, its restart count rose, or its state
  or health is worse;
- free disk on `/` or on the Docker data root under 15% or 50 GB;
- free inodes under 10%;
- `MemAvailable` under 8 GB, or swap use growing for 5 minutes;
- the 5-minute load average above 24 (of 32 cores) for 5 minutes.

The baseline (ids, mounts, restart counts, state and health, networks, CPU and memory from `docker stats --no-stream`;
never env, logs or files of a foreign container) is recorded by the box survey and compared at the end of each
deploy step and at every read of the watches.

### The first deploy (13/10) and the recorded foreign baseline

Deployed: `fde6ad4`, compose project `relay`, images `relay-api:fde6ad4`, `relay-migrate:fde6ad4`,
`relay-worker-dola:fde6ad4`, migrations 0001 to 0018, **api on port 8100** of the box (`http://192.168.18.227:8100`),
the dola worker drained. Ports in use by others: 8010 (`portfolio`) and 8090 (`billiards`).

Foreign baseline, read with `docker inspect --format` restricted to ids, image ids, mounts, networks, restart counts,
state and health, plus `docker stats --no-stream` (never env, config, logs or exec); recorded 13/10 04:26 UTC and
unchanged at the end of the first deploy and of the drills:

| | `portfolio` | `billiards` |
|---|---|---|
| container id | `76753cd8ea30...` | `07966b703e98...` |
| image id | `af956eefe46c...` | `720d8c0d6117...` |
| mounts / networks | none / `bridge` | none / `bridge` |
| restarts / state | 0 / running, no health check | 0 / running, no health check |
| CPU / memory (idle) | 0.00% / 31 MiB | 0.00% / 25 MiB |

Host at the baseline: 32 cores, 62.9 GiB (60.5 GiB available), swap 8 GiB unused, `/` and the Docker data root on one
filesystem (166 GB free of 195 GB, inodes 2% used), load average 0.00 / 0.66 / 4.4, containerd image store.

### Slot values and the load test (14/10)

Deployed on the box: `dola.browser_slots` **6** (stored setting) and `DOLA_BROWSER_SLOTS_MAX` **8** (a line in
`~/relay-deploy/deploy/.env`; the compose default stays 4). Why 8: the reserved shares leave one submit at any size up
to 4 (see "Browser shares stay reserved"), so the ceiling of 4 allowed no concurrent submit. The load test ran image jobs
and Check all rounds on nine jars at 1, 2, 4, 5, 6 and 8 slots: 47 jobs, 0 failures, 0 cookie-loss, reload, guest or
submit-timeout lines, worker memory peak 3.9 GiB (limit 12 GiB), loop lag max 432 ms, host load5 peak 5.5, MemAvailable
never under 57 GB, swap 0. Eight slots stayed clean; 6 is the last clean step minus one for headroom.

Recreate only the worker after an env change (from `~/relay-deploy`; never `--profile app`, which would touch the api):

```bash
docker compose -p relay -f deploy/compose.yml --profile dola up -d --no-build --pull never worker-dola
```

The graceful stop (780 s) drains what is in flight; check that no job is `submitting` first. Without `-f` compose finds no
file in `~/relay-deploy` and changes nothing.

Note (19/10): the env ceiling was removed. `dola.browser_slots` (stored 6 on the box) is now the only slot knob,
bounded 1 to 12; the `DOLA_BROWSER_SLOTS_MAX` line in `deploy/.env` was deleted at the deploy of `<commit>`.

### Live windows

A live window is any period with a planned live check, a deadline drill, the load test, the client switch and its first
hour, or a later live measurement. No gate and no heavy `box.sh` run happens in one: the operator first checks
`scripts/box.sh --status` (no busy folder, `gate.lock` free; otherwise wait or `--clear`), then creates
`~/relay-deploy/live-window`, and deletes it last. `box.sh` refuses to start while it exists (exit 77); a run that was
already going is not stopped by it.

**Deadline drills.** Stop the worker first, then lower `dola.render_deadline_s`, and keep it down well past the lowered
deadline and the lease expiry (the first drills ran 75 minutes). For the "sent, then killed" drill, kill the worker
(`docker kill -s KILL`) about **5 to 8 s after the job's "sent" event**: `sent_at` is stamped immediately before Enter,
so a kill 0.8 s later probably landed before the order existed: the final reconcile found nothing, and nothing showed that the order was placed (not proven). Start the worker
still drained and with the lowered deadline, so the final read or reconcile runs under it. Cleanup always runs, in this
order: delete (or set back) the stored deadline, read it back and compare it with the value recorded before, and only then
`relay provider resume dola`. Jobs cannot be pinned to a jar: run a drill alone, with no other job open.

### The secret tools

Both read real files only on the box at intake and are tested here with generated values only. Plain secret files sit
on the box only between the copy and their deletion, in a directory outside every repo, `~/relay-runs`, the deploy
directory and any build context (directory 0700, files 0600).

- **`scripts/import-accounts.py`** posts the jars once to the api on loopback:
  `python3 scripts/import-accounts.py --cookies COOKIES.jsonl --proxies PROXIES.txt --label-prefix n11 --username NAME`.
  Line n of the two files is one account (`host:port:user:password` or `user:password@host:port`). It asks for the admin
  password on the terminal without echo, keeps the session token in memory, refuses a non-loopback `--base`, a file with
  group or other access and files of different lengths, and prints counts and the server's known rejection reasons only
  (anything else prints as `other`). The connection ignores `http_proxy` and refuses redirects, and the run needs a
  terminal (`ssh -t`). Every account gets a label (`--label-prefix`, default `imp-` and the start time). It prints the
  account count before and after. **If the answer to the import is lost** (no answer, a cut or garbled answer, a 5xx),
  it reads the accounts again and decides by the labels it posted, created since the request started (not by the
  provider's count, which other operators change): "NOT applied" (exit 2, running it again is safe), "WAS applied" (exit 4,
  do not run it again) or "outcome unknown" (exit 3: the accounts could not be read; list them by the label prefix before
  anything else). Labels are not unique, a second run of an applied import duplicates every account, so before posting the
  script stops (exit 2, nothing sent) if any label it would post already exists. **The import runs alone**: the outcome
  check reads the newest 200 accounts, so no other bulk import may run at the same time.
  `--dry-run` parses and counts without a network call. Exit 0 all imported, 1 some rejected, 2 refused or failed,
  3 outcome unknown, 4 answer lost but applied.
- **`scripts/secret-scan.py`** counts planted secret values per target and prints counts only; no option takes a value.
  - At intake, while the files exist, make the keyed-hash list: `secret-scan.py hashes --secrets COOKIES.jsonl PROXIES.txt
    --key KEY --out HASHES` (key and list 0600, outside every repo; both deleted in the close step).
  - Scan: `secret-scan.py scan --secrets COOKIES.jsonl PROXIES.txt --path ~/relay-deploy --path ~/.bash_history`, and
    streams with a label: `docker logs relay-api-1 2>&1 | secret-scan.py scan --secrets ... --stdin api-logs`,
    `docker inspect <relay containers> | ... --stdin inspect`, `ps -eo args | ... --stdin process-args`, and the kept
    logs: `zcat ~/relay-logs/*/*.log.gz | ... --stdin kept-logs`.
  - After the files are deleted use `--hashes HASHES --key KEY` in place of `--secrets`.
  - A value under 8 characters is not scanned (the count skipped is printed). Every count must be 0; the exit status is 1
    when any value was found, 2 when nothing was found but something could not be read.
  - Secret files are read as strict UTF-8: a file that is not valid UTF-8 stops the run ("secret input unreadable", exit 2).
  - Each target prints `values`, `occurrences`, `unreadable` and `bytes`. An unreadable file or directory, or a link (to a
    file or to a directory), inside a scanned directory or named as the target itself, that is not followed, is counted as `unreadable` and is never clean; `bytes=0` on a stream means the stream was empty (a
    wrong container name), not that it is clean. Every `unreadable` must be 0 too.
  - A value is a cookie `value`, and for a proxy line (`host:port:user:password` or `user:password@host:port`, with or
    without a scheme) the whole line, the user, the password and the base64 of `user:password` (never the host); the user, the password and the base64 pair are also stored URL-decoded
    (an `@`-form line is decoded by the server, so `p%40ss` is stored as `p@ss`).
  - `--hashes` costs about 5 s per MB of data per 100 distinct value lengths: a container's 100 MB of logs takes minutes.
    A value that holds a separator (space, comma, semicolon, quote, bracket) is also stored as its pieces of 8 characters
    or more (`hashes` prints how many values this applies to); pieces shorter than 8 cannot be found in that mode.

### Chrome sandbox (tried 12/10, kept off)

`relay_browser/session.py` still launches Chrome with `--no-sandbox`. Tried on the box in the `browser` image, as a
non-root user (10001) without `--no-sandbox`:
- under Docker's default seccomp profile it fails: `Failed to move to new namespace ... Operation not permitted`, the
  zygote exits, Chrome aborts (the host allows user namespaces: `max_user_namespaces` 257238);
- as root it refuses: "Running as root without --no-sandbox is not supported";
- with `--security-opt seccomp=unconfined` it works.

Running the workers with seccomp off trades a Chrome sandbox for the loss of the container's syscall filter, which is
the wrong direction. A narrower profile (Docker's default plus user-namespace creation) would work but is a new
security surface, so it is the owner's choice and has not been adopted. The risk stays as before: a taken-over renderer
runs as root inside the worker container (third-party pages are rendered), and the private key stays out of its reach only
because Chrome receives a name list of the environment (`chrome_env`, `CHROME_ENV_NAMES`), not the worker's whole
environment. The owner decided on 13/10: the sandbox stays off for the go-live. Chrome is bumped about monthly, and
at once when an exploit is published, and every bump first passes the cookie-loss check; the sandbox is revisited
after the live run is stable.

## Restore runbook

**Not in use while there is no backup.** Kept for the day an off-box backup exists; nothing below has been drilled on the box.

Order matters: `restore_mark` writes whole tables and a running worker would claim jobs the dump knows nothing about.

1. Restore the database from the dump and start the api. Set the global setting `media_orphan_sweep` to `false` first
   (restoring MinIO from the nightly mirror happens later; a sweep now would delete files whose rows are not back).
2. Start **no worker**.
3. Run `relay jobs restore-mark --dump-time T` (ISO 8601 with an offset, for example `2026-10-03T02:00:00+07:00`; no
   offset exits 2). It runs as `relay_worker` (or the owner) with a 600 s statement timeout and a 30 s lock timeout.
   It refuses (`WorkersRunning`, exit 1) while any worker heartbeat is fresher than 45 s. It locks the heartbeat table
   and then the leases, jobs, commands, accounts and capacity rows. A worker that boots meanwhile **waits**: it writes
   its first heartbeat before any loop starts and retries that write every 1 s, logging that a restore mark holds the
   heartbeat table, until the mark has committed (a stop signal ends the wait). It does not exit, so Docker's restart
   loop is not involved; still start none until the mark is done. It marks every non-terminal job created before `T` plus the longest submit timeout as
   `unknown` (never `queued`; reconcile proves each one), except a `queued` job never claimed. A job that was claimed,
   requeued and has no account fails with refund at its render deadline and is never re-sent. Queued commands
   fail, `left_n` is rebuilt from reported balances, every account is due for a check. It also clears each job's late
   stamp and last answered read, so a job that was late at dump time is read again before it can fail. It prints
   `marked N job(s) unknown`.
4. Start the workers in drain mode (`<p>.claims_enabled` false; set it with `relay provider drain X` before they
   boot), let the reconciler and the checks settle, then `relay provider resume X`.
5. Reconcile MinIO and the rows, then set `media_orphan_sweep` back to `true`.

## What an operator must know

- **A job that keeps killing its worker fails.** Each time the reaper requeues an unsent job whose worker died
  it counts an attempt; at the provider's `max_attempts` the job fails with refund. The count covers every unsent job
  the dead worker held, so an innocent job sharing every crash fails and is refunded with it.
- **Upstream completed means the account keeps the charge.** A failed job whose order upstream finished (for
  example the fetch failed) keeps its account-pool charge, because the upstream account spent the credit. The client's
  key is refunded as for every failed job.
- **A dead proxy rests the account.** A failed proxy probe rests the account on every pool for the provider's
  proxy rest, 30 minutes (1 800 s) by default (`Caps.proxy_rest_s`); no browser opens through it.
- **The account check interval is a provider constant** (`Caps.check_interval_s`, 21 600 s by default), not a setting.
- **Admin account actions are worker commands** (wake, check, check all, proxy re-check, delete): the api queues
  them, the provider's worker runs them, so they wait while the worker is drained or stopped. A command aborted by a
  database deadlock or serialization failure is re-queued, a bounded number of times; other errors fail it.
- **Browser shares stay reserved:** reconcile up to 2 and check plus command 1, even when idle, so the submit
  share is `slots - 3` and never below 1. Up to 4 slots therefore give exactly one submit at a time; concurrent
  submits start at 5 slots. More submit slots mean a higher `<p>.browser_slots`, within the 1 to 12 the provider allows.
- **A late render is read once more before it fails.**
  - A job past `<provider>.render_deadline_s` fails only after one read that started after the deadline and that the
    provider answered: a poll that said "not ready", or a reconcile that found nothing. A read that failed does not
    count (for dola: a chain error, a signed-out account, a failed listing), and no job is failed while a read of it is
    running.
  - At the latest it fails 1 hour after a running worker first saw it late (the backstop, a constant in the core). After
    a long outage that hour starts at the restart, not at the deadline. So expect a job that cannot be read (a
    signed-out account) to fail up to 1 hour after its deadline, not at it. The cause code and the unclear-reply
    report are the same as before.
  - A video the final read finds is delivered normally (one charge, no refund) and keeps its download retries for 30
    minutes. An order that a reconcile finds after the deadline is held until its video or the backstop: a later "still
    rendering" poll does not end it.
  - An order proven "not sent" after the deadline is never sent again: the job fails with a refund.
  - For the first 5 minutes after a worker starts its job loops, the backstop and the download window fail nothing, so
    the reads that are due at start run first.
