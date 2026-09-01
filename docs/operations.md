# Operations

## Error tracking

The API sends unhandled errors and sampled performance traces to Sentry when configured. It does
not enable default PII collection.

```dotenv
SENTRY_DSN=https://public-key@organization.ingest.sentry.io/project
SENTRY_RELEASE=testexchange-api@commit-sha
SENTRY_TRACES_SAMPLE_RATE=0.05
```

`SENTRY_DSN` is required when `APP_ENV` is `staging` or `production`. After deployment, trigger one
controlled test exception and confirm the event, environment, release, and request ID in Sentry.

## Uptime

The `API uptime` workflow checks the readiness endpoint every five minutes. Set this repository
variable after the API has a stable URL:

```text
UPTIME_URL=https://api.example.com/ready
```

Enable GitHub Actions failure notifications for this repository. A non-2xx response, timeout, or
response without `{"status":"ready"}` fails the workflow.

## Database backups

The `Database backup` workflow creates a checksum-protected PostgreSQL custom-format archive every
day and uploads it to a private S3 or S3-compatible bucket with server-side encryption. Configure:

- Repository variable `BACKUPS_ENABLED=true`.
- Repository variable `BACKUP_S3_URI`, such as `s3://testexchange-backups/database`.
- Repository variable `BACKUP_AWS_REGION`.
- Optional repository variable `BACKUP_S3_ENDPOINT_URL` for an S3-compatible provider.
- Repository secrets `BACKUP_DATABASE_URL`, `BACKUP_AWS_ACCESS_KEY_ID`, and
  `BACKUP_AWS_SECRET_ACCESS_KEY`.

Use a direct PostgreSQL connection for `BACKUP_DATABASE_URL`, a bucket that is private and separate
from the production provider, least-privilege write-only credentials, versioning, and a lifecycle
policy. This archive covers the application tables in the `public` schema. Provider-managed Auth
users and Storage objects require the hosted provider's own backup/export coverage.

Create an on-demand local archive with:

```bash
DATABASE_URL=postgresql://... BACKUP_DIR=./backups bash scripts/backup_postgres.sh
```

## Restoration

Always restore into a new, empty database. The restore script verifies the checksum and refuses a
destination whose `public` schema already contains tables.

```bash
RESTORE_DATABASE_URL=postgresql://.../fresh_database \
  bash scripts/restore_postgres.sh backups/testexchange-TIMESTAMP.dump
```

CI performs this backup and restoration against a second PostgreSQL database and compares the
Alembic revision, profile count, and table count. Repeat the same test against a temporary hosted
database after production credentials are configured, then record its date and archive key in the
release notes.
