"""Exchange-aware candle fetcher for the bot.

Bybit symbols are served from the in-process WS stream (``candles:{symbol}``
Redis list). Binance symbols are polled via the public REST klines API on
demand — the bot opens at most ``bot_max_concurrent`` Binance trades at a time
and the monitor ticks at ``bot_monitor_tick_seconds``, so request volume is
negligible vs Binance's per-IP weight budget.
"""
from __future__ import annotations

import httpx

from app.logging_config import log
from app.services import redis_service


BINANCE_PERP_BASE = "https://fapi.binance.com/fapi/v1"
BINANCE_SPOT_BASE = "https://api.binance.com/api/v3"
_TIMEOUT_S = 5.0


def _binance_url(symbol: str, market_type: str | None, limit: int) -> str:
    base = BINANCE_PERP_BASE if (market_type or "perp").lower() == "perp" else BINANCE_SPOT_BASE
    return f"{base}/klines?symbol={symbol}&interval=1m&limit={max(1, limit)}"


def _parse_binance_kline(row: list) -> dict:
    """Map Binance kline array to our internal {t,o,h,l,c,v} dict."""
    return {
        "t": int(row[0]),
        "o": float(row[1]),
        "h": float(row[2]),
        "l": float(row[3]),
        "c": float(row[4]),
        "v": float(row[5]),
    }


async def _fetch_binance_klines(symbol: str, market_type: str | None, limit: int) -> list[dict]:
    url = _binance_url(symbol, market_type, limit)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("bot_binance_klines_failed", symbol=symbol, market_type=market_type, err=str(e))
        return []
    if not isinstance(data, list):
        return []
    return [_parse_binance_kline(row) for row in data if isinstance(row, list) and len(row) >= 6]


async def get_recent_candles(
    symbol: str,
    exchange: str,
    market_type: str | None,
    limit: int = 5,
) -> list[dict]:
    """Return the most recent ``limit`` 1m bars, newest first (matches Redis list order)."""
    ex = (exchange or "").lower()
    if ex == "bybit":
        return await redis_service.get_candles(symbol, limit=limit)
    if ex == "binance":
        bars = await _fetch_binance_klines(symbol, market_type, limit)
        # Binance returns oldest-first; flip to match Redis "newest first" contract.
        return list(reversed(bars))
    return []


async def get_latest_candle(
    symbol: str,
    exchange: str,
    market_type: str | None,
) -> dict | None:
    """Return the latest 1m bar, or None."""
    ex = (exchange or "").lower()
    if ex == "bybit":
        return await redis_service.get_latest_candle(symbol)
    if ex == "binance":
        bars = await _fetch_binance_klines(symbol, market_type, limit=1)
        return bars[-1] if bars else None
    return None
