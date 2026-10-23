#!/usr/bin/env bash
# Deploy a pushed commit to the box, or roll back to the tree kept from the deploy before. Run from the Mac.
#   scripts/deploy.sh <sha> [--workers dola,meta] [--allow-migrate]
#   scripts/rollback.sh [<sha>] [--workers dola,meta] [--allow-schema-mismatch]
# Deploy, in order (a step that fails stops the run and says what to do):
#   1. <sha> must be a commit of this repository and an ancestor of origin/main (pushed; `git fetch` first).
#   2. On the box: ~/relay-runs/gate.lock taken exclusive and held until the script ends (so no box.sh run, gate,
#      second deploy or rollback starts meanwhile: they are refused with 75), no live window
#      (~/relay-deploy/live-window), no busy ~/relay-runs folder, the deployed sha (GIT_SHA in
#      ~/relay-deploy/deploy/.env) readable and different from <sha>.
#   3. `git archive <sha>` into ~/relay-deploy-new; the env files are copied from the live tree with `cp -p` (never
#      printed) and GIT_SHA is set in the new deploy/.env.
#   4. If the migrations differ from the deployed tree's, --allow-migrate is required.
#   5. Build migrate, api and worker-<p> of each --workers provider in the new tree; then `migrate` when needed.
#   6. Rotate: ~/relay-deploy -> ~/relay-deploy-<old>, ~/relay-deploy-new -> ~/relay-deploy. Only the newest previous
#      tree is kept: older relay-deploy-<sha> trees go, with their relay-* images unless a running container uses one.
#   7. `up -d --no-deps api`, then /healthz 200 on 127.0.0.1:8100 and the admin bundle served.
#   8. First the claims state of every --workers provider: on when its stored <p>.claims_enabled (the default of its
#      spec when none is stored) is true, or when ~/relay-claims-snapshot holds <p>=true (a deploy or rollback that
#      stopped between draining <p> and resuming it; said with a WARNING). An unreadable state stops the run before
#      any worker is drained. Then per worker: <p>=true into the snapshot when <p> is on, `relay provider drain <p>`
#      twice (the second prints "(no change)"), wait until no job of <p> is submitting, `up -d --no-deps worker-<p>`,
#      a fresh heartbeat of the new process (and Chrome's version for a browser worker), `relay provider resume <p>`
#      only when <p> was on (a provider that was off stays off), then <p> leaves the snapshot (gone once empty).
# Rollback takes the same lock and makes the same box checks as step 2, swaps ~/relay-deploy with ~/relay-deploy-<sha>
# (the only kept tree when no sha is given), then step 7 and, with --workers, step 8. It refuses when that tree or its images are missing, and when the migrations of the two
# trees differ unless --allow-schema-mismatch (the database stays at the newer schema: the older code must cope with
# it). It never runs migrate and never touches a volume or the database.
# Exit codes: 0 done; 1 a step failed; 2 usage or refused (commit, tree, images); 3 migrations need --allow-migrate
# or --allow-schema-mismatch; 75 the box is busy (a run or a gate); 77 a live window is open.
set -euo pipefail

