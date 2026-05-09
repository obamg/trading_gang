"""Add wallet_swaps table for WalletWatch DEX swap tracking.

Revision ID: 011_wallet_swaps
Revises: 010_exchange_credentials
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "011_wallet_swaps"
down_revision = "010_exchange_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallet_swaps",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("wallet_address", sa.String(100), nullable=False),
        sa.Column(
            "entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("whale_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chain", sa.String(20), nullable=False),
        sa.Column("tx_hash", sa.String(120), nullable=False),
        sa.Column("block_number", sa.BigInteger, nullable=True),
        sa.Column("swap_type", sa.String(10), nullable=False),
        sa.Column("token_in_address", sa.String(100), nullable=False),
        sa.Column("token_in_symbol", sa.String(40), nullable=True),
        sa.Column("token_in_amount", sa.Numeric(40, 18), nullable=False),
        sa.Column("token_out_address", sa.String(100), nullable=False),
        sa.Column("token_out_symbol", sa.String(40), nullable=True),
        sa.Column("token_out_amount", sa.Numeric(40, 18), nullable=False),
        sa.Column("amount_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("venue", sa.String(40), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chain", "tx_hash", "wallet_address", name="uq_wallet_swap_tx"),
    )
    op.create_index("idx_wallet_swap_wallet", "wallet_swaps", ["wallet_address"])
    op.create_index("idx_wallet_swap_entity", "wallet_swaps", ["entity_id"])
    op.create_index("idx_wallet_swap_detected", "wallet_swaps", ["detected_at"])
    op.create_index(
        "idx_wallet_swap_token_out_buy",
        "wallet_swaps",
        ["token_out_address"],
        postgresql_where=sa.text("swap_type = 'buy'"),
    )


def downgrade() -> None:
    op.drop_index("idx_wallet_swap_token_out_buy", table_name="wallet_swaps")
    op.drop_index("idx_wallet_swap_detected", table_name="wallet_swaps")
    op.drop_index("idx_wallet_swap_entity", table_name="wallet_swaps")
    op.drop_index("idx_wallet_swap_wallet", table_name="wallet_swaps")
    op.drop_table("wallet_swaps")
