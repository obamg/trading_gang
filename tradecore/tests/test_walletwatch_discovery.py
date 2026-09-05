"""WalletWatch discovery tests — FIFO scorer + wallet aggregator + heuristics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.modules.walletwatch.discovery.scorer import (
    TokenPnlRow,
    TokenSwapEvent,
    compute_token_pnl,
    compute_wallet_score,
)


def _ts(minutes_offset: int) -> datetime:
    """Helper: anchor at a fixed UTC moment, offset by minutes."""
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes_offset)


# ---------- compute_token_pnl ----------


class TestTokenPnl:
    def test_simple_buy_then_hold(self):
        events = [
            TokenSwapEvent(
                wallet="0xabc",
                side="buy",
                amount_token=Decimal("1000"),
                amount_usd=Decimal("100"),
                ts=_ts(0),
            ),
        ]
        out = compute_token_pnl(events, current_price_usd=Decimal("0.20"))
        r = out["0xabc"]
        assert r["total_buy_usd"] == Decimal("100")
        assert r["current_balance"] == Decimal("1000")
        assert r["current_value_usd"] == Decimal("200")
        assert r["realized_pnl_usd"] == Decimal("0")
        assert r["unrealized_pnl_usd"] == Decimal("100")
        # Multiple = (sold + held) / cost = (0 + 200) / 100 = 2x.
        assert r["multiple"] == Decimal("2")

    def test_buy_then_full_sell_realizes_pnl(self):
        events = [
            TokenSwapEvent("0xabc", "buy", Decimal("1000"), Decimal("100"), _ts(0)),
            TokenSwapEvent("0xabc", "sell", Decimal("1000"), Decimal("250"), _ts(60)),
        ]
        out = compute_token_pnl(events, current_price_usd=Decimal("0.50"))
        r = out["0xabc"]
        assert r["total_buy_usd"] == Decimal("100")
        assert r["total_sell_usd"] == Decimal("250")
        assert r["current_balance"] == Decimal("0")
        assert r["realized_pnl_usd"] == Decimal("150")
        assert r["unrealized_pnl_usd"] == Decimal("0")
        # Multiple = (250 + 0) / 100 = 2.5x.
        assert r["multiple"] == Decimal("2.5")

    def test_partial_sell_keeps_remaining_position(self):
        events = [
            TokenSwapEvent("0xabc", "buy", Decimal("1000"), Decimal("100"), _ts(0)),
            TokenSwapEvent("0xabc", "sell", Decimal("400"), Decimal("80"), _ts(60)),
        ]
        out = compute_token_pnl(events, current_price_usd=Decimal("0.30"))
        r = out["0xabc"]
        assert r["current_balance"] == Decimal("600")
        # Sold 400 @ unit revenue 0.20, cost basis 0.10 → realized = 400 × 0.10 = 40
        assert r["realized_pnl_usd"] == Decimal("40")
        # Held 600 @ current 0.30 = 180; cost basis 600 × 0.10 = 60 → unrealized = 120
        assert r["current_value_usd"] == Decimal("180")
        assert r["unrealized_pnl_usd"] == Decimal("120")

    def test_fifo_uses_oldest_buys_first(self):
        events = [
            TokenSwapEvent("0xabc", "buy", Decimal("100"), Decimal("100"), _ts(0)),  # $1/tok
            TokenSwapEvent("0xabc", "buy", Decimal("100"), Decimal("300"), _ts(10)),  # $3/tok
            TokenSwapEvent("0xabc", "sell", Decimal("100"), Decimal("400"), _ts(60)),  # $4/tok out
        ]
        out = compute_token_pnl(events, current_price_usd=Decimal("4"))
        r = out["0xabc"]
        # Sells consume the oldest 100 tokens (at $1 cost) → realized = 100 × ($4 - $1) = 300
        assert r["realized_pnl_usd"] == Decimal("300")
        # Remaining 100 tokens at $3 cost; current price $4 → unrealized = 100 × $1 = 100
        assert r["current_balance"] == Decimal("100")
        assert r["unrealized_pnl_usd"] == Decimal("100")

    def test_sell_without_prior_buy_zero_cost(self):
        # Wallet had a position before our scan window. Sell shows up with no
        # matching buy — credit at zero cost basis (acceptable bias).
        events = [
            TokenSwapEvent("0xabc", "sell", Decimal("100"), Decimal("500"), _ts(0)),
        ]
        out = compute_token_pnl(events, current_price_usd=Decimal("5"))
        r = out["0xabc"]
        assert r["total_buy_usd"] == Decimal("0")
        assert r["realized_pnl_usd"] == Decimal("500")
        assert r["multiple"] is None  # cost basis is zero → undefined

    def test_multi_wallet_separation(self):
        events = [
            TokenSwapEvent("0xabc", "buy", Decimal("100"), Decimal("100"), _ts(0)),
            TokenSwapEvent("0xdef", "buy", Decimal("50"), Decimal("50"), _ts(5)),
        ]
        out = compute_token_pnl(events, current_price_usd=Decimal("2"))
        assert set(out.keys()) == {"0xabc", "0xdef"}
        assert out["0xabc"]["current_balance"] == Decimal("100")
        assert out["0xdef"]["current_balance"] == Decimal("50")

    def test_zero_amount_event_ignored(self):
        events = [
            TokenSwapEvent("0xabc", "buy", Decimal("0"), Decimal("100"), _ts(0)),
            TokenSwapEvent("0xabc", "buy", Decimal("100"), Decimal("100"), _ts(5)),
        ]
        out = compute_token_pnl(events, current_price_usd=Decimal("1"))
        assert out["0xabc"]["total_buy_amount"] == Decimal("100")


# ---------- compute_wallet_score ----------


class TestWalletScore:
    def test_empty_returns_none(self):
        assert compute_wallet_score([]) is None

    def test_single_winning_token(self):
        score = compute_wallet_score(
            [
                TokenPnlRow(
                    realized_pnl_usd=Decimal("100"),
                    unrealized_pnl_usd=Decimal("50"),
                    total_buy_usd=Decimal("100"),
                    multiple=Decimal("2.5"),
                ),
            ]
        )
        assert score["win_count"] == 1
        assert score["loss_count"] == 0
        assert score["win_rate"] == Decimal("1.0000")
        assert score["best_multiple"] == Decimal("2.5")
        assert score["token_count"] == 1
        # discovery_score = realized × win_rate × sqrt(tokens)
        #                 = 100 × 1.0 × sqrt(1) = 100. (Unrealized not counted.)
        assert score["discovery_score"] == Decimal("100")

    def test_mixed_wins_and_losses(self):
        score = compute_wallet_score(
            [
                TokenPnlRow(Decimal("200"), Decimal("0"), Decimal("100"), Decimal("3.0")),
                TokenPnlRow(Decimal("-30"), Decimal("0"), Decimal("100"), Decimal("0.7")),
                TokenPnlRow(Decimal("50"), Decimal("0"), Decimal("100"), Decimal("1.5")),
            ]
        )
        assert score["win_count"] == 2
        assert score["loss_count"] == 1
        assert score["win_rate"] == Decimal("0.6667")
        # avg_multiple = (3.0 + 0.7 + 1.5) / 3 ≈ 1.7333
        assert abs(score["avg_multiple"] - Decimal("1.7333")) < Decimal("0.0001")
        assert score["best_multiple"] == Decimal("3.0")

    def test_negative_pnl_zero_score(self):
        # A wallet with net loss is not interesting — discovery_score should be 0
        # regardless of token width.
        score = compute_wallet_score(
            [
                TokenPnlRow(Decimal("-100"), Decimal("0"), Decimal("100"), Decimal("0")),
                TokenPnlRow(Decimal("-50"), Decimal("0"), Decimal("100"), Decimal("0.5")),
            ]
        )
        assert score["discovery_score"] == Decimal("0")

    def test_width_factor_rewards_breadth(self):
        narrow = compute_wallet_score(
            [TokenPnlRow(Decimal("1000"), Decimal("0"), Decimal("100"), Decimal("11"))]
        )
        wide = compute_wallet_score(
            [
                TokenPnlRow(Decimal("200"), Decimal("0"), Decimal("100"), Decimal("3")),
                TokenPnlRow(Decimal("200"), Decimal("0"), Decimal("100"), Decimal("3")),
                TokenPnlRow(Decimal("200"), Decimal("0"), Decimal("100"), Decimal("3")),
                TokenPnlRow(Decimal("200"), Decimal("0"), Decimal("100"), Decimal("3")),
                TokenPnlRow(Decimal("200"), Decimal("0"), Decimal("100"), Decimal("3")),
            ]
        )
        # Same total PnL ($1000) but wide wallet gets the log breadth bonus.
        assert wide["discovery_score"] > narrow["discovery_score"]


# ---------- pool detection heuristic ----------


class TestPoolDetection:
    def test_pool_appears_on_both_sides(self):
        from app.modules.walletwatch.discovery.fetcher import _detect_pools

        # Pool sends and receives the token; wallets only on one side each.
        transfers = [
            {"from": "0xPOOL", "to": "0xWallet1"},
            {"from": "0xWallet1", "to": "0xPOOL"},
            {"from": "0xPOOL", "to": "0xWallet2"},
            {"from": "0xWallet2", "to": "0xPOOL"},
            {"from": "0xPOOL", "to": "0xWallet3"},
            # Wallet-to-wallet transfer (rare) — not a pool signal
            {"from": "0xWallet3", "to": "0xWallet4"},
        ]
        pools = _detect_pools(transfers)
        assert "0xpool" in pools
        # Wallets that only appear on one side aren't classified as pools.
        assert "0xwallet4" not in pools

    def test_no_bidirectional_addresses_returns_empty(self):
        from app.modules.walletwatch.discovery.fetcher import _detect_pools

        transfers = [
            {"from": "0xA", "to": "0xB"},
            {"from": "0xC", "to": "0xD"},
        ]
        assert _detect_pools(transfers) == set()


@pytest.mark.asyncio
async def test_price_at_returns_closest():
    from app.modules.walletwatch.discovery.pricing import price_at

    series = [(1000, 0.5), (2000, 0.7), (3000, 0.9)]
    assert price_at(series, 1100) == 0.5  # closest to 1000
    assert price_at(series, 2100) == 0.7  # closest to 2000
    assert price_at([], 1000) is None


# ---------- auto-promote helpers ----------


class _ScoreStub:
    """Duck-typed stand-in for WalletPnlScore — promote helpers only touch a
    few fields and we don't need a real ORM instance to exercise them."""

    def __init__(self, **kw):
        self.wallet_address = kw.get("wallet_address", "0x" + "a" * 40)
        self.chain = kw.get("chain", "ethereum")
        self.token_count = kw.get("token_count", 5)
        self.win_rate = kw.get("win_rate", Decimal("0.8"))
        self.promoted_at = kw.get("promoted_at")
        self.promoted_entity_id = kw.get("promoted_entity_id")


