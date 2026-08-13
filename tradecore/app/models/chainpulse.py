"""ChainPulse daily on-chain macro snapshots (Santiment)."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class ChainPulseSnapshot(Base):
    __tablename__ = "chainpulse_snapshots"

    id: Mapped[UUID] = uuid_pk()
    asset: Mapped[str] = mapped_column(String(20), nullable=False)  # "bitcoin" | "ethereum"
    mvrv: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    nvt: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    exchange_balance: Mapped[Decimal | None] = mapped_column(Numeric(30, 4), nullable=True)
    exchange_inflow: Mapped[Decimal | None] = mapped_column(Numeric(30, 4), nullable=True)
    exchange_outflow: Mapped[Decimal | None] = mapped_column(Numeric(30, 4), nullable=True)
    active_addresses: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    network_profit_loss: Mapped[Decimal | None] = mapped_column(Numeric(30, 4), nullable=True)
    regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    metric_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_chainpulse_asset_date", "asset", "metric_date", unique=True),
        Index("idx_chainpulse_snapshot", "snapshot_at"),
    )
