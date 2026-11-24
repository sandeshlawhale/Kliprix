"""Batch last_activity_at: the sidebar history is ordered by the latest work (D-055).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Never rebuild `batches` here (batch_alter_table + alter_column): SQLite drops the old
# table with foreign keys on, which cascades and deletes every video and clip. A plain
# ADD COLUMN with a constant default keeps the table in place.
EPOCH = "1970-01-01 00:00:00"


def upgrade() -> None:
    """Upgrade schema. Existing batches start at their creation time."""
    op.add_column(
        "batches",
        sa.Column("last_activity_at", sa.DateTime(), nullable=False, server_default=EPOCH),
    )
    op.execute("UPDATE batches SET last_activity_at = created_at")


def downgrade() -> None:
    """Downgrade schema (SQLite 3.35+ drops a column in place)."""
    op.drop_column("batches", "last_activity_at")