class TestPromoteHelpers:
    def test_short_addr_truncates_long_addresses(self):
        from app.modules.walletwatch.discovery.promote import _short_addr

        addr = "0x3650abcdef1234567890abcdef1234567890b5b8"
        assert _short_addr(addr) == "0x3650…b5b8"

    def test_short_addr_passes_short_strings_through(self):
        from app.modules.walletwatch.discovery.promote import _short_addr

        assert _short_addr("0xabc") == "0xabc"
        assert _short_addr("") == ""

    def test_default_entity_name_is_per_wallet_unique(self):
        from app.modules.walletwatch.discovery.promote import _default_entity_name

        s1 = _ScoreStub(wallet_address="0x3650abcdef1234567890abcdef1234567890b5b8")
        s2 = _ScoreStub(wallet_address="0x42e2abcdef1234567890abcdef1234567890e4748")
        n1 = _default_entity_name(s1)
        n2 = _default_entity_name(s2)
        assert n1 != n2
        assert n1.startswith("PnL Discovery ")

    def test_conviction_scales_with_token_count(self):
        from app.modules.walletwatch.discovery.promote import _conviction_for

        narrow = _ScoreStub(win_rate=Decimal("0.8"), token_count=1)
        wide = _ScoreStub(win_rate=Decimal("0.8"), token_count=10)
        # 0.8 × 1.1 = 0.88 vs 0.8 × 2.0 = 1.60 (clamped to 1.0)
        assert _conviction_for(narrow) == Decimal("0.880")
        assert _conviction_for(wide) == Decimal("1.0")

    def test_conviction_is_clamped_to_one(self):
        from app.modules.walletwatch.discovery.promote import _conviction_for

        s = _ScoreStub(win_rate=Decimal("1.0"), token_count=20)
        assert _conviction_for(s) == Decimal("1.0")


