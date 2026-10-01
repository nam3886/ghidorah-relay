"""Static guards over the source tree (contracts §2 import rules, §6.4 browser guards, §14.6 language).

Every guard takes the repo root, so its test can also run it on a small tree with a planted
violation and see it red (tests/test_guards.py).
"""

import ast
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]

PROVIDER_MODULES = {"relay_dola", "relay_vibes", "relay_meta"}
BROWSER_ONLY = {"patchright", "playwright"}
ROUTE_CALLS = {"route", "unroute", "unroute_all", "route_from_har", "route_web_socket"}
LAUNCH_CALLS = {"launch", "launch_persistent_context", "connect_over_cdp", "connect"}
SESSION_FILE = "packages/relay-browser/src/relay_browser/session.py"
REGISTRY_FILE = "packages/relay-core/src/relay_core/providers.py"   # the one core module that may name providers
PRIVATE_KEY_VAR = "CREDENTIAL_SEAL_PRIVATE_KEY"                      # workers only; the api holds the public key
PROVIDER_VARS = {"provider", "provider_name", "provider_id"}
PROVIDER_NAMES = {m.removeprefix("relay_") for m in PROVIDER_MODULES}
# Bare `"meta"` is the name of the `Output.meta` field, a dict key and an SQL parameter all over the core, so as a
# plain literal it is allowed; it is still caught in a comparison with a provider reference, as `"meta."` and as
# `"relay_meta"`.
LITERAL_TOKENS = (PROVIDER_NAMES - {"meta"}) | PROVIDER_MODULES
CODE_PREFIXES = tuple(n + "." for n in PROVIDER_NAMES)


def source_files(root: Path) -> list[Path]:
    files = (p for base in ("packages", "apps") for p in (root / base).rglob("*.py"))
    return sorted(p for p in files if not p.name.startswith("._"))   # skip macOS metadata files


def owner(root: Path, path: Path) -> str:
    """The top-level module a file belongs to: packages/<dir>/src/<module>/..."""
    parts = path.relative_to(root).parts
    return parts[3] if len(parts) > 3 and parts[2] == "src" else ""


def is_pure(root: Path, path: Path) -> bool:
    """A provider's pure modules are its whole package minus engine/** and e2e.py (contracts §2 rule 5)."""
    inner = path.relative_to(root).parts[4:]
    return owner(root, path) in PROVIDER_MODULES and bool(inner) and inner[0] != "engine" and inner != ("e2e.py",)


