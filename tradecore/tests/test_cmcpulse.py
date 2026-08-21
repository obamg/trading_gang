"""CMCPulse — regime/crowding context collection and per-trade stamping.

The contract that matters most: this module is OBSERVATIONAL. It must never
cost an entry (snapshot swallows everything) and nothing in the bots reads
it to decide.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.modules.cmcpulse import collector as c


# Stashed before any monkeypatching: tests patch c.httpx.AsyncClient, which
# is the same module object as our httpx import — using it here would recurse.
_RealAsyncClient = httpx.AsyncClient


def _client(handler) -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


# --- symbol mapping -------------------------------------------------------

@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("XRPUSDT", "XRP"), ("BTCUSDT", "BTC"), ("ABCUSDC", "ABC"),
        ("XRP", "XRP"),                  # already a base coin
        ("USDT", "USDT"),                # never strip to empty
        (" hypeusdt ", "HYPE"),
    ],
)
def test_base_coin(symbol, expected):
    assert c.base_coin(symbol) == expected


# --- fear & greed ---------------------------------------------------------

FNG_PAYLOAD = {
    "data": {"value": 69, "update_time": "2026-08-21T05:53:10.024Z",
             "value_classification": "Greed"},
    "status": {"error_code": "0"},
}


@pytest.mark.asyncio
async def test_collect_fear_greed_stores_hash(monkeypatch, fake_redis):
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)

    def handle(request):
        return httpx.Response(200, json=FNG_PAYLOAD)

    monkeypatch.setattr(c.httpx, "AsyncClient", lambda **kw: _client(handle))
    entry = await c.collect_fear_greed()
    assert entry == {"value": "69", "classification": "Greed",
                     "update_time": "2026-08-21T05:53:10.024Z"}
    stored = await fake_redis.hgetall(c.FEAR_GREED_KEY)
    assert stored["value"] == "69"


@pytest.mark.asyncio
async def test_collect_fear_greed_survives_busy_error(monkeypatch, fake_redis):
    """CMC serves errors as HTTP 200 with an error body ('system is busy') —
    the altseason probe hit exactly this. Must not store garbage."""
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)

    def handle(request):
        return httpx.Response(200, json={"status": {"error_code": "500"}})

    monkeypatch.setattr(c.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await c.collect_fear_greed() is None
    assert await fake_redis.hgetall(c.FEAR_GREED_KEY) == {}


@pytest.mark.asyncio
async def test_collect_fear_greed_survives_http_error(monkeypatch, fake_redis):
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)

    def handle(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(c.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await c.collect_fear_greed() is None


# --- trending -------------------------------------------------------------

TRENDING_PAYLOAD = {
    "data": {"cryptoTopSearchRanks": [
        {"symbol": "XRP", "name": "XRP", "rank": 5,
         "priceChange": {"priceChange24h": 19.257}},
        {"symbol": "CRV", "name": "Curve", "rank": 80,
         "priceChange": {"priceChange24h": 16.84}},
        {"symbol": "", "name": "broken"},
        {"symbol": "XRP", "name": "dupe", "rank": 9,
         "priceChange": {"priceChange24h": 1.0}},
    ]}
}


@pytest.mark.asyncio
async def test_collect_trending_stores_positions_not_marketcap_rank(monkeypatch, fake_redis):
    """The payload's `rank` field is MARKET-CAP rank (XRP=5 coincidentally,
    CRV=80). The crowding signal is the 1-based position in the list."""
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)

    def handle(request):
        return httpx.Response(200, json=TRENDING_PAYLOAD)

    monkeypatch.setattr(c.httpx, "AsyncClient", lambda **kw: _client(handle))
    n = await c.collect_trending()
    assert n == 2  # empty symbol skipped, dupe deduped (first position wins)

    xrp = json.loads(await fake_redis.hget(c.TRENDING_KEY, "XRP"))
    crv = json.loads(await fake_redis.hget(c.TRENDING_KEY, "CRV"))
    assert xrp == [1, 19.257]   # position 1, NOT rank 5
    assert crv == [2, 16.84]


@pytest.mark.asyncio
async def test_collect_trending_replaces_wholesale(monkeypatch, fake_redis):
    """Yesterday's trending must not linger as today's."""
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)
    await fake_redis.hset(c.TRENDING_KEY, mapping={"OLD": json.dumps([1, 0.0])})

    def handle(request):
        return httpx.Response(200, json=TRENDING_PAYLOAD)

    monkeypatch.setattr(c.httpx, "AsyncClient", lambda **kw: _client(handle))
    await c.collect_trending()
    assert await fake_redis.hget(c.TRENDING_KEY, "OLD") is None


# --- read side ------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_context_maps_exchange_symbol_to_coin(monkeypatch, fake_redis):
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)
    await fake_redis.hset(c.FEAR_GREED_KEY, mapping={"value": "69", "classification": "Greed"})
    await fake_redis.hset(c.TRENDING_KEY, mapping={"XRP": json.dumps([1, 19.257])})

    ctx = await c.get_context("XRPUSDT")
    assert ctx["fear_greed"] == 69
    assert ctx["fear_greed_class"] == "Greed"
    assert ctx["trending_rank"] == 1
    assert float(ctx["trending_change_24h"]) == 19.257

    ctx2 = await c.get_context("BTCUSDT")  # not trending
    assert ctx2["trending_rank"] is None


@pytest.mark.asyncio
async def test_get_context_all_null_when_redis_empty(monkeypatch, fake_redis):
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)
    ctx = await c.get_context("XRPUSDT")
    assert ctx == {"fear_greed": None, "fear_greed_class": None,
                   "trending_rank": None, "trending_change_24h": None}


# --- the stamp must never cost an entry -----------------------------------

class _Trade:
    id = "t1"
    symbol = "XRPUSDT"
    strategy = "newsevent"


@pytest.mark.asyncio
async def test_snapshot_swallows_db_failure(monkeypatch, fake_redis):
    monkeypatch.setattr(c.redis_service, "get_redis", lambda: fake_redis)

    class _BoomDB:
        def add(self, row):
            raise RuntimeError("db down")

        async def rollback(self):
            pass

    # Must not raise — the entry that triggered it already exists.
    await c.snapshot_trade_context(_BoomDB(), _Trade())


@pytest.mark.asyncio
async def test_snapshot_swallows_redis_failure(monkeypatch):
    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(c.redis_service, "get_redis", boom)

    class _DB:
        def add(self, row):
            self.row = row

        async def commit(self):
            pass

    db = _DB()
    # Redis down → context nulls → row still written with nulls, no raise.
    await c.snapshot_trade_context(db, _Trade())
    assert db.row.fear_greed is None
    assert db.row.trade_id == "t1"


def test_executor_hook_is_guarded():
    """Both entry paths call the snapshot inside a try/except that also
    covers the import — an entry must never be lost to context."""
    import inspect

    from app.modules.majorsbot import executor

    src = inspect.getsource(executor)
    assert src.count("snapshot_trade_context") == 2
    assert src.count("trade_context_hook_failed") == 2
