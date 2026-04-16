"""TradeLog — Trade and TradeTag."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open", index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    size_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    leverage: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("1"), server_default="1")
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    fees_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    net_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    setup_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotion: Mapped[str | None] = mapped_column(String(50), nullable=True)
    followed_oracle: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    oracle_signal_id: Mapped[UUID | None] = mapped_column(ForeignKey("oracle_signals.id"), nullable=True)
    exchange_trade_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class TradeTag(Base):
    __tablename__ = "trade_tags"

    id: Mapped[UUID] = uuid_pk()
    trade_id: Mapped[UUID] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_trade_tags_trade", "trade_id"),
        Index("idx_trade_tags_tag", "tag"),
    )
