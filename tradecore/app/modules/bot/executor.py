"""WaveBot — paper executor.

Persists a TradePlan as a ``bot_trades`` row, bumps the concurrent counter,
emits an ``alerts:bot`` event so the UI sees the new position instantly.

V2 adds the pending-limit leg (place / fill / cancel) and partial exits;
``close_paper_trade`` stays the single source of truth for final closes and
folds the partial leg into totals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.logging_config import log
from app.models.bot import BotSkippedSignal, BotTrade
from app.modules.bot import equity, strategy
from app.modules.bot.schemas import CloseReason, Direction, SkipReason, TradePlan
from app.services import redis_service

# Market-style exits slip; a TP is a resting limit and fills at its price.
_MARKET_EXITS = {CloseReason.STOP, CloseReason.MANUAL, CloseReason.KILL_SWITCH, CloseReason.MAX_HOLD}

OPEN_HASH_KEY = "bot:open:{symbol}"


async def open_paper_trade(
    db: AsyncSession,
    plan: TradePlan,
    entry_price: Decimal,
    *,
    oracle_score: Decimal | None = None,
    entry_turnover_usd: Decimal | None = None,
) -> BotTrade:
    """Insert the trade row, increment counter, publish trade_opened alert."""
    qty = strategy.compute_qty(plan.notional_usd, entry_price)
    now = datetime.now(timezone.utc)
    trade = BotTrade(
        symbol=plan.symbol,
        exchange=plan.exchange,
        market_type=plan.market_type,
        direction=plan.direction.value,
        alert_type=plan.alert_type,
        alert_detected_at=plan.alert_detected_at,
        entry_price=entry_price,
        entry_at=now,
        entry_mode="chase",
        signal_high=plan.signal_high,
        signal_low=plan.signal_low,
        notional_usd=plan.notional_usd,
        qty=qty,
        paper_equity_at_entry=plan.paper_equity,
        stop_price=plan.stop_price,
        initial_stop_price=plan.stop_price,
        take_profit_price=plan.take_profit_price,
        oracle_score_at_entry=oracle_score,
        entry_turnover_usd=entry_turnover_usd,
        vol_ratio=plan.vol_ratio,
        funding_pct=plan.funding_pct,
        pct_change=plan.pct_change,
        status="open",
    )
    db.add(trade)
    await db.commit()
    await db.refresh(trade)

    await equity.increment_concurrent()
    await _write_open_hash(trade)
    await redis_service.publish_alert(
        "bot",
        {
            "type": "trade_opened",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "exchange": trade.exchange,
            "direction": trade.direction,
            "entry_price": float(trade.entry_price),
            "stop_price": float(trade.stop_price),
            "take_profit_price": float(trade.take_profit_price),
            "notional_usd": float(trade.notional_usd),
            "qty": float(trade.qty),
            "alert_detected_at": trade.alert_detected_at.isoformat(),
            "entry_at": trade.entry_at.isoformat(),
        },
    )
    log.info(
        "bot_trade_opened",
        id=str(trade.id),
        symbol=trade.symbol,
        direction=trade.direction,
        entry=float(trade.entry_price),
        stop=float(trade.stop_price),
        tp=float(trade.take_profit_price),
        notional=float(trade.notional_usd),
        oracle=float(oracle_score) if oracle_score is not None else None,
    )
    return trade


async def place_pending_trade(
    db: AsyncSession,
    plan: TradePlan,
    *,
    limit_price: Decimal,
    expire_at: datetime,
    oracle_score: Decimal | None = None,
    entry_turnover_usd: Decimal | None = None,
) -> BotTrade:
    """Insert a pending retrace-limit row. No concurrent-counter bump and no
    open-hash — those belong to the fill transition."""
    qty = strategy.compute_qty(plan.notional_usd, limit_price)
    now = datetime.now(timezone.utc)
    trade = BotTrade(
        symbol=plan.symbol,
        exchange=plan.exchange,
        market_type=plan.market_type,
        direction=plan.direction.value,
        alert_type=plan.alert_type,
        alert_detected_at=plan.alert_detected_at,
        # entry_price/entry_at are the planned limit + placement time; both are
        # confirmed (entry_at rewritten) at fill.
        entry_price=limit_price,
        entry_at=now,
        entry_mode="retrace",
        limit_price=limit_price,
        expire_at=expire_at,
        signal_high=plan.signal_high,
        signal_low=plan.signal_low,
        notional_usd=plan.notional_usd,
        qty=qty,
        paper_equity_at_entry=plan.paper_equity,
        stop_price=plan.stop_price,
        initial_stop_price=plan.stop_price,
        take_profit_price=plan.take_profit_price,
        oracle_score_at_entry=oracle_score,
        entry_turnover_usd=entry_turnover_usd,
        vol_ratio=plan.vol_ratio,
        funding_pct=plan.funding_pct,
        pct_change=plan.pct_change,
        status="pending",
    )
    db.add(trade)
    await db.commit()
    await db.refresh(trade)

    await redis_service.publish_alert(
        "bot",
        {
            "type": "order_placed",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "exchange": trade.exchange,
            "direction": trade.direction,
            "limit_price": float(trade.limit_price),
            "stop_price": float(trade.stop_price),
            "notional_usd": float(trade.notional_usd),
            "qty": float(trade.qty),
            "expire_at": trade.expire_at.isoformat(),
            "alert_detected_at": trade.alert_detected_at.isoformat(),
            "placed_at": now.isoformat(),
        },
    )
    log.info(
        "bot_order_placed",
        id=str(trade.id),
        symbol=trade.symbol,
        direction=trade.direction,
        limit=float(trade.limit_price),
        stop=float(trade.stop_price),
        notional=float(trade.notional_usd),
        expire_at=trade.expire_at.isoformat(),
    )
    return trade


async def fill_pending_trade(db: AsyncSession, trade: BotTrade) -> BotTrade:
    """pending → open: stamp the fill, bump the concurrent counter, alert."""
    now = datetime.now(timezone.utc)
    trade.status = "open"
    trade.entry_price = trade.limit_price
    trade.entry_at = now
    await db.commit()

    await equity.increment_concurrent()
    await _write_open_hash(trade)
    await redis_service.publish_alert(
        "bot",
        {
            "type": "trade_opened",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "exchange": trade.exchange,
            "direction": trade.direction,
            "entry_mode": "retrace",
            "entry_price": float(trade.entry_price),
            "stop_price": float(trade.stop_price),
            "take_profit_price": float(trade.take_profit_price),
            "notional_usd": float(trade.notional_usd),
            "qty": float(trade.qty),
            "alert_detected_at": trade.alert_detected_at.isoformat(),
            "entry_at": trade.entry_at.isoformat(),
        },
    )
    log.info(
        "bot_trade_filled",
        id=str(trade.id),
        symbol=trade.symbol,
        direction=trade.direction,
        entry=float(trade.entry_price),
        stop=float(trade.stop_price),
        notional=float(trade.notional_usd),
    )
    return trade


async def cancel_pending_trade(db: AsyncSession, trade: BotTrade) -> BotTrade:
    """pending → cancelled (limit expired unfilled). Sets the per-symbol
    cooldown so the same coil doesn't immediately re-place."""
    now = datetime.now(timezone.utc)
    trade.status = "cancelled"
    trade.close_reason = CloseReason.EXPIRED.value
    trade.closed_at = now
    await db.commit()

    from app.modules.bot import vetoes  # local import to avoid cycle
    await vetoes.set_cooldown(trade.symbol)

    await redis_service.publish_alert(
        "bot",
        {
            "type": "order_cancelled",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "exchange": trade.exchange,
            "direction": trade.direction,
            "limit_price": float(trade.limit_price) if trade.limit_price is not None else None,
            "close_reason": CloseReason.EXPIRED.value,
            "cancelled_at": now.isoformat(),
        },
    )
    log.info(
        "bot_order_cancelled",
        id=str(trade.id),
        symbol=trade.symbol,
        direction=trade.direction,
        limit=float(trade.limit_price) if trade.limit_price is not None else None,
    )
    return trade


