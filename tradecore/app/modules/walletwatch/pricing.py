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
    "arbitrum": "arbitrum-one",
    "base": "base",
    "solana": "solana",
}
CG_BASE = "https://api.coingecko.com/api/v3"
PRICE_TTL_SECONDS = 3600
# A FAILED lookup is cached too, for a shorter window. Without this the module
# rate-limit spirals: a 429 returned early without writing any cache entry, so
# the next tick re-requested the identical contract, earned another 429, and
# never cached — 13,955 failed calls in 24h against a 30/min demo quota, with
# only 28 price keys in Redis to show for it. Short TTL so a transient outage
# costs minutes of staleness, not an hour.
FAILURE_TTL_SECONDS = 600
# Sentinel distinguishing "asked, got no price" from "ask failed". Both stop
# the retry, but only the latter should expire quickly.
_FAILURE_MARKER = "err"

# Circuit breaker. While this key is set every caller returns immediately
# without touching the network, so one rate-limit does not turn into a
# thundering herd across 32 wallets x thousands of swaps.
CG_BREAKER_KEY = "walletwatch:cg:breaker"
BREAKER_SECONDS = 300


def _cache_key(chain: str, addr: str) -> str:
    return f"walletwatch:price:{chain}:{addr.lower() if chain != 'solana' else addr}"


async def get_token_usd_price(chain: str, addr: str) -> float | None:
    """Cached CoinGecko by-contract lookup. Returns None if unknown."""
    if not addr:
        return None
    r = redis_service.get_redis()
    key = _cache_key(chain, addr)
    cached = await r.get(key)
    if cached is not None:
        raw = cached.decode() if isinstance(cached, bytes) else cached
        if raw == _FAILURE_MARKER:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass

    platform = CG_PLATFORM.get(chain)
    if not platform:
        return None

    # Breaker open: skip the call entirely rather than adding to the pile-up.
    if await r.get(CG_BREAKER_KEY):
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
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 429:
            # Trip the breaker for everyone, not just this contract — the quota
            # is per-key, so another contract would fail identically.
            await r.set(CG_BREAKER_KEY, "1", ex=BREAKER_SECONDS)
            log.warning(
                "walletwatch_cg_rate_limited",
                chain=chain, addr=addr, breaker_seconds=BREAKER_SECONDS,
            )
        else:
            log.debug("walletwatch_cg_lookup_failed", chain=chain, addr=addr, err=str(e))
        # Cache the failure so the next tick does not re-ask immediately.
        await r.set(key, _FAILURE_MARKER, ex=FAILURE_TTL_SECONDS)
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
