#!/usr/bin/env python3
"""Prove the tests are real guards: break the code on purpose and demand that the marked test turns red.

Every `@mutation(file, original, replacement)` beside a test (relay_core.testing.mutation) is collected from
the source. For each one: `original` must occur exactly once in `file` (else "anchor not found" or
"anchor ambiguous"), the replacement is applied, only the marked test runs, and it must FAIL. The file is
restored in a `finally` block. Exit 1 if any mutation survives or any anchor is stale.

Stale .pyc files make a mutation lie (the old code keeps running), so every run starts with `__pycache__`
removed and PYTHONDONTWRITEBYTECODE=1; the cache is removed again after the restore.

    mutate.py [ROOT]        ROOT defaults to the repo this script lives in
"""

import ast
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TIMEOUT_S = 300


def _literals(call: ast.Call, names: dict[str, str]) -> tuple[str, str, str]:
    """The three arguments: string literals, or module-level `NAME = "..."` constants of the same file."""
    def value(a: ast.expr) -> str | None:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
        return names.get(a.id) if isinstance(a, ast.Name) else None

    got = [value(a) for a in call.args]
    if len(got) != 3 or call.keywords or None in got:
        raise SystemExit("line %d: @mutation takes three strings (literals or module-level constants)" % call.lineno)
    return tuple(got)


def _is_marker(dec: ast.expr) -> bool:
    return isinstance(dec, ast.Call) and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "mutation"


def collect(root: Path) -> list[tuple[str, str, str, str]]:
    """(test node id, file, original, replacement) for every marker, in a stable order."""
    found = []
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
                found += [(node_id, *_literals(d, names)) for d in fn.decorator_list if _is_marker(d)]
    return found


def purge_bytecode(root: Path) -> None:
    for base in ("tests", "packages", "apps", "scripts"):
        for d in (root / base).rglob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)


def mutate_once(root: Path, node_id: str, file: str, original: str, replacement: str) -> tuple[bool, str]:
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
            cmd = [sys.executable, "-m", "pytest", node_id, "-x", "--tb=short", "-p", "no:cacheprovider"]
            run = subprocess.run(cmd, cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                 capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return False, "pytest timed out after %d s" % TIMEOUT_S
    finally:
        target.write_bytes(raw)
        purge_bytecode(root)
    if run.returncode == 1:
        reason = next((ln.strip() for ln in run.stdout.splitlines() if ln.startswith("E ")), "failed")
        return True, reason
    return False, "mutation survived" if run.returncode == 0 else "pytest exit %d (not a test failure)" % run.returncode


def run(root: Path = REPO) -> int:
    marks = collect(root)
    if not marks:
        print("mutate: no @mutation markers found", file=sys.stderr)
        return 1
    purge_bytecode(root)
    killed = 0
    for node_id, file, original, replacement in marks:
        ok, msg = mutate_once(root, node_id, file, original, replacement)
        killed += ok
        print("%s %s [%s -> %s]\n      %s" % ("red     " if ok else "FAILED  ", node_id, file,
                                              replacement.strip().splitlines()[0][:50] or "(removed)", msg[:200]))
    print("mutate: %d/%d killed, %d failed" % (killed, len(marks), len(marks) - killed))
    return 0 if killed == len(marks) else 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # let the `finally` restore the file
    sys.exit(run(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO))