async def take_partial_profit(
    db: AsyncSession, trade: BotTrade, *, exit_price: Decimal
) -> BotTrade:
    """Close bot_partial_fraction of the position at the partial target.

    Limit-style fill: fees on the leg (entry share + exit), no slippage. The
    partial PnL is realized into equity now; close_paper_trade later adds only
    the runner leg. qty stays the original total — partial_qty is the ledger.
    """
    direction = Direction(trade.direction)
    fraction = Decimal(str(app_settings.bot_partial_fraction))
    part_qty = Decimal(str(trade.qty)) * fraction
    if part_qty <= 0:
        return trade
    entry = Decimal(str(trade.entry_price))
    gross = strategy.realized_pnl(direction, entry, exit_price, part_qty)
    fees = strategy.round_trip_fee(
        entry * part_qty,
        exit_price * part_qty,
        Decimal(str(app_settings.bot_fee_pct_per_side)),
    )
    pnl = gross - fees
    now = datetime.now(timezone.utc)

    trade.partial_exit_price = exit_price
    trade.partial_exit_at = now
    trade.partial_qty = part_qty
    trade.partial_pnl_usd = pnl
    await db.commit()

    await equity.add_to_equity(pnl)
    await redis_service.publish_alert(
        "bot",
        {
            "type": "trade_partial_exit",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "exchange": trade.exchange,
            "direction": trade.direction,
            "entry_price": float(entry),
            "partial_exit_price": float(exit_price),
            "partial_qty": float(part_qty),
            "partial_pnl_usd": float(pnl),
            "partial_exit_at": now.isoformat(),
        },
    )
    log.info(
        "bot_trade_partial_exit",
        id=str(trade.id),
        symbol=trade.symbol,
        direction=trade.direction,
        price=float(exit_price),
        qty=float(part_qty),
        pnl=float(pnl),
    )
    return trade


