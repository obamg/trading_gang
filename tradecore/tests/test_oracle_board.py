"""Oracle Confluence Board tests — confluence filter, sort order, cache."""
from __future__ import annotations

import json

import pytest

from app.modules.oracle import board as board_module
from app.modules.oracle.board import _agreeing_modules


def _bd(direction: str, intensity: float) -> dict:
    """Synthesize a single module breakdown entry."""
    return {
        "direction": direction,
        "intensity": intensity,
        "weight": 10,
        "contribution": 0,
        "detail": None,
    }


# ---------- _agreeing_modules ----------


class TestAgreeingModules:
    def test_filters_to_target_direction(self):
        breakdown = {
            "radarx": _bd("bullish", 0.9),
            "whale": _bd("bearish", 0.9),
            "macro": _bd("bullish", 0.5),
        }
        out = _agreeing_modules(breakdown, "bullish")
        assert {m["name"] for m in out} == {"radarx", "macro"}

    def test_intensity_floor_excludes_weak_signals(self):
        # Anything ≤ 0.3 is too weak to count as "agreeing".
        breakdown = {
            "a": _bd("bullish", 0.31),   # in
            "b": _bd("bullish", 0.30),   # out (boundary — not strict greater)
            "c": _bd("bullish", 0.10),   # out
        }
        out = _agreeing_modules(breakdown, "bullish")
        assert {m["name"] for m in out} == {"a"}

    def test_neutral_modules_excluded(self):
        breakdown = {
            "a": _bd("neutral", 0.9),
            "b": _bd("bullish", 0.9),
        }
        out = _agreeing_modules(breakdown, "bullish")
        assert [m["name"] for m in out] == ["b"]


# ---------- compute_board orchestration ----------


def _live(symbol: str, score: int, breakdown: dict) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "recommendation": "BUY" if score > 0 else "SELL" if score < 0 else "HOLD",
        "confidence": "high",
        "confluence_count": sum(
            1 for v in breakdown.values()
            if float(v.get("intensity", 0)) > 0.3
        ),
        "current_price": 100.0,
        "signals_breakdown": breakdown,
    }


@pytest.fixture
def patched_board(monkeypatch, fake_redis):
    """Stub the I/O dependencies of compute_board so we can drive it from data."""
    # No HTTP — return a fixed universe.
    universe = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT"]

    async def _stub_universe():
        return universe

    # Per-symbol live results, controlled per test.
    live_table: dict[str, dict] = {}

    async def _stub_live(_db, sym, weights=None):  # noqa: ARG001
        return live_table[sym]

    # AsyncSessionLocal context manager — tests don't touch the DB.
    class _NullSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    monkeypatch.setattr(board_module, "_top_symbols_by_volume", _stub_universe)
    monkeypatch.setattr(board_module, "compute_live_score", _stub_live)
    monkeypatch.setattr(board_module, "AsyncSessionLocal", lambda: _NullSession())

    return live_table


@pytest.mark.asyncio
async def test_three_agreeing_modules_qualify(patched_board):
    patched_board["AAAUSDT"] = _live("AAAUSDT", 70, {
        "radarx": _bd("bullish", 0.9),
        "whaleradar": _bd("bullish", 0.8),
        "flowpulse": _bd("bullish", 0.7),
        "macropulse": _bd("neutral", 0.0),
    })
    patched_board["BBBUSDT"] = _live("BBBUSDT", 30, {
        "radarx": _bd("bullish", 0.9),
        "whaleradar": _bd("bullish", 0.8),
        # only 2 agreeing → drops out
        "flowpulse": _bd("neutral", 0.0),
    })
    # Fill the rest with noise so they don't qualify
    for sym in ["CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT"]:
        patched_board[sym] = _live(sym, 0, {"x": _bd("neutral", 0.0)})

    result = await board_module.compute_board()
    assert [r["symbol"] for r in result["bullish"]] == ["AAAUSDT"]
    assert result["bearish"] == []


