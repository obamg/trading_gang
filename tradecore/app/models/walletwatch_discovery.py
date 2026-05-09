"""WalletWatch discovery — PnL-based wallet scoring for auto-discovery.

Three tables:

  discovery_tokens     Tokens we're currently scoring (refreshed periodically
                       from trending sources + tokens with observed buy
                       activity).

  wallet_token_pnl     One row per (wallet, chain, token) — cost basis,
                       realized + unrealized PnL, multiple.

  wallet_pnl_score     Per-wallet aggregate rolled up across all scored
                       tokens. Drives the discovery leaderboard. When a
                       wallet crosses thresholds it gets promoted into
                       whale_entities (manual review for now; auto-promote
                       in a later phase).
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class DiscoveryToken(Base):
    __tablename__ = "discovery_tokens"

    id: Mapped[UUID] = uuid_pk()
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    address: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 'cg_top_gainers' | 'observed_swaps' | 'manual'
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_pool_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_at_discovery_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint("chain", "address", name="uq_discovery_token"),
        Index("idx_discovery_token_unscored", "last_scored_at"),
    )


class WalletTokenPnl(Base):
    __tablename__ = "wallet_token_pnl"

    id: Mapped[UUID] = uuid_pk()
    wallet_address: Mapped[str] = mapped_column(String(100), nullable=False)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    token_address: Mapped[str] = mapped_column(String(100), nullable=False)
    token_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)

    total_buy_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")
    total_buy_amount: Mapped[Decimal] = mapped_column(Numeric(40, 18), default=Decimal("0"), server_default="0")
    total_sell_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")
    total_sell_amount: Mapped[Decimal] = mapped_column(Numeric(40, 18), default=Decimal("0"), server_default="0")

    current_balance: Mapped[Decimal] = mapped_column(Numeric(40, 18), default=Decimal("0"), server_default="0")
    current_value_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")

    realized_pnl_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")
    unrealized_pnl_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")
    # (realized + unrealized + 0) / cost_basis. NULL when no buys.
    multiple: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    first_buy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint("wallet_address", "chain", "token_address", name="uq_wallet_token_pnl"),
        Index("idx_wallet_token_pnl_wallet", "wallet_address"),
        Index("idx_wallet_token_pnl_token", "chain", "token_address"),
        Index("idx_wallet_token_pnl_multiple", "multiple"),
    )


class WalletPnlScore(Base):
    __tablename__ = "wallet_pnl_score"

    id: Mapped[UUID] = uuid_pk()
    wallet_address: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)

    total_realized_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")
    total_unrealized_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")
    total_cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"), server_default="0")

    win_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    loss_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    win_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0"), server_default="0")

    avg_multiple: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"), server_default="0")
    best_multiple: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"), server_default="0")
    token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Combined ranking metric — higher = better. Computed in aggregator.
    discovery_score: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), server_default="0")

    last_scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("whale_entities.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        Index("idx_wallet_pnl_score_score", "discovery_score"),
        Index("idx_wallet_pnl_score_chain", "chain"),
        Index("idx_wallet_pnl_score_promoted", "promoted_at"),
    )