async def close_paper_trade(
    db: AsyncSession,
    trade: BotTrade,
    *,
    exit_price: Decimal,
    reason: CloseReason,
) -> BotTrade:
    """Mark a trade closed, apply net PnL (after slippage + fees) to equity,
    set cooldown, emit alert.

    Partial-aware: when a partial exit was taken, the exit here covers only the
    runner (qty − partial_qty), the persisted totals (realized_pnl_usd, fees_usd,
    realized_r*) span both legs, and R is measured against the INITIAL stop and
    TOTAL qty — the risk actually taken at entry.
    """
    direction = Direction(trade.direction)

    # Market exits (stop/manual/timeout) slip against us; TPs fill at the limit.
    slippage_pct = Decimal(str(app_settings.bot_slippage_pct))
    fill_price = (
        strategy.adverse_slippage_price(direction, exit_price, slippage_pct)
        if reason in _MARKET_EXITS
        else exit_price
    )

    entry = Decimal(str(trade.entry_price))
    total_qty = Decimal(str(trade.qty))
    partial_qty = (
        Decimal(str(trade.partial_qty)) if trade.partial_qty is not None else Decimal("0")
    )
    runner_qty = total_qty - partial_qty
    partial_pnl = (
        Decimal(str(trade.partial_pnl_usd))
        if trade.partial_pnl_usd is not None
        else Decimal("0")
    )

    fee_pct = Decimal(str(app_settings.bot_fee_pct_per_side))
    runner_gross = strategy.realized_pnl(direction, entry, fill_price, runner_qty)
    runner_fees = strategy.round_trip_fee(entry * runner_qty, fill_price * runner_qty, fee_pct)
    partial_gross = Decimal("0")
    partial_fees = Decimal("0")
    if partial_qty > 0 and trade.partial_exit_price is not None:
        partial_px = Decimal(str(trade.partial_exit_price))
        partial_gross = strategy.realized_pnl(direction, entry, partial_px, partial_qty)
        partial_fees = strategy.round_trip_fee(
            entry * partial_qty, partial_px * partial_qty, fee_pct
        )
    now = datetime.now(timezone.utc)

    # Funding leg — perps only. Shorts entered on positive-funding cascades
    # collect it while held; longs on negative funding likewise. Accrued on the
    # full entry notional for the whole hold — an estimate that slightly
    # overstates the transfer after a partial halves the exposure.
    funding_pnl = Decimal("0")
    if (trade.market_type or "").lower() == "perp":
        hold_hours = (now - trade.entry_at).total_seconds() / 3600.0
        funding_pnl = strategy.estimated_funding_pnl(
            direction,
            entry * total_qty,
            trade.funding_pct,
            hold_hours,
            float(getattr(app_settings, "bot_funding_interval_hours", 0) or 0),
        )

    runner_net = runner_gross - runner_fees + funding_pnl
    pnl = runner_net + partial_pnl
    fees = runner_fees + partial_fees

    # R against the initial stop and total qty. Without a partial this reduces
    # exactly to v1's realized_r_multiple(entry, stop, fill).
    initial_stop = Decimal(
        str(trade.initial_stop_price if trade.initial_stop_price is not None else trade.stop_price)
    )
    risk_usd = abs(entry - initial_stop) * total_qty
    r_multiple = (
        (runner_gross + partial_gross) / risk_usd if risk_usd > 0 else Decimal("0")
    )
    r_net = strategy.net_r_multiple(pnl, entry, initial_stop, total_qty)

    trade.close_price = fill_price
    trade.closed_at = now
    trade.close_reason = reason.value
    trade.realized_pnl_usd = pnl
    trade.realized_r = r_multiple
    trade.realized_r_net = r_net
    trade.fees_usd = fees
    trade.funding_pnl_usd = funding_pnl
    trade.status = "closed"
    await db.commit()

    # The partial leg was realized into equity when it filled — add only the
    # runner leg here so it isn't double-counted.
    await equity.add_to_equity(runner_net)
    await equity.decrement_concurrent()
    await _delete_open_hash(trade.symbol)
    from app.modules.bot import vetoes  # local import to avoid cycle
    await vetoes.set_cooldown(trade.symbol)

    # Kill-switch check piggybacks on each close — fastest feedback loop.
    await equity.check_and_maybe_trip_kill_switch()
    # Loss-throttle input: only losing stops count — a trailed stop-out in
    # profit is a win, not part of a loss cluster.
    if reason == CloseReason.STOP and pnl < 0:
        await equity.record_stop_close()

    await redis_service.publish_alert(
        "bot",
        {
            "type": "trade_closed",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "exchange": trade.exchange,
            "direction": trade.direction,
            "entry_price": float(trade.entry_price),
            "close_price": float(fill_price),
            "close_reason": reason.value,
            "realized_pnl_usd": float(pnl),
            "realized_r": float(r_multiple),
            "realized_r_net": float(r_net),
            "fees_usd": float(fees),
            "funding_pnl_usd": float(funding_pnl),
            "closed_at": now.isoformat(),
        },
    )
    log.info(
        "bot_trade_closed",
        id=str(trade.id),
        symbol=trade.symbol,
        reason=reason.value,
        pnl=float(pnl),
        gross_pnl=float(runner_gross + partial_gross),
        partial_pnl=float(partial_pnl) if partial_qty > 0 else None,
        fees=float(fees),
        funding=float(funding_pnl),
        r=float(r_multiple),
        r_net=float(r_net),
    )
    return trade


