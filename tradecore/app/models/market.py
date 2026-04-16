"""Market reference data — Symbol."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(String(50), default="binance", server_default="binance")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    market_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    avg_daily_volume_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    updated_at: Mapped[datetime] = updated_at_col()
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_symbols_active", "is_active"),
        Index("idx_symbols_type", "asset_type"),
    )
