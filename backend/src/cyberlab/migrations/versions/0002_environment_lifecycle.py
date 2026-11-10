"""environment lifecycle: last activity and expiry

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("environments") as batch_op:
        batch_op.add_column(sa.Column("last_activity_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
    # Existing environments count as active "now", so nothing is reaped right after upgrading.
    op.execute("UPDATE environments SET last_activity_at = updated_at")


def downgrade() -> None:
    with op.batch_alter_table("environments") as batch_op:
        batch_op.drop_column("expires_at")
        batch_op.drop_column("last_activity_at")
