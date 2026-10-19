#!/usr/bin/env bash
# Daily database backup with 14-day retention. Run on the server, e.g. from cron:
#   0 2 * * *  /mnt/md127/pixi-solo-dashboard/scripts/backup_db.sh >> /mnt/md127/pixi-solo-dashboard/dbbackup/backup.log 2>&1
# Restore:  docker exec -i <db container> pg_restore -U pixi -d pixi_test --clean --if-exists < dbbackup/pixi_test_<stamp>.dump
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP_DAYS="${KEEP_DAYS:-14}"
cd "$DIR"
mkdir -p dbbackup
CONTAINER="$(docker compose ps -q postgres | head -1)"
[[ -n "$CONTAINER" ]] || { echo "$(date -Is) postgres container not running" >&2; exit 1; }
OUT="dbbackup/pixi_test_$(date +%Y%m%d_%H%M%S).dump"
docker exec "$CONTAINER" pg_dump -U pixi -Fc pixi_test > "$OUT"
[[ -s "$OUT" ]] || { echo "$(date -Is) backup is empty: $OUT" >&2; rm -f "$OUT"; exit 1; }
find dbbackup -name 'pixi_test_*.dump' -mtime +"$KEEP_DAYS" -print -delete
echo "$(date -Is) wrote $OUT ($(du -h "$OUT" | cut -f1))"
