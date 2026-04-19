"""TradeLog service tests — P&L math, R-multiple, trade lifecycle."""
from __future__ import annotations

import pytest

from app.modules.tradelog.service import _compute_pnl, _direction_mul


# ---------- _direction_mul (pure) ----------

def test_direction_mul_long():
    assert _direction_mul("long") == 1


def test_direction_mul_short():
    assert _direction_mul("short") == -1


def test_direction_mul_default():
    assert _direction_mul("something_else") == -1


# ---------- _compute_pnl (pure) ----------

def test_pnl_long_profit():
    """Long: buy at 100, sell at 110 → 10% profit."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=110.0,
        size=10.0, fees_usd=5.0, stop_loss_price=95.0,
    )
    assert r["pnl_usd"] == 100.0        # (110-100)*10
    assert r["pnl_pct"] == pytest.approx(10.0, abs=0.01)
    assert r["net_pnl_usd"] == 95.0     # 100 - 5 fees
    assert r["fees_usd"] == 5.0


def test_pnl_long_loss():
    """Long: buy at 100, sell at 90 → 10% loss."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=90.0,
        size=10.0, fees_usd=5.0, stop_loss_price=95.0,
    )
    assert r["pnl_usd"] == -100.0
    assert r["pnl_pct"] == pytest.approx(-10.0, abs=0.01)
    assert r["net_pnl_usd"] == -105.0   # -100 - 5


def test_pnl_short_profit():
    """Short: sell at 100, cover at 90 → 10% profit."""
    r = _compute_pnl(
        side="short", entry_price=100.0, exit_price=90.0,
        size=10.0, fees_usd=5.0, stop_loss_price=105.0,
    )
    assert r["pnl_usd"] == 100.0        # (100-90)*10
    assert r["net_pnl_usd"] == 95.0


def test_pnl_short_loss():
    """Short: sell at 100, cover at 110 → 10% loss."""
    r = _compute_pnl(
        side="short", entry_price=100.0, exit_price=110.0,
        size=10.0, fees_usd=5.0, stop_loss_price=105.0,
    )
    assert r["pnl_usd"] == -100.0
    assert r["net_pnl_usd"] == -105.0


def test_pnl_no_fees():
    """Fees default to 0 when None."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=110.0,
        size=1.0, fees_usd=None, stop_loss_price=None,
    )
    assert r["pnl_usd"] == r["net_pnl_usd"]
    assert r["fees_usd"] is None


# ---------- R-multiple ----------

def test_r_multiple_long():
    """1R trade: entry 100, stop 95, exit 105 → R = net / risk."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=105.0,
        size=10.0, fees_usd=0.0, stop_loss_price=95.0,
    )
    # Risk per unit = 5, risk amount = 50. Gross PnL = 50. Net = 50.
    # R = 50 / 50 = 1.0
    assert r["r_multiple"] == pytest.approx(1.0, abs=0.01)


def test_r_multiple_2x():
    """2R trade: entry 100, stop 95, exit 110 → R = 2.0."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=110.0,
        size=10.0, fees_usd=0.0, stop_loss_price=95.0,
    )
    # Risk = 50. Net PnL = 100. R = 100/50 = 2.0
    assert r["r_multiple"] == pytest.approx(2.0, abs=0.01)


def test_r_multiple_negative():
    """Losing trade: entry 100, stop 95, exit 90 → R = -2.0."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=90.0,
        size=10.0, fees_usd=0.0, stop_loss_price=95.0,
    )
    # Risk = 50. Net PnL = -100. R = -100/50 = -2.0
    assert r["r_multiple"] == pytest.approx(-2.0, abs=0.01)


def test_r_multiple_none_without_stop_loss():
    """R-multiple should be None when stop_loss_price is missing."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=110.0,
        size=10.0, fees_usd=0.0, stop_loss_price=None,
    )
    assert r["r_multiple"] is None


def test_r_multiple_short_side():
    """R-multiple for a short trade."""
    r = _compute_pnl(
        side="short", entry_price=100.0, exit_price=90.0,
        size=10.0, fees_usd=0.0, stop_loss_price=105.0,
    )
    # Risk per unit = 5, risk = 50. Net PnL = 100. R = 2.0
    assert r["r_multiple"] == pytest.approx(2.0, abs=0.01)


# ---------- edge cases ----------

def test_pnl_zero_notional():
    """Zero entry price should not crash (pnl_pct defaults to 0)."""
    r = _compute_pnl(
        side="long", entry_price=0.0, exit_price=10.0,
        size=1.0, fees_usd=0.0, stop_loss_price=None,
    )
    assert r["pnl_pct"] == 0


def test_pnl_breakeven():
    """Entry == exit → 0 PnL."""
    r = _compute_pnl(
        side="long", entry_price=100.0, exit_price=100.0,
        size=10.0, fees_usd=2.0, stop_loss_price=95.0,
    )
    assert r["pnl_usd"] == 0.0
    assert r["net_pnl_usd"] == -2.0  # only fees


def test_pnl_very_small_size():
    """Sub-unit sizes should compute correctly."""
    r = _compute_pnl(
        side="long", entry_price=60000.0, exit_price=61000.0,
        size=0.001, fees_usd=0.1, stop_loss_price=59000.0,
    )
    assert r["pnl_usd"] == pytest.approx(1.0, abs=0.01)
