"""Exchange adapters for listing detection.

Each adapter exposes ``async def list_symbols() -> list[ListedSymbol]``,
returning the *current* set of tradable USDT-quoted symbols (spot + perp
where relevant). The detector diffs these against a Redis "known" set —
new entries are listings.

Pure I/O — no DB, no Redis. Failure-tolerant: a single exchange outage
must not break the others.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings
from app.logging_config import log


@dataclass(frozen=True)
class ListedSymbol:
    exchange: str       # bybit | binance | okx
    market_type: str    # spot | perp
    symbol: str         # exchange-native (e.g. "PEPEUSDT", "PEPE-USDT-SWAP")
    base_asset: str     # "PEPE"
    quote_asset: str    # "USDT"
    listing_ts_ms: int | None = None  # exchange-reported listing time, when available
    innovation: bool = False  # Bybit Innovation Zone risk tier — spot OR perp


# ---------- Bybit ----------


async def fetch_bybit_perps(client: httpx.AsyncClient) -> list[ListedSymbol]:
    """Bybit linear USDT perps via /v5/market/instruments-info."""
    url = f"{settings.bybit_rest_url}/v5/market/instruments-info"
    r = await client.get(url, params={"category": "linear", "limit": 1000}, timeout=15)
    r.raise_for_status()
    payload = (r.json() or {}).get("result") or {}
    items = payload.get("list") or []
    out: list[ListedSymbol] = []
    for it in items:
        if (
            it.get("status") == "Trading"
            and it.get("contractType") == "LinearPerpetual"
            and it.get("quoteCoin") == "USDT"
        ):
            out.append(
                ListedSymbol(
                    exchange="bybit",
                    market_type="perp",
                    symbol=it.get("symbol", ""),
                    base_asset=it.get("baseCoin", ""),
                    quote_asset="USDT",
                    listing_ts_ms=int(it.get("launchTime") or 0) or None,
                    # Bybit V5 returns ``innovation`` as a "0"/"1" string on
                    # the linear perp endpoint too. Cross-reference with the
                    # spot innovation set still happens in the detector for
                    # perps whose API response omits the field.
                    innovation=str(it.get("innovation", "0")) == "1",
                )
            )
    return out


async def fetch_bybit_spot_innovation(client: httpx.AsyncClient) -> list[ListedSymbol]:
    """Bybit spot listings tagged Innovation Zone via /v5/market/instruments-info.

    The Bybit spot instruments response carries an ``innovation`` field
    ("0" / "1"). We keep only USDT-quoted, currently trading items where
    that flag is set."""
    url = f"{settings.bybit_rest_url}/v5/market/instruments-info"
    r = await client.get(url, params={"category": "spot", "limit": 1000}, timeout=15)
    r.raise_for_status()
    payload = (r.json() or {}).get("result") or {}
    items = payload.get("list") or []
    out: list[ListedSymbol] = []
    for it in items:
        if (
            it.get("status") == "Trading"
            and it.get("quoteCoin") == "USDT"
            and str(it.get("innovation", "0")) == "1"
        ):
            out.append(
                ListedSymbol(
                    exchange="bybit",
                    market_type="spot",
                    symbol=it.get("symbol", ""),
                    base_asset=it.get("baseCoin", ""),
                    quote_asset="USDT",
                    innovation=True,
                )
            )
    return out


# ---------- Binance ----------


async def fetch_binance_spot(client: httpx.AsyncClient) -> list[ListedSymbol]:
    """Binance spot via /api/v3/exchangeInfo. CDN-fronted — works from the
    Paris VPS even though the WS edge is geo-blocked."""
    url = "https://api.binance.com/api/v3/exchangeInfo"
    r = await client.get(url, timeout=15)
    r.raise_for_status()
    items = (r.json() or {}).get("symbols") or []
    out: list[ListedSymbol] = []
    for it in items:
        if (
            it.get("status") == "TRADING"
            and it.get("quoteAsset") == "USDT"
            and it.get("isSpotTradingAllowed")
        ):
            out.append(
                ListedSymbol(
                    exchange="binance",
                    market_type="spot",
                    symbol=it.get("symbol", ""),
                    base_asset=it.get("baseAsset", ""),
                    quote_asset="USDT",
                )
            )
    return out


async def fetch_binance_perps(client: httpx.AsyncClient) -> list[ListedSymbol]:
    """Binance USDT-M perps via /fapi/v1/exchangeInfo."""
    url = f"{settings.binance_rest_url}/fapi/v1/exchangeInfo"
    r = await client.get(url, timeout=15)
    r.raise_for_status()
    items = (r.json() or {}).get("symbols") or []
    out: list[ListedSymbol] = []
    for it in items:
        if (
            it.get("status") == "TRADING"
            and it.get("contractType") == "PERPETUAL"
            and it.get("quoteAsset") == "USDT"
        ):
            out.append(
                ListedSymbol(
                    exchange="binance",
                    market_type="perp",
                    symbol=it.get("symbol", ""),
                    base_asset=it.get("baseAsset", ""),
                    quote_asset="USDT",
                    listing_ts_ms=int(it.get("onboardDate") or 0) or None,
                )
            )
    return out


# ---------- OKX ----------


async def fetch_okx_spot(client: httpx.AsyncClient) -> list[ListedSymbol]:
    """OKX spot via /api/v5/public/instruments?instType=SPOT."""
    url = "https://www.okx.com/api/v5/public/instruments"
    r = await client.get(url, params={"instType": "SPOT"}, timeout=15)
    r.raise_for_status()
    items = (r.json() or {}).get("data") or []
    out: list[ListedSymbol] = []
    for it in items:
        if it.get("state") == "live" and it.get("quoteCcy") == "USDT":
            out.append(
                ListedSymbol(
                    exchange="okx",
                    market_type="spot",
                    symbol=it.get("instId", ""),
                    base_asset=it.get("baseCcy", ""),
                    quote_asset="USDT",
                    listing_ts_ms=int(it.get("listTime") or 0) or None,
                )
            )
    return out


async def fetch_okx_perps(client: httpx.AsyncClient) -> list[ListedSymbol]:
    """OKX USDT-margined perps via /api/v5/public/instruments?instType=SWAP."""
    url = "https://www.okx.com/api/v5/public/instruments"
    r = await client.get(url, params={"instType": "SWAP"}, timeout=15)
    r.raise_for_status()
    items = (r.json() or {}).get("data") or []
    out: list[ListedSymbol] = []
    for it in items:
        if (
            it.get("state") == "live"
            and it.get("settleCcy") == "USDT"
            and it.get("ctType") == "linear"
        ):
            out.append(
                ListedSymbol(
                    exchange="okx",
                    market_type="perp",
                    symbol=it.get("instId", ""),
                    base_asset=it.get("ctValCcy") or it.get("baseCcy", ""),
                    quote_asset="USDT",
                    listing_ts_ms=int(it.get("listTime") or 0) or None,
                )
            )
    return out


# ---------- aggregator ----------


FETCHERS = [
    ("bybit_perp", fetch_bybit_perps),
    ("bybit_spot_innovation", fetch_bybit_spot_innovation),
    ("binance_spot", fetch_binance_spot),
    ("binance_perp", fetch_binance_perps),
    ("okx_spot", fetch_okx_spot),
    ("okx_perp", fetch_okx_perps),
]


async def fetch_all() -> list[ListedSymbol]:
    """Fetch all exchanges in parallel. Per-exchange failures are logged but
    don't fail the whole pass — a Bybit hiccup shouldn't blind us to Binance
    listings."""
    import asyncio

    async with httpx.AsyncClient() as client:
        async def _safe(name, fn):
            try:
                return await fn(client)
            except Exception as e:
                log.warning("listingwatch_fetch_failed", source=name, err=str(e))
                return []

        results = await asyncio.gather(*[_safe(n, f) for n, f in FETCHERS])

    flat: list[ListedSymbol] = []
    for chunk in results:
        flat.extend(chunk)
    return flat
