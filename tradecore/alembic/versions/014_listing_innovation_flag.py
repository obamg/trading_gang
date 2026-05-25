"""Add innovation flag to new_listing_events.

Tags listings (perp included) that sit in Bybit's Innovation Zone risk
tier, so the watcher / frontend can treat them differently from normal
listings (faster moves, wider thresholds, badge in the UI).

Revision ID: 014_listing_innovation
Revises: 013_listing_events
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa


revision = "014_listing_innovation"
down_revision = "013_listing_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "new_listing_events",
        sa.Column("innovation", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "idx_listing_event_innovation",
        "new_listing_events",
        ["innovation"],
    )


def downgrade() -> None:
    op.drop_index("idx_listing_event_innovation", table_name="new_listing_events")
    op.drop_column("new_listing_events", "innovation")
