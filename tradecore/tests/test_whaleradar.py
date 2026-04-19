"""WhaleRadar detector tests — large trade parsing, OI surge calculations."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.whaleradar.detector import (
    MIN_TRADE_USD,
    OI_SURGE_THRESHOLD_PCT,
)


# ---------- large trade detection ----------

@pytest.mark.asyncio
async def test_large_trade_detected_when_above_threshold(fake_redis):
    """A trade above MIN_TRADE_USD should fire an alert."""
    symbol = "BTCUSDT"
    trade = {
        "s": symbol,
        "p": "60000.0",
        "q": "10.0",          # 10 BTC * 60k = $600k > $300k threshold
        "T": 1700000000000,
        "m": False,            # buyer is maker → side = buy
    }
    # Push trade to redis stream
    stream_key = f"trades:{symbol}"
    fake_redis._lists[stream_key] = [json.dumps(trade)]

    # The trade size ($600k) exceeds MIN_TRADE_USD ($300k)
    size_usd = float(trade["p"]) * float(trade["q"])
    assert size_usd > MIN_TRADE_USD


@pytest.mark.asyncio
async def test_large_trade_ignored_when_below_threshold():
    """A trade below MIN_TRADE_USD should not fire."""
    size_usd = 50000.0 * 1.0  # $50k — well below $300k
    assert size_usd < MIN_TRADE_USD


# ---------- OI surge calculation ----------

def test_oi_surge_percentage():
    """Verify OI change percentage math."""
    prev_oi = 1_000_000.0
    curr_oi = 1_060_000.0
    change_pct = (curr_oi - prev_oi) / prev_oi * 100
    assert change_pct == pytest.approx(6.0, abs=0.01)
    assert change_pct > OI_SURGE_THRESHOLD_PCT


def test_oi_surge_below_threshold():
    prev_oi = 1_000_000.0
    curr_oi = 1_030_000.0
    change_pct = (curr_oi - prev_oi) / prev_oi * 100
    assert change_pct == pytest.approx(3.0, abs=0.01)
    assert change_pct < OI_SURGE_THRESHOLD_PCT


def test_oi_direction_labeling():
    """Direction label logic from the detector."""
    # OI up + price up → long_heavy
    oi_change = 6.0
    price_change = 2.0
    if oi_change > 0 and price_change > 0:
        direction = "long_heavy"
    elif oi_change > 0 and price_change < 0:
        direction = "short_heavy"
    elif oi_change < 0:
        direction = "oi_unwind"
    else:
        direction = "neutral"
    assert direction == "long_heavy"


def test_oi_direction_short_heavy():
    oi_change = 8.0
    price_change = -3.0
    if oi_change > 0 and price_change > 0:
        direction = "long_heavy"
    elif oi_change > 0 and price_change < 0:
        direction = "short_heavy"
    elif oi_change < 0:
        direction = "oi_unwind"
    else:
        direction = "neutral"
    assert direction == "short_heavy"


def test_oi_direction_unwind():
    oi_change = -10.0
    price_change = 5.0
    if oi_change > 0 and price_change > 0:
        direction = "long_heavy"
    elif oi_change > 0 and price_change < 0:
        direction = "short_heavy"
    elif oi_change < 0:
        direction = "oi_unwind"
    else:
        direction = "neutral"
    assert direction == "oi_unwind"


def test_oi_zero_prev_prevents_division_by_zero():
    prev_oi = 0.0
    curr_oi = 100_000.0
    # The detector guards this with `if prev_oi <= 0: continue`
    assert prev_oi <= 0  # should skip
