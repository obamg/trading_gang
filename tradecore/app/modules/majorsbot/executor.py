"""MajorsBot — paper executor.

Persists lifecycle transitions to ``majorsbot_trades``, keeps the paper-equity
ledger, and publishes ``alerts:majorsbot`` events. Mirrors bot/executor.py
conventions:

  - pending → open | cancelled limit state machine (volevent);
  - direct market opens (fundingfade);
  - partial legs realized into equity when they fill; close_trade folds them
    into the persisted totals;
  - realized_r_net = (gross − fees + funding) / (|entry − initial_stop| × qty).

Fee schedule is strategy-aware: volevent enters maker (limit), fundingfade
enters taker (market); partial TPs are maker; every final close here is a
market-style exit (stop/trail/band/max-hold) → taker + slippage. Slippage is
applied by the caller-agnostic close path; funding is computed by the engine
(it owns bars + funding history) and passed in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.logging_config import log
from app.models.majorsbot import MajorsBotTrade
from app.modules.majorsbot import equity, strategies
from app.services import redis_service

EXCHANGE = "bybit"
MARKET_TYPE = "perp"


def entry_fee_pct(strategy: str) -> Decimal:
    """volevent enters on a resting limit (maker); fundingfade at market (taker)."""
    if strategy == strategies.VOLEVENT:
        return Decimal(str(app_settings.majorsbot_maker_fee_pct))
    return Decimal(str(app_settings.majorsbot_taker_fee_pct))


async def place_pending_order(
    db: AsyncSession,
    *,
    symbol: str,
    direction: str,
    strategy: str,
    signal_at: datetime,
    signal_high: Decimal,
    signal_low: Decimal,
    limit_price: Decimal,
    stop_price: Decimal,
    take_profit_price: Decimal,
    qty: Decimal,
    paper_equity: Decimal,
    expire_at: datetime,
) -> MajorsBotTrade:
    """Insert a pending retrace-limit row. Stop/TP/qty here are the limit-price
    estimates; all are recomputed at the actual fill (gap fills move them)."""
    now = datetime.now(timezone.utc)
    trade = MajorsBotTrade(
        symbol=symbol,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        direction=direction,
        strategy=strategy,
        signal_at=signal_at,
        entry_price=limit_price,
        entry_at=now,
        entry_mode="limit",
        limit_price=limit_price,
        expire_at=expire_at,
        signal_high=signal_high,
        signal_low=signal_low,
        notional_usd=limit_price * qty,
        qty=qty,
        paper_equity_at_entry=paper_equity,
        stop_price=stop_price,
        initial_stop_price=stop_price,
        take_profit_price=take_profit_price,
        status="pending",
    )
    db.add(trade)
    await db.commit()
    await db.refresh(trade)

    await redis_service.publish_alert(
        "majorsbot",
        {
            "type": "order_placed",
            "id": str(trade.id),
            "symbol": symbol,
            "strategy": strategy,
            "direction": direction,
            "limit_price": float(limit_price),
            "stop_price": float(stop_price),
            "qty": float(qty),
            "expire_at": expire_at.isoformat(),
            "placed_at": now.isoformat(),
        },
    )
    log.info(
        "majorsbot_order_placed",
        id=str(trade.id),
        symbol=symbol,
        strategy=strategy,
        direction=direction,
        limit=float(limit_price),
        stop=float(stop_price),
        expire_at=expire_at.isoformat(),
    )
    return trade


async def fill_pending_order(
    db: AsyncSession,
    trade: MajorsBotTrade,
    *,
    fill_price: Decimal,
    stop_price: Decimal,
    take_profit_price: Decimal,
    qty: Decimal,
    entry_bar_at: datetime,
    paper_equity: Decimal,
) -> MajorsBotTrade:
    """pending → open. Entry/stop/TP/qty are recomputed at the fill price (gap
    fills land better than the limit; the 1% stop floor re-anchors on the
    fill, exactly as the bake-off does)."""
    now = datetime.now(timezone.utc)
    trade.status = "open"
    trade.entry_price = fill_price
    trade.entry_at = now
    trade.entry_bar_at = entry_bar_at
    trade.stop_price = stop_price
    trade.initial_stop_price = stop_price
    trade.take_profit_price = take_profit_price
    trade.qty = qty
    trade.notional_usd = fill_price * qty
    trade.paper_equity_at_entry = paper_equity
    await db.commit()

    await equity.increment_concurrent()
    await redis_service.publish_alert(
        "majorsbot",
        {
            "type": "trade_opened",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "direction": trade.direction,
            "entry_mode": "limit",
            "entry_price": float(fill_price),
            "stop_price": float(stop_price),
            "take_profit_price": float(take_profit_price),
            "qty": float(qty),
            "entry_at": now.isoformat(),
        },
    )
    log.info(
        "majorsbot_trade_filled",
        id=str(trade.id),
        symbol=trade.symbol,
        strategy=trade.strategy,
        direction=trade.direction,
        entry=float(fill_price),
        stop=float(stop_price),
        qty=float(qty),
    )
    return trade


async def cancel_pending_order(
    db: AsyncSession, trade: MajorsBotTrade, *, reason: str = strategies.CLOSE_EXPIRED
) -> MajorsBotTrade:
    """pending → cancelled (limit expired unfilled, or capacity gone at fill)."""
    now = datetime.now(timezone.utc)
    trade.status = "cancelled"
    trade.close_reason = reason
    trade.closed_at = now
    await db.commit()

    await redis_service.publish_alert(
        "majorsbot",
        {
            "type": "order_cancelled",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "direction": trade.direction,
            "limit_price": float(trade.limit_price) if trade.limit_price is not None else None,
            "close_reason": reason,
            "cancelled_at": now.isoformat(),
        },
    )
    log.info(
        "majorsbot_order_cancelled",
        id=str(trade.id),
        symbol=trade.symbol,
        strategy=trade.strategy,
        reason=reason,
    )
    return trade


async def open_market_trade(
    db: AsyncSession,
    *,
    symbol: str,
    direction: str,
    strategy: str,
    signal_at: datetime,
    entry_price: Decimal,
    entry_bar_at: datetime,
    stop_price: Decimal,
    qty: Decimal,
    paper_equity: Decimal,
    funding_rate: Decimal | None = None,
    funding_pctile: Decimal | None = None,
    initial_stop_price: Decimal | None = None,
    alert_extra: dict | None = None,
) -> MajorsBotTrade:
    """Direct market open (fundingfade, newsevent). Taker entry fee at close.

    ``initial_stop_price`` defaults to ``stop_price`` — the two only differ for
    a stopless newsevent position, where ``stop_price`` holds the liquidation
    price (where the position actually dies) while ``initial_stop_price``
    holds the reference risk unit that R is measured against. Keeping R
    anchored to a real risk unit is what lets a stopless strategy still be
    compared against volevent on net R.
    """
    now = datetime.now(timezone.utc)
    trade = MajorsBotTrade(
        symbol=symbol,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        direction=direction,
        strategy=strategy,
        signal_at=signal_at,
        entry_price=entry_price,
        entry_at=now,
        entry_bar_at=entry_bar_at,
        entry_mode="market",
        notional_usd=entry_price * qty,
        qty=qty,
        paper_equity_at_entry=paper_equity,
        stop_price=stop_price,
        initial_stop_price=(
            initial_stop_price if initial_stop_price is not None else stop_price
        ),
        take_profit_price=None,
        funding_rate_at_entry=funding_rate,
        funding_pctile_at_entry=funding_pctile,
        status="open",
    )
    db.add(trade)
    await db.commit()
    await db.refresh(trade)

    await equity.increment_concurrent()
    alert_payload = {
        "type": "trade_opened",
        "id": str(trade.id),
        "symbol": symbol,
        "strategy": strategy,
        "direction": direction,
        "entry_mode": "market",
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "qty": float(qty),
        "funding_rate": float(funding_rate) if funding_rate is not None else None,
        "entry_at": now.isoformat(),
    }
    # Strategy-specific context for the Telegram/WS card (e.g. newsevent's
    # leverage, stop_kind, news source). Core keys always win — an extra can
    # add fields but never overwrite one.
    for k, v in (alert_extra or {}).items():
        alert_payload.setdefault(k, v)
    await redis_service.publish_alert("majorsbot", alert_payload)
    log.info(
        "majorsbot_trade_opened",
        id=str(trade.id),
        symbol=symbol,
        strategy=strategy,
        direction=direction,
        entry=float(entry_price),
        stop=float(stop_price),
        qty=float(qty),
        funding_rate=float(funding_rate) if funding_rate is not None else None,
    )
    return trade


async def take_partial_profit(
    db: AsyncSession,
    trade: MajorsBotTrade,
    *,
    exit_price: Decimal,
    bar_close_at: datetime,
    fraction: Decimal | None = None,
) -> MajorsBotTrade:
    """Close ``fraction`` of the position at the TP (maker limit — no
    slippage). Realized into equity now; close_trade adds only the runner.
    partial_exit_at is the BAR close time so funding accrual can split legs.

    ``fraction`` defaults to VOLEVENT_PARTIAL_FRACTION so volevent's behaviour
    is unchanged; newsevent passes its own so the two can never drift into
    each other by a shared constant being retuned.
    """
    if fraction is None:
        fraction = strategies.VOLEVENT_PARTIAL_FRACTION
    part_qty = Decimal(str(trade.qty)) * fraction
    if part_qty <= 0 or trade.partial_exit_at is not None:
        return trade
    entry = Decimal(str(trade.entry_price))
    gross = strategies.realized_pnl(trade.direction, entry, exit_price, part_qty)
    fees = strategies.leg_fees(
        entry,
        exit_price,
        part_qty,
        entry_fee_pct(trade.strategy),
        Decimal(str(app_settings.majorsbot_maker_fee_pct)),
    )
    pnl = gross - fees

    trade.partial_exit_price = exit_price
    trade.partial_exit_at = bar_close_at
    trade.partial_qty = part_qty
    trade.partial_pnl_usd = pnl
    await db.commit()

    await equity.add_to_equity(pnl)
    await redis_service.publish_alert(
        "majorsbot",
        {
            "type": "trade_partial_exit",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "direction": trade.direction,
            "entry_price": float(entry),
            "partial_exit_price": float(exit_price),
            "partial_qty": float(part_qty),
            "partial_pnl_usd": float(pnl),
            "partial_exit_at": bar_close_at.isoformat(),
        },
    )
    log.info(
        "majorsbot_trade_partial_exit",
        id=str(trade.id),
        symbol=trade.symbol,
        strategy=trade.strategy,
        price=float(exit_price),
        qty=float(part_qty),
        pnl=float(pnl),
    )
    return trade


async def close_trade(
    db: AsyncSession,
    trade: MajorsBotTrade,
    *,
    raw_exit_price: Decimal,
    reason: str,
    funding_pnl: Decimal = Decimal("0"),
) -> MajorsBotTrade:
    """Final close of the remaining qty. Every close path here is market-style
    (stop / trail / funding_norm / max_hold) → adverse slippage + taker fee on
    the slipped fill. Partial-aware: totals span both legs; R is measured
    against the INITIAL stop and TOTAL qty."""
    direction = trade.direction
    slippage = Decimal(str(app_settings.majorsbot_slippage_pct))
    fill_price = strategies.adverse_slippage_price(direction, raw_exit_price, slippage)

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

    e_fee = entry_fee_pct(trade.strategy)
    taker = Decimal(str(app_settings.majorsbot_taker_fee_pct))
    maker = Decimal(str(app_settings.majorsbot_maker_fee_pct))
    runner_gross = strategies.realized_pnl(direction, entry, fill_price, runner_qty)
    runner_fees = strategies.leg_fees(entry, fill_price, runner_qty, e_fee, taker)
    partial_gross = Decimal("0")
    partial_fees = Decimal("0")
    if partial_qty > 0 and trade.partial_exit_price is not None:
        partial_px = Decimal(str(trade.partial_exit_price))
        partial_gross = strategies.realized_pnl(direction, entry, partial_px, partial_qty)
        partial_fees = strategies.leg_fees(entry, partial_px, partial_qty, e_fee, maker)
    now = datetime.now(timezone.utc)

    runner_net = runner_gross - runner_fees + funding_pnl
    pnl = runner_net + partial_pnl
    fees = runner_fees + partial_fees

    initial_stop = Decimal(
        str(trade.initial_stop_price if trade.initial_stop_price is not None else trade.stop_price)
    )
    risk_usd = abs(entry - initial_stop) * total_qty
    r_multiple = (
        (runner_gross + partial_gross) / risk_usd if risk_usd > 0 else Decimal("0")
    )
    r_net = strategies.net_r_multiple(pnl, entry, initial_stop, total_qty)

    trade.close_price = fill_price
    trade.closed_at = now
    trade.close_reason = reason
    trade.realized_pnl_usd = pnl
    trade.realized_r = r_multiple
    trade.realized_r_net = r_net
    trade.fees_usd = fees
    trade.funding_pnl_usd = funding_pnl
    trade.status = "closed"
    await db.commit()

    # The partial leg hit equity when it filled — only the runner (+funding) here.
    await equity.add_to_equity(runner_net)
    await equity.decrement_concurrent()

    await redis_service.publish_alert(
        "majorsbot",
        {
            "type": "trade_closed",
            "id": str(trade.id),
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "direction": direction,
            "entry_price": float(entry),
            "close_price": float(fill_price),
            "close_reason": reason,
            "realized_pnl_usd": float(pnl),
            "realized_r": float(r_multiple),
            "realized_r_net": float(r_net),
            "fees_usd": float(fees),
            "funding_pnl_usd": float(funding_pnl),
            "closed_at": now.isoformat(),
        },
    )
    log.info(
        "majorsbot_trade_closed",
        id=str(trade.id),
        symbol=trade.symbol,
        strategy=trade.strategy,
        reason=reason,
        pnl=float(pnl),
        gross_pnl=float(runner_gross + partial_gross),
        partial_pnl=float(partial_pnl) if partial_qty > 0 else None,
        fees=float(fees),
        funding=float(funding_pnl),
        r=float(r_multiple),
        r_net=float(r_net),
    )
    return trade
