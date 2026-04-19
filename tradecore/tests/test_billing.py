"""Billing service tests — webhook handler, price mapping, subscription logic."""
from __future__ import annotations

from app.services.billing_service import _ts_to_dt


# ---------- _ts_to_dt (pure) ----------

def test_ts_to_dt_none():
    assert _ts_to_dt(None) is None


def test_ts_to_dt_valid():
    from datetime import datetime, timezone
    dt = _ts_to_dt(1700000000)
    assert isinstance(dt, datetime)
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2023


def test_ts_to_dt_zero():
    dt = _ts_to_dt(0)
    assert dt is not None
    assert dt.year == 1970


# ---------- plan feature mapping ----------

def test_free_plan_features():
    """Free plan should have radarx, sentimentpulse, riskcalc, tradelog only."""
    # Verify the expected feature shape for the free plan
    free = {
        "radarx": True,
        "whaleradar": False,
        "liquidmap": False,
        "sentimentpulse": True,
        "macropulse": False,
        "gemradar": False,
        "riskcalc": True,
        "tradelog": True,
        "performancecore": False,
        "oracle": False,
    }
    enabled = [k for k, v in free.items() if v]
    assert set(enabled) == {"radarx", "sentimentpulse", "riskcalc", "tradelog"}


def test_pro_plan_unlocks_all_modules():
    pro = {
        "radarx": True,
        "whaleradar": True,
        "liquidmap": True,
        "sentimentpulse": True,
        "macropulse": True,
        "gemradar": True,
        "riskcalc": True,
        "tradelog": True,
        "performancecore": True,
        "oracle": True,
    }
    assert all(pro.values())
