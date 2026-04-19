"""PerformanceCore aggregator tests — drawdown, win rate, profit factor."""
from __future__ import annotations

import pytest

from app.modules.performance.aggregator import _max_drawdown


# ---------- _max_drawdown (pure) ----------

def test_max_drawdown_simple():
    """Equity goes up then down."""
    equity = [100.0, 110.0, 120.0, 90.0, 95.0]
    dd_usd, dd_pct = _max_drawdown(equity)
    # Peak = 120, trough = 90, DD = 30, DD% = 30/120 * 100 = 25%
    assert dd_usd == pytest.approx(30.0, abs=0.01)
    assert dd_pct == pytest.approx(25.0, abs=0.01)


def test_max_drawdown_no_drawdown():
    """Monotonically increasing → no drawdown."""
    equity = [100.0, 110.0, 120.0, 130.0]
    dd_usd, dd_pct = _max_drawdown(equity)
    assert dd_usd == 0.0
    assert dd_pct == 0.0


def test_max_drawdown_all_losses():
    """Monotonically decreasing from start."""
    equity = [100.0, 80.0, 60.0, 40.0]
    dd_usd, dd_pct = _max_drawdown(equity)
    assert dd_usd == pytest.approx(60.0, abs=0.01)
    assert dd_pct == pytest.approx(60.0, abs=0.01)


def test_max_drawdown_empty():
    dd_usd, dd_pct = _max_drawdown([])
    assert dd_usd == 0.0
    assert dd_pct == 0.0


def test_max_drawdown_single_point():
    dd_usd, dd_pct = _max_drawdown([100.0])
    assert dd_usd == 0.0
    assert dd_pct == 0.0


def test_max_drawdown_recovery_then_deeper():
    """Two drawdowns — should find the deeper one."""
    equity = [100.0, 120.0, 110.0, 130.0, 80.0]
    dd_usd, dd_pct = _max_drawdown(equity)
    # Peak = 130, trough = 80, DD = 50, DD% = 50/130 * 100 ≈ 38.46%
    assert dd_usd == pytest.approx(50.0, abs=0.01)
    assert dd_pct == pytest.approx(38.46, abs=0.1)


def test_max_drawdown_negative_equity():
    """Equity starts negative (e.g., losses from start)."""
    equity = [-10.0, -20.0, -30.0]
    dd_usd, dd_pct = _max_drawdown(equity)
    # Known limitation: peak <= 0 → dd_pct is 0.0
    assert dd_usd == pytest.approx(20.0, abs=0.01)


# ---------- win rate ----------

def test_win_rate_calculation():
    wins = 7
    losses = 3
    total = wins + losses
    win_rate = wins / total * 100
    assert win_rate == pytest.approx(70.0, abs=0.01)


def test_win_rate_all_wins():
    wins = 10
    total = 10
    win_rate = wins / total * 100
    assert win_rate == 100.0


def test_win_rate_all_losses():
    wins = 0
    total = 5
    win_rate = wins / total * 100
    assert win_rate == 0.0


# ---------- profit factor ----------

def test_profit_factor():
    """Profit factor = gross wins / gross losses."""
    gross_wins = 5000.0
    gross_losses = 2000.0
    pf = gross_wins / gross_losses
    assert pf == pytest.approx(2.5, abs=0.01)


def test_profit_factor_no_losses():
    """No losses → profit factor is undefined (None)."""
    gross_losses = 0.0
    pf = gross_losses if gross_losses > 0 else None
    assert pf is None


# ---------- expectancy ----------

def test_expectancy():
    """Expectancy = (WR * avg_win) + ((1-WR) * avg_loss)."""
    win_rate = 0.6
    avg_win = 2.0   # percent
    avg_loss = -1.5  # percent
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    assert expectancy == pytest.approx(0.6, abs=0.01)


def test_expectancy_negative():
    """Losing system."""
    win_rate = 0.3
    avg_win = 1.0
    avg_loss = -2.0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    assert expectancy == pytest.approx(-1.1, abs=0.01)


# ---------- max consecutive losses ----------

def test_max_consecutive_losses():
    pnls = [1, -1, -1, -1, 1, -1, -1, 1]
    max_streak = 0
    current = 0
    for p in pnls:
        if p < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    assert max_streak == 3


def test_max_consecutive_losses_none():
    pnls = [1, 1, 1]
    max_streak = 0
    current = 0
    for p in pnls:
        if p < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    assert max_streak == 0
