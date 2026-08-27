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
- Versioned testing contracts with required tasks and evidence rules
- Tester applications and assignments with explicit state transitions
- Versioned evidence submissions whose storage keys remain private
- Private assignment conversations
- Developer approval, rejection, and correction requests
- Credit accounts with row locking and an append-only ledger
- Disputes and participant-visible audit history
- Automatic private-beta starting credits, configurable with `SIGNUP_CREDIT_GRANT`

The automated quality checker is intentionally not implemented here. A later AI pre-check may
produce advice, but a developer or human moderator remains responsible for credit decisions.

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
4. Set `CORS_ORIGINS` to a JSON list containing the deployed frontend origin.
5. Run `alembic upgrade head` against the Supabase database.
6. Configure the frontend Supabase client and send its session access token to this API.

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
