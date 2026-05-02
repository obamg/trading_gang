"""FlowPulse — order flow signal snapshots."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class FlowSignal(Base):
    __tablename__ = "flow_signals"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)

    # Order book imbalance
    bid_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    ask_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    book_imbalance: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    # Taker buy/sell ratio
    taker_buy_vol: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    taker_sell_vol: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    taker_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    # Top trader long/short (position-based)
    top_long_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    top_short_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    # Composite
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    intensity: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_flow_symbol_time", "symbol", "snapshot_at"),
        Index("idx_flow_snapshot", "snapshot_at"),
    )
