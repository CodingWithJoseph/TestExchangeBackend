# Backend guide

This guide explains the backend in interview-friendly terms: what each layer owns, why each table
exists, and how one request moves through the system.

## Request lifecycle

1. The React frontend signs the user in through Supabase Auth.
2. Supabase returns a short-lived JWT access token.
3. The frontend sends that token in the API `Authorization` header.
4. `SupabaseTokenVerifier` downloads and caches the project's public signing keys, verifies the
   signature, issuer, audience, expiration, and subject, and creates a `CurrentUser` value.
5. The route validates the request body with a Pydantic schema.
6. A service applies ownership checks and workflow rules.
7. SQLAlchemy writes the business record, audit event, and any credit entry in one transaction.
8. If any operation fails, the request dependency rolls the entire transaction back.

Routes translate HTTP into function calls. Services contain business rules. Models describe the
database. Schemas define the public API contract. This separation keeps policy out of controllers
and makes the rules testable without a browser.

## Why each table exists

| Table | Responsibility |
|---|---|
| `profiles` | Public application identity for a Supabase user; authentication credentials remain in Supabase Auth. |
| `campaigns` | Public-safe recruitment brief, platform, reward, tester target, owner, and lifecycle. |
| `testing_contracts` | Private, versioned agreement defining expectations before a tester starts. |
| `contract_tasks` | Individually verifiable testing steps; separate rows make evidence coverage enforceable. |
| `assignments` | One tester's relationship to one campaign and the authoritative workflow state. |
| `evidence_submissions` | Immutable submission attempts; versions preserve correction history. |
| `evidence_items` | Files, links, notes, screenshots, and logs mapped to required contract tasks. |
| `messages` | Private communication scoped to the developer and tester sharing an assignment. |
| `reviews` | One recorded developer decision for each submission version. |
| `credit_accounts` | Current balance and the row that is locked to prevent concurrent overspending. |
| `credit_ledger_entries` | Append-only explanation for every credit movement and its idempotency key. |
| `disputes` | Escalation record that can later be resolved by a human moderator. |
| `audit_events` | Product-level history of important actions without exposing private content publicly. |

`credit_accounts` and `credit_ledger_entries` deliberately coexist. Summing the ledger could
calculate a balance, but locking a single account row is what prevents two simultaneous requests
from spending the same credits. The ledger remains the permanent financial explanation.

## Testing workflow

```text
applied
  -> accepted
  -> in_progress
  -> submitted
       -> changes_requested -> submitted (a new evidence version)
       -> rejected          -> dispute may be opened
       -> approved          -> tester reward is appended once
```

The database and service layer enforce these transitions. A client cannot skip directly from
`applied` to `submitted`, review somebody else's submission, or award the same assignment twice.

## Credit lifecycle

1. Creating a profile can add a configurable signup grant.
2. Publishing a campaign locks its contract and reserves
   `target_testers × reward_credits` from the owner's account.
3. Approval creates one idempotent reward entry for the tester.
4. A unique idempotency key prevents retries from paying twice.
5. ORM hooks and database triggers reject updates and deletes on ledger rows.

Future payment providers should add purchase entries only after a verified webhook. They should
never set a balance directly.

## Privacy boundary

Anonymous routes return only `CampaignRead`, which contains the public recruitment brief. Private
contracts, evidence storage keys, messages, reviews, disputes, and audit events require a verified
user who is either the campaign owner or the assigned tester.

## Production safeguards

The production-safeguards migration adds defense in depth around the service-level checks:

- Every application table has Row Level Security enabled and direct `anon`/`authenticated`
  table grants revoked. The FastAPI service uses the database connection as its trusted application
  boundary; the browser cannot read private workflow tables through PostgREST.
- The private `test-evidence` Supabase Storage bucket permits only assignment-scoped uploads from
  an assigned tester while the assignment is active. Reads are limited to that tester and the
  campaign owner. There are no direct update or delete policies, so submitted evidence is
  immutable through the browser. Supabase manages `storage.objects` itself and creates it with RLS
  enabled; the migration adds project policies without trying to alter that managed table.
- The API applies separate configurable read/write fixed-window limits. The in-process limiter
  protects a single API process; production deployments with multiple workers should add a shared
  gateway or reverse-proxy limit.
- Moderator access is an explicit server-side `MODERATOR_USER_IDS` UUID allowlist. Moderators can
  list disputes, inspect the private assignment case, claim one open dispute, and resolve it. A
  claim is exclusive and every claim or resolution writes an audit event. Resolving a dispute does
  not silently alter the credit ledger.

Evidence object keys must be shaped as `<assignment-id>/<file-name>`. This matches the first-folder
check in the Storage policies and prevents a tester from attaching an object belonging to another
assignment. The migration is safe to run against the local SQLite test database: it adds the
moderation columns there and skips PostgreSQL-only RLS and Storage statements. If the production
connection role cannot create policies on Supabase's managed Storage table, the migration rolls
back only that Storage savepoint, logs a warning, and leaves the rest of the migration applied.
Run `docs/supabase-storage-policies.sql` in the Supabase SQL Editor in that case.
