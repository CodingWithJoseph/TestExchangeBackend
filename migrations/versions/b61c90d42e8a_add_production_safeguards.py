"""Add production access and moderation safeguards.

Revision ID: b61c90d42e8a
Revises: 7b3c2d1e9f04
Create Date: 2026-08-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b61c90d42e8a"
down_revision: str | None = "7b3c2d1e9f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
)


def _postgres_relation_exists(schema: str, table: str) -> bool:
    bind = op.get_bind()
    return bool(
        bind.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = :schema AND table_name = :table
                )
                """
            ),
            {"schema": schema, "table": table},
        )
    )


def _postgres_role_exists(role: str) -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
            {"role": role},
        )
    )


def _install_supabase_safeguards() -> None:
    if not (_postgres_role_exists("anon") and _postgres_role_exists("authenticated")):
        return

    for table in APP_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM anon, authenticated')

    if not _postgres_relation_exists("storage", "objects"):
        return

    op.execute("ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE SCHEMA IF NOT EXISTS private")
    op.execute("REVOKE ALL ON SCHEMA private FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA private TO authenticated")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.testexchange_can_read_evidence(folder text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = ''
        AS $$
        BEGIN
            RETURN EXISTS (
                SELECT 1
                FROM public.assignments AS assignment
                JOIN public.campaigns AS campaign ON campaign.id = assignment.campaign_id
                WHERE assignment.id = folder::uuid
                  AND (
                    assignment.tester_id = auth.uid()
                    OR campaign.owner_id = auth.uid()
                  )
            );
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN false;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.testexchange_can_upload_evidence(folder text)
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
                  AND assignment.status IN (
                    'accepted',
                    'in_progress',
                    'changes_requested'
                  )
            );
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN false;
        END;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION private.testexchange_can_read_evidence(text) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION private.testexchange_can_upload_evidence(text) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.testexchange_can_read_evidence(text) TO authenticated"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.testexchange_can_upload_evidence(text) TO authenticated"
    )
    op.execute(
        """
        INSERT INTO storage.buckets (
            id, name, public, file_size_limit, allowed_mime_types
        ) VALUES (
            'test-evidence',
            'test-evidence',
            false,
            52428800,
            ARRAY[
                'image/png', 'image/jpeg', 'image/webp', 'video/mp4',
                'text/plain', 'application/pdf', 'application/zip'
            ]
        )
        ON CONFLICT (id) DO UPDATE SET
            public = EXCLUDED.public,
            file_size_limit = EXCLUDED.file_size_limit,
            allowed_mime_types = EXCLUDED.allowed_mime_types
        """
    )
    op.execute("DROP POLICY IF EXISTS testexchange_evidence_read ON storage.objects")
    op.execute("DROP POLICY IF EXISTS testexchange_evidence_upload ON storage.objects")
    op.execute(
        """
        CREATE POLICY testexchange_evidence_read
        ON storage.objects FOR SELECT TO authenticated
        USING (
            bucket_id = 'test-evidence'
            AND private.testexchange_can_read_evidence((storage.foldername(name))[1])
        )
        """
    )
    op.execute(
        """
        CREATE POLICY testexchange_evidence_upload
        ON storage.objects FOR INSERT TO authenticated
        WITH CHECK (
            bucket_id = 'test-evidence'
            AND private.testexchange_can_upload_evidence((storage.foldername(name))[1])
        )
        """
    )


def upgrade() -> None:
    with op.batch_alter_table("disputes") as batch_op:
        batch_op.add_column(sa.Column("assigned_to", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_disputes_assigned_to_profiles",
            "profiles",
            ["assigned_to"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_disputes_assigned_to", ["assigned_to"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        _install_supabase_safeguards()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        if _postgres_relation_exists("storage", "objects"):
            op.execute("DROP POLICY IF EXISTS testexchange_evidence_upload ON storage.objects")
            op.execute("DROP POLICY IF EXISTS testexchange_evidence_read ON storage.objects")
            op.execute(
                """
                DELETE FROM storage.buckets AS bucket
                WHERE bucket.id = 'test-evidence'
                  AND NOT EXISTS (
                    SELECT 1 FROM storage.objects AS object
                    WHERE object.bucket_id = bucket.id
                  )
                """
            )
        op.execute("DROP FUNCTION IF EXISTS private.testexchange_can_upload_evidence(text)")
        op.execute("DROP FUNCTION IF EXISTS private.testexchange_can_read_evidence(text)")
        if _postgres_role_exists("anon") and _postgres_role_exists("authenticated"):
            for table in APP_TABLES:
                op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
                op.execute(f'GRANT ALL ON TABLE public."{table}" TO anon, authenticated')

    with op.batch_alter_table("disputes") as batch_op:
        batch_op.drop_index("ix_disputes_assigned_to")
        batch_op.drop_constraint("fk_disputes_assigned_to_profiles", type_="foreignkey")
        batch_op.drop_column("assigned_at")
        batch_op.drop_column("assigned_to")
