#!/usr/bin/env bash
# Prove a backup of scripts/backup-db.sh restores. Runs ON the box; never touches the live database:
#   scripts/restore-db.sh <relay-<stamp>-<sha>.dump.enc> [<key file>]     (key: ~/relay-backup-cfg/backup.key)
# Decrypts the file and pg_restores it into a throwaway container of the postgres image of deploy/compose.yml (no
# network, trust auth, data on a tmpfs so no volume is left on disk, removed at the end), then prints the row counts of a few key tables.
# A backup that came from Telegram: copy it to the box first (scp F phuongnam@192.168.18.227:), then run this.
# For a real restore of production follow "Restore runbook" in docs/operations.md with the decrypted dump.
set -euo pipefail

f=${1:?usage: scripts/restore-db.sh <file.dump.enc> [<key file>]}
key=${2:-$HOME/relay-backup-cfg/backup.key}
[ -r "$f" ] && [ -r "$key" ] || { echo "restore: $f or $key is not readable" >&2; exit 2; }
image=$(sed -n 's/^ *image: *\(postgres:[^ ]*\).*/\1/p' "$(dirname "$0")/../deploy/compose.yml" | head -1)
[ -n "$image" ] || { echo "restore: no postgres image in deploy/compose.yml" >&2; exit 2; }
name=relay-restore-check-$$

docker run -d --rm --name "$name" --network none --tmpfs /var/lib/postgresql/data -e POSTGRES_USER=relay -e POSTGRES_DB=relay \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$image" > /dev/null
trap 'docker rm -fv "$name" > /dev/null 2>&1 || true' EXIT
# -h 127.0.0.1: the image's first-start server listens on the socket only, so TCP answers once it is the real one
for _ in $(seq 60); do docker exec "$name" pg_isready -q -h 127.0.0.1 -U relay -d relay && break; sleep 1; done
docker exec "$name" pg_isready -q -h 127.0.0.1 -U relay -d relay || { echo "restore: postgres did not start" >&2; exit 1; }

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$key" -in "$f" \
  | docker exec -i "$name" pg_restore -U relay -d relay --no-owner --no-privileges --exit-on-error
docker exec "$name" psql -U relay -d relay -tAX -F ' ' -c "
  SELECT 'accounts', count(*) FROM accounts UNION ALL SELECT 'api_keys', count(*) FROM api_keys
  UNION ALL SELECT 'key_wallets', count(*) FROM key_wallets UNION ALL SELECT 'ledger', count(*) FROM ledger
  UNION ALL SELECT 'jobs', count(*) FROM jobs UNION ALL SELECT 'settings_kv', count(*) FROM settings_kv"
echo "restore: $(basename "$f") restored into a throwaway container: OK"
