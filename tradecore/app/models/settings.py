"""UserSettings, Watchlist."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Notifications
    telegram_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    email_alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # RadarX
    radarx_zscore_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("3.0"), server_default="3.0")
    radarx_ratio_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("4.0"), server_default="4.0")
    radarx_min_volume_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("10000000"), server_default="10000000")
    radarx_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    radarx_timeframe: Mapped[str] = mapped_column(String(10), default="5m", server_default="5m")
    # WhaleRadar
    whaleradar_min_trade_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("300000"), server_default="300000")
    whaleradar_min_onchain_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("500000"), server_default="500000")
    # GemRadar
    gemradar_min_mcap_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("1000000"), server_default="1000000")
    gemradar_max_mcap_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("100000000"), server_default="100000000")
    gemradar_min_price_change: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("10.0"), server_default="10.0")
    gemradar_risk_tolerance: Mapped[str] = mapped_column(String(20), default="medium", server_default="medium")
    # Oracle
    oracle_min_score: Mapped[int] = mapped_column(Integer, default=65, server_default="65")
    oracle_min_confluence: Mapped[int] = mapped_column(Integer, default=4, server_default="4")
    oracle_paper_mode: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    oracle_auto_execute: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    oracle_max_daily_trades: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    oracle_max_account_risk_pct: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("2.0"), server_default="2.0")
    oracle_daily_loss_limit_pct: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("5.0"), server_default="5.0")
    # Oracle weights
    weight_macropulse: Mapped[int] = mapped_column(Integer, default=25, server_default="25")
    weight_whaleradar: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    weight_radarx: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    weight_liquidmap: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    weight_sentimentpulse: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    weight_gemradar: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    # General
    default_account_balance_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    risk_per_trade_pct: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.0"), server_default="1.0")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", server_default="UTC")
    theme: Mapped[str] = mapped_column(String(20), default="dark", server_default="dark")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbols: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list, server_default=text("'{}'"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
