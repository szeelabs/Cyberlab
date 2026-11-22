"""classrooms: a teacher's class and its members

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classrooms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("lab_id", sa.String(63), nullable=False),
        sa.Column("join_code", sa.String(16), nullable=False),
        sa.Column("open", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_classrooms_owner_id", "classrooms", ["owner_id"])
    op.create_index("ix_classrooms_join_code", "classrooms", ["join_code"], unique=True)
    op.create_table(
        "class_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "classroom_id",
            sa.String(36),
            sa.ForeignKey("classrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("classroom_id", "user_id"),
    )
    op.create_index("ix_class_members_classroom_id", "class_members", ["classroom_id"])
    op.create_index("ix_class_members_user_id", "class_members", ["user_id"])


def downgrade() -> None:
    op.drop_table("class_members")
    op.drop_table("classrooms")
