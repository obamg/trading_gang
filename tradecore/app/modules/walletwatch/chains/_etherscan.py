"""Etherscan V2 unified multichain API.

One API key (`ETHERSCAN_API_KEY`) works across 50+ chains via the ``chainid``
parameter — BSC=56, Arbitrum=42161, Base=8453, Polygon=137, Optimism=10, etc.

We use this for chains where the cheapest path to "swaps for this wallet" is
"pair ERC-20 transfers in the same tx hash". Ethereum mainnet stays on
Alchemy because ``alchemy_getAssetTransfers`` also gives us native ETH and
internal calls in one shot, which Etherscan's ``tokentx`` doesn't.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings as app_settings
from app.logging_config import log

ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


def _normalize_amount(raw: str, decimals: str) -> Decimal:
    try:
        d = int(decimals)
    except (TypeError, ValueError):
        d = 18
    val = _to_decimal(raw)
    if d:
        val = val / (Decimal(10) ** d)
    return val


async def fetch_token_swaps(
    chain_name: str,
    chainid: int,
    wallet: str,
    from_block: int,
) -> tuple[list[dict[str, Any]], int]:
    """Pair ERC-20/BEP-20 transfers per tx hash into swap events.

    Returns (swap_events, latest_block_seen). When the API key is missing,
    no-ops cleanly so the detector tick stays green in dev.
    """
    api_key = getattr(app_settings, "etherscan_api_key", "") or ""
    if not api_key:
        return [], from_block
    wallet = wallet.lower()
    params = {
        "chainid": chainid,
        "module": "account",
        "action": "tokentx",
        "address": wallet,
        "startblock": max(from_block, 0),
        "endblock": 99999999,
        "sort": "asc",
        "page": 1,
        "offset": 200,
        "apikey": api_key,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(ETHERSCAN_V2_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            log.warning(
                "walletwatch_etherscan_fetch_failed",
                chain=chain_name,
                wallet=wallet,
                err=str(e),
            )
            return [], from_block

    if str(data.get("status")) != "1":
        if data.get("message") not in ("No transactions found", "OK"):
            log.debug(
                "walletwatch_etherscan_api_msg",
                chain=chain_name,
                wallet=wallet,
                msg=data.get("message"),
            )
        return [], from_block

    transfers = data.get("result") or []
    by_hash: dict[str, dict[str, list[dict[str, Any]]]] = {}
    latest_block = from_block
    for t in transfers:
        h = t.get("hash")
        if not h:
            continue
        from_addr = (t.get("from") or "").lower()
        to_addr = (t.get("to") or "").lower()
        direction = "out" if from_addr == wallet else ("in" if to_addr == wallet else None)
        if direction is None:
            continue
        slot = by_hash.setdefault(h, {"out": [], "in": []})
        slot[direction].append(t)
        try:
            latest_block = max(latest_block, int(t.get("blockNumber") or 0))
        except (TypeError, ValueError):
            pass

    swaps: list[dict[str, Any]] = []
    for tx_hash, legs in by_hash.items():
        if not legs["out"] or not legs["in"]:
            continue
        out_norm = [
            (t, _normalize_amount(t.get("value") or "0", t.get("tokenDecimal") or "18"))
            for t in legs["out"]
        ]
        in_norm = [
            (t, _normalize_amount(t.get("value") or "0", t.get("tokenDecimal") or "18"))
            for t in legs["in"]
        ]
        biggest_out = max(out_norm, key=lambda x: x[1])
        biggest_in = max(in_norm, key=lambda x: x[1])
        if biggest_out[1] <= 0 or biggest_in[1] <= 0:
            continue
        try:
            ts_raw = int(biggest_in[0].get("timeStamp") or biggest_out[0].get("timeStamp") or 0)
            detected_at = (
                datetime.fromtimestamp(ts_raw, tz=timezone.utc) if ts_raw else datetime.now(timezone.utc)
            )
        except (TypeError, ValueError):
            detected_at = datetime.now(timezone.utc)
        try:
            block_number = int(biggest_in[0].get("blockNumber") or 0)
        except (TypeError, ValueError):
            block_number = None
        swaps.append(
            {
                "wallet_address": wallet,
                "chain": chain_name,
                "tx_hash": tx_hash,
                "block_number": block_number,
                "token_in_address": (biggest_out[0].get("contractAddress") or "").lower(),
                "token_in_symbol": biggest_out[0].get("tokenSymbol"),
                "token_in_amount": biggest_out[1],
                "token_out_address": (biggest_in[0].get("contractAddress") or "").lower(),
                "token_out_symbol": biggest_in[0].get("tokenSymbol"),
                "token_out_amount": biggest_in[1],
                "venue": None,
                "detected_at": detected_at,
            }
        )

    return swaps, latest_block
