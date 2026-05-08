"""Bybit adapter — validation rules + execution-row normalization.

Same shape as test_binance_adapter.py — proves the abstraction holds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.exchanges.base import Credentials
from app.services.exchanges.bybit import (
    BybitAdapter,
    BybitPermissionError,
    _normalize_exec,
)


CREDS = Credentials(api_key="key", api_secret="secret")
_RealAsyncClient = httpx.AsyncClient


def _build_client(handler):
    return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# ---------- _normalize_exec (pure) ----------

def test_normalize_buy_exec():
    row = {
        "execId": "abc-123",
        "orderId": "ord-99",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "execPrice": "50000.5",
        "execQty": "0.1",
        "execFee": "0.5",
        "feeCurrency": "USDT",
        "execPnl": "0",
        "closedSize": "0",
        "execTime": "1700000000000",
    }
    f = _normalize_exec(row)
    assert f.exchange == "bybit"
    assert f.exchange_trade_id == "abc-123"
    assert f.symbol == "BTCUSDT"
    assert f.side == "buy"
    assert f.price == 50000.5
    assert f.qty == 0.1
    assert f.fee_usd == 0.5
    assert f.is_reduce_only is False


def test_normalize_closing_exec():
    row = {
        "execId": "x", "orderId": "o", "symbol": "ETHUSDT",
        "side": "Sell", "execPrice": "3000", "execQty": "1",
        "execFee": "1", "feeCurrency": "USDT",
        "execPnl": "25.5", "closedSize": "1",
        "execTime": "1700000000000",
    }
    f = _normalize_exec(row)
    assert f.side == "sell"
    assert f.realized_pnl_usd == 25.5
    assert f.is_reduce_only is True


# ---------- validate ----------

@pytest.mark.asyncio
async def test_validate_refuses_withdrawal_permission(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v5/user/query-api" in request.url.path:
            return httpx.Response(200, json={
                "retCode": 0, "retMsg": "OK",
                "result": {
                    "permissions": {
                        "ContractTrade": ["Order", "Position"],
                        "Wallet": ["AccountTransfer", "SubMemberTransfer", "Withdraw"],
                    },
                    "ips": ["1.2.3.4"],
                },
            })
        return httpx.Response(404)

    adapter = BybitAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.bybit.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    with pytest.raises(BybitPermissionError, match="withdrawal"):
        await adapter.validate(CREDS)


@pytest.mark.asyncio
async def test_validate_accepts_contract_only_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v5/user/query-api" in request.url.path:
            return httpx.Response(200, json={
                "retCode": 0, "retMsg": "OK",
                "result": {
                    "permissions": {"ContractTrade": ["Order", "Position"]},
                    "ips": ["1.2.3.4"],
                },
            })
        return httpx.Response(404)

    adapter = BybitAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.bybit.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    perms = await adapter.validate(CREDS)
    assert perms["futures"] is True
    assert perms["withdraw"] is False
    assert perms["ip_restricted"] is True


@pytest.mark.asyncio
async def test_validate_rejects_key_without_derivatives(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v5/user/query-api" in request.url.path:
            return httpx.Response(200, json={
                "retCode": 0, "retMsg": "OK",
                "result": {"permissions": {"Spot": ["Order"]}},
            })
        return httpx.Response(404)

    adapter = BybitAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.bybit.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    with pytest.raises(BybitPermissionError, match="Derivatives|Contract"):
        await adapter.validate(CREDS)


# ---------- fetch_fills + pagination ----------

@pytest.mark.asyncio
async def test_fetch_fills_paginates_via_cursor(monkeypatch):
    page_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v5/execution/list" in request.url.path:
            params = dict(request.url.params)
            page_calls.append(params)
            cursor = params.get("cursor")
            if cursor is None:
                # First page returns one fill + a next cursor.
                return httpx.Response(200, json={
                    "retCode": 0, "retMsg": "OK",
                    "result": {
                        "list": [{
                            "execId": "1", "orderId": "o1", "symbol": "BTCUSDT",
                            "side": "Buy", "execPrice": "50000", "execQty": "0.1",
                            "execFee": "0.5", "feeCurrency": "USDT",
                            "execPnl": "0", "closedSize": "0",
                            "execTime": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                        }],
                        "nextPageCursor": "PAGE2",
                    },
                })
            # Second page closes pagination with no further cursor.
            return httpx.Response(200, json={
                "retCode": 0, "retMsg": "OK",
                "result": {
                    "list": [{
                        "execId": "2", "orderId": "o2", "symbol": "BTCUSDT",
                        "side": "Sell", "execPrice": "51000", "execQty": "0.1",
                        "execFee": "0.5", "feeCurrency": "USDT",
                        "execPnl": "100", "closedSize": "0.1",
                        "execTime": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                    }],
                    "nextPageCursor": "",
                },
            })
        return httpx.Response(404)

    adapter = BybitAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.bybit.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    since = datetime.now(timezone.utc) - timedelta(days=1)
    fills = await adapter.fetch_fills(CREDS, since=since)

    assert len(fills) == 2
    assert fills[0].exchange_trade_id == "1"
    assert fills[1].exchange_trade_id == "2"
    # Used the cursor — second call had cursor=PAGE2.
    assert any(p.get("cursor") == "PAGE2" for p in page_calls)


@pytest.mark.asyncio
async def test_signed_get_query_string_matches_signed_payload(monkeypatch):
    """Regression: the URL we send must serialize params in the same order
    we used to compute the signature. Bybit will reject with retCode=10004
    "error sign" if the bytes differ.
    """
    import hashlib
    import hmac
    import urllib.parse

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v5/execution/list" in request.url.path:
            seen["raw_query"] = request.url.query.decode() if isinstance(request.url.query, bytes) else str(request.url.query)
            seen["sign"] = request.headers.get("X-BAPI-SIGN")
            seen["timestamp"] = request.headers.get("X-BAPI-TIMESTAMP")
            seen["recv_window"] = request.headers.get("X-BAPI-RECV-WINDOW")
            return httpx.Response(200, json={
                "retCode": 0, "retMsg": "OK",
                "result": {"list": [], "nextPageCursor": ""},
            })
        return httpx.Response(404)

    adapter = BybitAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.bybit.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    await adapter.fetch_fills(CREDS, since=since)

    # The wire query string must be the exact byte sequence the signature was
    # computed over. Recompute the HMAC the same way the adapter did and assert
    # it matches the X-BAPI-SIGN header — this only holds when the URL we sent
    # is byte-identical to the signed payload.
    raw = f"{seen['timestamp']}{CREDS.api_key}{seen['recv_window']}{seen['raw_query']}"
    expected = hmac.new(CREDS.api_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    assert seen["sign"] == expected
    # And the wire query must be sorted (the property we're protecting).
    pairs = urllib.parse.parse_qsl(seen["raw_query"])
    keys = [k for k, _ in pairs]
    assert keys == sorted(keys)
