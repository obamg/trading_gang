"""Plan, Subscription, Invoice."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    price_monthly_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    price_yearly_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    stripe_price_id_monthly: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stripe_price_id_yearly: Mapped[str | None] = mapped_column(String(100), nullable=True)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    max_watchlist_size: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    alert_delay_seconds: Mapped[int] = mapped_column(Integer, default=300, server_default="300")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = created_at_col()


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    billing_cycle: Mapped[str] = mapped_column(String(20), default="monthly", server_default="monthly")
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[UUID | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    invoice_pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
