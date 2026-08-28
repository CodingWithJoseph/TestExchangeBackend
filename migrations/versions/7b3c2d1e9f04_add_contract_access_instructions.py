"""Add private contract access instructions.

Revision ID: 7b3c2d1e9f04
Revises: 22eb9a0d4076
Create Date: 2026-08-27 14:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b3c2d1e9f04"
down_revision: str | None = "22eb9a0d4076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("testing_contracts", sa.Column("access_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("testing_contracts", "access_instructions")
