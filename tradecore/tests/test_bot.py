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
    def test_supports_bybit_and_binance(self):
        from app.modules.bot import vetoes

        assert vetoes.supports_exchange("bybit") is True
        assert vetoes.supports_exchange("BYBIT") is True
        assert vetoes.supports_exchange("binance") is True
        assert vetoes.supports_exchange("BINANCE") is True

    def test_rejects_unknown_exchange(self):
        from app.modules.bot import vetoes

        assert vetoes.supports_exchange("okx") is False
        assert vetoes.supports_exchange("") is False


# ---------- asset selection filters ----------


class TestPerpOnly:
    def test_off_allows_everything(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_perp_only", False)
        assert vetoes.market_allowed("spot") is True
        assert vetoes.market_allowed(None) is True
        assert vetoes.market_allowed("perp") is True

    def test_on_allows_only_perp(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_perp_only", True)
        assert vetoes.market_allowed("perp") is True
        assert vetoes.market_allowed("spot") is False
        assert vetoes.market_allowed(None) is False  # unknown treated as non-perp


class TestSymbolBlocklist:
    def test_parsing_is_case_insensitive_and_trimmed(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_symbol_blocklist", "ESUSDT, esportsusdt , SIRENUSDT")
        assert vetoes.blocklist() == {"ESUSDT", "ESPORTSUSDT", "SIRENUSDT"}
        assert vetoes.is_blocked("esusdt") is True
        assert vetoes.is_blocked("ESPORTSUSDT") is True
        assert vetoes.is_blocked("BTCUSDT") is False

    def test_empty_blocklist_blocks_nothing(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_symbol_blocklist", "")
        assert vetoes.blocklist() == set()
        assert vetoes.is_blocked("ESUSDT") is False


class TestTurnover:
    def test_prefers_quote_volume_field(self):
        from app.modules.bot import candle_source

        candles = [{"q": 100.0}, {"q": 250.5}]
        assert candle_source.turnover_from_candles(candles) == 350.5

    def test_falls_back_to_volume_times_close(self):
        from app.modules.bot import candle_source

        candles = [{"v": 10, "c": 2.0}, {"v": 5, "c": 4.0}]  # 20 + 20
        assert candle_source.turnover_from_candles(candles) == 40.0


# ---------- risk-normalized sizing ----------


class TestComputeNotional:
    def test_falls_back_to_cap_when_risk_pct_none(self):
        n = strategy.compute_notional(
            paper_equity=Decimal("10000"), entry_price=Decimal("100"),
            stop_price=Decimal("95"), max_notional_pct=Decimal("0.05"),
            risk_per_trade_pct=None,
        )
        assert n == Decimal("500")  # 5% × 10000 (legacy fixed-notional behaviour)

    def test_falls_back_to_cap_when_risk_pct_zero(self):
        n = strategy.compute_notional(
            paper_equity=Decimal("10000"), entry_price=Decimal("100"),
            stop_price=Decimal("95"), max_notional_pct=Decimal("0.05"),
            risk_per_trade_pct=Decimal("0"),
        )
        assert n == Decimal("500")

    def test_risk_normalized_below_cap(self):
        # stop 10% away; risk 0.25% of 10k = $25 → notional 25/0.10 = $250 (< $500 cap)
        n = strategy.compute_notional(
            paper_equity=Decimal("10000"), entry_price=Decimal("100"),
            stop_price=Decimal("90"), max_notional_pct=Decimal("0.05"),
            risk_per_trade_pct=Decimal("0.0025"),
        )
        assert n == Decimal("250")

    def test_cap_binds_for_tight_stop(self):
        # stop 1% away → would need $2500 notional for $25 risk; cap holds it at $500
        n = strategy.compute_notional(
            paper_equity=Decimal("10000"), entry_price=Decimal("100"),
            stop_price=Decimal("99"), max_notional_pct=Decimal("0.05"),
            risk_per_trade_pct=Decimal("0.0025"),
        )
        assert n == Decimal("500")

    def test_dollar_risk_constant_across_stop_widths(self):
        # The whole fix: $-risk at the stop is constant regardless of stop width
        # (until the cap binds). Cap set high so it never binds here.
        for stop in (Decimal("90"), Decimal("80"), Decimal("75")):
            n = strategy.compute_notional(
                paper_equity=Decimal("10000"), entry_price=Decimal("100"),
                stop_price=stop, max_notional_pct=Decimal("0.50"),
                risk_per_trade_pct=Decimal("0.0025"),
            )
            stop_dist = (Decimal("100") - stop) / Decimal("100")
            assert abs(n * stop_dist - Decimal("25")) < Decimal("0.0001")


class TestPlanEntryRiskSizing:
    def _alert(self, direction="short_squeeze") -> dict:
        return {
            "type": "wave_active", "symbol": "BTCUSDT", "exchange": "bybit",
            "base_asset": "BTC", "direction": direction,
            "detected_at": datetime(2026, 6, 9, 11, 30, tzinfo=timezone.utc).isoformat(),
        }

    def test_wide_stop_shrinks_notional_and_fixes_dollar_risk(self):
        # Wide signal candle (l=80) → wide stop → notional well below the $500 cap,
        # with dollar-risk pinned to ~$25.
        plan = strategy.plan_entry(
            alert=self._alert("short_squeeze"),
            signal_candle=_candle(h=105, l=80, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
            risk_per_trade_pct=Decimal("0.0025"),
        )
        assert plan is not None
        assert plan.notional_usd < Decimal("500")
        stop_dist = abs(Decimal("104") - plan.stop_price) / Decimal("104")
        assert abs(plan.notional_usd * stop_dist - Decimal("25")) < Decimal("0.01")

    def test_default_still_fixed_notional(self):
        # No risk_per_trade_pct → unchanged legacy sizing at the cap.
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
        assert plan.notional_usd == Decimal("500")


# ---------- exit costs: slippage + fees ----------


class TestSlippage:
    def test_long_exit_slips_down(self):
        p = strategy.adverse_slippage_price(Direction.LONG, Decimal("100"), Decimal("0.0005"))
        assert p == Decimal("100") * Decimal("0.9995")

    def test_short_exit_slips_up(self):
        p = strategy.adverse_slippage_price(Direction.SHORT, Decimal("100"), Decimal("0.0005"))
        assert p == Decimal("100") * Decimal("1.0005")

    def test_zero_slippage_is_noop(self):
        assert strategy.adverse_slippage_price(Direction.LONG, Decimal("100"), Decimal("0")) == Decimal("100")


class TestFees:
    def test_round_trip_charges_both_legs(self):
        f = strategy.round_trip_fee(Decimal("500"), Decimal("520"), Decimal("0.0006"))
        assert f == Decimal("1020") * Decimal("0.0006")

    def test_zero_fee_is_zero(self):
        assert strategy.round_trip_fee(Decimal("500"), Decimal("520"), Decimal("0")) == Decimal("0")


# ---------- funding accrual ----------


class TestFundingPnl:
    def test_long_pays_positive_funding(self):
        pnl = strategy.estimated_funding_pnl(
            Direction.LONG, Decimal("1000"), Decimal("0.001"), hold_hours=24, interval_hours=8
        )
        assert pnl == Decimal("-3")  # 3 intervals × 0.1% × $1000, long pays

    def test_short_receives_positive_funding(self):
        pnl = strategy.estimated_funding_pnl(
            Direction.SHORT, Decimal("1000"), Decimal("0.001"), hold_hours=24, interval_hours=8
        )
        assert pnl == Decimal("3")

    def test_long_receives_negative_funding(self):
        pnl = strategy.estimated_funding_pnl(
            Direction.LONG, Decimal("1000"), Decimal("-0.002"), hold_hours=8, interval_hours=8
        )
        assert pnl == Decimal("2")

    def test_hold_shorter_than_interval_accrues_nothing(self):
        pnl = strategy.estimated_funding_pnl(
            Direction.SHORT, Decimal("1000"), Decimal("0.001"), hold_hours=7.9, interval_hours=8
        )
        assert pnl == Decimal("0")

    def test_zero_interval_disables_accrual(self):
        pnl = strategy.estimated_funding_pnl(
            Direction.SHORT, Decimal("1000"), Decimal("0.001"), hold_hours=24, interval_hours=0
        )
        assert pnl == Decimal("0")

    def test_none_funding_is_zero(self):
        pnl = strategy.estimated_funding_pnl(
            Direction.LONG, Decimal("1000"), None, hold_hours=24, interval_hours=8
        )
        assert pnl == Decimal("0")


# ---------- net R ----------


class TestNetR:
    def test_net_r_is_pnl_over_dollar_risk(self):
        # entry 100, stop 95 → $5 risk/unit; qty 10 → $50 at risk. $95 net pnl = 1.9R.
        r = strategy.net_r_multiple(Decimal("95"), Decimal("100"), Decimal("95"), Decimal("10"))
        assert r == Decimal("1.9")

    def test_net_r_below_gross_r_when_costs_positive(self):
        entry, stop, qty = Decimal("100"), Decimal("95"), Decimal("10")
        exit_price = Decimal("110")  # clean 2R winner on price
        gross_r = strategy.realized_r_multiple(Direction.LONG, entry, stop, exit_price)
        gross_pnl = strategy.realized_pnl(Direction.LONG, entry, exit_price, qty)
        fees = strategy.round_trip_fee(entry * qty, exit_price * qty, Decimal("0.0006"))
        net_r = strategy.net_r_multiple(gross_pnl - fees, entry, stop, qty)
        assert gross_r == Decimal("2")
        assert net_r < gross_r

    def test_zero_risk_returns_zero(self):
        r = strategy.net_r_multiple(Decimal("10"), Decimal("100"), Decimal("100"), Decimal("10"))
        assert r == Decimal("0")


# ---------- per-direction gates ----------


class TestDirectionEnabled:
    def test_defaults_allow_both(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_long_enabled", True)
        monkeypatch.setattr(settings, "bot_short_enabled", True)
        assert vetoes.direction_enabled(Direction.LONG) is True
        assert vetoes.direction_enabled(Direction.SHORT) is True

    def test_long_gate_blocks_only_longs(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_long_enabled", False)
        monkeypatch.setattr(settings, "bot_short_enabled", True)
        assert vetoes.direction_enabled(Direction.LONG) is False
        assert vetoes.direction_enabled(Direction.SHORT) is True

    def test_short_gate_blocks_only_shorts(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_long_enabled", True)
        monkeypatch.setattr(settings, "bot_short_enabled", False)
        assert vetoes.direction_enabled(Direction.LONG) is True
        assert vetoes.direction_enabled(Direction.SHORT) is False


# ---------- liquidity-aware notional cap ----------


class TestEffectiveNotionalCap:
    def test_disabled_returns_base_cap(self):
        cap = strategy.effective_notional_cap_pct(
            Decimal("0.05"), Decimal("10000"), Decimal("50000"), Decimal("0")
        )
        assert cap == Decimal("0.05")

    def test_unknown_turnover_returns_base_cap(self):
        cap = strategy.effective_notional_cap_pct(
            Decimal("0.05"), Decimal("10000"), None, Decimal("0.005")
        )
        assert cap == Decimal("0.05")

    def test_thin_book_shrinks_cap(self):
        # 0.5% of $50k turnover = $250 → 2.5% of $10k equity < 5% base cap.
        cap = strategy.effective_notional_cap_pct(
            Decimal("0.05"), Decimal("10000"), Decimal("50000"), Decimal("0.005")
        )
        assert cap == Decimal("0.025")

    def test_deep_book_keeps_base_cap(self):
        # 0.5% of $10M turnover = $50k → far above the 5% ($500) base cap.
        cap = strategy.effective_notional_cap_pct(
            Decimal("0.05"), Decimal("10000"), Decimal("10000000"), Decimal("0.005")
        )
        assert cap == Decimal("0.05")


# ---------- entry-quality filters (2026-07-10 calibration) ----------


class TestEntryQualityFilters:
    def test_vol_ratio_gate(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_min_vol_ratio", 15.0)
        assert vetoes.vol_ratio_ok(20.0) is True
        assert vetoes.vol_ratio_ok(14.9) is False
        assert vetoes.vol_ratio_ok(None) is True  # unknown → allow
        monkeypatch.setattr(settings, "bot_min_vol_ratio", 0.0)
        assert vetoes.vol_ratio_ok(1.0) is True

    def test_funding_veto_is_two_sided(self, monkeypatch):
        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_max_abs_funding", 0.002)
        assert vetoes.funding_ok(0.001) is True
        assert vetoes.funding_ok(0.002) is False
        assert vetoes.funding_ok(-0.003) is False
        assert vetoes.funding_ok(None) is True
        monkeypatch.setattr(settings, "bot_max_abs_funding", 0.0)
        assert vetoes.funding_ok(0.01) is True

    def test_blocked_hours_parsing_and_gate(self, monkeypatch):
        from datetime import datetime, timezone

        from app.config import settings
        from app.modules.bot import vetoes

        monkeypatch.setattr(settings, "bot_blocked_hours_utc", "0, 1,2,25,junk")
        assert vetoes.blocked_hours() == {0, 1, 2}
        assert vetoes.hour_ok(datetime(2026, 7, 10, 1, 30, tzinfo=timezone.utc)) is False
        assert vetoes.hour_ok(datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)) is True
        monkeypatch.setattr(settings, "bot_blocked_hours_utc", "")
        assert vetoes.hour_ok(datetime(2026, 7, 10, 1, 30, tzinfo=timezone.utc)) is True
