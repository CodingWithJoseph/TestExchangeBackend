"""Repair application access controls and enforce Storage suspension.

Revision ID: d82e41f6a903
Revises: c94f2b8a7d10
"""

import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import ProgrammingError

revision = "d82e41f6a903"
down_revision = "c94f2b8a7d10"
branch_labels = None
depends_on = None

APP_TABLES = (
    "profiles",
    "campaigns",
    "testing_contracts",
    "contract_tasks",
    "assignments",
    "evidence_submissions",
    "evidence_items",
    "messages",
    "reviews",
    "credit_accounts",
    "credit_ledger_entries",
    "disputes",
    "audit_events",
    "testing_sessions",
    "notifications",
    "beta_program_state",
    "waitlist_entries",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    roles = [
        role
        for role in ("anon", "authenticated")
        if bind.scalar(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
            {"role": role},
        )
    ]
    # Never swallow a failure to protect private application data.
    for table in APP_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        for role in roles:
            op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM {role}')
    if "authenticated" not in roles or not bind.scalar(
        sa.text("SELECT to_regclass('storage.objects') IS NOT NULL")
    ):
        return
    try:
        with bind.begin_nested():
            # An immutable SQL snapshot shipped alongside this migration.
            op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) != "42501":
            raise
        logging.getLogger("alembic.runtime.migration").warning(
            "Application table security is installed. Storage suspension policies require "
            "an owner role: run docs/supabase-storage-policies.sql in Supabase SQL Editor "
            "and verify direct Storage access before public launch."
        )


def downgrade() -> None:
    # Security repair is intentionally retained on rollback. No schema is added.
    pass
