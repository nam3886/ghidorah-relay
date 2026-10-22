#!/usr/bin/env bash
# Run a command on the box against an exact copy of this working tree.
#   scripts/box.sh -- <command...>              sync, check the fingerprint, run, stream the output, exit with its status
#   scripts/box.sh --detach NAME -- <command>   the same under nohup setsid: returns at once, survives the Mac sleeping
#   scripts/box.sh --wait NAME                  follow NAME's log until it ends, exit with its status
#   scripts/box.sh --log NAME                   print NAME's log so far
#   scripts/box.sh --status NAME                running | exit N
#   scripts/box.sh --status                     the folders on the box and which run holds each
#   scripts/box.sh --tree NAME ...              use the folder ~/relay-runs/NAME instead of the tree's own name
#   scripts/box.sh --clear TREE                 stop the run that holds folder TREE and free it (a run that was killed)
#   scripts/box.sh --sync-only                  sync and print the fingerprint
#   scripts/box.sh --fingerprint                print the fingerprint of the local tree (no ssh)
#   scripts/box.sh --print-remote NAME          the script that runs on the box for NAME (tests read it)
# The tree is the git top-level of the current directory; it lands in ~/relay-runs/<folder>/ on the box, with
# `rsync --delete`. The folder is the name of the tree's directory, or --tree NAME (letters, digits, - and _): two
# sessions that run from one working tree use two folders (the tester `--tree tester`, a developer its own name).
# A run holds its folder (flock on ~/relay-runs/busy/<folder>.lock, with the run's name beside it) from before the
# check of its fingerprint until it ends, detached or not; a sync into a held folder is refused (exit 76) and names the
# run. The lock dies with the processes that hold it, so a run that ends or is killed frees the folder; --clear stops
# the process groups of what is left holding it, each only after its pid and start time are checked again.
# ssh and rsync start with a clean environment (HOME, PATH and the agent socket only), so no variable of the Mac can be
# sent, whatever any ssh_config says. The folder name is checked, the directory name of the tree included. Never copied: .git, .venv, caches, deploy/*.env, `._*`. A tree that
# holds a real env file is refused; nothing is ever read from outside the tree (~/relay-secrets included). After the
# sync both sides compute one fingerprint (sorted file list with sha256 and the executable bit) and a run is refused
# when they differ. The environment of a run is the box's own plus what the command line sets: nothing is passed from
# the Mac (ssh sends no variable) and MUTATE_CMD is refused. Logs: ~/relay-runs/logs/NAME.{log,exit,sh,pid}.
# ~/relay-runs/gate.lock is a reader/writer lock held for the whole run (it dies with the run's processes): every run
# takes it shared, a whole-tree gate (check.sh --full with no MUTATE_ONLY and no test path) takes it exclusive. A gate
# is refused (exit 75) while any other run holds it, and any other run is refused (exit 75) while a gate holds it; runs
# in different folders share it freely. Other containers on the box are not ours: nothing here lists, stops or prunes them.
# CPU budget: only while relay's production stack runs on the box (a container of compose project `relay` is up;
# RELAY_GATE_CAP=on|off overrides, default auto; if docker or ssh fails during the check the run is capped, and says
# so). Then every run executes under `taskset -c $RELAY_GATE_CPUS nice -n 10 env` (default 0-11: 12 of the 32 cores), the
# gate's own containers (the browser-tier runner, the mutation queue containers, the check Postgres and MinIO) are
# pinned to the same cores (RELAY_GATE_CPUSET, CHECK_CPUSET) so the whole gate shares one 12-core set, and check.sh
# takes smaller job counts (RELAY_GATE_CAPPED=1). Before the deploy a run is uncapped, as it always was. The
# `box:` line shows capped=0|1.
# Live window: while ~/relay-deploy/live-window exists on the box, no run starts (exit 77), before the sync and again
# just before the command. The operator of a live window creates the file when it starts and deletes it when it ends.
# Exit codes: the command's own; 2 usage or refused tree; 3 fingerprint differs; 75 gate lock refused (see above); 76 folder held by
# another run; 77 a live window is open; 98 a detached run died without an exit status.
set -euo pipefail

