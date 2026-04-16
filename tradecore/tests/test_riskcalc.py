"""Pure-function tests for the RiskCalc position sizer."""
from __future__ import annotations

from app.modules.riskcalc.calculator import RiskInput, calculate_position


def _base(**over) -> RiskInput:
    defaults = dict(
        account_balance_usd=10_000,
        risk_pct=1.0,
        entry_price=100.0,
        stop_loss_price=95.0,
        take_profit_price=120.0,
        max_leverage=10.0,
        asset_type="futures",
        side="long",
    )
    defaults.update(over)
    return RiskInput(**defaults)


def test_correct_position_size():
    # Risk = 1% of 10k = $100. Stop distance = 5%. Size = $100 / 0.05 = $2000.
    r = calculate_position(_base())
    assert r["risk_amount_usd"] == 100.0
    assert r["stop_distance_pct"] == 5.0
    assert r["position_size_usd"] == 2000.0
    # 2000 / 100 = 20 units
    assert abs(r["position_size_units"] - 20.0) < 1e-6


def test_leverage_cap_applied():
    # 10% risk of 100k with a tight 0.5% stop wants a huge position — capped by max_leverage.
    r = calculate_position(_base(
        account_balance_usd=100_000,
        risk_pct=10.0,
        stop_loss_price=99.5,  # 0.5% stop
        max_leverage=5.0,
    ))
    # Capped at 5x → position = 500k, leverage == 5.0
    assert r["leverage"] == 5.0
    assert r["position_size_usd"] == 500_000.0


def test_rr_ratio_below_2_warning():
    r = calculate_position(_base(take_profit_price=108.0))  # reward 8, risk 5 → 1.6
    assert r["rr_ratio"] < 2
    assert any("Risk:Reward" in w for w in r["warnings"])


def test_high_risk_warning():
    r = calculate_position(_base(risk_pct=3.0))
    assert any("Risk per trade above 2%" in w for w in r["warnings"])


def test_liquidation_only_when_leveraged():
    # Large balance, small risk → no leverage needed, no liquidation price.
    r = calculate_position(_base(account_balance_usd=1_000_000, risk_pct=0.1))
    assert r["leverage"] <= 1.0
    assert r["liquidation_price"] is None


def test_short_side_math():
    r = calculate_position(_base(
        entry_price=100.0,
        stop_loss_price=105.0,
        take_profit_price=90.0,
        side="short",
    ))
    assert r["rr_ratio"] == 2.0  # reward 10, risk 5
