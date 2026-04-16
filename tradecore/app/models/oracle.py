"""Oracle — signals and outcomes."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class OracleSignal(Base):
    __tablename__ = "oracle_signals"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), default="futures", server_default="futures")
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    confluence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    signals_breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    rr_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    suggested_size: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    suggested_leverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    macro_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vix_at_signal: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    dxy_at_signal: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    timeframe: Mapped[str] = mapped_column(String(10), default="5m", server_default="5m")
    signal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_oracle_symbol", "symbol"),
        Index("idx_oracle_signal_at", "signal_at"),
        Index("idx_oracle_score", "score"),
        Index("idx_oracle_recommendation", "recommendation"),
    )


class OracleOutcome(Base):
    __tablename__ = "oracle_outcomes"

    id: Mapped[UUID] = uuid_pk()
    signal_id: Mapped[UUID] = mapped_column(
        ForeignKey("oracle_signals.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    price_at_signal: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    price_15m: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    price_1h: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    price_4h: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    price_24h: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    pnl_15m_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    pnl_1h_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    pnl_4h_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    pnl_24h_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    was_correct_1h: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    was_correct_4h: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_oracle_outcomes_signal", "signal_id"),
    )
