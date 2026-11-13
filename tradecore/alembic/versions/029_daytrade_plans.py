"""daytrade_plans — paper journal for the DayTrade opening-range-breakout playbook.

Revision ID: 029_daytrade_plans
Revises: 028_drop_gemradar
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "029_daytrade_plans"
down_revision = "028_drop_gemradar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daytrade_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("session_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("playbook", sa.String(12), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("entry", sa.Numeric(30, 8), nullable=False),
        sa.Column("stop", sa.Numeric(30, 8), nullable=False),
        sa.Column("target", sa.Numeric(30, 8), nullable=False),
        sa.Column("range_high", sa.Numeric(30, 8), nullable=False),
        sa.Column("range_low", sa.Numeric(30, 8), nullable=False),
        sa.Column("stop_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("vol_x", sa.Numeric(8, 2), nullable=True),
        sa.Column("funding_rate", sa.Numeric(12, 8), nullable=True),
        sa.Column("checks", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ai_verdict", sa.String(10), nullable=True),
        sa.Column("ai_reason", sa.Text(), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Numeric(30, 8), nullable=True),
        sa.Column("pnl_r", sa.Numeric(8, 3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("exchange", "symbol", "session_at", name="uq_daytrade_plan_session"),
    )
    op.create_index("idx_daytrade_plans_triggered", "daytrade_plans", ["triggered_at"])
    op.create_index("idx_daytrade_plans_status", "daytrade_plans", ["status"])


def downgrade() -> None:
    op.drop_index("idx_daytrade_plans_status", table_name="daytrade_plans")
    op.drop_index("idx_daytrade_plans_triggered", table_name="daytrade_plans")
    op.drop_table("daytrade_plans")
