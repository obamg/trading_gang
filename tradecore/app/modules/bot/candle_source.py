"""Exchange-aware candle fetcher for the bot.

Bybit symbols are served from the in-process WS stream (``candles:{symbol}``
Redis list, **5m** bars). Binance symbols are polled via the public REST klines
API on demand — the bot opens at most ``bot_max_concurrent`` Binance trades at a
time and the monitor ticks at ``bot_monitor_tick_seconds``, so request volume is
negligible vs Binance's per-IP weight budget.

Both exchanges use the **same 5m timeframe** so a signal produces the same stop
width, entry confirmation, and monitor granularity regardless of venue. (This
was previously 1m for Binance, which made Binance stops ~5× tighter than Bybit
for identical signals — effectively two different strategies.)
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from app.logging_config import log
from app.services import redis_service


BINANCE_PERP_BASE = "https://fapi.binance.com/fapi/v1"
BINANCE_SPOT_BASE = "https://api.binance.com/api/v3"
_TIMEOUT_S = 5.0
# Match the Bybit WS stream (kline.5 → candles:{symbol}) so the strategy behaves
# identically across exchanges.
_INTERVAL = "5m"


def _binance_url(symbol: str, market_type: str | None, limit: int) -> str:
    base = BINANCE_PERP_BASE if (market_type or "perp").lower() == "perp" else BINANCE_SPOT_BASE
    return f"{base}/klines?symbol={symbol}&interval={_INTERVAL}&limit={max(1, limit)}"


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


def turnover_from_candles(candles: list[dict]) -> float:
    """Sum quote-volume (USD turnover) across candles. Prefers the bar's quote
    field (``q``); falls back to base-volume × close when it's absent (Binance
    klines, which we map without quote volume)."""
    total = 0.0
    for c in candles:
        q = c.get("q")
        if q is not None:
            total += float(q)
        else:
            v = float(c.get("v") or c.get("volume") or 0)
            close = float(c.get("c") or c.get("close") or 0)
            total += v * close
    return total


async def recent_turnover_usd(
    symbol: str,
    exchange: str,
    market_type: str | None,
    bars: int = 12,
) -> float:
    """Rolling USD turnover over the last ``bars`` candles — a liquidity proxy."""
    candles = await get_recent_candles(symbol, exchange, market_type, limit=bars)
    return turnover_from_candles(candles)


async def get_live_price(
    symbol: str,
    exchange: str,
    market_type: str | None,
) -> Decimal | None:
    """Best-effort live price for entry fills.

    Bybit candles in Redis are CLOSED 5m bars only, so "latest close" is up to
    5 minutes stale — on a symbol that just moved 3%+ in one bar. Use the
    orderbook.1 mid instead (bookticker:{symbol}, 60s TTL). Binance klines
    include the forming bar, so its latest close is already near-live.

    Returns None when no live source is available; callers fall back to the
    latest candle close.
    """
    ex = (exchange or "").lower()
    if ex == "bybit":
        top = await redis_service.get_bookticker(symbol)
        if top is not None:
            bid, ask = top
            if bid > 0 and ask > 0:
                return (Decimal(str(bid)) + Decimal(str(ask))) / 2
        return None
    if ex == "binance":
        candle = await get_latest_candle(symbol, exchange, market_type)
        if candle is not None:
            px = Decimal(str(candle.get("c") or candle.get("close") or 0))
            if px > 0:
                return px
    return None


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
