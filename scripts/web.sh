#!/usr/bin/env bash
# The admin web (apps/admin-web, Vue + Vite) on the box. Nothing here runs Node, npm or Docker on the Mac (D95).
#   scripts/web.sh [--tree NAME] lock     regenerate apps/admin-web/package-lock.json in the container; pull it back
#   scripts/web.sh [--tree NAME] build    build in the container; pull back static/app/ and the two generated
#                                         declaration files (components.d.ts, auto-imports.d.ts), ready to commit
#   scripts/web.sh [--tree NAME] check    lockfile audit, `style=` grep, then vue-tsc, ESLint and Vitest in the container
#   scripts/web.sh [--tree NAME] verify   a fresh build must equal the committed dist and both declaration files, byte
#                                         for byte; else it fails and names the fix (`scripts/web.sh build`, commit)
#   scripts/web.sh [--tree NAME] format   `npm run format` (Prettier) in the container; pull back only the files it
#                                         changed under apps/admin-web/, each only if it still has its pushed content
#                                         (a file edited meanwhile is skipped and listed: rerun); prints the counts
# On the Mac the command goes through scripts/box.sh (the same --tree NAME rules: a developer its own folder) and, for
# `lock` and `build` only, the result is pulled back with rsync once the run on the box ended well: box.sh is one way
# (rsync --delete), so those two are the only pull-back paths. `check` and `verify` build in docker's own scratch space
# (the build context is a copy), write nothing into the synced tree, and a hash of apps/admin-web and of the dist taken
# before and after must be the same, else the run fails.
# The container is deploy/Dockerfile's web-* stages (Node image pinned by digest, workdir /web, npm ci from the
# lockfile, install scripts off). Exit codes: the failing step's own; 2 usage; 1 a check failed.
set -euo pipefail
cd "$(dirname "$0")/.."

APP=apps/admin-web
STATIC=packages/relay-core/src/relay_core/admin/static
DECLS=(components.d.ts auto-imports.d.ts)
BOX="${RELAY_BOX:-phuongnam@192.168.18.227}"

die() { echo "web.sh: $1" >&2; exit "${2:-2}"; }

tree=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tree) tree="${2:-}"; shift 2 || die "--tree needs a folder name" ;;
    -h | --help) sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) break ;;
  esac
done
cmd="${1:-}"
case "$cmd" in lock | build | check | verify | format) ;; *) die "usage: scripts/web.sh [--tree NAME] lock|build|check|verify|format" ;; esac
[ $# -eq 1 ] || die "one subcommand only"

# ---- on the Mac: through box.sh, and pull back what build and lock made ---------------------------------------------
if [ "${RELAY_ON_BOX:-}" != 1 ]; then
  boxargs=()
  [ -z "$tree" ] || boxargs+=(--tree "$tree")
  scripts/box.sh ${boxargs[@]+"${boxargs[@]}"} -- ./scripts/web.sh "$cmd"
  case "$cmd" in lock | build | format) ;; *) exit 0 ;; esac
  folder="${tree:-$(basename "$(git rev-parse --show-toplevel)")}"
  clean() { env -i HOME="$HOME" PATH="$PATH" ${SSH_AUTH_SOCK:+SSH_AUTH_SOCK="$SSH_AUTH_SOCK"} "$@"; }
  pull() { clean rsync -a "${@:3}" -e "ssh -o BatchMode=yes -o SendEnv=-*" "$BOX:relay-runs/$folder/$1" "$2"; }
  if [ "$cmd" = format ]; then
    # The box listed the files the formatter changed in .web-format/ with the sha256 each had when the tree was pushed
    # (MANIFEST). A file here is overwritten only if it still has that hash: a developer who kept editing meanwhile
    # keeps the edit, and the file is listed to rerun. No --delete, nothing but those files.
    got=$(mktemp -d "${TMPDIR:-/tmp}/relay-format.XXXXXX")
    trap 'rm -rf "$got"' EXIT
    pull ".web-format/" "$got/"
    python3 - "$got" "$APP" <<'PY'
import hashlib, os, shutil, sys
got, app = sys.argv[1:]
done, skipped = 0, []
for row in open(os.path.join(got, "MANIFEST"), encoding="utf-8").read().splitlines():
    pushed, rel = row.split("  ", 1)
    dest = os.path.join(app, rel)
    try:
        with open(dest, "rb") as fh:
            now = hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        now = None
    if now != pushed:
        skipped.append(rel)
        continue
    shutil.copyfile(os.path.join(got, "files", rel), dest)
    done += 1
for rel in skipped:
    print("web.sh format: %s changed during format, rerun" % rel)
