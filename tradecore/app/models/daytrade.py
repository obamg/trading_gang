"""DayTrade — one row per triggered opening-range-breakout plan.

Paper only: nothing here is ever sent to an exchange. `status` is the paper
outcome (open → target | stop | time), or `blocked` when the risk gate refused
the plan. Blocked rows are kept so the journal shows the gate working, and are
excluded from the forward-test scoreboard.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class DayTradePlan(Base):
    __tablename__ = "daytrade_plans"

    id: Mapped[UUID] = uuid_pk()
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    session_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Playbook.version at the time: the scoreboard only counts the current rules.
    playbook: Mapped[str] = mapped_column(String(12), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)      # LONG | SHORT
    status: Mapped[str] = mapped_column(String(10), nullable=False)   # open|target|stop|time|blocked

    entry: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    stop: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    target: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    range_high: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    range_low: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    stop_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    vol_x: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)

    # {"reasons": [...gate refusals], "sizing": {...riskcalc output at the reference account}}
    checks: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # Advisory only — logged so "rules alone" vs "rules + AI" can be compared.
    ai_verdict: Mapped[str | None] = mapped_column(String(10), nullable=True)  # take | skip
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    pnl_r: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        # One plan per symbol per session: makes the scanner idempotent.
        UniqueConstraint("exchange", "symbol", "session_at", name="uq_daytrade_plan_session"),
        Index("idx_daytrade_plans_triggered", "triggered_at"),
        Index("idx_daytrade_plans_status", "status"),
    )
