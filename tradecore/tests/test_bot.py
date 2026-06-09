"""WaveBot — strategy + veto logic tests.

Focus on the pure functions in strategy.py. The listener/executor/monitor
are integration-shaped (DB + Redis) and covered by the FastAPI integration
suite in CI.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.modules.bot import strategy
from app.modules.bot.schemas import Direction


def _candle(h, l, c, t=1717920000000) -> dict:
    return {"t": t, "o": (h + l) / 2, "h": h, "l": l, "c": c, "v": 1000}


class TestDirectionMapping:
    def test_short_squeeze_is_long(self):
        assert strategy.map_direction("short_squeeze") == Direction.LONG

    def test_long_flush_is_short(self):
        assert strategy.map_direction("long_flush") == Direction.SHORT

    def test_unknown_returns_none(self):
        assert strategy.map_direction("something_else") is None
        assert strategy.map_direction("") is None


class TestStop:
    def test_long_stop_is_below_signal_low(self):
        # 100 low, 0.05% buffer → 100 × 0.9995 = 99.95
        stop = strategy.compute_stop(
            Direction.LONG, Decimal("105"), Decimal("100"), Decimal("0.0005")
        )
        assert stop == Decimal("99.9500")

    def test_short_stop_is_above_signal_high(self):
        # 105 high, 0.05% buffer → 105 × 1.0005 = 105.0525
        stop = strategy.compute_stop(
            Direction.SHORT, Decimal("105"), Decimal("100"), Decimal("0.0005")
        )
        assert stop == Decimal("105.0525")


class TestTakeProfit:
    def test_long_tp_is_two_r_above_entry(self):
        # entry 100, stop 95 → risk 5 → TP at 100 + 2×5 = 110
        tp = strategy.compute_take_profit(
            Direction.LONG, Decimal("100"), Decimal("95"), Decimal("2")
        )
        assert tp == Decimal("110")

    def test_short_tp_is_two_r_below_entry(self):
        # entry 100, stop 105 → risk 5 → TP at 100 - 2×5 = 90
        tp = strategy.compute_take_profit(
            Direction.SHORT, Decimal("100"), Decimal("105"), Decimal("2")
        )
        assert tp == Decimal("90")


class TestHitDetection:
    def test_long_stop_hit_when_bar_low_at_or_below_stop(self):
        assert strategy.is_stop_hit(Direction.LONG, Decimal("100"), Decimal("95"), Decimal("95"))
        assert strategy.is_stop_hit(Direction.LONG, Decimal("100"), Decimal("94"), Decimal("95"))
        assert not strategy.is_stop_hit(Direction.LONG, Decimal("100"), Decimal("96"), Decimal("95"))

    def test_short_stop_hit_when_bar_high_at_or_above_stop(self):
        assert strategy.is_stop_hit(Direction.SHORT, Decimal("105"), Decimal("100"), Decimal("105"))
        assert strategy.is_stop_hit(Direction.SHORT, Decimal("106"), Decimal("100"), Decimal("105"))
        assert not strategy.is_stop_hit(Direction.SHORT, Decimal("104"), Decimal("100"), Decimal("105"))

    def test_long_tp_hit_when_bar_high_at_or_above_tp(self):
        assert strategy.is_tp_hit(Direction.LONG, Decimal("110"), Decimal("105"), Decimal("110"))
        assert not strategy.is_tp_hit(Direction.LONG, Decimal("109"), Decimal("105"), Decimal("110"))

    def test_short_tp_hit_when_bar_low_at_or_below_tp(self):
        assert strategy.is_tp_hit(Direction.SHORT, Decimal("100"), Decimal("90"), Decimal("90"))
        assert not strategy.is_tp_hit(Direction.SHORT, Decimal("100"), Decimal("91"), Decimal("90"))


class TestRealizedPnl:
    def test_long_winner(self):
        pnl = strategy.realized_pnl(Direction.LONG, Decimal("100"), Decimal("110"), Decimal("5"))
        assert pnl == Decimal("50")

    def test_long_loser(self):
        pnl = strategy.realized_pnl(Direction.LONG, Decimal("100"), Decimal("95"), Decimal("5"))
        assert pnl == Decimal("-25")

    def test_short_winner(self):
        pnl = strategy.realized_pnl(Direction.SHORT, Decimal("100"), Decimal("90"), Decimal("5"))
        assert pnl == Decimal("50")

    def test_short_loser(self):
        pnl = strategy.realized_pnl(Direction.SHORT, Decimal("100"), Decimal("105"), Decimal("5"))
        assert pnl == Decimal("-25")


class TestRMultiple:
    def test_long_2r_winner(self):
        # entry 100, stop 95 (risk 5), exit 110 → +10/5 = +2R
        r = strategy.realized_r_multiple(
            Direction.LONG, Decimal("100"), Decimal("95"), Decimal("110")
        )
        assert r == Decimal("2")

    def test_long_stop_is_minus_one_r(self):
        r = strategy.realized_r_multiple(
            Direction.LONG, Decimal("100"), Decimal("95"), Decimal("95")
        )
        assert r == Decimal("-1")

    def test_short_2r_winner(self):
        # entry 100, stop 105 (risk 5), exit 90 → +10/5 = +2R
        r = strategy.realized_r_multiple(
            Direction.SHORT, Decimal("100"), Decimal("105"), Decimal("90")
        )
        assert r == Decimal("2")

    def test_zero_risk_returns_zero(self):
        r = strategy.realized_r_multiple(
            Direction.LONG, Decimal("100"), Decimal("100"), Decimal("110")
        )
        assert r == Decimal("0")


class TestQty:
    def test_qty_is_notional_over_entry(self):
        assert strategy.compute_qty(Decimal("500"), Decimal("100")) == Decimal("5")

    def test_zero_entry_returns_zero(self):
        assert strategy.compute_qty(Decimal("500"), Decimal("0")) == Decimal("0")


class TestPlanEntry:
    def _alert(self, direction="short_squeeze") -> dict:
        return {
            "type": "wave_active",
            "symbol": "BTCUSDT",
            "exchange": "bybit",
            "base_asset": "BTC",
            "direction": direction,
            "pct_change": 0.034,
            "vol_ratio": 5.2,
            "funding_pct": -0.0015,
            "detected_at": datetime(2026, 6, 9, 11, 30, tzinfo=timezone.utc).isoformat(),
        }

    def test_long_plan_has_stop_below_entry_and_tp_above(self):
        plan = strategy.plan_entry(
            alert=self._alert("short_squeeze"),
            signal_candle=_candle(h=105, l=100, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert plan is not None
        assert plan.direction == Direction.LONG
        assert plan.stop_price < Decimal("104")
        assert plan.take_profit_price > Decimal("104")
        assert plan.notional_usd == Decimal("500.000")  # 5% × 10000

    def test_short_plan_has_stop_above_entry_and_tp_below(self):
        plan = strategy.plan_entry(
            alert=self._alert("long_flush"),
            signal_candle=_candle(h=105, l=100, c=101),
            entry_price=Decimal("101"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert plan is not None
        assert plan.direction == Direction.SHORT
        assert plan.stop_price > Decimal("101")
        assert plan.take_profit_price < Decimal("101")

    def test_invalid_direction_returns_none(self):
        plan = strategy.plan_entry(
            alert=self._alert("nope"),
            signal_candle=_candle(h=105, l=100, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert plan is None

    def test_zero_equity_returns_none(self):
        plan = strategy.plan_entry(
            alert=self._alert("short_squeeze"),
            signal_candle=_candle(h=105, l=100, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("0"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert plan is None

    def test_broken_candle_returns_none(self):
        # low ≥ high — broken
        plan = strategy.plan_entry(
            alert=self._alert("short_squeeze"),
            signal_candle=_candle(h=100, l=100, c=100),
            entry_price=Decimal("100"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert plan is None

    def test_long_with_entry_below_stop_returns_none(self):
        # Entry 99 but signal low 100 → stop ~100, which is ABOVE entry.
        # Long trade with stop above entry would invert risk — reject.
        plan = strategy.plan_entry(
            alert=self._alert("short_squeeze"),
            signal_candle=_candle(h=105, l=100, c=99),
            entry_price=Decimal("99"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert plan is None

    def test_plan_carries_alert_context(self):
        plan = strategy.plan_entry(
            alert=self._alert("short_squeeze"),
            signal_candle=_candle(h=105, l=100, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
            oracle_score=Decimal("42"),
        )
        assert plan is not None
        assert plan.vol_ratio == Decimal("5.2")
        assert plan.pct_change == Decimal("0.034")
        assert plan.funding_pct == Decimal("-0.0015")
        assert plan.oracle_score == Decimal("42")


class TestParseCandle:
    def test_short_keys(self):
        h, l, c = strategy.parse_candle({"h": 105, "l": 100, "c": 103})
        assert (h, l, c) == (Decimal("105"), Decimal("100"), Decimal("103"))

    def test_long_keys_fallback(self):
        h, l, c = strategy.parse_candle({"high": 105, "low": 100, "close": 103})
        assert (h, l, c) == (Decimal("105"), Decimal("100"), Decimal("103"))


# ---------- vetoes ----------


class TestExchangeSupport:
    def test_supports_only_market_data_source(self, monkeypatch):
        from app.modules.bot import vetoes
        from app.config import settings

        monkeypatch.setattr(settings, "market_data_source", "bybit")
        assert vetoes.supports_exchange("bybit") is True
        assert vetoes.supports_exchange("BYBIT") is True
        assert vetoes.supports_exchange("binance") is False

    def test_supports_nothing_when_source_is_unset(self, monkeypatch):
        from app.modules.bot import vetoes
        from app.config import settings

        monkeypatch.setattr(settings, "market_data_source", "")
        assert vetoes.supports_exchange("bybit") is False
        assert vetoes.supports_exchange("binance") is False
