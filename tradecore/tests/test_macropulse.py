"""MacroPulse tests — macro scoring, VIX/DXY classification, context computation."""
from __future__ import annotations

import pytest

from app.modules.macropulse.score import (
    _dxy_trend,
    _etf_label,
    _risk_environment,
    _vix_level,
)


# ---------- _vix_level (pure) ----------

def test_vix_low():
    assert _vix_level(12.0) == "low"
    assert _vix_level(14.9) == "low"


def test_vix_medium():
    assert _vix_level(15.0) == "medium"
    assert _vix_level(20.0) == "medium"
    assert _vix_level(25.0) == "medium"


def test_vix_high():
    assert _vix_level(25.1) == "high"
    assert _vix_level(80.0) == "high"


def test_vix_none():
    assert _vix_level(None) == "neutral"


# ---------- _dxy_trend (pure) ----------

def test_dxy_rising():
    assert _dxy_trend(0.5) == "rising"
    assert _dxy_trend(0.11) == "rising"


def test_dxy_falling():
    assert _dxy_trend(-0.3) == "falling"
    assert _dxy_trend(-0.11) == "falling"


def test_dxy_neutral():
    assert _dxy_trend(0.0) == "neutral"
    assert _dxy_trend(0.05) == "neutral"
    assert _dxy_trend(-0.09) == "neutral"


def test_dxy_none():
    assert _dxy_trend(None) == "neutral"


# ---------- _etf_label (pure) ----------

def test_etf_positive():
    assert _etf_label(100_000_000.0) == "positive"


def test_etf_negative():
    assert _etf_label(-50_000_000.0) == "negative"


def test_etf_neutral():
    assert _etf_label(0.0) == "neutral"
    assert _etf_label(None) == "neutral"


# ---------- _risk_environment (pure) ----------

def test_risk_favorable():
    assert _risk_environment(30) == "favorable"
    assert _risk_environment(50) == "favorable"


def test_risk_neutral():
    assert _risk_environment(0) == "neutral"
    assert _risk_environment(20) == "neutral"


def test_risk_caution():
    assert _risk_environment(-10) == "caution"
    assert _risk_environment(-25) == "caution"


def test_risk_off():
    assert _risk_environment(-30) == "risk_off"
    assert _risk_environment(-60) == "risk_off"


# ---------- macro score computation (pure) ----------

def test_macro_score_computation():
    """Test the compute_macro_score binary step logic from collector.py."""
    from app.modules.macropulse.collector import compute_macro_score

    # All bullish: DXY falling, VIX low, yields falling, SPX up
    score, breakdown = compute_macro_score(
        dxy_change=-0.5,       # falling → +10
        vix_value=12.0,        # < 15  → +10
        us10y_change=-0.2,     # falling → +5
        sp500_change=1.0,      # positive → +10
    )
    assert score == 35  # 10+10+5+10 = 35 (max bullish)


def test_macro_score_all_bearish():
    from app.modules.macropulse.collector import compute_macro_score

    score, breakdown = compute_macro_score(
        dxy_change=0.5,        # rising → -10
        vix_value=30.0,        # > 25 → -15
        us10y_change=0.3,      # rising → -10
        sp500_change=-1.5,     # negative → -10
    )
    assert score == -45  # -10-15-10-10 = -45 (max bearish)


def test_macro_score_neutral():
    from app.modules.macropulse.collector import compute_macro_score

    score, breakdown = compute_macro_score(
        dxy_change=None, vix_value=None, us10y_change=None,
        sp500_change=None,
    )
    assert score == 0


def test_macro_score_clamped():
    """Score should always be within [-100, 100]."""
    from app.modules.macropulse.collector import compute_macro_score

    score, _ = compute_macro_score(
        dxy_change=-2.0, vix_value=10.0, us10y_change=-1.0,
        sp500_change=5.0,
    )
    assert -100 <= score <= 100


# ---------- asymmetry awareness ----------

def test_macro_score_asymmetry():
    """Document the known asymmetry: max bull=+35, max bear=-45."""
    from app.modules.macropulse.collector import compute_macro_score

    bull_score, _ = compute_macro_score(
        dxy_change=-1.0, vix_value=10.0, us10y_change=-1.0,
        sp500_change=2.0,
    )
    bear_score, _ = compute_macro_score(
        dxy_change=1.0, vix_value=40.0, us10y_change=1.0,
        sp500_change=-2.0,
    )
    assert bull_score == 35
    assert bear_score == -45
    # Intentional: macro is a risk filter with bearish bias