BOX="${RELAY_BOX:-phuongnam@192.168.18.227}"
RUNS="relay-runs"                      # relative to the box user's home
LIVE_MARKER="relay-deploy/live-window" # relative to the box user's home; present while a live window is open
GATE_CPUS="${RELAY_GATE_CPUS:-0-11}"   # the host cores a run may use (a taskset list)
GATE_CAP="${RELAY_GATE_CAP:-auto}"
EXCLUDES=(.git .venv __pycache__ .pytest_cache .ruff_cache .mypy_cache .mutate.lock deploy/.env 'deploy/*.env' '._*' .DS_Store node_modules
          /.worktrees /plans /.claude /CLAUDE.md /.codegraph) # a leading / matches at the top of the tree only

die() { echo "box.sh: $1" >&2; exit "${2:-2}"; }
[[ "$GATE_CPUS" =~ ^[0-9]+([-,][0-9]+)*$ ]] || die "RELAY_GATE_CPUS is a taskset list such as 0-11 or 0,2,4: '$GATE_CPUS'"
[[ "$GATE_CAP" =~ ^(auto|on|off)$ ]] || die "RELAY_GATE_CAP is auto, on or off: '$GATE_CAP'"
# ssh sends the variables that match a SendEnv line (the system ssh_config has "SendEnv LANG LC_*", a user's
# ~/.ssh/config may have more, and an -o option on the command line is read before those files and cannot undo them),
# and it can only send variables that exist. So ssh and rsync start with a clean environment: HOME (keys,
# known_hosts), PATH and the agent socket, nothing else. `-o SendEnv=-*` is added as well.
SSH_OPTS=(-o BatchMode=yes -o "SendEnv=-*")
clean() { env -i HOME="$HOME" PATH="$PATH" ${SSH_AUTH_SOCK:+SSH_AUTH_SOCK="$SSH_AUTH_SOCK"} "$@"; }
ssh_box() { clean ssh "${SSH_OPTS[@]}" "$BOX" "$@"; }

# Shell that runs on the box. `alive FILE` is true while the pid in FILE ("PID STARTTIME") is running with that start
# time (field 22 of /proc/PID/stat): a pid that was reused by another process is not the run. BOX_PROC is for tests.
REMOTE_LIB='
P=${BOX_PROC:-/proc}
starttime() { sed "s/^.*) //" "$P/$1/stat" 2>/dev/null | awk "{print \$20}"; }
alive() { local p t; read -r p t < "$1" 2>/dev/null || return 1; [ -n "$p" ] && [ -n "$t" ] && [ "$(starttime "$p")" = "$t" ]; }
'
remote() {            # remote 'BODY' ARG...: runs BODY (with REMOTE_LIB) on the box with the arguments, which are names
  local body=$1; shift
  { printf '%s\n' "$REMOTE_LIB"; printf '%s\n' "$body"; } | ssh_box "bash -s -- $*"
}

BODY_FOLDERS='
cd "$HOME/relay-runs" 2>/dev/null || exit 0
mkdir -p busy
for d in */; do
  d=${d%/}
  case "$d" in logs | busy) continue ;; esac
  if flock -n "busy/$d.lock" true 2>/dev/null; then echo "$d: free"
  else echo "$d: busy, $(cat "busy/$d.info" 2>/dev/null)"; fi
