"""WhaleRadar events — large trades, on-chain transfers, OI surges."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class WhaleTrade(Base):
    __tablename__ = "whale_trades"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    trade_size_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    exchange: Mapped[str] = mapped_column(String(50), default="binance", server_default="binance")
    is_futures: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_whale_trades_symbol", "symbol"),
        Index("idx_whale_trades_detected", "detected_at"),
        Index("idx_whale_trades_size", "trade_size_usd"),
    )


class WhaleOnchainTransfer(Base):
    __tablename__ = "whale_onchain_transfers"

    id: Mapped[UUID] = uuid_pk()
    asset: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    from_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    from_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    to_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    transfer_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    chain: Mapped[str] = mapped_column(String(50), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_onchain_asset", "asset"),
        Index("idx_onchain_detected", "detected_at"),
        Index("idx_onchain_type", "transfer_type"),
    )


class OISurgeEvent(Base):
    __tablename__ = "oi_surge_events"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    oi_before_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    oi_after_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    oi_change_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    price_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_oi_symbol", "symbol"),
        Index("idx_oi_detected", "detected_at"),
    )
