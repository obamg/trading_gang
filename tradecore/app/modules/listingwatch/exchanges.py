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
                    # Bybit V5 perp tags risk tier via ``symbolType`` — values
                    # include "normal", "innovation", "stock", "commodity".
                    # (Spot uses a separate ``innovation`` "0"/"1" field —
                    # handled in fetch_bybit_spot_innovation.) Cross-reference
                    # with the spot set still happens in the listingwatch
                    # detector for any perps whose API row omits this field.
                    innovation=it.get("symbolType") == "innovation",
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


# Binance high-volatility risk tiers — values as they actually appear in
# the BAPI ``tags`` array (verified live against the production endpoint):
#   "innovation-zone" — legacy Innovation Zone (e.g. SUSHI, GMX, PEPE, PENDLE)
#   "Seed"            — 2024 successor for newer listings, partially overlaps
#                       innovation-zone but covers ~6× more tokens today
# Both denote the same surveillance tier WaveWatch is scoped to. Add
# "Monitoring" only if the trader desk wants to widen the net — it carries
# different semantics (assets under review for potential delisting).
BINANCE_INNOVATION_TAGS = {"innovation-zone", "Seed"}


async def fetch_binance_innovation(client: httpx.AsyncClient) -> list[ListedSymbol]:
    """Binance spot products tagged Innovation Zone or Seed.

    Binance does not expose risk-tier badges through the documented REST
    API. The BAPI products endpoint (consumed by binance.com itself to
    render the tag badges in the UI) returns a ``tags`` array per product
    including ``"Innovation"`` and ``"Seed"`` — both denote the
    high-volatility tier WaveWatch surveils. The endpoint is undocumented
    but stable in practice; treat fetch errors as soft and continue.
    """
    url = "https://www.binance.com/bapi/asset/v1/public/asset-service/product/get-products"
    r = await client.get(url, timeout=15)
    r.raise_for_status()
    data = (r.json() or {}).get("data") or []
    out: list[ListedSymbol] = []
    for it in data:
        if it.get("q") != "USDT":
            continue
        if it.get("st") != "TRADING":
            continue
        tags = list(it.get("tags") or [])
        tag_single = it.get("tag")
        if tag_single and tag_single not in tags:
            tags.append(tag_single)
        if not any(t in BINANCE_INNOVATION_TAGS for t in tags):
            continue
        out.append(
            ListedSymbol(
                exchange="binance",
                market_type="spot",
                symbol=it.get("s", ""),
                base_asset=it.get("b", ""),
                quote_asset="USDT",
                innovation=True,
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
