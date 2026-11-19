"""Video transcribe_engine: the speech-to-text engine that ran, shown on the Transcribe step
(D-030, a temporary debug display).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("transcribe_engine", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("transcribe_engine")