async def log_skipped(
    db: AsyncSession,
    *,
    alert: dict,
    direction: Direction | str,
    reason: SkipReason,
    oracle_score: float | None = None,
    extra_context: dict | None = None,
) -> None:
    """Persist a row to bot_skipped_signals for postmortem analysis."""
    dir_str = direction.value if isinstance(direction, Direction) else str(direction)
    ctx = {"alert": alert}
    if extra_context:
        ctx.update(extra_context)
    row = BotSkippedSignal(
        symbol=alert.get("symbol", ""),
        exchange=alert.get("exchange", ""),
        alert_type=alert.get("type", "wave_active"),
        direction=dir_str,
        alert_detected_at=_parse_dt(alert.get("detected_at")),
        skip_reason=reason.value,
        oracle_score=Decimal(str(oracle_score)) if oracle_score is not None else None,
        context=ctx,
    )
    db.add(row)
    await db.commit()
    log.info(
        "bot_signal_skipped",
        symbol=row.symbol,
        reason=reason.value,
        direction=dir_str,
        oracle=oracle_score,
    )


def _parse_dt(s) -> datetime:
    if isinstance(s, datetime):
        return s
    if isinstance(s, str):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def _write_open_hash(trade: BotTrade) -> None:
    r = redis_service.get_redis()
    key = OPEN_HASH_KEY.format(symbol=trade.symbol)
    await r.hset(
        key,
        mapping={
            "id": str(trade.id),
            "direction": trade.direction,
            "entry": str(trade.entry_price),
            "stop": str(trade.stop_price),
            "tp": str(trade.take_profit_price),
            "opened_at": trade.entry_at.isoformat(),
        },
    )


async def _delete_open_hash(symbol: str) -> None:
    r = redis_service.get_redis()
    await r.delete(OPEN_HASH_KEY.format(symbol=symbol))
