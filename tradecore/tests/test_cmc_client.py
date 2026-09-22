"""CMC shared client — the failure handling is the whole point of the file.

The contract worth protecting: a rate limit or an unentitled endpoint must
COST ONE CALL and then stop, because the alternative is the CoinGecko
spiral (13,955 failed calls in 24h) that walletwatch already paid for once.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import cmc_client as cmc

# Stashed before any monkeypatching: tests patch cmc.httpx.AsyncClient, which
# is the same module object as our httpx import — using it here would recurse.
_RealAsyncClient = httpx.AsyncClient


def _client(handler) -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


def _ok(data, credits: int = 1) -> dict:
    return {"status": {"error_code": 0, "error_message": None,
                       "credit_count": credits}, "data": data}


def _err(message: str) -> dict:
    return {"status": {"error_code": 1001, "error_message": message}}


@pytest.fixture
def keyed(monkeypatch, fake_redis):
    """A configured key + fake Redis, the normal operating state."""
    monkeypatch.setattr(cmc.redis_service, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(cmc, "api_key", lambda: "test-key")
    return fake_redis


# --- the disabled default -------------------------------------------------

@pytest.mark.asyncio
async def test_no_key_short_circuits_without_network(monkeypatch, fake_redis):
    """Unset key must not touch the network — it is the default state."""
    monkeypatch.setattr(cmc.redis_service, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(cmc, "api_key", lambda: "")

    def handle(request):  # pragma: no cover - must never run
        raise AssertionError("network touched with no API key configured")

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert cmc.enabled() is False
    assert await cmc.get("/v1/key/info") is None


# --- happy path + credit accounting ---------------------------------------

@pytest.mark.asyncio
async def test_get_returns_data_and_sends_key_header(monkeypatch, keyed):
    seen = {}

    def handle(request):
        seen["key"] = request.headers.get("X-CMC_PRO_API_KEY")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_ok([{"symbol": "BTC"}]))

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    data = await cmc.get("/v1/cryptocurrency/trending/latest", {"limit": 5})
    assert data == [{"symbol": "BTC"}]
    assert seen["key"] == "test-key"
    assert "limit=5" in seen["url"]


@pytest.mark.asyncio
async def test_credits_accumulate_across_calls(monkeypatch, keyed):
    def handle(request):
        return httpx.Response(200, json=_ok([], credits=3))

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    await cmc.get("/v1/a")
    await cmc.get("/v1/b")
    assert await cmc.credits_used_this_month() == 6


@pytest.mark.asyncio
async def test_empty_data_is_none_not_exception(monkeypatch, keyed):
    def handle(request):
        return httpx.Response(200, json={"status": {"credit_count": 1}})

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await cmc.get("/v1/whatever") is None


# --- 429: the spiral guard ------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_trips_breaker_and_stops_calling(monkeypatch, keyed):
    calls = {"n": 0}

    def handle(request):
        calls["n"] += 1
        return httpx.Response(429, json=_err("rate limited"))

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await cmc.get("/v1/a") is None
    assert await keyed.get(cmc.BREAKER_KEY) is not None

    # A DIFFERENT path must also be suppressed: the quota is per key.
    assert await cmc.get("/v1/b") is None
    assert calls["n"] == 1, "breaker must stop the second request"


# --- 403: the entitlement probe -------------------------------------------

@pytest.mark.asyncio
async def test_not_entitled_is_per_path_and_sticky(monkeypatch, keyed):
    """A 403 kills ONE path for a day; other paths keep working.

    This is how we discover what the free Basic plan actually covers —
    without it, an unentitled endpoint would burn a call on every tick.
    """
    calls: list[str] = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/gated":
            return httpx.Response(403, json=_err("plan not authorized"))
        return httpx.Response(200, json=_ok([{"symbol": "ETH"}]))

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await cmc.get("/v1/gated") is None
    assert await cmc.get("/v1/gated") is None          # suppressed locally
    assert calls.count("/v1/gated") == 1

    assert await cmc.get("/v1/open") == [{"symbol": "ETH"}]
    assert await keyed.get(cmc.BREAKER_KEY) is None, "403 must not trip the breaker"


# --- caching --------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_key_serves_second_call_without_network(monkeypatch, keyed):
    calls = {"n": 0}

    def handle(request):
        calls["n"] += 1
        return httpx.Response(200, json=_ok({"v": 1}))

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    first = await cmc.get("/v1/x", cache_key="cmc:test:x", cache_ttl=60)
    second = await cmc.get("/v1/x", cache_key="cmc:test:x", cache_ttl=60)
    assert first == second == {"v": 1}
    assert calls["n"] == 1


# --- transport failures are soft ------------------------------------------

@pytest.mark.asyncio
async def test_timeout_returns_none(monkeypatch, keyed):
    def handle(request):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await cmc.get("/v1/x") is None
    assert await keyed.get(cmc.BREAKER_KEY) is None, "a timeout is not a rate limit"


@pytest.mark.asyncio
async def test_non_json_body_returns_none(monkeypatch, keyed):
    def handle(request):
        return httpx.Response(200, text="<html>maintenance</html>")

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await cmc.get("/v1/x") is None


# --- typed helpers --------------------------------------------------------

@pytest.mark.asyncio
async def test_quotes_latest_batches_symbols_into_one_call(monkeypatch, keyed):
    seen = {}

    def handle(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_ok({"BTC": {}, "ETH": {}}))

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    data = await cmc.quotes_latest(["btc", " eth ", ""])
    assert set(data) == {"BTC", "ETH"}
    assert "symbol=BTC%2CETH" in seen["url"] or "symbol=BTC,ETH" in seen["url"]


@pytest.mark.asyncio
async def test_quotes_latest_no_symbols_is_noop(monkeypatch, keyed):
    def handle(request):  # pragma: no cover - must never run
        raise AssertionError("called with no symbols")

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    assert await cmc.quotes_latest([]) is None


@pytest.mark.asyncio
async def test_trending_rejects_unknown_kind(keyed):
    with pytest.raises(ValueError):
        await cmc.trending("nonsense")


@pytest.mark.asyncio
async def test_trending_latest_omits_time_period(monkeypatch, keyed):
    """time_period is a gainers/most-visited parameter, not a latest one."""
    seen = {}

    def handle(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_ok([]))

    monkeypatch.setattr(cmc.httpx, "AsyncClient", lambda **kw: _client(handle))
    await cmc.trending("latest")
    assert "time_period" not in seen["url"]

    await cmc.trending("most-visited")
    assert "time_period=24h" in seen["url"]
