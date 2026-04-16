"""RadarX volume-spike alerts."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class RadarXAlert(Base):
    __tablename__ = "radarx_alerts"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="5m", server_default="5m")
    z_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    ratio: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    candle_volume_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    avg_volume_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    price_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    volume_24h_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_radarx_symbol", "symbol"),
        Index("idx_radarx_triggered", "triggered_at", postgresql_using="btree"),
        Index("idx_radarx_zscore", "z_score"),
    )
