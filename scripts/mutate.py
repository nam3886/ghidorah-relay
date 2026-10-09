#!/usr/bin/env python3
"""Prove the tests are real guards: break the code on purpose and demand that the marked test turns red.

Every @mutation(file, original, replacement[, expect=...]) beside a test (relay_core.testing.mutation) is read from
the source. For each one: `original` must occur exactly once in `file` (else "anchor not found" or
"anchor ambiguous"), the replacement is applied, only the marked test runs, and it must FAIL. The file is
restored in a `finally` block. Exit 1 if any mutation survives or any anchor is stale.

All of that happens in a private copy of the tree, never in the working tree: a mutated file in the working
tree turned other lanes' test runs red while a mutation was in flight (01/10). The copy's source dirs go
first on PYTHONPATH, ahead of the editable installs that point at the working tree.

A marked test must pass UNMUTATED before its mutation counts: a test that errors in setup (no database, a
missing fixture) would otherwise turn every mutation "red" for the wrong reason (01/10). Each test's
baseline runs once per run when it is green. A baseline that fails or times out is run once more, alone: green the
second time counts and the test is printed as FLAKY (a timing-sensitive test on a loaded machine); red twice is
FAILED with both failure lines. A mutant run is never retried: a surviving mutation is FAILED.

Stale .pyc files make a mutation lie (the old code keeps running), so every run starts with `__pycache__`
removed and PYTHONDONTWRITEBYTECODE=1; the cache is removed again after the restore.

--jobs N runs the work on N private copies at once. The unit of work is one test: its baseline, then its markers one
after the other (the baseline is run once, as in the serial run). Units start heaviest first (most markers). A unit
whose first baseline fails is not retried at once: after the queue is empty the failed baselines are repeated one at a
time on an idle machine (green the second time = FLAKY and its markers run then; red twice = FAILED), so a baseline
that failed only because N tests were running together is not counted against the test. A mutant run is never
retried. The output and the exit status are those of the serial run, in marker order.

Browser tests (@pytest.mark.browser) need Chrome, which the box has only in its docker image. With
MUTATE_BROWSER_IMAGE=<tag> (built by scripts/browser-image.sh) they run in that image, the copy mounted read-only, and
with --jobs they have a queue of their own (MUTATE_BROWSER_JOBS workers, default 3). Without it they run here.

Two runs in one tree corrupt each other (each restores the other's mutated snapshot, 01/10), so a run takes
an exclusive lock on ROOT/.mutate.lock and a second run is refused (exit 2) instead of waiting.

    mutate.py [ROOT] [--only PATTERN ...] [--jobs N]   ROOT defaults to this repo; PATTERN selects markers whose test id
                                            or file contains it (one lane mutating only its own files)
"""

import argparse
import ast
import fcntl
import itertools
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TIMEOUT_S = 300
LIVE: set[int] = set()                 # process groups of the pytest runs in flight, stopped together on SIGTERM
STOP = threading.Event()
CONTAINERS: set[str] = set()           # names of the browser containers in flight
BROWSER: set[str] = set()              # test ids that run in the browser image (empty: everything runs here)
BROWSER_TESTS = "packages/relay-browser/tests/"
IMAGE = ""                             # the image tag (scripts/browser-image.sh), from MUTATE_BROWSER_IMAGE
_names = itertools.count()


def _literals(call: ast.Call, names: dict[str, str], where: str = "") -> tuple[str, str, str, str | None]:
    """The three arguments: string literals, or module-level `NAME = "..."` constants of the same file."""
    def value(a: ast.expr) -> str | None:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
        return names.get(a.id) if isinstance(a, ast.Name) else None

    got = [value(a) for a in call.args]
    expect = [value(k.value) for k in call.keywords if k.arg == "expect"]
    if len(got) != 3 or None in got or len(call.keywords) != len(expect) or None in expect:
        raise SystemExit("%s:%d: @mutation takes three strings (literals or module-level NAME = \"...\" constants; an "
                         "empty string is fine, an expression is not) and an optional expect= string"
                         % (where, call.lineno))
    return (*got, expect[0] if expect else None)


def _is_marker(dec: ast.expr) -> bool:
    return isinstance(dec, ast.Call) and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "mutation"


