"""Engine availability reported by processing workers

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23 07:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_engine_report",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("worker", sa.String(length=200), nullable=False),
        sa.Column("engine_id", sa.String(length=40), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=False),
        sa.Column("availability", sa.String(length=40), nullable=False),
        sa.Column("check", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_engine_report")),
        sa.UniqueConstraint(
            "worker", "engine_id", "execution_mode", name=op.f("uq_worker_engine_report_worker")
        ),
    )
    op.create_index(op.f("ix_worker_engine_report_engine_id"), "worker_engine_report", ["engine_id"])
    op.create_index(op.f("ix_worker_engine_report_checked_at"), "worker_engine_report", ["checked_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_worker_engine_report_checked_at"), table_name="worker_engine_report")
    op.drop_index(op.f("ix_worker_engine_report_engine_id"), table_name="worker_engine_report")
    op.drop_table("worker_engine_report")
