"""scripts/box.sh: argument parsing, the refusals, the exclude list, the fingerprint, the gate lock and what is (not)
sent to the box. ssh and rsync are replaced by stubs that record their calls, so nothing here needs the box; the
parts that do (a real sync, a detached run, the lock) are shown by the commands in the box-runner report."""

import fcntl
import os
import re
import shutil
import signal
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from relay_core.testing.mutation import mutation

from guards import REPO

BOX = "scripts/box.sh"

SSH_STUB = """\
#!/usr/bin/env bash
# records the call and every variable it was started with beyond HOME/PATH; answers the way the box would
. "@CONTROL@"
echo "ARGS: $*" >> "@LOG@"
env | grep -vE '^(HOME|PATH|PWD|SHLVL|_|OLDPWD|SSH_AUTH_SOCK)=' | sed 's/^/ENV: /' >> "@LOG@" || true
case "$*" in
  *"python3 -"*)
    code=$(cat)
    if [ -n "${STUB_FP:-}" ]; then echo "$STUB_FP"; else python3 -c "$code" "$STUB_TREE"; fi ;;
  *"test ! -e relay-deploy/live-window"*) exit "${STUB_LIVE:-0}" ;;   # 1 = the marker file exists
  *"compose.project=relay)"*) exit "${STUB_PROD:-11}" ;;     # 10 = production is up, 11 = none, else an error
  *"gate.lock true"*)
    if [ -n "${STUB_REAL_LOCKS:-}" ]; then all="$*"; cd "@DIR@" && bash -c "${all#*@192.168.18.227 }"; exit $?; fi
    exit "${STUB_GATE_BUSY:-0}" ;;
  *"busy/"*".lock true"*) exit "${STUB_FOLDER_BUSY:-0}" ;;       # is the folder free? (0 = free)
  *"cat relay-runs/busy/"*".info"*) echo "${STUB_INFO:-}" ;;
  *"bash -ls"*) cat > "@DIR@/ran.sh" ;;
  *"bash -s"*)                                                   # a script on stdin: kept, numbered
    n=$(ls "@DIR@"/remote-*.sh 2>/dev/null | wc -l | tr -d ' ')
    cat > "@DIR@/remote-$n.sh"; cp "@DIR@/remote-$n.sh" "@DIR@/remote.sh"
    # the check "is a run of this name running?" ends in `alive "$n.pid"`
    if grep -q 'alive "[$]n.pid"$' "@DIR@/remote-$n.sh"; then exit "${STUB_RUNNING:-1}"; fi ;;
  *"cat > "*) cat > "@DIR@/detached.sh" ;;
esac
exit 0
"""
RSYNC_STUB = """\
#!/usr/bin/env bash
. "@CONTROL@"
echo "RSYNC: $*" >> "@LOG@"
env | grep -vE '^(HOME|PATH|PWD|SHLVL|_|OLDPWD|SSH_AUTH_SOCK)=' | sed 's/^/ENV: /' >> "@LOG@" || true
exit 0
"""


