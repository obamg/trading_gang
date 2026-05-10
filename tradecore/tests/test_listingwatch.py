"""ListingWatch — exchange adapters, diff logic, cross-listing detection.

Pure-function tests for everything that can be unit-tested without I/O.
"""
from __future__ import annotations

from app.modules.listingwatch.detector import _index_by_base, _key
from app.modules.listingwatch.exchanges import ListedSymbol


# ---------- index_by_base ----------


def test_index_by_base_groups_same_token():
    items = [
        ListedSymbol("bybit", "perp", "PEPEUSDT", "PEPE", "USDT"),
        ListedSymbol("binance", "spot", "PEPEUSDT", "PEPE", "USDT"),
        ListedSymbol("okx", "perp", "PEPE-USDT-SWAP", "PEPE", "USDT"),
        ListedSymbol("bybit", "perp", "BTCUSDT", "BTC", "USDT"),
    ]
    idx = _index_by_base(items)
    assert len(idx["PEPE"]) == 3
    assert len(idx["BTC"]) == 1


def test_index_by_base_case_insensitive():
    items = [
        ListedSymbol("bybit", "perp", "wifusdt", "wif", "usdt"),
        ListedSymbol("binance", "spot", "WIFUSDT", "WIF", "USDT"),
    ]
    idx = _index_by_base(items)
    assert len(idx["WIF"]) == 2


# ---------- composite key ----------


def test_key_format():
    s = ListedSymbol("bybit", "perp", "PEPEUSDT", "PEPE", "USDT")
    assert _key(s) == "bybit:perp:PEPEUSDT"


def test_key_distinguishes_market_types():
    spot = ListedSymbol("binance", "spot", "PEPEUSDT", "PEPE", "USDT")
    perp = ListedSymbol("binance", "perp", "PEPEUSDT", "PEPE", "USDT")
    assert _key(spot) != _key(perp)


# ---------- detector e2e (with fake redis) ----------


import pytest


@pytest.mark.asyncio
async def test_detector_bootstrap_seeds_silently(fake_redis, monkeypatch):
    """First run must not fire alerts — just silently learn the universe."""
    from app.modules.listingwatch import detector

    # walletwatch_enabled flag pattern: enable the flag for this test
    from app.config import settings
    monkeypatch.setattr(settings, "listingwatch_enabled", True, raising=False)

    snapshot = [
        ListedSymbol("bybit", "perp", "BTCUSDT", "BTC", "USDT"),
        ListedSymbol("bybit", "perp", "ETHUSDT", "ETH", "USDT"),
        ListedSymbol("binance", "spot", "BTCUSDT", "BTC", "USDT"),
    ]

    async def _fake_fetch_all():
        return snapshot

    monkeypatch.setattr(detector, "fetch_all", _fake_fetch_all)

    result = await detector.run_listingwatch_detect()

    assert result.get("bootstrapped") == 3
    # No alerts published on bootstrap.
    assert not [c for c, _m in fake_redis.published() if c == "alerts:listingwatch"]
    # Known set populated.
    known = await fake_redis.smembers("listingwatch:known")
    assert "bybit:perp:BTCUSDT" in known
    assert "binance:spot:BTCUSDT" in known
    # Bootstrap sentinel set.
    assert await fake_redis.exists("listingwatch:bootstrapped") == 1


