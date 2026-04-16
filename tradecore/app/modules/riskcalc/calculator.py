"""RiskCalc — position sizing & risk assessment.

Pure functions — no DB access. The router handles persistence.
"""
from __future__ import annotations

from dataclasses import dataclass


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

    position_size_usd = risk_amount_usd / stop_distance
    position_size_units = position_size_usd / entry

    # Leverage needed to hold this position given the account balance.
    raw_leverage = position_size_usd / balance if balance > 0 else 0.0
    leverage = min(raw_leverage, float(params.max_leverage))
    # If capped, shrink the position accordingly.
    if raw_leverage > params.max_leverage:
        position_size_usd = balance * params.max_leverage
        position_size_units = position_size_usd / entry

    # Liquidation price (simplified isolated-margin, no fees/maintenance margin).
    # Only meaningful when the position is actually leveraged (> 1x).
    liquidation_price: float | None = None
    if params.asset_type == "futures" and leverage > 1:
        if side == "long":
            liquidation_price = entry * (1 - 1 / leverage)
        else:
            liquidation_price = entry * (1 + 1 / leverage)

    max_loss_usd = position_size_units * abs(entry - stop)

    potential_profit_usd: float | None = None
    rr_ratio: float | None = None
    if tp is not None:
        if side == "long":
            potential_profit_usd = position_size_units * (tp - entry)
            rr_ratio = (tp - entry) / (entry - stop) if entry != stop else None
        else:
            potential_profit_usd = position_size_units * (entry - tp)
            rr_ratio = (entry - tp) / (stop - entry) if stop != entry else None

    warnings: list[str] = []
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
        "potential_profit_usd": round(potential_profit_usd, 2) if potential_profit_usd is not None else None,
        "rr_ratio": round(rr_ratio, 2) if rr_ratio is not None else None,
        "warnings": warnings,
    }


__all__ = ["RiskInput", "calculate_position"]