done
'
# The holders are found by the lock itself (every process with the lock file open), not by what the run wrote down.
# A holder is signalled only if, just before the signal, its start time is still the one read when it was found and it
# still has the lock file open: a pid that was reused in between is somebody else's.
BODY_CLEAR='
tree=$1
mkdir -p "$HOME/relay-runs/busy"
lock="$HOME/relay-runs/busy/$tree.lock"
if flock -n "$lock" true 2>/dev/null; then echo "folder $tree is free"; exit 0; fi
echo "folder $tree is held: $(cat "$HOME/relay-runs/busy/$tree.info" 2>/dev/null)"
holds() { local fd; for fd in "$P/$1"/fd/*; do [ "$(readlink "$fd" 2>/dev/null)" = "$lock" ] && return 0; done; return 1; }
holders() { local d p; for d in "$P"/[0-9]*; do p=${d#"$P"/}; holds "$p" && echo "$p $(starttime "$p")"; done; }
mine=$(ps -o pgid= -p $$ | tr -d " ")
for sig in TERM KILL; do
  while read -r p st; do
    g=$(ps -o pgid= -p "$p" 2>/dev/null | tr -d " ")
    if [ "$(starttime "$p")" != "$st" ] || ! holds "$p"; then
      echo "skipping pid $p: it is no longer the process that held the lock"; continue
    fi
    if [ -n "$g" ] && [ "$g" -gt 1 ] && [ "$g" != "$mine" ]; then
      echo "sending $sig to process group $g (pid $p)"; kill -"$sig" -- "-$g" 2>/dev/null
    fi
  done < <(holders)
  sleep 2
  if flock -n "$lock" true 2>/dev/null; then echo "folder $tree is free"; exit 0; fi
done
echo "folder $tree is still held by: $(holders | cut -d" " -f1 | tr "\n" " ")" >&2
exit 1
'
BODY_STATUS='
cd "$HOME/relay-runs/logs" || exit 1
n=$1
if [ -f "$n.exit" ]; then echo "exit $(cat "$n.exit")"
elif alive "$n.pid"; then echo running
elif [ -f "$n.log" ]; then echo "ended without an exit status"; else echo unknown; fi
'
# the exit file is read before the log is fetched, so a run that ends in between still has its last lines shown
BODY_WAIT='
cd "$HOME/relay-runs/logs" || exit 1
n=$1
if [ -f "$n.exit" ]; then cat "$n.exit"; elif alive "$n.pid"; then echo running; else echo dead; fi
'
BODY_RUNNING='
cd "$HOME/relay-runs/logs" || exit 1
n=$1
[ -f "$n.pid" ] && ! [ -f "$n.exit" ] && alive "$n.pid"
'
BODY_START='
cd "$HOME/relay-runs/logs" || exit 1
n=$1
rm -f "$n.exit" "$n.log"
nohup setsid bash -l "$n.sh" > "$n.log" 2>&1 < /dev/null &
p=$!
echo "$p $(starttime "$p")" > "$n.pid"
'

# The same code runs on both sides. The skip rules mirror EXCLUDES: a difference between them shows as a mismatch.
FP_PY='
import fnmatch, hashlib, os, sys
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"}
SKIP_TOP = {".worktrees", "plans", ".claude", "CLAUDE.md", ".codegraph"}
root = sys.argv[1]
def skipped(rel, name):
    return (name in (".git", ".DS_Store", ".mutate.lock") or name.startswith("._")
            or fnmatch.fnmatch(rel, "deploy/*.env") or rel in SKIP_TOP)
def digest(path):
    if os.path.islink(path):
        return "link:" + os.readlink(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "%s:%d" % (h.hexdigest(), os.stat(path).st_mode & 0o111 != 0)
rows = []
for d, dirs, files in os.walk(root):
    dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not (d == root and x in SKIP_TOP))
    names = list(files) + [x for x in dirs if os.path.islink(os.path.join(d, x))]
    for name in names:
        path = os.path.join(d, name)
        rel = os.path.relpath(path, root)
        if not skipped(rel, name):
            rows.append("%s\0%s\n" % (rel, digest(path)))
rows.sort()
print(hashlib.sha256("".join(rows).encode()).hexdigest(), len(rows))
'

mode=run
name=""
tree_arg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --detach) mode=detach; name="${2:-}"; shift 2 || die "--detach needs a name" ;;
    --wait) mode=wait; name="${2:-}"; shift 2 || die "--wait needs a name" ;;
    --log) mode=log; name="${2:-}"; shift 2 || die "--log needs a name" ;;
    --status)
      mode=status; name=""
      case "${2:-}" in "" | --*) mode=folders; shift ;; *) name="$2"; shift 2 ;; esac ;;
    --tree) tree_arg="${2:-}"; shift 2 || die "--tree needs a folder name" ;;
    --clear) mode=clear; tree_arg="${2:-}"; shift 2 || die "--clear needs a folder name" ;;
    --sync-only) mode=sync; shift ;;
    --fingerprint) mode=fingerprint; shift ;;
    --print-remote) mode=print; name="${2:-}"; shift 2 || die "--print-remote needs a name" ;;
    --) shift; break ;;
    -h | --help) sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done
if [ "$mode" = print ]; then
  case "$name" in
    folders) body=$BODY_FOLDERS ;; clear) body=$BODY_CLEAR ;; status) body=$BODY_STATUS ;; wait) body=$BODY_WAIT ;;
    running) body=$BODY_RUNNING ;; start) body=$BODY_START ;; *) die "no remote script called '$name'" ;;
  esac
  printf '%s\n%s\n' "$REMOTE_LIB" "$body"; exit 0
fi
case "$mode" in detach | wait | log | status)
  [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "a run name is letters, digits, dot, dash and underscore: '$name'" ;;
esac
[ "$mode" != clear ] || [ -n "$tree_arg" ] || die "--clear needs a folder name"
FOLDER_RULE='^[A-Za-z0-9][A-Za-z0-9_-]*$'
case "$mode" in run | detach) [ $# -gt 0 ] || die "no command: scripts/box.sh [--detach NAME] -- <command...>" ;; esac
case "$mode" in run | detach)
  for word in "$@"; do
    case "$word" in *MUTATE_CMD*) die "MUTATE_CMD is never passed to a run on the box" ;; esac
    case "$word" in *WEB_CHECK_CMD*) die "WEB_CHECK_CMD is never passed to a run on the box" ;; esac
  done ;;
esac

# ---- runs that only read the logs on the box ----------------------------------------------------------------------
logfile() { echo "$RUNS/logs/$name.$1"; }

BUSY="$RUNS/busy"
if [ "$mode" = folders ]; then
  remote "$BODY_FOLDERS"; exit $?
fi
if [ "$mode" = clear ]; then
  [[ "$tree_arg" =~ $FOLDER_RULE ]] || die "a folder name is letters, digits, dash and underscore: '$tree_arg'"
  case "$tree_arg" in logs | busy) die "'$tree_arg' is not a folder name (the box keeps its logs and locks there)" ;; esac
  remote "$BODY_CLEAR" "$tree_arg"; exit $?
fi
if [ "$mode" = log ]; then
  ssh_box "cat $(logfile log)"; exit $?
fi
if [ "$mode" = status ]; then
  remote "$BODY_STATUS" "$name"; exit $?
fi
if [ "$mode" = wait ]; then
  offset=0
  while true; do
    if state=$(remote "$BODY_WAIT" "$name" 2>/dev/null); then
      chunk=$(mktemp)
      if ssh_box "tail -c +$((offset + 1)) $(logfile log)" > "$chunk" 2>/dev/null; then
        cat "$chunk"
        offset=$((offset + $(wc -c < "$chunk")))
        rm -f "$chunk"
        case "$state" in
          running) ;;
          dead) die "run '$name' ended without an exit status (killed?)" 98 ;;
          *) exit "$state" ;;
        esac
      else
        rm -f "$chunk"
      fi
    fi
    sleep 5            # an ssh that fails (the Mac woke up, the network moved) is simply tried again
  done
fi

# ---- runs that need the tree --------------------------------------------------------------------------------------
root=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git working tree"
tree="${tree_arg:-$(basename "$root")}"
runname="${name:-foreground-$(date +%s)}"

if [ "$mode" = fingerprint ]; then
  python3 -c "$FP_PY" "$root"; exit 0
fi
# the folder is the --tree name or the directory name of the tree: either goes into remote commands, so it is checked
[[ "$tree" =~ $FOLDER_RULE ]] || die "the folder name '$tree' (the directory name of the tree, or --tree) is letters, digits, dash and underscore: use --tree NAME"
case "$tree" in logs | busy) die "'$tree' is not a folder name: use --tree NAME" ;; esac

real_env=$(find "$root" \( -name .git -o -name .venv -o -name node_modules \) -prune -o -type f \
  \( -name .env -o -name '*.env' \) -print | sed "s#^$root/##")
[ -z "$real_env" ] || die "refusing to sync: the tree holds a real env file (only *.example may be in it): $(echo "$real_env" | tr '\n' ' ')"

# a whole-tree gate takes the lock: check.sh --full with no MUTATE_ONLY and no test path. The command may be wrapped
# (`bash -c 'set -o pipefail; ./scripts/check.sh --full 2>&1 | ts'`): every argument is read as shell words, with the
# shell's operators as words of their own, and what follows the first operator after check.sh does not count.
is_gate=0
if [ "$mode" = run ] || [ "$mode" = detach ]; then
  seen=0; others=0; stopped=0; full=0
  set -f
  for arg in "$@"; do
    arg=${arg//2>&1/ | }; arg=${arg//;/ ; }; arg=${arg//|/ | }; arg=${arg//&/ & }; arg=${arg//>/ > }; arg=${arg//</ < }
    for word in $arg; do
      [ "$stopped" = 1 ] && continue
      case "$word" in
        ';' | '|' | '&' | '>' | '<') [ "$seen" = 1 ] && stopped=1 ;;
        MUTATE_ONLY=*) others=1 ;;
        */check.sh | check.sh) seen=1 ;;
        --full) [ "$seen" = 1 ] && full=1 ;;
        --local) ;;
        *) [ "$seen" = 1 ] && others=1 ;;
      esac
    done
  done
  set +f
  if [ "$seen" = 1 ] && [ "$others" = 0 ] && [ "$full" = 1 ]; then is_gate=1; fi
