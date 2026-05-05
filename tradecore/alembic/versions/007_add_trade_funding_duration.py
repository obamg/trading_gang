"""Add funding_paid_usd, hold_duration_seconds, exit_reason to trades.

Revision ID: 007_trade_funding_duration
Revises: 006_add_flow_signals
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = "007_trade_funding_duration"
down_revision = "006_add_flow_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("funding_paid_usd", sa.Numeric(20, 2), nullable=True))
    op.add_column("trades", sa.Column("hold_duration_seconds", sa.Integer(), nullable=True))
    op.add_column("trades", sa.Column("exit_reason", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "exit_reason")
    op.drop_column("trades", "hold_duration_seconds")
    op.drop_column("trades", "funding_paid_usd")
