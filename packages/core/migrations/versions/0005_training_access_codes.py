"""Access codes for training notebooks on borrowed GPUs

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "training_access_code",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["reconstruction_job.id"], ondelete="CASCADE",
                                name=op.f("fk_training_access_code_job_id_reconstruction_job")),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE",
                                name=op.f("fk_training_access_code_project_id_project")),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], ondelete="CASCADE",
                                name=op.f("fk_training_access_code_created_by_app_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_training_access_code")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_training_access_code_token_hash")),
    )
    op.create_index(op.f("ix_training_access_code_job_id"), "training_access_code", ["job_id"])
    op.create_index(op.f("ix_training_access_code_project_id"), "training_access_code", ["project_id"])
    op.create_index(op.f("ix_training_access_code_created_by"), "training_access_code", ["created_by"])


def downgrade() -> None:
    op.drop_index(op.f("ix_training_access_code_created_by"), table_name="training_access_code")
    op.drop_index(op.f("ix_training_access_code_project_id"), table_name="training_access_code")
    op.drop_index(op.f("ix_training_access_code_job_id"), table_name="training_access_code")
    op.drop_table("training_access_code")
