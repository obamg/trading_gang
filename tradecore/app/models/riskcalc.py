"""RiskCalc — calculation history."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk


class RiskCalcHistory(Base):
    __tablename__ = "riskcalc_history"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    account_balance_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    risk_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    risk_amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    stop_distance_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    position_size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    position_size_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    leverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    liquidation_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    max_loss_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    potential_profit_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    rr_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    oracle_signal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("oracle_signals.id"), nullable=True
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_riskcalc_calculated", "calculated_at"),
    )
