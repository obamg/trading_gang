"""USD pricing for swap legs.

Strategy (in order):
  1. If one leg of the swap is a stable, derive the swap's USD size from that
     leg directly (most accurate — that's literally what changed hands).
  2. Otherwise look up the non-stable leg's USD price via CoinGecko's
     "by contract address" endpoint, cached in Redis for 1h.

Why prefer the stable side: avoids price drift, decoder bugs, and CG rate
limits. A USDC→PEPE swap of 482000 USDC *is* a $482k swap regardless of where
PEPE is marked.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx

from app.config import settings as app_settings
from app.logging_config import log
from app.modules.walletwatch import classifier
from app.services import redis_service

CG_PLATFORM = {
    "ethereum": "ethereum",
    "bsc": "binance-smart-chain",
    "solana": "solana",
}
CG_BASE = "https://api.coingecko.com/api/v3"
PRICE_TTL_SECONDS = 3600


def _cache_key(chain: str, addr: str) -> str:
    return f"walletwatch:price:{chain}:{addr.lower() if chain != 'solana' else addr}"


async def get_token_usd_price(chain: str, addr: str) -> float | None:
    """Cached CoinGecko by-contract lookup. Returns None if unknown."""
    if not addr:
        return None
    r = redis_service.get_redis()
    key = _cache_key(chain, addr)
    cached = await r.get(key)
    if cached:
        try:
            return float(cached)
        except (TypeError, ValueError):
            pass

    platform = CG_PLATFORM.get(chain)
    if not platform:
        return None
    params = {
        "contract_addresses": addr,
        "vs_currencies": "usd",
    }
    headers = {}
    cg_key = getattr(app_settings, "coingecko_api_key", "") or ""
    if cg_key:
        headers["x-cg-demo-api-key"] = cg_key
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{CG_BASE}/simple/token_price/{platform}",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.debug("walletwatch_cg_lookup_failed", chain=chain, addr=addr, err=str(e))
        return None

    # CG returns lower-case key for EVM, original case for Solana.
    bucket = data.get(addr.lower()) or data.get(addr) or {}
    price = bucket.get("usd")
    if price is None:
        # Negative-cache so we don't hammer CG for unknown contracts.
        await r.set(key, "0", ex=PRICE_TTL_SECONDS)
        return None
    await r.set(key, str(price), ex=PRICE_TTL_SECONDS)
    return float(price)


async def estimate_swap_usd(
    chain: str,
    token_in_addr: str,
    token_in_amount: Decimal,
    token_out_addr: str,
    token_out_amount: Decimal,
) -> float | None:
    """Best USD estimate for a swap. Prefers the stable leg.

    Stables are assumed to peg at $1 — close enough for alert thresholds.
    Falls back to CoinGecko on the non-stable leg.
    """
    if classifier.is_stable(chain, token_in_addr):
        return float(token_in_amount)
    if classifier.is_stable(chain, token_out_addr):
        return float(token_out_amount)
    # Neither leg is a stable — try the leg most likely priced (majors first).
    for addr, amount in (
        (token_in_addr, token_in_amount),
        (token_out_addr, token_out_amount),
    ):
        if not addr:
            continue
        price = await get_token_usd_price(chain, addr)
        if price and price > 0:
            return float(amount) * price
    return None


__all__ = ["estimate_swap_usd", "get_token_usd_price"]


_ = json  # placeholder for future structured cache values
