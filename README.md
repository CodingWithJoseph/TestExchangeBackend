# TestExchange Backend

The Python API for TestExchange, a community where software builders complete real tests,
earn credits for approved work, and spend those credits on their own testing campaigns.

This repository owns private workflow data and decisions. Public community pages may show a
sanitized campaign brief, but testing contracts, build access, evidence, conversations, reviews,
and disputes remain behind authentication.

## Stack

- FastAPI for the HTTP API and generated OpenAPI documentation
- Supabase Auth access tokens, verified locally through the project's JWKS endpoint
- SQLAlchemy 2 for database access
- PostgreSQL in production through the Supabase connection string
- Alembic for repeatable schema migrations
- Pytest and Ruff for automated verification

The API never receives or stores user passwords. The frontend signs users in with Supabase and
sends the resulting access token as `Authorization: Bearer <token>`.

## What is implemented

- Profiles tied to the UUID in the Supabase JWT `sub` claim
- Public software-testing campaigns across Android, iOS, web, desktop, API, and other platforms
- Versioned testing contracts with required tasks, private access instructions, and evidence rules
- Tester applications and assignments with explicit state transitions
- Versioned evidence submissions whose storage keys remain private
- Private assignment conversations
- Developer approval, rejection, and correction requests
- Participant-only submission and review history endpoints for restoring workspaces after refresh
- Credit accounts with row locking and an append-only ledger
- Atomic campaign launch, recruitment pause/resume/close, capacity locking, and automatic completion
- Owner application decline and tester withdrawal without interrupting accepted testers
- Disputes and participant-visible audit history
- Moderator-only dispute queue, participant suspension/restoration, waitlist review, and resolution audit trail
- Assignment-scoped evidence storage keys with private Supabase Storage policy support
- Per-process read/write API rate limits with configurable windows and response headers
- A concurrency-safe public-beta cap and anonymous waitlist, configurable with `PUBLIC_BETA_MAX_USERS`
- Automatic public-beta starting credits, configurable with `SIGNUP_CREDIT_GRANT`
- Private, deterministic submission quality pre-checks that explain evidence gaps to reviewers

The quality pre-check is advisory and intentionally deterministic in this phase. The
`GET /api/v1/submissions/{submission_id}/quality-check` endpoint reports required-task coverage,
evidence content, summary specificity, and concrete observations. It never approves or rejects a
submission and never transfers credits; the campaign owner or a human moderator remains
responsible for that decision. A future AI provider can add more advice behind the same boundary.

Production safeguards are applied by the latest Alembic migration. App tables are denied direct
Supabase Data API access for `anon` and `authenticated`; the FastAPI service remains the
authorized application boundary. The private `test-evidence` Storage bucket allows an assigned
tester to upload only while their assignment is active and lets only the tester or campaign owner
read objects. Evidence keys must use `<assignment-id>/<file-name>`.

Some Supabase connection strings use a role that cannot modify the managed `storage.objects`
table. In that case the migration keeps the database changes and logs a warning; run
`docs/supabase-storage-policies.sql` once in the Supabase SQL Editor to install the Storage
policies.

Set `MODERATOR_USER_IDS` to a comma-separated list of Supabase Auth user UUIDs before running the
moderator API. This is a server-side allowlist; client-controlled profile or JWT metadata does not
grant moderator access. A moderator can uphold a rejection or approve the disputed submission and
issue its tester reward exactly once through an idempotent ledger entry.

Public registration is enabled with `PUBLIC_BETA_ENABLED=true` and capped by
`PUBLIC_BETA_MAX_USERS` (200 by default). A seat is claimed only when the authenticated user creates
their TestExchange profile. The singleton beta-state row is locked in that transaction, so
simultaneous signups cannot exceed the cap. When full or paused, the anonymous waitlist endpoint
accepts a normalized email address. Supabase email confirmation should remain enabled.

## Run locally

Create a virtual environment and install the project:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

The default `.env.example` uses SQLite so the schema can be explored without installing a
database. Apply the migration and start the API:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API documentation. Protected endpoints
require a real Supabase access token.

For a local PostgreSQL database, start `compose.yaml` and set:

```dotenv
DATABASE_URL=postgresql://testexchange:testexchange@localhost:54322/testexchange
```

## Connect Supabase

1. Create a Supabase project and use asymmetric JWT signing keys.
2. Copy the project URL into `SUPABASE_URL`.
3. Copy the PostgreSQL connection string into `DATABASE_URL`; keep `sslmode=require` for hosted
   Supabase connections.
4. Set `CORS_ORIGINS` to a JSON list containing every frontend origin you use. `localhost`
   and `127.0.0.1` are different browser origins during local development.
5. Run `alembic upgrade head` against the Supabase database.
6. Configure the frontend Supabase client and send its session access token to this API.
7. Keep public email signup enabled for the public beta, require email confirmation, and set the
   frontend Auth redirect URLs for every approved origin.

After applying the production-safeguards migration, create no additional public Storage policies
for `test-evidence` unless they preserve the assignment-folder checks. The built-in rate limiter
is a useful baseline for one API process; a multi-process deployment should also enforce shared
limits at its reverse proxy or API gateway.

The backend only needs public signing keys to verify users. Do not put a Supabase secret or
service-role key in the frontend.

## Verify changes

```powershell
ruff check .
ruff format --check .
pytest --cov=app --cov-report=term-missing
alembic upgrade head
alembic downgrade base
```

See [docs/backend-guide.md](docs/backend-guide.md) for the request lifecycle, database-table
rationale, credit design, and workflow state machine.
