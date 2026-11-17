"""ticket purpose: terminal or desktop

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing tickets were all terminal tickets (they expire within minutes anyway).
    with op.batch_alter_table("terminal_tickets") as batch_op:
        batch_op.add_column(
            sa.Column("purpose", sa.String(16), nullable=False, server_default="terminal")
        )


def downgrade() -> None:
    with op.batch_alter_table("terminal_tickets") as batch_op:
        batch_op.drop_column("purpose")
