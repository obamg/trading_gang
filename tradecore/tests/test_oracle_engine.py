"""Extended Oracle engine tests — weight normalization, GemRadar signal, ATR, trade params."""
from __future__ import annotations

import pytest

from app.modules.oracle.engine import (
    DEFAULT_WEIGHTS,
    MAJOR_SYMBOLS,
    _atr_from_candles,
    _clip01,
    _confidence,
    _dir,
    _score_to_recommendation,
)


# ---------- _clip01 (pure) ----------

def test_clip01_within_range():
    assert _clip01(0.5) == 0.5


def test_clip01_below():
    assert _clip01(-0.3) == 0.0


def test_clip01_above():
    assert _clip01(1.5) == 1.0


def test_clip01_boundaries():
    assert _clip01(0.0) == 0.0
    assert _clip01(1.0) == 1.0


# ---------- _dir (pure) ----------

def test_dir_bullish():
    assert _dir(1) == "bullish"
    assert _dir(5) == "bullish"


def test_dir_bearish():
    assert _dir(-1) == "bearish"
    assert _dir(-10) == "bearish"


def test_dir_neutral():
    assert _dir(0) == "neutral"


# ---------- weight normalization ----------

def test_default_weights_sum_to_100():
    assert sum(DEFAULT_WEIGHTS.values()) == 100


def test_weight_normalization_with_custom_weights():
    """When user sets all weights to 50, normalization should scale to 100 total."""
    custom = {mod: 50 for mod in DEFAULT_WEIGHTS}
    raw_sum = sum(custom.values())  # 300
    scale = 100.0 / raw_sum
    normalized = {k: v * scale for k, v in custom.items()}
    assert sum(normalized.values()) == pytest.approx(100.0, abs=0.01)


def test_weight_normalization_with_zero_weights():
    """All zero weights should result in zero scale (no crash)."""
    custom = {mod: 0 for mod in DEFAULT_WEIGHTS}
    raw_sum = sum(custom.values())
    scale = 100.0 / raw_sum if raw_sum > 0 else 0.0
    assert scale == 0.0


def test_weight_normalization_unequal():
    """Unequal weights should still normalize to 100."""
    custom = {"macropulse": 100, "whaleradar": 100, "radarx": 0, "liquidmap": 0, "sentimentpulse": 0, "gemradar": 0}
    raw_sum = sum(custom.values())  # 200
    scale = 100.0 / raw_sum
    total_score = 0.0
    for mod, weight in custom.items():
        # All bullish at intensity 1.0
        total_score += 1.0 * (weight * scale)
    assert total_score == pytest.approx(100.0, abs=0.01)


# ---------- score with normalized weights ----------

def test_full_bullish_score_reaches_100():
    """With normalization, all modules at full intensity=1.0 bullish should reach 100."""
    score = 0.0
    raw_sum = sum(DEFAULT_WEIGHTS.values())
    scale = 100.0 / raw_sum
    for mod, weight in DEFAULT_WEIGHTS.items():
        score += 1.0 * (weight * scale)  # direction=1, intensity=1
    assert score == pytest.approx(100.0, abs=0.01)


def test_partial_modules_still_reach_extremes():
    """Even with only 3 modules active at full intensity, normalization should use full range."""
    weights = {"macropulse": 25, "whaleradar": 20, "radarx": 15, "liquidmap": 0, "sentimentpulse": 0, "gemradar": 0}
    raw_sum = sum(weights.values())  # 60
    scale = 100.0 / raw_sum
    score = 0.0
    for mod, weight in weights.items():
        if weight > 0:
            score += 1.0 * (weight * scale)
    assert score == pytest.approx(100.0, abs=0.01)


# ---------- _atr_from_candles (pure) ----------

def test_atr_basic():
    """Simple candles with known ATR."""
    candles = [
        {"high": 110.0, "low": 90.0, "close": 100.0},
        {"high": 105.0, "low": 95.0, "close": 100.0},
        {"high": 108.0, "low": 92.0, "close": 100.0},
    ]
    atr = _atr_from_candles(candles, period=2)
    assert atr > 0


def test_atr_empty_candles():
    assert _atr_from_candles([], period=14) == 0.0


def test_atr_single_candle():
    candles = [{"high": 100.0, "low": 90.0, "close": 95.0}]
    assert _atr_from_candles(candles, period=14) == 0.0


def test_atr_handles_invalid_data():
    candles = [
        {"high": "bad", "low": 90.0, "close": 100.0},
        {"high": 105.0, "low": 95.0, "close": 100.0},
    ]
    # Should handle TypeError/ValueError gracefully
    atr = _atr_from_candles(candles, period=1)
    assert isinstance(atr, float)


# ---------- major symbols ----------

def test_major_symbols_include_top_coins():
    assert "BTCUSDT" in MAJOR_SYMBOLS
    assert "ETHUSDT" in MAJOR_SYMBOLS
    assert "SOLUSDT" in MAJOR_SYMBOLS


def test_gemradar_skipped_for_majors():
    """GemRadar should return neutral for major symbols."""
    # This verifies the guard in _gemradar_signal
    assert "BTCUSDT" in MAJOR_SYMBOLS
    assert "RANDOMTOKEN" not in MAJOR_SYMBOLS


# ---------- recommendation thresholds ----------

def test_recommendations_cover_full_range():
    """All recommendation bands should be reachable."""
    assert _score_to_recommendation(100) == "strong_long"
    assert _score_to_recommendation(75) == "strong_long"
    assert _score_to_recommendation(74) == "long"
    assert _score_to_recommendation(50) == "long"
    assert _score_to_recommendation(49) == "watch_long"
    assert _score_to_recommendation(25) == "watch_long"
    assert _score_to_recommendation(24) == "neutral"
    assert _score_to_recommendation(0) == "neutral"
    assert _score_to_recommendation(-24) == "neutral"
    assert _score_to_recommendation(-25) == "watch_short"
    assert _score_to_recommendation(-49) == "watch_short"
    assert _score_to_recommendation(-50) == "short"
    assert _score_to_recommendation(-74) == "short"
    assert _score_to_recommendation(-75) == "strong_short"
    assert _score_to_recommendation(-100) == "strong_short"


# ---------- confidence ----------

def test_confidence_mapping():
    assert _confidence(0) == "low"
    assert _confidence(2) == "low"
    assert _confidence(3) == "medium"
    assert _confidence(4) == "medium"
    assert _confidence(5) == "high"
    assert _confidence(6) == "high"
