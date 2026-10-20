#!/usr/bin/env bash
# Deploy the dashboard: backup → sync → build → health check → automatic rollback on failure.
#
#   scripts/deploy.sh                          # to sit@10.20.31.40:/mnt/md127/pixi-solo-dashboard
#   scripts/deploy.sh user@host:/path          # another server
#   scripts/deploy.sh --local /path/to/dir     # rehearsal on this machine (no SSH)
#
# Options:  --no-db-backup   skip pg_dump (files are always backed up)
#           --delete         also remove files on the server that are no longer in the repo
#           --dry-run        show what would be synced, change nothing
#
# SSH: key-based login, or export SSHPASS=… (needs `sshpass`).
# The target keeps its own .env (never overwritten). Backups land in <target>/oldbackup/<YYMMDD>/
# (one folder per day, e.g. oldbackup/261020/): files_<HHMMSS>.tgz and pixi_test_<stamp>.dump.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="sit@10.20.31.40:/mnt/md127/pixi-solo-dashboard"
LOCAL=0
DB_BACKUP=1
DRY_RUN=0
DELETE=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) LOCAL=1; TARGET="$2"; shift 2 ;;
        --no-db-backup) DB_BACKUP=0; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --delete) DELETE=(--delete); shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) TARGET="$1"; shift ;;
    esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
BK="oldbackup/$(date +%y%m%d)"
FILES_TGZ="$BK/files_${STAMP#*_}.tgz"
DB_DUMP="$BK/pixi_test_$STAMP.dump"
log() { printf '\033[1;34m[deploy %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[deploy] %s\033[0m\n' "$*" >&2; exit 1; }

if [[ $LOCAL -eq 1 ]]; then
    DIR="$TARGET"
    run() { bash -c "$1"; }
    RSYNC_DEST="$DIR/"
else
    HOST="${TARGET%%:*}"
    DIR="${TARGET#*:}"
    SSH=(ssh -o StrictHostKeyChecking=accept-new)
    RSYNC_RSH="ssh -o StrictHostKeyChecking=accept-new"
    if [[ -n "${SSHPASS:-}" ]]; then
        command -v sshpass >/dev/null || die "SSHPASS is set but sshpass is not installed"
        SSH=(sshpass -e "${SSH[@]}")
        RSYNC_RSH="sshpass -e $RSYNC_RSH"
    fi
    run() { "${SSH[@]}" "$HOST" "$1"; }
    RSYNC_DEST="$HOST:$DIR/"
fi

# Files that belong to the server, never to the repo.
EXCLUDES=(--exclude '.env' --exclude 'oldbackup/' --exclude 'dbbackup/' --exclude '__pycache__/'
          --exclude '*.pyc' --exclude 'docker-compose.dev.yml')

log "source  $SRC"
log "target  $TARGET"
[[ -f "$SRC/api/app.py" && -f "$SRC/web/index.html" ]] || die "run from a solo-pixi-essential checkout"

if [[ $DRY_RUN -eq 1 ]]; then
    rsync -an "${DELETE[@]}" --itemize-changes "${EXCLUDES[@]}" ${RSYNC_RSH:+-e "$RSYNC_RSH"} "$SRC/" "$RSYNC_DEST"
    exit 0
fi

# ── 1. Backup ────────────────────────────────────────────────────────────────
run "mkdir -p '$DIR/$BK'"
log "backing up current files → $FILES_TGZ"
run "cd '$DIR' && tar czf '$FILES_TGZ' --exclude=oldbackup --exclude=dbbackup --exclude='__pycache__' . 2>/dev/null || true"
DB_CONTAINER="$(run "cd '$DIR' && docker compose ps -q postgres 2>/dev/null | head -1" || true)"
DB_NOTE="no database backup was taken"
if [[ $DB_BACKUP -eq 1 && -n "$DB_CONTAINER" ]]; then
    log "backing up the database → $DB_DUMP"
    run "docker exec $DB_CONTAINER pg_dump -U pixi -Fc pixi_test > '$DIR/$DB_DUMP'"
    run "test -s '$DIR/$DB_DUMP'" || die "database backup is empty — aborting before any change"
    DB_NOTE="database backup: $DB_DUMP"
else
    log "database backup skipped"
fi
PORT="$(run "grep -s '^PIXI_HTTP_PORT=' '$DIR/.env' | cut -d= -f2" || true)"
PORT="${PORT:-8001}"
version_of() { sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }
PREV_VERSION="$(run "curl -fsS --max-time 5 http://localhost:$PORT/health" 2>/dev/null | version_of || true)"
log "running version: ${PREV_VERSION:-unknown}"

# ── 2. Sync ──────────────────────────────────────────────────────────────────
log "syncing files"
rsync -a "${DELETE[@]}" "${EXCLUDES[@]}" ${RSYNC_RSH:+-e "$RSYNC_RSH"} "$SRC/" "$RSYNC_DEST"

# ── 3. Build + start ─────────────────────────────────────────────────────────
log "docker compose up -d --build"
run "cd '$DIR' && docker compose up -d --build 2>&1 | tail -5"

# ── 4. Health check ──────────────────────────────────────────────────────────
WANT="$(run "grep -s '^APP_VERSION=' '$DIR/.env' | cut -d= -f2" || true)"   # the server's .env wins over the code default
WANT="${WANT:-$(sed -n 's/^APP_VERSION = os.environ.get("APP_VERSION", "\(.*\)")/\1/p' "$SRC/api/config.py")}"
healthy=0
for i in $(seq 1 40); do
    body="$(run "curl -fsS --max-time 5 http://localhost:$PORT/health" 2>/dev/null || true)"
    if [[ "$body" == *'"status":"ok"'* && "$(printf '%s' "$body" | version_of)" == "${WANT}" ]]; then
        if run "curl -fsS --max-time 5 http://localhost:$PORT/ | grep -q 'js/main.js'"; then healthy=1; break; fi
    fi
    sleep 3
done

if [[ $healthy -eq 1 ]]; then
    log "healthy: $body"
    log "done — backup kept in $DIR/$BK"
    exit 0
fi

# ── 5. Rollback ──────────────────────────────────────────────────────────────
log "health check FAILED — restoring $FILES_TGZ"
run "cd '$DIR' && find . -mindepth 1 -maxdepth 1 ! -name oldbackup ! -name dbbackup ! -name .env -exec rm -rf {} + && tar xzf '$FILES_TGZ'"
run "cd '$DIR' && docker compose up -d --build 2>&1 | tail -5"
for i in $(seq 1 40); do
    if run "curl -fsS --max-time 5 http://localhost:$PORT/health" >/dev/null 2>&1; then
        die "deploy failed; previous version restored and healthy (database untouched; $DB_NOTE)"
    fi
    sleep 3
done
die "deploy failed AND the restored version is not healthy — check 'docker compose logs api' on the server"
