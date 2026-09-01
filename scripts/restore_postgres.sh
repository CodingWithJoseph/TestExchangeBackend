#!/usr/bin/env bash
set -euo pipefail

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required and must name a fresh database}"

if [[ $# -ne 1 ]]; then
  echo "Usage: RESTORE_DATABASE_URL=... bash scripts/restore_postgres.sh <archive.dump>" >&2
  exit 2
fi

archive_path="$1"
if [[ ! -f "$archive_path" ]]; then
  echo "Backup archive not found: $archive_path" >&2
  exit 2
fi

checksum_path="${archive_path}.sha256"
if [[ -f "$checksum_path" ]]; then
  checksum_dir="$(dirname "$checksum_path")"
  checksum_file="$(basename "$checksum_path")"
  (cd "$checksum_dir" && sha256sum --check "$checksum_file")
fi

existing_tables="$(psql "$RESTORE_DATABASE_URL" --tuples-only --no-align --command="SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public';")"
if [[ "$existing_tables" != "0" ]]; then
  echo "Refusing to restore into a non-empty public schema." >&2
  exit 3
fi

# PostgreSQL creates an empty public schema in every new database, while the
# schema-scoped archive also contains CREATE SCHEMA public. Omitting CASCADE makes
# this fail safely if the destination contains any non-table objects.
psql "$RESTORE_DATABASE_URL" --set=ON_ERROR_STOP=1 --command="DROP SCHEMA public;"

pg_restore \
  --exit-on-error \
  --no-owner \
  --no-privileges \
  --dbname="$RESTORE_DATABASE_URL" \
  "$archive_path"

psql "$RESTORE_DATABASE_URL" --set=ON_ERROR_STOP=1 --command="SELECT version_num FROM alembic_version;"
