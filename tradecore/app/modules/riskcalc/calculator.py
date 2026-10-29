"""RiskCalc — position sizing & risk assessment.

Pure functions — no DB access. The router handles persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Funding rate against your side above this (fraction per 8h) blocks the trade.
MAX_FUNDING_AGAINST = 0.0005


@dataclass
class RiskInput:
    account_balance_usd: float
    risk_pct: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float | None = None
    max_leverage: float = 20.0
    asset_type: str = "futures"  # futures | spot
    side: str = "long"            # long | short
    maintenance_margin_rate: float = 0.004  # Binance smallest tier default (0.4%)
    taker_fee_pct: float = 0.05      # per side, Binance VIP 0 taker
    stop_slippage_pct: float = 0.05  # assumed extra fill past the stop
    funding_rate: float | None = None  # fraction per 8h; > 0 means longs pay
    funding_settlements: int = 0       # settlements inside the holding window


def funding_settlements(now: datetime, hold_hours: float) -> int:
    """Count 00/08/16 UTC funding settlements in (now, now + hold_hours]."""
    end = now + timedelta(hours=hold_hours)
    t = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=now.hour % 8)
    n = 0
    while (t := t + timedelta(hours=8)) <= end:
        n += 1
    return n


def _get_mmr_for_notional(notional_usd: float) -> float:
    """Binance USDⓈ-M tiered maintenance margin rate."""
    if notional_usd < 50_000:
        return 0.004
    if notional_usd < 250_000:
        return 0.005
    if notional_usd < 1_000_000:
        return 0.0065
    if notional_usd < 5_000_000:
        return 0.01
    if notional_usd < 20_000_000:
        return 0.025
    return 0.05


def calculate_position(params: RiskInput) -> dict:
    balance = float(params.account_balance_usd)
    risk_pct = float(params.risk_pct)
    entry = float(params.entry_price)
    stop = float(params.stop_loss_price)
    tp = float(params.take_profit_price) if params.take_profit_price else None

    if balance <= 0 or entry <= 0 or stop <= 0:
        raise ValueError("balance, entry, and stop must be > 0")
    if risk_pct <= 0 or risk_pct > 100:
        raise ValueError("risk_pct must be in (0, 100]")
    if entry == stop:
        raise ValueError("entry and stop cannot be equal")

    side = params.side.lower()
    if side == "long" and stop >= entry:
        raise ValueError("for long, stop_loss must be below entry")
    if side == "short" and stop <= entry:
        raise ValueError("for short, stop_loss must be above entry")

    risk_amount_usd = balance * (risk_pct / 100.0)
    stop_distance = abs(entry - stop) / entry  # as fraction
    stop_distance_pct = stop_distance * 100.0

    # Loss at the stop is the price move plus entry+exit taker fees and stop
    # slippage, all proportional to notional. Size so the total fits the risk.
    friction = (2 * params.taker_fee_pct + params.stop_slippage_pct) / 100.0
    position_size_usd = risk_amount_usd / (stop_distance + friction)
    position_size_units = position_size_usd / entry

    # Leverage needed to hold this position given the account balance.
    raw_leverage = position_size_usd / balance if balance > 0 else 0.0
    leverage = min(raw_leverage, float(params.max_leverage))
    # If capped, shrink the position accordingly.
    if raw_leverage > params.max_leverage:
        position_size_usd = balance * params.max_leverage
        position_size_units = position_size_usd / entry

    # Liquidation price (isolated margin, includes maintenance margin rate).
    liquidation_price: float | None = None
    mmr = _get_mmr_for_notional(position_size_usd) if params.asset_type == "futures" else 0.0
    if params.asset_type == "futures" and leverage > 1:
        if side == "long":
            liquidation_price = entry * (1 - 1 / leverage + mmr)
        else:
            liquidation_price = entry * (1 + 1 / leverage - mmr)

    fees_slippage_usd = position_size_usd * friction
    max_loss_usd = position_size_units * abs(entry - stop) + fees_slippage_usd

    potential_profit_usd: float | None = None
    rr_ratio: float | None = None
    if tp is not None:
        gross = position_size_units * (tp - entry if side == "long" else entry - tp)
        potential_profit_usd = gross - position_size_usd * 2 * params.taker_fee_pct / 100.0
        rr_ratio = potential_profit_usd / max_loss_usd if max_loss_usd > 0 else None

    # Positive = you pay. Longs pay when the rate is positive, shorts when negative.
    funding_against = (params.funding_rate or 0.0) * (1 if side == "long" else -1)
    funding_cost_usd = position_size_usd * funding_against * params.funding_settlements

    warnings: list[str] = []
    if friction * 100.0 >= stop_distance_pct:
        warnings.append(
            f"BLOCKED: Fees and slippage ({friction * 100:.2f}%) are at least the stop "
            f"distance ({stop_distance_pct:.2f}%) — widen the stop"
        )
    if params.funding_settlements and funding_against > MAX_FUNDING_AGAINST:
        warnings.append(
            f"BLOCKED: Funding {funding_against * 100:.3f}%/8h against your side "
            f"(max {MAX_FUNDING_AGAINST * 100:.2f}%)"
        )
    elif funding_cost_usd > 0:
        warnings.append(
            f"You pay ~${funding_cost_usd:.2f} funding over "
            f"{params.funding_settlements} settlement(s) in the holding window"
        )
    if leverage > 10:
        warnings.append("Leverage is high (>10x) — consider reducing position size")
    if liquidation_price is not None:
        liq_stop_gap = abs(liquidation_price - stop) / entry
        if liq_stop_gap < 0.02:
            warnings.append("Liquidation price is dangerously close to stop loss")
    if rr_ratio is not None and rr_ratio < 2:
        warnings.append("Risk:Reward below 2:1 — edge may not cover losers")
    if risk_pct > 2:
        warnings.append("Risk per trade above 2% of account — aggressive sizing")

    return {
        "risk_amount_usd": round(risk_amount_usd, 2),
        "stop_distance_pct": round(stop_distance_pct, 4),
        "position_size_units": round(position_size_units, 8),
        "position_size_usd": round(position_size_usd, 2),
        "leverage": round(leverage, 2),
        "liquidation_price": round(liquidation_price, 8) if liquidation_price else None,
        "max_loss_usd": round(max_loss_usd, 2),
        "fees_slippage_usd": round(fees_slippage_usd, 2),
        "funding_cost_usd": round(funding_cost_usd, 2),
        "funding_settlements": params.funding_settlements,
        "potential_profit_usd": round(potential_profit_usd, 2) if potential_profit_usd is not None else None,
        "rr_ratio": round(rr_ratio, 2) if rr_ratio is not None else None,
        "warnings": warnings,
    }


__all__ = ["RiskInput", "calculate_position", "funding_settlements"]
