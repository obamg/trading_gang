"""Classify a paired-transfer swap into buy / sell / rotate, and decide whether
it's an "interesting" buy (i.e. an alt accumulation, not a stable rotate or a
BTC/ETH-wrapper swap).

A swap is the result of pairing one or more inbound and outbound token transfers
within a single transaction by the watched wallet:

  - in_assets  = tokens leaving the wallet  (what they paid with)
  - out_assets = tokens entering the wallet (what they received)

Notation everywhere is "from the wallet's perspective":
  - buy   : paid with stable/native, received an alt
  - sell  : paid with an alt, received stable/native
  - rotate: alt -> alt, or stable -> stable (uninteresting for "what are they buying")
"""
from __future__ import annotations

# Lower-case contract addresses. Update lists as needed.
STABLES: dict[str, set[str]] = {
    "ethereum": {
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
        "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
        "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
        "0x853d955acef822db058eb8505911ed77f175b99e",  # FRAX
        "0x4c9edd5852cd905f086c759e8383e09bff1e68b3",  # USDe
        "0x57e114b691db790c35207b2e685d4a43181e6061",  # ENA wrappers etc — extend
    },
    "bsc": {
        "0x55d398326f99059ff775485246999027b3197955",  # USDT
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
        "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
        "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3",  # DAI
        "0x14016e85a25aeb13065688cafb43044c2ef86784",  # TUSD
    },
    "arbitrum": {
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC (native)
        "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",  # USDC.e (bridged)
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",  # DAI
    },
    "base": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (native)
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC (bridged)
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    },
    "solana": {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    },
}

# "Native or BTC/ETH wrapper" — explicitly NOT what the user wants in the alert
# feed since they asked for "beyond BTC and ETH". Still spendable as an in-asset
# (paying with WETH counts as a buy).
MAJORS: dict[str, set[str]] = {
    "ethereum": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
        "eth_native",  # synthetic for native ETH
    },
    "bsc": {
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",  # BTCB
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # ETH on BSC
        "bnb_native",
    },
    "arbitrum": {
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",  # WBTC
        "eth_native",
    },
    "base": {
        "0x4200000000000000000000000000000000000006",  # WETH
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC
        "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22",  # cbETH
        "eth_native",
    },
    "solana": {
        "So11111111111111111111111111111111111111112",  # WSOL
        "sol_native",
    },
}


def _norm(chain: str, addr: str) -> str:
    """EVM addresses are hex and case-insensitive; Solana mints are base58 and
    case-sensitive — keep them as-is."""
    if not addr:
        return ""
    return addr if chain == "solana" else addr.lower()


def is_stable(chain: str, addr: str) -> bool:
    table = STABLES.get(chain, set())
    target = _norm(chain, addr)
    return target in (table if chain == "solana" else {a.lower() for a in table})


def is_major(chain: str, addr: str) -> bool:
    table = MAJORS.get(chain, set())
    target = _norm(chain, addr)
    return target in (table if chain == "solana" else {a.lower() for a in table})


def is_quote_asset(chain: str, addr: str) -> bool:
    """Anything you'd reasonably pay with: stables + native/major wrappers."""
    return is_stable(chain, addr) or is_major(chain, addr)


def classify_swap(
    chain: str, in_assets: list[str], out_assets: list[str]
) -> str:
    """Return 'buy' | 'sell' | 'rotate'.

    in_assets  = tokens the wallet *paid with* (left the wallet)
    out_assets = tokens the wallet *received* (entered the wallet)
    """
    if not in_assets or not out_assets:
        return "rotate"

    paid_only_quote = all(is_quote_asset(chain, a) for a in in_assets)
    received_only_quote = all(is_quote_asset(chain, a) for a in out_assets)
    received_has_alt = any(not is_quote_asset(chain, a) for a in out_assets)
    paid_has_alt = any(not is_quote_asset(chain, a) for a in in_assets)

    if paid_only_quote and received_has_alt:
        return "buy"
    if received_only_quote and paid_has_alt:
        return "sell"
    return "rotate"


def is_interesting_buy(chain: str, swap_type: str, token_out_address: str) -> bool:
    """Per user ask: 'what are they buying beyond BTC and ETH'.

    A buy of WBTC/WETH/WBNB/etc. is still a buy, but it's not what we want to
    surface in the alert feed. Filter those out.
    """
    if swap_type != "buy":
        return False
    return not is_major(chain, token_out_address)
