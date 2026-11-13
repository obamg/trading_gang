"""XsTrend — one row per week of the paper cross-sectional trend portfolio.

Paper only: nothing here is ever sent to an exchange. `status` is
open → settled, or `skipped` when fewer than 30 coins were eligible.
Returns are fractions of gross capital (0.01 = 1%).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col


class XsTrendWeek(Base):
    __tablename__ = "xstrend_weeks"

    week_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    # rules.VERSION at the time: the scoreboard only counts the current rules.
    rules: Mapped[str] = mapped_column(String(12), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # open|settled|skipped
    universe: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    weights: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    # At settlement: {"returns": {sym: r}, "drifted": {sym: w}, "funding": {sym: rate_sum}}
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    gross: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    funding: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    net: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    mkt: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
