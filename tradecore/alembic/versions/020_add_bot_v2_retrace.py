"""Add WaveBot v2 retrace/partial-trail columns to bot_trades.

Retrace LIMIT entries add a pending → open/cancelled leg to the lifecycle
(limit_price, expire_at, entry_mode); partial-trail exits need the frozen
initial stop, the favorable peak, and the partial-leg fields. status is a
plain varchar, so the new 'pending' / 'cancelled' values need no DDL.

Revision ID: 020_bot_v2_retrace
Revises: 019_bot_instrumentation
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa


revision = "020_bot_v2_retrace"
down_revision = "019_bot_instrumentation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_trades", sa.Column("entry_mode", sa.String(10), nullable=True))
    op.add_column("bot_trades", sa.Column("limit_price", sa.Numeric(30, 12), nullable=True))
    op.add_column(
        "bot_trades", sa.Column("expire_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "bot_trades", sa.Column("initial_stop_price", sa.Numeric(30, 12), nullable=True)
    )
    op.add_column("bot_trades", sa.Column("peak_price", sa.Numeric(30, 12), nullable=True))
    op.add_column(
        "bot_trades", sa.Column("partial_exit_price", sa.Numeric(30, 12), nullable=True)
    )
    op.add_column(
        "bot_trades", sa.Column("partial_exit_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "bot_trades", sa.Column("partial_pnl_usd", sa.Numeric(20, 4), nullable=True)
    )
    op.add_column("bot_trades", sa.Column("partial_qty", sa.Numeric(40, 18), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_trades", "partial_qty")
    op.drop_column("bot_trades", "partial_pnl_usd")
    op.drop_column("bot_trades", "partial_exit_at")
    op.drop_column("bot_trades", "partial_exit_price")
    op.drop_column("bot_trades", "peak_price")
    op.drop_column("bot_trades", "initial_stop_price")
    op.drop_column("bot_trades", "expire_at")
    op.drop_column("bot_trades", "limit_price")
    op.drop_column("bot_trades", "entry_mode")
