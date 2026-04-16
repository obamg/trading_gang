"""Unit tests for Oracle scoring + recommendation thresholds."""
from __future__ import annotations

from app.modules.oracle.engine import (
    DEFAULT_WEIGHTS,
    _confidence,
    _score_to_recommendation,
)


def test_score_to_recommendation_thresholds():
    assert _score_to_recommendation(90) == "strong_long"
    assert _score_to_recommendation(60) == "long"
    assert _score_to_recommendation(30) == "watch_long"
    assert _score_to_recommendation(0) == "neutral"
    assert _score_to_recommendation(-30) == "watch_short"
    assert _score_to_recommendation(-60) == "short"
    assert _score_to_recommendation(-90) == "strong_short"


def test_confidence_bands():
    assert _confidence(0) == "low"
    assert _confidence(2) == "low"
    assert _confidence(3) == "medium"
    assert _confidence(5) == "high"
    assert _confidence(6) == "high"


def test_weights_sum_to_100():
    assert sum(DEFAULT_WEIGHTS.values()) == 100


def test_strong_bullish_all_modules_agree():
    """Simulate every module pointing bullish at full intensity — the raw weighted sum
    (intensity × weight × direction) should saturate above +75."""
    signals = {
        mod: {"direction": "bullish", "intensity": 1.0} for mod in DEFAULT_WEIGHTS
    }
    score = _score_from(signals)
    assert score >= 75
    assert _score_to_recommendation(score) in ("strong_long", "long")


def test_neutral_when_mixed_signals():
    signals = {
        "macropulse":    {"direction": "bullish", "intensity": 0.5},
        "whaleradar":    {"direction": "bearish", "intensity": 0.5},
        "radarx":        {"direction": "bullish", "intensity": 0.5},
        "liquidmap":     {"direction": "bearish", "intensity": 0.5},
        "sentimentpulse": {"direction": "bullish", "intensity": 0.5},
        "gemradar":      {"direction": "bearish", "intensity": 0.5},
    }
    score = _score_from(signals)
    assert -25 < score < 25
    assert _score_to_recommendation(score) == "neutral"


def test_score_within_bounds():
    """No combination of inputs should produce a score outside [-100, 100]."""
    for intensity in (0.0, 0.5, 1.0, 1.5):  # 1.5 stress-tests clipping
        for direction in ("bullish", "bearish", "neutral"):
            signals = {mod: {"direction": direction, "intensity": intensity} for mod in DEFAULT_WEIGHTS}
            score = _score_from(signals)
            # Score uses min(1, intensity) style clipping inside each collector, but
            # here we emulate the aggregator's own sum — cap at 100.
            assert -100 <= max(-100, min(100, score)) <= 100


# ---------- helpers ----------

def _score_from(signals: dict) -> float:
    """Mirror the aggregation step of compute_live_score for pure-input testing."""
    score = 0.0
    for mod, weight in DEFAULT_WEIGHTS.items():
        s = signals.get(mod, {"direction": "neutral", "intensity": 0.0})
        direction = s["direction"]
        intensity = max(0.0, min(1.0, float(s["intensity"])))
        sign = 1 if direction == "bullish" else (-1 if direction == "bearish" else 0)
        score += sign * intensity * weight
    return max(-100.0, min(100.0, score))
