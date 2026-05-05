"""Add whale_entities and whale_entity_addresses tables.

Revision ID: 009_whale_entities
Revises: 008_oracle_mfe_mae
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "009_whale_entities"
down_revision = "008_oracle_mfe_mae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whale_entities",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("total_transfers", sa.Integer, server_default="0", nullable=False),
        sa.Column("total_volume_usd", sa.Numeric(30, 2), server_default="0", nullable=False),
        sa.Column("avg_move_after_1h_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("avg_move_after_4h_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("conviction_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_whale_entity_type", "whale_entities", ["entity_type"])
    op.create_index("idx_whale_entity_conviction", "whale_entities", ["conviction_score"])

    op.create_table(
        "whale_entity_addresses",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("whale_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.String(100), nullable=False),
        sa.Column("chain", sa.String(50), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_whale_addr_entity", "whale_entity_addresses", ["entity_id"])
    op.create_index("idx_whale_addr_address", "whale_entity_addresses", ["address"], unique=True)


def downgrade() -> None:
    op.drop_table("whale_entity_addresses")
    op.drop_table("whale_entities")
