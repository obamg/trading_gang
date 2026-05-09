"""Bybit stream translator tests — verify Bybit shapes map cleanly to the
Binance-compatible Redis schema. Pure-function tests, no I/O.
"""
from __future__ import annotations

from app.services.bybit_stream import (
    parse_topic,
    translate_kline,
    translate_liquidation,
    translate_orderbook_top,
    translate_trade,
)


# ---------- topic parsing ----------


def test_parse_topic_kline():
    assert parse_topic("kline.5.BTCUSDT") == ("kline.5", "BTCUSDT")


def test_parse_topic_public_trade():
    assert parse_topic("publicTrade.ETHUSDT") == ("publicTrade", "ETHUSDT")


def test_parse_topic_orderbook():
    assert parse_topic("orderbook.1.SOLUSDT") == ("orderbook.1", "SOLUSDT")


def test_parse_topic_liquidation():
    assert parse_topic("allLiquidation.DOGEUSDT") == ("allLiquidation", "DOGEUSDT")


def test_parse_topic_unknown_returns_none():
    assert parse_topic("foo.bar") is None
    assert parse_topic("") is None
    # Numeric-only doesn't match any known prefix.
    assert parse_topic("1.2") is None


# ---------- kline translation ----------


def test_translate_kline_closed_candle():
    item = {
        "start": 1672324800000,
        "end": 1672325099999,
        "interval": "5",
        "open": "16649.5",
        "close": "16677",
        "high": "16678.1",
        "low": "16608",
        "volume": "2.081",
        "turnover": "34666.78",
        "confirm": True,
        "timestamp": 1672324988882,
    }
    out = translate_kline(item)
    assert out is not None
    assert out["t"] == 1672324800000
    assert out["T"] == 1672325099999
    assert out["o"] == 16649.5
    assert out["h"] == 16678.1
    assert out["l"] == 16608.0
    assert out["c"] == 16677.0
    assert out["v"] == 2.081
    assert out["q"] == 34666.78
    assert out["n"] == 0  # Bybit doesn't expose trade count


def test_translate_kline_unconfirmed_returns_none():
    item = {
        "start": 1, "end": 2, "open": "1", "close": "1",
        "high": "1", "low": "1", "volume": "1", "turnover": "1",
        "confirm": False,
    }
    assert translate_kline(item) is None


def test_translate_kline_missing_fields_returns_none():
    assert translate_kline({"confirm": True}) is None


# ---------- trade translation ----------


def test_translate_trade_buy_taker():
    """S=Buy means taker is buyer → buyer is NOT maker → m=0."""
    item = {
        "T": 1672304486865,
        "s": "BTCUSDT",
        "S": "Buy",
        "v": "0.001",
        "p": "16578.50",
        "i": "abc",
    }
    out = translate_trade(item)
    assert out == {
        "p": 16578.50,
        "q": 0.001,
        "usd": 16578.50 * 0.001,
        "m": 0,
        "T": 1672304486865,
        "a": "abc",
    }


def test_translate_trade_sell_taker():
    """S=Sell means taker is seller → buyer IS maker → m=1."""
    item = {
        "T": 1, "s": "ETHUSDT", "S": "Sell",
        "v": "0.5", "p": "2000", "i": "id1",
    }
    out = translate_trade(item)
    assert out["m"] == 1
    assert out["usd"] == 1000.0


def test_translate_trade_missing_fields_returns_none():
    assert translate_trade({"S": "Buy"}) is None
    assert translate_trade({"p": "x", "v": "1"}) is None


# ---------- orderbook translation ----------


def test_translate_orderbook_top_basic():
    data = {
        "s": "BTCUSDT",
        "b": [["16493.50", "0.006"]],
        "a": [["16493.75", "0.090"]],
    }
    assert translate_orderbook_top(data) == (16493.50, 16493.75)


def test_translate_orderbook_top_empty_side_returns_none():
    # During delta updates Bybit can drop one side temporarily.
    assert translate_orderbook_top({"b": [], "a": [["1", "1"]]}) is None
    assert translate_orderbook_top({"b": [["1", "1"]], "a": []}) is None
    assert translate_orderbook_top({}) is None


def test_translate_orderbook_top_malformed_returns_none():
    assert translate_orderbook_top({"b": [["x", "1"]], "a": [["1", "1"]]}) is None


# ---------- liquidation translation ----------


def test_translate_liquidation_long_liquidated():
    """Bybit S=Sell → long position liquidated → side='long' (matches Binance forceOrder)."""
    item = {
        "T": 1739502302929,
        "s": "BTCUSDT",
        "S": "Sell",
        "v": "0.001",
        "p": "26000.00",
    }
    out = translate_liquidation(item)
    assert out is not None
    assert out["symbol"] == "BTCUSDT"
    assert out["side"] == "long"
    assert out["price"] == 26000.0
    assert out["qty"] == 0.001
    assert out["usd"] == 26.0
    assert out["T"] == 1739502302929


def test_translate_liquidation_short_liquidated():
    item = {"T": 1, "s": "ETHUSDT", "S": "Buy", "v": "1", "p": "2000"}
    out = translate_liquidation(item)
    assert out is not None
    assert out["side"] == "short"


def test_translate_liquidation_missing_symbol_returns_none():
    assert translate_liquidation({"S": "Buy", "v": "1", "p": "1"}) is None