print("web.sh format: %d file(s) reformatted, %d skipped" % (done, len(skipped)))
sys.exit(1 if skipped else 0)
PY
    exit $?
  fi
  if [ "$cmd" = lock ]; then
    pull "$APP/package-lock.json" "$APP/package-lock.json"
    echo "web.sh: pulled back $APP/package-lock.json"
  else
    mkdir -p "$STATIC/app"
    pull "$STATIC/app/" "$STATIC/app/" --delete
    for f in "${DECLS[@]}"; do pull "$APP/$f" "$APP/$f"; done
    echo "web.sh: pulled back $STATIC/app/ and ${DECLS[*]}"
  fi
  exit 0
fi

# ---- on the box ------------------------------------------------------------------------------------------------------
[ -d "$APP" ] || die "$APP does not exist in this tree"
scratch=$(mktemp -d "${TMPDIR:-/tmp}/relay-web.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

# `--output type=local` gives the stage's files and stores no image; `type=cacheonly` stores nothing at all.
dbuild() { docker build -f deploy/Dockerfile --target "$1" --output "$2" --progress=plain . ; }

# A hash of what check and verify must not change: the app's own tree (node_modules never is in it) and the dist.
tree_hash() {
  python3 - "$APP" "$STATIC" <<'PY'
import hashlib, os, sys
rows = []
for top in sys.argv[1:]:
    for d, dirs, files in os.walk(top):
        dirs[:] = [x for x in dirs if x != "node_modules"]
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                rows.append("%s %s %d\n" % (p, hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mode & 0o111 != 0))
print(hashlib.sha256("".join(sorted(rows)).encode()).hexdigest())
PY
}

# The lockfile policy (contracts: supply chain): exact versions, one registry, no git/file/link/tarball dependency,
# install scripts only for the approved packages (apps/admin-web/approved-install-scripts.txt; missing = none).
# The production licences are printed for the round report.
audit_lock() {
  python3 - "$APP" <<'PY'
import collections, json, os, re, sys
app = sys.argv[1]
bad = []
try:
    lock = json.load(open(os.path.join(app, "package-lock.json")))
    pkg = json.load(open(os.path.join(app, "package.json")))
except (OSError, ValueError) as e:
    sys.exit("lockfile audit: cannot read package.json or package-lock.json: %s" % e)
if lock.get("lockfileVersion") != 3:
    bad.append("lockfileVersion is %r, not 3" % lock.get("lockfileVersion"))
exact = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
for section in ("dependencies", "devDependencies"):
    for name, ver in pkg.get(section, {}).items():
        if not exact.match(ver):
            bad.append("package.json %s %s is %r: not an exact version" % (section, name, ver))
npmrc_path = os.path.join(app, ".npmrc")
npmrc = [ln.replace(" ", "").strip() for ln in open(npmrc_path)] if os.path.exists(npmrc_path) else []
for need in ("save-exact=true", "ignore-scripts=true", "fund=false"):
    if need not in npmrc:
        bad.append(".npmrc lacks %s" % need)
approved_file = os.path.join(app, "approved-install-scripts.txt")
approved = set()
if os.path.exists(approved_file):
    approved = {ln.split("#")[0].strip() for ln in open(approved_file)} - {""}
scripted, licences, count = set(), collections.Counter(), 0
for path, meta in lock.get("packages", {}).items():
    if path == "":
        continue
    count += 1
    name = meta.get("name") or path.split("node_modules/")[-1]
    resolved = meta.get("resolved", "")
    if meta.get("link"):
        bad.append("%s is a link" % path)
    elif not resolved.startswith("https://registry.npmjs.org/"):
        bad.append("%s resolved from %r, not https://registry.npmjs.org/" % (name, resolved))
    elif not meta.get("integrity", "").startswith("sha"):
        bad.append("%s has no integrity hash" % name)
    if meta.get("hasInstallScript"):
        scripted.add(name)
    if not meta.get("dev"):
        licences[str(meta.get("license", "UNKNOWN"))] += 1
if scripted != approved:
    bad.append("install scripts: lockfile has %s, approved list has %s" % (sorted(scripted), sorted(approved)))
if bad:
    sys.exit("lockfile audit FAILED:\n  " + "\n  ".join(bad))
print("lockfile audit: %d packages, registry.npmjs.org only, install scripts %s; production licences: %s"
      % (count, sorted(scripted) or "none", ", ".join("%s x%d" % kv for kv in sorted(licences.items())) or "none"))
PY
}

# No static style="..." in the web source (the admin CSP blocks it): `:style` and `v-bind:style` are fine, so a `style=`
# glued to `:`, a letter, `-` or `_` is not a match.
style_grep() {
  local paths=() p
  [ -e "$APP/src" ] || die "$APP/src does not exist: nothing to grep" 1
  for p in src index.html public; do [ -e "$APP/$p" ] && paths+=("$APP/$p"); done
  if grep -rnE '(^|[^:[:alnum:]_-])style=' "${paths[@]}"; then
    die "a static style= attribute is in the web source (see the CSP rules): use a class or :style" 1
  fi
}

same_tree_after() {          # same_tree_after BEFORE: the run must not have written into the synced tree
  local after; after=$(tree_hash)
  [ "$1" = "$after" ] || die "the run changed the source tree (hash $1 -> $after): check and verify must write nothing" 1
}

case "$cmd" in
  lock)
    dbuild web-lock-out "type=local,dest=$scratch/lock"
    cp "$scratch/lock/package-lock.json" "$APP/package-lock.json"
    # a failing audit does not stop the pull-back (the lockfile is what the developer needs to fix it); `check` fails
    audit_lock || echo "web.sh lock: the new lockfile fails the audit above; web.sh check will fail until it is fixed" >&2
    ;;
  build)
    dbuild web-out "type=local,dest=$scratch/out"
    rm -rf "$STATIC/app" && mkdir -p "$STATIC" && cp -R "$scratch/out/static/app" "$STATIC/app"
    for f in "${DECLS[@]}"; do cp "$scratch/out/$f" "$APP/$f"; done
    echo "web.sh: built $(find "$STATIC/app" -type f | wc -l) files into $STATIC/app"
    ;;
  format)
    dbuild web-format-out "type=local,dest=$scratch/fmt"
    # .web-format/ in this folder is what the Mac pulls: the changed files (formatted) and MANIFEST, one row per file
    # with the sha256 the file has in this folder, which is what the Mac pushed. The next sync deletes the folder.
    rm -rf .web-format && mkdir .web-format
    python3 - "$scratch/fmt" "$APP" .web-format <<'PY'
