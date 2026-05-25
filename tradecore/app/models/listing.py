"""ListingWatch — newly-listed perp/spot tokens on Bybit/Binance/OKX with
post-listing signal tracking.

Lifecycle:
  detected (T-0)  →  watching (T+0..T+4h)  →  ended (T+4h)

The watcher reads market data from the same Redis schema as everything else
(``candles:{sym}``, ``trades:{sym}``, ``bookticker:{sym}``) — no exchange-
specific code lives in this module.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class NewListingEvent(Base):
    """One row per detected new listing — Bybit perp, Binance spot/perp, OKX
    spot/perp. Created at T-0 by the detector, updated by the watcher as
    signals fire."""

    __tablename__ = "new_listing_events"

    id: Mapped[UUID] = uuid_pk()

    # Where + what was listed.
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)  # bybit | binance | okx
    market_type: Mapped[str] = mapped_column(String(10), nullable=False)  # spot | perp
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)  # exchange-native, e.g. "PEPEUSDT"
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)  # "PEPE"
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)  # "USDT"

    # Cross-listing flag — base_asset already trades elsewhere when this row
    # is created. Tells the watcher to use a different signal config (the
    # token has prior price discovery, so "initial squeeze" is meaningless,
    # but "Binance pump" is highly relevant).
    is_cross_listing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    other_exchanges: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Bybit Innovation Zone tier — high volatility / risk. Set when the
    # symbol itself is innovation-flagged on Bybit, OR when the base asset
    # has an innovation sibling in the same detection snapshot (e.g. a
    # Bybit perp whose spot pair is in Innovation Zone).
    innovation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # ^ JSON list of {"exchange": "bybit", "market_type": "perp", "symbol": "PEPEUSDT"}

    # Listing timing.
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ^ exchange-reported listing time when available; falls back to detected_at.
    watcher_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # T-0 snapshot — captured by the watcher on first data tick.
    t0_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    t0_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Rolling state (last refresh by watcher).
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    high_15m: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    low_15m: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    high_1h: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    low_1h: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    last_funding_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="watching"
    )  # watching | ended | error

    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint("exchange", "market_type", "symbol", name="uq_listing_event"),
        Index("idx_listing_event_status", "status"),
        Index("idx_listing_event_detected", "detected_at"),
        Index("idx_listing_event_base", "base_asset"),
    )


class ListingSignal(Base):
    """Typed signal fired by the listingwatch detector — one row per emit.
    Provides a queryable history for retrospection ("did the pump_fade signal
    we fired on TOKEN actually mark the top?")."""

    __tablename__ = "listing_signals"

    id: Mapped[UUID] = uuid_pk()
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("new_listing_events.id", ondelete="CASCADE"), nullable=False
    )

    signal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # pump_fade | breakout_long | initial_squeeze | funding_extreme | floor_held | listing_detected

    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # long | short | neutral
    conviction: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)  # 0..1

    price_at_emit: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    seconds_since_t0: Mapped[int | None] = mapped_column(Integer, nullable=True)

    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # ^ signal-specific stats: {volume_z, cvd, funding_pct, hh_15m, etc.}

    emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_listing_signal_listing", "listing_id"),
        Index("idx_listing_signal_emitted", "emitted_at"),
        Index("idx_listing_signal_type", "signal_type"),
    )
