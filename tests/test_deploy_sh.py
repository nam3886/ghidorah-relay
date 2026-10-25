"""scripts/deploy.sh and scripts/rollback.sh against a fake box: ssh runs the remote script here, with HOME a directory
that plays the box user's home and fake docker, curl and sleep on PATH that record their calls and answer the way the
box would. The trees, the env files and the rotation are real directories; flock is real (the tests run on the box)."""

import fcntl
import gzip
import importlib
import os
import shlex
import stat
import subprocess
import time
from pathlib import Path

from relay_core.testing.mutation import mutation

from guards import REPO

DEPLOY = "scripts/deploy.sh"
MIG = "packages/relay-core/src/relay_core/db/migrations"
SECRET = "S3CRET-VALUE-0042"
SNAPSHOT = "relay-claims-snapshot"
ENV_FILES = (".env", "api.env", "migrate.env", "worker-dola.env", "worker-meta.env", "worker-flow.env",
             "worker-gemini.env", "worker-qwen.env")

SSH_STUB = """\
#!/usr/bin/env bash
env | grep -vE '^(HOME|PATH|PWD|SHLVL|_|OLDPWD|SSH_AUTH_SOCK)=' | sed 's/^/ENV: /' >> "@LOG@" || true
echo "SSH: $*" >> "@LOG@"
set -a; . "@CONTROL@"; set +a
while [ "${1:-}" = -o ]; do shift 2; done
shift
export HOME="@HOME@" PATH="@BIN@/box:$PATH"
cd "$HOME"
if [ -n "${STUB_HOLDER_HANGS:-}" ] && [[ "$*" == *"flock -x -n 9"* ]]; then   # a session that never ends
  bash -c "$*"; exec /bin/sleep 300
fi
exec bash -c "$*"
"""
DOCKER_STUB = """\
#!/usr/bin/env bash
set -a; . "@CONTROL@"; set +a
S="@STATE@"
live=$(sed -n 's/^GIT_SHA=//p' "$HOME/relay-deploy/deploy/.env" 2>/dev/null | tail -1)
mkdir -p "$HOME/relay-runs"                  # could a box.sh run take gate.lock (shared) right now?
gate=$(flock -s -n "$HOME/relay-runs/gate.lock" true 2>/dev/null && echo free || echo held)
echo "DOCKER [$(basename "$PWD") live=$live]: $* {gate=$gate}" >> "@LOG@"
touch "$S/images"
case "$*" in
  *" build "*)
    [ -z "${STUB_BUILD_FAIL:-}" ] || exit 1
    if [ -n "${STUB_KILL_HOLDER:-}" ]; then   # the lock's ssh session drops during the build
      for fd in /proc/[0-9]*/fd/*; do
        [ "$(readlink "$fd" 2>/dev/null)" = "$HOME/relay-runs/gate.lock" ] || continue
        p=${fd#/proc/}; kill -9 "${p%%/*}" 2>/dev/null || true
      done
    fi
    sha=$(sed -n 's/^GIT_SHA=//p' deploy/.env | tail -1)
    after=0
    for w in "$@"; do [ "$after" = 1 ] && echo "relay-$w:$sha" >> "$S/images"; [ "$w" = build ] && after=1; done ;;
  *"run --rm migrate"*) exit "${STUB_MIGRATE_FAIL:-0}" ;;
  *" logs --no-color --timestamps "*)
    [ -z "${STUB_LOGS_FAIL:-}" ] || exit 1
    [ -n "${STUB_LOGS_EMPTY:-}" ] || echo "2026-10-24T01:00:00Z a line of ${!#}" ;;
  *"relay provider drain "*)                 # $S/claims-<p>: the stored <p>.claims_enabled (none: no row)
    p=${!#}
    if [ -n "${STUB_DRAIN_NO_STORE:-}" ]; then echo "draining $p"
    elif [ "$(cat "$S/claims-$p" 2>/dev/null)" = false ]; then echo "draining $p (no change)"
    else echo false > "$S/claims-$p"; echo "draining $p"; fi ;;
  *"relay provider resume "*) p=${!#}; echo true > "$S/claims-$p"; echo "resumed $p" ;;
  *"exec -T postgres"*)
    sql=${!#}
    case "$sql" in
      *submitting*)
        seq=(${STUB_SUBMITTING:-0}); i=$(cat "$S/sub-i" 2>/dev/null || echo 0); echo $((i + 1)) > "$S/sub-i"
        echo "${seq[$i]:-0}" ;;
      "SELECT now()") echo "2026-10-23 01:00:00+00" ;;
      *worker_heartbeats*) echo "${STUB_HB:-1}" ;;
      "SELECT value FROM settings_kv WHERE key = '"*".claims_enabled'")
        p=${sql#*key = \\'}; p=${p%%.claims_enabled*}
        case "${STUB_CLAIMS_READ:-}" in
          "") cat "$S/claims-$p" 2>/dev/null || true ;;
          odd) echo 1 ;;
          *) exit 9 ;;
        esac ;;
      *) exit 9 ;;
    esac ;;
  *"chrome --version"*) echo "Google Chrome 154.0.8037.92" ;;
  "image inspect "*) grep -qxF "$3" "$S/images" || exit 1 ;;
  "image rm "*) grep -vxF "$3" "$S/images" > "$S/images.tmp" || true; mv "$S/images.tmp" "$S/images" ;;
  "ps --format"*) printf '%s\\n' ${STUB_IN_USE:-} ;;
esac
exit 0
"""
CURL_STUB = """\
#!/usr/bin/env bash
set -a; . "@CONTROL@"; set +a
echo "CURL: $*" >> "@LOG@"
case "$*" in
  *healthz*) printf %s "${STUB_HEALTHZ:-200}" ;;
  *"/manage"*) echo '<script type="module" src="/ui/app/assets/index-AbC12_45.js"></script>' ;;
  *"/ui/app/assets/index-AbC12_45.js"*) exit "${STUB_BUNDLE_FAIL:-0}" ;;
  *) exit 22 ;;
esac
"""


