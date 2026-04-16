"""PerformanceCore — aggregated analytics."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshots"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    total_trades: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    winning_trades: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    losing_trades: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    breakeven_trades: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    avg_win_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    avg_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    avg_rr_achieved: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    expectancy: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    total_fees_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    net_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    max_drawdown_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    max_consecutive_losses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_trade_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    worst_trade_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    best_setup: Mapped[str | None] = mapped_column(String(100), nullable=True)
    starting_balance_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    ending_balance_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_perf_user_period", "user_id", "period"),
        Index("idx_perf_computed", "computed_at"),
    )


class SignalPerformance(Base):
    __tablename__ = "signal_performance"

    id: Mapped[UUID] = uuid_pk()
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    total_signals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    correct_1h: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    correct_4h: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    accuracy_1h_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    accuracy_4h_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    avg_move_1h_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    avg_move_4h_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_signal_perf_module_symbol", "module", "symbol", "computed_at", unique=True),
    )