def imported_modules(path: Path) -> list[tuple[int, str]]:
    """Absolute dotted names a file imports; relative imports are resolved against the file's package."""
    parts = path.parts
    # the last `src` is the repo's own, not a parent directory's
    src_at = len(parts) - 1 - parts[::-1].index("src") if "src" in parts else -1
    package = list(parts[src_at + 1:-1]) if src_at >= 0 else []
    out = []
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out += [(n.lineno, a.name) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = package[:len(package) - (n.level - 1)] if n.level else []
            mod = ".".join([*base, *([n.module] if n.module else [])])
            if mod:
                out.append((n.lineno, mod))
                # `from relay_x import engine` imports a submodule; `engine` is the only one the rules care about
                out += [(n.lineno, mod + ".engine") for a in n.names if a.name == "engine"]
    return out


def import_violations(root: Path = REPO) -> list[str]:
    bad = []
    for path in source_files(root):
        me = owner(root, path)
        rel = path.relative_to(root)
        for line, mod in imported_modules(path):
            top = mod.split(".")[0]
            where = "%s:%d imports %s" % (rel, line, mod)
            if me == "relay_core" and (top in PROVIDER_MODULES or top == "relay_browser"):
                bad.append(where + " (relay_core imports no provider and no relay_browser)")
            if me in PROVIDER_MODULES and top in PROVIDER_MODULES and top != me:
                bad.append(where + " (providers never import each other)")
            if top in BROWSER_ONLY and me != "relay_browser":
                bad.append(where + " (patchright only inside relay_browser)")
            if is_pure(root, path) and (top in BROWSER_ONLY or top == "relay_browser"
                                        or mod == me + ".engine" or mod.startswith(me + ".engine.")):
                bad.append(where + " (a pure provider module: the API imports it)")
    return bad


def _calls(path: Path):
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            yield n.lineno, n.func.attr


def route_violations(root: Path = REPO) -> list[str]:
    """Any route turns off Chrome's HTTP cache; serving dola's scripts through one broke Send 5/5 (30/09)."""
    return ["%s:%d .%s(" % (p.relative_to(root), line, attr)
            for p in source_files(root) for line, attr in _calls(p) if attr in ROUTE_CALLS]


def launch_violations(root: Path = REPO) -> list[str]:
    """Browsers start only in relay_browser/session.py (open_session), which owns the profile lock."""
    bad = []
    for p in source_files(root):
        if p.relative_to(root).as_posix() == SESSION_FILE:
            continue
        src = p.read_text(encoding="utf-8")
        if not re.search(r"\b(chromium|firefox|webkit|patchright|playwright)\b", src):
            continue                   # `.connect(` on a DB pool is not a browser launch
        bad += ["%s:%d .%s(" % (p.relative_to(root), line, attr) for line, attr in _calls(p) if attr in LAUNCH_CALLS]
    return bad


def demo_violations(root: Path = REPO) -> list[str]:
    """v4 proved its rules with an in-module `_demo()`; relay proves them with test files, so none may come back."""
    bad = []
    for p in source_files(root):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_demo":
                bad.append("%s:%d def _demo" % (p.relative_to(root), n.lineno))
            elif isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "_demo":
                bad.append("%s:%d _demo(" % (p.relative_to(root), n.lineno))
    return bad


SPAWN_CALLS = {"create_task", "ensure_future", "gather", "TaskGroup", "start_soon", "run_coroutine_threadsafe"}


def tx_task_violations(root: Path = REPO) -> list[str]:
    """No task is started inside an `async with tx(...)` block.

    The transaction guard accepts a connection only while its block is open. A task started inside the block can
    pass that check, outlive the block, and then write on a connection that is back in the pool, where each
    statement commits alone. Awaiting the work inline keeps every statement inside the block."""
    bad = []
    for p in source_files(root):
        if "tests" in p.relative_to(root).parts:      # a test may start a late task on purpose, to prove the refusal
            continue
        for block in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(block, ast.AsyncWith):
                continue
            opens_tx = any(isinstance(i.context_expr, ast.Call)
                           and getattr(i.context_expr.func, "id", getattr(i.context_expr.func, "attr", "")) == "tx"
                           for i in block.items)
            if not opens_tx:
                continue
            for n in (x for stmt in block.body for x in ast.walk(stmt)):
                name = getattr(n.func, "id", getattr(n.func, "attr", "")) if isinstance(n, ast.Call) else ""
                if name in SPAWN_CALLS:
                    bad.append("%s:%d %s( inside a tx() block" % (p.relative_to(root), n.lineno, name))
    return bad


def _is_provider_ref(n: ast.AST) -> bool:
    """`provider`, `job.provider`, `row["provider"]`, `getattr(job, "provider")`."""
    if isinstance(n, ast.Subscript):
        return _is_str(n.slice) and n.slice.value in PROVIDER_VARS
    if isinstance(n, ast.Call):
        return (getattr(n.func, "id", "") == "getattr" and len(n.args) > 1 and _is_str(n.args[1])
                and n.args[1].value in PROVIDER_VARS)
    name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else ""
    return name in PROVIDER_VARS


def _is_str(n: ast.AST) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def _docstring_ids(tree: ast.AST) -> set[int]:
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, scopes) and n.body
            and isinstance(n.body[0], ast.Expr) and _is_str(n.body[0].value)}


def core_provider_violations(root: Path = REPO) -> list[str]:
    """I18: the core never branches on a provider and never names one.

    Two rules, both skipped only for the registry module:
    - a comparison, `in` test or `match` that has a provider reference (`provider`, `x.provider`,
      `row["provider"]`, `getattr(x, "provider")`) on one side and a string literal on the other;
    - any string literal in code (docstrings excepted) that is `dola` or `vibes`, a provider package
      (`relay_dola`), or starts with a provider name and a dot (`dola.likeness_refused`, `meta.x`). This covers
      `spec.name == "dola"`, `{"dola": 1}[p]`, `p.startswith("dola")` and provider error codes. A bare `"meta"`
      is not flagged by this rule (see LITERAL_TOKENS); rule A still catches `provider == "meta"`.
    Provider package imports are import_violations' job.
    """
    bad = []
    for p in source_files(root):
        rel = p.relative_to(root)
        if owner(root, p) != "relay_core" or rel.as_posix() == REGISTRY_FILE:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        docstrings = _docstring_ids(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare):
                operands = [n.left, *n.comparators]
                collections = (ast.Tuple, ast.List, ast.Set)
                literal = any(_is_str(o) or (isinstance(o, collections) and any(map(_is_str, o.elts)))
                              for o in operands)
                if literal and any(map(_is_provider_ref, operands)):
                    bad.append("%s:%d compares a provider name with a string literal" % (rel, n.lineno))
            elif isinstance(n, ast.Match) and _is_provider_ref(n.subject):
                if any(_is_str(c) for case in n.cases for c in ast.walk(case.pattern) if isinstance(c, ast.Constant)):
                    bad.append("%s:%d matches a provider name against a string literal" % (rel, n.lineno))
            elif _is_str(n) and id(n) not in docstrings and (n.value.startswith(CODE_PREFIXES) or any(
                    n.value == m or n.value.startswith(m + ".") for m in LITERAL_TOKENS)):
                bad.append("%s:%d names a provider: %r" % (rel, n.lineno, n.value))
    return bad


