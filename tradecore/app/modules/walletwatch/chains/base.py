"""Base swap detection via Etherscan V2 (chainid=8453)."""
from __future__ import annotations

from app.modules.walletwatch.chains._etherscan import fetch_token_swaps

BASE_CHAINID = 8453


async def fetch_swaps(wallet: str, from_block: int):
    return await fetch_token_swaps("base", BASE_CHAINID, wallet, from_block)


__all__ = ["fetch_swaps"]
