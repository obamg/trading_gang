"""Alert delivery and cooldowns."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class UserAlertDelivery(Base):
    __tablename__ = "user_alert_deliveries"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    alert_ref_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    alert_ref_table: Mapped[str] = mapped_column(String(100), nullable=False)
    delivered_via: Mapped[str] = mapped_column(String(50), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    was_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_delivery_user", "user_id"),
        Index("idx_delivery_module", "module"),
        Index("idx_delivery_delivered", "delivered_at"),
    )


class AlertCooldown(Base):
    __tablename__ = "alert_cooldowns"

    id: Mapped[UUID] = uuid_pk()
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    last_alert_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_cooldown_symbol_module", "symbol", "module", unique=True),
        Index("idx_cooldown_until", "cooldown_until"),
    )