fi

if [ "$mode" = run ] || [ "$mode" = detach ]; then
  if ! ssh_box "test ! -e $LIVE_MARKER"; then
    die "a live window is open on the box (~/$LIVE_MARKER exists): no run starts until its operator removes it" 77
  fi
fi
capped=0
if [ "$mode" = run ] || [ "$mode" = detach ]; then
  case "$GATE_CAP" in
    on) capped=1 ;;
    auto)                      # 10 = a container of project `relay` is up, 11 = none; anything else is an error: fail closed
      found=0
      ssh_box 'ids=$(docker ps -q --filter label=com.docker.compose.project=relay) || exit 2; [ -n "$ids" ] && exit 10; exit 11' || found=$?
      case "$found" in
        10) capped=1 ;;
        11) ;;
        *) capped=1; echo "box: could not tell whether the production stack runs (status $found): running capped" >&2 ;;
      esac ;;
  esac
fi
if [ "$mode" = detach ]; then
  if remote "$BODY_RUNNING" "$name"; then
    die "a run named '$name' is still running on the box"
  fi
fi
# gate.lock is a reader/writer lock: every run holds it shared, a whole-tree gate holds it exclusive. So a gate waits
# for no one and is refused while any other run is on the box, and every other run is refused while a gate runs.
lock_flag=-s
[ "$is_gate" = 0 ] || lock_flag=-x
if { [ "$mode" = run ] || [ "$mode" = detach ]; } && ! ssh_box "mkdir -p $RUNS && flock $lock_flag -n $RUNS/gate.lock true"; then
  if [ "$is_gate" = 1 ]; then
    die "a whole-tree gate needs the box to itself and another run holds ~/$RUNS/gate.lock: wait for it" 75
  fi
  die "a whole-tree gate is running on the box (~/$RUNS/gate.lock): no other run starts until it ends" 75
