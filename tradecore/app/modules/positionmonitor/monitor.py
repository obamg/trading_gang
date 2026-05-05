"""Position Monitor — watches open trades for SL/TP hits and trailing stop updates.

Runs every 30 seconds via scheduler. For each open trade:
  1. Fetches current price from Redis candle stream
  2. Checks if price hit stop_loss or take_profit
  3. Publishes exit signal alerts via WebSocket + Redis pubsub
  4. Supports trailing stops (auto-tightens stop as price moves favorably)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.tradelog import Trade
from app.services import redis_service


async def check_open_positions() -> int:
    """Main loop: check all open trades against current prices."""
    checked = 0
    async with AsyncSessionLocal() as db:
        trades = (
            await db.execute(
                select(Trade).where(Trade.status == "open")
            )
        ).scalars().all()

        if not trades:
            return 0

        for trade in trades:
            try:
                hit = await _check_trade(db, trade)
                if hit:
                    checked += 1
            except Exception as e:
                log.error("positionmonitor_check_failed", trade_id=str(trade.id), err=str(e))

        await db.commit()

    if checked:
        log.info("positionmonitor_alerts_fired", count=checked)
    return checked


async def _check_trade(db: AsyncSession, trade: Trade) -> bool:
    """Check a single trade. Returns True if an alert was fired."""
    candle = await redis_service.get_latest_candle(trade.symbol)
    if not candle:
        return False

    current_price = float(candle.get("c") or candle.get("close") or 0)
    if current_price <= 0:
        return False

    entry = float(trade.entry_price)
    sl = float(trade.stop_loss_price) if trade.stop_loss_price else None
    tp = float(trade.take_profit_price) if trade.take_profit_price else None
    side = trade.side.lower()

    trigger: str | None = None

    # Check stop loss hit
    if sl:
        if side == "long" and current_price <= sl:
            trigger = "stop_loss_hit"
        elif side == "short" and current_price >= sl:
            trigger = "stop_loss_hit"

    # Check take profit hit
    if tp and not trigger:
        if side == "long" and current_price >= tp:
            trigger = "take_profit_hit"
        elif side == "short" and current_price <= tp:
            trigger = "take_profit_hit"

    if not trigger:
        # Trailing stop logic: tighten stop if price moved favorably
        await _update_trailing_stop(db, trade, current_price, side)
        return False

    # Fire alert
    pnl_pct = _unrealized_pnl_pct(side, entry, current_price)
    alert = {
        "module": "positionmonitor",
        "type": "exit_signal",
        "trade_id": str(trade.id),
        "user_id": str(trade.user_id),
        "symbol": trade.symbol,
        "side": side,
        "entry_price": entry,
        "current_price": current_price,
        "trigger": trigger,
        "unrealized_pnl_pct": round(pnl_pct, 2),
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_service.publish_alert("positionmonitor", alert)
    log.info(
        "positionmonitor_exit_signal",
        symbol=trade.symbol,
        trigger=trigger,
        pnl_pct=round(pnl_pct, 2),
    )
    return True


async def _update_trailing_stop(
    db: AsyncSession, trade: Trade, current_price: float, side: str
) -> None:
    """Tighten stop loss as price moves in trader's favor.

    Trail distance = 50% of the original stop distance.
    Only moves stop in favorable direction (never widens).
    """
    sl = float(trade.stop_loss_price) if trade.stop_loss_price else None
    entry = float(trade.entry_price)
    if not sl:
        return

    original_stop_dist = abs(entry - sl)
    trail_dist = original_stop_dist * 0.5

    if side == "long":
        # Price moved up — trail the stop up
        new_stop = current_price - trail_dist
        if new_stop > sl:
            trade.stop_loss_price = Decimal(str(round(new_stop, 8)))
    else:
        # Price moved down (short) — trail the stop down
        new_stop = current_price + trail_dist
        if new_stop < sl:
            trade.stop_loss_price = Decimal(str(round(new_stop, 8)))


def _unrealized_pnl_pct(side: str, entry: float, current: float) -> float:
    if entry <= 0:
        return 0.0
    if side == "long":
        return (current - entry) / entry * 100
    return (entry - current) / entry * 100


async def run_position_check() -> None:
    """Scheduler entry point."""
    try:
        await check_open_positions()
    except Exception as e:
        log.error("positionmonitor_run_failed", err=str(e))


__all__ = ["check_open_positions", "run_position_check"]
