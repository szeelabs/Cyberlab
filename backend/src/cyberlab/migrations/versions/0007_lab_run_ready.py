"""lab runs: ready flag for labs prepared in the background

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing runs were prepared before the API answered, so they are ready.
    with op.batch_alter_table("lab_runs") as batch_op:
        batch_op.add_column(
            sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("lab_runs") as batch_op:
        batch_op.drop_column("ready")
