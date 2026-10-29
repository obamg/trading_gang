"""RiskCalc: fees, stop slippage, funding and the loss-streak cool-down."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.riskcalc.calculator import RiskInput, calculate_position, funding_settlements
from app.modules.riskcalc.portfolio import loss_streak


def _base(**over) -> RiskInput:
    defaults = dict(
        account_balance_usd=10_000,
        risk_pct=1.0,
        entry_price=100.0,
        stop_loss_price=99.5,  # 0.5% stop
        side="long",
    )
    defaults.update(over)
    return RiskInput(**defaults)


def test_loss_at_stop_includes_fees_and_slippage():
    # 0.05% taker each side + 0.05% slippage = 0.15% on top of a 0.5% stop.
    r = calculate_position(_base())
    assert r["position_size_usd"] == pytest.approx(100 / 0.0065, abs=0.01)
    assert r["max_loss_usd"] == pytest.approx(100.0, abs=0.01)
    assert r["fees_slippage_usd"] == pytest.approx(r["position_size_usd"] * 0.0015, abs=0.01)


def test_fee_free_sizing_would_overshoot_risk():
    # The old sizing ($20k on a 0.5% stop) loses $130 at the stop, not $100.
    fee_free = calculate_position(_base(taker_fee_pct=0, stop_slippage_pct=0))
    assert fee_free["position_size_usd"] == pytest.approx(20_000)
    assert fee_free["position_size_usd"] * 0.0065 == pytest.approx(130)


def test_rr_is_net_of_fees():
    r = calculate_position(_base(take_profit_price=101.0))  # 2:1 on price
    assert r["rr_ratio"] < 2


def test_stop_inside_fees_is_blocked():
    r = calculate_position(_base(stop_loss_price=99.9))  # 0.1% stop < 0.15% friction
    assert any(w.startswith("BLOCKED: Fees") for w in r["warnings"])


def test_funding_against_long_is_costed_and_extreme_rate_blocked():
    paid = calculate_position(_base(funding_rate=0.0001, funding_settlements=2))
    assert paid["funding_cost_usd"] == pytest.approx(paid["position_size_usd"] * 0.0002, abs=0.01)
    assert any("funding" in w for w in paid["warnings"])

    blocked = calculate_position(_base(funding_rate=0.001, funding_settlements=1))
    assert any(w.startswith("BLOCKED: Funding") for w in blocked["warnings"])

    # Same rate is income for a short; no settlement in the window, no check.
    short = calculate_position(_base(side="short", stop_loss_price=100.5, funding_rate=0.001, funding_settlements=1))
    assert short["funding_cost_usd"] < 0
    assert not any("BLOCKED" in w for w in short["warnings"])
    none = calculate_position(_base(funding_rate=0.001, funding_settlements=0))
    assert not any("BLOCKED" in w for w in none["warnings"])


@pytest.mark.parametrize("now,hours,expected", [
    ("2026-10-29T09:30", 12, 1),   # 16:00
    ("2026-10-29T07:59", 12, 2),   # 08:00, 16:00
    ("2026-10-29T08:00", 8, 1),    # 16:00 only; 08:00 is not inside (now, end]
    ("2026-10-29T17:00", 1, 0),
    ("2026-10-29T23:00", 24, 3),   # 00:00, 08:00, 16:00
])
def test_funding_settlements(now, hours, expected):
    t = datetime.fromisoformat(now).replace(tzinfo=timezone.utc)
    assert funding_settlements(t, hours) == expected


def test_loss_streak_counts_from_newest():
    assert loss_streak([-5, -3, -1, 10]) == 3
    assert loss_streak([4, -5, -3]) == 0
    assert loss_streak([0, -1]) == 0
    assert loss_streak([]) == 0
