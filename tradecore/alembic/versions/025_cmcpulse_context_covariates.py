"""Add crowding + regime covariates to trade_context_snapshots.

The table already recorded Fear & Greed and CMC search-trending rank. These
five columns widen the contemporaneous record while the forward tests are
still running, which is the only window in which it is free to do: at the
gate we want to ask "which kind of attention marked a worse entry?" and
"did we only ever trade one regime?" against data captured at entry, not
reconstructed afterwards.

All nullable with no server default — existing rows backfill as NULL, which
is honest: those covariates genuinely were not collected then. NULL on a new
row means "not in that list" for the rank columns and "not collected" for
the two regime columns (they need a CMC API key; without one the collector
no-ops).

Revision ID: 025_cmcpulse_covariates
Revises: 024_walletwatch_active
Create Date: 2026-09-22
"""
import sqlalchemy as sa
from alembic import op

revision = "025_cmcpulse_covariates"
# Verified against the revision chain, not filenames: 024_walletwatch_active
# declares down_revision 023_trade_context and nothing chains off it, so it
# is the real head. (Chaining off a filename-adjacent revision instead of the
# actual head took prod down on 2026-08-21.)
down_revision = "024_walletwatch_active"
branch_labels = None
depends_on = None

TABLE = "trade_context_snapshots"


def upgrade() -> None:
    # Crowding: three populations distinct from search-trending. Kept as
    # separate columns rather than one flag so the gate can decompose them.
    op.add_column(TABLE, sa.Column("most_visited_rank", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("gainers_losers_rank", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("community_rank", sa.Integer(), nullable=True))
    # Regime: the whole-market backdrop at entry.
    op.add_column(
        TABLE, sa.Column("btc_dominance_pct", sa.Numeric(6, 2), nullable=True)
    )
    op.add_column(
        TABLE, sa.Column("total_mcap_usd", sa.Numeric(24, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column(TABLE, "total_mcap_usd")
    op.drop_column(TABLE, "btc_dominance_pct")
    op.drop_column(TABLE, "community_rank")
    op.drop_column(TABLE, "gainers_losers_rank")
    op.drop_column(TABLE, "most_visited_rank")
