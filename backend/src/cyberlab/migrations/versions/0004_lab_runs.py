"""lab runs: ready-made labs started in a workspace

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lab_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("lab_id", sa.String(63), nullable=False),
        sa.Column("answer_hashes", sa.JSON(), nullable=False),
        sa.Column("solved", sa.JSON(), nullable=False),
        sa.Column("setup_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("lab_runs")
