"""RadarX detector tests — use FakeRedis + mocked DB session so we never touch Postgres."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.radarx import detector


def _candles(volumes: list[float], close: float = 100.0) -> list[dict]:
    """Return candles in newest-first order (matches Redis lpush convention)."""
    return [
        {
            "open_time": 1_000 * i,
            "close_time": 1_000 * i + 999,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": v,
            "quote_volume": v * close,
        }
        for i, v in enumerate(volumes)
    ]


async def _prime_candles(fake_redis, symbol: str, volumes: list[float]):
    key = f"candles:{symbol}"
    # Newest first
    for candle in _candles(volumes):
        fake_redis._lists.setdefault(key, []).append(json.dumps(candle))


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda row: setattr(row, "id", "row-uuid"))
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_detector_fires_above_threshold(fake_redis):
    # 20 baseline candles at volume 100, current candle at volume 2000 — huge spike.
    symbol = "BTCUSDT"
    # Baseline has small natural variance (so std>0), current is a huge spike.
    volumes = [2000.0] + [90.0 + (i % 5) * 4 for i in range(20)]
    await _prime_candles(fake_redis, symbol, volumes)

    db = _mock_db()
    alert = await detector.detect_symbol(
        db,
        symbol,
        volume_24h_usd=1_000_000_000,  # passes min volume gate
        z_threshold=3.0,
        ratio_threshold=4.0,
        min_volume_usd=10_000_000,
    )
    assert alert is not None
    assert alert["symbol"] == symbol
    assert alert["z_score"] > 3
    assert alert["ratio"] > 4
    db.add.assert_called_once()
    db.commit.assert_awaited()
    # Should have published on alerts:radarx
    assert any(ch == "alerts:radarx" for ch, _ in fake_redis.published())


@pytest.mark.asyncio
async def test_detector_does_not_fire_below_threshold(fake_redis):
    # Current volume is only 1.5× the baseline — nowhere near 4×/z=3.
    symbol = "ETHUSDT"
    volumes = [150.0] + [100.0] * 20
    await _prime_candles(fake_redis, symbol, volumes)

    db = _mock_db()
    alert = await detector.detect_symbol(
        db, symbol, volume_24h_usd=1_000_000_000,
    )
    assert alert is None
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_cooldown_prevents_duplicate_alert(fake_redis):
    symbol = "SOLUSDT"
    # Baseline has small natural variance (so std>0), current is a huge spike.
    volumes = [2000.0] + [90.0 + (i % 5) * 4 for i in range(20)]
    await _prime_candles(fake_redis, symbol, volumes)
    # Pre-set cooldown
    await fake_redis.set(f"cooldown:radarx:{symbol}", "1", ex=1800)

    db = _mock_db()
    alert = await detector.detect_symbol(
        db, symbol, volume_24h_usd=1_000_000_000,
    )
    assert alert is None
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_volume_gate_blocks_low_liquidity_symbol(fake_redis):
    symbol = "LOWVOL"
    # Baseline has small natural variance (so std>0), current is a huge spike.
    volumes = [2000.0] + [90.0 + (i % 5) * 4 for i in range(20)]
    await _prime_candles(fake_redis, symbol, volumes)

    db = _mock_db()
    # 24h volume well below the min — detector should reject before doing any work.
    alert = await detector.detect_symbol(
        db, symbol, volume_24h_usd=100_000, min_volume_usd=10_000_000,
    )
    assert alert is None