def _test_functions(root: Path):
    """(file, module-level string constants, test node id, function node) for every test, in a stable order."""
    for base in ("tests", "packages", "apps"):
        for path in sorted((root / base).rglob("test_*.py")):
            if path.name.startswith("._") or "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                     and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                     for t in n.targets if isinstance(t, ast.Name)}
            funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
            tests = [(rel + "::" + n.name, n) for n in tree.body if isinstance(n, funcs)]
            for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
                tests += [("%s::%s::%s" % (rel, cls.name, n.name), n) for n in cls.body if isinstance(n, funcs)]
            for node_id, fn in tests:
                yield rel, names, node_id, fn


def collect(root: Path) -> list[tuple[str, str, str, str, str | None]]:
    """(test node id, file, original, replacement, expect) for every marker, in a stable order."""
    return [(node_id, *_literals(d, names, rel)) for rel, names, node_id, fn in _test_functions(root)
            for d in fn.decorator_list if _is_marker(d)]


def browser_tests(root: Path) -> set[str]:
    """Node ids of the tests that need Chrome, which the box has only in its image: the ones marked
    `@pytest.mark.browser`, and every test of relay-browser, because a mutant of it that is not caught on the way
    starts a real Chrome even in a test that is not marked (test_no_redactor_means_nothing_starts)."""
    return {node_id for rel, _names, node_id, fn in _test_functions(root)
            if rel.startswith(BROWSER_TESTS)
            or any(isinstance(d, ast.Attribute) and d.attr == "browser" for d in fn.decorator_list)}


def unmarked(root: Path = REPO) -> list[str]:
    """Test ids of the markers without expect=: such a marker counts a red for ANY failure, even a helper timeout."""
    return [m[0] for m in collect(root) if m[4] is None]


def purge_bytecode(root: Path) -> None:
    for base in ("tests", "packages", "apps", "scripts"):
        for d in (root / base).rglob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)


_SKIP = shutil.ignore_patterns(".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mutate.lock")


def copy_tree(root: Path, into: Path) -> Path:
    """A private copy of the tree to mutate in, without the venv and caches."""
    dest = into / "tree"
    shutil.copytree(root, dest, ignore=_SKIP, symlinks=True)
    return dest


