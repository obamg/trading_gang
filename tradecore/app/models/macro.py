"""MacroPulse — daily snapshots and economic calendar."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class MacroSnapshot(Base):
    __tablename__ = "macro_snapshots"

    id: Mapped[UUID] = uuid_pk()
    dxy: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    us10y: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    us2y: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    yield_spread: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    vix: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    sp500: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    nasdaq: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    gold_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    btc_etf_flows_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    stablecoin_mcap_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    fed_rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    macro_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_macro_snapshot_time", "snapshot_at", unique=True),
    )


class EconomicEvent(Base):
    __tablename__ = "economic_events"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(10), default="US", server_default="US")
    impact: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_value: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(50), nullable=True)
    previous_value: Mapped[str | None] = mapped_column(String(50), nullable=True)
    surprise_direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    btc_reaction_1h_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    btc_reaction_4h_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        Index("idx_eco_scheduled", "scheduled_at"),
        Index("idx_eco_impact", "impact"),
    )
