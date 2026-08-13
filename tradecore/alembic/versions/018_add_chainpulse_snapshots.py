"""Add chainpulse_snapshots — daily Santiment on-chain macro data.

Revision ID: 018_chainpulse_snapshots
Revises: 019_bot_instrumentation
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "018_chainpulse_snapshots"
# Chained after 022 (bot work shipped first; 022 retired the WaveBot tables) —
# the number is out of order but the revision graph is what alembic follows.
down_revision = "022_drop_bot_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chainpulse_snapshots",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("mvrv", sa.Numeric(12, 4), nullable=True),
        sa.Column("nvt", sa.Numeric(12, 4), nullable=True),
        sa.Column("exchange_balance", sa.Numeric(30, 4), nullable=True),
        sa.Column("exchange_inflow", sa.Numeric(30, 4), nullable=True),
        sa.Column("exchange_outflow", sa.Numeric(30, 4), nullable=True),
        sa.Column("active_addresses", sa.Numeric(20, 2), nullable=True),
        sa.Column("network_profit_loss", sa.Numeric(30, 4), nullable=True),
        sa.Column("regime", sa.String(20), nullable=True),
        sa.Column("metric_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_chainpulse_asset_date", "chainpulse_snapshots", ["asset", "metric_date"], unique=True)
    op.create_index("idx_chainpulse_snapshot", "chainpulse_snapshots", ["snapshot_at"])


def downgrade() -> None:
    op.drop_index("idx_chainpulse_snapshot", table_name="chainpulse_snapshots")
    op.drop_index("idx_chainpulse_asset_date", table_name="chainpulse_snapshots")
    op.drop_table("chainpulse_snapshots")
