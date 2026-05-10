"""Add listingwatch tables for new-listing detection + signals.

Revision ID: 013_listing_events
Revises: 012_discovery
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "013_listing_events"
down_revision = "012_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "new_listing_events",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("market_type", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("base_asset", sa.String(20), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("is_cross_listing", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("other_exchanges", JSONB, nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watcher_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("t0_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("t0_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_price", sa.Numeric(30, 12), nullable=True),
        sa.Column("high_15m", sa.Numeric(30, 12), nullable=True),
        sa.Column("low_15m", sa.Numeric(30, 12), nullable=True),
        sa.Column("high_1h", sa.Numeric(30, 12), nullable=True),
        sa.Column("low_1h", sa.Numeric(30, 12), nullable=True),
        sa.Column("last_funding_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("signal_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="watching"),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("exchange", "market_type", "symbol", name="uq_listing_event"),
    )
    op.create_index("idx_listing_event_status", "new_listing_events", ["status"])
    op.create_index("idx_listing_event_detected", "new_listing_events", ["detected_at"])
    op.create_index("idx_listing_event_base", "new_listing_events", ["base_asset"])

    op.create_table(
        "listing_signals",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("new_listing_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_type", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("conviction", sa.Numeric(4, 3), nullable=False),
        sa.Column("price_at_emit", sa.Numeric(30, 12), nullable=True),
        sa.Column("seconds_since_t0", sa.Integer, nullable=True),
        sa.Column("context", JSONB, nullable=True),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_listing_signal_listing", "listing_signals", ["listing_id"])
    op.create_index("idx_listing_signal_emitted", "listing_signals", ["emitted_at"])
    op.create_index("idx_listing_signal_type", "listing_signals", ["signal_type"])


def downgrade() -> None:
    op.drop_index("idx_listing_signal_type", table_name="listing_signals")
    op.drop_index("idx_listing_signal_emitted", table_name="listing_signals")
    op.drop_index("idx_listing_signal_listing", table_name="listing_signals")
    op.drop_table("listing_signals")

    op.drop_index("idx_listing_event_base", table_name="new_listing_events")
    op.drop_index("idx_listing_event_detected", table_name="new_listing_events")
    op.drop_index("idx_listing_event_status", table_name="new_listing_events")
    op.drop_table("new_listing_events")
