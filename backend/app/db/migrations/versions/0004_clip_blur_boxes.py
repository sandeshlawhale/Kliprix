"""Clip blur_boxes_json: manual blur boxes drawn in the clip editor (D-048).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("blur_boxes_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("clips") as batch:
        batch.drop_column("blur_boxes_json")
