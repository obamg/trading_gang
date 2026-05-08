"""Fill → Trade pairing algorithm.

Covers: simple long round-trip, simple short round-trip, scale-in entries,
partial closes, position flips, external entries (no prior open seen),
trust of exchange-reported realized_pnl, and pure idempotency of inputs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.exchanges.base import Fill
from app.services.exchanges.pairing import pair_fills


T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _f(
    side: str,
    qty: float,
    price: float,
    *,
    minutes: float = 0,
    fee: float = 0.0,
    realized: float | None = None,
    trade_id: str = "",
    symbol: str = "BTCUSDT",
) -> Fill:
    return Fill(
        exchange="binance",
        exchange_trade_id=trade_id or f"t{int(minutes * 1000)}",
        exchange_order_id=None,
        symbol=symbol,
        side=side,
        price=price,
        qty=qty,
        fee_usd=fee,
        fee_asset="USDT",
        realized_pnl_usd=realized,
        is_reduce_only=realized not in (None, 0.0),
        ts=T0 + timedelta(minutes=minutes),
    )


# ---------- simple round-trips ----------

def test_long_round_trip():
    fills = [
        _f("buy", 1.0, 100.0, minutes=0, fee=0.5, trade_id="open"),
        _f("sell", 1.0, 110.0, minutes=10, fee=0.5, realized=10.0, trade_id="close"),
    ]
    [tr] = pair_fills(fills)
    assert tr.side == "long"
    assert tr.entry_price == 100.0
    assert tr.exit_price == 110.0
    assert tr.size == 1.0
    assert tr.exchange_trade_id == "close"
    assert tr.pnl_usd == 10.0
    assert tr.fees_usd == 1.0
    assert tr.net_pnl_usd == 9.0
    assert tr.exit_reason is None


def test_short_round_trip():
    fills = [
        _f("sell", 2.0, 200.0, minutes=0, fee=1.0, trade_id="open"),
        _f("buy", 2.0, 180.0, minutes=5, fee=1.0, realized=40.0, trade_id="close"),
    ]
    [tr] = pair_fills(fills)
    assert tr.side == "short"
    assert tr.entry_price == 200.0
    assert tr.exit_price == 180.0
    assert tr.pnl_usd == 40.0
    assert tr.net_pnl_usd == 38.0


# ---------- scaling in ----------

def test_scale_in_uses_weighted_average_entry():
    fills = [
        _f("buy", 1.0, 100.0, minutes=0, fee=0.5),
        _f("buy", 1.0, 120.0, minutes=1, fee=0.5),  # weighted avg = 110
        _f("sell", 2.0, 130.0, minutes=2, fee=1.0, realized=40.0),
    ]
    [tr] = pair_fills(fills)
    assert tr.entry_price == 110.0
    assert tr.size == 2.0
    assert tr.pnl_usd == 40.0  # trusts realized
    assert tr.fees_usd == 2.0  # 0.5 + 0.5 entry + 1.0 close


# ---------- partial close ----------

def test_partial_close_emits_one_trade_keeps_remainder_open():
    fills = [
        _f("buy", 2.0, 100.0, minutes=0, fee=1.0),
        _f("sell", 1.0, 110.0, minutes=5, fee=0.5, realized=10.0, trade_id="partial"),
    ]
    trades = pair_fills(fills)
    assert len(trades) == 1
    tr = trades[0]
    assert tr.size == 1.0
    assert tr.pnl_usd == 10.0
    # Half of entry fees pro-rated to closed portion + the closing fee.
    assert tr.fees_usd == 1.0  # 0.5 (half of 1.0) + 0.5
    assert tr.net_pnl_usd == 9.0


def test_two_partial_closes_emit_two_trades():
    fills = [
        _f("buy", 4.0, 100.0, minutes=0, fee=2.0),
        _f("sell", 1.0, 110.0, minutes=5, fee=0.5, realized=10.0, trade_id="p1"),
        _f("sell", 3.0, 120.0, minutes=10, fee=1.5, realized=60.0, trade_id="p2"),
    ]
    trades = pair_fills(fills)
    assert len(trades) == 2
    assert trades[0].size == 1.0
    assert trades[0].pnl_usd == 10.0
    assert trades[1].size == 3.0
    assert trades[1].pnl_usd == 60.0


# ---------- position flip ----------

def test_position_flip_closes_old_and_opens_new():
    fills = [
        _f("buy", 1.0, 100.0, minutes=0, fee=0.5),
        # Sells 3 — closes the long 1 and opens a short 2.
        _f("sell", 3.0, 110.0, minutes=5, fee=1.5, realized=10.0, trade_id="flip"),
        _f("buy", 2.0, 105.0, minutes=10, fee=1.0, realized=10.0, trade_id="cover"),
    ]
    trades = pair_fills(fills)
    assert len(trades) == 2
    assert trades[0].side == "long"
    assert trades[0].size == 1.0
    assert trades[1].side == "short"
    assert trades[1].size == 2.0
    assert trades[1].entry_price == 110.0
    assert trades[1].exit_price == 105.0


# ---------- external entry ----------

def test_external_entry_recorded_without_pnl():
    # No prior position, but the fill claims realized PnL — user opened
    # before connecting their key.
    fills = [
        _f("sell", 1.0, 110.0, minutes=0, fee=0.5, realized=10.0, trade_id="ext"),
    ]
    [tr] = pair_fills(fills)
    assert tr.exit_reason == "external_entry"
    assert tr.pnl_usd is None
    assert tr.net_pnl_usd is None
    assert tr.exchange_trade_id == "ext"


def test_external_entry_does_not_block_subsequent_pairs():
    fills = [
        _f("sell", 1.0, 110.0, minutes=0, fee=0.5, realized=10.0, trade_id="ext"),
        _f("buy", 2.0, 100.0, minutes=5, fee=1.0, trade_id="open"),
        _f("sell", 2.0, 105.0, minutes=10, fee=1.0, realized=10.0, trade_id="close"),
    ]
    trades = pair_fills(fills)
    assert len(trades) == 2
    assert trades[0].exit_reason == "external_entry"
    assert trades[1].exit_reason is None
    assert trades[1].entry_price == 100.0


# ---------- multi-symbol ----------

def test_two_symbols_are_paired_independently():
    fills = [
        _f("buy", 1.0, 100.0, minutes=0, symbol="BTCUSDT", trade_id="b1"),
        _f("buy", 1.0, 50.0, minutes=1, symbol="ETHUSDT", trade_id="e1"),
        _f("sell", 1.0, 110.0, minutes=5, symbol="BTCUSDT", realized=10.0, trade_id="b2"),
        _f("sell", 1.0, 55.0, minutes=6, symbol="ETHUSDT", realized=5.0, trade_id="e2"),
    ]
    trades = pair_fills(fills)
    assert {t.symbol for t in trades} == {"BTCUSDT", "ETHUSDT"}
    btc = next(t for t in trades if t.symbol == "BTCUSDT")
    eth = next(t for t in trades if t.symbol == "ETHUSDT")
    assert btc.entry_price == 100.0 and btc.exit_price == 110.0
    assert eth.entry_price == 50.0 and eth.exit_price == 55.0


# ---------- chronology ----------

def test_unsorted_input_is_sorted_before_pairing():
    sell = _f("sell", 1.0, 110.0, minutes=10, fee=0.5, realized=10.0, trade_id="close")
    buy = _f("buy", 1.0, 100.0, minutes=0, fee=0.5, trade_id="open")
    [tr] = pair_fills([sell, buy])
    assert tr.entry_price == 100.0
    assert tr.exit_price == 110.0


# ---------- trust of realized_pnl ----------

def test_realized_pnl_overrides_computed():
    # Computed P&L = (110-100)*1 = 10, but exchange says 12 (e.g. funding).
    fills = [
        _f("buy", 1.0, 100.0, minutes=0, fee=0.0),
        _f("sell", 1.0, 110.0, minutes=5, fee=0.0, realized=12.0),
    ]
    [tr] = pair_fills(fills)
    assert tr.pnl_usd == 12.0


# ---------- still-open positions ----------

def test_open_position_emits_no_trade():
    fills = [_f("buy", 1.0, 100.0)]
    assert pair_fills(fills) == []


def test_empty_input():
    assert pair_fills([]) == []
