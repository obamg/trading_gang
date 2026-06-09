"""Add bot_trades + bot_skipped_signals — WaveBot paper trading module.

Revision ID: 016_bot_tables
Revises: 015_wave_assets
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "016_bot_tables"
down_revision = "015_wave_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_trades",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("alert_type", sa.String(20), nullable=False),
        sa.Column("alert_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_high", sa.Numeric(30, 12), nullable=False),
        sa.Column("signal_low", sa.Numeric(30, 12), nullable=False),
        sa.Column("notional_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("qty", sa.Numeric(40, 18), nullable=False),
        sa.Column("paper_equity_at_entry", sa.Numeric(20, 2), nullable=False),
        sa.Column("stop_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("take_profit_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("close_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(20), nullable=True),
        sa.Column("realized_pnl_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("realized_r", sa.Numeric(8, 3), nullable=True),
        sa.Column("oracle_score_at_entry", sa.Numeric(6, 2), nullable=True),
        sa.Column("vol_ratio", sa.Numeric(8, 3), nullable=True),
        sa.Column("funding_pct", sa.Numeric(8, 5), nullable=True),
        sa.Column("pct_change", sa.Numeric(8, 5), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_bot_trade_status_symbol", "bot_trades", ["status", "symbol"])
    op.create_index("idx_bot_trade_opened", "bot_trades", ["entry_at"])

    op.create_table(
        "bot_skipped_signals",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("alert_type", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("alert_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("skip_reason", sa.String(30), nullable=False),
        sa.Column("oracle_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("context", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_bot_skipped_reason",
        "bot_skipped_signals",
        ["skip_reason", "alert_detected_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_bot_skipped_reason", table_name="bot_skipped_signals")
    op.drop_table("bot_skipped_signals")
    op.drop_index("idx_bot_trade_opened", table_name="bot_trades")
    op.drop_index("idx_bot_trade_status_symbol", table_name="bot_trades")
    op.drop_table("bot_trades")
