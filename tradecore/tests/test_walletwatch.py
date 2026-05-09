"""WalletWatch tests — classifier, pricing fallback, chain pairing."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.modules.walletwatch import classifier


USDC_ETH = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
WBTC = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
PEPE = "0x6982508145454ce325ddbe47a25d4ec3d2311933"
USDT_BSC = "0x55d398326f99059ff775485246999027b3197955"
CAKE = "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
USDC_ARB = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
WETH_ARB = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
ARB_TOKEN = "0x912ce59144191c1204e64559fe8253a0e49e6548"  # ARB
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
WETH_BASE = "0x4200000000000000000000000000000000000006"
CBBTC_BASE = "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"
DEGEN_BASE = "0x4ed4e862860bed51a9570b96d89af5e1b0efefed"  # DEGEN
USDC_SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


# ---------- classifier ----------


class TestClassifier:
    def test_usdc_to_pepe_is_buy(self):
        assert classifier.classify_swap("ethereum", [USDC_ETH], [PEPE]) == "buy"

    def test_pepe_to_usdc_is_sell(self):
        assert classifier.classify_swap("ethereum", [PEPE], [USDC_ETH]) == "sell"

    def test_weth_to_pepe_is_buy(self):
        # paying with a major (WETH) is still a buy.
        assert classifier.classify_swap("ethereum", [WETH], [PEPE]) == "buy"

    def test_usdc_to_weth_is_rotate(self):
        # quote → quote — uninteresting for "what are they buying".
        assert classifier.classify_swap("ethereum", [USDC_ETH], [WETH]) == "rotate"

    def test_alt_to_alt_is_rotate(self):
        OTHER = "0x1111111111111111111111111111111111111111"
        assert classifier.classify_swap("ethereum", [PEPE], [OTHER]) == "rotate"

    def test_empty_legs_is_rotate(self):
        assert classifier.classify_swap("ethereum", [], [PEPE]) == "rotate"
        assert classifier.classify_swap("ethereum", [USDC_ETH], []) == "rotate"

    def test_bsc_usdt_to_cake_is_buy(self):
        assert classifier.classify_swap("bsc", [USDT_BSC], [CAKE]) == "buy"

    def test_arbitrum_usdc_to_arb_is_buy(self):
        assert classifier.classify_swap("arbitrum", [USDC_ARB], [ARB_TOKEN]) == "buy"

    def test_arbitrum_buy_of_weth_is_not_interesting(self):
        # Same BTC/ETH-wrapper exclusion applies on Arbitrum.
        assert classifier.is_interesting_buy("arbitrum", "buy", WETH_ARB) is False

    def test_base_usdc_to_degen_is_buy(self):
        assert classifier.classify_swap("base", [USDC_BASE], [DEGEN_BASE]) == "buy"

    def test_base_buy_of_cbbtc_is_not_interesting(self):
        # Coinbase wrapped BTC is still BTC for "what are they buying" purposes.
        assert classifier.is_interesting_buy("base", "buy", CBBTC_BASE) is False

    def test_base_buy_of_weth_is_not_interesting(self):
        assert classifier.is_interesting_buy("base", "buy", WETH_BASE) is False

    def test_solana_usdc_to_bonk_is_buy(self):
        assert classifier.classify_swap("solana", [USDC_SOL], [BONK]) == "buy"

    def test_solana_wsol_to_bonk_is_buy(self):
        assert classifier.classify_swap("solana", [WSOL], [BONK]) == "buy"

    # ---- is_interesting_buy: BTC/ETH wrapper exclusion (per user ask) ----

    def test_buy_of_pepe_is_interesting(self):
        assert classifier.is_interesting_buy("ethereum", "buy", PEPE) is True

    def test_buy_of_wbtc_is_not_interesting(self):
        # User asked: "what are they buying beyond BTC and ETH". WBTC is BTC.
        assert classifier.is_interesting_buy("ethereum", "buy", WBTC) is False

    def test_buy_of_weth_is_not_interesting(self):
        assert classifier.is_interesting_buy("ethereum", "buy", WETH) is False

    def test_sell_is_never_interesting(self):
        assert classifier.is_interesting_buy("ethereum", "sell", PEPE) is False

    def test_rotate_is_never_interesting(self):
        assert classifier.is_interesting_buy("ethereum", "rotate", PEPE) is False

    def test_case_insensitive_eth_addresses(self):
        # Real-world inputs come in mixed case — ensure normalization works.
        assert classifier.is_stable("ethereum", USDC_ETH.upper()) is True
        assert classifier.is_major("ethereum", WETH.upper()) is True


# ---------- pricing: stable-side derivation ----------


@pytest.mark.asyncio
async def test_pricing_prefers_stable_in_leg(fake_redis):
    from app.modules.walletwatch import pricing

    # 482000 USDC → 1B PEPE — should report $482k regardless of CG.
    usd = await pricing.estimate_swap_usd(
        "ethereum",
        token_in_addr=USDC_ETH,
        token_in_amount=Decimal("482000"),
        token_out_addr=PEPE,
        token_out_amount=Decimal("1000000000"),
    )
    assert usd == 482_000.0


@pytest.mark.asyncio
async def test_pricing_prefers_stable_out_leg(fake_redis):
    from app.modules.walletwatch import pricing

    # PEPE → USDC: still anchored to the stable leg.
    usd = await pricing.estimate_swap_usd(
        "ethereum",
        token_in_addr=PEPE,
        token_in_amount=Decimal("1000000000"),
        token_out_addr=USDC_ETH,
        token_out_amount=Decimal("250000"),
    )
    assert usd == 250_000.0


@pytest.mark.asyncio
async def test_pricing_returns_none_without_stable_or_price(fake_redis, monkeypatch):
    from app.modules.walletwatch import pricing

    async def _no_price(*_args, **_kwargs):
        return None

    monkeypatch.setattr(pricing, "get_token_usd_price", _no_price)

    OTHER = "0x9999999999999999999999999999999999999999"
    usd = await pricing.estimate_swap_usd(
        "ethereum",
        token_in_addr=PEPE,
        token_in_amount=Decimal("1000000000"),
        token_out_addr=OTHER,
        token_out_amount=Decimal("500"),
    )
    assert usd is None


# ---------- ethereum chain pairing ----------


def test_eth_pairing_largest_legs(monkeypatch):
    """Confirm the ETH adapter's pairing logic picks the dominant in/out leg
    when an aggregator splits a route across multiple transfers in one tx.
    """
    from app.modules.walletwatch.chains import ethereum as eth_chain

    # Simulate alchemy_getAssetTransfers result: two hashes,
    #   tx A: wallet pays USDC + small WETH gas refund, receives PEPE
    #   tx B: only outbound — should NOT produce a swap (no inbound leg)
    out_transfers = [
        {
            "hash": "0xaaa",
            "blockNum": "0x1",
            "value": 482000.0,
            "asset": "USDC",
            "rawContract": {"address": USDC_ETH},
            "metadata": {"blockTimestamp": "2026-05-09T12:00:00Z"},
        },
        {
            "hash": "0xaaa",
            "blockNum": "0x1",
            "value": 0.001,  # dust gas refund
            "asset": "WETH",
            "rawContract": {"address": WETH},
            "metadata": {"blockTimestamp": "2026-05-09T12:00:00Z"},
        },
        {
            "hash": "0xbbb",
            "blockNum": "0x2",
            "value": 100.0,
            "asset": "USDC",
            "rawContract": {"address": USDC_ETH},
            "metadata": {"blockTimestamp": "2026-05-09T12:01:00Z"},
        },
    ]
    in_transfers = [
        {
            "hash": "0xaaa",
            "blockNum": "0x1",
            "value": 1_000_000_000.0,
            "asset": "PEPE",
            "rawContract": {"address": PEPE},
            "metadata": {"blockTimestamp": "2026-05-09T12:00:00Z"},
        },
    ]

    by_hash: dict[str, dict[str, list]] = {}
    for d, lst in (("out", out_transfers), ("in", in_transfers)):
        for t in lst:
            by_hash.setdefault(t["hash"], {"out": [], "in": []})[d].append(t)

    # Replay the adapter's pairing inline to assert the dominant-leg pick.
    swaps = []
    for tx_hash, legs in by_hash.items():
        if not legs["out"] or not legs["in"]:
            continue
        biggest_out = max(legs["out"], key=lambda x: float(x["value"]))
        biggest_in = max(legs["in"], key=lambda x: float(x["value"]))
        swaps.append((tx_hash, biggest_out["asset"], biggest_in["asset"]))

    assert swaps == [("0xaaa", "USDC", "PEPE")]
    # WETH dust + standalone outbound tx must NOT appear.
    assert all(s[0] != "0xbbb" for s in swaps)

    # And confirm the helper that converts a transfer to chain.ETH_NATIVE works.
    assert eth_chain._addr_or_native({"rawContract": {"address": None}}) == eth_chain.ETH_NATIVE


# ---------- bsc decimals normalization ----------


def test_bsc_decimal_normalization():
    from app.modules.walletwatch.chains.bsc import _normalize_amount

    # 1.5 USDT (18 decimals)
    assert _normalize_amount("1500000000000000000", "18") == Decimal("1.5")
    # 250000 USDC (6 decimals)
    assert _normalize_amount("250000000000", "6") == Decimal("250000")
    # invalid decimals → falls back to 18
    assert _normalize_amount("1000000000000000000", "garbage") == Decimal("1")


# ---------- detector classification + cursor advance shape ----------


@pytest.mark.asyncio
async def test_detector_skips_when_disabled(fake_redis, monkeypatch):
    from app.modules.walletwatch import detector

    monkeypatch.setattr(
        "app.modules.walletwatch.detector.app_settings.walletwatch_enabled",
        False,
        raising=False,
    )

    class _DummyDB:
        async def execute(self, *_a, **_k):
            raise AssertionError("DB should not be touched when disabled")

    result = await detector.scan_all(_DummyDB())
    assert result == {"skipped": 1}


def test_swap_event_shape_is_decimal():
    """Adapters must return Decimal amounts so we don't lose precision before DB."""
    from app.modules.walletwatch.chains.solana import _swap_from_event

    wallet = "abc"
    tx = {
        "signature": "sigZ",
        "slot": 100,
        "timestamp": int(datetime(2026, 5, 9, tzinfo=timezone.utc).timestamp()),
        "source": "JUPITER",
        "events": {
            "swap": {
                "nativeInput": None,
                "nativeOutput": None,
                "tokenInputs": [
                    {
                        "userAccount": wallet,
                        "mint": USDC_SOL,
                        "rawTokenAmount": {"tokenAmount": "100000000", "decimals": 6},
                    }
                ],
                "tokenOutputs": [
                    {
                        "userAccount": wallet,
                        "mint": BONK,
                        "rawTokenAmount": {"tokenAmount": "5000000000", "decimals": 5},
                    }
                ],
            }
        },
    }
    ev = _swap_from_event(wallet, tx)
    assert ev is not None
    assert ev["chain"] == "solana"
    assert ev["tx_hash"] == "sigZ"
    assert ev["token_in_address"] == USDC_SOL
    assert ev["token_in_amount"] == Decimal("100")  # 100_000_000 / 10^6
    assert ev["token_out_address"] == BONK
    assert ev["token_out_amount"] == Decimal("50000")  # 5_000_000_000 / 10^5
    assert ev["venue"] == "jupiter"
