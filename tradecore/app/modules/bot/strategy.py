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
    oracle_score: Decimal | None = None,
) -> TradePlan | None:
    """Build a TradePlan or return None if the inputs don't validate.

    Returns None for: unknown direction, non-positive equity, broken candle,
    non-positive entry, stop equal to or beyond entry (would invert R sign).
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
    notional = paper_equity * position_size_pct

    return TradePlan(
        symbol=alert["symbol"],
        exchange=alert["exchange"],
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
