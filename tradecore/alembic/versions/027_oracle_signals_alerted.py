"""oracle_signals.alerted — which signals were pushed to Telegram.

Oracle alerts are now published on rarity within the trailing 24h rather than a
fixed |score| >= 65 (which the combiner never reached). To judge whether the
alerted tail actually beats the rest, each row must record whether it was
alerted. Backfills false: nothing was ever alerted before this.

Revision ID: 027_oracle_alerted
Revises: 026_drop_bot_tables
Create Date: 2026-09-24
"""
import sqlalchemy as sa
from alembic import op

revision = "027_oracle_alerted"
down_revision = "026_drop_bot_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("oracle_signals", sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("idx_oracle_signals_alerted_at", "oracle_signals", ["alerted", "signal_at"])


def downgrade() -> None:
    op.drop_index("idx_oracle_signals_alerted_at", table_name="oracle_signals")
    op.drop_column("oracle_signals", "alerted")
