"""xstrend_weeks — paper record of the weekly cross-sectional trend portfolio.

Revision ID: 030_xstrend_weeks
Revises: 029_daytrade_plans
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "030_xstrend_weeks"
down_revision = "029_daytrade_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    num = lambda name: sa.Column(name, sa.Numeric(12, 8), nullable=True)  # noqa: E731
    op.create_table(
        "xstrend_weeks",
        sa.Column("week_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("rules", sa.String(12), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("universe", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("weights", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        num("turnover"),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        num("gross"), num("cost"), num("funding"), num("net"), num("mkt"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("xstrend_weeks")