# ---------- 2026-09-04: the same rate-limit spiral, in discovery ----------
#
# discovery/pricing.py carried an identical defect to the swap pricer fixed in
# PR #20: the cache was only written `if series`, so BOTH a failed call and a
# successful-but-empty one left no entry and were re-asked on every re-score.
# Dormant at the time only because scoring had not needed prices recently.

import httpx as _httpx  # noqa: E402
import pytest as _pytest  # noqa: E402

from app.modules.walletwatch import pricing as _swap_pricing  # noqa: E402
from app.modules.walletwatch.discovery import pricing as _disc_pricing  # noqa: E402


class _R:
    def __init__(self, status):
        self.status_code = status


def _err(status):
    return _httpx.HTTPStatusError("boom", request=None, response=_R(status))


def _fake_client(behaviour):
    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return behaviour()
    return lambda **kw: _C()


@_pytest.mark.asyncio
async def test_discovery_failed_series_is_negative_cached(monkeypatch, fake_redis):
    monkeypatch.setattr(_disc_pricing.redis_service, "get_redis", lambda: fake_redis)
    calls = {"n": 0}

    def behaviour():
        calls["n"] += 1
        raise _err(500)

    monkeypatch.setattr(_disc_pricing.httpx, "AsyncClient", _fake_client(behaviour))

    assert await _disc_pricing.get_price_series("ethereum", "0xabc", 0, 3_600_000) == []
    assert calls["n"] == 1
    assert await _disc_pricing.get_price_series("ethereum", "0xabc", 0, 3_600_000) == []
    assert calls["n"] == 1, "failure not cached — discovery re-asks every re-score"


