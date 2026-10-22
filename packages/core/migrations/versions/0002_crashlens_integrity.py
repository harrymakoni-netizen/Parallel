"""CrashLens: interpretation sources and evidence-original integrity

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22 23:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGINALS_SQL = """
CREATE OR REPLACE FUNCTION parallel_evidence_item_original_immutable() RETURNS trigger AS $$
BEGIN
  IF NEW.original_sha256 IS DISTINCT FROM OLD.original_sha256
     OR NEW.source_asset_id IS DISTINCT FROM OLD.source_asset_id THEN
    RAISE EXCEPTION 'evidence originals are immutable (evidence_item %)', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER evidence_item_original_immutable BEFORE UPDATE ON evidence_item
  FOR EACH ROW EXECUTE FUNCTION parallel_evidence_item_original_immutable();

CREATE OR REPLACE FUNCTION parallel_evidence_asset_immutable() RETURNS trigger AS $$
BEGIN
  IF OLD.sensitivity = 'evidence' AND (NEW.sha256 IS DISTINCT FROM OLD.sha256
     OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
     OR NEW.sensitivity IS DISTINCT FROM OLD.sensitivity
     OR NEW.derived_from_id IS DISTINCT FROM OLD.derived_from_id) THEN
    RAISE EXCEPTION 'evidence media records are immutable (source_asset %)', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER source_asset_evidence_immutable BEFORE UPDATE ON source_asset
  FOR EACH ROW EXECUTE FUNCTION parallel_evidence_asset_immutable();
"""


CASE_SCOPED = ("domain_model", "scenario", "simulation_run")


def upgrade() -> None:
    for table in CASE_SCOPED:
        op.add_column(table, sa.Column("case_id", sa.String(length=32), nullable=True))
        op.create_index(op.f(f"ix_{table}_case_id"), table, ["case_id"], unique=False)
        op.create_foreign_key(
            op.f(f"fk_{table}_case_id_crash_case"), table, "crash_case", ["case_id"], ["id"], ondelete="RESTRICT"
        )
    op.add_column(
        "evidence_observation",
        sa.Column("based_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
    )
    op.execute(ORIGINALS_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS source_asset_evidence_immutable ON source_asset")
    op.execute("DROP FUNCTION IF EXISTS parallel_evidence_asset_immutable()")
    op.execute("DROP TRIGGER IF EXISTS evidence_item_original_immutable ON evidence_item")
    op.execute("DROP FUNCTION IF EXISTS parallel_evidence_item_original_immutable()")
    op.drop_column("evidence_observation", "based_on")
    for table in CASE_SCOPED:
        op.drop_constraint(op.f(f"fk_{table}_case_id_crash_case"), table, type_="foreignkey")
        op.drop_index(op.f(f"ix_{table}_case_id"), table_name=table)
        op.drop_column(table, "case_id")
