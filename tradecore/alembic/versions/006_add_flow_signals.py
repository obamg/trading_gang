"""Add flow_signals table for FlowPulse module.

Revision ID: 006_add_flow_signals
Revises: 005_gemradar_liquidity
Create Date: 2026-05-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "006_add_flow_signals"
down_revision = "005_gemradar_liquidity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flow_signals",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("bid_usd", sa.Numeric(30, 2), nullable=True),
        sa.Column("ask_usd", sa.Numeric(30, 2), nullable=True),
        sa.Column("book_imbalance", sa.Numeric(10, 4), nullable=True),
        sa.Column("taker_buy_vol", sa.Numeric(30, 2), nullable=True),
        sa.Column("taker_sell_vol", sa.Numeric(30, 2), nullable=True),
        sa.Column("taker_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("top_long_ratio", sa.Numeric(5, 2), nullable=True),
        sa.Column("top_short_ratio", sa.Numeric(5, 2), nullable=True),
        sa.Column("direction", sa.String(10), nullable=True),
        sa.Column("intensity", sa.Numeric(4, 3), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_flow_symbol_time", "flow_signals", ["symbol", "snapshot_at"])
    op.create_index("idx_flow_snapshot", "flow_signals", ["snapshot_at"])


def downgrade() -> None:
    op.drop_index("idx_flow_snapshot", table_name="flow_signals")
    op.drop_index("idx_flow_symbol_time", table_name="flow_signals")
    op.drop_table("flow_signals")
