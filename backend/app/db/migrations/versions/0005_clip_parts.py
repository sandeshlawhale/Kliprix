"""Clip parts_json and montage: the source ranges of a montage clip (D-051).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("parts_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("montage", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("clips") as batch:
        batch.drop_column("montage")
        batch.drop_column("parts_json")
