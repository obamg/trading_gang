"""GemRadar — small-cap alerts."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class GemRadarAlert(Base):
    __tablename__ = "gemradar_alerts"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contract_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    chain: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dex: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_cex_listed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cex_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    price_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    price_change_period_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_usd_current: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    volume_usd_avg: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    volume_24h_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    volume_mcap_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    market_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    liquidity_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 2), nullable=True)
    social_velocity: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    price_change_1h_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    price_change_24h_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    risk_score: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_score_numeric: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_contract_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_liquidity_locked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_mint_function: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    top10_wallet_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    contract_age_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_flags: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_gemradar_detected", "detected_at"),
        Index("idx_gemradar_risk", "risk_score"),
        Index("idx_gemradar_chain", "chain"),
    )
