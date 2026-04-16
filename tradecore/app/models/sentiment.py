"""SentimentPulse hourly snapshots."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class SentimentSnapshot(Base):
    __tablename__ = "sentiment_snapshots"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    long_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    short_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    open_interest_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_sentiment_symbol_time", "symbol", "snapshot_at", unique=True),
        Index("idx_sentiment_snapshot", "snapshot_at"),
    )


class MarketSentimentSnapshot(Base):
    __tablename__ = "market_sentiment_snapshots"

    id: Mapped[UUID] = uuid_pk()
    fear_greed_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fear_greed_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    btc_dominance_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    total_mcap_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_market_sentiment_time", "snapshot_at", unique=True),
    )
