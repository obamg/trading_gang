"""Whale Entity Profiling — known wallets and their behaviors."""
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class WhaleEntity(Base):
    __tablename__ = "whale_entities"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    total_transfers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_volume_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2), default=Decimal("0"), server_default="0")
    avg_move_after_1h_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    avg_move_after_4h_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    conviction_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        Index("idx_whale_entity_type", "entity_type"),
        Index("idx_whale_entity_conviction", "conviction_score"),
    )


class WhaleEntityAddress(Base):
    __tablename__ = "whale_entity_addresses"

    id: Mapped[UUID] = uuid_pk()
    entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("whale_entities.id", ondelete="CASCADE"), nullable=False
    )
    address: Mapped[str] = mapped_column(String(100), nullable=False)
    chain: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    # False takes the address out of the WalletWatch scan without losing the
    # row or its PnL-discovery provenance. Set by the high-frequency pruner.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deactivated_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_whale_addr_entity", "entity_id"),
        Index("idx_whale_addr_address", "address", unique=True),
        Index("idx_whale_addr_active", "is_active"),
    )
