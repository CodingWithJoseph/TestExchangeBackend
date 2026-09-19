"""Run only against the disposable, migrated PostgreSQL database used by CI."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.db.session import normalize_database_url
from app.models import Assignment, Campaign, Profile
from app.models.enums import AssignmentStatus
from tests.test_security_migrations import load_migration
from tests.test_workflow import campaign_payload

database_url = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not database_url, reason="Requires disposable PostgreSQL test database"
)


@pytest.fixture
def storage_database():
    engine = create_engine(normalize_database_url(database_url))
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("""
                DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
                    CREATE ROLE anon;
                  END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
                    CREATE ROLE authenticated;
                  END IF;
                END $$;
                CREATE SCHEMA auth;
                CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
                  $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
                GRANT USAGE ON SCHEMA auth TO authenticated;
                CREATE SCHEMA storage;
                CREATE TABLE storage.buckets (
                  id text PRIMARY KEY, name text, public boolean,
                  file_size_limit bigint, allowed_mime_types text[]
                );
                CREATE TABLE storage.objects (bucket_id text, name text);
                ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
                GRANT USAGE ON SCHEMA storage TO authenticated;
                GRANT SELECT, INSERT, DELETE ON storage.objects TO authenticated;
                CREATE FUNCTION storage.foldername(name text) RETURNS text[]
                  LANGUAGE sql IMMUTABLE AS
                  $$ SELECT string_to_array(name, '/') $$;
            """)
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def test_forward_repair_survives_real_storage_owner_error(storage_database):
    connection = storage_database
    migration = load_migration("d82e41f6a903_repair_release_security.py")
    connection.exec_driver_sql("CREATE ROLE release_migration_probe")
    connection.exec_driver_sql("GRANT USAGE, CREATE ON SCHEMA public TO release_migration_probe")
    dbname = connection.scalar(text("SELECT current_database()"))
    connection.exec_driver_sql(f'GRANT CREATE ON DATABASE "{dbname}" TO release_migration_probe')
    connection.exec_driver_sql("GRANT USAGE ON SCHEMA auth, storage TO release_migration_probe")
    connection.exec_driver_sql(
        "GRANT SELECT, INSERT, UPDATE ON storage.buckets TO release_migration_probe"
    )
    connection.exec_driver_sql("GRANT SELECT ON storage.objects TO release_migration_probe")
    for table in migration.APP_TABLES:
        connection.exec_driver_sql(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
        connection.exec_driver_sql(f'GRANT ALL ON public."{table}" TO anon, authenticated')
        connection.exec_driver_sql(f'ALTER TABLE public."{table}" OWNER TO release_migration_probe')
    connection.exec_driver_sql("SET LOCAL ROLE release_migration_probe")
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()
    for table in migration.APP_TABLES:
        assert connection.scalar(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = CAST(:table AS regclass)"),
            {"table": "public." + table},
        )
        for role in ("anon", "authenticated"):
            assert not connection.scalar(
                text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                {"role": role, "table": "public." + table},
            )
    # Storage setup was denied and rolled back, while table repair survived.
    assert not connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='storage')")
    )


def test_storage_suspension_denies_direct_reads_uploads_and_deletes(storage_database):
    connection = storage_database
    sql = Path(__file__).parents[1] / "docs/supabase-storage-policies.sql"
    connection.exec_driver_sql(sql.read_text(encoding="utf-8"))
    owner, tester, campaign_id, assignment_id = [uuid4() for _ in range(4)]
    with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
        db.add_all(
            [
                Profile(id=owner, username="owner-" + owner.hex[:8], display_name="Owner"),
                Profile(id=tester, username="tester-" + tester.hex[:8], display_name="Tester"),
            ]
        )
        db.flush()
        db.add(
            Campaign(
                id=campaign_id,
                owner_id=owner,
                **{**campaign_payload(), "slug": "storage-" + campaign_id.hex},
            )
        )
        db.flush()
        db.add(
            Assignment(
                id=assignment_id,
                campaign_id=campaign_id,
                tester_id=tester,
                status=AssignmentStatus.IN_PROGRESS,
                accepted_at=datetime.now(UTC),
            )
        )
        db.commit()
    connection.execute(
        text("INSERT INTO storage.objects VALUES (:bucket, :name)"),
        {"bucket": "test-evidence", "name": f"{assignment_id}/existing.png"},
    )
    connection.execute(
        text("SELECT set_config('request.jwt.claim.sub', :uid, true)"), {"uid": str(tester)}
    )
    connection.exec_driver_sql("SET LOCAL ROLE authenticated")
    assert connection.scalar(text("SELECT count(*) FROM storage.objects")) == 1
    connection.execute(
        text("INSERT INTO storage.objects VALUES (:bucket, :name)"),
        {"bucket": "test-evidence", "name": f"{assignment_id}/allowed.png"},
    )
    connection.exec_driver_sql("RESET ROLE")
    connection.execute(
        text("UPDATE profiles SET is_suspended = true WHERE id = :uid"), {"uid": tester}
    )
    connection.exec_driver_sql("SET LOCAL ROLE authenticated")
    assert connection.scalar(text("SELECT count(*) FROM storage.objects")) == 0
    with pytest.raises(ProgrammingError), connection.begin_nested():
        connection.execute(
            text("INSERT INTO storage.objects VALUES (:bucket, :name)"),
            {"bucket": "test-evidence", "name": f"{assignment_id}/denied.png"},
        )
    assert connection.execute(text("DELETE FROM storage.objects")).rowcount == 0
    connection.exec_driver_sql("RESET ROLE")
    assert connection.scalar(text("SELECT count(*) FROM storage.objects")) == 2
