"""WalletWatch — DEX swaps observed for labeled smart-money wallets."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class WalletSwap(Base):
    __tablename__ = "wallet_swaps"

    id: Mapped[UUID] = uuid_pk()
    wallet_address: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whale_entities.id", ondelete="SET NULL"), nullable=True
    )
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(120), nullable=False)
    block_number: Mapped[int | None] = mapped_column(nullable=True)

    swap_type: Mapped[str] = mapped_column(String(10), nullable=False)  # buy | sell | rotate
    token_in_address: Mapped[str] = mapped_column(String(100), nullable=False)
    token_in_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    token_in_amount: Mapped[Decimal] = mapped_column(Numeric(40, 18), nullable=False)
    token_out_address: Mapped[str] = mapped_column(String(100), nullable=False)
    token_out_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    token_out_amount: Mapped[Decimal] = mapped_column(Numeric(40, 18), nullable=False)

    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    venue: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        UniqueConstraint("chain", "tx_hash", "wallet_address", name="uq_wallet_swap_tx"),
        Index("idx_wallet_swap_wallet", "wallet_address"),
        Index("idx_wallet_swap_entity", "entity_id"),
        Index("idx_wallet_swap_detected", "detected_at"),
        Index(
            "idx_wallet_swap_token_out_buy",
            "token_out_address",
            postgresql_where="swap_type = 'buy'",
        ),
    )
