"""Redis service tests — candle storage, cooldown, get_candle_at."""
from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_push_and_get_latest_candle(fake_redis):
    from app.services import redis_service

    candle = {"open_time": 1000, "close": 60000.0, "high": 61000.0, "low": 59000.0, "volume": 100.0}
    await redis_service.push_candle("BTCUSDT", candle)

    latest = await redis_service.get_latest_candle("BTCUSDT")
    assert latest is not None
    assert latest["close"] == 60000.0


@pytest.mark.asyncio
async def test_get_candles_ordering(fake_redis):
    from app.services import redis_service

    for i in range(5):
        await redis_service.push_candle("ETHUSDT", {
            "open_time": 1000 * i,
            "close": 3000.0 + i,
        })

    candles = await redis_service.get_candles("ETHUSDT", limit=5)
    assert len(candles) == 5
    # Newest first (highest open_time)
    assert candles[0]["open_time"] == 4000


@pytest.mark.asyncio
async def test_get_candle_at_exact_match(fake_redis):
    from app.services import redis_service

    target_ts = 300_000  # 5 min in ms
    for i in range(5):
        await redis_service.push_candle("BTCUSDT", {
            "open_time": i * 300_000,
            "close": 60000.0 + i * 100,
        })

    result = await redis_service.get_candle_at("BTCUSDT", target_ts, tolerance_ms=300_000)
    assert result is not None
    assert result["open_time"] == target_ts


@pytest.mark.asyncio
async def test_get_candle_at_within_tolerance(fake_redis):
    from app.services import redis_service

    await redis_service.push_candle("BTCUSDT", {
        "open_time": 300_000,
        "close": 60100.0,
    })
    # Target 310_000 is 10s after candle open — within 5min tolerance
    result = await redis_service.get_candle_at("BTCUSDT", 310_000, tolerance_ms=300_000)
    assert result is not None


@pytest.mark.asyncio
async def test_get_candle_at_outside_tolerance(fake_redis):
    from app.services import redis_service

    await redis_service.push_candle("BTCUSDT", {
        "open_time": 300_000,
        "close": 60100.0,
    })
    # Target is way off — 10 minutes away with tight tolerance
    result = await redis_service.get_candle_at("BTCUSDT", 900_000, tolerance_ms=60_000)
    assert result is None


@pytest.mark.asyncio
async def test_get_candle_at_empty(fake_redis):
    from app.services import redis_service

    result = await redis_service.get_candle_at("NOTHING", 300_000)
    assert result is None


@pytest.mark.asyncio
async def test_cooldown_set_and_check(fake_redis):
    from app.services import redis_service

    symbol = "BTCUSDT"
    module = "radarx"

    # Not on cooldown initially
    on_cd = await redis_service.is_on_cooldown(module, symbol)
    assert not on_cd

    # Set cooldown
    await redis_service.set_alert_cooldown(module, symbol, minutes=30)

    # Now on cooldown
    on_cd = await redis_service.is_on_cooldown(module, symbol)
    assert on_cd


@pytest.mark.asyncio
async def test_candle_buffer_trim(fake_redis):
    from app.services import redis_service

    # Push more than CANDLE_MAX candles
    for i in range(60):
        await redis_service.push_candle("SOLUSDT", {"open_time": i * 1000, "close": 100.0 + i})

    candles = await redis_service.get_candles("SOLUSDT", limit=100)
    assert len(candles) <= redis_service.CANDLE_MAX
