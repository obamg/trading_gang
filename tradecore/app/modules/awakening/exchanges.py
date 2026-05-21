"""REST ticker fetchers for the awakening detector.

Each fetcher returns a list of (symbol, turnover_usd, price_change_pct) for
currently-trading USDT-quoted perps. Pure I/O — no DB, no Redis.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings
from app.logging_config import log


@dataclass(frozen=True)
class Ticker:
    exchange: str         # "binance" | "bybit"
    symbol: str           # native, e.g. "ZECUSDT"
    turnover_24h: float   # quote-asset volume, USD-equivalent
    price_change_pct: float  # last 24h %, signed


async def fetch_binance_tickers(client: httpx.AsyncClient) -> list[Ticker]:
    """All Binance USDT-M perp tickers via /fapi/v1/ticker/24hr."""
    url = f"{settings.binance_rest_url}/fapi/v1/ticker/24hr"
    r = await client.get(url, timeout=15)
    r.raise_for_status()
    items = r.json() or []
    out: list[Ticker] = []
    for it in items:
        sym = it.get("symbol") or ""
        if not sym.endswith("USDT") or not sym.isascii() or not sym.isalnum():
            continue
        try:
            turnover = float(it.get("quoteVolume", 0))
            pct = float(it.get("priceChangePercent", 0))
        except (TypeError, ValueError):
            continue
        out.append(Ticker(exchange="binance", symbol=sym, turnover_24h=turnover, price_change_pct=pct))
    return out


async def fetch_bybit_tickers(client: httpx.AsyncClient) -> list[Ticker]:
    """All Bybit linear USDT perp tickers via /v5/market/tickers."""
    url = f"{settings.bybit_rest_url}/v5/market/tickers"
    r = await client.get(url, params={"category": "linear"}, timeout=15)
    r.raise_for_status()
    payload = (r.json() or {}).get("result") or {}
    items = payload.get("list") or []
    out: list[Ticker] = []
    for it in items:
        sym = it.get("symbol") or ""
        if not sym.endswith("USDT") or not sym.isascii() or not sym.isalnum():
            continue
        try:
            turnover = float(it.get("turnover24h", 0))
            pct = float(it.get("price24hPcnt", 0)) * 100.0  # Bybit returns 0.05, not 5
        except (TypeError, ValueError):
            continue
        out.append(Ticker(exchange="bybit", symbol=sym, turnover_24h=turnover, price_change_pct=pct))
    return out


async def fetch_all_tickers() -> list[Ticker]:
    """Fetch both exchanges in parallel; per-exchange failures are non-fatal."""
    import asyncio

    async with httpx.AsyncClient() as client:
        async def _safe(name, fn):
            try:
                return await fn(client)
            except Exception as e:
                log.warning("awakening_fetch_failed", source=name, err=str(e))
                return []

        binance_t, bybit_t = await asyncio.gather(
            _safe("binance", fetch_binance_tickers),
            _safe("bybit", fetch_bybit_tickers),
        )

    return [*binance_t, *bybit_t]
