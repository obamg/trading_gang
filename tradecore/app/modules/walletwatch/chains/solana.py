"""Solana swap detection via Helius enhanced-transactions API.

Helius parses Jupiter / Raydium / Orca swaps into a clean ``events.swap``
object so we don't have to decode program logs ourselves.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings as app_settings
from app.logging_config import log

SOL_NATIVE = "sol_native"
HELIUS_URL = "https://api.helius.xyz/v0/addresses/{addr}/transactions"
PAGE_SIZE = 100


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


def _swap_from_event(wallet: str, tx: dict[str, Any]) -> dict[str, Any] | None:
    swap = (tx.get("events") or {}).get("swap")
    if not swap:
        return None
    in_legs: list[tuple[str, str | None, Decimal]] = []  # (addr, symbol, amount)
    out_legs: list[tuple[str, str | None, Decimal]] = []

    # Native SOL leg
    nat_in = swap.get("nativeInput") or {}
    nat_out = swap.get("nativeOutput") or {}
    if nat_in.get("account") == wallet and nat_in.get("amount"):
        in_legs.append((SOL_NATIVE, "SOL", _to_decimal(nat_in["amount"]) / Decimal(1_000_000_000)))
    if nat_out.get("account") == wallet and nat_out.get("amount"):
        out_legs.append((SOL_NATIVE, "SOL", _to_decimal(nat_out["amount"]) / Decimal(1_000_000_000)))

    # SPL token legs
    for ti in swap.get("tokenInputs") or []:
        if ti.get("userAccount") != wallet:
            continue
        amount = _to_decimal(((ti.get("rawTokenAmount") or {}).get("tokenAmount") or 0))
        decimals = int((ti.get("rawTokenAmount") or {}).get("decimals") or 0)
        if decimals:
            amount = amount / (Decimal(10) ** decimals)
        in_legs.append((ti.get("mint") or "", None, amount))
    for to in swap.get("tokenOutputs") or []:
        if to.get("userAccount") != wallet:
            continue
        amount = _to_decimal(((to.get("rawTokenAmount") or {}).get("tokenAmount") or 0))
        decimals = int((to.get("rawTokenAmount") or {}).get("decimals") or 0)
        if decimals:
            amount = amount / (Decimal(10) ** decimals)
        out_legs.append((to.get("mint") or "", None, amount))

    if not in_legs or not out_legs:
        return None
    biggest_in = max(in_legs, key=lambda x: x[2])
    biggest_out = max(out_legs, key=lambda x: x[2])
    if biggest_in[2] <= 0 or biggest_out[2] <= 0:
        return None

    ts = tx.get("timestamp")
    try:
        detected_at = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        detected_at = datetime.now(timezone.utc)

    return {
        "wallet_address": wallet,
        "chain": "solana",
        "tx_hash": tx.get("signature") or "",
        "block_number": tx.get("slot"),
        "token_in_address": biggest_in[0],
        "token_in_symbol": biggest_in[1],
        "token_in_amount": biggest_in[2],
        "token_out_address": biggest_out[0],
        "token_out_symbol": biggest_out[1],
        "token_out_amount": biggest_out[2],
        "venue": (tx.get("source") or "").lower() or None,  # JUPITER, RAYDIUM, etc.
        "detected_at": detected_at,
    }


async def fetch_swaps(wallet: str, last_signature: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Return (swap_events, newest_signature_seen).

    Helius pages with `before=<signature>` going backwards. We collect until
    we hit the previous cursor or run out of new tx.
    """
    api_key = getattr(app_settings, "helius_api_key", "") or ""
    if not api_key:
        return [], last_signature

    swaps: list[dict[str, Any]] = []
    newest_sig: str | None = None
    before: str | None = None
    pages = 0
    max_pages = 3  # 300 most-recent tx per poll cap

    async with httpx.AsyncClient(timeout=15.0) as client:
        while pages < max_pages:
            params: dict[str, Any] = {"api-key": api_key, "limit": PAGE_SIZE}
            if before:
                params["before"] = before
            try:
                resp = await client.get(HELIUS_URL.format(addr=wallet), params=params)
                resp.raise_for_status()
                page = resp.json()
            except httpx.HTTPError as e:
                log.warning("walletwatch_sol_fetch_failed", wallet=wallet, err=str(e))
                break
            if not isinstance(page, list) or not page:
                break
            if newest_sig is None:
                newest_sig = page[0].get("signature")
            stop = False
            for tx in page:
                sig = tx.get("signature")
                if last_signature and sig == last_signature:
                    stop = True
                    break
                ev = _swap_from_event(wallet, tx)
                if ev and ev["tx_hash"]:
                    swaps.append(ev)
            if stop:
                break
            before = page[-1].get("signature")
            if not before:
                break
            pages += 1

    return swaps, newest_sig or last_signature
