"""Binance adapter — validation rules + fill normalization.

HTTP is mocked via httpx.MockTransport. We verify:
  - keys with withdrawals enabled are refused
  - keys without futures permission are refused
  - userTrades rows are mapped to canonical Fill objects
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.exchanges.base import Credentials
from app.services.exchanges.binance import (
    BinanceAdapter,
    BinancePermissionError,
    _normalize_trade,
)


CREDS = Credentials(api_key="key", api_secret="secret")


_RealAsyncClient = httpx.AsyncClient


def _build_client(handler):
    return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# ---------- _normalize_trade (pure) ----------

def test_normalize_buy_fill():
    row = {
        "id": 12345,
        "orderId": 99,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": "50000.5",
        "qty": "0.1",
        "commission": "1.25",
        "commissionAsset": "USDT",
        "realizedPnl": "0",
        "time": 1700000000000,
    }
    f = _normalize_trade(row)
    assert f.exchange == "binance"
    assert f.exchange_trade_id == "12345"
    assert f.exchange_order_id == "99"
    assert f.symbol == "BTCUSDT"
    assert f.side == "buy"
    assert f.price == 50000.5
    assert f.qty == 0.1
    assert f.fee_usd == 1.25
    assert f.realized_pnl_usd == 0.0
    assert f.is_reduce_only is False


def test_normalize_closing_fill_marks_reduce_only():
    row = {
        "id": 999, "orderId": 1, "symbol": "ETHUSDT",
        "side": "SELL", "price": "3000", "qty": "1",
        "commission": "1.5", "commissionAsset": "USDT",
        "realizedPnl": "50.25", "time": 1700000000000,
    }
    f = _normalize_trade(row)
    assert f.side == "sell"
    assert f.realized_pnl_usd == 50.25
    assert f.is_reduce_only is True


def test_normalize_bnb_fee_is_zeroed_in_usd():
    row = {
        "id": 1, "orderId": 1, "symbol": "BTCUSDT",
        "side": "BUY", "price": "50000", "qty": "0.1",
        "commission": "0.001", "commissionAsset": "BNB",
        "realizedPnl": "0", "time": 1700000000000,
    }
    f = _normalize_trade(row)
    # We can't price BNB from the trade row alone — leave as 0 rather than
    # silently double-counting in some other unit.
    assert f.fee_usd == 0.0
    assert f.fee_asset == "BNB"


# ---------- validate ----------

@pytest.mark.asyncio
async def test_validate_refuses_withdrawal_enabled_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/sapi/v1/account/apiRestrictions" in request.url.path:
            return httpx.Response(200, json={
                "enableReading": True,
                "enableFutures": True,
                "enableWithdrawals": True,  # <-- not allowed
            })
        return httpx.Response(404)

    adapter = BinanceAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.binance.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    with pytest.raises(BinancePermissionError, match="withdrawals"):
        await adapter.validate(CREDS)


@pytest.mark.asyncio
async def test_validate_refuses_key_without_futures(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/sapi/v1/account/apiRestrictions" in request.url.path:
            return httpx.Response(200, json={
                "enableReading": True,
                "enableFutures": False,  # <-- required
                "enableWithdrawals": False,
            })
        return httpx.Response(404)

    adapter = BinanceAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.binance.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    with pytest.raises(BinancePermissionError, match="Futures"):
        await adapter.validate(CREDS)


@pytest.mark.asyncio
async def test_validate_accepts_well_scoped_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/sapi/v1/account/apiRestrictions" in request.url.path:
            return httpx.Response(200, json={
                "enableReading": True,
                "enableFutures": True,
                "enableWithdrawals": False,
                "enableSpotAndMarginTrading": False,
                "ipRestrict": True,
            })
        if "/fapi/v2/account" in request.url.path:
            return httpx.Response(200, json={"totalWalletBalance": "0"})
        return httpx.Response(404)

    adapter = BinanceAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.binance.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )
    perms = await adapter.validate(CREDS)
    assert perms["futures"] is True
    assert perms["withdraw"] is False
    assert perms["ip_restricted"] is True


# ---------- fetch_fills ----------

@pytest.mark.asyncio
async def test_fetch_fills_discovers_symbols_and_pages(monkeypatch):
    income_calls = []
    user_trade_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if "/fapi/v1/income" in path:
            income_calls.append(params)
            # Return one symbol regardless of window so we don't loop forever.
            return httpx.Response(200, json=[
                {"symbol": "BTCUSDT", "income": "5.0", "time": int(params.get("startTime", 0))}
            ])
        if "/fapi/v1/userTrades" in path:
            user_trade_calls.append(params)
            return httpx.Response(200, json=[{
                "id": 1, "orderId": 1, "symbol": "BTCUSDT",
                "side": "BUY", "price": "50000", "qty": "0.1",
                "commission": "1.0", "commissionAsset": "USDT",
                "realizedPnl": "0", "time": int(params.get("startTime", 0)) + 1000,
            }])
        return httpx.Response(404)

    adapter = BinanceAdapter()
    monkeypatch.setattr(
        "app.services.exchanges.binance.httpx.AsyncClient",
        lambda **kw: _build_client(handler),
    )

    # 10-day window forces at least 2 income pages (7-day chunks).
    since = datetime.now(timezone.utc) - timedelta(days=10)
    fills = await adapter.fetch_fills(CREDS, since=since)

    assert len(income_calls) >= 2
    assert all(f.symbol == "BTCUSDT" for f in fills)
    assert all(f.exchange == "binance" for f in fills)
    # Sorted oldest-first.
    assert fills == sorted(fills, key=lambda x: x.ts)
