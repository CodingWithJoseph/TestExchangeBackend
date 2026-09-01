"""Add beta workflow invariants.

Revision ID: c94f2b8a7d10
Revises: b61c90d42e8a
Create Date: 2026-08-28 14:00:00
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import ProgrammingError

revision: str = "c94f2b8a7d10"
down_revision: str | None = "b61c90d42e8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _storage_objects_exist() -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'storage' AND table_name = 'buckets'
                )
                """
            )
        )
    )


def _postgres_role_exists(role: str) -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
            {"role": role},
        )
    )


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.add_column(
            sa.Column("is_suspended", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("suspended_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("suspension_reason", sa.String(length=500)))

    op.create_table(
        "beta_program_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claimed_seats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_beta_program_singleton"),
        sa.CheckConstraint("claimed_seats >= 0", name="ck_beta_claimed_seats_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO beta_program_state (id, claimed_seats) SELECT 1, COUNT(*) FROM profiles"
        )
    )

    op.create_table(
        "waitlist_entries",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_waitlist_entries_email", "waitlist_entries", ["email"], unique=True)

    with op.batch_alter_table("testing_contracts") as batch_op:
        batch_op.add_column(
            sa.Column("minimum_duration_days", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("required_sessions", sa.Integer(), nullable=False, server_default="1")
        )

    op.create_table(
        "testing_sessions",
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", "session_date", name="uq_testing_session_day"),
    )
    op.create_index(
        "ix_testing_sessions_assignment_id", "testing_sessions", ["assignment_id"], unique=False
    )
    op.create_index(
        "ix_testing_sessions_assignment_created",
        "testing_sessions",
        ["assignment_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"], unique=False)
    op.create_index("ix_notifications_read_at", "notifications", ["read_at"], unique=False)
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_read",
        "notifications",
        ["user_id", "read_at"],
        unique=False,
    )

    with op.batch_alter_table("disputes") as batch_op:
        batch_op.add_column(
            sa.Column(
                "remedy",
                sa.Enum(
                    "none",
                    "award_tester",
                    name="dispute_remedy",
                    native_enum=False,
                ),
                nullable=True,
            )
        )

    if (
        op.get_bind().dialect.name == "postgresql"
        and _postgres_role_exists("anon")
        and _postgres_role_exists("authenticated")
    ):
        op.execute('ALTER TABLE public."testing_sessions" ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE public."testing_sessions" FROM anon, authenticated')
        op.execute('ALTER TABLE public."notifications" ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE public."notifications" FROM anon, authenticated')
        op.execute('ALTER TABLE public."beta_program_state" ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE public."beta_program_state" FROM anon, authenticated')
        op.execute('ALTER TABLE public."waitlist_entries" ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE public."waitlist_entries" FROM anon, authenticated')

    if op.get_bind().dialect.name == "postgresql" and _storage_objects_exist():
        # Executable archives and complex documents require a malware-scanning pipeline.
        # Keep private-beta uploads to formats the product can safely preview or inspect.
        try:
            with op.get_bind().begin_nested():
                op.execute(
                    """
                    CREATE OR REPLACE FUNCTION private.testexchange_can_delete_orphan_evidence(
                        folder text,
                        object_name text
                    )
                    RETURNS boolean
                    LANGUAGE plpgsql
                    SECURITY DEFINER
                    SET search_path = ''
                    AS $$
                    BEGIN
                        RETURN EXISTS (
                            SELECT 1
                            FROM public.assignments AS assignment
                            WHERE assignment.id = folder::uuid
                              AND assignment.tester_id = auth.uid()
                              AND assignment.status IN ('in_progress', 'changes_requested')
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM public.evidence_items AS evidence
                            WHERE evidence.storage_key = object_name
                        );
                    EXCEPTION WHEN invalid_text_representation THEN
                        RETURN false;
                    END;
                    $$
                    """
                )
                op.execute(
                    "REVOKE ALL ON FUNCTION "
                    "private.testexchange_can_delete_orphan_evidence(text, text) FROM PUBLIC"
                )
                op.execute(
                    "GRANT EXECUTE ON FUNCTION "
                    "private.testexchange_can_delete_orphan_evidence(text, text) TO authenticated"
                )
                op.execute(
                    "DROP POLICY IF EXISTS testexchange_evidence_orphan_cleanup ON storage.objects"
                )
                op.execute(
                    """
                    CREATE POLICY testexchange_evidence_orphan_cleanup
                    ON storage.objects FOR DELETE TO authenticated
                    USING (
                        bucket_id = 'test-evidence'
                        AND private.testexchange_can_delete_orphan_evidence(
                            (storage.foldername(name))[1],
                            name
                        )
                    )
                    """
                )
                op.execute(
                    """
                    UPDATE storage.buckets
                    SET allowed_mime_types = ARRAY[
                        'image/png', 'image/jpeg', 'image/webp', 'video/mp4', 'text/plain'
                    ]
                    WHERE id = 'test-evidence'
                    """
                )
        except ProgrammingError:
            logging.getLogger("alembic.runtime.migration").warning(
                "Storage MIME types were not updated by this database role. "
                "Run docs/supabase-storage-policies.sql in the Supabase SQL Editor."
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql" and _storage_objects_exist():
        try:
            with op.get_bind().begin_nested():
                op.execute(
                    "DROP POLICY IF EXISTS testexchange_evidence_orphan_cleanup ON storage.objects"
                )
                op.execute(
                    "DROP FUNCTION IF EXISTS "
                    "private.testexchange_can_delete_orphan_evidence(text, text)"
                )
                op.execute(
                    """
                    UPDATE storage.buckets
                    SET allowed_mime_types = ARRAY[
                        'image/png', 'image/jpeg', 'image/webp', 'video/mp4',
                        'text/plain', 'application/pdf', 'application/zip'
                    ]
                    WHERE id = 'test-evidence'
                    """
                )
        except ProgrammingError:
            logging.getLogger("alembic.runtime.migration").warning(
                "Storage MIME types were not restored by this database role."
            )

    with op.batch_alter_table("disputes") as batch_op:
        batch_op.drop_column("remedy")

    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_index("ix_notifications_read_at", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_testing_sessions_assignment_created", table_name="testing_sessions")
    op.drop_index("ix_testing_sessions_assignment_id", table_name="testing_sessions")
    op.drop_table("testing_sessions")

    with op.batch_alter_table("testing_contracts") as batch_op:
        batch_op.drop_column("required_sessions")
        batch_op.drop_column("minimum_duration_days")

    op.drop_index("ix_waitlist_entries_email", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")
    op.drop_table("beta_program_state")

    with op.batch_alter_table("profiles") as batch_op:
        batch_op.drop_column("suspension_reason")
        batch_op.drop_column("suspended_at")
        batch_op.drop_column("is_suspended")
