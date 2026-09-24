#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_DIR:=./backups}"

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="$BACKUP_DIR/vitae-${timestamp}.dump"

case "$DATABASE_URL" in
  postgresql://*|postgres://*)
    pg_dump "$DATABASE_URL" --format=custom --no-owner --file="$output"
    ;;
  *)
    echo "backup-db requires a PostgreSQL DATABASE_URL" >&2
    exit 1
    ;;
esac

echo "Created $output"
