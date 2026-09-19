# Operations

## Website beta deployment

Deploy the API before the matching website. Set `APP_ENV=production`, the intended PostgreSQL
`DATABASE_URL`, `SUPABASE_URL`, exact HTTPS website `CORS_ORIGINS`, real `MODERATOR_USER_IDS`,
and the Sentry settings below. Keep rate limits enabled. Set beta capacity intentionally and
assign someone to check the moderation queue and support mailbox during launch.

Configure `SUPABASE_SERVICE_ROLE_KEY` in the backend secret store for moderator evidence access.
It must belong to the same Supabase project as `SUPABASE_URL`. Never copy it to the frontend,
logs, screenshots, or GitHub variables. Missing credentials cause the attachment endpoint to
return 503; API startup alone does not verify this integration.

Moderators request a 60-second attachment URL through
`POST /api/v1/moderation/disputes/{dispute_id}/evidence/{evidence_id}/url`. The API validates
the moderator and the case/assignment relationship, and audits IDs without recording the URL.
Signed links are bearer links and can be used until expiration. Do not cache or log response
bodies from this endpoint. Human moderation handles both explicit rejections and submissions
whose contractual review window has expired; no automatic credit award occurs on timeout.

## Mandatory database and Storage verification

1. Take a recoverable backup and record the currently deployed API revision.
2. Run `alembic upgrade head`. The release repair is `d82e41f6a903`. It enables RLS and revokes
   browser-role access for all 17 application tables even on databases that previously stamped
   the flawed safeguard migration. Application-table failures abort the migration.
3. A Storage ownership warning does **not** mean Storage is ready. Run
   `docs/supabase-storage-policies.sql` in the intended Supabase project's SQL Editor using its
   table-owner privileges. The Storage changes use a separate savepoint so a permissions
   failure cannot undo application-table protections.
4. `docs/repair-application-security.sql` is the manual equivalent for application tables. Use
   it if immediate repair is necessary and after a restore that excludes ACLs. Downgrading the
   release intentionally retains protections. An Alembic head stamp is not a security audit.
5. Verify all listed application tables have `relrowsecurity = true` and neither `anon` nor
   `authenticated` has SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, or TRIGGER privileges.
   Include inherited/PUBLIC grants in this check using `has_table_privilege`, not just direct
   grant rows. The expected table list is in the repair SQL.
6. Inspect `pg_policies` for `storage.objects` and remove any obsolete permissive policy that
   also grants access to `test-evidence`. PostgreSQL permissive policies combine with OR.
   Confirm the bucket is private and its 50 MB / PNG, JPEG, WebP, MP4, TXT limits are applied.
7. With real staging sessions, verify active assignment participants can upload/read as allowed,
   unrelated users and anonymous users cannot read, submitted evidence cannot be changed or
   deleted, and suspended participants cannot make new read/upload/delete requests. Previously
   issued signed URLs remain valid until expiration; suspension cannot revoke those tokens.
8. Test a moderator's case attachment link, expiration, and `moderation.evidence_opened` audit
   record using the configured backend credentials. Local SQL tests do not replace this hosted
   Supabase Storage check.

The CI PostgreSQL suite verifies seat and campaign-capacity races, single reward issuance,
owner-review versus escalation serialization, and actual RLS behavior during migration failure.

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

Do not mark backups ready until a scheduled production archive succeeds and a restore into an
isolated destination succeeds. Record coverage separately for application tables, Supabase Auth
identities, Storage metadata, and the evidence object bytes. A public-schema dump covers only the
first item. Obtain and test the provider/export process for the others; do not assume a database
backup contains uploaded files. Restrict test restores containing real data and delete them using
the operator's approved retention process. Reapply/verify database and Storage policies after a
restore because the archive omits ownership and grants.

## Launch support and recovery

- Verify `/ready` over the public API URL and enable the uptime workflow variable above. Verify
  that a deliberate staging failure actually reaches the operator's alert destination.
- Run the frontend runbook's complete owner/tester/moderator flow on the hosted candidate.
  Keep the test evidence and release revisions in a private release record.
- For abuse, suspend through the moderator workflow, record the reason, and check both API and
  direct Storage denial. Avoid putting private evidence or signed links in public issue reports.
- For account deletion requests, verify the requester using the existing account/contact channel,
  identify Auth, profile, evidence, active assignments, and any necessary dispute records, and
  document the disposition before executing deletion. There is no automated deletion workflow
  in this release; the published support process must have an operator who can fulfill requests.
- If onboarding or moderation fails, pause new beta registration with the moderator control and
  preserve active work. Roll back application artifacts if needed while retaining the security
  repair. Never restore an old database over production as an informal rollback.

The release record must contain the configured website/API URLs, support mailbox, moderator,
successful smoke-test date, monitored Sentry release, uptime run, backup archive and restore-test
date, and Auth/Storage recovery coverage. Unconfigured or skipped workflow runs are not passing
operational checks.
