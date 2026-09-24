"""Oracle -> Telegram alert gate.

The old gate was |score| >= 65, which the combiner never reached (day one of
real data: -17..+15). These pin the replacement: rarity within a trailing
window, a confluence floor, and a warm-up so day one cannot page on noise.
"""
from __future__ import annotations

from app.modules.oracle.engine import agreeing_modules, should_alert
from app.services.telegram_service import service as tg

KW = dict(min_confluence=2, min_abs_score=20, percentile=0.97, min_sample=100)


def test_confluence_floor_blocks_first():
    ok, why = should_alert(60, 1, [], **KW)
    assert not ok and "confluence" in why


def test_absolute_floor_during_warmup():
    assert should_alert(19, 3, [], **KW)[0] is False
    ok, why = should_alert(20, 3, [], **KW)
    assert ok and "warmup" in why


def test_percentile_engages_only_with_enough_sample():
    trailing = list(range(1, 100))            # 99 samples: still warm-up
    assert should_alert(25, 2, trailing, **KW)[0] is True
    trailing = list(range(1, 101))            # 100 samples: p97 cut = 97
    assert should_alert(25, 2, trailing, **KW)[0] is False
    ok, why = should_alert(98, 2, trailing, **KW)
    assert ok and "p97" in why


def test_rarity_is_relative_not_absolute():
    """A 30 is rare on a flat day and ordinary on a wild one."""
    assert should_alert(30, 2, [5] * 150, **KW)[0] is True
    assert should_alert(30, 2, [80] * 150, **KW)[0] is False


def test_agreeing_modules_follow_the_sign():
    bd = {"whaleradar": {"direction": "bearish", "contribution": -1.1},
          "sentimentpulse": {"direction": "bullish", "contribution": 8.6},
          "radarx": {"direction": "neutral", "contribution": 0.0},
          "divergence": {"direction": "bullish", "contribution": 3.6}}
    assert agreeing_modules(bd, 12) == ["sentimentpulse", "divergence"]
    assert agreeing_modules(bd, -5) == ["whaleradar"]
    assert agreeing_modules(bd, 0) == []


def test_telegram_card_says_why():
    text = tg._format_alert("oracle", {
        "symbol": "SOLUSDT", "score": 34, "confluence_count": 3,
        "agreeing_modules": ["sentimentpulse", "divergence", "whaleradar"],
        "entry_price": 120.5, "stop_loss": 117.0, "take_profit": 127.0,
        "alert_reason": "|score| 34 >= p97 cut 31 (n=412)",
    })
    assert "SOLUSDT" in text and "long bias" in text
    assert "sentimentpulse, divergence, whaleradar" in text
    assert "p97" in text and "not advice" in text
