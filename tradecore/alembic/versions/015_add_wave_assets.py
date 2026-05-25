"""Add wave_assets — universe table for WaveWatch surveillance.

One row per Bybit Innovation Zone (exchange, market_type, symbol) under
continuous monitoring. Per-tick state (current score, last alert ts) lives
in Redis; this table holds the membership of the universe plus snapshots
the API needs for leaderboard rendering.

Revision ID: 015_wave_assets
Revises: 014_listing_innovation
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "015_wave_assets"
down_revision = "014_listing_innovation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wave_assets",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("market_type", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("base_asset", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("latest_score_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_alerted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("exchange", "market_type", "symbol", name="uq_wave_asset"),
    )
    op.create_index("idx_wave_asset_status", "wave_assets", ["status"])
    op.create_index("idx_wave_asset_score", "wave_assets", ["latest_score"])


def downgrade() -> None:
    op.drop_index("idx_wave_asset_score", table_name="wave_assets")
    op.drop_index("idx_wave_asset_status", table_name="wave_assets")
    op.drop_table("wave_assets")
