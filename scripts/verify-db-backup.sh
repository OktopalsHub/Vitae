#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"

case "$DATABASE_URL" in
  postgresql://*|postgres://*)
    pg_restore --list "$BACKUP_FILE" >/dev/null
    ;;
  *)
    echo "verify-db-backup requires a PostgreSQL DATABASE_URL" >&2
    exit 1
    ;;
esac

echo "Backup archive is readable: $BACKUP_FILE"
