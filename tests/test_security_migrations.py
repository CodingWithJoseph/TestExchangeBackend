import importlib.util
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError


def load_migration(filename):
    path = Path(__file__).parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location("security_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_original_migration_keeps_table_security_on_storage_permission_failure():
    migration = load_migration("b61c90d42e8a_add_production_safeguards.py")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE journal (command TEXT)"))

        class Batch:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        class Bind:
            dialect = SimpleNamespace(name="postgresql")

            def begin_nested(self):
                return connection.begin_nested()

        class Operations:
            def get_bind(self):
                return Bind()

            def batch_alter_table(self, *args):
                return Batch()

            def execute(self, statement):
                if "DROP POLICY" in statement:
                    raise ProgrammingError(
                        statement, {}, Exception("must be owner of table objects")
                    )
                connection.execute(
                    text("INSERT INTO journal VALUES (:statement)"), {"statement": statement}
                )

        migration.op = Operations()
        migration._postgres_role_exists = lambda role: True
        migration._postgres_relation_exists = lambda schema, table: True
        migration.upgrade()
        survived = connection.execute(text("SELECT command FROM journal")).scalars().all()
        assert sum("ENABLE ROW LEVEL SECURITY" in command for command in survived) == 13
        assert sum("REVOKE ALL ON TABLE" in command for command in survived) == 26
    engine.dispose()
