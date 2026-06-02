"""Discovery candidate-token sources.

Two sources for "tokens worth scoring":

  1. CoinGecko top movers — ``/coins/markets`` filtered by 7d % change.
     Captures broadly-known winners. EVM-only for now.

  2. Observed buy activity — tokens we've already seen ≥3 unique smart-money
     wallets buy in the last 7d (from ``wallet_swaps``). This dogfoods Layer 1
     output as Layer 2 input.

Both feed into ``discovery_tokens`` with a ``source`` label so we can later
report on which source surfaces the winners.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.logging_config import log
from app.models.walletwatch import WalletSwap
from app.models.walletwatch_discovery import DiscoveryToken
from app.modules.walletwatch import classifier
from app.modules.walletwatch.pricing import CG_BASE, CG_PLATFORM

# CoinGecko platform IDs we want to score. SOL excluded for the EVM MVP.
CG_PLATFORMS = ["ethereum", "binance-smart-chain", "arbitrum-one", "base"]
PLATFORM_TO_CHAIN = {v: k for k, v in CG_PLATFORM.items()}
PLATFORM_TO_CHAIN.update({"binance-smart-chain": "bsc", "arbitrum-one": "arbitrum"})

# CG distinguishes asset-platform IDs (used for token addresses) from category
# slugs (used to filter /coins/markets). Most chains use an "-ecosystem"
# category, except BSC which kept the bare chain slug. Without this mapping
# the markets endpoint 404s for ethereum/arbitrum-one/base.
PLATFORM_TO_CATEGORY = {
    "ethereum": "ethereum-ecosystem",
    "binance-smart-chain": "binance-smart-chain",
    "arbitrum-one": "arbitrum-ecosystem",
    "base": "base-ecosystem",
}

CG_MARKETS_URL = f"{CG_BASE}/coins/markets"
TOP_GAINERS_LIMIT = 25
OBSERVED_LOOKBACK_DAYS = 7
OBSERVED_MIN_BUYERS = 3


async def _cg_top_gainers_for_platform(
    client: httpx.AsyncClient,
    platform: str,
    id_to_platforms: dict[str, dict],
) -> list[dict]:
    """Top tokens on one CG asset platform by 7d % change.

    Returns minimal dicts with chain/address/symbol/name/price. /coins/markets
    doesn't include per-chain contract addresses on its own — those come from
    the platforms map (built once per refresh from /coins/list).
    """
    category = PLATFORM_TO_CATEGORY.get(platform, platform)
    params = {
        "vs_currency": "usd",
        "category": category,
        "order": "price_change_percentage_7d_desc",
        "per_page": TOP_GAINERS_LIMIT,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "7d",
    }
    headers = {}
    cg_key = getattr(app_settings, "coingecko_api_key", "") or ""
    if cg_key:
        headers["x-cg-demo-api-key"] = cg_key
    try:
        resp = await client.get(CG_MARKETS_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.debug("discovery_cg_markets_failed", platform=platform, err=str(e))
        return []

    out: list[dict] = []
    for row in data:
        cg_id = row.get("id")
        platforms = id_to_platforms.get(cg_id or "") or {}
        addr = platforms.get(platform)
        if not addr:
            continue
        chain = PLATFORM_TO_CHAIN.get(platform)
        if not chain:
            continue
        # Skip stables/majors so we don't waste scoring budget on USDC/WETH/etc.
        if classifier.is_stable(chain, addr) or classifier.is_major(chain, addr):
            continue
        out.append(
            {
                "chain": chain,
                "address": addr.lower(),
                "symbol": (row.get("symbol") or "").upper(),
                "name": row.get("name"),
                "price_usd": row.get("current_price"),
            }
        )
    return out


async def _fetch_coin_platforms(client: httpx.AsyncClient) -> dict[str, dict]:
    """Build a {cg_id: {platform: address}} map from /coins/list?include_platform=true.

    One call per refresh (~10k coin records). Returned dict is keyed by the
    coin's CG id (e.g. "shiba-inu") with on-chain addresses per platform.
    Returns {} on failure so the caller degrades gracefully (cg_top_gainers
    becomes 0, observed_swaps still works).
    """
    headers = {}
    cg_key = getattr(app_settings, "coingecko_api_key", "") or ""
    if cg_key:
        headers["x-cg-demo-api-key"] = cg_key
    try:
        resp = await client.get(
            f"{CG_BASE}/coins/list",
            params={"include_platform": "true"},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("discovery_cg_list_failed", err=str(e))
        return {}
    return {
        row.get("id") or "": (row.get("platforms") or {})
        for row in data
        if row.get("id")
    }


async def _from_coingecko() -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        id_to_platforms = await _fetch_coin_platforms(client)
        rows: list[dict] = []
        for platform in CG_PLATFORMS:
            rows.extend(
                await _cg_top_gainers_for_platform(client, platform, id_to_platforms)
            )
    return rows


async def _from_observed_swaps(db: AsyncSession) -> list[dict]:
    """Tokens that ≥N distinct wallets bought in our own data lately."""
    since = datetime.now(timezone.utc) - timedelta(days=OBSERVED_LOOKBACK_DAYS)
    stmt = (
        select(
            WalletSwap.chain,
            WalletSwap.token_out_address,
            WalletSwap.token_out_symbol,
            func.count(distinct(WalletSwap.wallet_address)).label("buyers"),
        )
        .where(
            WalletSwap.swap_type == "buy",
            WalletSwap.detected_at >= since,
            WalletSwap.token_out_address.notin_(classifier.all_major_addresses()),
        )
        .group_by(
            WalletSwap.chain,
            WalletSwap.token_out_address,
            WalletSwap.token_out_symbol,
        )
        .having(func.count(distinct(WalletSwap.wallet_address)) >= OBSERVED_MIN_BUYERS)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "chain": r.chain,
            "address": r.token_out_address,
            "symbol": r.token_out_symbol,
            "name": None,
            "price_usd": None,
        }
        for r in rows
    ]


async def refresh_candidates(db: AsyncSession) -> dict[str, int]:
    """Pull from both sources and upsert into discovery_tokens.

    Existing rows keep their last_scored_at — we only update symbol/name/source
    when missing. New rows get discovered_at = now and source from this batch.
    """
    cg = await _from_coingecko()
    observed = await _from_observed_swaps(db)
    counts = {"cg_top_gainers": 0, "observed_swaps": 0}
    now = datetime.now(timezone.utc)

    seen: set[tuple[str, str]] = set()
    for source, rows in (("cg_top_gainers", cg), ("observed_swaps", observed)):
        for r in rows:
            key = (r["chain"], r["address"])
            if key in seen:
                continue
            seen.add(key)
            stmt = (
                pg_insert(DiscoveryToken)
                .values(
                    chain=r["chain"],
                    address=r["address"],
                    symbol=r.get("symbol"),
                    name=r.get("name"),
                    source=source,
                    price_at_discovery_usd=Decimal(str(r["price_usd"]))
                    if r.get("price_usd") is not None
                    else None,
                    discovered_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_discovery_token",
                    set_={
                        "symbol": r.get("symbol") or DiscoveryToken.symbol,
                        "name": r.get("name") or DiscoveryToken.name,
                    },
                )
            )
            await db.execute(stmt)
            counts[source] += 1
    await db.commit()
    return counts


__all__ = ["refresh_candidates"]
