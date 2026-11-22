"""custom labs: labs written and imported by users, optionally shared

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_labs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("catalog_id", sa.String(63), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_custom_labs_owner_id", "custom_labs", ["owner_id"])


def downgrade() -> None:
    op.drop_table("custom_labs")
