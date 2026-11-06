#!/usr/bin/env bash
# Daily encrypted backup of the relay database, sent off the box to the owner's Telegram. Runs ON the box.
#   scripts/backup-db.sh
# Install (on the box, once; 20:00 UTC = 03:00 GMT+7). The folder first: cron's `>>` fails before the script runs
# when the folder of the log is missing.
#   (umask 077 && mkdir -p ~/relay-backups/daily)
#   crontab -e  ->  0 20 * * * $HOME/relay-deploy/scripts/backup-db.sh >> $HOME/relay-backups/daily/backup.log 2>&1
# What it does, in order (any failure exits non-zero and, when it can, says so in the Telegram chat):
#   1. Takes ~/relay-backups/daily/backup.lock (flock); a second run meanwhile is refused with 75. Then removes the
#      .tmp-* folders a killed run left (they may hold a plain dump).
#   2. `pg_dump -Fc` of the database relay through the postgres service of ~/relay-deploy (compose project relay),
#      into a 0700 temp folder, removed at exit; the dump must start with PGDMP and hold at least 64 KB (the live one
#      is about 0.5 MB; an empty database dumps far smaller, so less means the data is missing).
#   3. Encrypts it: openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt, passphrase file ~/relay-backup-cfg/backup.key
#      (the owner keeps a copy on the Mac). Result: ~/relay-backups/daily/relay-<UTC stamp>-<GIT_SHA>.dump.enc, 0600.
#   4. Keeps the newest 14 files of ~/relay-backups/daily.
#   5. sendDocument to TELEGRAM_CHAT_ID with TELEGRAM_BOT_TOKEN from ~/relay-backup-cfg/telegram.env (0600, two plain
#      KEY=value lines: no quotes, no export; the file is read, never sourced); the caption gives the time in GMT+7,
#      the sha, the size and the sha256 of the encrypted file. The token goes to curl on stdin (`-K -`), never on a
#      command line, into a child's environment or into the output. A file above 49 MB is not sent (the Bot API takes
#      at most 50 MB): the run fails and says so in the chat. A failed send keeps the local file.
# Restore (proves a backup, never touches the live database): scripts/restore-db.sh <file.dump.enc>
# Decrypt by hand (Mac or box): openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass file:<key> -in F -out F.dump
# Exit codes: 0 sent; 1 a step failed; 75 another backup run holds the lock.
set -euo pipefail

D=$HOME/relay-deploy
CFG=$HOME/relay-backup-cfg
OUT=$HOME/relay-backups/daily
KEEP=14
TG_ENV=$CFG/telegram.env
KEY=$CFG/backup.key
MIN_DUMP=$((64 * 1024))
MAX_SEND=$((49 * 1024 * 1024))
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID            # never exported, even when the caller's environment had them
TELEGRAM_BOT_TOKEN= TELEGRAM_CHAT_ID=

redact() { local s=$1; [ -z "$TELEGRAM_BOT_TOKEN" ] || s=${s//"$TELEGRAM_BOT_TOKEN"/<token>}; printf '%s' "$s"; }

tg() {   # tg METHOD CURL_ARGS...: one Bot API call; the URL (with the token) and chat_id go in on stdin
  local method=$1 resp; shift
  resp=$(printf 'url = "https://api.telegram.org/bot%s/%s"\nform-string = "chat_id=%s"\n' \
           "$TELEGRAM_BOT_TOKEN" "$method" "$TELEGRAM_CHAT_ID" \
         | curl -sS --max-time 300 --retry 3 -K - "$@" 2>&1) && [[ "$resp" == *'"ok":true'* ]] && return 0
  echo "backup: $method: $(redact "$resp")" >&2
  return 1
}

fail() {   # fail MESSAGE: tell the chat when the config is readable, then exit 1
  echo "backup: $1" >&2
  [ -z "$TELEGRAM_BOT_TOKEN" ] || tg sendMessage --form-string "text=relay DB backup FAILED: $1" > /dev/null || true
  exit 1
}

umask 077
mkdir -p "$OUT"
exec 9> "$OUT/backup.lock"
flock -n 9 || { echo "backup: another backup run holds $OUT/backup.lock" >&2; exit 75; }
rm -rf "$OUT"/.tmp-*

[ -r "$TG_ENV" ] || fail "$TG_ENV is not readable"
cfg() { sed -n "s/^$1=//p" "$TG_ENV" | tail -1; }
TELEGRAM_BOT_TOKEN=$(cfg TELEGRAM_BOT_TOKEN)
TELEGRAM_CHAT_ID=$(cfg TELEGRAM_CHAT_ID)
[ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ] || { TELEGRAM_BOT_TOKEN=; fail "$TG_ENV lacks TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"; }
[ -s "$KEY" ] || fail "the key file $KEY is missing or empty"

sha=$(sed -n 's/^GIT_SHA=//p' "$D/deploy/.env" 2>/dev/null | tail -1)
sha=${sha:-unknown}
now=$(date -u +%s)
stamp=$(date -u -d "@$now" +%Y%m%dT%H%M%SZ)
file=$OUT/relay-$stamp-$sha.dump.enc
tmp=$(mktemp -d "$OUT/.tmp-XXXXXX")
trap 'rm -rf "$tmp"' EXIT

(cd "$D" && docker compose -p relay -f deploy/compose.yml exec -T postgres \
   sh -c 'pg_dump -U "$POSTGRES_USER" -d relay -Fc' < /dev/null > "$tmp/relay.dump") || fail "pg_dump failed"
[ "$(head -c 5 "$tmp/relay.dump")" = PGDMP ] || fail "pg_dump wrote no custom-format dump"
[ "$(stat -c %s "$tmp/relay.dump")" -ge $MIN_DUMP ] || fail "the dump is under 64 KB: the data is missing"
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$KEY" -in "$tmp/relay.dump" -out "$tmp/out.enc" \
  || fail "encryption failed"
mv "$tmp/out.enc" "$file"
echo "backup: wrote $file"

old=("$OUT"/relay-*.dump.enc)                          # name order is time order
[ ${#old[@]} -le $KEEP ] || rm -f "${old[@]:0:${#old[@]}-KEEP}"

size=$(stat -c %s "$file")
[ "$size" -le $MAX_SEND ] || fail "$((size / 1048576)) MB is above the 49 MB the Telegram send allows; kept at $file"
hash=$(sha256sum "$file" | cut -d' ' -f1)
caption="relay DB $(TZ=UTC-7 date -d "@$now" '+%Y-%m-%d %H:%M') GMT+7
sha $sha, $((size / 1024)) KB
sha256 $hash"
tg sendDocument -F "document=@$file" --form-string "caption=$caption" > /dev/null \
  || fail "the Telegram send failed; the backup is kept at $file"
echo "backup: sent $(basename "$file") ($size bytes)"
