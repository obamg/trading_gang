"""WaveWatch — continuous surveillance of Bybit Innovation Zone assets.

One row per (exchange, market_type, symbol) currently in the innovation
universe. Universe refresh maintains the table — when a symbol leaves the
innovation tier its row is marked status='removed' rather than deleted, so
historical alerts remain queryable.

State that changes every tick (current score, last alert timestamp) lives
in Redis to keep DB write volume sane.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class WaveAsset(Base):
    """One innovation-flagged asset under continuous wave surveillance."""

    __tablename__ = "wave_assets"

    id: Mapped[UUID] = uuid_pk()

    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    market_type: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )  # active | removed

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Snapshotted on each detector tick so the API can render a leaderboard
    # without recomputing from scratch. Wall-clock truth lives in Redis.
    latest_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    latest_score_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint("exchange", "market_type", "symbol", name="uq_wave_asset"),
        Index("idx_wave_asset_status", "status"),
        Index("idx_wave_asset_score", "latest_score"),
    )
