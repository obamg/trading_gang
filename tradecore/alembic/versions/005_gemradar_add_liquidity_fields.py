"""Add liquidity and extended price change columns to gemradar_alerts.

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "005_gemradar_liquidity"
down_revision = "004_add_news_articles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gemradar_alerts", sa.Column("liquidity_usd", sa.Numeric(30, 2), nullable=True))
    op.add_column("gemradar_alerts", sa.Column("price_change_1h_pct", sa.Numeric(8, 2), nullable=True))
    op.add_column("gemradar_alerts", sa.Column("price_change_24h_pct", sa.Numeric(8, 2), nullable=True))
    op.add_column("gemradar_alerts", sa.Column("volume_24h_usd", sa.Numeric(30, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("gemradar_alerts", "volume_24h_usd")
    op.drop_column("gemradar_alerts", "price_change_24h_pct")
    op.drop_column("gemradar_alerts", "price_change_1h_pct")
    op.drop_column("gemradar_alerts", "liquidity_usd")
