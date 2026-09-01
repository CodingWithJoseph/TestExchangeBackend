#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${ADMIN_DATABASE_URL:?ADMIN_DATABASE_URL is required}"
: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required}"
: "${RESTORE_DATABASE_NAME:?RESTORE_DATABASE_NAME is required}"

if [[ ! "$RESTORE_DATABASE_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || (( ${#RESTORE_DATABASE_NAME} > 63 )); then
  echo "RESTORE_DATABASE_NAME must be a valid PostgreSQL identifier" >&2
  exit 2
fi

restore_database_created=false
cleanup_restore_database() {
  if [[ "$restore_database_created" == true ]]; then
    psql "$ADMIN_DATABASE_URL" \
      --set=ON_ERROR_STOP=1 \
      --command="DROP DATABASE \"$RESTORE_DATABASE_NAME\" WITH (FORCE);" >/dev/null || \
      echo "Warning: could not remove temporary database $RESTORE_DATABASE_NAME" >&2
  fi
}
trap cleanup_restore_database EXIT

existing_database="$(psql "$ADMIN_DATABASE_URL" --tuples-only --no-align --command="SELECT 1 FROM pg_database WHERE datname = '$RESTORE_DATABASE_NAME';")"
if [[ -n "$existing_database" ]]; then
  echo "Refusing to replace existing database: $RESTORE_DATABASE_NAME" >&2
  exit 3
fi

psql "$ADMIN_DATABASE_URL" \
  --set=ON_ERROR_STOP=1 \
  --command="CREATE DATABASE \"$RESTORE_DATABASE_NAME\";"
restore_database_created=true

archive_path="$(bash scripts/backup_postgres.sh)"
bash scripts/restore_postgres.sh "$archive_path"

source_revision="$(psql "$DATABASE_URL" --tuples-only --no-align --command="SELECT version_num FROM alembic_version;")"
restored_revision="$(psql "$RESTORE_DATABASE_URL" --tuples-only --no-align --command="SELECT version_num FROM alembic_version;")"
source_profiles="$(psql "$DATABASE_URL" --tuples-only --no-align --command="SELECT COUNT(*) FROM profiles;")"
restored_profiles="$(psql "$RESTORE_DATABASE_URL" --tuples-only --no-align --command="SELECT COUNT(*) FROM profiles;")"
source_tables="$(psql "$DATABASE_URL" --tuples-only --no-align --command="SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public';")"
restored_tables="$(psql "$RESTORE_DATABASE_URL" --tuples-only --no-align --command="SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public';")"

test "$source_revision" = "$restored_revision"
test "$source_profiles" = "$restored_profiles"
test "$source_tables" = "$restored_tables"
printf 'Backup restoration verified: revision=%s profiles=%s tables=%s\n' \
  "$restored_revision" "$restored_profiles" "$restored_tables"
