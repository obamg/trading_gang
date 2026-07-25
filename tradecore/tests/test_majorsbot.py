"""MajorsBot tests.

Pure-function coverage for strategies.py + data.py rolling helpers (trigger
detection, retrace/stop/floor math, partial+trail transitions, funding
percentile + band exit, funding-accrual sign), plus engine orchestration with
the fake Redis and a stubbed data layer (pending fill/cancel paths,
once-per-bar entry gating, concurrent cap, fundingfade event gating).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.majorsbot import data, strategies
from app.modules.majorsbot.strategies import PositionState

H1 = 3_600_000


def _bar(t, o, h, l, c, v=1000.0):
    return {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}


def _quiet_bars(n, *, t0=0, px=100.0, spread=0.5, vol=1000.0):
    """n flat bars: o=c=px, range ±spread → TR = 2×spread, TR% = 2×spread/px."""
    return [
        _bar(t0 + k * H1, px, px + spread, px - spread, px, vol) for k in range(n)
    ]


def _dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ---------- rolling helpers (bake-off parity) ----------


class TestRollingHelpers:
    def test_true_ranges_use_prev_close_gaps(self):
        bars = [_bar(0, 100, 101, 99, 100), _bar(H1, 105, 106, 104.5, 105)]
        trs = data.true_ranges(bars)
        assert trs[0] == pytest.approx(2.0)
        # gap: max(106−104.5, |106−100|, |104.5−100|) = 6
        assert trs[1] == pytest.approx(6.0)

    def test_atr_at_is_inclusive_window_mean(self):
        bars = _quiet_bars(30)  # TR = 1.0 everywhere
        assert data.atr_at(bars, 29, 24) == pytest.approx(1.0)

    def test_atr_at_needs_enough_history(self):
        assert data.atr_at(_quiet_bars(10), 9, 24) is None

    def test_mean_tr_pct_window_ends_before_idx(self):
        bars = _quiet_bars(11)
        # Blow up the LAST bar's TR — it must not contaminate its own signal.
        bars[-1]["h"] = 200.0
        assert data.mean_tr_pct(bars, 10, 10) == pytest.approx(0.01)

    def test_mean_tr_pct_needs_enough_history(self):
        assert data.mean_tr_pct(_quiet_bars(10), 9, 10) is None

    def test_median_volume_prior_window(self):
        bars = _quiet_bars(11)
        bars[-1]["v"] = 99999.0  # own volume excluded
        assert data.median_volume(bars, 10, 10) == pytest.approx(1000.0)


# ---------- volevent trigger detection ----------


def _trigger_setup(*, ret_ok=True, vol_ok=True, up=True):
    n = strategies.VOLEVENT_LOOKBACK_BARS + 2
    bars = _quiet_bars(n)  # mean TR% = 0.01 → threshold |ret| ≥ 0.03
    t = bars[-1]["t"]
    move = 0.04 if ret_ok else 0.02
    if up:
        bars[-1] = _bar(t, 100, 104.5, 99.8, 100 * (1 + move), 5000 if vol_ok else 2000)
    else:
        bars[-1] = _bar(t, 100, 100.2, 95.5, 100 * (1 - move), 5000 if vol_ok else 2000)
    return bars


class TestVoleventSignal:
    def test_long_trigger_fires_with_move_and_volume(self):
        sig = strategies.volevent_signal(_trigger_setup())
        assert sig is not None
        assert sig["direction"] == "long"
        assert sig["limit_price"] == pytest.approx((104.5 + 99.8) / 2)
        assert sig["trigger_low"] == pytest.approx(99.8)

    def test_short_trigger_goes_with_the_down_move(self):
        sig = strategies.volevent_signal(_trigger_setup(up=False))
        assert sig is not None
        assert sig["direction"] == "short"

    def test_no_signal_below_return_threshold(self):
        assert strategies.volevent_signal(_trigger_setup(ret_ok=False)) is None

    def test_no_signal_below_volume_multiple(self):
        assert strategies.volevent_signal(_trigger_setup(vol_ok=False)) is None

    def test_no_signal_with_short_history(self):
        bars = _trigger_setup()[-500:]
        assert strategies.volevent_signal(bars) is None

    def test_no_signal_on_zero_median_volume(self):
        bars = _trigger_setup()
        for b in bars[:-1]:
            b["v"] = 0.0
        assert strategies.volevent_signal(bars) is None


class TestVoleventEntryMath:
    def test_limit_fill_gap_bonus_long(self):
        # Bar opens below the limit → filled at the (better) open.
        assert strategies.limit_fill_price("long", Decimal("101"), Decimal("102")) == Decimal("101")
        assert strategies.limit_fill_price("long", Decimal("103"), Decimal("102")) == Decimal("102")

    def test_limit_fill_gap_bonus_short(self):
        assert strategies.limit_fill_price("short", Decimal("103"), Decimal("102")) == Decimal("103")
        assert strategies.limit_fill_price("short", Decimal("101"), Decimal("102")) == Decimal("102")

    def test_stop_at_trigger_extreme_when_wide_enough(self):
        stop, risk = strategies.volevent_stop("long", Decimal("102"), Decimal("99.8"))
        assert risk == Decimal("2.2")
        assert stop == Decimal("99.8")

    def test_one_percent_floor_widens_narrow_stops(self):
        stop, risk = strategies.volevent_stop("long", Decimal("100"), Decimal("99.5"))
        assert risk == Decimal("1.00")  # 1% of 100 beats the 0.5 raw distance
        assert stop == Decimal("99.00")
        stop_s, risk_s = strategies.volevent_stop("short", Decimal("100"), Decimal("100.4"))
        assert risk_s == Decimal("1.00")
        assert stop_s == Decimal("101.00")

    def test_take_profit_is_r_multiple_from_entry(self):
        tp = strategies.take_profit_price("long", Decimal("100"), Decimal("2"), Decimal("1.5"))
        assert tp == Decimal("103")
        tp_s = strategies.take_profit_price("short", Decimal("100"), Decimal("2"), Decimal("1.5"))
        assert tp_s == Decimal("97")


# ---------- funding percentile + fundingfade math ----------


class TestFundingPercentile:
    def test_percentile_counts_at_or_below_current(self):
        window = [0.0001] * 89 + [0.001]
        assert strategies.funding_percentile(window) == pytest.approx(1.0)
        window_min = [0.0003] * 89 + [-0.001]
        assert strategies.funding_percentile(window_min) == pytest.approx(1 / 90)

    def test_direction_at_extremes_only(self):
        assert strategies.fundingfade_direction(0.99) == "short"
        assert strategies.fundingfade_direction(1.0) == "short"
        assert strategies.fundingfade_direction(0.01) == "long"
        assert strategies.fundingfade_direction(0.5) is None
        assert strategies.fundingfade_direction(0.98) is None

    def test_normal_band_bounds_inclusive(self):
        assert strategies.in_normal_band(0.40)
        assert strategies.in_normal_band(0.60)
        assert not strategies.in_normal_band(0.39)
        assert not strategies.in_normal_band(0.61)

    def test_stop_is_1_5_atr(self):
        stop, risk = strategies.fundingfade_stop("short", Decimal("100"), Decimal("2"))
        assert risk == Decimal("3")
        assert stop == Decimal("103")
        stop_l, _ = strategies.fundingfade_stop("long", Decimal("100"), Decimal("2"))
        assert stop_l == Decimal("97")

    def test_trail_distance_is_2_atr_for_fundingfade(self):
        # risk = 1.5×ATR → distance = 2×ATR = risk × 2/1.5
        risk = Decimal("3")  # ATR 2
        assert strategies.trail_distance_for(strategies.FUNDINGFADE, risk) == Decimal("4")
        assert strategies.trail_distance_for(strategies.VOLEVENT, risk) == Decimal("3")


# ---------- per-bar walk (partial + trail transitions) ----------


def _step(state, o, h, l, *, direction="long", entry="100", stop="95",
          is_entry_bar=False, tp="107.5", dist="5"):
    return strategies.step_position_bar(
        direction,
        Decimal(entry),
        Decimal(stop),
        state,
        Decimal(str(o)),
        Decimal(str(h)),
        Decimal(str(l)),
        is_entry_bar=is_entry_bar,
        tp_price=Decimal(tp) if tp is not None else None,
        trail_arm_r=Decimal("1.0"),
        trail_distance=Decimal(dist) if dist is not None else None,
    )


class TestStepPositionBar:
    def _fresh(self):
        return PositionState(stop=Decimal("95"), peak=None, partial_taken=False)

    def test_entry_bar_stop_fills_exactly(self):
        _, events = _step(self._fresh(), 100, 101, 94, is_entry_bar=True)
        assert events == [("close", Decimal("95"), "stop")]

    def test_later_bar_stop_takes_gap_penalty(self):
        _, events = _step(self._fresh(), 93, 94, 90)
        assert events == [("close", Decimal("93"), "stop")]

    def test_stop_wins_tie_with_tp(self):
        _, events = _step(self._fresh(), 100, 108, 94)
        assert events[0][0] == "close"
        assert len(events) == 1

    def test_partial_fills_flat_at_tp(self):
        state, events = _step(self._fresh(), 100, 107.6, 99)
        assert ("partial", Decimal("107.5")) in events
        assert state.partial_taken is True
        # Trail armed too: peak 107.6, stop ratchets to 102.6.
        assert state.peak == Decimal("107.6")
        assert state.stop == Decimal("102.6")
        # No second partial on a later bar.
        _, events2 = _step(state, 104, 108, 103, stop="102.6")
        assert events2 == []

    def test_trail_arms_but_only_stops_on_a_later_bar(self):
        # Bar arms the trail (fav +1R) and its own low is under the NEW trail
        # level — but the pre-bar stop stands this bar (no close).
        state, events = _step(self._fresh(), 100, 105, 96)
        assert events == []
        assert state.stop == Decimal("100")  # peak 105 − 1R
        # Next bar breaches the trailed stop → close, gap-aware, reason trail.
        _, events2 = _step(state, 99, 101, 98)
        assert events2 == [("close", Decimal("99"), "trail")]

    def test_ratchet_never_loosens(self):
        state, _ = _step(self._fresh(), 100, 106, 100)  # peak 106 → stop 101
        assert state.stop == Decimal("101")
        state2, _ = _step(state, 104, 104.5, 101.5)  # lower high — peak keeps 106
        assert state2.peak == Decimal("106")
        assert state2.stop == Decimal("101")

    def test_short_direction_mirrors(self):
        state = PositionState(stop=Decimal("105"), peak=None, partial_taken=False)
        state, events = _step(
            state, 100, 104, 95, direction="short", stop="105", tp="92.5"
        )
        assert events == []
        assert state.peak == Decimal("95")   # fav low, +1R
        assert state.stop == Decimal("100")  # 95 + 1R
        _, events2 = _step(state, 101, 102, 99, direction="short", stop="105", tp="92.5")
        assert events2 == [("close", Decimal("101"), "trail")]

    def test_no_trail_when_distance_none(self):
        state, _ = _step(self._fresh(), 100, 106, 100, dist=None, tp=None)
        assert state.stop == Decimal("95")
        assert state.peak is None


# ---------- costs, funding sign, sizing ----------


class TestCostsAndSizing:
    def test_adverse_slippage_direction(self):
        assert strategies.adverse_slippage_price(
            "long", Decimal("100"), Decimal("0.0002")
        ) == Decimal("99.98")
        assert strategies.adverse_slippage_price(
            "short", Decimal("100"), Decimal("0.0002")
        ) == Decimal("100.02")

    def test_funding_sign_long_pays_positive_rate(self):
        pnl = strategies.funding_event_pnl("long", Decimal("0.0001"), Decimal("100"), Decimal("2"))
        assert pnl == Decimal("-0.02")

    def test_funding_sign_short_receives_positive_rate(self):
        pnl = strategies.funding_event_pnl("short", Decimal("0.0001"), Decimal("100"), Decimal("2"))
        assert pnl == Decimal("0.02")

    def test_funding_sign_long_receives_negative_rate(self):
        pnl = strategies.funding_event_pnl("long", Decimal("-0.0001"), Decimal("100"), Decimal("2"))
        assert pnl == Decimal("0.02")

    def test_qty_risk_normalized_then_notional_capped(self):
        qty = strategies.compute_qty(
            paper_equity=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.0025"),
            entry_price=Decimal("100"),
            stop_price=Decimal("98"),
            max_notional_pct=Decimal("0.05"),
        )
        # risk qty 25/2 = 12.5 → notional 1250 > 500 cap → capped to 5.
        assert qty == Decimal("5")

    def test_qty_uses_risk_when_under_cap(self):
        qty = strategies.compute_qty(
            paper_equity=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.0025"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            max_notional_pct=Decimal("0.05"),
        )
        assert qty == Decimal("2.5")

    def test_qty_zero_on_degenerate_stop(self):
        assert strategies.compute_qty(
            paper_equity=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.0025"),
            entry_price=Decimal("100"),
            stop_price=Decimal("100"),
            max_notional_pct=Decimal("0.05"),
        ) == Decimal("0")

    def test_net_r_is_net_pnl_over_initial_risk(self):
        r = strategies.net_r_multiple(
            Decimal("-25"), Decimal("100"), Decimal("95"), Decimal("5")
        )
        assert r == Decimal("-1")


# ---------- executor transitions (fake Redis + in-memory DB) ----------


class FakeDB:
    def __init__(self) -> None:
        self.commits = 0
        self.added = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj) -> None:
        pass


def _make_trade(**overrides):
    from app.models.majorsbot import MajorsBotTrade

    now = datetime.now(timezone.utc)
    defaults = dict(
        symbol="BTCUSDT",
        exchange="bybit",
        market_type="perp",
        direction="long",
        strategy="volevent",
        signal_at=now - timedelta(hours=2),
        entry_price=Decimal("100"),
        entry_at=now - timedelta(hours=1),
        entry_bar_at=now - timedelta(hours=1),
        entry_mode="limit",
        limit_price=Decimal("100"),
        expire_at=now + timedelta(hours=5),
        signal_high=Decimal("105"),
        signal_low=Decimal("99"),
        notional_usd=Decimal("500"),
        qty=Decimal("5"),
        paper_equity_at_entry=Decimal("10000"),
        stop_price=Decimal("95"),
        initial_stop_price=Decimal("95"),
        take_profit_price=Decimal("107.5"),
        status="open",
    )
    defaults.update(overrides)
    return MajorsBotTrade(**defaults)


def _published(fake_redis, type_):
    return [
        json.loads(msg)
        for ch, msg in fake_redis.published()
        if ch == "alerts:majorsbot" and json.loads(msg)["type"] == type_
    ]


@pytest.mark.asyncio
async def test_fill_pending_recomputes_at_fill_and_bumps_counter(fake_redis):
    from app.modules.majorsbot import executor

    db = FakeDB()
    trade = _make_trade(status="pending")
    bar_at = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    await executor.fill_pending_order(
        db,
        trade,
        fill_price=Decimal("99.5"),
        stop_price=Decimal("97"),
        take_profit_price=Decimal("103.25"),
        qty=Decimal("4"),
        entry_bar_at=bar_at,
        paper_equity=Decimal("10000"),
    )
    assert trade.status == "open"
    assert trade.entry_price == Decimal("99.5")
    assert trade.stop_price == Decimal("97")
    assert trade.initial_stop_price == Decimal("97")
    assert trade.qty == Decimal("4")
    assert trade.notional_usd == Decimal("398.0")
    assert trade.entry_bar_at == bar_at
    assert await fake_redis.get("majorsbot:concurrent_count") == "1"
    assert _published(fake_redis, "trade_opened")


@pytest.mark.asyncio
async def test_cancel_pending_records_reason_without_counter_bump(fake_redis):
    from app.modules.majorsbot import executor

    db = FakeDB()
    trade = _make_trade(status="pending")
    await executor.cancel_pending_order(db, trade)
    assert trade.status == "cancelled"
    assert trade.close_reason == "expired"
    assert trade.realized_pnl_usd is None
    assert await fake_redis.get("majorsbot:concurrent_count") is None
    assert _published(fake_redis, "order_cancelled")


@pytest.mark.asyncio
async def test_partial_leg_realizes_pnl_with_maker_fees(fake_redis, monkeypatch):
    from app.config import settings
    from app.modules.majorsbot import executor

    monkeypatch.setattr(settings, "majorsbot_maker_fee_pct", 0.0)
    await fake_redis.set("majorsbot:equity:paper", "10000")
    db = FakeDB()
    trade = _make_trade(qty=Decimal("10"))
    bar_close = datetime(2026, 7, 1, 13, tzinfo=timezone.utc)
    await executor.take_partial_profit(
        db, trade, exit_price=Decimal("107.5"), bar_close_at=bar_close
    )
    assert trade.partial_qty == Decimal("5")
    assert trade.partial_pnl_usd == Decimal("37.5")
    assert trade.partial_exit_at == bar_close  # BAR close, not wall clock
    assert trade.qty == Decimal("10")
    assert Decimal(await fake_redis.get("majorsbot:equity:paper")) == Decimal("10037.5")
    # Idempotent — a re-walked bar can't double-take.
    await executor.take_partial_profit(
        db, trade, exit_price=Decimal("107.5"), bar_close_at=bar_close
    )
    assert trade.partial_pnl_usd == Decimal("37.5")


@pytest.mark.asyncio
async def test_close_after_partial_totals_r_net_and_equity(fake_redis, monkeypatch):
    from app.config import settings
    from app.modules.majorsbot import executor

    monkeypatch.setattr(settings, "majorsbot_maker_fee_pct", 0.0)
    monkeypatch.setattr(settings, "majorsbot_taker_fee_pct", 0.0)
    monkeypatch.setattr(settings, "majorsbot_slippage_pct", 0.0)
    await fake_redis.set("majorsbot:equity:paper", "10000")
    await fake_redis.set("majorsbot:concurrent_count", "1")

    db = FakeDB()
    trade = _make_trade(
        qty=Decimal("10"),
        stop_price=Decimal("101"),
        peak_price=Decimal("108"),
        partial_exit_price=Decimal("107.5"),
        partial_exit_at=datetime.now(timezone.utc),
        partial_qty=Decimal("5"),
        partial_pnl_usd=Decimal("37.5"),
    )
    await executor.close_trade(
        db, trade, raw_exit_price=Decimal("101"), reason="trail"
    )
    assert trade.status == "closed"
    assert trade.close_reason == "trail"
    # Runner (101−100)×5 = +5; totals 42.5; risk = 5×10 = 50.
    assert trade.realized_pnl_usd == Decimal("42.5")
    assert trade.realized_r == Decimal("0.85")
    assert trade.realized_r_net == Decimal("0.85")
    assert Decimal(await fake_redis.get("majorsbot:equity:paper")) == Decimal("10005")
    assert await fake_redis.get("majorsbot:concurrent_count") == "0"
    assert _published(fake_redis, "trade_closed")


@pytest.mark.asyncio
async def test_close_applies_slippage_taker_fee_and_funding(fake_redis, monkeypatch):
    from app.config import settings
    from app.modules.majorsbot import executor

    monkeypatch.setattr(settings, "majorsbot_taker_fee_pct", 0.0006)
    monkeypatch.setattr(settings, "majorsbot_slippage_pct", 0.0002)
    await fake_redis.set("majorsbot:equity:paper", "10000")
    await fake_redis.set("majorsbot:concurrent_count", "1")

    db = FakeDB()
    # fundingfade short: entry taker; stop at 103 → raw exit 103, slip worsens.
    trade = _make_trade(
        strategy="fundingfade",
        direction="short",
        entry_mode="market",
        stop_price=Decimal("103"),
        initial_stop_price=Decimal("103"),
        take_profit_price=None,
        qty=Decimal("1"),
    )
    funding = Decimal("0.05")  # short collected one positive event
    await executor.close_trade(
        db, trade, raw_exit_price=Decimal("103"), reason="stop", funding_pnl=funding
    )
    fill = Decimal("103") * Decimal("1.0002")
    gross = Decimal("100") - fill
    fees = Decimal("100") * Decimal("0.0006") + fill * Decimal("0.0006")
    expected = gross - fees + funding
    assert trade.close_price == fill
    assert trade.funding_pnl_usd == funding
    assert Decimal(str(trade.realized_pnl_usd)) == expected.quantize(Decimal("0.01")) or abs(
        Decimal(str(trade.realized_pnl_usd)) - expected
    ) < Decimal("0.01")
    assert Decimal(await fake_redis.get("majorsbot:equity:paper")) == Decimal("10000") + expected


# ---------- engine funding accrual ----------


class TestAccruedFunding:
    def _md(self, bars, funding):
        return data.MarketData(symbol="BTCUSDT", bars=bars, funding=funding)

    def test_events_split_full_then_runner_qty(self):
        from app.modules.majorsbot import engine

        t0 = 1_000 * H1
        bars = [_bar(t0 + k * H1, 100, 100.5, 99.5, 100) for k in range(20)]
        funding = [
            (t0, 0.0001),            # at entry bar — excluded (ts ≤ entry)
            (t0 + 8 * H1, 0.0001),   # before/at partial bar — full qty
            (t0 + 16 * H1, 0.0001),  # after partial — runner qty
        ]
        trade = _make_trade(
            direction="long",
            qty=Decimal("2"),
            partial_qty=Decimal("1"),
            entry_bar_at=_dt(t0),
            partial_exit_at=_dt(t0 + 9 * H1),  # partial bar opened t0+8h
        )
        pnl = engine._accrued_funding(trade, self._md(bars, funding), exit_bar_ms=t0 + 16 * H1)
        # long pays: −(0.0001×100×2) − (0.0001×100×1) = −0.03
        assert pnl == Decimal("-0.03")

    def test_no_funding_data_accrues_zero(self):
        from app.modules.majorsbot import engine

        trade = _make_trade(entry_bar_at=_dt(0))
        md = self._md([_bar(0, 100, 101, 99, 100)], None)
        assert engine._accrued_funding(trade, md, exit_bar_ms=H1) == Decimal("0")

    def test_pctile_at_event_uses_trailing_90_inclusive(self):
        from app.modules.majorsbot import engine

        base = 500 * H1
        funding = [(base + k * 8 * H1, 0.0001) for k in range(99)]
        funding.append((base + 99 * 8 * H1, 0.001))
        pct = engine._pctile_at_event(funding, base + 99 * 8 * H1)
        assert pct == pytest.approx(1.0)
        assert engine._pctile_at_event(funding, base + 3) is None  # unknown ts
        short_hist = funding[:50]
        assert engine._pctile_at_event(short_hist, short_hist[-1][0]) is None


# ---------- engine orchestration (fake redis + stubbed data + FakeDB) ----------


def _quiet_high_bars(n, t0):
    """Bars that never touch a ~102 long limit (price parked above it)."""
    return [_bar(t0 + k * H1, 105, 106, 104.5, 105) for k in range(n)]


@pytest.fixture
def engine_env(monkeypatch, fake_redis):
    """Stub the data layer + DB seams; return a context object."""
    from app.modules.majorsbot import engine

    ctx = SimpleNamespace(md=None, trades=[], engine=engine, redis=fake_redis)

    async def fake_get_market_data(symbol):
        return ctx.md

    async def fake_get_trades(db, symbol):
        return ctx.trades

    monkeypatch.setattr(data, "get_market_data", fake_get_market_data)
    monkeypatch.setattr(engine, "_get_trades", fake_get_trades)
    return ctx


@pytest.mark.asyncio
async def test_disabled_tick_is_a_noop():
    from app.modules.majorsbot import engine

    assert await engine.run_majorsbot_tick() == {"skipped": "disabled"}


@pytest.mark.asyncio
async def test_volevent_signal_places_pending_once_per_bar(engine_env):
    engine = engine_env.engine
    bars = _trigger_setup()
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=None)
    now = _dt(bars[-1]["t"] + H1 + 5 * 60_000)

    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", now)
    assert out["placed"] == 1
    assert len(db.added) == 1
    trade = db.added[0]
    assert trade.status == "pending"
    assert trade.strategy == "volevent"
    assert trade.direction == "long"
    assert float(trade.limit_price) == pytest.approx((104.5 + 99.8) / 2)
    # Order dies 7h after the trigger bar opened (placement +6h).
    assert trade.expire_at == _dt(bars[-1]["t"] + 7 * H1)

    # Same completed bar again → gate holds, nothing new placed.
    db2 = FakeDB()
    out2 = await engine._process_symbol(db2, "BTCUSDT", now)
    assert out2["placed"] == 0
    assert db2.added == []


@pytest.mark.asyncio
async def test_concurrent_cap_blocks_new_placement(engine_env, fake_redis):
    engine = engine_env.engine
    bars = _trigger_setup()
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=None)
    await fake_redis.set("majorsbot:concurrent_count", "6")

    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(bars[-1]["t"] + H1))
    assert out["placed"] == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_pending_fills_when_bar_touches_limit(engine_env, fake_redis):
    engine = engine_env.engine
    t0 = 2_000 * H1
    limit = Decimal("102.15")
    trade = _make_trade(
        status="pending",
        signal_at=_dt(t0),
        limit_price=limit,
        entry_price=limit,
        signal_high=Decimal("104.5"),
        signal_low=Decimal("99.8"),
        expire_at=_dt(t0 + 7 * H1),
        entry_bar_at=None,
    )
    engine_env.trades = [trade]
    bars = [
        _bar(t0, 100, 104.5, 99.8, 104),        # trigger bar — cannot fill
        _bar(t0 + H1, 103, 103.5, 101.9, 103),  # touches 102.15
    ]
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=None)

    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(t0 + 2 * H1))
    assert out["filled"] == 1
    assert trade.status == "open"
    assert trade.entry_price == limit  # no gap — open was above the limit
    assert trade.entry_bar_at == _dt(t0 + H1)
    # Stop re-anchored at the fill: risk = max(102.15−99.8, 1%×102.15) = 2.35.
    assert float(trade.stop_price) == pytest.approx(99.8)
    # qty capped by notional: 10000×0.05/102.15.
    assert float(trade.qty) == pytest.approx(500 / 102.15)
    assert await fake_redis.get("majorsbot:concurrent_count") == "1"


@pytest.mark.asyncio
async def test_pending_expires_unfilled(engine_env, fake_redis):
    engine = engine_env.engine
    t0 = 2_000 * H1
    trade = _make_trade(
        status="pending",
        signal_at=_dt(t0),
        limit_price=Decimal("102.15"),
        signal_high=Decimal("104.5"),
        signal_low=Decimal("99.8"),
        expire_at=_dt(t0 + 7 * H1),
        entry_bar_at=None,
    )
    engine_env.trades = [trade]
    engine_env.md = data.MarketData(
        symbol="BTCUSDT", bars=_quiet_high_bars(9, t0), funding=None
    )
    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(t0 + 8 * H1))
    assert out["cancelled"] == 1
    assert trade.status == "cancelled"
    assert trade.close_reason == "expired"
    assert await fake_redis.get("majorsbot:concurrent_count") is None


@pytest.mark.asyncio
async def test_pending_cancelled_when_capacity_gone_at_fill(engine_env, fake_redis):
    engine = engine_env.engine
    t0 = 2_000 * H1
    trade = _make_trade(
        status="pending",
        signal_at=_dt(t0),
        limit_price=Decimal("102.15"),
        signal_high=Decimal("104.5"),
        signal_low=Decimal("99.8"),
        expire_at=_dt(t0 + 7 * H1),
        entry_bar_at=None,
    )
    engine_env.trades = [trade]
    engine_env.md = data.MarketData(
        symbol="BTCUSDT",
        bars=[_bar(t0, 100, 104.5, 99.8, 104), _bar(t0 + H1, 103, 103.5, 101.9, 103)],
        funding=None,
    )
    await fake_redis.set("majorsbot:concurrent_count", "6")
    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(t0 + 2 * H1))
    assert out["cancelled"] == 1
    assert trade.status == "cancelled"
    assert trade.close_reason == "max_concurrent"


@pytest.mark.asyncio
async def test_open_position_stops_out_through_engine(engine_env, fake_redis, monkeypatch):
    from app.config import settings

    engine = engine_env.engine
    monkeypatch.setattr(settings, "majorsbot_maker_fee_pct", 0.0)
    monkeypatch.setattr(settings, "majorsbot_taker_fee_pct", 0.0)
    monkeypatch.setattr(settings, "majorsbot_slippage_pct", 0.0)
    await fake_redis.set("majorsbot:equity:paper", "10000")
    await fake_redis.set("majorsbot:concurrent_count", "1")

    t0 = 2_000 * H1
    trade = _make_trade(
        status="open",
        qty=Decimal("1"),
        entry_bar_at=_dt(t0),
        entry_at=_dt(t0),
        stop_price=Decimal("95"),
        initial_stop_price=Decimal("95"),
    )
    engine_env.trades = [trade]
    bars = [
        _bar(t0, 100, 101, 98, 100),        # entry bar — stop untouched
        _bar(t0 + H1, 98, 99, 94.9, 95.5),  # sweeps the 95 stop
    ]
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=None)
    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(t0 + 2 * H1))
    assert out["closed"] == 1
    assert trade.status == "closed"
    assert trade.close_reason == "stop"
    assert trade.close_price == Decimal("95")
    assert trade.realized_r_net == Decimal("-1")
    assert Decimal(await fake_redis.get("majorsbot:equity:paper")) == Decimal("9995")
    assert await fake_redis.get("majorsbot:concurrent_count") == "0"


@pytest.mark.asyncio
async def test_fundingfade_enters_short_on_extreme_event(engine_env, fake_redis):
    engine = engine_env.engine
    t0 = 3_000 * H1
    bars = [_bar(t0 + k * H1, 100, 100.5, 99.5, 100) for k in range(30)]
    event_ts = bars[-1]["t"]  # funding event at the latest completed bar
    funding = [(event_ts - (95 - k) * 8 * H1, 0.0001) for k in range(95)]
    funding.append((event_ts, 0.001))  # max of window → pct 1.0 → short
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=funding)

    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(event_ts + H1 + 5 * 60_000))
    assert out["opened"] == 1
    trade = db.added[0]
    assert trade.strategy == "fundingfade"
    assert trade.direction == "short"
    assert trade.entry_price == Decimal("100")  # event-bar open
    # ATR(24) of quiet bars = 1 → stop = 100 + 1.5.
    assert float(trade.stop_price) == pytest.approx(101.5)
    assert trade.entry_bar_at == _dt(event_ts)
    assert float(trade.funding_rate_at_entry) == pytest.approx(0.001)
    assert await fake_redis.get("majorsbot:concurrent_count") == "1"


@pytest.mark.asyncio
async def test_fundingfade_event_not_reentered_on_next_bar(engine_env):
    engine = engine_env.engine
    t0 = 3_000 * H1
    bars = [_bar(t0 + k * H1, 100, 100.5, 99.5, 100) for k in range(30)]
    event_ts = bars[-1]["t"]
    funding = [(event_ts - (95 - k) * 8 * H1, 0.0001) for k in range(95)]
    funding.append((event_ts, 0.001))
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=funding)

    db = FakeDB()
    await engine._process_symbol(db, "BTCUSDT", _dt(event_ts + H1))
    assert len(db.added) == 1
    # A NEW completed bar arrives (gate reopens) but the event was seen.
    bars2 = bars + [_bar(event_ts + H1, 100, 100.5, 99.5, 100)]
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars2, funding=funding)
    db2 = FakeDB()
    out = await engine._process_symbol(db2, "BTCUSDT", _dt(event_ts + 2 * H1))
    assert out["opened"] == 0
    assert db2.added == []


@pytest.mark.asyncio
async def test_fundingfade_stale_event_skipped(engine_env):
    engine = engine_env.engine
    t0 = 3_000 * H1
    bars = [_bar(t0 + k * H1, 100, 100.5, 99.5, 100) for k in range(30)]
    event_ts = bars[-1]["t"]
    funding = [(event_ts - (95 - k) * 8 * H1, 0.0001) for k in range(95)]
    funding.append((event_ts, 0.001))
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=funding)
    db = FakeDB()
    # Discovered 3h late — bake-off would have entered at the event bar open;
    # entering that far behind is disallowed.
    out = await engine._process_symbol(db, "BTCUSDT", _dt(event_ts + 3 * H1))
    assert out["opened"] == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_band_renormalization_exit_with_funding_accrual(
    engine_env, fake_redis, monkeypatch
):
    from app.config import settings

    engine = engine_env.engine
    monkeypatch.setattr(settings, "majorsbot_maker_fee_pct", 0.0)
    monkeypatch.setattr(settings, "majorsbot_taker_fee_pct", 0.0)
    monkeypatch.setattr(settings, "majorsbot_slippage_pct", 0.0)
    await fake_redis.set("majorsbot:equity:paper", "10000")
    await fake_redis.set("majorsbot:concurrent_count", "1")

    t0 = 4_000 * H1
    band_ts = t0 + 8 * H1
    # Window at the band event: 45×0.0001 + 44×0.0003 + event 0.0002
    # → count(≤0.0002) = 46/90 ≈ 0.511 — inside 40–60.
    funding = [(t0 - (90 - k) * 8 * H1, 0.0001) for k in range(46)]
    funding += [(t0 - (44 - k) * 8 * H1, 0.0003) for k in range(44)]
    funding.append((band_ts, 0.0002))
    bars = [_bar(t0 + k * H1, 100, 100.5, 99.5, 100) for k in range(9)]  # ≤ band bar
    trade = _make_trade(
        status="open",
        strategy="fundingfade",
        direction="short",
        entry_mode="market",
        qty=Decimal("1"),
        entry_at=_dt(t0),
        entry_bar_at=_dt(t0),
        stop_price=Decimal("103"),
        initial_stop_price=Decimal("103"),
        take_profit_price=None,
    )
    engine_env.trades = [trade]
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=funding)

    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(band_ts + H1))
    assert out["closed"] == 1
    assert trade.close_reason == "funding_norm"
    assert trade.close_price == Decimal("100")  # band-bar open, no slip (zeroed)
    # Short collected the band event itself: +0.0002×100×1 = +0.02.
    assert trade.funding_pnl_usd == Decimal("0.02")
    assert trade.status == "closed"


@pytest.mark.asyncio
async def test_trail_state_persists_across_ticks(engine_env):
    engine = engine_env.engine
    t0 = 5_000 * H1
    trade = _make_trade(
        status="open",
        qty=Decimal("1"),
        entry_at=_dt(t0),
        entry_bar_at=_dt(t0),
        stop_price=Decimal("95"),
        initial_stop_price=Decimal("95"),
        take_profit_price=Decimal("107.5"),
    )
    engine_env.trades = [trade]
    # Bar arms the trail (high 105 = +1R) → stop should ratchet to 100.
    bars = [_bar(t0, 100, 101, 99, 100), _bar(t0 + H1, 101, 105, 100.5, 104)]
    engine_env.md = data.MarketData(symbol="BTCUSDT", bars=bars, funding=None)
    db = FakeDB()
    out = await engine._process_symbol(db, "BTCUSDT", _dt(t0 + 2 * H1))
    assert out["closed"] == 0
    assert trade.status == "open"
    assert trade.stop_price == Decimal("100")
    assert trade.peak_price == Decimal("105")
    # Watermark advanced — re-running the same bars is a no-op.
    db2 = FakeDB()
    await engine._process_symbol(db2, "BTCUSDT", _dt(t0 + 2 * H1))
    assert trade.stop_price == Decimal("100")
    assert trade.status == "open"


# ---------- weekly report section ----------


class TestMajorsbotReportSection:
    def test_compose_renders_both_strategies(self):
        from app.scripts.forward_test_report import compose_majorsbot_section

        text = compose_majorsbot_section(
            {
                "equity": 10123.45,
                "open": 2,
                "per_strategy": {
                    "volevent": {
                        "signals": 4,
                        "filled": 3,
                        "pending": 1,
                        "cancelled": 1,
                        "closed": 2,
                        "avg_r_net": 0.151,
                        "total_r_net": 0.30,
                    },
                    "fundingfade": {
                        "signals": 10,
                        "filled": 10,
                        "pending": 0,
                        "cancelled": 0,
                        "closed": 8,
                        "avg_r_net": 0.028,
                        "total_r_net": 0.22,
                    },
                },
            }
        )
        assert "MajorsBot" in text
        assert "volevent" in text and "fundingfade" in text
        assert "+0.151" in text
        assert "$10,123.45" in text

    def test_compose_handles_empty_stats(self):
        from app.scripts.forward_test_report import compose_majorsbot_section

        text = compose_majorsbot_section({"equity": None, "open": 0, "per_strategy": {}})
        assert "volevent" in text and "fundingfade" in text