@pytest.mark.asyncio
async def test_dominant_direction_wins_when_split(patched_board):
    # Wallet has 3 bullish + 2 bearish — bullish wins because it has the count.
    patched_board["AAAUSDT"] = _live("AAAUSDT", 25, {
        "radarx": _bd("bullish", 0.9),
        "whaleradar": _bd("bullish", 0.8),
        "flowpulse": _bd("bullish", 0.7),
        "macropulse": _bd("bearish", 0.6),
        "sentimentpulse": _bd("bearish", 0.5),
    })
    for sym in ["BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT"]:
        patched_board[sym] = _live(sym, 0, {"x": _bd("neutral", 0.0)})

    result = await board_module.compute_board()
    assert [r["symbol"] for r in result["bullish"]] == ["AAAUSDT"]
    assert all(m["direction"] == "bullish" for m in result["bullish"][0]["modules"])


@pytest.mark.asyncio
async def test_bullish_sorted_desc_bearish_sorted_asc(patched_board):
    # Three qualifying bullish symbols at different scores
    bd_bull = {
        "a": _bd("bullish", 0.9),
        "b": _bd("bullish", 0.9),
        "c": _bd("bullish", 0.9),
    }
    bd_bear = {
        "a": _bd("bearish", 0.9),
        "b": _bd("bearish", 0.9),
        "c": _bd("bearish", 0.9),
    }
    patched_board["AAAUSDT"] = _live("AAAUSDT", 90, bd_bull)
    patched_board["BBBUSDT"] = _live("BBBUSDT", 50, bd_bull)
    patched_board["CCCUSDT"] = _live("CCCUSDT", 70, bd_bull)
    patched_board["DDDUSDT"] = _live("DDDUSDT", -90, bd_bear)
    patched_board["EEEUSDT"] = _live("EEEUSDT", -50, bd_bear)
    patched_board["FFFUSDT"] = _live("FFFUSDT", -70, bd_bear)

    result = await board_module.compute_board()
    assert [r["symbol"] for r in result["bullish"]] == ["AAAUSDT", "CCCUSDT", "BBBUSDT"]
    assert [r["symbol"] for r in result["bearish"]] == ["DDDUSDT", "FFFUSDT", "EEEUSDT"]


@pytest.mark.asyncio
async def test_split_50_50_excluded(patched_board):
    # 3 bullish, 3 bearish — neither side dominates → drops out entirely.
    patched_board["AAAUSDT"] = _live("AAAUSDT", 0, {
        "a": _bd("bullish", 0.9),
        "b": _bd("bullish", 0.9),
        "c": _bd("bullish", 0.9),
        "d": _bd("bearish", 0.9),
        "e": _bd("bearish", 0.9),
        "f": _bd("bearish", 0.9),
    })
    for sym in ["BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT"]:
        patched_board[sym] = _live(sym, 0, {"x": _bd("neutral", 0.0)})

    result = await board_module.compute_board()
    assert result["bullish"] == []
    assert result["bearish"] == []


@pytest.mark.asyncio
async def test_snapshot_cached_in_redis(patched_board, fake_redis):
    patched_board["AAAUSDT"] = _live("AAAUSDT", 80, {
        "radarx": _bd("bullish", 0.9),
        "whaleradar": _bd("bullish", 0.9),
        "flowpulse": _bd("bullish", 0.9),
    })
    for sym in ["BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT"]:
        patched_board[sym] = _live(sym, 0, {"x": _bd("neutral", 0.0)})

    await board_module.compute_board()
    cached_raw = await fake_redis.get(board_module.BOARD_CACHE_KEY)
    assert cached_raw is not None
    cached = json.loads(cached_raw)
    assert cached["bullish"][0]["symbol"] == "AAAUSDT"
    assert cached["min_modules"] == board_module.MIN_MODULES


@pytest.mark.asyncio
async def test_get_cached_board_returns_none_on_miss(fake_redis):
    # No prior compute_board call → key absent → None.
    assert await board_module.get_cached_board() is None
