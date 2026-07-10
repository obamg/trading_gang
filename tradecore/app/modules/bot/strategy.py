"""WaveBot — pure strategy logic.

Zero I/O — every decision is a function of inputs. Keeps the unit-test surface
small and lets the listener / executor / monitor reuse the same primitives.

Direction mapping from detector → bot:
  short_squeeze (green cascade, funding negative) → LONG
  long_flush    (red   cascade, funding positive) → SHORT
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.modules.bot.schemas import Direction, TradePlan

DETECTOR_DIRECTION_MAP: dict[str, Direction] = {
    "short_squeeze": Direction.LONG,
    "long_flush": Direction.SHORT,
}


def map_direction(detector_direction: str) -> Direction | None:
    return DETECTOR_DIRECTION_MAP.get(detector_direction)


def parse_candle(c: dict) -> tuple[Decimal, Decimal, Decimal]:
    """Returns (high, low, close) from a Redis candle dict.

    Stream candles use short keys (``h``/``l``/``c``); fall back to long
    names so the function works with both stream output and test fixtures.
    """
    high = c.get("h", c.get("high"))
    low = c.get("l", c.get("low"))
    close = c.get("c", c.get("close"))
    return Decimal(str(high)), Decimal(str(low)), Decimal(str(close))


def compute_stop(
    direction: Direction,
    signal_high: Decimal,
    signal_low: Decimal,
    buffer_pct: Decimal,
) -> Decimal:
    """Stop = the opposite extreme of the 5m signal candle, plus a buffer."""
    if direction == Direction.LONG:
        return signal_low * (Decimal("1") - buffer_pct)
    return signal_high * (Decimal("1") + buffer_pct)


def compute_take_profit(
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
    r_multiple: Decimal,
) -> Decimal:
    """TP = entry + R × (entry − stop) for longs, mirror for shorts."""
    if direction == Direction.LONG:
        risk = entry_price - stop_price
        return entry_price + r_multiple * risk
    risk = stop_price - entry_price
    return entry_price - r_multiple * risk


def compute_qty(notional_usd: Decimal, entry_price: Decimal) -> Decimal:
    if entry_price <= 0:
        return Decimal("0")
    return notional_usd / entry_price


def compute_notional(
    *,
    paper_equity: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    max_notional_pct: Decimal,
    risk_per_trade_pct: Decimal | None,
) -> Decimal:
    """Position notional in USD.

    Risk-normalized: notional is sized so the distance to the stop costs
    ``paper_equity × risk_per_trade_pct`` if hit, capped at
    ``paper_equity × max_notional_pct``. This decouples dollar-risk from the
    signal-candle width — without it, a wide stop carries far more dollar risk
    than a tight one for the same notional, which made the bot net-positive in R
    yet net-negative in dollars.

    Falls back to fixed-notional (the cap) when ``risk_per_trade_pct`` is None/≤0
    or the stop distance is degenerate.
    """
    cap = paper_equity * max_notional_pct
    if risk_per_trade_pct is None or risk_per_trade_pct <= 0 or entry_price <= 0:
        return cap
    stop_dist_frac = abs(entry_price - stop_price) / entry_price
    if stop_dist_frac <= 0:
        return cap
    risk_usd = paper_equity * risk_per_trade_pct
    return min(risk_usd / stop_dist_frac, cap)


def is_stop_hit(direction: Direction, bar_high: Decimal, bar_low: Decimal, stop: Decimal) -> bool:
    if direction == Direction.LONG:
        return bar_low <= stop
    return bar_high >= stop


def is_tp_hit(direction: Direction, bar_high: Decimal, bar_low: Decimal, tp: Decimal) -> bool:
    if direction == Direction.LONG:
        return bar_high >= tp
    return bar_low <= tp


def realized_pnl(direction: Direction, entry: Decimal, exit_price: Decimal, qty: Decimal) -> Decimal:
    delta = (exit_price - entry) if direction == Direction.LONG else (entry - exit_price)
    return delta * qty


def adverse_slippage_price(
    direction: Direction, price: Decimal, slippage_pct: Decimal
) -> Decimal:
    """Worsen a market-exit fill by ``slippage_pct`` — sell lower for longs, buy
    higher for shorts. Used for stop/manual/timeout exits (not limit TPs)."""
    if slippage_pct <= 0:
        return price
    if direction == Direction.LONG:
        return price * (Decimal("1") - slippage_pct)
    return price * (Decimal("1") + slippage_pct)


def round_trip_fee(
    entry_notional: Decimal, exit_notional: Decimal, fee_pct_per_side: Decimal
) -> Decimal:
    """Taker fee charged on both the entry and the exit leg."""
    if fee_pct_per_side <= 0:
        return Decimal("0")
    return (abs(entry_notional) + abs(exit_notional)) * fee_pct_per_side


def estimated_funding_pnl(
    direction: Direction,
    entry_notional: Decimal,
    funding_pct: Decimal | None,
    hold_hours: float,
    interval_hours: float,
) -> Decimal:
    """Estimated funding transfer over the hold, at the entry-time rate.

    Perp funding: longs pay shorts when the rate is positive, and vice versa.
    Holds the entry rate constant across intervals — an estimate; cascade-driven
    extremes mean-revert, so long holds overstate the transfer.
    """
    if funding_pct is None or interval_hours <= 0 or entry_notional <= 0:
        return Decimal("0")
    intervals = int(hold_hours / interval_hours)
    if intervals <= 0:
        return Decimal("0")
    transfer = funding_pct * entry_notional * intervals
    return -transfer if direction == Direction.LONG else transfer


def net_r_multiple(
    net_pnl_usd: Decimal,
    entry: Decimal,
    stop: Decimal,
    qty: Decimal,
) -> Decimal:
    """R net of all costs: net PnL over the dollar-risk at entry.

    ``realized_r_multiple`` is price-move only, so it overstates the edge by
    the cost load (~0.05R at typical stop widths) — comparable to the whole
    measured expectancy. Scale-up decisions should read this one.
    """
    risk_usd = abs(entry - stop) * qty
    if risk_usd == 0:
        return Decimal("0")
    return net_pnl_usd / risk_usd


def realized_r_multiple(
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    exit_price: Decimal,
) -> Decimal:
    """How many R the trade made. -1 ≈ hit stop, +2 ≈ hit 2R TP."""
    risk_per_unit = (entry - stop) if direction == Direction.LONG else (stop - entry)
    if risk_per_unit == 0:
        return Decimal("0")
    move = (exit_price - entry) if direction == Direction.LONG else (entry - exit_price)
    return move / risk_per_unit


def plan_entry(
    *,
    alert: dict,
    signal_candle: dict,
    entry_price: Decimal,
    paper_equity: Decimal,
    position_size_pct: Decimal,
    stop_buffer_pct: Decimal,
    r_multiple: Decimal,
    risk_per_trade_pct: Decimal | None = None,
    oracle_score: Decimal | None = None,
) -> TradePlan | None:
    """Build a TradePlan or return None if the inputs don't validate.

    Returns None for: unknown direction, non-positive equity, broken candle,
    non-positive entry, stop equal to or beyond entry (would invert R sign).

    ``position_size_pct`` is the notional cap; ``risk_per_trade_pct`` (when set)
    drives risk-normalized sizing within that cap — see ``compute_notional``.
    """
    direction = map_direction(alert.get("direction", ""))
    if direction is None or paper_equity <= 0 or entry_price <= 0:
        return None

    signal_high, signal_low, _ = parse_candle(signal_candle)
    if signal_high <= 0 or signal_low <= 0 or signal_high <= signal_low:
        return None

    stop_price = compute_stop(direction, signal_high, signal_low, stop_buffer_pct)

    if direction == Direction.LONG and stop_price >= entry_price:
        return None
    if direction == Direction.SHORT and stop_price <= entry_price:
        return None

    take_profit = compute_take_profit(direction, entry_price, stop_price, r_multiple)
    notional = compute_notional(
        paper_equity=paper_equity,
        entry_price=entry_price,
        stop_price=stop_price,
        max_notional_pct=position_size_pct,
        risk_per_trade_pct=risk_per_trade_pct,
    )

    return TradePlan(
        symbol=alert["symbol"],
        exchange=alert["exchange"],
        market_type=alert.get("market_type"),
        direction=direction,
        alert_type=alert.get("type", "wave_active"),
        alert_detected_at=_parse_dt(alert.get("detected_at")),
        signal_high=signal_high,
        signal_low=signal_low,
        stop_price=stop_price,
        take_profit_price=take_profit,
        notional_usd=notional,
        paper_equity=paper_equity,
        vol_ratio=_dec_opt(alert.get("vol_ratio")),
        funding_pct=_dec_opt(alert.get("funding_pct")),
        pct_change=_dec_opt(alert.get("pct_change")),
        oracle_score=oracle_score,
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


def _dec_opt(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (ValueError, TypeError):
        return None