BOX="${RELAY_BOX:-phuongnam@192.168.18.227}"
die() { echo "$NAME: $1" >&2; exit "${2:-2}"; }
NAME=deploy
mode=deploy
[ "${1:-}" != --rollback ] || { mode=rollback; NAME=rollback; shift; }
SSH_OPTS=(-o BatchMode=yes -o "SendEnv=-*" -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
clean() { env -i HOME="$HOME" PATH="$PATH" ${SSH_AUTH_SOCK:+SSH_AUTH_SOCK="$SSH_AUTH_SOCK"} "$@"; }
ssh_box() { clean ssh "${SSH_OPTS[@]}" "$BOX" "$@"; }

# ---- the shell that runs on the box: one function per step, called as `remote <function> <args>` ---------------------
REMOTE_LIB='
set -euo pipefail
D=$HOME/relay-deploy
N=$HOME/relay-deploy-new
MIG=packages/relay-core/src/relay_core/db/migrations
IMAGES="relay-api relay-migrate relay-worker-dola relay-worker-meta relay-worker-vibes relay-worker-flow"
fail() { echo "box: $1" >&2; exit "${2:-1}"; }
compose() { local t=$1; shift; (cd "$t" && docker compose -p relay -f deploy/compose.yml "$@"); }
sha_of() { sed -n "s/^GIT_SHA=//p" "$1/deploy/.env" 2>/dev/null | tail -1; }
sql() {   # read-only, through the postgres container
  compose "$D" exec -T postgres sh -c "PGOPTIONS=\"-c default_transaction_read_only=on\" psql -U \"\$POSTGRES_USER\" -d relay -tAX -c \"\$1\"" sh "$1"
}

preflight() {   # under the held gate.lock
  [ ! -e "$D/live-window" ] || fail "a live window is open (~/relay-deploy/live-window exists): no deploy until its operator removes it" 77
  if [ -d "$HOME/relay-runs" ]; then
    cd "$HOME/relay-runs"
    for l in busy/*.lock; do
      [ -e "$l" ] || continue
      if ! flock -n "$l" true; then fail "folder $(basename "$l" .lock) holds a run (scripts/box.sh --status): wait for it" 75; fi
    done
  fi
  local old; old=$(sha_of "$D")
  [ -n "$old" ] || fail "no GIT_SHA in ~/relay-deploy/deploy/.env: the deployed commit is unknown" 2
  [ ! -e "$HOME/relay-deploy-$old" ] || fail "~/relay-deploy-$old already exists: move it away first" 2
  echo "deployed=$old"
}

stage() {   # stage SHA PROVIDER...: the env files of the live tree into the new one, GIT_SHA set
  local sha=$1 f p; shift
  for f in .env api.env migrate.env; do [ -f "$D/deploy/$f" ] || fail "~/relay-deploy/deploy/$f is missing" 2; done
  for p in "$@"; do [ -f "$D/deploy/worker-$p.env" ] || fail "~/relay-deploy/deploy/worker-$p.env is missing" 2; done
  for f in "$D"/deploy/.env "$D"/deploy/*.env; do cp -p "$f" "$N/deploy/"; done
  if grep -q "^GIT_SHA=" "$N/deploy/.env"; then sed -i "s/^GIT_SHA=.*/GIT_SHA=$sha/" "$N/deploy/.env"
  else echo "GIT_SHA=$sha" >> "$N/deploy/.env"; fi
  [ "$(sha_of "$N")" = "$sha" ] || fail "GIT_SHA could not be set in the new deploy/.env"
  [ ! -e "$D/live-window" ] || fail "a live window was opened meanwhile" 77
  if diff -rq "$D/$MIG" "$N/$MIG" > /dev/null 2>&1; then echo "migrations=same"; else echo "migrations=differ"; fi
}

build() {   # build PROVIDER...
  local args=(--profile app) svcs=(migrate api) p
  for p in "$@"; do args+=(--profile "$p"); svcs+=("worker-$p"); done
  compose "$N" "${args[@]}" build "${svcs[@]}"
}

migrate() { compose "$N" --profile app run --rm migrate; }

rotate() {   # rotate OLD: the live tree becomes relay-deploy-OLD, the new one goes live; older trees are removed
  local old=$1 d s img used cur
  [ ! -e "$D/live-window" ] || fail "a live window was opened meanwhile" 77
  used=$(docker ps --format "{{.Image}}") || fail "docker ps failed"
  mv -T "$D" "$HOME/relay-deploy-$old"                 # -T: never into an existing directory
  mv -T "$N" "$D"
  echo "rotated: ~/relay-deploy-$old is the previous tree"
  cur=$(sha_of "$D")
  for d in "$HOME"/relay-deploy-*; do
    s=${d##*/relay-deploy-}
    [[ "$s" =~ ^[0-9a-f]{7,40}$ ]] && [ "$s" != "$old" ] || continue
    for img in $IMAGES; do
      [ "$s" != "$cur" ] || break                      # the images of the commit that just went live stay
      docker image inspect "$img:$s" > /dev/null 2>&1 || continue
      if grep -qxF "$img:$s" <<< "$used"; then echo "kept $img:$s (a running container uses it)"
      else docker image rm "$img:$s" > /dev/null && echo "removed $img:$s"; fi
    done
    rm -rf "$d" && echo "removed ~/relay-deploy-$s"
  done
}

rollback_check() {   # rollback_check TARGET|- PROVIDER...
  local t=$1 cur; shift
  cur=$(sha_of "$D")                                     # preflight checked it, and that ~/relay-deploy-$cur is free
  if [ "$t" = - ]; then
    local found=() d s
    for d in "$HOME"/relay-deploy-*; do s=${d##*/relay-deploy-}; [[ "$s" =~ ^[0-9a-f]{7,40}$ ]] && found+=("$s"); done
    [ ${#found[@]} = 1 ] || fail "${#found[@]} previous trees (${found[*]:-none}): name the one to go back to" 2
    t=${found[0]}
  fi
  [ -d "$HOME/relay-deploy-$t" ] && [ "$(sha_of "$HOME/relay-deploy-$t")" = "$t" ] \
    || fail "no tree ~/relay-deploy-$t with GIT_SHA=$t: nothing to roll back to" 2
  local img
  for img in relay-api "${@/#/relay-worker-}"; do
    docker image inspect "$img:$t" > /dev/null 2>&1 || fail "image $img:$t is missing: rebuild it or deploy instead" 2
  done
  if diff -rq "$D/$MIG" "$HOME/relay-deploy-$t/$MIG" > /dev/null 2>&1; then echo "schema=same"; else echo "schema=differs"; fi
  echo "current=$cur"
  echo "target=$t"
}

rollback_rotate() {   # rollback_rotate CURRENT TARGET
  mv -T "$D" "$HOME/relay-deploy-$1"
  mv -T "$HOME/relay-deploy-$2" "$D"
  echo "rotated: ~/relay-deploy is $2, ~/relay-deploy-$1 is kept"
}

api() {
  compose "$D" --profile app up -d --no-deps api
  local i code=""
  for i in $(seq 30); do
    code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8100/healthz || true)
    [ "$code" = 200 ] && break
    sleep 2
  done
  [ "$code" = 200 ] || fail "/healthz answers ${code:-nothing}, not 200"
  local js
  js=$(curl -fsS http://127.0.0.1:8100/manage | grep -o "/ui/app/assets/index-[A-Za-z0-9_-]*\.js" | head -1 || true)
  [ -n "$js" ] || fail "/manage names no admin bundle"
  curl -fsS -o /dev/null "http://127.0.0.1:8100$js" || fail "the admin bundle $js is not served"
  echo "api: healthz 200, bundle $js served"
}

drain() {   # drain P: twice, the second must find it drained already
  local p=$1 first second
  first=$(compose "$D" --profile app exec -T api relay provider drain "$p")
  second=$(compose "$D" --profile app exec -T api relay provider drain "$p")
  echo "$first / $second"
  [[ "$second" == *"(no change)"* ]] || fail "the second drain of $p did not print (no change): the drain was not stored"
}

idle() {   # idle P: until no job of P is submitting (15 minutes at most)
  local p=$1 n i
  for i in $(seq 90); do
    n=$(sql "SELECT count(*) FROM jobs WHERE provider = '"'"'$p'"'"' AND status = '"'"'submitting'"'"'")
    [ "$n" = 0 ] && { echo "$p: no job submitting"; return 0; }
    echo "$p: $n job(s) submitting, waiting"
    sleep 10
  done
  fail "$p still has jobs submitting after 15 minutes"
}

worker() {   # worker P: restart it, then a heartbeat of the new process and Chrome
  local p=$1 t0 i ok=0
  t0=$(sql "SELECT now()")
  compose "$D" --profile "$p" up -d --no-deps "worker-$p"
  for i in $(seq 24); do
    ok=$(sql "SELECT count(*) FROM worker_heartbeats WHERE provider = '"'"'$p'"'"' AND started_at >= '"'"'$t0'"'"' AND at > now() - interval '"'"'60 seconds'"'"'")
    [ "$ok" != 0 ] && break
    sleep 5
  done
  [ "$ok" != 0 ] || fail "worker-$p wrote no fresh heartbeat within 2 minutes of its start"
  echo "worker-$p: fresh heartbeat"
  case "$p" in dola | meta) echo "worker-$p: $(compose "$D" --profile "$p" exec -T "worker-$p" /opt/google/chrome/chrome --version)" ;; esac
}

resume() { compose "$D" --profile app exec -T api relay provider resume "$1"; }

SNAP=$HOME/relay-claims-snapshot   # "P=true" per provider drained while it claimed and not resumed yet
claims() {   # claims P=DEFAULT...: "state P on|off" per provider: on when its stored P.claims_enabled (DEFAULT when
             # none is stored) is true, or when the snapshot holds P=true (a run stopped between its drain and resume)
  local a p v raw snap
  for a in "$@"; do
    p=${a%%=*}
    raw=$(sql "SELECT value FROM settings_kv WHERE key = '"'"'$p.claims_enabled'"'"'")
    v=${raw:-${a#*=}}
    [ "$v" = true ] || [ "$v" = false ] \
      || fail "$p.claims_enabled reads [$raw], not true or false: its claims state is unknown"
    snap=$(sed -n "s/^$p=//p" "$SNAP" 2> /dev/null | tail -1 || true)
    if [ "$snap" = true ] && [ "$v" = false ]; then   # a snapshot line only ever turns a provider back on
      v=true
      echo "box: WARNING: $p.claims_enabled is false, but ~/relay-claims-snapshot holds [$p=$snap], left by a deploy \
or rollback that stopped between draining $p and resuming it: $p will be resumed" >&2
    fi
    echo "$p: claims_enabled=$v"
    if [ "$v" = true ]; then echo "state $p on"; else echo "state $p off"; fi
  done
}

snap_write() {   # snap_write FILE: FILE replaces the snapshot durably; an empty FILE removes it
  if [ -s "$1" ]; then sync "$1"; mv -f "$1" "$SNAP"; else rm -f "$1" "$SNAP"; fi
  sync "$HOME"                                         # the rename or the removal itself
}

mark() {   # mark P: right before P (which claims) is drained: the snapshot keeps P=true until P is resumed
  { grep -sv "^$1=" "$SNAP" || true; echo "$1=true"; } > "$SNAP.tmp"
  snap_write "$SNAP.tmp"
}

restore() {   # restore P on|off: P claims again when it did before, then P leaves the snapshot
  if [ "$2" = on ]; then resume "$1"; fi
  [ -e "$SNAP" ] || return 0
  grep -v "^$1=" "$SNAP" > "$SNAP.tmp" || true
  snap_write "$SNAP.tmp"
}
'
remote() { { printf '%s\n' "$REMOTE_LIB"; printf '%q ' "$@"; echo; } | ssh_box "bash -s"; }

hint="nothing was changed on the box"

# The lock: one ssh session takes ~/relay-runs/gate.lock exclusive and keeps it (its shell holds the descriptor) until
# its stdin closes, which happens when this script ends, however it ends. box.sh runs take gate.lock shared, a gate
# takes it exclusive: neither starts while a deploy or a rollback holds it, and a second one is refused here. Each
# line sent to it is answered "held", so check_lock proves before every step that the lock is still there.
LOCK_CMD='mkdir -p relay-runs && exec 9>> relay-runs/gate.lock && if flock -x -n 9; then echo locked; while read -r _; do echo held; done; exit 0; fi; echo busy'
lock_dir=""
release() {   # bounded: a holder that has not ended 10 s after its stdin closed is killed
  [ -n "$lock_dir" ] || return 0
  exec 7>&- 8<&-
  local i
  for i in $(seq 50); do kill -0 "$hold_pid" 2> /dev/null || break; sleep 0.2; done
  pkill -P "$hold_pid" 2> /dev/null || true
  kill "$hold_pid" 2> /dev/null || true
  wait "$hold_pid" 2> /dev/null || true
  rm -rf "$lock_dir"
}
check_lock() {   # check_lock STEP: the holder is alive and its box side still answers, or the run stops here
  local ans=""
  if kill -0 "$hold_pid" 2> /dev/null && { echo ping >&7; } 2> /dev/null; then read -r -t 30 ans <&8 || true; fi
  [ "$ans" = held ] || { echo "$NAME: lost the lock (~/relay-runs/gate.lock) before '$1': stopped. $hint" >&2; exit 1; }
}
take_lock() {
  lock_dir=$(mktemp -d)
  mkfifo "$lock_dir/in" "$lock_dir/out"
  ssh_box "$LOCK_CMD" < "$lock_dir/in" > "$lock_dir/out" &
  hold_pid=$!
  trap release EXIT
  trap '' PIPE                                     # a holder that died fails check_lock instead of killing this script
  exec 7> "$lock_dir/in" 8< "$lock_dir/out"
  local state=""
  read -r -t 60 state <&8 || true
  [ "$state" = locked ] || die "another deploy, rollback, gate or box.sh run holds ~/relay-runs/gate.lock: wait for it \
(scripts/box.sh --status)" 75
}
step() {    # step FUNCTION ARGS...: runs it on the box under the lock; a failure stops here and says where things stand
  check_lock "$1"
  remote "$@" || { local rc=$?; echo "$NAME: stopped at '$1' (exit $rc): $hint" >&2; exit "$rc"; }
}

workers=()
sha=""
allow=0
while [ $# -gt 0 ]; do
  case "$1" in
    --workers)
      [ -n "${2:-}" ] || die "--workers needs a list such as dola,meta"
      IFS=, read -ra workers <<< "$2"; shift 2 ;;
    --allow-migrate) [ "$mode" = deploy ] || die "unknown argument: $1"; allow=1; shift ;;
    --allow-schema-mismatch) [ "$mode" = rollback ] || die "unknown argument: $1"; allow=1; shift ;;
    -h | --help) sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown argument: $1 (see --help)" ;;
    *) [ -z "$sha" ] || die "one commit only"; sha=$1; shift ;;
  esac
done
for p in ${workers[@]+"${workers[@]}"}; do
  case "$p" in dola | meta | vibes | flow) ;; *) die "unknown provider '$p' (dola, meta, vibes, flow)" ;; esac