fi

if ! ssh_box "mkdir -p $RUNS/$tree $RUNS/logs $BUSY && flock -n $BUSY/$tree.lock true"; then
  held=$(ssh_box "cat $BUSY/$tree.info 2>/dev/null" || true)
  die "folder $tree on the box is held by a running run (${held:-unknown}): use --tree NAME for another folder, or --clear $tree if that run is dead" 76
fi
excludes=()
for pattern in "${EXCLUDES[@]}"; do excludes+=("--exclude=$pattern"); done
# the receiving rsync runs under the folder's lock: a run that took the folder after the check above makes it fail
clean rsync -a --delete -e "ssh ${SSH_OPTS[*]}" \
  --rsync-path="flock -n $BUSY/$tree.lock rsync" "${excludes[@]}" "$root/" "$BOX:$RUNS/$tree/" \
  || die "the sync into folder $tree failed (it may have been taken by a run just now): try again or use --tree NAME" 76

mac=$(python3 -c "$FP_PY" "$root")
there=$(printf '%s' "$FP_PY" | ssh_box "python3 - $RUNS/$tree")
[ "$mac" = "$there" ] || die "the fingerprint differs after the sync (Mac $mac, box $there): nothing was run" 3
echo "box: $BOX tree=$tree fingerprint=${mac:0:16} files=${mac##* } capped=$capped" >&2
[ "$mode" != sync ] || exit 0

