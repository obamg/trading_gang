"""Add market_type column to bot_trades.

Revision ID: 017_bot_market_type
Revises: 016_bot_tables
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa


revision = "017_bot_market_type"
down_revision = "016_bot_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_trades",
        sa.Column("market_type", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_trades", "market_type")