done
if [ -n "$sha" ]; then [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || die "a commit is 7 to 40 hex digits: '$sha'"; fi

default_claims() {   # the default <p>.claims_enabled of each provider's spec (test_deploy_sh compares them)
  case "$1" in dola) echo true ;; meta | vibes | flow) echo false ;; esac
}

restart_workers() {   # a provider claims after its restart exactly when it claimed before: off stays off
  local p out was claims args=()
  [ -n "${workers[*]+x}" ] || return 0
  hint="the api is live; no worker was drained"
  for p in "${workers[@]}"; do args+=("$p=$(default_claims "$p")"); done
  out=$(step claims "${args[@]}")
  sed "/^state /d; s/^/$NAME: /" <<< "$out"
  claims=" $(sed -n 's/^state \([a-z]*\) \([a-z]*\)$/\1=\2/p' <<< "$out" | tr '\n' ' ')"
  for p in ${workers[@]+"${workers[@]}"}; do
    was=off
    [[ "$claims " != *" $p=on "* ]] || was=on
    if [ "$was" = on ]; then
      hint="the api is live; worker-$p may be drained: fix it, then \`relay provider resume $p\` (or scripts/rollback.sh, or \
deploy again: ~/relay-claims-snapshot keeps that $p claimed)"
    else
      hint="the api is live; worker-$p may be down; $p was off before and stays drained: fix it (or scripts/rollback.sh)"
    fi
    if [ "$was" = on ]; then step mark "$p"; fi
    step drain "$p"
    step idle "$p"
    step worker "$p"
    step restore "$p" "$was"
    if [ "$was" = on ]; then
      echo "$NAME: $p claims again (it claimed before)"
    else
      echo "$NAME: $p left drained (it was off before)"
    fi
    restarted+=" worker-$p"
  done
}
restarted="api"