def source_path(root: Path) -> str:
    """The copy's package sources, so its (mutated) modules shadow the editable installs."""
    dirs = [str(p) for base in ("packages", "apps") for p in sorted((root / base).glob("*/src"))]
    return os.pathsep.join(dirs + [os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)


def _pytest(root: Path, node_id: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pytest", node_id, "-x", "--tb=short", "-rfE", "-p", "no:cacheprovider"]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": source_path(root)}
    name = ""
    if IMAGE and node_id in BROWSER:
        # Chrome lives in the image (tini + xvfb-exec as its entrypoint): the copy is mounted read-only and its
        # sources go first on PYTHONPATH, ahead of the image's own install of the packages. These tests use no database.
        name = "relay-mutate-%d-%d" % (os.getpid(), next(_names))
        srcs = ["/work/" + str(p.relative_to(root)) for b in ("packages", "apps")
                for p in sorted(root.glob(b + "/*/src"))] + ["/work/scripts"]
        cmd = ["docker", "run", "--rm", "--name", name, "--label", "relay.mutate.run=%d" % os.getpid(),
               "--shm-size=1g", "-v", "%s:/work:ro" % root, "-w", "/work", "-e", "PYTHONDONTWRITEBYTECODE=1",
               "-e", "PYTHONPATH=" + os.pathsep.join(srcs), IMAGE, "pytest",
               "-p", "short_tmp_plugin", *cmd[3:]]    # Chrome's socket path must stay under 108 bytes
        CONTAINERS.add(name)
    # Own session: a timeout or SIGTERM stops pytest and everything it started, not only pytest.
    proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    LIVE.add(proc.pid)
    try:
        out, err = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        _remove(name)
        proc.communicate()
        raise
    finally:
        LIVE.discard(proc.pid)
        CONTAINERS.discard(name)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _remove(*names: str) -> None:
    """Stop browser containers: killing the docker client does not stop the container."""
    names = tuple(n for n in names if n)
    if names:
        subprocess.run(["docker", "rm", "-f", *names], capture_output=True, check=False)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def terminate(*_) -> None:
    STOP.set()
    for pid in list(LIVE):
        _kill_group(pid)
    _remove(*CONTAINERS)
    sys.exit(143)


def baseline(root: Path, node_id: str) -> str:
    """'' when the test passes unmutated, else why it does not (then no mutation of it can count)."""
    purge_bytecode(root)
    try:
        run = _pytest(root, node_id)
    except subprocess.TimeoutExpired:
        return "baseline timed out after %d s" % TIMEOUT_S
    if run.returncode == 0:
        return ""
    reason = next((ln.strip() for ln in run.stdout.splitlines() if ln.startswith("E ")), "exit %d" % run.returncode)
    return "baseline not green (unmutated test fails: %s)" % reason


def baseline_checked(root: Path, node_id: str) -> tuple[str, str]:
    """(failure, flaky). `failure` is '' when the test passes unmutated, on the first run or the second; `flaky` is
    the first run's reason when the second run was needed and green, else ''. Only a failed first run is repeated."""
    first = baseline(root, node_id)
    if not first:
        return "", ""
    second = baseline(root, node_id)
    if not second:
        return "", first
    return "%s; second run: %s" % (first, second), ""


def mutate_once(root: Path, node_id: str, file: str, original: str, replacement: str,
                expect: str | None = None) -> tuple[bool, str]:
    """(ok, message). ok means the mutation turned its test red."""
    target = root / file
    if not target.is_file():
        return False, "file not found: " + file
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    hits = text.count(original)
    if hits != 1:
        what = "not found" if hits == 0 else "ambiguous (%d matches)" % hits
        return False, "anchor %s in %s" % (what, file)
    try:
        target.write_bytes(text.replace(original, replacement, 1).encode("utf-8"))
        purge_bytecode(root)
        try:
            run = _pytest(root, node_id)
        except subprocess.TimeoutExpired:
            return False, "pytest timed out after %d s" % TIMEOUT_S
    finally:
        target.write_bytes(raw)
        purge_bytecode(root)
    if run.returncode == 1:
        # Red means the marked test itself failed (a FAILED/ERROR line naming it; a collection error names only the
        # file) and, with expect=, that its output says what the mutation should have made it say.
        lines = run.stdout.splitlines()
        if not any(ln.startswith(("FAILED ", "ERROR ")) and node_id in ln for ln in lines):
            return False, "pytest exit 1 but the marked test did not fail (collection or other error)"
        if expect is not None and expect not in run.stdout:
            return False, "red, but not as expected: %r missing from the failure output" % expect
        return True, next((ln.strip() for ln in lines if ln.startswith("E ")), "failed")
    return False, "mutation survived" if run.returncode == 0 else "pytest exit %d (not a test failure)" % run.returncode


def run(root: Path = REPO, only: tuple[str, ...] = (), jobs: int = 1) -> int:
    fd = os.open(root / ".mutate.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("mutate: another run holds %s; concurrent runs corrupt files, try again when it ends"
                  % (root / ".mutate.lock"), file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory(prefix="relay-mutate-") as tmp:
            return _run_locked(copy_tree(root, Path(tmp)), only, jobs)
    finally:
        os.close(fd)                          # closing the descriptor releases the lock


def shown_of(replacement: str) -> str:
    return (replacement.strip().splitlines() or [""])[0][:50] or "(removed)"


def line(ok: bool, node_id: str, file: str, shown: str, msg: str) -> str:
    return "%s %s [%s -> %s]\n      %s" % ("red     " if ok else "FAILED  ", node_id, file, shown, msg[:400])


def _summary(killed: int, total: int, flaky: int) -> int:
    print("mutate: %d/%d killed, %d failed, %d flaky baseline(s)" % (killed, total, total - killed, flaky))
    return 0 if killed == total else 1


Unit = tuple[str, str, list[tuple[bool, str]]]      # (baseline failure, flaky first failure, one outcome per marker)


def browser_jobs() -> int:
    return max(1, int(os.environ.get("MUTATE_BROWSER_JOBS", "3")))


def _units(marks: list) -> list[tuple[str, list]]:
    """The markers grouped by test, heaviest (most markers) first; ties keep the marker order."""
    by_test: dict[str, list] = {}
    for m in marks:
        by_test.setdefault(m[0], []).append(m)
    return sorted(by_test.items(), key=lambda kv: -len(kv[1]))


def _mutants(root: Path, ms: list) -> list[tuple[bool, str]]:
    return [mutate_once(root, m[0], m[1], m[2], m[3], m[4]) for m in ms]


def _parallel(root: Path, marks: list, jobs: int) -> dict[str, Unit]:
    """Phase 1: N workers, each on its own copy, take tests off the queue; the tests that run in the browser image
    have a queue of their own with BROWSER_JOBS workers (a Chrome is heavy), working at the same time. Phase 2: the
    failed baselines, one at a time, on copy 0 with nothing else running; a recovered test's markers run there too."""
    units = _units(marks)
    queues = {False: queue.Queue(), True: queue.Queue()}
    for u in units:
        queues[bool(IMAGE) and u[0] in BROWSER].put(u)
    done: dict[str, Unit] = {}
    retry: list[tuple[str, list, str]] = []
    lock = threading.Lock()
    with tempfile.TemporaryDirectory(prefix="relay-mutate-jobs-") as tmp:
        sizes = {False: min(jobs, queues[False].qsize()), True: min(browser_jobs(), queues[True].qsize())}
        copies = [copy_tree(root, Path(tmp) / ("w%d" % i)) for i in range(sum(sizes.values()))]
        workers = [(c, False) for c in copies[:sizes[False]]] + [(c, True) for c in copies[sizes[False]:]]

        def work(copy: Path, browser: bool) -> None:
            while not STOP.is_set():
                try:
                    node_id, ms = queues[browser].get_nowait()
                except queue.Empty:
                    return
                first = baseline(copy, node_id)
                if first:
                    with lock:
                        retry.append((node_id, ms, first))
                    continue
                outcomes = _mutants(copy, ms)
                with lock:
                    done[node_id] = ("", "", outcomes)

        with ThreadPoolExecutor(max_workers=len(copies)) as pool:
            for f in [pool.submit(work, c, b) for c, b in workers]:
                f.result()
        for node_id, ms, first in sorted(retry, key=lambda r: [u[0] for u in units].index(r[0])):
            second = baseline(copies[0], node_id)
            if second:
                done[node_id] = ("%s; second run: %s" % (first, second), "", [(False, "")] * len(ms))
            else:
                done[node_id] = ("", first, _mutants(copies[0], ms))
    return done


def _run_locked(root: Path, only: tuple[str, ...], jobs: int = 1) -> int:
    marks = [m for m in collect(root) if not only or any(p in m[0] or p in m[1] for p in only)]
    if not marks:
        print("mutate: no @mutation markers found" + (" for %s" % ", ".join(only) if only else ""),
              file=sys.stderr)
        return 1
    global BROWSER, IMAGE
    IMAGE = os.environ.get("MUTATE_BROWSER_IMAGE", "")
    BROWSER = browser_tests(root) if IMAGE else set()
    purge_bytecode(root)
    killed, flaky = 0, 0
    baselines: dict[str, str] = {}
    if jobs > 1:
        os.environ.setdefault("RELAY_TEST_PG_TEMPLATE", "relay_tpl_%d_%d" % (time.time(), os.getpid()))
        done = _parallel(root, marks, jobs)
        seen: dict[str, int] = {}
        for node_id, file, _original, replacement, _expect in marks:
            failure, first_failure, outcomes = done[node_id]
            shown = shown_of(replacement)
            n = seen[node_id] = seen.get(node_id, -1) + 1
            if n == 0 and first_failure:
                flaky += 1
                print("FLAKY    %s [%s -> %s]\n      %s" % (node_id, file, shown, first_failure[:200]))
            ok, msg = (False, failure) if failure else outcomes[n]
            killed += ok
            print(line(ok, node_id, file, shown, msg))
        return _summary(killed, len(marks), flaky)
    for node_id, file, original, replacement, expect in marks:
        shown = shown_of(replacement)
        if node_id not in baselines:
            baselines[node_id], first_failure = baseline_checked(root, node_id)
            if first_failure:
                flaky += 1
                print("FLAKY    %s [%s -> %s]\n      %s" % (node_id, file, shown, first_failure[:200]))
        if baselines[node_id]:
            ok, msg = False, baselines[node_id]
        else:
            ok, msg = mutate_once(root, node_id, file, original, replacement, expect)
        killed += ok
        print(line(ok, node_id, file, shown, msg))
    return _summary(killed, len(marks), flaky)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)        # stops the pytest runs in flight; the copies are thrown away
    ap = argparse.ArgumentParser(prog="mutate.py")
    ap.add_argument("root", nargs="?", default=str(REPO))
    ap.add_argument("--only", action="append", default=[], help="select markers by test id or file substring")
    ap.add_argument("--jobs", type=int, default=1, help="private copies running tests at once (default 1: serial)")
    args = ap.parse_args()
    sys.exit(run(Path(args.root).resolve(), tuple(args.only), max(1, args.jobs)))
