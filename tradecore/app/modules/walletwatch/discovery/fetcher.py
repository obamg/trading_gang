"""Per-token DEX swap history for PnL scoring.

EVM-only for the discovery MVP. We fetch all ERC-20 transfers of the target
token via Etherscan V2 ``tokentx`` and use a heuristic to identify which
addresses are AMM pools (any address that both sends and receives this token
in volume). Transfers from a pool to a wallet → buy; from a wallet to a
pool → sell. Wallets that look like contracts (>50 transfers in window) are
filtered out as noise.

For Solana support we'd plug in Birdeye's ``/defi/txs/token`` endpoint or
parse Helius enhanced transactions per mint — that comes in Phase B.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings as app_settings
from app.logging_config import log
from app.modules.walletwatch.chains._etherscan import (
    ETHERSCAN_V2_URL,
    _normalize_amount,
)
from app.modules.walletwatch.discovery.pricing import get_price_series, price_at
from app.modules.walletwatch.discovery.scorer import TokenSwapEvent

EVM_CHAIN_IDS = {
    "ethereum": 1,
    "bsc": 56,
    "arbitrum": 42161,
    "base": 8453,
}

PAGE_OFFSET = 10000
MAX_PAGES = 3                 # → up to 30k transfers per token per scan
POOL_TOP_N = 5                # top-N "both sides" addresses considered pools
WALLET_NOISE_FLOOR = 50       # >50 transfers in window → likely contract/bot
MIN_TRANSFERS = 10            # low-liquidity tokens skipped — pool detection unreliable


async def fetch_token_swap_events(
    chain: str,
    token_address: str,
    from_ts_ms: int,
    to_ts_ms: int,
) -> list[TokenSwapEvent]:
    """Return classified swap events for ``token_address`` in the time window."""
    chainid = EVM_CHAIN_IDS.get(chain)
    if chainid is None:
        return []
    api_key = getattr(app_settings, "etherscan_api_key", "") or ""
    if not api_key:
        return []

    transfers = await _fetch_recent_token_transfers(chainid, token_address, api_key)
    if len(transfers) < MIN_TRANSFERS:
        return []

    in_window = [
        t for t in transfers
        if from_ts_ms <= int(t.get("timeStamp") or 0) * 1000 <= to_ts_ms
    ]
    if len(in_window) < MIN_TRANSFERS:
        return []

    pools = _detect_pools(in_window)
    if not pools:
        return []

    # Per-address transfer count in the window — used to drop noisy contracts.
    addr_counts: dict[str, int] = defaultdict(int)
    for t in in_window:
        addr_counts[(t.get("from") or "").lower()] += 1
        addr_counts[(t.get("to") or "").lower()] += 1

    series = await get_price_series(chain, token_address, from_ts_ms, to_ts_ms)
    if not series:
        log.debug("discovery_no_price_series", chain=chain, token=token_address)
        return []

    events: list[TokenSwapEvent] = []
    for t in in_window:
        from_addr = (t.get("from") or "").lower()
        to_addr = (t.get("to") or "").lower()
        from_is_pool = from_addr in pools
        to_is_pool = to_addr in pools
        if from_is_pool == to_is_pool:
            continue  # pool→pool or wallet→wallet — not a swap
        if from_is_pool:
            wallet, side = to_addr, "buy"
        else:
            wallet, side = from_addr, "sell"
        if addr_counts[wallet] > WALLET_NOISE_FLOOR:
            continue
        try:
            ts_s = int(t.get("timeStamp") or 0)
        except (TypeError, ValueError):
            continue
        if ts_s <= 0:
            continue
        amount = _normalize_amount(t.get("value") or "0", t.get("tokenDecimal") or "18")
        if amount <= 0:
            continue
        price = price_at(series, ts_s * 1000)
        if not price or price <= 0:
            continue
        amount_usd = amount * Decimal(str(price))
        events.append(
            TokenSwapEvent(
                wallet=wallet,
                side=side,
                amount_token=amount,
                amount_usd=amount_usd,
                ts=datetime.fromtimestamp(ts_s, tz=timezone.utc),
            )
        )
    return events


async def _fetch_recent_token_transfers(
    chainid: int, token: str, api_key: str
) -> list[dict[str, Any]]:
    """Most-recent N transfers of ``token`` (descending), capped at MAX_PAGES.

    We sort desc and stop when no more results — for a token under heavy
    activity that means we get the freshest 30k. Older transfers fall out
    naturally.
    """
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "chainid": chainid,
                "module": "account",
                "action": "tokentx",
                "contractaddress": token,
                "page": page,
                "offset": PAGE_OFFSET,
                "sort": "desc",
                "apikey": api_key,
            }
            try:
                resp = await client.get(ETHERSCAN_V2_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                log.warning(
                    "discovery_tokentx_failed",
                    token=token,
                    page=page,
                    err=str(e),
                )
                break
            if str(data.get("status")) != "1":
                break
            chunk = data.get("result") or []
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < PAGE_OFFSET:
                break
    return out


def _detect_pools(transfers: list[dict[str, Any]]) -> set[str]:
    """Heuristic: AMM pools both send and receive the token in volume.

    Take the top-N addresses ranked by (in_count + out_count) that appear on
    both sides. For most ERC-20 tokens this surfaces 1–3 Uniswap V3 pools and
    1–2 router contracts — exactly what we want.
    """
    in_count: dict[str, int] = defaultdict(int)
    out_count: dict[str, int] = defaultdict(int)
    for t in transfers:
        out_count[(t.get("from") or "").lower()] += 1
        in_count[(t.get("to") or "").lower()] += 1
    candidates = [
        (a, in_count[a] + out_count[a]) for a in in_count.keys() & out_count.keys()
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return {a for a, _ in candidates[:POOL_TOP_N]}


__all__ = ["fetch_token_swap_events"]