if [ "$mode" = rollback ]; then
  take_lock
  step preflight > /dev/null
  out=$(step rollback_check "${sha:--}" ${workers[@]+"${workers[@]}"})
  echo "$out"
  cur=$(sed -n 's/^current=//p' <<< "$out")
  target=$(sed -n 's/^target=//p' <<< "$out")
  if grep -qx "schema=differs" <<< "$out" && [ "$allow" = 0 ]; then
    die "the migrations of $cur and $target differ: the database already has $cur's schema and a rollback never \
undoes a migration, so $target's code would run against a schema it does not know. Roll back only if that code copes \
with it, with --allow-schema-mismatch" 3
  fi
  step rollback_rotate "$cur" "$target"
  hint="~/relay-deploy is now $target: fix the api, or scripts/deploy.sh again"
  step api
  restart_workers
  echo "$NAME: $cur -> $target; restarted: $restarted"
  exit 0
fi

[ -n "$sha" ] || die "usage: scripts/deploy.sh <sha> [--workers dola,meta] [--allow-migrate]"
full=$(git rev-parse --verify --quiet "$sha^{commit}") || die "commit $sha is unknown in this repository (git fetch?)"
git merge-base --is-ancestor "$full" origin/main \
  || die "$sha is not on origin/main: push it (and git fetch) first; only pushed commits are deployed"
