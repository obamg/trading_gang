"""WaveBot — open-position monitor.

Scheduler tick that walks every open ``bot_trades`` row and closes any whose
stop or take-profit has been touched. Idempotent: ``status='open'`` filter
ensures we never close a trade twice even if the tick double-fires.

Tie-breaker when both stop and TP fall inside the same bar: stop wins
(pessimistic intra-bar fill — paper should err toward worse-than-reality, not
better, or we'll over-fit when going live).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.bot import BotTrade
from app.modules.bot import executor, strategy
from app.modules.bot.schemas import CloseReason, Direction
from app.services import redis_service


async def _bars_to_check(trade: BotTrade) -> list[dict]:
    """Pull recent 1m candles for the symbol that haven't been observed yet.

    The Redis stream keeps the last 50 candles. We only need bars that
    closed AFTER the previous monitor tick, but since ticks run every 30s
    and bars are 1m, scanning the last 5 bars is more than enough.
    """
    candles = await redis_service.get_candles(trade.symbol, limit=5)
    # Filter to bars on/after entry_at — earlier ones happened before the trade.
    entry_ms = int(trade.entry_at.timestamp() * 1000)
    fresh = []
    for c in candles:
        ts = int(c.get("t") or c.get("open_time") or 0)
        if ts >= entry_ms:
            fresh.append(c)
    return fresh


def _decide_close(trade: BotTrade, bar: dict) -> tuple[CloseReason, Decimal] | None:
    """If this bar touched stop or TP, return (reason, fill_price)."""
    direction = Direction(trade.direction)
    high, low, _ = strategy.parse_candle(bar)
    if strategy.is_stop_hit(direction, high, low, Decimal(str(trade.stop_price))):
        return (CloseReason.STOP, Decimal(str(trade.stop_price)))
    if strategy.is_tp_hit(direction, high, low, Decimal(str(trade.take_profit_price))):
        return (CloseReason.TP, Decimal(str(trade.take_profit_price)))
    return None


async def run_monitor_tick() -> dict:
    """Scheduler entry. Walks open trades, closes any whose stop/TP hit."""
    if not getattr(app_settings, "bot_enabled", False):
        return {"skipped": "disabled"}

    closed = 0
    checked = 0
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(BotTrade).where(BotTrade.status == "open"))
        ).scalars().all()
        for trade in rows:
            checked += 1
            try:
                bars = await _bars_to_check(trade)
                if not bars:
                    continue
                # Iterate oldest first for deterministic close order.
                for bar in reversed(bars):
                    decision = _decide_close(trade, bar)
                    if decision is None:
                        continue
                    reason, fill = decision
                    await executor.close_paper_trade(
                        db, trade, exit_price=fill, reason=reason
                    )
                    closed += 1
                    break
            except Exception as e:
                log.warning(
                    "bot_monitor_one_failed",
                    id=str(trade.id),
                    symbol=trade.symbol,
                    err=str(e),
                )
    log.info("bot_monitor_tick", checked=checked, closed=closed)
    return {"checked": checked, "closed": closed}


async def run_daily_anchor_reset() -> dict:
    """Cron at 00:00 UTC — snapshot equity as the new daily anchor."""
    if not getattr(app_settings, "bot_enabled", False):
        return {"skipped": "disabled"}
    from app.modules.bot import equity

    anchor = await equity.reset_daily_anchor()
    log.info("bot_daily_anchor_reset", anchor=float(anchor), at=datetime.now(timezone.utc).isoformat())
    return {"anchor": float(anchor)}
