"""Add instrumentation columns to bot_trades.

Phase-0 measurement fixes: costs decomposed (fees, funding), R net of costs,
and liquidity at entry — so edge decisions are made on net numbers.

Revision ID: 019_bot_instrumentation
Revises: 017_bot_market_type
Create Date: 2026-07-10
"""
from alembic import op
import sqlalchemy as sa


revision = "019_bot_instrumentation"
down_revision = "017_bot_market_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_trades",
        sa.Column("realized_r_net", sa.Numeric(8, 3), nullable=True),
    )
    op.add_column(
        "bot_trades",
        sa.Column("fees_usd", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "bot_trades",
        sa.Column("funding_pnl_usd", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "bot_trades",
        sa.Column("entry_turnover_usd", sa.Numeric(20, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_trades", "entry_turnover_usd")
    op.drop_column("bot_trades", "funding_pnl_usd")
    op.drop_column("bot_trades", "fees_usd")
    op.drop_column("bot_trades", "realized_r_net")
