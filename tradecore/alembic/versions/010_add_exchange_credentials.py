"""Add exchange_credentials table and Trade idempotency index.

Revision ID: 010_exchange_credentials
Revises: 009_whale_entities
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "010_exchange_credentials"
down_revision = "009_whale_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exchange_credentials",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("label", sa.String(50), server_default="default", nullable=False),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("api_secret_enc", sa.Text, nullable=False),
        sa.Column("passphrase_enc", sa.Text, nullable=True),
        sa.Column("permissions", JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text, nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "exchange", "label", name="uq_exchange_creds_user_label"),
    )
    op.create_index("ix_exchange_credentials_user_id", "exchange_credentials", ["user_id"])
    op.create_index("idx_exchange_creds_active", "exchange_credentials", ["is_active"])

    # Idempotent upsert key for synced fills: same (exchange, exchange_trade_id)
    # must never insert twice. NULLs are allowed (manual trades have no exchange_trade_id).
    op.create_index(
        "uq_trades_exchange_trade_id",
        "trades",
        ["exchange", "exchange_trade_id"],
        unique=True,
        postgresql_where=sa.text("exchange_trade_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_trades_exchange_trade_id", table_name="trades")
    op.drop_index("idx_exchange_creds_active", table_name="exchange_credentials")
    op.drop_index("ix_exchange_credentials_user_id", table_name="exchange_credentials")
    op.drop_table("exchange_credentials")