@_pytest.mark.asyncio
async def test_discovery_empty_series_is_cached(monkeypatch, fake_redis):
    """A successful-but-empty response is a real answer, not a miss."""
    monkeypatch.setattr(_disc_pricing.redis_service, "get_redis", lambda: fake_redis)
    calls = {"n": 0}

    def behaviour():
        calls["n"] += 1

        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"prices": []}
        return _Resp()

    monkeypatch.setattr(_disc_pricing.httpx, "AsyncClient", _fake_client(behaviour))

    assert await _disc_pricing.get_price_series("ethereum", "0xdef", 0, 3_600_000) == []
    assert await _disc_pricing.get_price_series("ethereum", "0xdef", 0, 3_600_000) == []
    assert calls["n"] == 1, "empty series re-fetched — the original bug"


@_pytest.mark.asyncio
async def test_discovery_shares_the_breaker_with_the_swap_pricer(monkeypatch, fake_redis):
    """One CoinGecko quota, one breaker: a 429 raised by discovery must stop
    the swap pricer too, and vice versa."""
    monkeypatch.setattr(_disc_pricing.redis_service, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(_swap_pricing.redis_service, "get_redis", lambda: fake_redis)
    calls = {"n": 0}

    def behaviour():
        calls["n"] += 1
        raise _err(429)

    monkeypatch.setattr(_disc_pricing.httpx, "AsyncClient", _fake_client(behaviour))

    await _disc_pricing.get_price_series("ethereum", "0x111", 0, 3_600_000)
    assert calls["n"] == 1
    assert await fake_redis.get(_swap_pricing.CG_BREAKER_KEY) is not None

    # The swap pricer must now short-circuit on the breaker discovery tripped.
    swap_calls = {"n": 0}

    def swap_behaviour():
        swap_calls["n"] += 1
        raise _err(429)

    monkeypatch.setattr(_swap_pricing.httpx, "AsyncClient", _fake_client(swap_behaviour))
    assert await _swap_pricing.get_token_usd_price("ethereum", "0x222") is None
    assert swap_calls["n"] == 0, "breaker is not shared across the two callers"


@_pytest.mark.asyncio
async def test_discovery_skips_network_while_breaker_open(monkeypatch, fake_redis):
    monkeypatch.setattr(_disc_pricing.redis_service, "get_redis", lambda: fake_redis)
    await fake_redis.set(_swap_pricing.CG_BREAKER_KEY, "1", ex=300)
    calls = {"n": 0}

    def behaviour():
        calls["n"] += 1
        raise AssertionError("must not call CoinGecko while the breaker is open")

    monkeypatch.setattr(_disc_pricing.httpx, "AsyncClient", _fake_client(behaviour))
    assert await _disc_pricing.get_price_series("ethereum", "0x333", 0, 3_600_000) == []
    assert calls["n"] == 0
