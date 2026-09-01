#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"

backup_dir="${BACKUP_DIR:-./backups}"
backup_schema="${BACKUP_SCHEMA:-public}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive_path="${backup_dir%/}/testexchange-${timestamp}.dump"

umask 077
mkdir -p "$backup_dir"
pg_dump \
  --format=custom \
  --compress=6 \
  --schema="$backup_schema" \
  --no-owner \
  --no-privileges \
  --file="$archive_path" \
  "$DATABASE_URL"
(cd "$backup_dir" && sha256sum "$(basename "$archive_path")") > "${archive_path}.sha256"
printf '%s\n' "$archive_path"
