"""CMCPulse — market-regime context snapshots stamped onto bot trades."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class TradeContextSnapshot(Base):
    """What the market 'mood' looked like at the moment a bot trade opened.

    Observational only — nothing reads these to make decisions. The purpose
    is the evaluation gate: when a strategy reaches its n, these columns let
    us test hypotheses like "entries taken while the symbol was trending on
    CMC do worse" against contemporaneous data instead of reconstruction.
    """

    __tablename__ = "trade_context_snapshots"

    id: Mapped[UUID] = uuid_pk()
    trade_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    # CMC Fear & Greed index 0-100 + its label, from the keyless official API.
    fear_greed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fear_greed_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 1-based position in CMC's top-search list at entry; NULL = not trending.
    trending_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # CMC's 24h price change % for the coin at entry (only when trending).
    trending_change_24h: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_trade_context_strategy", "strategy"),
        Index("idx_trade_context_captured", "captured_at"),
    )
