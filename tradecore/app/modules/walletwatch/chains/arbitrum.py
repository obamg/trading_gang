"""Arbitrum One swap detection via Etherscan V2 (chainid=42161)."""
from __future__ import annotations

from app.modules.walletwatch.chains._etherscan import fetch_token_swaps

ARBITRUM_CHAINID = 42161


async def fetch_swaps(wallet: str, from_block: int):
    return await fetch_token_swaps("arbitrum", ARBITRUM_CHAINID, wallet, from_block)


__all__ = ["fetch_swaps"]
