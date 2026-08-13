"""Drop the retired WaveBot tables.

WaveBot (the ``bot`` module) was retired: paused in prod since 2026-07-31, its
forward test never cleared the n≥30 gate, and it was superseded by MajorsBot
(and an upcoming BTC-only bot). This migration drops its two tables:

  - bot_trades
  - bot_skipped_signals

A one-off data backup of both tables was taken separately on the VPS before
this ran, so the drop is not the only copy of the history.

DROP TABLE removes each table's dependent indexes
(idx_bot_trade_status_symbol, idx_bot_trade_opened, idx_bot_skipped_reason)
automatically, so they need no explicit drop.

Revision ID: 022_drop_bot_tables
Revises: 021_majorsbot_trades
Create Date: 2026-08-13
"""
from alembic import op


revision = "022_drop_bot_tables"
down_revision = "021_majorsbot_trades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS keeps this idempotent even if a prior partial run already
    # removed a table. DROP TABLE cascades to the table's own indexes.
    op.execute("DROP TABLE IF EXISTS bot_skipped_signals")
    op.execute("DROP TABLE IF EXISTS bot_trades")


def downgrade() -> None:
    raise NotImplementedError(
        "The WaveBot tables (bot_trades, bot_skipped_signals) were intentionally "
        "dropped when the bot module was retired. Their schema is not recreated "
        "here and the data is not recoverable from this migration — restore from "
        "the separate VPS backup taken before revision 022 if it is ever needed."
    )
