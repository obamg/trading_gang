"""WaveBot — position monitor.

Scheduler tick, three passes over ``bot_trades``:

  1. pending  — retrace limits: fill when a bar trades through the limit,
                cancel once expire_at passes unfilled.
  2. open     — exit management. ``fixed_tp`` keeps the v1 stop/TP scan;
                ``partial_trail`` runs the partial-take + ratcheting-trail
                state machine (strategy.step_trail_bar).
  3. reconcile — Redis concurrency counter vs DB truth (status='open' only).

Idempotent: status filters ensure a double-fired tick can't fill or close a
trade twice.

Tie-breaker when both stop and TP (or limit and stop) fall inside the same
bar: stop wins (pessimistic intra-bar fill — paper should err toward
worse-than-reality, not better, or we'll over-fit when going live).
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
from app.modules.bot.schemas import CloseReason, Direction, TrailState


async def _bars_to_check(trade: BotTrade) -> list[dict]:
    """Pull recent candles for the symbol that haven't been observed yet.

    Bybit symbols come from the WS-fed Redis list; Binance symbols are
    polled via REST. We only need bars after entry_at (placement time for
    pending rows, fill time for open ones) — scanning the last 5 is plenty
    for 30s ticks against 5m bars.
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
    """v1 fixed-TP rule: if this bar touched stop or TP, return (reason, fill)."""
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


async def _process_pending(db, trade: BotTrade, now: datetime) -> str:
    """One pending limit: returns filled | filled_stopped | cancelled | waiting."""
    direction = Direction(trade.direction)
    limit = Decimal(str(trade.limit_price))
    stop = Decimal(str(trade.stop_price))
    expire_ms = int(trade.expire_at.timestamp() * 1000) if trade.expire_at else None
    for bar in reversed(await _bars_to_check(trade)):  # oldest first
        # A bar that opened at/after expiry can't fill — the order is gone.
        if expire_ms is not None:
            ts = int(bar.get("t") or bar.get("open_time") or 0)
            if ts >= expire_ms:
                break
        high, low, _ = strategy.parse_candle(bar)
        verdict = strategy.check_pending_fill(direction, high, low, limit, stop)
        if verdict == "none":
            continue
        await executor.fill_pending_trade(db, trade)
        if verdict == "filled_stopped":
            # Pessimistic: the filling bar also swept the stop — in and out.
            await executor.close_paper_trade(
                db, trade, exit_price=stop, reason=CloseReason.STOP
            )
        return verdict
    if trade.expire_at is not None and now >= trade.expire_at:
        await executor.cancel_pending_trade(db, trade)
        return "cancelled"
    return "waiting"


async def _process_open_fixed(db, trade: BotTrade) -> bool:
    """v1 exit logic, verbatim: first bar to touch stop or TP closes the trade."""
    for bar in reversed(await _bars_to_check(trade)):  # oldest first
        decision = _decide_close(trade, bar)
        if decision is None:
            continue
        reason, fill = decision
        await executor.close_paper_trade(db, trade, exit_price=fill, reason=reason)
        return True
    return False


async def _process_open_trail(db, trade: BotTrade) -> bool:
    """partial_trail exit: partial take at partial_take_r, then a ratcheting
    trail on the runner. Persists peak/stop so state survives across ticks."""
    direction = Direction(trade.direction)
    entry = Decimal(str(trade.entry_price))
    initial_stop = Decimal(
        str(trade.initial_stop_price if trade.initial_stop_price is not None else trade.stop_price)
    )
    state = TrailState(
        stop=Decimal(str(trade.stop_price)),
        peak=Decimal(str(trade.peak_price)) if trade.peak_price is not None else entry,
        partial_taken=trade.partial_exit_at is not None,
    )
    take_r = Decimal(str(app_settings.bot_partial_take_r))
    arm_r = Decimal(str(app_settings.bot_trail_arm_r))
    dist_r = Decimal(str(app_settings.bot_trail_distance_r))

    for bar in reversed(await _bars_to_check(trade)):  # oldest first
        high, low, _ = strategy.parse_candle(bar)
        state, events = strategy.step_trail_bar(
            direction,
            entry,
            initial_stop,
            state,
            high,
            low,
            partial_take_r=take_r,
            trail_arm_r=arm_r,
            trail_distance_r=dist_r,
        )
        for kind, price in events:
            if kind == "partial":
                await executor.take_partial_profit(db, trade, exit_price=price)
            elif kind == "stop":
                await executor.close_paper_trade(
                    db, trade, exit_price=price, reason=CloseReason.STOP
                )
                return True

    # Still open — persist the ratchet state for the next tick.
    changed = False
    if state.stop != Decimal(str(trade.stop_price)):
        trade.stop_price = state.stop
        changed = True
    if trade.peak_price is None or state.peak != Decimal(str(trade.peak_price)):
        trade.peak_price = state.peak
        changed = True
    if changed:
        await db.commit()
    return False


