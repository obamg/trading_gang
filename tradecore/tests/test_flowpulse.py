"""FlowPulse tests — composite scoring, thresholds, signal direction."""
from __future__ import annotations

from app.modules.flowpulse.detector import (
    _compute_composite,
    BOOK_IMBALANCE_ALERT,
    TAKER_RATIO_ALERT,
    TOP_RATIO_EXTREME,
)


# ---------- composite direction + intensity ----------

def test_all_bullish_signals():
    book = {"imbalance": 2.5, "bid_usd": 500_000, "ask_usd": 200_000}
    taker = {"ratio": 1.8, "buy_vol": 9_000_000, "sell_vol": 5_000_000}
    top = {"long_ratio": 65.0, "short_ratio": 35.0}
    direction, intensity = _compute_composite(book, taker, top)
    assert direction == "bullish"
    assert intensity > 0.3


def test_all_bearish_signals():
    book = {"imbalance": 0.4, "bid_usd": 100_000, "ask_usd": 250_000}
    taker = {"ratio": 0.5, "buy_vol": 3_000_000, "sell_vol": 6_000_000}
    top = {"long_ratio": 35.0, "short_ratio": 65.0}
    direction, intensity = _compute_composite(book, taker, top)
    assert direction == "bearish"
    assert intensity > 0.3


def test_neutral_when_balanced():
    book = {"imbalance": 1.0, "bid_usd": 300_000, "ask_usd": 300_000}
    taker = {"ratio": 1.0, "buy_vol": 5_000_000, "sell_vol": 5_000_000}
    top = {"long_ratio": 50.0, "short_ratio": 50.0}
    direction, intensity = _compute_composite(book, taker, top)
    assert direction == "neutral"
    assert intensity == 0.0


def test_no_data_returns_neutral():
    direction, intensity = _compute_composite(None, None, None)
    assert direction == "neutral"
    assert intensity == 0.0


def test_partial_data_book_only():
    book = {"imbalance": 3.5, "bid_usd": 700_000, "ask_usd": 200_000}
    direction, intensity = _compute_composite(book, None, None)
    assert direction == "bullish"
    assert intensity > 0


def test_partial_data_taker_only_bearish():
    taker = {"ratio": 0.3, "buy_vol": 1_000_000, "sell_vol": 3_333_333}
    direction, intensity = _compute_composite(None, taker, None)
    assert direction == "bearish"
    assert intensity > 0


# ---------- alert thresholds ----------

def test_extreme_book_imbalance_triggers():
    assert 4.0 >= BOOK_IMBALANCE_ALERT
    assert 0.2 <= 1.0 / BOOK_IMBALANCE_ALERT


def test_extreme_taker_ratio_triggers():
    assert 2.5 >= TAKER_RATIO_ALERT
    assert 0.3 <= 1.0 / TAKER_RATIO_ALERT


def test_extreme_top_ratio_triggers():
    assert 75.0 >= TOP_RATIO_EXTREME


def test_normal_values_no_alert():
    assert 1.5 < BOOK_IMBALANCE_ALERT
    assert 1.3 < TAKER_RATIO_ALERT
    assert 60.0 < TOP_RATIO_EXTREME


# ---------- intensity clamping ----------

def test_intensity_never_exceeds_one():
    book = {"imbalance": 100.0, "bid_usd": 999_999, "ask_usd": 1}
    taker = {"ratio": 50.0, "buy_vol": 99_999, "sell_vol": 1}
    top = {"long_ratio": 99.0, "short_ratio": 1.0}
    _, intensity = _compute_composite(book, taker, top)
    assert intensity <= 1.0


def test_intensity_never_negative():
    book = {"imbalance": 0.01, "bid_usd": 1, "ask_usd": 999_999}
    _, intensity = _compute_composite(book, None, None)
    assert intensity >= 0.0
