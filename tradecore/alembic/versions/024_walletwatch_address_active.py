"""Add is_active / deactivation audit to whale_entity_addresses.

WalletWatch scans every row in this table on a 60s tick, so a single
high-frequency address is unbounded load: one promoted wallet was doing
35,000 swaps/day on its own. There was no way to stop watching an address
short of deleting it, which loses the provenance. A flag keeps the row (and
its PnL-discovery history) while taking it out of the scan.

Revision ID: 024_walletwatch_active
Revises: 023_trade_context
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "024_walletwatch_active"
# Verified against `alembic heads` on prod, not against filenames — 023 is the
# real head. (Chaining 023 off a filename-adjacent revision instead of the
# actual head took prod down on 2026-08-21.)
down_revision = "023_trade_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whale_entity_addresses",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "whale_entity_addresses",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "whale_entity_addresses",
        sa.Column("deactivated_reason", sa.String(64), nullable=True),
    )
    # The scan filters on this every tick.
    op.create_index(
        "idx_whale_addr_active", "whale_entity_addresses", ["is_active"]
    )


def downgrade() -> None:
    op.drop_index("idx_whale_addr_active", table_name="whale_entity_addresses")
    op.drop_column("whale_entity_addresses", "deactivated_reason")
    op.drop_column("whale_entity_addresses", "deactivated_at")
    op.drop_column("whale_entity_addresses", "is_active")
