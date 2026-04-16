"""TradeLog service — create/update/close trades, compute P&L.

Kept as plain async functions; the router calls these directly. All P&L
math is done once on close so subsequent reads are cheap.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tradelog import Trade, TradeTag


def _direction_mul(side: str) -> int:
    return 1 if side.lower() == "long" else -1


def _compute_pnl(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    size: float,
    fees_usd: float | None,
    stop_loss_price: float | None,
) -> dict:
    mul = _direction_mul(side)
    pnl_usd = (exit_price - entry_price) * size * mul
    fees = float(fees_usd or 0)
    net = pnl_usd - fees
    notional = entry_price * size
    pnl_pct = (pnl_usd / notional * 100) if notional else 0.0
    r_multiple: float | None = None
    if stop_loss_price is not None and entry_price != stop_loss_price:
        risk_per_unit = abs(entry_price - stop_loss_price)
        risk_amount = risk_per_unit * size
        if risk_amount > 0:
            r_multiple = net / risk_amount
    return {
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 4),
        "fees_usd": round(fees, 2) if fees_usd is not None else None,
        "net_pnl_usd": round(net, 2),
        "r_multiple": round(r_multiple, 2) if r_multiple is not None else None,
    }


async def create_trade(db: AsyncSession, user_id: UUID, data: dict) -> Trade:
    entry_price = float(data["entry_price"])
    size = float(data["size"])
    side = str(data.get("side", "long")).lower()
    entry_at_raw = data.get("entry_at")
    if isinstance(entry_at_raw, datetime):
        entry_at = entry_at_raw if entry_at_raw.tzinfo else entry_at_raw.replace(tzinfo=timezone.utc)
    else:
        entry_at = datetime.now(timezone.utc)

    trade = Trade(
        user_id=user_id,
        symbol=str(data["symbol"]).upper(),
        asset_type=str(data.get("asset_type", "futures")),
        side=side,
        status=str(data.get("status", "open")),
        is_paper=bool(data.get("is_paper", False)),
        entry_price=Decimal(str(entry_price)),
        entry_at=entry_at,
        size=Decimal(str(size)),
        size_usd=Decimal(str(round(entry_price * size, 2))),
        leverage=Decimal(str(data.get("leverage", 1))),
        stop_loss_price=Decimal(str(data["stop_loss_price"])) if data.get("stop_loss_price") else None,
        take_profit_price=Decimal(str(data["take_profit_price"])) if data.get("take_profit_price") else None,
        setup_name=data.get("setup_name"),
        notes=data.get("notes"),
        emotion=data.get("emotion"),
        followed_oracle=bool(data.get("followed_oracle", False)),
        oracle_signal_id=data.get("oracle_signal_id"),
        exchange=data.get("exchange"),
        exchange_trade_id=data.get("exchange_trade_id"),
    )
    db.add(trade)
    await db.commit()
    await db.refresh(trade)

    for tag in data.get("tags") or []:
        db.add(TradeTag(trade_id=trade.id, tag=str(tag)))
    if data.get("tags"):
        await db.commit()

    return trade


async def update_trade(db: AsyncSession, user_id: UUID, trade_id: UUID, updates: dict) -> Trade | None:
    row = (
        await db.execute(
            select(Trade).where(Trade.id == trade_id, Trade.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.status == "deleted":
        return None

    # Simple field updates
    for field in ("notes", "setup_name", "emotion", "followed_oracle",
                  "take_profit_price", "stop_loss_price"):
        if field in updates and updates[field] is not None:
            val = updates[field]
            if field in ("take_profit_price", "stop_loss_price"):
                val = Decimal(str(val))
            setattr(row, field, val)

    # Closing the trade
    if updates.get("exit_price") is not None:
        exit_price = float(updates["exit_price"])
        exit_at_raw = updates.get("exit_at")
        if isinstance(exit_at_raw, datetime):
            exit_at = exit_at_raw if exit_at_raw.tzinfo else exit_at_raw.replace(tzinfo=timezone.utc)
        else:
            exit_at = datetime.now(timezone.utc)
        fees = updates.get("fees_usd")
        pnl = _compute_pnl(
            side=row.side,
            entry_price=float(row.entry_price),
            exit_price=exit_price,
            size=float(row.size),
            fees_usd=float(fees) if fees is not None else None,
            stop_loss_price=float(row.stop_loss_price) if row.stop_loss_price else None,
        )
        row.exit_price = Decimal(str(exit_price))
        row.exit_at = exit_at
        row.pnl_usd = Decimal(str(pnl["pnl_usd"]))
        row.pnl_pct = Decimal(str(pnl["pnl_pct"]))
        if pnl["fees_usd"] is not None:
            row.fees_usd = Decimal(str(pnl["fees_usd"]))
        row.net_pnl_usd = Decimal(str(pnl["net_pnl_usd"]))
        if pnl["r_multiple"] is not None:
            row.r_multiple = Decimal(str(pnl["r_multiple"]))
        row.status = "closed"

    if "tags_add" in updates and updates["tags_add"]:
        for tag in updates["tags_add"]:
            db.add(TradeTag(trade_id=row.id, tag=str(tag)))

    await db.commit()
    await db.refresh(row)
    return row


async def soft_delete(db: AsyncSession, user_id: UUID, trade_id: UUID) -> bool:
    row = (
        await db.execute(
            select(Trade).where(Trade.id == trade_id, Trade.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    row.status = "deleted"
    await db.commit()
    return True


def serialize(row: Trade, tags: list[str] | None = None) -> dict:
    return {
        "id": str(row.id),
        "symbol": row.symbol,
        "asset_type": row.asset_type,
        "side": row.side,
        "status": row.status,
        "is_paper": row.is_paper,
        "entry_price": float(row.entry_price),
        "entry_at": row.entry_at.isoformat(),
        "exit_price": float(row.exit_price) if row.exit_price else None,
        "exit_at": row.exit_at.isoformat() if row.exit_at else None,
        "size": float(row.size),
        "size_usd": float(row.size_usd) if row.size_usd else None,
        "leverage": float(row.leverage),
        "stop_loss_price": float(row.stop_loss_price) if row.stop_loss_price else None,
        "take_profit_price": float(row.take_profit_price) if row.take_profit_price else None,
        "pnl_usd": float(row.pnl_usd) if row.pnl_usd else None,
        "pnl_pct": float(row.pnl_pct) if row.pnl_pct else None,
        "fees_usd": float(row.fees_usd) if row.fees_usd else None,
        "net_pnl_usd": float(row.net_pnl_usd) if row.net_pnl_usd else None,
        "r_multiple": float(row.r_multiple) if row.r_multiple else None,
        "setup_name": row.setup_name,
        "notes": row.notes,
        "emotion": row.emotion,
        "followed_oracle": row.followed_oracle,
        "oracle_signal_id": str(row.oracle_signal_id) if row.oracle_signal_id else None,
        "exchange": row.exchange,
        "exchange_trade_id": row.exchange_trade_id,
        "tags": tags or [],
    }


__all__ = ["create_trade", "update_trade", "soft_delete", "serialize"]
