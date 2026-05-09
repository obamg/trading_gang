"""BSC swap detection via the Etherscan V2 unified API (chainid=56).

Adding Arbitrum/Base/Polygon later is a one-liner: copy this file, change
the chainid + chain_name. The Etherscan API key is shared across all of them.
"""
from __future__ import annotations

from app.modules.walletwatch.chains._etherscan import (
    _normalize_amount,  # re-exported for tests
    fetch_token_swaps,
)

BSC_CHAINID = 56


async def fetch_swaps(wallet: str, from_block: int):
    return await fetch_token_swaps("bsc", BSC_CHAINID, wallet, from_block)


__all__ = ["fetch_swaps", "_normalize_amount"]
