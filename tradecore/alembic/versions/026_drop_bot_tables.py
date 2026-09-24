"""Drop the bot tables — MajorsBot removed.

All trading bots were removed from TradeCore on 2026-09-24. This drops what
they owned:

  majorsbot_trades         153 rows (101 closed, 51 cancelled, 1 pending)
  trade_context_snapshots   54 rows (CMCPulse regime stamp, per bot entry)

The forward-test record goes with them: volevent n=58 at -0.0887 avg net R,
fundingfade n=28 at -0.4142, newsevent n=15 at +0.118% of equity. That was a
deliberate call, not an oversight — see the PR.

**downgrade() recreates the schema but CANNOT restore the rows.** The source
data (fills, bar series, news legs) is not retained anywhere, so the ledger
cannot be rebuilt. Treat this as one-way.

CMCPulse itself survives: its collectors are the only source of Fear & Greed,
CMC search-trending and whole-market regime in the app, and they are read via
GET /cmcpulse/context rather than through these tables.

Not to be confused with 022_drop_bot_tables, which dropped `bot_trades` and
`bot_skipped_signals` — the earlier WaveBot, retired 2026-08.

Revision ID: 026_drop_bot_tables
Revises: 025_cmcpulse_covariates
Create Date: 2026-09-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "026_drop_bot_tables"
down_revision = "025_cmcpulse_covariates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS so a database that never ran the bots migrates cleanly.
    # DROP TABLE cascades to each table's own indexes.
    op.execute("DROP TABLE IF EXISTS trade_context_snapshots")
    op.execute("DROP TABLE IF EXISTS majorsbot_trades")


def downgrade() -> None:
    """Schema only — the rows are gone for good.

    Deliberately minimal: recreating the full original column set would imply
    the data could come back. It cannot. This exists so the chain stays
    reversible, not so the ledger does.
    """
    op.create_table(
        "majorsbot_trades",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "trade_context_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
