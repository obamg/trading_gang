"""Redis data layer — candles, trades, liquidations, funding, OI, cooldowns, sessions, pubsub.

Key naming conventions (other teams depend on these EXACTLY):
  candles:{symbol}            list, last 50 OHLCV JSON dicts (lpush + ltrim)
  trades:{symbol}             Redis stream, max 1000 entries
  symbols:active              set of active symbol strings
  funding:{symbol}            float, TTL 3600s
  oi:{symbol}                 hash {oi_usd, oi_contracts}, TTL 300s
  liq_heatmap:{symbol}        hash {price_bucket: usd_size}
  cooldown:{module}:{symbol}  key with TTL (exists = on cooldown)
  session:{token_hash}        user_id, TTL = session ttl
  pubsub channel: alerts:{module}   alert dicts
  pubsub channel: liquidations       forceOrder events
"""
from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis, from_url

from app.config import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    """Module-level Redis client. Call init_redis() from app lifespan first."""
    if _redis is None:
        raise RuntimeError("Redis not initialised — call init_redis() in lifespan")
    return _redis


async def init_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(settings.redis_url, decode_responses=True)
        await _redis.ping()
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


# ---------- candles ----------

CANDLE_MAX = 50


async def push_candle(symbol: str, candle: dict) -> None:
    r = get_redis()
    key = f"candles:{symbol}"
    await r.lpush(key, json.dumps(candle))
    await r.ltrim(key, 0, CANDLE_MAX - 1)


async def get_candles(symbol: str, limit: int = 50) -> list[dict]:
    r = get_redis()
    raw = await r.lrange(f"candles:{symbol}", 0, limit - 1)
    return [json.loads(x) for x in raw]


async def get_latest_candle(symbol: str) -> dict | None:
    r = get_redis()
    raw = await r.lindex(f"candles:{symbol}", 0)
    return json.loads(raw) if raw else None


async def get_candle_at(symbol: str, target_ts_ms: int, tolerance_ms: int = 300_000) -> dict | None:
    """Return the candle whose open_time is closest to *target_ts_ms*.

    Returns None if no candle is within *tolerance_ms* (default 5 min).
    """
    candles = await get_candles(symbol, limit=CANDLE_MAX)
    if not candles:
        return None
    best: dict | None = None
    best_diff = float("inf")
    for c in candles:
        ts = int(c.get("open_time", 0))
        diff = abs(ts - target_ts_ms)
        if diff < best_diff:
            best_diff = diff
            best = c
    if best is not None and best_diff <= tolerance_ms:
        return best
    return None


# ---------- trades stream ----------

TRADES_STREAM_MAX = 1000


async def push_trade(symbol: str, trade: dict) -> None:
    r = get_redis()
    fields = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in trade.items()}
    await r.xadd(f"trades:{symbol}", fields, maxlen=TRADES_STREAM_MAX, approximate=True)


async def read_trades(symbol: str, count: int = 100) -> list[dict]:
    r = get_redis()
    entries = await r.xrevrange(f"trades:{symbol}", count=count)
    return [{"id": eid, **fields} for eid, fields in entries]


# ---------- symbol list ----------

async def set_symbol_list(symbols: list[str]) -> None:
    r = get_redis()
    async with r.pipeline(transaction=True) as pipe:
        pipe.delete("symbols:active")
        if symbols:
            pipe.sadd("symbols:active", *symbols)
        await pipe.execute()


async def get_symbol_list() -> list[str]:
    r = get_redis()
    return sorted(await r.smembers("symbols:active"))


# ---------- funding rates ----------

async def set_funding_rate(symbol: str, rate: float, ttl: int = 3600) -> None:
    r = get_redis()
    await r.set(f"funding:{symbol}", str(rate), ex=ttl)


async def get_funding_rate(symbol: str) -> float | None:
    r = get_redis()
    v = await r.get(f"funding:{symbol}")
    return float(v) if v is not None else None


# ---------- open interest ----------

async def set_open_interest(symbol: str, data: dict, ttl: int = 300) -> None:
    r = get_redis()
    key = f"oi:{symbol}"
    await r.hset(key, mapping={k: str(v) for k, v in data.items()})
    await r.expire(key, ttl)


async def get_open_interest(symbol: str) -> dict | None:
    r = get_redis()
    data = await r.hgetall(f"oi:{symbol}")
    if not data:
        return None
    out: dict[str, Any] = {}
    for k, v in data.items():
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


# ---------- liquidation heatmap ----------

def _price_bucket(price: float, bucket_pct: float = 0.005) -> str:
    """Bucket price to 0.5% precision for heatmap aggregation."""
    if price <= 0:
        return "0"
    import math
    step = price * bucket_pct
    bucket = round(price / step) * step
    return f"{bucket:.8f}"


async def update_liquidation_heatmap(symbol: str, price: float, size: float, side: str) -> None:
    r = get_redis()
    key = f"liq_heatmap:{symbol}"
    field = f"{side}:{_price_bucket(price)}"
    await r.hincrbyfloat(key, field, size)
    await r.expire(key, 3600)


async def get_liquidation_heatmap(symbol: str) -> dict[str, float]:
    r = get_redis()
    data = await r.hgetall(f"liq_heatmap:{symbol}")
    return {k: float(v) for k, v in data.items()}


# ---------- alert cooldowns ----------

async def set_alert_cooldown(module: str, symbol: str, minutes: int) -> None:
    r = get_redis()
    await r.set(f"cooldown:{module}:{symbol}", "1", ex=minutes * 60)


async def is_on_cooldown(module: str, symbol: str) -> bool:
    r = get_redis()
    return bool(await r.exists(f"cooldown:{module}:{symbol}"))


# ---------- user session cache ----------

async def set_user_session(token_hash: str, user_id: str, ttl_seconds: int) -> None:
    r = get_redis()
    await r.set(f"session:{token_hash}", user_id, ex=ttl_seconds)


async def get_user_session(token_hash: str) -> str | None:
    r = get_redis()
    return await r.get(f"session:{token_hash}")


async def invalidate_user_session(token_hash: str) -> None:
    r = get_redis()
    await r.delete(f"session:{token_hash}")


# ---------- pubsub alerts ----------

async def publish_alert(module: str, alert: dict) -> None:
    r = get_redis()
    await r.publish(f"alerts:{module}", json.dumps(alert, default=str))


async def publish_liquidation(event: dict) -> None:
    r = get_redis()
    await r.publish("liquidations", json.dumps(event, default=str))
