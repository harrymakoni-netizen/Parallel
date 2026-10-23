"""Assistant: deterministic replay order and adoption records for suggestions

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 00:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_message", sa.Column("seq", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("assistant_tool_call", sa.Column("seq", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("assistant_tool_call", sa.Column("tool_use_id", sa.String(length=80), nullable=True))
    op.add_column("assistant_tool_call", sa.Column("adopted_by", sa.String(length=32), nullable=True))
    op.add_column("assistant_tool_call", sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assistant_tool_call", sa.Column("adopted_object_id", sa.String(length=32), nullable=True))
    op.create_index(
        op.f("ix_assistant_tool_call_adopted_by"), "assistant_tool_call", ["adopted_by"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_assistant_tool_call_adopted_by_app_user"),
        "assistant_tool_call",
        "app_user",
        ["adopted_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_assistant_tool_call_adopted_by_app_user"), "assistant_tool_call", type_="foreignkey"
    )
    op.drop_index(op.f("ix_assistant_tool_call_adopted_by"), table_name="assistant_tool_call")
    for col in ("adopted_object_id", "adopted_at", "adopted_by", "tool_use_id", "seq"):
        op.drop_column("assistant_tool_call", col)
    op.drop_column("assistant_message", "seq")