import hashlib, os, shutil, sys
fmt, app, out = sys.argv[1:]
rows = []
for d, _dirs, files in os.walk(fmt):
    for f in files:
        new = os.path.join(d, f)
        rel = os.path.relpath(new, fmt)
        old = os.path.join(app, rel)
        if not os.path.isfile(old):
            continue
        a, b = open(old, "rb").read(), open(new, "rb").read()
        if a != b:
            os.makedirs(os.path.dirname(os.path.join(out, "files", rel)), exist_ok=True)
            shutil.copyfile(new, os.path.join(out, "files", rel))
            rows.append("%s  %s\n" % (hashlib.sha256(a).hexdigest(), rel))
open(os.path.join(out, "MANIFEST"), "w", encoding="utf-8").write("".join(sorted(rows)))
print("web.sh format: the formatter changed %d file(s)" % len(rows))
PY
    ;;
  check)
    before=$(tree_hash)
    audit_lock
    style_grep
    # --no-cache-filter: the checks run every time (a cached layer would print nothing). The log is kept, the summary
    # lines (one per tool) are printed from it whether the build passed or not, then the log's tail if it failed.
    rc=0
    docker build -f deploy/Dockerfile --target web-check --no-cache-filter web-check --progress=plain . \
      > "$scratch/check.log" 2>&1 || rc=$?
    sed -E 's/^#[0-9]+ [0-9.]+ //' "$scratch/check.log" | grep -E '^(WEBCHECK |[[:space:]]*(Test Files|Tests)[[:space:]])' \
      | sed -E 's/^[[:space:]]*(Test Files|Tests)[[:space:]]+/WEBCHECK Vitest \1: /' | grep -v '^WEBCHECK .*echo' || true
    if [ "$rc" != 0 ]; then
      sed -E 's/^#[0-9]+ [0-9.]+ //' "$scratch/check.log" | tail -60 >&2
      die "web.sh check: FAILED (a step above did not pass; the log tail is shown)" 1
    fi
    same_tree_after "$before"
    echo "web.sh check: GREEN"
    ;;
  verify)
    before=$(tree_hash)
    dbuild web-out "type=local,dest=$scratch/out"
    fail=0
    diff -r "$scratch/out/static/app" "$STATIC/app" > "$scratch/diff" 2>&1 || fail=1
    for f in "${DECLS[@]}"; do cmp -s "$scratch/out/$f" "$APP/$f" || { echo "differs: $APP/$f" >> "$scratch/diff"; fail=1; }; done
    same_tree_after "$before"
    if [ "$fail" = 1 ]; then
      head -40 "$scratch/diff" >&2
      die "a fresh build differs from the committed dist or declaration files: run scripts/web.sh build and commit the result" 1
    fi
    echo "web.sh verify: GREEN (dist and declaration files equal a fresh build)"
    ;;
esac