new=$(git rev-parse --short=7 "$full")

take_lock
old=$(step preflight | sed -n 's/^deployed=//p')
[ -n "$old" ] || die "the deployed commit could not be read" 1
[ "$old" != "$new" ] || die "$new is already deployed"
echo "$NAME: $old -> $new"

check_lock copy
git archive --format=tar "$full" | ssh_box "rm -rf relay-deploy-new && mkdir relay-deploy-new && tar -xf - -C relay-deploy-new" \
  || die "the tree could not be copied to ~/relay-deploy-new: $hint" 1
hint="production is unchanged (the staged tree is ~/relay-deploy-new)"
staged=$(step stage "$new" ${workers[@]+"${workers[@]}"})
migrate=0
if grep -qx "migrations=differ" <<< "$staged"; then
  [ "$allow" = 1 ] || die "the migrations of $new differ from $old's: migrate must run before the api starts. \
Deploy again with --allow-migrate when the migration may run now" 3
  migrate=1
fi
step build ${workers[@]+"${workers[@]}"}
if [ "$migrate" = 1 ]; then
  hint="the old api still runs; the migration stopped part way: read its output before anything else"
  step migrate
fi
step rotate "$old"
hint="~/relay-deploy is $new: fix it, or go back with scripts/rollback.sh"
step api
restart_workers
echo "$NAME: $old -> $new; migrate: $([ "$migrate" = 1 ] && echo ran || echo none); restarted: $restarted"
