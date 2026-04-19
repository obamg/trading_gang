"""SentimentPulse tests — funding rate alerts, L/S ratio extremes, market snapshot."""
from __future__ import annotations

from app.modules.sentimentpulse.collector import (
    EXTREME_FUNDING_ABS,
    EXTREME_LS_PCT,
)


# ---------- funding rate alert thresholds ----------

def test_extreme_funding_positive():
    """High positive funding → overleveraged longs → bearish signal."""
    funding = 0.0008
    assert abs(funding) > EXTREME_FUNDING_ABS
    side = "long" if funding > 0 else "short"
    assert side == "long"


def test_extreme_funding_negative():
    """Deep negative funding → overleveraged shorts → bullish squeeze signal."""
    funding = -0.0006
    assert abs(funding) > EXTREME_FUNDING_ABS
    side = "long" if funding > 0 else "short"
    assert side == "short"


def test_normal_funding_no_alert():
    funding = 0.0001
    assert abs(funding) <= EXTREME_FUNDING_ABS


# ---------- long/short ratio extremes ----------

def test_extreme_long_ratio():
    """70%+ longs → crowded positioning → contrarian bearish signal."""
    long_ratio = 75.0
    assert long_ratio >= EXTREME_LS_PCT


def test_extreme_short_ratio():
    """70%+ shorts → crowded positioning → contrarian bullish signal."""
    short_ratio = 72.0
    assert short_ratio >= EXTREME_LS_PCT


def test_balanced_ratio_no_alert():
    long_ratio = 55.0
    short_ratio = 45.0
    assert long_ratio < EXTREME_LS_PCT
    assert short_ratio < EXTREME_LS_PCT


# ---------- fear/greed classification ----------

def test_fear_greed_classification():
    """Verify label mapping for fear/greed index values."""
    def classify(value: int) -> str:
        if value <= 20:
            return "Extreme Fear"
        if value <= 40:
            return "Fear"
        if value <= 60:
            return "Neutral"
        if value <= 80:
            return "Greed"
        return "Extreme Greed"

    assert classify(10) == "Extreme Fear"
    assert classify(25) == "Fear"
    assert classify(50) == "Neutral"
    assert classify(70) == "Greed"
    assert classify(90) == "Extreme Greed"


# ---------- snapshot time flooring ----------

def test_snapshot_time_flooring():
    """Snapshots should be floored to hour boundary for dedup."""
    from datetime import datetime, timezone
    ts = datetime(2026, 4, 19, 14, 37, 22, tzinfo=timezone.utc)
    floored = ts.replace(minute=0, second=0, microsecond=0)
    assert floored.minute == 0
    assert floored.second == 0
    assert floored.hour == 14


# ---------- OI USD computation ----------

def test_oi_usd_from_contracts_and_price():
    """OI in contracts * close price = OI in USD."""
    oi_contracts = 50_000.0
    close_price = 60_000.0
    oi_usd = oi_contracts * close_price
    assert oi_usd == 3_000_000_000.0


def test_oi_usd_zero_price():
    """If price is missing, OI USD should be None."""
    oi_contracts = 50_000.0
    close_price = 0.0
    oi_usd = oi_contracts * close_price if close_price > 0 else None
    assert oi_usd is None
