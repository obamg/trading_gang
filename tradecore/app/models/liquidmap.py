"""LiquidMap — notable liquidation events (heatmap lives in Redis only)."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class LiquidationEvent(Base):
    __tablename__ = "liquidation_events"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    size_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    is_cascade: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_liq_symbol", "symbol"),
        Index("idx_liq_detected", "detected_at"),
        Index("idx_liq_size", "size_usd"),
    )
