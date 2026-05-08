"""Idempotent upsert of paired TradeRecord -> Trade rows.

Keyed on (exchange, exchange_trade_id) — the partial unique index in
migration 010 enforces this at the DB level. We check first to avoid raising
on conflict; a true race would just update.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tradelog import Trade
from app.services.exchanges.pairing import TradeRecord


async def upsert_trades(
    db: AsyncSession,
    user_id: UUID,
    records: list[TradeRecord],
) -> dict:
    """Insert each record if (exchange, exchange_trade_id) isn't present.

    Returns counters: {inserted, skipped}. Open trades aren't produced by
    pair_fills, so every record has status='closed'.
    """
    inserted = 0
    skipped = 0
    for rec in records:
        existing = (
            await db.execute(
                select(Trade.id).where(
                    and_(
                        Trade.exchange == rec.exchange,
                        Trade.exchange_trade_id == rec.exchange_trade_id,
                    )
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue

        row = Trade(
            user_id=user_id,
            symbol=rec.symbol,
            asset_type="futures",
            side=rec.side,
            status="closed",
            is_paper=False,
            entry_price=Decimal(str(round(rec.entry_price, 8))),
            entry_at=rec.entry_at,
            exit_price=Decimal(str(round(rec.exit_price, 8))),
            exit_at=rec.exit_at,
            size=Decimal(str(round(rec.size, 8))),
            size_usd=Decimal(str(round(rec.entry_price * rec.size, 2))),
            leverage=Decimal("1"),  # not exposed by userTrades
            pnl_usd=Decimal(str(round(rec.pnl_usd, 2))) if rec.pnl_usd is not None else None,
            fees_usd=Decimal(str(round(rec.fees_usd, 2))),
            net_pnl_usd=Decimal(str(round(rec.net_pnl_usd, 2))) if rec.net_pnl_usd is not None else None,
            hold_duration_seconds=int((rec.exit_at - rec.entry_at).total_seconds()),
            exit_reason=rec.exit_reason,
            exchange=rec.exchange,
            exchange_trade_id=rec.exchange_trade_id,
        )
        if rec.entry_price > 0 and rec.pnl_usd is not None:
            notional = rec.entry_price * rec.size
            if notional > 0:
                row.pnl_pct = Decimal(str(round(rec.pnl_usd / notional * 100, 4)))
        db.add(row)
        inserted += 1

    if inserted:
        await db.commit()
    return {"inserted": inserted, "skipped": skipped}


__all__ = ["upsert_trades"]
