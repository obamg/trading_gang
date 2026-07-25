"""Add majorsbot_trades — the fixed-universe majors paper bot.

Mirrors bot_trades (incl. the v2 retrace/partial-trail fields) plus a
``strategy`` discriminator (volevent | fundingfade), fundingfade entry context
(funding_rate_at_entry / funding_pctile_at_entry), and entry_bar_at (bar-walk
resume point + funding-accrual lower bound). signal_high/low and
take_profit_price are nullable here — fundingfade rows have neither.

Revision ID: 021_majorsbot_trades
Revises: 020_bot_v2_retrace
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "021_majorsbot_trades"
down_revision = "020_bot_v2_retrace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "majorsbot_trades",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("market_type", sa.String(10), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("signal_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_bar_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signal_high", sa.Numeric(30, 12), nullable=True),
        sa.Column("signal_low", sa.Numeric(30, 12), nullable=True),
        sa.Column("notional_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("qty", sa.Numeric(40, 18), nullable=False),
        sa.Column("paper_equity_at_entry", sa.Numeric(20, 2), nullable=False),
        sa.Column("stop_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("take_profit_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("close_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(20), nullable=True),
        sa.Column("realized_pnl_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("realized_r", sa.Numeric(8, 3), nullable=True),
        sa.Column("realized_r_net", sa.Numeric(8, 3), nullable=True),
        sa.Column("fees_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("funding_pnl_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("funding_rate_at_entry", sa.Numeric(12, 9), nullable=True),
        sa.Column("funding_pctile_at_entry", sa.Numeric(6, 4), nullable=True),
        sa.Column("entry_mode", sa.String(10), nullable=True),
        sa.Column("limit_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_stop_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("peak_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("partial_exit_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("partial_exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("partial_pnl_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("partial_qty", sa.Numeric(40, 18), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_majorsbot_trade_status_symbol", "majorsbot_trades", ["status", "symbol"]
    )
    op.create_index("idx_majorsbot_trade_opened", "majorsbot_trades", ["entry_at"])
    op.create_index("idx_majorsbot_trade_strategy", "majorsbot_trades", ["strategy"])


def downgrade() -> None:
    op.drop_index("idx_majorsbot_trade_strategy", table_name="majorsbot_trades")
    op.drop_index("idx_majorsbot_trade_opened", table_name="majorsbot_trades")
    op.drop_index("idx_majorsbot_trade_status_symbol", table_name="majorsbot_trades")
    op.drop_table("majorsbot_trades")
