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

from sqlalchemy import func, select

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.bot import BotTrade
from app.modules.bot import candle_source, equity, executor, strategy
from app.modules.bot.schemas import CloseReason, Direction


async def _bars_to_check(trade: BotTrade) -> list[dict]:
    """Pull recent 1m candles for the symbol that haven't been observed yet.

    Bybit symbols come from the WS-fed Redis list; Binance symbols are
    polled via REST. We only need bars after the previous tick — scanning
    the last 5 is plenty for 30s ticks against 1m bars.
    """
    candles = await candle_source.get_recent_candles(
        trade.symbol, trade.exchange, trade.market_type, limit=5
    )
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


async def _max_hold_fill(trade: BotTrade) -> Decimal:
    """Best-available exit price for a timed-out position. Uses the latest candle
    close; falls back to entry price when the symbol's stream has gone fully
    stale (truly orphaned) so the slot is still freed."""
    candle = await candle_source.get_latest_candle(
        trade.symbol, trade.exchange, trade.market_type
    )
    if candle is not None:
        px = Decimal(str(candle.get("c") or candle.get("close") or 0))
        if px > 0:
            return px
    return Decimal(str(trade.entry_price))


async def run_monitor_tick() -> dict:
    """Scheduler entry. Walks open trades, closes any whose stop/TP hit, and
    force-closes any held longer than ``bot_max_hold_hours`` (orphan cleanup)."""
    if not getattr(app_settings, "bot_enabled", False):
        return {"skipped": "disabled"}

    max_hold_hours = int(getattr(app_settings, "bot_max_hold_hours", 0) or 0)
    now = datetime.now(timezone.utc)
    closed = 0
    expired = 0
    checked = 0
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(BotTrade).where(BotTrade.status == "open"))
        ).scalars().all()
        for trade in rows:
            checked += 1
            try:
                closed_this = False
                # Iterate oldest first for deterministic close order.
                for bar in reversed(await _bars_to_check(trade)):
                    decision = _decide_close(trade, bar)
                    if decision is None:
                        continue
                    reason, fill = decision
                    await executor.close_paper_trade(
                        db, trade, exit_price=fill, reason=reason
                    )
                    closed += 1
                    closed_this = True
                    break
                if closed_this:
                    continue
                # Max-hold: positions that never touched stop/TP (often because the
                # symbol left the WS sub and candles went stale) are force-closed.
                if max_hold_hours > 0:
                    age_hours = (now - trade.entry_at).total_seconds() / 3600.0
                    if age_hours >= max_hold_hours:
                        fill = await _max_hold_fill(trade)
                        await executor.close_paper_trade(
                            db, trade, exit_price=fill, reason=CloseReason.MAX_HOLD
                        )
                        closed += 1
                        expired += 1
            except Exception as e:
                log.warning(
                    "bot_monitor_one_failed",
                    id=str(trade.id),
                    symbol=trade.symbol,
                    err=str(e),
                )
        # Reconcile the Redis concurrency counter against DB truth — a fresh
        # count, since the listener may have opened trades during this tick.
        open_now = (
            await db.execute(
                select(func.count()).select_from(BotTrade).where(BotTrade.status == "open")
            )
        ).scalar_one()
    drift = await equity.reconcile_concurrent(int(open_now))
    if drift != 0:
        log.warning("bot_concurrent_reconciled", drift=drift, actual=int(open_now))
    log.info("bot_monitor_tick", checked=checked, closed=closed, expired=expired)
    return {"checked": checked, "closed": closed, "expired": expired}


async def run_daily_anchor_reset() -> dict:
    """Cron at 00:00 UTC — snapshot equity as the new daily anchor."""
    if not getattr(app_settings, "bot_enabled", False):
        return {"skipped": "disabled"}
    from app.modules.bot import equity

    anchor = await equity.reset_daily_anchor()
    log.info("bot_daily_anchor_reset", anchor=float(anchor), at=datetime.now(timezone.utc).isoformat())
    return {"anchor": float(anchor)}
