"""Ethereum swap detection via Alchemy ``alchemy_getAssetTransfers``.

Two calls per wallet per poll (one outbound, one inbound), merged by tx hash.
Transfers in the same hash where the wallet is *both* sender and receiver of
different assets = swap (or aggregator-routed swap — works the same way).

We only keep the largest in-leg + largest out-leg per tx; multi-token routes
(rare for our cohort) are collapsed into the dominant pair. Aggregator gas
refunds and dust legs drop out naturally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings as app_settings
from app.logging_config import log

ETH_NATIVE = "eth_native"
ALCHEMY_URL = "https://eth-mainnet.g.alchemy.com/v2/{key}"
PAGE_SIZE_HEX = "0x64"  # 100


def _addr_or_native(t: dict[str, Any]) -> str:
    raw = (t.get("rawContract") or {}).get("address")
    if raw:
        return raw.lower()
    return ETH_NATIVE


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


async def _fetch_transfers(
    client: httpx.AsyncClient,
    api_key: str,
    wallet: str,
    from_block_hex: str,
    direction: str,  # "out" (fromAddress=wallet) or "in" (toAddress=wallet)
) -> list[dict[str, Any]]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [
            {
                "fromBlock": from_block_hex,
                "toBlock": "latest",
                ("fromAddress" if direction == "out" else "toAddress"): wallet,
                "category": ["external", "internal", "erc20"],
                "withMetadata": True,
                "excludeZeroValue": True,
                "maxCount": PAGE_SIZE_HEX,
                "order": "asc",
            }
        ],
    }
    resp = await client.post(ALCHEMY_URL.format(key=api_key), json=payload, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    return (data.get("result") or {}).get("transfers") or []


async def fetch_swaps(wallet: str, from_block: int) -> tuple[list[dict[str, Any]], int]:
    """Return (swap_events, latest_block_seen).

    swap_events shape:
      {
        "wallet_address", "chain"="ethereum", "tx_hash", "block_number",
        "token_in_address", "token_in_symbol", "token_in_amount" (Decimal),
        "token_out_address", "token_out_symbol", "token_out_amount" (Decimal),
        "venue"=None,  # Alchemy doesn't tell us the DEX; left for later enrichment
        "detected_at" (datetime, UTC),
      }
    """
    api_key = getattr(app_settings, "alchemy_api_key", "") or ""
    if not api_key:
        return [], from_block
    wallet = wallet.lower()
    from_block_hex = hex(max(from_block, 0))

    async with httpx.AsyncClient() as client:
        try:
            outs = await _fetch_transfers(client, api_key, wallet, from_block_hex, "out")
            ins = await _fetch_transfers(client, api_key, wallet, from_block_hex, "in")
        except httpx.HTTPError as e:
            log.warning("walletwatch_eth_fetch_failed", wallet=wallet, err=str(e))
            return [], from_block

    by_hash: dict[str, dict[str, list[dict[str, Any]]]] = {}
    latest_block = from_block
    for direction, transfers in (("out", outs), ("in", ins)):
        for t in transfers:
            h = t.get("hash")
            if not h:
                continue
            slot = by_hash.setdefault(h, {"out": [], "in": []})
            slot[direction].append(t)
            try:
                blk = int(t.get("blockNum", "0x0"), 16)
                latest_block = max(latest_block, blk)
            except (TypeError, ValueError):
                pass

    swaps: list[dict[str, Any]] = []
    for tx_hash, legs in by_hash.items():
        if not legs["out"] or not legs["in"]:
            continue  # plain transfer in or out — not a swap
        # Pick the largest leg on each side (by raw value, agnostic of price).
        biggest_out = max(legs["out"], key=lambda x: float(x.get("value") or 0))
        biggest_in = max(legs["in"], key=lambda x: float(x.get("value") or 0))
        in_amount = _to_decimal(biggest_out.get("value"))   # paid (left wallet)
        out_amount = _to_decimal(biggest_in.get("value"))   # received (entered)
        if in_amount <= 0 or out_amount <= 0:
            continue
        try:
            block_number = int(biggest_in.get("blockNum") or biggest_out.get("blockNum") or "0x0", 16)
        except (TypeError, ValueError):
            block_number = None
        ts_raw = (biggest_in.get("metadata") or {}).get("blockTimestamp") or (
            biggest_out.get("metadata") or {}
        ).get("blockTimestamp")
        try:
            detected_at = (
                datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts_raw
                else datetime.now(timezone.utc)
            )
        except ValueError:
            detected_at = datetime.now(timezone.utc)
        swaps.append(
            {
                "wallet_address": wallet,
                "chain": "ethereum",
                "tx_hash": tx_hash,
                "block_number": block_number,
                "token_in_address": _addr_or_native(biggest_out),
                "token_in_symbol": biggest_out.get("asset"),
                "token_in_amount": in_amount,
                "token_out_address": _addr_or_native(biggest_in),
                "token_out_symbol": biggest_in.get("asset"),
                "token_out_amount": out_amount,
                "venue": None,
                "detected_at": detected_at,
            }
        )

    return swaps, latest_block