class Box:
    """A repository with commits A (deployed), B, C (adds a migration) and D (not on origin/main), a fake box home where
    A is deployed with its env files, and the stubs. Keywords STUB_* steer the stubs."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.repo, self.home, self.state = tmp_path / "repo", tmp_path / "box", tmp_path / "state"
        self.log, self.control = tmp_path / "stub.log", tmp_path / "control.env"
        for d in (self.repo, self.home, self.state):
            d.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.a = self.commit({"%s/0001_init.sql" % MIG: "create table t ();\n", "deploy/compose.yml": "name: relay\n"})
        self.b = self.commit({"app.py": "print(1)\n"})
        self.c = self.commit({"%s/0002_more.sql" % MIG: "alter table t add c int;\n"})
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.d = self.commit({"app.py": "print(2)\n"})
        self.git("reset", "-q", "--hard", self.c)
        self.place(self.a, "relay-deploy")
        self.images("relay-api:" + self.a, "relay-migrate:" + self.a, "relay-worker-dola:" + self.a)
        bins = tmp_path / "bin"
        (bins / "box").mkdir(parents=True)
        subst = {"@LOG@": str(self.log), "@HOME@": str(self.home), "@BIN@": str(bins), "@CONTROL@": str(self.control),
                 "@STATE@": str(self.state)}
        for rel, text in (("ssh", SSH_STUB), ("box/docker", DOCKER_STUB), ("box/curl", CURL_STUB),
                          ("box/sleep", "#!/usr/bin/env bash\nexit 0\n")):
            for k, v in subst.items():
                text = text.replace(k, v)
            path = bins / rel
            path.write_text(text, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.bins = bins

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files: dict[str, str]) -> str:
        for rel, text in files.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "--short=7", "HEAD")

    def place(self, sha: str, name: str) -> Path:
        """The tree of `sha` as the box has it at ~/name, with its env files (mode 0600, a secret in each)."""
        tree = self.home / name
        tree.mkdir()
        archive = subprocess.run(["git", "archive", sha], cwd=self.repo, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-xf", "-", "-C", str(tree)], input=archive, check=True)
        for f in ENV_FILES:
            path = tree / "deploy" / f
            path.write_text(("GIT_SHA=%s\n" % sha if f == ".env" else "") + "TOKEN=%s-%s\n" % (SECRET, f),
                            encoding="utf-8")
            path.chmod(0o600)
            os.utime(path, (1_000_000_000, 1_000_000_000))
        return tree

    def images(self, *names: str) -> None:
        with open(self.state / "images", "a", encoding="utf-8") as fh:
            fh.writelines(n + "\n" for n in names)

    def image_list(self) -> list[str]:
        return (self.state / "images").read_text(encoding="utf-8").split()

    def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
        """STUB_* keywords go to the stubs (they run under a clean environment); others are variables of the caller."""
        self.control.write_text("".join("%s=%s\n" % (k, shlex.quote(v)) for k, v in env.items()
                                        if k.startswith("STUB_")), encoding="utf-8")
        (self.state / "sub-i").unlink(missing_ok=True)
        env = {"PATH": "%s:%s" % (self.bins, os.environ["PATH"]), "HOME": str(self.dir),
               **{k: v for k, v in env.items() if not k.startswith("STUB_")}}
        return subprocess.run(["bash", str(REPO / script), *args], cwd=self.repo, env=env, capture_output=True,
                              text=True, timeout=120)

    def deploy(self, *args: str, **stub: str) -> subprocess.CompletedProcess:
        return self.run(DEPLOY, *args, **stub)

    def rollback(self, *args: str, **stub: str) -> subprocess.CompletedProcess:
        return self.run("scripts/rollback.sh", *args, **stub)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def docker(self) -> list[str]:
        return [line for line in self.calls() if line.startswith("DOCKER")]

    def index(self, needle: str) -> int:
        """The position of the first docker call that contains `needle` (-1: none)."""
        return next((i for i, line in enumerate(self.docker()) if needle in line), -1)

    def deployed(self) -> str:
        env = (self.home / "relay-deploy" / "deploy" / ".env").read_text(encoding="utf-8")
        return [line[8:] for line in env.splitlines() if line.startswith("GIT_SHA=")][-1]

    def trees(self) -> list[str]:
        return sorted(p.name for p in self.home.iterdir() if p.name.startswith("relay-deploy"))


def unchanged(box: Box) -> bool:
    """Production as it was: A live, no previous tree made (a staged ~/relay-deploy-new may be left)."""
    return box.deployed() == box.a and set(box.trees()) <= {"relay-deploy", "relay-deploy-new"}


def test_help_describes_both_scripts():
    run = subprocess.run(["bash", str(REPO / DEPLOY), "--help"], capture_output=True, text=True)
    assert run.returncode == 0 and "scripts/deploy.sh <sha>" in run.stdout and "scripts/rollback.sh" in run.stdout


# ---- a deploy that goes through -------------------------------------------------------------------------------------
@mutation(DEPLOY, '    step drain "$p"\n    step idle "$p"\n    step worker "$p"\n',
          '    step worker "$p"\n    step drain "$p"\n    step idle "$p"\n', expect="the worker restarted before")
@mutation(DEPLOY, '    step worker "$p"\n    step restore "$p" "$was"\n',
          '    step restore "$p" "$was"\n    step worker "$p"\n',
          expect="resumed before the worker restarted")
@mutation(DEPLOY, '[ "$n" = 0 ] && { echo', 'true && { echo', expect="the worker restarted before")
@mutation(DEPLOY, 'step build ${workers[@]+"${workers[@]}"}\nif',
          'step rotate "$old"\nstep build ${workers[@]+"${workers[@]}"}\nif', expect="the deploy failed")
@mutation(DEPLOY, 'compose "$N" "${args[@]}" build', 'compose "$D" "${args[@]}" build',
          expect="built after the rotation")
@mutation(DEPLOY, 'cp -p "$f"', 'cp "$f"', expect="an env file was not copied as it was")
@mutation(DEPLOY, 'sed -i "s/^GIT_SHA=.*/GIT_SHA=$sha/"', "true", expect="the deploy failed")
@mutation(DEPLOY, 'case "$p" in dola | meta | qwen) echo', 'case "$p" in nothing) echo', expect="no Chrome version")
def test_a_deploy_builds_first_rotates_starts_the_api_then_drains_waits_restarts_and_resumes_each_worker(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b, "--workers", "dola", STUB_SUBMITTING="2 0")
    assert run.returncode == 0, "the deploy failed: %s %s" % (run.stdout, run.stderr)
    assert box.deployed() == box.b and box.trees() == ["relay-deploy", "relay-deploy-" + box.a]
    for f in ENV_FILES:                                     # the env files of the live tree, as they were
        new, old = (box.home / t / "deploy" / f for t in ("relay-deploy", "relay-deploy-" + box.a))
        same_time = f == ".env" or new.stat().st_mtime == old.stat().st_mtime    # .env gets the new GIT_SHA
        assert stat.S_IMODE(new.stat().st_mode) == 0o600 and same_time, \
            "an env file was not copied as it was: " + f
        assert ("TOKEN=%s-%s" % (SECRET, f)) in new.read_text()
    build, up_api = box.index(" build "), box.index("up -d --no-deps api")
    assert box.docker()[build].startswith("DOCKER [relay-deploy-new live=%s]" % box.a), \
        "built after the rotation: " + box.docker()[build]
    assert "--profile app --profile dola build migrate api worker-dola" in box.docker()[build]
    assert box.docker()[up_api].startswith("DOCKER [relay-deploy live=%s]" % box.b) and build < up_api
    assert {"relay-api:" + box.b, "relay-worker-dola:" + box.b} <= set(box.image_list())
    drains = [i for i, line in enumerate(box.docker()) if "provider drain dola" in line]
    polls = [i for i, line in enumerate(box.docker()) if "submitting" in line]
    restart, resume = box.index("up -d --no-deps worker-dola"), box.index("provider resume dola")
    assert len(drains) == 2 and up_api < drains[0] and len(polls) == 2 and drains[1] < polls[0] < polls[1] < restart, \
        "the worker restarted before its drain and the end of its submits: %s" % box.docker()
    assert -1 < restart < resume, "resumed before the worker restarted"
    assert "Google Chrome 154" in run.stdout, "no Chrome version: " + run.stdout
    assert box.index("run --rm migrate") == -1 and box.index("--profile meta") == -1
    curls = [line for line in box.calls() if line.startswith("CURL")]
    assert "127.0.0.1:8100/healthz" in curls[0] and "/ui/app/assets/index-AbC12_45.js" in curls[-1]
    assert "%s -> %s; migrate: none; restarted: api worker-dola" % (box.a, box.b) in run.stdout
    assert SECRET not in run.stdout + run.stderr + box.log.read_text(), "an env file was printed"


def test_without_workers_only_the_api_is_built_and_restarted_and_no_worker_is_drained(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b)
    assert run.returncode == 0, run.stderr
    assert "--profile app build migrate api" in box.docker()[box.index(" build ")]
    assert box.index("provider") == -1 and box.index("worker-") == -1 and box.index("exec -T postgres") == -1
    assert "restarted: api\n" in run.stdout


# ---- the log of each replaced container ---------------------------------------------------------------------------
def kept_logs(box: Box, service: str) -> list[Path]:
    d = box.home / "relay-logs" / service
    return sorted(d.glob("*")) if d.exists() else []


@mutation(DEPLOY, "  keep_logs app api\n", "", expect="the api log was not kept")
@mutation(DEPLOY, '  keep_logs "$p" "worker-$p"\n', "", expect="the worker-dola log was not kept")
def test_a_deploy_and_a_rollback_keep_the_log_of_each_container_before_replacing_it(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b, "--workers", "dola")
    assert run.returncode == 0, run.stderr
    for service, profile in (("api", "app"), ("worker-dola", "dola")):
        logs = box.index("--profile %s logs --no-color --timestamps %s" % (profile, service))
        assert -1 < logs < box.index("up -d --no-deps " + service), "the %s log was not kept" % service
        files = kept_logs(box, service)
        assert len(files) == 1 and files[0].name.endswith("Z.log.gz"), "the %s log was not kept" % service
        assert gzip.decompress(files[0].read_bytes()).decode() == "2026-10-24T01:00:00Z a line of %s\n" % service
    assert stat.S_IMODE((box.home / "relay-logs").stat().st_mode) == 0o700
    time.sleep(1.1)                                      # the next log is named by the next second
    assert box.rollback("--workers", "dola").returncode == 0
    assert len(kept_logs(box, "api")) == len(kept_logs(box, "worker-dola")) == 2


@mutation(DEPLOY, "-mtime +$LOGS_DAYS -delete", "-mtime +999 -delete", expect="a log older than 7 days was kept")
@mutation(DEPLOY, "[ ${#old[@]} -le $LOGS_FILES ] ||", "true ||", expect="more than 20 logs were kept")
def test_kept_logs_last_7_days_and_at_most_20_per_service(tmp_path):
    box = Box(tmp_path)
    d = box.home / "relay-logs" / "api"
    d.mkdir(parents=True)
    old = ["20260101T%02d0000Z.log.gz" % i for i in range(20)]
    for name in old:
        (d / name).write_bytes(b"x")
    stale = d / time.strftime("%Y%m%dT%H%M%SZ.log.gz", time.gmtime(time.time() - 3600))   # a recent name, an old file
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 8 * 86400,) * 2)
    assert box.deploy(box.b).returncode == 0
    kept = [p.name for p in kept_logs(box, "api")]
    assert stale.name not in kept, "a log older than 7 days was kept"
    assert len(kept) == 20 and kept[:19] == old[1:], "more than 20 logs were kept: %s" % kept


@mutation(DEPLOY, '    rm -f "$f"\n    echo "box: WARNING', '    exit 1\n    echo "box: WARNING',
          expect="a log that could not be kept stopped the deploy")
@mutation(DEPLOY, 'if [ -s "$f" ] && gzip -f "$f"; then', 'if gzip -f "$f"; then', expect="an empty log was kept")
def test_a_log_that_cannot_be_kept_is_a_warning_and_an_empty_one_leaves_no_file(tmp_path):
    box = Box(tmp_path / "failing")
    run = box.deploy(box.b, STUB_LOGS_FAIL="1")
    assert run.returncode == 0, "a log that could not be kept stopped the deploy: %s" % run.stderr
    assert "WARNING: the log of api could not be kept" in run.stderr and kept_logs(box, "api") == []
    box = Box(tmp_path / "empty")
    assert box.deploy(box.b, STUB_LOGS_EMPTY="1").returncode == 0
    assert kept_logs(box, "api") == [], "an empty log was kept"


@mutation(DEPLOY, 'SSH_OPTS=(-o BatchMode=yes -o "SendEnv=-*"', "SSH_OPTS=(-o BatchMode=yes",
          expect="an ssh command line lacks SendEnv")
@mutation(DEPLOY, " -o ServerAliveInterval=15 -o ServerAliveCountMax=3)", ")",
          expect="an ssh command line lacks SendEnv or the keepalive")
@mutation(DEPLOY, 'clean() { env -i HOME="$HOME"', 'clean() { env HOME="$HOME"', expect="a variable reached ssh")
def test_every_ssh_call_sends_no_variable_of_the_mac(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b, LC_ALL="C", FOO_FROM_MAC="1")
    assert run.returncode == 0, run.stderr
    ssh = [line for line in box.calls() if line.startswith("SSH: ")]
    prefix = "SSH: -o BatchMode=yes -o SendEnv=-* -o ServerAliveInterval=15 -o ServerAliveCountMax=3 phuongnam@"
    assert len(ssh) >= 5 and all(line.startswith(prefix) for line in ssh), \
        "an ssh command line lacks SendEnv or the keepalive: %s" % ssh
    assert not [line for line in box.calls() if line.startswith("ENV: ")], "a variable reached ssh: %s" % box.calls()


# ---- refusals before anything reaches the box ---------------------------------------------------------------------
@mutation(DEPLOY, '|| die "commit $sha is unknown', '|| echo "commit $sha is unknown',
          expect="an unknown commit was not refused")
@mutation(DEPLOY, 'git merge-base --is-ancestor "$full" origin/main \\', "true \\",
          expect="a commit not on origin/main was not")
@mutation(DEPLOY, '*) die "unknown provider', '*) echo "unknown provider', expect="an unknown provider was not")
@mutation(DEPLOY, '[[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || die', 'true || die', expect="a bad commit was not")
def test_a_commit_that_is_unknown_not_pushed_or_malformed_and_an_unknown_provider_are_refused_before_ssh(tmp_path):
    box = Box(tmp_path)
    cases = (((box.d,), "not on origin/main", "a commit not on origin/main"),
             (("abcdef1",), "unknown in this repository", "an unknown commit"),
             ((box.b, "--workers", "dola,nope"), "unknown provider", "an unknown provider"),
             (("HEAD;id",), "7 to 40 hex", "a bad commit"), ((), "usage", "no commit"),
             ((box.b, box.c), "one commit only", "two commits"),
             ((box.b, "--allow-schema-mismatch"), "unknown argument", "a rollback flag"))
    for args, why, label in cases:
        run = box.deploy(*args)
        assert run.returncode == 2 and why in run.stderr and box.calls() == [], \
            "%s was not refused: %r -> %d %r" % (label, args, run.returncode, run.stderr)
    assert unchanged(box)


# ---- refusals on the box, before anything changes -----------------------------------------------------------------
@mutation(DEPLOY, '[ ! -e "$D/live-window" ] || fail "a live window is open', 'true || fail "a live window is open',
          expect="a deploy ran in a live window")
def test_a_live_window_refuses_the_deploy_with_77(tmp_path):
    box = Box(tmp_path)
    (box.home / "relay-deploy" / "live-window").touch()
    run = box.deploy(box.b)
    assert run.returncode == 77 and "no deploy until" in run.stderr and box.docker() == [] \
        and box.trees() == ["relay-deploy"], \
        "a deploy ran in a live window: %d %r" % (run.returncode, run.stderr)


def hold(path: Path, kind: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a")
    fcntl.flock(fh, kind | fcntl.LOCK_NB)
    return fh


@mutation(DEPLOY, 'if ! flock -n "$l" true; then', "if false; then", expect="a deploy ran next to a busy folder")
@mutation(DEPLOY, '[ "$state" = locked ] || die', "true || die", expect="a deploy ran next to a gate")
def test_a_busy_folder_or_a_held_gate_lock_refuses_the_deploy_with_75_and_free_ones_do_not(tmp_path):
    box = Box(tmp_path)
    runs = box.home / "relay-runs"
    hold(runs / "busy" / "free.lock", fcntl.LOCK_EX).close()          # a folder whose run ended: free
    held = hold(runs / "busy" / "dev1.lock", fcntl.LOCK_EX)
    run = box.deploy(box.b)
    assert run.returncode == 75 and "dev1" in run.stderr and box.docker() == [] and unchanged(box), \
        "a deploy ran next to a busy folder: %d %r" % (run.returncode, run.stderr)
    held.close()
    gate = hold(runs / "gate.lock", fcntl.LOCK_SH)
    run = box.deploy(box.b)
    assert run.returncode == 75 and "gate.lock" in run.stderr and box.docker() == [] and unchanged(box), \
        "a deploy ran next to a gate: %d %r" % (run.returncode, run.stderr)
    gate.close()
    other = hold(runs / "gate.lock", fcntl.LOCK_EX)                  # another deploy or rollback holds it
    run = box.deploy(box.b)
    assert run.returncode == 75 and "gate.lock" in run.stderr and box.docker() == [] and unchanged(box), \
        "a deploy ran next to a gate: %d %r" % (run.returncode, run.stderr)
    other.close()
    assert box.deploy(box.b).returncode == 0


@mutation(DEPLOY, "echo locked; while read", "echo locked; flock -u 9; while read",
          expect="gate.lock was free during the")
@mutation(DEPLOY, "take_lock\nold=$(step preflight", "old=$(step preflight", expect="gate.lock was free during the")
@mutation(DEPLOY, 'if [ "$mode" = rollback ]; then\n  take_lock\n', 'if [ "$mode" = rollback ]; then\n',
          expect="gate.lock was free during the")
def test_a_deploy_and_a_rollback_hold_gate_lock_exclusive_from_the_first_check_to_the_end_and_release_it(tmp_path):
    box = Box(tmp_path)
    gate = box.home / "relay-runs" / "gate.lock"
    for name, run in (("deploy", lambda: box.deploy(box.b, "--workers", "dola")),
                      ("rollback", lambda: box.rollback("--workers", "dola"))):
        box.log.unlink(missing_ok=True)
        done = run()
        assert done.returncode == 0, "the %s failed, or gate.lock was free during the %s: %r" % (
            name, name, done.stderr)
        calls = box.docker()
        assert len(calls) > 5 and all(line.endswith("{gate=held}") for line in calls), \
            "gate.lock was free during the %s: %s" % (name, [c for c in calls if not c.endswith("{gate=held}")])
        hold(gate, fcntl.LOCK_EX).close()                               # released at the end: a run may start


@mutation(DEPLOY, '[ "$ans" = held ] || {', "true || {", expect="the deploy went on after it lost the lock")
@mutation(DEPLOY, '  check_lock "$1"\n  remote "$@" ||', '  remote "$@" ||',
          expect="the deploy went on after it lost the lock")
def test_a_deploy_that_loses_its_lock_stops_before_the_next_step_and_rotates_nothing(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.c, "--allow-migrate", "--workers", "dola", STUB_KILL_HOLDER="1")
    assert run.returncode == 1 and "lost the lock" in run.stderr and "before 'migrate'" in run.stderr \
        and box.index("run --rm migrate") == -1 and unchanged(box), \
        "the deploy went on after it lost the lock: %d %r %s" % (run.returncode, run.stderr, box.docker())


@mutation(DEPLOY, '  pkill -P "$hold_pid" 2> /dev/null || true\n', "", expect="the end of the run hung")
def test_the_end_of_a_run_is_bounded_when_the_lock_session_does_not_end(tmp_path):
    box = Box(tmp_path)
    start = time.monotonic()
    try:
        run = box.deploy(box.b, STUB_HOLDER_HANGS="1")
    except subprocess.TimeoutExpired:
        raise AssertionError("the end of the run hung on a lock session that does not end") from None
    took = time.monotonic() - start
    assert run.returncode == 0 and took < 60, "the end of the run hung: %d after %.0f s %r" % (
        run.returncode, took, run.stderr)


@mutation(DEPLOY, "  step preflight > /dev/null\n", "", expect="a rollback ran")
def test_a_rollback_makes_the_box_checks_of_a_deploy(tmp_path):
    box = deployed_b(tmp_path)
    marker = box.home / "relay-deploy" / "live-window"
    marker.touch()
    run = box.rollback()
    assert run.returncode == 77 and "live window" in run.stderr and box.deployed() == box.b and box.docker() == [], \
        "a rollback ran in a live window: %d %r" % (run.returncode, run.stderr)
    marker.unlink()
    held = hold(box.home / "relay-runs" / "busy" / "dev1.lock", fcntl.LOCK_EX)
    run = box.rollback()
    assert run.returncode == 75 and "dev1" in run.stderr and box.deployed() == box.b and box.docker() == [], \
        "a rollback ran next to a busy folder: %d %r" % (run.returncode, run.stderr)
    held.close()
    gate = hold(box.home / "relay-runs" / "gate.lock", fcntl.LOCK_SH)
    run = box.rollback()
    assert run.returncode == 75 and "gate.lock" in run.stderr and box.deployed() == box.b, \
        "a rollback ran next to a gate: %d %r" % (run.returncode, run.stderr)
    gate.close()
    assert box.rollback().returncode == 0 and box.deployed() == box.a


@mutation(DEPLOY, '[ -n "$old" ] || fail "no GIT_SHA', 'true || fail "no GIT_SHA', expect="an unknown deployed commit")
@mutation(DEPLOY, '[ "$old" != "$new" ] || die', "true || die", expect="a redeploy of the live commit")
@mutation(DEPLOY, '[ -f "$D/deploy/$f" ] || fail', "true || fail", expect="a missing env file")
@mutation(DEPLOY, '[ -f "$D/deploy/worker-$p.env" ] || fail', "true || fail", expect="a missing env file")
def test_an_unknown_deployed_commit_the_live_commit_itself_and_a_missing_env_file_are_refused(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.a)
    assert run.returncode == 2 and "already deployed" in run.stderr and box.docker() == [], \
        "a redeploy of the live commit was not refused"
    env = box.home / "relay-deploy" / "deploy" / ".env"
    env.write_text("TOKEN=x\n")
    run = box.deploy(box.b)
    assert run.returncode == 2 and "deployed commit is unknown" in run.stderr and box.docker() == [], \
        "an unknown deployed commit was not refused: %d %r" % (run.returncode, run.stderr)
    env.write_text("GIT_SHA=%s\n" % box.a)
    for missing, workers in (("migrate.env", ()), ("worker-meta.env", ("--workers", "dola,meta"))):
        (box.home / "relay-deploy" / "deploy" / missing).rename(box.dir / missing)
        run = box.deploy(box.b, *workers)
        assert run.returncode == 2 and missing in run.stderr and box.docker() == [] and unchanged(box), \
            "a missing env file was not refused: %s %d %r" % (missing, run.returncode, run.stderr)
        (box.dir / missing).rename(box.home / "relay-deploy" / "deploy" / missing)


# ---- migrations ---------------------------------------------------------------------------------------------------
@mutation(DEPLOY, '[ "$allow" = 1 ] || die "the migrations of $new', 'true || die "the migrations of $new',
          expect="a migration went ahead without --allow-migrate")
@mutation(DEPLOY, 'if diff -rq "$D/$MIG" "$N/$MIG"', "if true", expect="a migration went ahead without --allow-migrate")
@mutation(DEPLOY, 'compose "$N" --profile app run --rm migrate', 'compose "$D" --profile app run --rm migrate',
          expect="migrate did not run in the new tree before the rotation")
def test_a_commit_with_new_migrations_needs_allow_migrate_and_then_migrates_after_the_build_and_before_the_rotation(
        tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.c)
    assert run.returncode == 3 and "--allow-migrate" in run.stderr and box.index(" build ") == -1 \
        and box.index("migrate") == -1 and unchanged(box), \
        "a migration went ahead without --allow-migrate: %d %r" % (run.returncode, run.stderr)
    run = box.deploy(box.c, "--allow-migrate")
    assert run.returncode == 0, run.stderr
    build, migrate, up_api = (box.index(n) for n in (" build ", "run --rm migrate", "up -d --no-deps api"))
    assert build < migrate < up_api and box.docker()[migrate].startswith(
        "DOCKER [relay-deploy-new live=%s]" % box.a), \
        "migrate did not run in the new tree before the rotation: %s" % box.docker()
    assert "migrate: ran" in run.stdout and box.deployed() == box.c


# ---- failures stop the deploy -------------------------------------------------------------------------------------
@mutation(DEPLOY, '>&2; exit "$rc"; }', '>&2; }', expect="a failed build was rotated")
def test_a_failed_build_or_migration_stops_before_the_rotation(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b, STUB_BUILD_FAIL="1")
    assert run.returncode == 1 and "stopped at 'build'" in run.stderr and "production is unchanged" in run.stderr \
        and unchanged(box) and box.index("up -d") == -1, \
        "a failed build was rotated: %d %r %r" % (run.returncode, run.stderr, box.trees())
    run = box.deploy(box.c, "--allow-migrate", STUB_MIGRATE_FAIL="1")
    assert run.returncode == 1 and "stopped at 'migrate'" in run.stderr and unchanged(box) \
        and box.index("up -d") == -1, "a failed migration was rotated"


@mutation(DEPLOY, '[ ! -e "$HOME/relay-deploy-$old" ] || fail', "true || fail", expect="a kept tree was overwritten")
def test_an_existing_tree_of_the_deployed_commit_refuses_the_deploy_before_anything_moves(tmp_path):
    box = Box(tmp_path)
    box.place(box.a, "relay-deploy-" + box.a)
    run = box.deploy(box.b)
    assert run.returncode == 2 and "already exists" in run.stderr and box.deployed() == box.a \
        and box.docker() == [], \
        "a kept tree was overwritten: %d %r %r" % (run.returncode, run.stderr, box.trees())


@mutation(DEPLOY, '[ "$code" = 200 ] || fail "/healthz', 'true || fail "/healthz', expect="an unhealthy api passed")
@mutation(DEPLOY, 'curl -fsS -o /dev/null "http://127.0.0.1:8100$js" || fail', "true || fail",
          expect="an unserved bundle passed")
def test_an_unhealthy_api_or_an_unserved_bundle_fails_the_deploy_and_names_the_rollback(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b, "--workers", "dola", STUB_HEALTHZ="502")
    assert run.returncode == 1 and "502" in run.stderr and "scripts/rollback.sh" in run.stderr \
        and box.index("provider drain") == -1, "an unhealthy api passed: %d %r" % (run.returncode, run.stderr)
    assert box.deployed() == box.b                       # rotated: the new tree is live, the rollback brings A back
    box2 = Box(tmp_path / "bundle")
    run = box2.deploy(box2.b, STUB_BUNDLE_FAIL="22")
    assert run.returncode == 1 and "bundle" in run.stderr, "an unserved bundle passed: %r" % run.stderr


@mutation(DEPLOY, '[[ "$second" == *"(no change)"* ]] || fail', "true || fail", expect="an unstored drain passed")
@mutation(DEPLOY, '[ "$ok" != 0 ] || fail "worker-$p wrote', 'true || fail "worker-$p wrote',
          expect="a worker without a heartbeat was resumed")
def test_an_unstored_drain_or_a_worker_without_a_fresh_heartbeat_stops_and_leaves_the_provider_drained(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b, "--workers", "dola", STUB_DRAIN_NO_STORE="1")
    assert run.returncode == 1 and "(no change)" in run.stderr and box.index("up -d --no-deps worker-dola") == -1, \
        "an unstored drain passed: %d %r" % (run.returncode, run.stderr)
    box2 = Box(tmp_path / "hb")
    run = box2.deploy(box2.b, "--workers", "dola", STUB_HB="0")
    assert run.returncode == 1 and "heartbeat" in run.stderr and box2.index("provider resume") == -1 \
        and "relay provider resume dola" in run.stderr, \
        "a worker without a heartbeat was resumed: %d %r" % (run.returncode, run.stderr)


# ---- the trees and images kept ------------------------------------------------------------------------------------
@mutation(DEPLOY, 'if grep -qxF "$img:$s" <<< "$used"; then', "if false; then", expect="an image in use was removed")
@mutation(DEPLOY, '[ "$s" != "$cur" ] || break', "true || break", expect="the live commit's images were removed")
@mutation(DEPLOY, '&& [ "$s" != "$old" ] || continue', "|| continue", expect="the previous tree was removed")
def test_only_the_newest_previous_tree_is_kept_and_older_images_go_unless_in_use_or_live(tmp_path):
    box = Box(tmp_path)
    box.place(box.a, "relay-deploy-1111111")           # an older tree; its worker image still runs
    box.place(box.b, "relay-deploy-" + box.b)           # a tree of the commit about to go live (an earlier rollback)
    (box.home / "relay-deploy-notes").mkdir()           # not a commit: never touched
    box.images("relay-api:1111111", "relay-migrate:1111111", "relay-worker-dola:1111111", "relay-api:" + box.b)
    run = box.deploy(box.b, STUB_IN_USE="relay-worker-dola:1111111 relay-api:" + box.a)
    assert run.returncode == 0, run.stderr
    assert box.trees() == ["relay-deploy", "relay-deploy-" + box.a, "relay-deploy-notes"], \
        "the previous tree was removed or an older one kept: %s" % box.trees()
    images = set(box.image_list())
    assert "relay-worker-dola:1111111" in images, "an image in use was removed"
    assert "relay-api:" + box.b in images, "the live commit's images were removed"
    assert not {"relay-api:1111111", "relay-migrate:1111111"} & images
    assert {"relay-api:" + box.a, "relay-migrate:" + box.a} <= images and box.index("volume") == -1


# ---- rollback -----------------------------------------------------------------------------------------------------
def deployed_b(tmp_path: Path) -> Box:
    box = Box(tmp_path)
    assert box.deploy(box.b, "--workers", "dola").returncode == 0
    box.log.unlink()
    return box


def test_a_rollback_swaps_the_trees_back_restarts_the_api_and_with_workers_drains_restarts_and_resumes(tmp_path):
    box = deployed_b(tmp_path)
    run = box.rollback("--workers", "dola", STUB_SUBMITTING="1 0")
    assert run.returncode == 0, run.stderr
    assert box.deployed() == box.a and box.trees() == ["relay-deploy", "relay-deploy-" + box.b]
    up_api = box.index("up -d --no-deps api")
    assert box.docker()[up_api].startswith("DOCKER [relay-deploy live=%s]" % box.a)
    order = [box.index(n) for n in ("provider drain dola", "up -d --no-deps worker-dola", "provider resume dola")]
    assert up_api < order[0] < order[1] < order[2]
    assert box.index(" build ") == box.index("migrate") == box.index("image rm") == box.index("volume") == -1
    assert "%s -> %s" % (box.b, box.a) in run.stdout
    back = box.rollback(box.b)                           # and forward again, naming the tree
    assert back.returncode == 0 and box.deployed() == box.b, back.stderr


@mutation(DEPLOY, '[ ${#found[@]} = 1 ] || fail', "true || fail",
          expect="a rollback went ahead with no or several trees")
@mutation(DEPLOY, '[ -d "$HOME/relay-deploy-$t" ] && [ "$(sha_of "$HOME/relay-deploy-$t")" = "$t" ] \\', "true \\",
          expect="a rollback to a missing tree went ahead")
@mutation(DEPLOY, 'docker image inspect "$img:$t" > /dev/null 2>&1 || fail', "true || fail",
          expect="a rollback without its images went ahead")
def test_a_rollback_refuses_a_missing_or_ambiguous_tree_and_missing_images_and_changes_nothing(tmp_path):
    box = Box(tmp_path)
    run = box.rollback()
    assert run.returncode == 2 and "0 previous trees" in run.stderr and box.deployed() == box.a, \
        "a rollback went ahead with no or several trees: %r" % run.stderr
    run = box.rollback("1234567")
    assert run.returncode == 2 and "nothing to roll back to" in run.stderr and box.deployed() == box.a, \
        "a rollback to a missing tree went ahead: %r" % run.stderr
    box.place(box.b, "relay-deploy-" + box.b)
    box.place(box.a, "relay-deploy-1111111")
    run = box.rollback()
    assert run.returncode == 2 and "name the one" in run.stderr and box.deployed() == box.a, \
        "a rollback went ahead with no or several trees: %r" % run.stderr
    run = box.rollback(box.b)                            # the tree is there, its api image is not
    assert run.returncode == 2 and "relay-api:%s is missing" % box.b in run.stderr and box.deployed() == box.a, \
        "a rollback without its images went ahead: %r" % run.stderr
    box.images("relay-api:" + box.b)
    run = box.rollback(box.b, "--workers", "dola")
    assert run.returncode == 2 and "relay-worker-dola:%s is missing" % box.b in run.stderr \
        and box.deployed() == box.a, "a rollback without its images went ahead: %r" % run.stderr
    box.images("relay-worker-dola:" + box.b)
    box.place(box.a, "relay-deploy-" + box.a)           # the live commit's own name is taken: nowhere to put it
    run = box.rollback(box.b)
    assert run.returncode == 2 and "already exists" in run.stderr and box.deployed() == box.a, \
        "a rollback over a kept tree went ahead: %r" % run.stderr
    assert box.index("up -d") == -1 and box.rollback("--allow-migrate").returncode == 2


@mutation(DEPLOY, 'if grep -qx "schema=differs" <<< "$out" && [ "$allow" = 0 ]; then', "if false; then",
          expect="a rollback across a migration went ahead")
def test_a_rollback_across_a_migration_needs_allow_schema_mismatch_and_never_migrates(tmp_path):
    box = Box(tmp_path)
    assert box.deploy(box.c, "--allow-migrate").returncode == 0
    box.log.unlink()
    run = box.rollback()
    assert run.returncode == 3 and "--allow-schema-mismatch" in run.stderr and box.deployed() == box.c \
        and box.index("up -d") == -1, \
        "a rollback across a migration went ahead: %d %r" % (run.returncode, run.stderr)
    run = box.rollback("--allow-schema-mismatch")
    assert run.returncode == 0 and box.deployed() == box.a, run.stderr
    assert box.index("migrate") == -1 and box.index("volume") == -1


# ---- the claims state of each provider survives the restart --------------------------------------------------------
def claims(box: Box, provider: str) -> str | None:
    """The stored <provider>.claims_enabled in the fake database (None: no row)."""
    path = box.state / ("claims-" + provider)
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


@mutation(DEPLOY, '  if [ "$2" = on ]; then resume "$1"; fi', '  resume "$1"',
          expect="a provider that was off came back on")
@mutation(DEPLOY, '[[ "$claims " != *" $p=on "* ]] || was=on', "true", expect="a provider that claimed was not resumed")
@mutation(DEPLOY, '    v=${raw:-${a#*=}}', '    v=${raw:-true}', expect="a provider that was off came back on")
@mutation(DEPLOY, '  case "$1" in dola) echo true ;;', '  case "$1" in dola) echo false ;;',
          expect="a provider that claimed was not resumed")
def test_after_a_deploy_and_a_rollback_each_provider_claims_exactly_when_it_claimed_before(tmp_path):
    box = Box(tmp_path)
    box.images("relay-worker-meta:" + box.a, "relay-worker-flow:" + box.a)
    (box.state / "claims-meta").write_text("true\n")      # turned on by the owner although its default is off
    for name, run in (("deploy", lambda: box.deploy(box.b, "--workers", "dola,meta,flow")),
                      ("rollback", lambda: box.rollback("--workers", "dola,meta,flow"))):
        box.log.unlink(missing_ok=True)
        done = run()
        assert done.returncode == 0, "the %s failed: %r" % (name, done.stderr)
        # dola: no row, default on; meta: stored on; flow: no row, default off (then a stored off on the rollback)
        assert claims(box, "flow") == "false" and box.index("provider resume flow") == -1, \
            "a provider that was off came back on after the %s: %s" % (name, box.docker())
        assert claims(box, "dola") == claims(box, "meta") == "true", \
            "a provider that claimed was not resumed after the %s: %s" % (name, box.docker())
        assert "flow left drained (it was off before)" in done.stdout and "dola claims again" in done.stdout \
            and "meta claims again" in done.stdout, done.stdout
        reads = [box.index("key = '%s.claims_enabled'" % p) for p in ("dola", "meta", "flow")]
        assert -1 < max(reads) < box.index("provider drain dola"), "a drain came before every state was read"


@mutation(DEPLOY, '      || fail "$p.claims_enabled reads', '      || v=false; true "$p.claims_enabled reads',
          expect="an unreadable claims state went on")
def test_an_unreadable_claims_state_stops_before_any_provider_is_drained(tmp_path):
    for i, read in enumerate(("fail", "odd")):
        box = Box(tmp_path / str(i))
        run = box.deploy(box.b, "--workers", "dola,flow", STUB_CLAIMS_READ=read)
        assert run.returncode != 0 and "stopped at 'claims'" in run.stderr and "no worker was drained" in run.stderr \
            and box.index("provider") == -1 and box.index("up -d --no-deps worker-") == -1, \
            "an unreadable claims state went on (%s): %d %r %s" % (read, run.returncode, run.stderr, box.docker())
        assert not (box.home / SNAPSHOT).exists(), "a snapshot was kept of an unreadable state"
    assert "reads [1], not true or false" in run.stderr


def test_the_claims_defaults_of_deploy_sh_are_those_of_the_provider_specs():
    for package in ("relay_dola", "relay_flow", "relay_gemini", "relay_meta", "relay_vibes", "relay_qwen"):
        module = importlib.import_module(package + ".spec")
        name = module.SPEC.name
        default = module.SPEC.settings()[name + ".claims_enabled"].default
        script = "source <(sed -n '/^default_claims() {/,/^}/p' %s); default_claims %s" % (REPO / DEPLOY, name)
        got = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout.strip()
        assert got == str(default).lower(), "deploy.sh says %s for %s.claims_enabled, the spec %s" % (
            got, name, default)


def snapshot(box: Box) -> list[str] | None:
    path = box.home / SNAPSHOT
    return sorted(path.read_text(encoding="utf-8").split()) if path.exists() else None


def failed_after_the_drain(tmp_path: Path) -> Box:
    """A deploy of B with dola (on: no row, default) and flow (off) whose new dola worker writes no heartbeat: dola is
    left drained (a stored false) and the snapshot keeps dola=true (flow, off, needs no line)."""
    box = Box(tmp_path)
    box.images("relay-worker-flow:" + box.a)
    run = box.deploy(box.b, "--workers", "dola,flow", STUB_HB="0")
    assert run.returncode == 1 and "heartbeat" in run.stderr and claims(box, "dola") == "false", run.stderr
    assert "relay-claims-snapshot keeps that dola claimed" in run.stderr, run.stderr
    assert snapshot(box) == ["dola=true"], "the snapshot is not what claimed before the drain: %s" % snapshot(box)
    box.log.unlink()
    return box


@mutation(DEPLOY, 'snap=$(sed -n "s/^$p=//p" "$SNAP") || fail', 'snap=$(true) || fail',
          expect="a provider that was on stayed drained")
@mutation(DEPLOY, 'echo "$1=true"; } > "$SNAP.tmp"', '} > "$SNAP.tmp"', expect="the snapshot is not what claimed")
@mutation(DEPLOY, 'else rm -f "$1" "$SNAP"; fi', 'else mv -f "$1" "$SNAP"; fi',
          expect="the snapshot outlived the restore")
def test_a_deploy_again_after_one_that_stopped_after_its_drain_restores_what_each_provider_was(tmp_path):
    box = failed_after_the_drain(tmp_path)
    run = box.deploy(box.c, "--allow-migrate", "--workers", "dola,flow")
    assert run.returncode == 0, run.stderr
    assert claims(box, "dola") == "true" and "WARNING: dola.claims_enabled is false, but ~/relay-claims-snapshot " \
        "holds [dola=true]" in run.stderr, "a provider that was on stayed drained: %s %s" % (run.stderr, box.docker())
    assert claims(box, "flow") == "false" and box.index("provider resume flow") == -1, \
        "a provider that was off came back on: %s" % box.docker()
    assert not (box.home / SNAPSHOT).exists(), "the snapshot outlived the restore"


@mutation(DEPLOY, '  snap_without "$1" > "$SNAP.tmp"', '  : > "$SNAP.tmp"',
          expect="a provider left the snapshot before its restore")
def test_a_rollback_after_a_deploy_that_stopped_after_its_drain_restores_each_provider_as_it_comes(tmp_path):
    box = failed_after_the_drain(tmp_path)
    run = box.rollback("--workers", "flow")             # flow only: dola stays in the snapshot
    assert run.returncode == 0 and box.deployed() == box.a, run.stderr
    assert claims(box, "flow") == "false" and box.index("provider resume") == -1, \
        "a provider that was off came back on: %s" % box.docker()
    assert snapshot(box) == ["dola=true"], "a provider left the snapshot before its restore: %s" % snapshot(box)
    box.log.unlink()
    run = box.rollback(box.b, "--workers", "dola,flow")
    assert run.returncode == 0 and box.deployed() == box.b, run.stderr
    assert claims(box, "dola") == "true" and claims(box, "flow") == "false" \
        and box.index("provider resume flow") == -1, \
        "a provider left the snapshot before its restore, or flow came back on: %s" % box.docker()
    assert not (box.home / SNAPSHOT).exists()


@mutation(DEPLOY, '    if [ "$snap" = true ] && [ "$v" = false ]; then',
          '    if [ -n "$snap" ] && v=$snap && false; then',
          expect="a leftover flow=false overrode the owner")
def test_a_leftover_snapshot_line_never_keeps_or_turns_a_provider_off(tmp_path):
    box = Box(tmp_path)
    (box.home / SNAPSHOT).write_text("flow=false\n")    # left by an older run; the owner has turned flow on since
    (box.state / "claims-flow").write_text("true\n")
    run = box.deploy(box.b, "--workers", "flow")
    assert run.returncode == 0, run.stderr
    restart, resume = box.index("up -d --no-deps worker-flow"), box.index("provider resume flow")
    assert claims(box, "flow") == "true" and -1 < restart < resume, \
        "a leftover flow=false overrode the owner: %s" % box.docker()
    assert snapshot(box) is None and "WARNING" not in run.stderr


@mutation(DEPLOY, 'if [ "$was" = on ]; then step mark', 'if true; then step mark',
          expect="a provider that was off came back on")
def test_a_provider_that_was_off_and_stopped_after_its_drain_stays_off_on_the_next_deploy(tmp_path):
    box = Box(tmp_path)
    run = box.deploy(box.b, "--workers", "flow", STUB_HB="0")
    assert run.returncode == 1 and "heartbeat" in run.stderr and claims(box, "flow") == "false", run.stderr
    run = box.deploy(box.c, "--allow-migrate", "--workers", "flow")
    assert run.returncode == 0, run.stderr
    assert claims(box, "flow") == "false" and box.index("provider resume flow") == -1 and snapshot(box) is None, \
        "a provider that was off came back on: %s %s" % (snapshot(box), box.docker())
