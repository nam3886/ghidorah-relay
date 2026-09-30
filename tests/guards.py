"""Static guards over the source tree (contracts §2 import rules, §6.4 browser guards, §14.6 language).

Every guard takes the repo root, so its test can also run it on a small tree with a planted
violation and see it red (tests/test_guards.py).
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROVIDER_MODULES = {"relay_dola", "relay_vibes", "relay_meta"}
BROWSER_ONLY = {"patchright", "playwright"}
ROUTE_CALLS = {"route", "unroute", "unroute_all", "route_from_har", "route_web_socket"}
LAUNCH_CALLS = {"launch", "launch_persistent_context", "connect_over_cdp", "connect"}
SESSION_FILE = "packages/relay-browser/src/relay_browser/session.py"


def source_files(root: Path) -> list[Path]:
    files = (p for base in ("packages", "apps") for p in (root / base).rglob("*.py"))
    return sorted(p for p in files if not p.name.startswith("._"))   # skip macOS metadata files


def owner(root: Path, path: Path) -> str:
    """The top-level module a file belongs to: packages/<dir>/src/<module>/..."""
    parts = path.relative_to(root).parts
    return parts[3] if len(parts) > 3 and parts[2] == "src" else ""


def imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(n.lineno, a.name) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append((n.lineno, n.module))
    return out


def import_violations(root: Path = REPO) -> list[str]:
    bad = []
    for path in source_files(root):
        me = owner(root, path)
        rel = path.relative_to(root)
        is_spec = me in PROVIDER_MODULES and path.name == "spec.py"
        for line, mod in imported_modules(path):
            top = mod.split(".")[0]
            where = "%s:%d imports %s" % (rel, line, mod)
            if me == "relay_core" and (top in PROVIDER_MODULES or top == "relay_browser"):
                bad.append(where + " (relay_core imports no provider and no relay_browser)")
            if me in PROVIDER_MODULES and top in PROVIDER_MODULES and top != me:
                bad.append(where + " (providers never import each other)")
            if top in BROWSER_ONLY and me != "relay_browser":
                bad.append(where + " (patchright only inside relay_browser)")
            if is_spec and (top in BROWSER_ONLY or top == "relay_browser" or mod.startswith(me + ".engine")):
                bad.append(where + " (a spec is pure: the API imports it)")
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