# ---- deploy (contracts §14.8 static half, I20): which env files each compose service can see ------------------
def is_private_key_var(name: str) -> bool:
    return "PRIVATE_KEY" in name.upper()


def _service_env_files(svc: dict) -> list[str]:
    files = svc.get("env_file") or []
    files = [files] if isinstance(files, (str, dict)) else files
    return [f if isinstance(f, str) else f["path"] for f in files]


def _service_env_names(svc: dict) -> set[str]:
    env = svc.get("environment") or {}
    return set(env) if isinstance(env, dict) else {e.split("=")[0] for e in env}


def _declared_names(path: Path) -> set[str]:
    lines = (ln.split("#")[0].strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return {ln.split("=")[0] for ln in lines if ln}


def _image_violations(deploy: Path) -> list[str]:
    """Base images are pinned by digest (D23); only images built from this repo (`relay-*`) are exempt."""
    bad = []
    for name in ("compose.yml", "compose.check.yml"):
        for svc, body in yaml.safe_load((deploy / name).read_text(encoding="utf-8"))["services"].items():
            image = body.get("image", "")
            if image and "@sha256:" not in image and not image.startswith("relay-"):
                bad.append("%s: service %s image %s is not pinned by digest" % (name, svc, image))
    lines = (deploy / "Dockerfile").read_text(encoding="utf-8").splitlines()
    stages = {m.group(1) for ln in lines if (m := re.search(r"\bAS (\S+)$", ln))}
    for ln in lines:
        ref = re.match(r"FROM (\S+)", ln) or re.search(r"COPY --from=(\S+)", ln)
        if ref and ref.group(1) not in stages and "@sha256:" not in ref.group(1):
            bad.append("Dockerfile: %s is not pinned by digest" % ref.group(1))
    return bad


def deploy_violations(root: Path = REPO) -> list[str]:
    deploy = root / "deploy"
    services = yaml.safe_load((deploy / "compose.yml").read_text(encoding="utf-8"))["services"]
    bad, users = _image_violations(deploy), {}
    for name, svc in services.items():
        for f in _service_env_files(svc):
            users.setdefault(f, []).append(name)
            if not (deploy / (f + ".example")).exists():
                bad.append("service %s: env file %s has no %s.example template" % (name, f, f))
    bad += ["env file %s is shared by %s (one env file per service)" % (f, ", ".join(s)) for f, s in users.items()
            if len(s) > 1]
    for name, svc in services.items():
        if name.startswith("worker-"):
            continue                       # only provider workers hold a private key (I20)
        for var in sorted(v for v in _service_env_names(svc) if is_private_key_var(v)):
            bad.append("service %s sets %s in its environment" % (name, var))
        for f in _service_env_files(svc):
            t = deploy / (f + ".example")
            bad += ["service %s reads %s, which declares %s" % (name, f, v)
                    for v in sorted(_declared_names(t) if t.exists() else ()) if is_private_key_var(v)]
    for ex in sorted(deploy.glob("*.env.example")):
        for line in ex.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and not re.fullmatch(r"[A-Z][A-Z0-9_]*=", line):
                bad.append("%s: %r is not a bare variable name (templates hold names only)" % (ex.name, line))
        bad += ["%s declares %s (workers only)" % (ex.name, v) for v in sorted(_declared_names(ex))
                if is_private_key_var(v) and not ex.name.startswith("worker-")]
    return bad


# Identifiers and user-facing error text are English. Comments and docstrings may stay Vietnamese:
# they carry the reason behind a decision. (Carried over from dola_v4's language gate.)
VIET_MARKS = set("àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ")
# Whole-word match only: `_demo` was once flagged because it contains `dem`.
VIET_WORD = re.compile(
    r"^_{0,2}(moc|bay_gio|ngu|nham|lech|gia|muc|bang|cot|tham_so|dieu_kien|con_lai|ung_vien|khoi_phuc"
    r"|hoan|dem|sach|ngan_sach|ky_vong|qua_han|vua_hoi|da_tieu|co_cu|co_moi|luoi_cuoi|nghe_duoc"
    r"|loi_nguoi_dung|khong_tru_credit|ket_qua|thoi_gian|tai_khoan|nguoi_dung|so_du|khoa)$", re.I)


def identifier_violations(root: Path = REPO) -> list[str]:
    bad = []
    for p in source_files(root) + sorted((root / "tests").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        rel = p.relative_to(root)
        for n in ast.walk(tree):
            name = (n.name if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    else n.id if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                    else n.arg if isinstance(n, ast.arg) else None)
            if name and (not name.isascii() or VIET_WORD.match(name)):
                bad.append("%s:%d identifier %r is not English" % (rel, n.lineno, name))
            if isinstance(n, ast.Raise):
                for c in ast.walk(n):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and VIET_MARKS & set(c.value.lower()):
                        bad.append("%s:%d error text is not English" % (rel, c.lineno))
    return bad