async def run_monitor_tick() -> dict:
    """Scheduler entry. Fills/expires pending limits, manages open exits, and
    force-closes positions held longer than ``bot_max_hold_hours``."""
    if not getattr(app_settings, "bot_enabled", False):
        return {"skipped": "disabled"}

    exit_mode = str(getattr(app_settings, "bot_exit_mode", "fixed_tp") or "fixed_tp").lower()
    max_hold_hours = int(getattr(app_settings, "bot_max_hold_hours", 0) or 0)
    now = datetime.now(timezone.utc)
    checked = 0
    closed = 0
    expired = 0
    pending_checked = 0
    filled = 0
    cancelled = 0
    async with AsyncSessionLocal() as db:
        # Pass 1 — pending retrace limits.
        pending_rows = (
            await db.execute(select(BotTrade).where(BotTrade.status == "pending"))
        ).scalars().all()
        for trade in pending_rows:
            pending_checked += 1
            try:
                outcome = await _process_pending(db, trade, now)
                if outcome in ("filled", "filled_stopped"):
                    filled += 1
                    if outcome == "filled_stopped":
                        closed += 1
                elif outcome == "cancelled":
                    cancelled += 1
            except Exception as e:
                log.warning(
                    "bot_monitor_pending_failed",
                    id=str(trade.id),
                    symbol=trade.symbol,
                    err=str(e),
                )

        # Pass 2 — open positions (includes limits filled this tick).
        rows = (
            await db.execute(select(BotTrade).where(BotTrade.status == "open"))
        ).scalars().all()
        for trade in rows:
            checked += 1
            try:
                if exit_mode == "partial_trail":
                    closed_this = await _process_open_trail(db, trade)
                else:
                    closed_this = await _process_open_fixed(db, trade)
                if closed_this:
                    closed += 1
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
        # Pass 3 — reconcile the Redis concurrency counter against DB truth
        # (status='open' only; pending rows never touch the counter).
        open_now = (
            await db.execute(
                select(func.count()).select_from(BotTrade).where(BotTrade.status == "open")
            )
        ).scalar_one()
    drift = await equity.reconcile_concurrent(int(open_now))
    if drift != 0:
        log.warning("bot_concurrent_reconciled", drift=drift, actual=int(open_now))
    log.info(
        "bot_monitor_tick",
        checked=checked,
        closed=closed,
        expired=expired,
        pending=pending_checked,
        filled=filled,
        cancelled=cancelled,
    )
    return {
        "checked": checked,
        "closed": closed,
        "expired": expired,
        "pending": pending_checked,
        "filled": filled,
        "cancelled": cancelled,
    }


async def run_daily_anchor_reset() -> dict:
    """Cron at 00:00 UTC — snapshot equity as the new daily anchor."""
    if not getattr(app_settings, "bot_enabled", False):
        return {"skipped": "disabled"}
    from app.modules.bot import equity

    anchor = await equity.reset_daily_anchor()
    log.info("bot_daily_anchor_reset", anchor=float(anchor), at=datetime.now(timezone.utc).isoformat())
    return {"anchor": float(anchor)}
