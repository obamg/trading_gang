"""Add MFE/MAE columns to oracle_outcomes.

Revision ID: 008_oracle_mfe_mae
Revises: 007_trade_funding_duration
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision = "008_oracle_mfe_mae"
down_revision = "007_trade_funding_duration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("oracle_outcomes", sa.Column("mfe_1h_pct", sa.Numeric(8, 4), nullable=True))
    op.add_column("oracle_outcomes", sa.Column("mae_1h_pct", sa.Numeric(8, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("oracle_outcomes", "mae_1h_pct")
    op.drop_column("oracle_outcomes", "mfe_1h_pct")