# ---- the script that runs on the box --------------------------------------------------------------------------------
quoted=$(printf '%q ' "$@")
body() {
  echo 'cd "$HOME/'"$RUNS/$tree"'" || exit 127'
  echo 'mkdir -p "$HOME/'"$BUSY"'"'
  echo 'exec 8>"$HOME/'"$BUSY/$tree"'.lock"'
  echo 'flock -n 8 || { echo "box.sh: folder '"$tree"' is held by a running run: $(cat "$HOME/'"$BUSY/$tree"'.info")" >&2; exit 76; }'
  echo 'echo "run='"$runname"' pgid=$(ps -o pgid= -p $$ | tr -d " ") since=$(date -u +%FT%TZ)" > "$HOME/'"$BUSY/$tree"'.info"'
  echo 'now=$(python3 - "$HOME/'"$RUNS/$tree"'" <<'"'FPEOF'"
  printf '%s\n' "$FP_PY"
  echo 'FPEOF'
  echo ')'
  echo '[ "$now" = "'"$mac"'" ] || { echo "box.sh: the folder changed between the sync and the start (box: $now)" >&2; exit 3; }'
  echo '[ ! -e "$HOME/'"$LIVE_MARKER"'" ] || { echo "box.sh: a live window is open on the box (~/'"$LIVE_MARKER"' exists): nothing was run" >&2; exit 77; }'
  echo 'export PATH="$HOME/.local/bin:$PATH" RELAY_ON_BOX=1'
  if [ "$capped" = 1 ]; then
    echo 'export RELAY_GATE_CAPPED=1 RELAY_GATE_CPUSET='"$GATE_CPUS"' CHECK_CPUSET='"$GATE_CPUS"
  fi
  echo 'unset MUTATE_CMD WEB_CHECK_CMD'
  echo 'exec 9>"$HOME/'"$RUNS"'/gate.lock"'
  if [ "$is_gate" = 1 ]; then
    echo 'flock -x -n 9 || { echo "box.sh: a whole-tree gate needs the box to itself and another run holds gate.lock" >&2; exit 75; }'
  else
    echo 'flock -s -n 9 || { echo "box.sh: a whole-tree gate is running on the box: no other run starts until it ends" >&2; exit 75; }'
  fi
  if [ "$capped" = 1 ]; then
    echo "taskset -c $GATE_CPUS nice -n 10 env $quoted"   # env: a leading NAME=value stays an assignment
  else
    echo "$quoted"
  fi
}

if [ "$mode" = run ]; then
  { echo '('; body; echo ')'; echo 'exit $?'; } | ssh_box "bash -ls"
  exit $?
fi

script=$(mktemp)
{
  echo "echo 'box.sh: run $name, tree $tree, fingerprint ${mac:0:16}, started '\$(date -u +%FT%TZ)"
  echo '('
  body
  echo ')'
  echo 'rc=$?'
  echo "echo \"\$rc\" > \"\$HOME/$(logfile exit).tmp\" && mv \"\$HOME/$(logfile exit).tmp\" \"\$HOME/$(logfile exit)\""
} > "$script"
ssh_box "cat > $(logfile sh)" < "$script"
rm -f "$script"
remote "$BODY_START" "$name"
echo "box: detached run '$name' started; follow with: scripts/box.sh --wait $name" >&2
