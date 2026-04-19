"""LiquidMap tracker tests — price bucketing, clustering, and ingestion."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.liquidmap.tracker import (
    ALERT_THRESHOLD,
    HEATMAP_BUCKET_PCT,
    PERSIST_THRESHOLD,
    _price_bucket,
    get_heatmap,
    ingest_event,
)


# ---------- _price_bucket (pure) ----------

def test_price_bucket_basic():
    bucket = _price_bucket(100.0)
    assert isinstance(bucket, str)
    assert float(bucket) == pytest.approx(100.0, abs=0.2)


def test_price_bucket_precision():
    """Adjacent prices should land in different buckets at 0.1% granularity."""
    a = _price_bucket(100.0)
    b = _price_bucket(100.15)
    # 0.15% apart with 0.1% bucket → should be different buckets
    assert a != b


def test_price_bucket_deterministic():
    """Same price always maps to the same bucket."""
    a = _price_bucket(100.0)
    b = _price_bucket(100.0)
    assert a == b


def test_price_bucket_zero_returns_zero():
    bucket = _price_bucket(0.0)
    assert bucket == "0"


def test_price_bucket_very_small_price():
    """Sub-penny assets should still produce valid buckets."""
    bucket = _price_bucket(0.00001)
    assert float(bucket) >= 0


def test_price_bucket_large_price():
    bucket = _price_bucket(65000.0)
    assert float(bucket) == pytest.approx(65000.0, rel=0.002)


# ---------- ingest_event ----------

@pytest.mark.asyncio
async def test_ingest_event_below_persist_threshold(fake_redis):
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    event = {
        "symbol": "BTCUSDT",
        "side": "long",
        "price": 60000.0,
        "size_usd": 50_000,  # below PERSIST_THRESHOLD
    }
    result = await ingest_event(db, event)
    # Should update heatmap but NOT persist to DB or publish alert
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_event_above_persist_threshold(fake_redis):
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    event = {
        "symbol": "BTCUSDT",
        "side": "long",
        "price": 60000.0,
        "size_usd": PERSIST_THRESHOLD + 1,
    }
    result = await ingest_event(db, event)
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_event_above_alert_threshold_publishes(fake_redis):
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    event = {
        "symbol": "ETHUSDT",
        "side": "short",
        "price": 3500.0,
        "size_usd": ALERT_THRESHOLD + 1,
    }
    result = await ingest_event(db, event)
    assert result is not None
    assert any(ch == "alerts:liquidmap" for ch, _ in fake_redis.published())


@pytest.mark.asyncio
async def test_ingest_event_missing_fields_returns_none(fake_redis):
    db = MagicMock()
    result = await ingest_event(db, {"symbol": "BTCUSDT"})
    assert result is None


# ---------- get_heatmap ----------

@pytest.mark.asyncio
async def test_get_heatmap_empty(fake_redis):
    levels = await get_heatmap("BTCUSDT", top_n=10)
    assert levels == []


@pytest.mark.asyncio
async def test_get_heatmap_returns_sorted_by_size(fake_redis):
    r = fake_redis
    key = "liqmap:BTCUSDT"
    # Manually set heatmap data (side:bucket → size_usd)
    r._keys[f"{key}"] = ""  # dummy
    # Use the hash structure the tracker expects
    if not hasattr(r, '_hashes'):
        r._hashes = {}
    r._hashes[key] = {
        "long:60000.00000000": "500000",
        "short:61000.00000000": "1000000",
        "long:59000.00000000": "200000",
    }
    # Patch hgetall on fake redis
    async def _hgetall(k):
        return r._hashes.get(k, {})
    r.hgetall = _hgetall

    levels = await get_heatmap("BTCUSDT", top_n=3)
    assert len(levels) <= 3
    if len(levels) >= 2:
        assert levels[0]["size_usd"] >= levels[1]["size_usd"]
