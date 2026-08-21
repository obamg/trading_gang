"""Add trade_context_snapshots — CMCPulse regime context per bot trade.

Observational columns only: Fear & Greed and CMC trending-rank at the moment
a MajorsBot trade opens, so the evaluation gate can test crowding/regime
hypotheses against contemporaneous data. Nothing reads these to trade.

Revision ID: 023_trade_context
Revises: 018_chainpulse_snapshots
Create Date: 2026-08-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "023_trade_context"
# NOT 022: the chain is non-linear at the tail — 018_chainpulse was merged
# AFTER 022 and declares 022 as its parent, so 018 is the true head despite
# its filename. Pointing 023 at 022 created a second head and took prod down
# (alembic refuses "upgrade head" with multiple heads; the api entrypoint
# crash-looped). Check `alembic heads`, not filenames, before chaining.
down_revision = "018_chainpulse_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_context_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("fear_greed", sa.Integer(), nullable=True),
        sa.Column("fear_greed_class", sa.String(20), nullable=True),
        sa.Column("trending_rank", sa.Integer(), nullable=True),
        sa.Column("trending_change_24h", sa.Numeric(12, 4), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_trade_context_strategy", "trade_context_snapshots", ["strategy"])
    op.create_index("idx_trade_context_captured", "trade_context_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_table("trade_context_snapshots")
