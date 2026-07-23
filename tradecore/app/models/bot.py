"""WaveBot — paper-trading bot trades + skipped-signal log.

Two tables:

  bot_trades             One row per paper trade. Lifecycle:
                         chase entry:   status=open → status=closed
                         retrace entry: status=pending → open (limit filled) → closed,
                                        or pending → cancelled (expired unfilled).
                         Close sets close_reason + close_price + closed_at +
                         realized_pnl_usd.

  bot_skipped_signals    One row per ``wave_active`` alert that the bot saw but
                         did not act on. Critical for tuning: without this we
                         can't tell whether the vetoes are correctly filtering
                         junk or cutting off winners.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class BotTrade(Base):
    __tablename__ = "bot_trades"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    market_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # perp | spot
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # long | short
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entry_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_high: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    signal_low: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(40, 18), nullable=False)
    paper_equity_at_entry: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)

    stop_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    take_profit_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    realized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    realized_r: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    # realized_r is price-move only; realized_r_net is net PnL (fees, slippage,
    # funding) over dollar-risk at entry — the number scale-up decisions use.
    realized_r_net: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    fees_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    funding_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)

    oracle_score_at_entry: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    # Rolling USD turnover at entry — the liquidity axis for calibrating
    # bot_min_turnover_usd against realized outcomes.
    entry_turnover_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    vol_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    funding_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 5), nullable=True)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(8, 5), nullable=True)

    # V2 retrace/partial-trail fields — null on v1 (chase / fixed_tp) rows.
    entry_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)  # chase | retrace
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Stop at entry, frozen — stop_price ratchets under partial_trail, and R
    # must always be measured against the risk taken at entry.
    initial_stop_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    peak_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    partial_exit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    partial_exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    partial_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    # qty stays the ORIGINAL total; partial_qty is the closed portion.
    partial_qty: Mapped[Decimal | None] = mapped_column(Numeric(40, 18), nullable=True)

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        Index("idx_bot_trade_status_symbol", "status", "symbol"),
        Index("idx_bot_trade_opened", "entry_at"),
    )


class BotSkippedSignal(Base):
    __tablename__ = "bot_skipped_signals"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    alert_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    skip_reason: Mapped[str] = mapped_column(String(30), nullable=False)
    oracle_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_bot_skipped_reason", "skip_reason", "alert_detected_at"),
    )
