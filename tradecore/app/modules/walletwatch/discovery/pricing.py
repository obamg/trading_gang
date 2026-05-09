"""Token historical price series for PnL scoring.

CoinGecko's ``market_chart/range`` endpoint returns auto-resolution price
points: 5-min for 1 day, hourly for ≤90d, daily for >90d. We cache the full
series per (chain, token, from_bucket, to_bucket) in Redis for 6h so we
don't pay a CG call every time we re-score a token.
"""
from __future__ import annotations

import json
from typing import List, Tuple

import httpx

from app.config import settings as app_settings
from app.logging_config import log
from app.modules.walletwatch.pricing import CG_BASE, CG_PLATFORM
from app.services import redis_service

HISTORY_TTL_SECONDS = 6 * 3600


def _series_cache_key(chain: str, addr: str, from_ms: int, to_ms: int) -> str:
    bucket_from = from_ms // (3600 * 1000)
    bucket_to = to_ms // (3600 * 1000)
    a = addr.lower() if chain != "solana" else addr
    return f"walletwatch:price_series:{chain}:{a}:{bucket_from}:{bucket_to}"


async def get_price_series(
    chain: str,
    addr: str,
    from_ts_ms: int,
    to_ts_ms: int,
) -> List[Tuple[int, float]]:
    """Hourly (ts_ms, price_usd) pairs for the range. Empty list on miss."""
    platform = CG_PLATFORM.get(chain)
    if not platform:
        return []
    r = redis_service.get_redis()
    key = _series_cache_key(chain, addr, from_ts_ms, to_ts_ms)
    cached = await r.get(key)
    if cached:
        try:
            return [(int(p[0]), float(p[1])) for p in json.loads(cached)]
        except (ValueError, TypeError):
            pass

    contract_path = addr.lower() if chain != "solana" else addr
    url = f"{CG_BASE}/coins/{platform}/contract/{contract_path}/market_chart/range"
    params = {
        "vs_currency": "usd",
        "from": from_ts_ms // 1000,
        "to": to_ts_ms // 1000,
    }
    headers = {}
    cg_key = getattr(app_settings, "coingecko_api_key", "") or ""
    if cg_key:
        headers["x-cg-demo-api-key"] = cg_key
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.debug("discovery_price_series_failed", chain=chain, addr=addr, err=str(e))
        return []

    prices = data.get("prices") or []
    series: List[Tuple[int, float]] = [(int(ms), float(p)) for ms, p in prices]
    if series:
        await r.set(key, json.dumps(series), ex=HISTORY_TTL_SECONDS)
    return series


def price_at(series: List[Tuple[int, float]], ts_ms: int) -> float | None:
    """Closest price in the series to ``ts_ms``. None if series is empty."""
    if not series:
        return None
    closest = min(series, key=lambda x: abs(x[0] - ts_ms))
    return closest[1]


__all__ = ["get_price_series", "price_at"]