def write_tree(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class Box:
    """A tree, the stubs, and a way to run scripts/box.sh from the tree. Variables named STUB_* steer the stubs (a
    control file: the stubs run under a clean environment); any other keyword is a variable of the caller's shell."""

    def __init__(self, tmp_path: Path, files: dict[str, str] | None = None, dirname: str = "proj"):
        self.dir = tmp_path
        self.tree = write_tree(tmp_path / "work" / dirname, {"a.txt": "one\n", **(files or {})})
        self.log = tmp_path / "stub.log"
        self.control = tmp_path / "control.env"
        bins = tmp_path / "bin"
        bins.mkdir()
        for name, text in (("ssh", SSH_STUB), ("rsync", RSYNC_STUB)):
            path = bins / name
            path.write_text(text.replace("@CONTROL@", str(self.control)).replace("@LOG@", str(self.log))
                            .replace("@DIR@", str(tmp_path)), encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.bins = bins

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        stub = {"STUB_TREE": str(self.tree), **{k: v for k, v in env.items() if k.startswith("STUB_")}}
        self.control.write_text("".join("%s=%r\n" % item for item in stub.items()), encoding="utf-8")
        full = {"PATH": "%s:%s" % (self.bins, os.environ["PATH"]), "HOME": str(self.dir),
                **{k: v for k, v in env.items() if not k.startswith("STUB_")}}
        return subprocess.run(["bash", str(REPO / BOX), *args], cwd=self.tree, env=full, capture_output=True,
                              text=True)

    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def script(self, needle: str) -> str:
        """The newest script sent to the box on stdin (`bash -s`) that contains `needle`."""
        found = [f.read_text() for f in sorted(self.dir.glob("remote-*.sh"), key=lambda f: int(f.stem.split("-")[1]))
                 if needle in f.read_text()]
        assert found, "no script with %r was sent: %r" % (needle, self.calls())
        return found[-1]


def test_help_describes_the_script():
    run = subprocess.run(["bash", str(REPO / BOX), "--help"], capture_output=True, text=True)
    assert run.returncode == 0 and "scripts/box.sh -- <command...>" in run.stdout and "--detach NAME" in run.stdout


@mutation(BOX, 'case "$mode" in run | detach) [ $# -gt 0 ] ||', 'case "$mode" in run | detach) true ||',
          expect="a run without a command was accepted")
def test_a_run_without_a_command_or_with_an_unknown_argument_is_refused(tmp_path):
    box = Box(tmp_path)
    for args in (("--",), (), ("--detach", "x", "--"), ("--nope",)):
        run = box.run(*args)
        assert run.returncode == 2 and box.calls() == "", \
            "a run without a command was accepted: %r -> %d %r" % (args, run.returncode, run.stderr)


@mutation(BOX, "^[A-Za-z0-9][A-Za-z0-9._-]*$", "^.*$", expect="a bad run name was accepted")
def test_a_run_name_is_letters_digits_dot_dash_and_underscore(tmp_path):
    box = Box(tmp_path)
    for bad in ("a b", "../x", "-x", "x;y", "$(id)"):
        for mode in ("--detach", "--wait", "--log", "--status"):
            run = box.run(mode, bad, *(("--", "true") if mode == "--detach" else ()))
            assert run.returncode == 2 and "a run name is" in run.stderr and box.calls() == "", \
                "a bad run name was accepted: %s %r" % (mode, bad)


@mutation(BOX, 'case "$word" in *MUTATE_CMD*)', 'case "$word" in *MUTATE_CMDX*)', expect="MUTATE_CMD was passed")
def test_mutate_cmd_is_never_passed_to_a_run(tmp_path):
    box = Box(tmp_path)
    for args in (("--", "MUTATE_CMD=true", "./scripts/check.sh", "--full"), ("--", "env", "MUTATE_CMD=x", "ls")):
        run = box.run(*args)
        assert run.returncode == 2 and "MUTATE_CMD is never passed" in run.stderr and box.calls() == "", \
            "MUTATE_CMD was passed: %r" % (args,)


@mutation(BOX, 'case "$word" in *WEB_CHECK_CMD*)', 'case "$word" in *WEB_CHECK_CMDX*)',
          expect="WEB_CHECK_CMD was passed")
def test_web_check_cmd_is_never_passed_to_a_run(tmp_path):
    box = Box(tmp_path)
    run = box.run("--", "WEB_CHECK_CMD=true", "./scripts/check.sh")
    assert run.returncode == 2 and "WEB_CHECK_CMD is never passed" in run.stderr and box.calls() == "", \
        "WEB_CHECK_CMD was passed"


@mutation(BOX, '[ -z "$real_env" ] ||', "true ||", expect="a tree with a real env file was synced")
def test_a_tree_with_a_real_env_file_is_refused_before_anything_is_sent(tmp_path):
    for where in ("deploy/prod.env", ".env", "deploy/.env", "sub/dir/secret.env"):
        box = Box(tmp_path / where.replace("/", "_"), {where: "SECRET=1\n"})
        run = box.run("--", "true")
        assert run.returncode == 2 and "real env file" in run.stderr and where in run.stderr and box.calls() == "", \
            "a tree with a real env file was synced: %s -> %r" % (where, run.stderr)
    clean = Box(tmp_path / "clean", {"deploy/.env.example": "X=\n", "deploy/compose.yml": "x: 1\n"})
    ok = clean.run("--", "true")
    assert ok.returncode == 0 and "mkdir -p relay-runs/proj" in clean.calls(), ok.stderr


@mutation(BOX, "rsync -a --delete -e", "rsync -a -e", expect="rsync did not delete what the Mac deleted")
@mutation(BOX, "EXCLUDES=(.git .venv __pycache__", "EXCLUDES=(.git __pycache__", expect="the exclude list lost")
@mutation(BOX, "/.worktrees /plans /.claude /CLAUDE.md /.codegraph)", "/.worktrees)", expect="the exclude list lost")
def test_the_sync_deletes_what_is_gone_and_leaves_out_git_venv_caches_and_env_files(tmp_path):
    box = Box(tmp_path)
    run = box.run("--", "true")
    assert run.returncode == 0, run.stderr
    rsync = next(line for line in box.calls().splitlines() if line.startswith("RSYNC: "))
    assert " --delete " in rsync, "rsync did not delete what the Mac deleted: " + rsync
    for pattern in (".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".mutate.lock",
                    "deploy/.env", "deploy/*.env", "._*", ".DS_Store", "node_modules",
                    "/.worktrees", "/plans", "/.claude", "/CLAUDE.md", "/.codegraph"):
        assert "--exclude=%s " % pattern in rsync, "the exclude list lost %s: %s" % (pattern, rsync)
    assert rsync.endswith("%s/ phuongnam@192.168.18.227:relay-runs/proj/" % box.tree), rsync
    assert "relay-secrets" not in box.calls()


@mutation(BOX, 'env -i HOME="$HOME"', 'env HOME="$HOME"', expect="a variable of the caller reached ssh")
def test_nothing_of_the_callers_environment_reaches_ssh_or_rsync(tmp_path):
    box = Box(tmp_path)
    run = box.run("--", "true", LC_CTYPE="zz_ZZ.TEST", LANG="zz_ZZ.TEST", LC_ALL="zz_ZZ.TEST", FOO_FROM_MAC="1",
                  SSH_AUTH_SOCK="/tmp/agent.sock")
    assert run.returncode == 0, run.stderr
    assert "ENV:" not in box.calls(), "a variable of the caller reached ssh: " + box.calls()
    assert "ARGS:" in box.calls() and "RSYNC:" in box.calls()


@mutation(BOX, 'SSH_OPTS=(-o BatchMode=yes -o "SendEnv=-*")', "SSH_OPTS=(-o BatchMode=yes)",
          expect="an ssh command line lacks SendEnv=-*")
@mutation(BOX, 'ssh "${SSH_OPTS[@]}" "$BOX"', 'ssh -o BatchMode=yes "$BOX"',
          expect="an ssh command line lacks SendEnv=-*")
@mutation(BOX, '-e "ssh ${SSH_OPTS[*]}"', '-e "ssh -o BatchMode=yes"', expect="the ssh of rsync lacks SendEnv=-*")
def test_every_ssh_command_line_sends_no_environment_variable(tmp_path):
    box = Box(tmp_path)
    assert box.run("--", "true").returncode == 0
    assert box.run("--detach", "j1", "--", "true").returncode == 0
    assert box.run("--status").returncode == 0 and box.run("--clear", "dev1").returncode == 0
    ssh_lines = [line for line in box.calls().splitlines() if line.startswith("ARGS: ")]
    assert len(ssh_lines) >= 8
    for line in ssh_lines:
        assert line.startswith("ARGS: -o BatchMode=yes -o SendEnv=-* phuongnam@"), \
            "an ssh command line lacks SendEnv=-*: " + line
    for line in [x for x in box.calls().splitlines() if x.startswith("RSYNC: ")]:
        assert "-e ssh -o BatchMode=yes -o SendEnv=-* " in line, "the ssh of rsync lacks SendEnv=-*: " + line


@mutation(BOX, '[ "$mac" = "$there" ] ||', "true ||", expect="a run went ahead on a tree that differs")
def test_a_fingerprint_that_differs_after_the_sync_refuses_the_run(tmp_path):
    box = Box(tmp_path)
    run = box.run("--", "echo", "SHOULD-NOT-RUN", STUB_FP="deadbeef 3")
    assert run.returncode == 3 and "fingerprint differs" in run.stderr, \
        "a run went ahead on a tree that differs: %d %r" % (run.returncode, run.stderr)
    assert not (box.dir / "ran.sh").exists()
    same = box.run("--", "echo", "fine")
    assert same.returncode == 0 and "fingerprint=" in same.stderr and (box.dir / "ran.sh").exists()


def fingerprint(box: Box) -> str:
    run = box.run("--fingerprint")
    assert run.returncode == 0, run.stderr
    return run.stdout.split()[0]


@mutation(BOX, 'name in (".git", ".DS_Store", ".mutate.lock")', 'name in (".DS_Store", ".mutate.lock")',
          expect="(a .git file included)")
@mutation(BOX, 'fnmatch.fnmatch(rel, "deploy/*.env")', 'fnmatch.fnmatch(rel, "deploy/*.envX")',
          expect="an excluded file changed the fingerprint")
@mutation(BOX, 'name.startswith("._")', 'name.startswith("._X")', expect="an excluded file changed the fingerprint")
@mutation(BOX, 'SKIP_TOP = {".worktrees", "plans", ".claude", "CLAUDE.md", ".codegraph"}',
          'SKIP_TOP = {".worktrees", "plans", ".claude"}',
          expect="an excluded file changed the fingerprint")
@mutation(BOX, '"CLAUDE.md", ".codegraph"}', '"CLAUDE.md"}', expect="an excluded file changed the fingerprint")
@mutation(BOX, " and not (d == root and x in SKIP_TOP)", "", expect="an excluded file changed the fingerprint")
@mutation(BOX, "or rel in SKIP_TOP)", "or name in SKIP_TOP)", expect="a nested plans or CLAUDE.md was left out")
@mutation(BOX, "not (d == root and x in SKIP_TOP)", "not (x in SKIP_TOP)",
          expect="a nested plans or CLAUDE.md was left out")
def test_the_fingerprint_ignores_what_the_sync_leaves_out_and_sees_everything_else(tmp_path):
    box = Box(tmp_path)
    base = fingerprint(box)
    for rel in (".venv/lib/x.py", "__pycache__/m.pyc", "sub/.pytest_cache/n", ".ruff_cache/r", ".mutate.lock",
                "deploy/.env", "deploy/local.env", "._a.txt", ".DS_Store", "sub/.git",
                "node_modules/x/y.js", "apps/web/node_modules/p.js",
                ".worktrees/t/a.py", "plans/p/plan.md", ".claude/settings.local.json", "CLAUDE.md",
                ".codegraph/codegraph.db"):
        path = box.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("noise\n", encoding="utf-8")
    assert fingerprint(box) == base, "an excluded file changed the fingerprint (a .git file included)"
    for rel in ("apps/plans/p.md", "apps/CLAUDE.md"):
        before = fingerprint(box)
        (box.tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (box.tree / rel).write_text("seen\n", encoding="utf-8")
        assert fingerprint(box) != before, "a nested plans or CLAUDE.md was left out: " + rel


@mutation(BOX, "os.stat(path).st_mode & 0o111 != 0", "0", expect="the executable bit")
def test_the_fingerprint_changes_with_a_content_a_new_file_a_deletion_and_the_executable_bit(tmp_path):
    box = Box(tmp_path, {"run.sh": "echo\n", "b.txt": "b\n"})
    seen = {fingerprint(box)}
    (box.tree / "a.txt").write_text("two\n", encoding="utf-8")
    seen.add(fingerprint(box))
    (box.tree / "new.txt").write_text("n\n", encoding="utf-8")
    seen.add(fingerprint(box))
    (box.tree / "b.txt").unlink()
    seen.add(fingerprint(box))
    (box.tree / "run.sh").chmod(0o755)
    seen.add(fingerprint(box))
    assert len(seen) == 5, "the executable bit or an edit did not change the fingerprint: %d distinct" % len(seen)


GATE = ("--", "./scripts/check.sh", "--full")


@mutation(BOX, 'if [ "$seen" = 1 ] && [ "$others" = 0 ] && [ "$full" = 1 ]; then is_gate=1; fi',
          'if [ "$seen" = 1 ] && [ "$others" = 0 ] && [ "$full" = 1 ]; then is_gate=0; fi',
          expect="a whole-tree gate did not take the lock")
@mutation(BOX, "MUTATE_ONLY=*) others=1 ;;", "MUTATE_ONLY=*) ;;", expect="a narrowed run took the gate lock")
@mutation(BOX, '*) [ "$seen" = 1 ] && others=1 ;;', "*) ;;", expect="a narrowed run took the gate lock")
def test_only_a_whole_tree_gate_takes_the_lock_exclusive_and_every_other_run_takes_it_shared(tmp_path):
    gate = Box(tmp_path / "gate")
    assert gate.run(*GATE, "--local").returncode == 0
    assert "flock -x -n relay-runs/gate.lock true" in gate.calls() \
        and "flock -x -n 9" in (gate.dir / "ran.sh").read_text(), \
        "a whole-tree gate did not take the lock"
    for narrowed in (("--", "MUTATE_ONLY=test_x", "./scripts/check.sh", "--full"),
                     ("--", "./scripts/check.sh", "--full", "packages/x/tests/test_y.py"),
                     ("--", "./scripts/check.sh", "tests/test_y.py"), ("--", "uv", "run", "ruff", "check", ".")):
        box = Box(tmp_path / ("n%d" % abs(hash(narrowed))))
        assert box.run(*narrowed).returncode == 0
        script = (box.dir / "ran.sh").read_text()
        assert "flock -s -n relay-runs/gate.lock true" in box.calls() and "flock -s -n 9" in script \
            and "flock -x" not in script and "flock -x" not in box.calls(), \
            "a narrowed run took the gate lock: %r" % (narrowed,)


WRAPPED_GATE = ("--", "bash", "-c",
                "set -o pipefail; ./scripts/check.sh --full 2>&1 | while IFS= read -r l; do echo \"$l\"; done")


@mutation(BOX, "arg=${arg//;/ ; }; arg=${arg//|/ | }; arg=${arg//&/ & }; arg=${arg//>/ > }; arg=${arg//</ < }",
          "arg=$arg", expect="a wrapped whole-tree gate did not take the lock")
@mutation(BOX, "arg=${arg//2>&1/ | }; ", "", expect="a wrapped whole-tree gate did not take the lock")
def test_a_gate_wrapped_in_a_shell_takes_the_lock_like_a_plain_one_and_a_wrapped_narrowed_run_does_not(tmp_path):
    for n, wrapped in enumerate((WRAPPED_GATE, ("--", "env", "MUTATE_JOBS=1", "./scripts/check.sh", "--full"),
                                 ("--", "sh", "-c", "export A=1\n./scripts/check.sh --full; echo done"))):
        box = Box(tmp_path / ("w%d" % n))
        assert box.run(*wrapped).returncode == 0
        assert "flock -x -n relay-runs/gate.lock true" in box.calls() \
            and "flock -x -n 9" in (box.dir / "ran.sh").read_text(), \
            "a wrapped whole-tree gate did not take the lock: %r" % (wrapped,)
    for n, narrowed in enumerate((("--", "bash", "-c", "MUTATE_ONLY=test_x ./scripts/check.sh --full"),
                                  ("--", "bash", "-c", "./scripts/check.sh --full tests/test_y.py | cat"),
                                  ("--", "bash", "-c", "./scripts/check.sh --soak | cat"))):
        box = Box(tmp_path / ("x%d" % n))
        assert box.run(*narrowed).returncode == 0
        assert "flock -x" not in box.calls() and "flock -x" not in (box.dir / "ran.sh").read_text(), \
            "a wrapped narrowed run took the gate lock: %r" % (narrowed,)


@mutation(BOX, "';' | '|' | '&' | '>' | '<') [ \"$seen\" = 1 ] && stopped=1 ;;", "';' | '|' | '&' | '>' | '<') ;;",
          expect="the words after the gate's pipe counted as test paths")
def test_the_words_after_the_pipe_of_a_wrapped_gate_are_not_test_paths(tmp_path):
    box = Box(tmp_path / "pipe")
    assert box.run("--", "bash", "-c", "./scripts/check.sh --full | tee out.log").returncode == 0
    assert "flock -x -n 9" in (box.dir / "ran.sh").read_text(), "the words after the gate's pipe counted as test paths"


@mutation(BOX, 'wait for it" 75', 'wait for it" 0', expect="a gate was not refused")
@mutation(BOX, 'until it ends" 75', 'until it ends" 0', expect="a run was not refused while a gate runs")
def test_a_refused_lock_check_stops_a_gate_and_any_other_run_with_exit_75_before_anything_is_synced(tmp_path):
    box = Box(tmp_path)
    for mode in (GATE, ("--detach", "g1") + GATE):
        run = box.run(*mode, STUB_GATE_BUSY="1")
        assert run.returncode == 75 and "needs the box to itself" in run.stderr, \
            "a gate was not refused: %r -> %d" % (mode, run.returncode)
    for mode in (("--", "true"), ("--detach", "n1", "--", "true")):
        run = box.run(*mode, STUB_GATE_BUSY="1")
        assert run.returncode == 75 and "a whole-tree gate is running" in run.stderr, \
            "a run was not refused while a gate runs: %r -> %d" % (mode, run.returncode)
    assert not (box.dir / "ran.sh").exists() and not (box.dir / "detached.sh").exists() and "RSYNC" not in box.calls()


# ---- the lock itself, for real: the script box.sh would run on the box is run here, against lock files under HOME ----

FAKE_CHECK = '#!/usr/bin/env bash\ntouch "$HOME/started-$(basename "$PWD")"\nexec sleep "${HOLD:-60}"\n'
HOLD_FOR_A_MINUTE = ("--", "bash", "-c", 'touch "$HOME/started-$(basename "$PWD")"; exec sleep 60')


class Runs:
    """Runs of scripts/box.sh that really hold or test ~/relay-runs/gate.lock (flock needs Linux: on the Mac these
    tests fail rather than skip, the box is where they run, D95). Every started run is stopped at the end."""

    def __init__(self, tmp_path: Path):
        self.box = Box(tmp_path, {"scripts/check.sh": FAKE_CHECK})
        (self.box.tree / "scripts/check.sh").chmod(0o755)
        self.procs: list[subprocess.Popen] = []
        assert shutil.which("flock"), "flock is not installed: these tests run on the box"

    def script(self, tree: str, *args: str) -> tuple[subprocess.CompletedProcess, Path | None]:
        """box.sh with real lock checks: its result and the script it would send to the box (None if refused)."""
        (self.box.dir / "ran.sh").unlink(missing_ok=True)
        run = self.box.run("--tree", tree, *args, STUB_REAL_LOCKS="1")
        if run.returncode != 0:
            return run, None
        path = self.box.dir / ("ran-%s.sh" % tree)
        shutil.copy(self.box.dir / "ran.sh", path)
        shutil.copytree(self.box.tree, self.box.dir / "relay-runs" / tree, ignore=shutil.ignore_patterns(".git"),
                        dirs_exist_ok=True)
        return run, path

    def start(self, path: Path) -> subprocess.Popen:
        env = {"PATH": os.environ["PATH"], "HOME": str(self.box.dir)}
        proc = subprocess.Popen(["bash", str(path)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        self.procs.append(proc)
        return proc

    def hold(self, tree: str, *args: str) -> subprocess.Popen:
        """Start a run that keeps running, and wait until its command has started (so it holds the lock)."""
        run, path = self.script(tree, *args)
        assert path is not None, "the holder was refused: " + run.stderr
        proc = self.start(path)
        started = self.box.dir / ("started-%s" % tree)
        for _ in range(150):
            if started.exists():
                return proc
            assert proc.poll() is None, "the holder ended: " + proc.stderr.read()
            time.sleep(0.1)
        raise AssertionError("the holder never started its command")

    def finish(self, proc: subprocess.Popen) -> tuple[int, str]:
        out, err = proc.communicate(timeout=30)
        return proc.returncode, err

    def stop(self, proc: subprocess.Popen, sig: int = signal.SIGKILL) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        proc.wait(timeout=30)
        # the holder's children share its lock descriptor: the flock goes when the last of them is gone, not the leader
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)

    def lock_state_when(self, want: tuple[bool, bool]) -> tuple[bool, bool]:
        """lock_state(), polled for up to 10 s until it is `want`: a killed holder's flock goes a moment later."""
        deadline = time.monotonic() + 10
        state = self.lock_state()
        while state != want and time.monotonic() < deadline:
            time.sleep(0.05)
            state = self.lock_state()
        return state

    def lock_state(self) -> tuple[bool, bool]:
        """(an exclusive lock can be taken now, a shared one can): of gate.lock, as another process would see it."""
        results = []
        for kind in (fcntl.LOCK_EX, fcntl.LOCK_SH):
            with open(self.box.dir / "relay-runs" / "gate.lock", "a") as fh:
                try:
                    fcntl.flock(fh, kind | fcntl.LOCK_NB)
                    results.append(True)
                except BlockingIOError:
                    results.append(False)
        return results[0], results[1]


@pytest.fixture
def runs(tmp_path):
    r = Runs(tmp_path)
    yield r
    for proc in r.procs:
        r.stop(proc)


@mutation(BOX, '[ "$is_gate" = 0 ] || lock_flag=-x', '[ "$is_gate" = 0 ] || lock_flag=-s',
          expect="a whole-tree gate was not refused by the early check")
@mutation(BOX, "flock -x -n 9 ||", "flock -s -n 9 ||", expect="a whole-tree gate was not refused by its own lock")
@mutation(BOX, "echo 'exec 9>\"$HOME/'\"$RUNS\"'/gate.lock\"'", "echo ':'", expect="the holder ended")
def test_a_whole_tree_gate_is_refused_while_a_narrowed_run_holds_the_lock_and_runs_when_it_ended(runs):
    run, prepared = runs.script("g", "--", "HOLD=0", "./scripts/check.sh", "--full")   # made while the lock is free
    assert run.returncode == 0 and prepared is not None, run.stderr
    narrowed = runs.hold("n1", *HOLD_FOR_A_MINUTE)
    assert runs.lock_state() == (False, True), "a run took no lock"
    early, none = runs.script("g", "--", "./scripts/check.sh", "--full")
    assert none is None and early.returncode == 75 and "needs the box to itself" in early.stderr, \
        "a whole-tree gate was not refused by the early check: %d %r" % (early.returncode, early.stderr)
    rc, err = runs.finish(runs.start(prepared))
    assert rc == 75 and "needs the box to itself" in err, \
        "a whole-tree gate was not refused by its own lock: %d %r" % (rc, err)
    runs.stop(narrowed, signal.SIGTERM)
    # negative control: the same gate, with nobody holding the lock, runs to its end
    run, path = runs.script("g", "--", "HOLD=0", "./scripts/check.sh", "--full")
    assert run.returncode == 0 and path is not None, run.stderr
    assert runs.finish(runs.start(path))[0] == 0, "the control gate did not run"


@mutation(BOX, "flock -s -n 9 ||", "true ||", expect="a narrowed run was not refused by its own lock")
def test_a_narrowed_run_is_refused_while_a_whole_tree_gate_holds_the_lock_and_runs_when_it_ended(runs):
    run, prepared = runs.script("n1", "--", "true")                                    # made while the lock is free
    assert run.returncode == 0 and prepared is not None, run.stderr
    gate = runs.hold("g", "--", "./scripts/check.sh", "--full")
    assert runs.lock_state() == (False, False), "a gate holds the lock exclusive"
    for args in (("--", "true"), ("--detach", "d1", "--", "true")):
        early, none = runs.script("n2", *args)
        assert none is None and early.returncode == 75 and "a whole-tree gate is running" in early.stderr, \
            "a narrowed run was not refused by the early check: %r %d %r" % (args, early.returncode, early.stderr)
    rc, err = runs.finish(runs.start(prepared))
    assert rc == 75 and "a whole-tree gate is running" in err, \
        "a narrowed run was not refused by its own lock: %d %r" % (rc, err)
    runs.stop(gate, signal.SIGTERM)
    run, path = runs.script("n3", "--", "true")                                        # negative control
    assert run.returncode == 0 and path is not None, run.stderr
    assert runs.finish(runs.start(path))[0] == 0, "the control run did not run"


@mutation(BOX, "lock_flag=-s\n", "lock_flag=-x\n", expect="two narrowed runs were refused")
@mutation(BOX, "flock -s -n 9 ||", "flock -x -n 9 ||", expect="two narrowed runs were refused")
def test_two_narrowed_runs_in_different_folders_hold_the_lock_together(runs):
    first = runs.hold("n1", *HOLD_FOR_A_MINUTE)
    run, path = runs.script("n2", *HOLD_FOR_A_MINUTE)
    assert path is not None, "two narrowed runs were refused: %d %r" % (run.returncode, run.stderr)
    second = runs.start(path)
    time.sleep(1.5)                                  # a refused run ends at once with 75; a running one is still there
    assert first.poll() is None and second.poll() is None, "two narrowed runs were refused: " + str(second.poll())
    assert runs.lock_state() == (False, True), "two narrowed runs were refused: the lock is not shared"
    # negative control: a third run in the first folder is refused by the folder lock, not by gate.lock
    third, _ = runs.script("n1", "--", "true")
    assert third.returncode in (0, 76), third.stderr   # the stub answers the folder check; gate.lock never says 75
    assert third.returncode != 75, "gate.lock refused a shared run"


def test_the_lock_dies_with_the_process_that_holds_it(runs):
    gate = runs.hold("g", "--", "./scripts/check.sh", "--full")
    assert runs.lock_state() == (False, False)
    runs.stop(gate)                                   # SIGKILL: no trap, no clean-up code runs
    assert runs.lock_state_when((True, True)) == (True, True), "a killed gate still holds the lock"
    narrowed = runs.hold("n1", *HOLD_FOR_A_MINUTE)
    assert runs.lock_state() == (False, True)
    runs.stop(narrowed)
    assert runs.lock_state_when((True, True)) == (True, True), "a killed narrowed run still holds the lock"
    run, path = runs.script("g2", "--", "HOLD=0", "./scripts/check.sh", "--full")
    assert run.returncode == 0 and path is not None and runs.finish(runs.start(path))[0] == 0, \
        "a gate was refused after the holders were killed: " + run.stderr


@mutation(BOX, "echo 'unset MUTATE_CMD WEB_CHECK_CMD'", "echo ':'", expect="the run script does not unset MUTATE_CMD")
def test_the_script_run_on_the_box_enters_the_tree_clears_mutate_cmd_and_runs_the_quoted_command(tmp_path):
    box = Box(tmp_path)
    assert box.run("--", "sh", "-c", "echo 'a b'; exit 3", "x y", "MARK=$HOME").returncode == 0
    ran = (box.dir / "ran.sh").read_text()
    assert 'cd "$HOME/relay-runs/proj"' in ran and "unset MUTATE_CMD" in ran, \
        "the run script does not unset MUTATE_CMD: " + ran
    assert "sh -c echo\\ \\'a\\ b\\'\\;\\ exit\\ 3 x\\ y MARK=\\$HOME" in ran, ran
    assert ran.rstrip().endswith("exit $?") and "RELAY_ON_BOX=1" in ran


@mutation(BOX, 'echo "taskset -c $GATE_CPUS nice -n 10 env $quoted"', 'echo "$quoted"',
          expect="the run is not under the CPU budget")
@mutation(BOX, "export RELAY_GATE_CAPPED=1 RELAY_GATE_CPUSET=", "export RELAY_GATE_CAPPED=0 RELAY_X=",
          expect="the capped run does not carry the cap")
def test_while_the_production_stack_runs_every_run_is_under_the_cpu_budget_and_the_gate_containers_get_theirs(tmp_path):
    box = Box(tmp_path)
    assert box.run("--", "echo", "hi", STUB_PROD="10").returncode == 0
    ran = (box.dir / "ran.sh").read_text()
    assert "taskset -c 0-11 nice -n 10 env echo hi" in ran, "the run is not under the CPU budget: " + ran
    assert "RELAY_GATE_CAPPED=1 RELAY_GATE_CPUSET=0-11 CHECK_CPUSET=0-11" in ran, \
        "the capped run does not carry the cap: " + ran
    other = Box(tmp_path / "other")
    done = other.run("--", "MUTATE_ONLY=x", "true", STUB_PROD="10", RELAY_GATE_CPUS="2,4-5")
    assert done.returncode == 0, done.stderr
    ran = (other.dir / "ran.sh").read_text()
    assert "taskset -c 2,4-5 nice -n 10 env MUTATE_ONLY=x true" in ran and "CHECK_CPUSET=2,4-5" in ran, ran
    assert "capped=1" in done.stderr, done.stderr


@mutation(BOX, "        11) ;;\n", "        11) capped=1 ;;\n", expect="a run before the deploy was capped")
def test_before_the_deploy_a_run_is_uncapped_and_the_override_decides_either_way(tmp_path):
    box = Box(tmp_path)
    assert box.run("--", "echo", "hi").returncode == 0               # no production container: STUB_PROD=11
    ran = (box.dir / "ran.sh").read_text()
    assert "taskset" not in ran and "RELAY_GATE_CAPPED" not in ran and "CHECK_CPUSET" not in ran, \
        "a run before the deploy was capped: " + ran
    assert "capped=0" in box.run("--sync-only").stderr
    assert "echo hi" in [ln.strip() for ln in ran.splitlines()], ran       # the command runs as it was given
    on = Box(tmp_path / "on")
    assert on.run("--", "true", RELAY_GATE_CAP="on").returncode == 0
    assert "taskset -c 0-11" in (on.dir / "ran.sh").read_text()
    off = Box(tmp_path / "off")
    assert off.run("--", "true", STUB_PROD="10", RELAY_GATE_CAP="off").returncode == 0
    assert "taskset" not in (off.dir / "ran.sh").read_text()
    assert Box(tmp_path / "bad").run("--", "true", RELAY_GATE_CAP="maybe").returncode == 2


@mutation(BOX, "        *) capped=1; echo", "        *) echo", expect="a run was uncapped after a failed check")
@mutation(BOX, "label=com.docker.compose.project=relay)", "label=com.docker.compose.project=relay-deploy)",
          expect="the production project was not asked for exactly")
def test_the_cap_asks_for_the_production_project_exactly_and_fails_closed_when_it_cannot_tell(tmp_path):
    box = Box(tmp_path)
    assert box.run("--", "true").returncode == 0
    assert "compose.project=relay) || exit 2" in box.calls(), "the production project was not asked for exactly"
    for status in ("1", "2", "255", "0"):
        other = Box(tmp_path / ("s" + status))
        done = other.run("--", "true", STUB_PROD=status)
        ran = (other.dir / "ran.sh").read_text()
        assert done.returncode == 0 and "taskset -c 0-11" in ran and "could not tell" in done.stderr, \
            "a run was uncapped after a failed check: " + status
    off = Box(tmp_path / "off")
    assert off.run("--", "true", STUB_PROD="2", RELAY_GATE_CAP="off").returncode == 0
    assert "taskset" not in (off.dir / "ran.sh").read_text()


@mutation(BOX, "[[ \"$GATE_CPUS\" =~ ^[0-9]+([-,][0-9]+)*$ ]] ||", "true ||", expect="a bad CPU list was accepted")
def test_a_bad_cpu_budget_is_refused_before_anything_is_sent(tmp_path):
    box = Box(tmp_path)
    for bad in ("0-11; id", "", "a", "0-"):
        run = box.run("--", "true", RELAY_GATE_CPUS=bad or "x")
        assert run.returncode == 2 and box.calls() == "", "a bad CPU list was accepted: %r" % bad


@mutation(BOX, 'die "a live window is open on the box', 'echo "a live window is open on the box',
          expect="a run started in a live window")
def test_no_run_starts_while_the_live_window_marker_exists_and_reading_is_still_allowed(tmp_path):
    box = Box(tmp_path)
    for args in (("--", "true"), ("--detach", "x", "--", "true")):
        run = box.run(*args, STUB_LIVE="1")
        assert run.returncode == 77 and "live window" in run.stderr and not (box.dir / "ran.sh").exists() \
            and "RSYNC" not in box.calls(), "a run started in a live window: %r" % (args,)
    for args in (("--status",), ("--log", "x"), ("--sync-only",)):
        assert box.run(*args, STUB_LIVE="1").returncode == 0, args
    assert box.run("--", "true").returncode == 0
    ran = (box.dir / "ran.sh").read_text()
    assert '[ ! -e "$HOME/relay-deploy/live-window" ]' in ran and "exit 77" in ran


@mutation(BOX, 'rm -f "$n.exit" "$n.log"\nnohup setsid', 'rm -f "$n.exit" "$n.log" &&\nnohup setsid',
          expect="the start command chains nohup")
@mutation(BOX, 'echo "$p $(starttime "$p")" > "$n.pid"', 'echo "$p" > "$n.pid"',
          expect="the pid file has no start time")
def test_a_detached_run_is_started_by_a_command_that_returns_at_once_and_writes_pid_start_time_and_exit_file(tmp_path):
    box = Box(tmp_path)
    assert box.run("--detach", "demo-1", "--", "echo", "hi").returncode == 0
    script = (box.dir / "detached.sh").read_text()
    assert "relay-runs/logs/demo-1.exit.tmp" in script and 'mv "$HOME/relay-runs/logs/demo-1.exit.tmp"' in script
    start = box.script("nohup setsid")
    assert 'nohup setsid bash -l "$n.sh" > "$n.log" 2>&1 < /dev/null &' in start and "bash -s -- demo-1" in box.calls()
    assert not re.search(r"&&\s*nohup", start), \
        "the start command chains nohup to what comes before it, so ssh waits for the whole run"
    assert 'echo "$p $(starttime "$p")" > "$n.pid"' in start, "the pid file has no start time"


def test_log_status_and_wait_read_only_the_logs_and_need_no_tree(tmp_path):
    box = Box(tmp_path)
    for mode in ("--log", "--status"):
        run = subprocess.run(["bash", str(REPO / BOX), mode, "demo-1"], cwd=tmp_path, capture_output=True, text=True,
                             env={"PATH": "%s:%s" % (box.bins, os.environ["PATH"]), "HOME": str(tmp_path)})
        assert run.returncode == 0, run.stderr
    assert "relay-runs/logs/demo-1.log" in box.calls() and "rsync" not in box.calls().lower()
    assert "bash -s -- demo-1" in box.calls()


def test_the_documented_exit_codes_and_the_header_agree_with_the_script():
    text = (REPO / BOX).read_text(encoding="utf-8")
    for code in ("2 usage", "3 fingerprint", "75 gate lock", "77 a live window", "98 a detached run"):
        assert code in text.split("set -euo pipefail")[0], code
    assert textwrap.dedent("""\
        set -euo pipefail""") in text and " 3 )" not in text


@mutation(BOX, "die \"a run named '$name' is still running on the box\"", "true",
          expect="a second run of a name that is still running was started")
def test_a_name_that_is_still_running_is_not_reused(tmp_path):
    box = Box(tmp_path)
    run = box.run("--detach", "demo-1", "--", "true", STUB_RUNNING="0")
    assert run.returncode == 2 and "still running" in run.stderr and not (box.dir / "detached.sh").exists(), \
        "a second run of a name that is still running was started"


# ---- one folder per run owner -------------------------------------------------------------------------------------
TREE_RULE = "^[A-Za-z0-9][A-Za-z0-9_-]*$"


@mutation(BOX, TREE_RULE, "^.*$", expect="a bad folder name was accepted")
@mutation(BOX, 'case "$tree" in logs | busy) die', 'case "$tree" in nothing) die',
          expect="a bad folder name was accepted: 'logs'")
def test_tree_picks_the_folder_and_its_name_is_checked(tmp_path):
    box = Box(tmp_path)
    assert box.run("--tree", "dev_1-b", "--", "true").returncode == 0
    rsync = next(line for line in box.calls().splitlines() if line.startswith("RSYNC: "))
    assert rsync.endswith(":relay-runs/dev_1-b/") and "mkdir -p relay-runs/dev_1-b" in box.calls()
    assert 'cd "$HOME/relay-runs/dev_1-b"' in (box.dir / "ran.sh").read_text()
    for bad in ("a b", "../x", ".x", "x.y", "-x", "x;y", "logs", "busy"):
        fresh = Box(tmp_path / ("t" + str(abs(hash(bad)))))
        run = fresh.run("--tree", bad, "--", "true")
        assert run.returncode == 2 and fresh.calls() == "", \
            "a bad folder name was accepted: %r -> %d %r" % (bad, run.returncode, run.stderr)
    assert Box(tmp_path / "none").run("--tree").returncode == 2


@mutation(BOX, 'if ! ssh_box "mkdir -p $RUNS/$tree $RUNS/logs $BUSY && flock -n $BUSY/$tree.lock true"; then',
          "if false; then", expect="a sync went into a held folder")
def test_a_sync_into_a_held_folder_is_refused_and_names_the_run(tmp_path):
    box = Box(tmp_path)
    for args in (("--", "echo", "x"), ("--detach", "r1", "--", "echo", "x"), ("--sync-only",)):
        run = box.run(*args, STUB_FOLDER_BUSY="1", STUB_INFO="run=gate-9 pgid=77 since=2026-10-09T01:02:03Z")
        assert run.returncode == 76 and "gate-9" in run.stderr and "--tree NAME" in run.stderr and "--clear proj" \
            in run.stderr, "a sync went into a held folder: %r -> %d %r" % (args, run.returncode, run.stderr)
    assert "RSYNC:" not in box.calls() and not (box.dir / "ran.sh").exists()
    free = box.run("--", "echo", "x")
    assert free.returncode == 0 and "RSYNC:" in box.calls()


@mutation(BOX, '--rsync-path="flock -n $BUSY/$tree.lock rsync"', '--rsync-path="rsync"',
          expect="the sync did not run under the folder's lock")
@mutation(BOX, "flock -n 8 ||", "true ||", expect="the run script does not take the folder")
@mutation(BOX, "[ \"$now\" = \"'\"$mac\"'\" ] ||", "true ||", expect="the run script does not re-check the fingerprint")
def test_the_sync_runs_under_the_folders_lock_and_the_run_script_takes_the_folder_and_re_checks(tmp_path):
    box = Box(tmp_path)
    fp = fingerprint(box)
    assert box.run("--", "echo", "hi").returncode == 0
    rsync = next(line for line in box.calls().splitlines() if line.startswith("RSYNC: "))
    assert "--rsync-path=flock -n relay-runs/busy/proj.lock rsync" in rsync, \
        "the sync did not run under the folder's lock: " + rsync
    ran = (box.dir / "ran.sh").read_text()
    assert 'exec 8>"$HOME/relay-runs/busy/proj.lock"' in ran and "flock -n 8 ||" in ran and "exit 76" in ran, \
        "the run script does not take the folder: " + ran
    assert 'echo "run=foreground-' in ran and "pgid=$(ps -o pgid= -p $$" in ran and "busy/proj.info" in ran
    assert "python3 - " in ran and "FPEOF" in ran and 'fingerprint")' not in ran
    assert '[ "$now" = "%s" ] ||' % fp.split()[0] in ran or fp.split()[0] in ran, \
        "the run script does not re-check the fingerprint: " + ran
    assert box.run("--detach", "job-7", "--", "echo", "hi").returncode == 0
    assert 'echo "run=job-7 pgid=' in (box.dir / "detached.sh").read_text()


@mutation(BOX, 'case "$tree_arg" in logs | busy) die', 'case "$tree_arg" in nothing) die',
          expect="--clear accepted a reserved folder name")
@mutation(BOX, '[[ "$tree_arg" =~ $FOLDER_RULE ]] || die "a folder name is',
          '[[ "$tree_arg" =~ . ]] || die "a folder name is', expect="--clear accepted a bad folder name")
def test_status_lists_the_folders_and_clear_takes_a_folder_name(tmp_path):
    box = Box(tmp_path)
    assert box.run("--status").returncode == 0
    listing = box.script("for d in */")
    assert 'flock -n "busy/$d.lock" true' in listing and 'case "$d" in logs | busy) continue ;; esac' in listing
    assert "rsync" not in box.calls().lower()
    assert box.run("--status", "demo-1").returncode == 0 and "bash -s -- demo-1" in box.calls()
    assert box.run("--clear", "dev1").returncode == 0 and "bash -s -- dev1" in box.calls()
    for bad in ("a b", "x;y", ".x", ""):
        assert box.run("--clear", bad).returncode == 2, "--clear accepted a bad folder name: %r" % bad
    for reserved in ("logs", "busy"):
        assert box.run("--clear", reserved).returncode == 2, "--clear accepted a reserved folder name: %r" % reserved


# ---- the scripts that run on the box, run here against a fake /proc ------------------------------------------------
def remote_script(which: str) -> str:
    out = subprocess.run(["bash", str(REPO / BOX), "--print-remote", which], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def stat_line(pid: int, start: int, pgid: int) -> str:
    """/proc/PID/stat: the command name may hold spaces and a ')', field 22 is the start time."""
    return "%d (a b) c) S 1 %d %d 0 -1 %s %d\n" % (pid, pgid, pgid, " ".join(["0"] * 13), start)


class FakeBox:
    """A fake /proc and fake flock, ps and sleep for the scripts of the box; `kill` is a function logged to a file."""

    def __init__(self, tmp_path: Path):
        self.dir, self.proc, self.home = tmp_path, tmp_path / "proc", tmp_path / "home"
        self.proc.mkdir(parents=True)
        (self.home / "relay-runs" / "logs").mkdir(parents=True)
        (self.home / "relay-runs" / "busy").mkdir(parents=True)
        self.lock = self.home / "relay-runs" / "busy" / "dev1.lock"
        self.kills = tmp_path / "kills.log"
        self.free = tmp_path / "free"
        bins = tmp_path / "fakebin"
        bins.mkdir()
        scripts = {
            "flock": '#!/usr/bin/env bash\n[ -e "%s" ]\n' % self.free,
            "sleep": "#!/usr/bin/env bash\nexit 0\n",
            "ps": textwrap.dedent("""\
                #!/usr/bin/env bash
                pid=${@: -1}
                [ -e "%(d)s/reuse-$pid" ] && { rm "%(d)s/reuse-$pid"; sed -i.bak 's/ 5000$/ 9999/' "%(p)s/$pid/stat"; }
                [ -e "%(d)s/release-$pid" ] && { rm "%(d)s/release-$pid"; rm -f "%(p)s/$pid"/fd/*; }
                if [ -f "%(p)s/$pid/pgid" ]; then cat "%(p)s/$pid/pgid"; else echo 4242; fi
                """) % {"d": tmp_path, "p": self.proc},
        }
        for name, text in scripts.items():
            f = bins / name
            f.write_text(text, encoding="utf-8")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.bins = bins

    def process(self, pid: int, start: int, pgid: int, holds_lock: bool = True) -> None:
        d = self.proc / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "stat").write_text(stat_line(pid, start, pgid), encoding="utf-8")
        (d / "pgid").write_text("%d\n" % pgid, encoding="utf-8")
        (d / "fd" / "3").symlink_to(self.lock if holds_lock else self.home / "other-file")

    def run(self, which: str, *args: str) -> subprocess.CompletedProcess:
        prelude = 'kill() { echo "KILL $*" >> "%s"; : > "%s"; }\n' % (self.kills, self.free)
        script = prelude + "set -- %s\n" % " ".join(args) + remote_script(which)
        env = {"PATH": "%s:%s" % (self.bins, os.environ["PATH"]), "HOME": str(self.home), "BOX_PROC": str(self.proc)}
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)

    def killed(self) -> str:
        return self.kills.read_text() if self.kills.exists() else ""


@mutation(BOX, '[ "$(starttime "$p")" != "$st" ] || ! holds "$p"', '! holds "$p"',
          expect="--clear signalled a pid whose start time had changed")
@mutation(BOX, '[ "$(starttime "$p")" != "$st" ] || ! holds "$p"', '[ "$(starttime "$p")" != "$st" ]',
          expect="--clear signalled a pid that no longer held the lock")
@mutation(BOX, '[ "$g" -gt 1 ] && [ "$g" != "$mine" ]', '[ "$g" -gt 1 ]', expect="--clear stopped its own group")
@mutation(BOX, 'readlink "$fd" 2>/dev/null)" = "$lock" ]', 'readlink "$fd" 2>/dev/null)" != "$lock" ]',
          expect="--clear stopped a process that does not hold the lock")
def test_clear_stops_the_process_group_of_whoever_holds_the_lock_and_only_a_pid_that_is_still_that_process(tmp_path):
    # a holder (pid 111, group 777), a process with another file open, and a process in our own group (4242)
    box = FakeBox(tmp_path)
    box.process(111, 5000, 777)
    box.process(222, 6000, 888, holds_lock=False)
    out = box.run("clear", "dev1")
    assert out.returncode == 0 and "folder dev1 is free" in out.stdout and "pgid" not in out.stderr, out.stderr
    assert box.killed().strip() == "KILL -TERM -- -777", \
        "--clear stopped a process that does not hold the lock: " + box.killed()
    # the same holder is in our own group: never stopped
    own = FakeBox(tmp_path / "own")
    own.process(111, 5000, 4242)
    out = own.run("clear", "dev1")
    assert own.killed() == "", "--clear stopped its own group: " + own.killed()
    # the pid was reused between finding it and signalling it: a new start time
    reused = FakeBox(tmp_path / "reused")
    reused.process(111, 5000, 777)
    (tmp_path / "reused" / "reuse-111").write_text("x")
    out = reused.run("clear", "dev1")
    assert "skipping pid 111" in out.stdout and "TERM" not in reused.killed(), \
        "--clear signalled a pid whose start time had changed: " + reused.killed()
    # it stopped holding the lock between the two
    left = FakeBox(tmp_path / "left")
    left.process(111, 5000, 777)
    (tmp_path / "left" / "release-111").write_text("x")
    out = left.run("clear", "dev1")
    assert "skipping pid 111" in out.stdout and left.killed() == "", \
        "--clear signalled a pid that no longer held the lock: " + left.killed()


@mutation(BOX, '&& [ "$(starttime "$p")" = "$t" ]', "&& true", expect="a reused pid counted as running")
def test_a_detached_run_counts_as_running_only_while_the_pid_has_the_stored_start_time(tmp_path):
    box = FakeBox(tmp_path)
    box.process(111, 5000, 111)
    logs = box.home / "relay-runs" / "logs"
    (logs / "x.log").write_text("hi\n")
    cases = (("111 5000\n", "running", 0), ("111 4999\n", "ended without an exit status", 1),
             ("111\n", "ended without an exit status", 1), ("999 5000\n", "ended without an exit status", 1),
             ("", "ended without an exit status", 1))
    for text, status, running_rc in cases:
        (logs / "x.pid").write_text(text)
        got = box.run("status", "x")
        assert got.stdout.strip() == status, "a reused pid counted as running: %r -> %r" % (text, got.stdout)
        assert box.run("wait", "x").stdout.strip() == ("running" if status == "running" else "dead"), text
        assert box.run("running", "x").returncode == running_rc, \
            "a reused pid counted as running (name check): %r" % text
    (logs / "x.exit").write_text("7\n")
    assert box.run("status", "x").stdout.strip() == "exit 7" and box.run("wait", "x").stdout.strip() == "7"
    (logs / "x.exit").unlink()
    (logs / "x.pid").unlink()
    assert box.run("running", "x").returncode == 1


@mutation(BOX, '[[ "$tree" =~ $FOLDER_RULE ]] || die "the folder name', 'true || die "the folder name',
          expect="a checkout directory name reached the remote shell")
def test_the_directory_name_of_the_tree_is_a_folder_name_too_and_is_checked_before_anything_is_sent(tmp_path):
    for bad in ("pro;j", "my proj", "a$(id)", "x`y`", "-x", "a'b"):
        box = Box(tmp_path / ("c%d" % abs(hash(bad))), dirname=bad)
        run = box.run("--", "true")
        assert run.returncode == 2 and box.calls() == "" and "folder name" in run.stderr, \
            "a checkout directory name reached the remote shell: %r -> %d %r" % (bad, run.returncode, run.stderr)
    ok = Box(tmp_path / "ok", dirname="relay-admin_ui2")
    assert ok.run("--", "true").returncode == 0 and "mkdir -p relay-runs/relay-admin_ui2" in ok.calls()
    named = Box(tmp_path / "named", dirname="pro;j")
    assert named.run("--tree", "dev1", "--", "true").returncode == 0, "--tree replaces a bad directory name"
    assert named.run("--fingerprint").returncode == 0
