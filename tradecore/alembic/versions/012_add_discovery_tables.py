"""Add walletwatch discovery tables for PnL-based wallet scoring.

Revision ID: 012_discovery
Revises: 011_wallet_swaps
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "012_discovery"
down_revision = "011_wallet_swaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_tokens",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("chain", sa.String(20), nullable=False),
        sa.Column("address", sa.String(100), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("primary_pool_address", sa.String(100), nullable=True),
        sa.Column("price_at_discovery_usd", sa.Numeric(30, 12), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chain", "address", name="uq_discovery_token"),
    )
    op.create_index("idx_discovery_token_unscored", "discovery_tokens", ["last_scored_at"])

    op.create_table(
        "wallet_token_pnl",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("wallet_address", sa.String(100), nullable=False),
        sa.Column("chain", sa.String(20), nullable=False),
        sa.Column("token_address", sa.String(100), nullable=False),
        sa.Column("token_symbol", sa.String(40), nullable=True),
        sa.Column("total_buy_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("total_buy_amount", sa.Numeric(40, 18), server_default="0", nullable=False),
        sa.Column("total_sell_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("total_sell_amount", sa.Numeric(40, 18), server_default="0", nullable=False),
        sa.Column("current_balance", sa.Numeric(40, 18), server_default="0", nullable=False),
        sa.Column("current_value_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("realized_pnl_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("unrealized_pnl_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("multiple", sa.Numeric(10, 4), nullable=True),
        sa.Column("first_buy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("wallet_address", "chain", "token_address", name="uq_wallet_token_pnl"),
    )
    op.create_index("idx_wallet_token_pnl_wallet", "wallet_token_pnl", ["wallet_address"])
    op.create_index("idx_wallet_token_pnl_token", "wallet_token_pnl", ["chain", "token_address"])
    op.create_index("idx_wallet_token_pnl_multiple", "wallet_token_pnl", ["multiple"])

    op.create_table(
        "wallet_pnl_score",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("wallet_address", sa.String(100), nullable=False, unique=True),
        sa.Column("chain", sa.String(20), nullable=False),
        sa.Column("total_realized_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("total_unrealized_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("total_cost_basis_usd", sa.Numeric(20, 2), server_default="0", nullable=False),
        sa.Column("win_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("loss_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("win_rate", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.Column("avg_multiple", sa.Numeric(10, 4), server_default="0", nullable=False),
        sa.Column("best_multiple", sa.Numeric(10, 4), server_default="0", nullable=False),
        sa.Column("token_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("discovery_score", sa.Numeric(20, 4), server_default="0", nullable=False),
        sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "promoted_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("whale_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_wallet_pnl_score_score", "wallet_pnl_score", ["discovery_score"])
    op.create_index("idx_wallet_pnl_score_chain", "wallet_pnl_score", ["chain"])
    op.create_index("idx_wallet_pnl_score_promoted", "wallet_pnl_score", ["promoted_at"])


def downgrade() -> None:
    op.drop_index("idx_wallet_pnl_score_promoted", table_name="wallet_pnl_score")
    op.drop_index("idx_wallet_pnl_score_chain", table_name="wallet_pnl_score")
    op.drop_index("idx_wallet_pnl_score_score", table_name="wallet_pnl_score")
    op.drop_table("wallet_pnl_score")

    op.drop_index("idx_wallet_token_pnl_multiple", table_name="wallet_token_pnl")
    op.drop_index("idx_wallet_token_pnl_token", table_name="wallet_token_pnl")
    op.drop_index("idx_wallet_token_pnl_wallet", table_name="wallet_token_pnl")
    op.drop_table("wallet_token_pnl")

    op.drop_index("idx_discovery_token_unscored", table_name="discovery_tokens")
    op.drop_table("discovery_tokens")