@pytest.mark.asyncio
async def test_detector_diff_finds_new_listing(fake_redis, monkeypatch):
    """After bootstrap, a new symbol must be detected and trigger force-subscribe."""
    from app.modules.listingwatch import detector
    from app.config import settings
    monkeypatch.setattr(settings, "listingwatch_enabled", True, raising=False)

    # Pre-seed bootstrap state.
    await fake_redis.set("listingwatch:bootstrapped", "1")
    await fake_redis.sadd("listingwatch:known", "bybit:perp:BTCUSDT", "bybit:perp:ETHUSDT")

    # Patch fetch_all to return a snapshot with one truly new symbol.
    snapshot = [
        ListedSymbol("bybit", "perp", "BTCUSDT", "BTC", "USDT"),
        ListedSymbol("bybit", "perp", "ETHUSDT", "ETH", "USDT"),
        ListedSymbol("bybit", "perp", "FRESHUSDT", "FRESH", "USDT"),
    ]

    async def _fake_fetch_all():
        return snapshot

    monkeypatch.setattr(detector, "fetch_all", _fake_fetch_all)

    # Patch DB session to a no-op insert that always returns a fake row id.
    from uuid import uuid4

    fake_id = uuid4()

    class _FakeResult:
        def scalar(self):
            return fake_id

    class _FakeSession:
        async def execute(self, _stmt):
            return _FakeResult()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(detector, "AsyncSessionLocal", lambda: _FakeSession())

    result = await detector.run_listingwatch_detect()
    assert result["new"] == 1
    assert result["inserted"] == 1

    # Force-subscribe set should now contain FRESHUSDT (Bybit perp).
    forced = await fake_redis.smembers("bybit:force_subscribe")
    assert "FRESHUSDT" in forced

    # Alert was published.
    msgs = [m for c, m in fake_redis.published() if c == "alerts:listingwatch"]
    assert len(msgs) == 1
    assert "FRESHUSDT" in msgs[0]
    assert "listing_detected" in msgs[0]


@pytest.mark.asyncio
async def test_detector_marks_cross_listing(fake_redis, monkeypatch):
    """When a token already trades elsewhere, is_cross_listing must be true."""
    from app.modules.listingwatch import detector
    from app.config import settings
    monkeypatch.setattr(settings, "listingwatch_enabled", True, raising=False)

    await fake_redis.set("listingwatch:bootstrapped", "1")
    # PEPE already known on Bybit perp + Binance spot.
    await fake_redis.sadd(
        "listingwatch:known", "bybit:perp:PEPEUSDT", "binance:spot:PEPEUSDT"
    )

    # New: PEPE just appeared on Binance perp too.
    snapshot = [
        ListedSymbol("bybit", "perp", "PEPEUSDT", "PEPE", "USDT"),
        ListedSymbol("binance", "spot", "PEPEUSDT", "PEPE", "USDT"),
        ListedSymbol("binance", "perp", "PEPEUSDT", "PEPE", "USDT"),
    ]

    async def _fake_fetch_all():
        return snapshot

    monkeypatch.setattr(detector, "fetch_all", _fake_fetch_all)

    captured: dict = {}

    class _FakeResult:
        def scalar(self):
            from uuid import uuid4
            return uuid4()

    class _FakeSession:
        async def execute(self, stmt):
            # Capture the values being inserted to verify the flag is set.
            params = getattr(stmt, "compile", lambda: None)()
            if params is not None:
                captured["params"] = params.params
            return _FakeResult()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(detector, "AsyncSessionLocal", lambda: _FakeSession())

    await detector.run_listingwatch_detect()

    msgs = [m for c, m in fake_redis.published() if c == "alerts:listingwatch"]
    assert len(msgs) == 1
    # Cross-listing flag is in the published payload.
    assert '"is_cross_listing": true' in msgs[0]


@pytest.mark.asyncio
async def test_detector_disabled_short_circuits(fake_redis, monkeypatch):
    """When the feature flag is off, the detector returns immediately."""
    from app.modules.listingwatch import detector
    from app.config import settings
    monkeypatch.setattr(settings, "listingwatch_enabled", False, raising=False)

    called = {"n": 0}

    async def _fake_fetch_all():
        called["n"] += 1
        return []

    monkeypatch.setattr(detector, "fetch_all", _fake_fetch_all)

    result = await detector.run_listingwatch_detect()
    assert result == {"skipped": 1}
    assert called["n"] == 0
