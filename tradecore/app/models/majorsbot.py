"""MajorsBot — paper trades for the fixed-universe majors bot.

One table, ``majorsbot_trades`` — mirrors ``bot_trades`` (including the v2
retrace/partial-trail fields) plus a ``strategy`` discriminator:

  volevent     F4-A vol-event momentum retrace: limit entry at the 50% retrace
               of the trigger bar, pending → open (filled) | cancelled (expired
               after 6h unfilled); 50% off at +1.5R, runner on a 1R peak-trail.
  fundingfade  F1-B funding-extreme fade: market entry at the funding-event bar
               open, exits on the 40–60th-pctile band renormalization or a
               2×ATR peak-trail after +1R.

Lifecycle: volevent rows go pending → open → closed (or pending → cancelled);
fundingfade rows open directly. Close sets close_reason + close_price +
closed_at + realized_pnl_usd / realized_r / realized_r_net / fees_usd /
funding_pnl_usd — same net-R accounting as WaveBot.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class MajorsBotTrade(Base):
    __tablename__ = "majorsbot_trades"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)  # bybit
    market_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # perp
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # long | short
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)  # volevent | fundingfade
    # Signal time: trigger-bar open (volevent) / funding-event ts (fundingfade).
    signal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entry_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Open time of the 1h bar the fill belongs to — the bar-walk resume point
    # and the exclusive lower bound for funding-event accrual.
    entry_bar_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Trigger-bar extremes (volevent stop anchor). Null on fundingfade rows —
    # its stop is ATR-derived, not bar-derived.
    signal_high: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    signal_low: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(40, 18), nullable=False)
    paper_equity_at_entry: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)

    stop_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    # Null on fundingfade — no fixed TP, only the band exit / trail.
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
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

    # Fundingfade context at entry — the event rate and its trailing-90-event
    # percentile. Null on volevent rows.
    funding_rate_at_entry: Mapped[Decimal | None] = mapped_column(Numeric(12, 9), nullable=True)
    funding_pctile_at_entry: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    # v2 fields — pending-limit leg + partial-trail state (mirrors bot_trades).
    entry_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)  # limit | market
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Stop at entry, frozen — stop_price ratchets under the trail, and R must
    # always be measured against the risk taken at entry.
    initial_stop_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    peak_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    partial_exit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    # Stamped with the partial bar's CLOSE time (not wall clock) so funding
    # accrual can split full-qty vs runner-qty legs at the right boundary.
    partial_exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    partial_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    # qty stays the ORIGINAL total; partial_qty is the closed portion.
    partial_qty: Mapped[Decimal | None] = mapped_column(Numeric(40, 18), nullable=True)

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        Index("idx_majorsbot_trade_status_symbol", "status", "symbol"),
        Index("idx_majorsbot_trade_opened", "entry_at"),
        Index("idx_majorsbot_trade_strategy", "strategy"),
    )
