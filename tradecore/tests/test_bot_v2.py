"""WaveBot v2 — retrace entry + partial-trail exit tests.

Pure-function coverage for the new strategy primitives (retrace limit, stop
floor, partial target, trail arming/ratchet, pending-fill verdicts, the
per-bar trail stepper), plus executor-level transition tests (pending fill /
cancel / partial-aware final close) against the fake Redis and an in-memory
DB stand-in.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.modules.bot import strategy
from app.modules.bot.schemas import Direction, TrailState


def _candle(h, l, c, t=1717920000000) -> dict:
    return {"t": t, "o": (h + l) / 2, "h": h, "l": l, "c": c, "v": 1000}


# ---------- retrace limit ----------


class TestRetraceLimit:
    def test_long_limit_is_depth_into_the_range(self):
        # ref 104, range 5, depth 0.5 → 104 − 2.5 = 101.5
        limit = strategy.compute_retrace_limit(
            Direction.LONG, Decimal("104"), Decimal("105"), Decimal("100"), Decimal("0.5")
        )
        assert limit == Decimal("101.5")

    def test_long_limit_clamped_to_signal_low(self):
        # depth 1.0 → raw 99, below the signal low → clamped to 100
        limit = strategy.compute_retrace_limit(
            Direction.LONG, Decimal("104"), Decimal("105"), Decimal("100"), Decimal("1.0")
        )
        assert limit == Decimal("100")

    def test_long_degenerate_when_ref_at_or_below_signal_low(self):
        # Price already retraced through the whole bar — clamped limit ≥ ref.
        assert (
            strategy.compute_retrace_limit(
                Direction.LONG, Decimal("100"), Decimal("105"), Decimal("100"), Decimal("0.5")
            )
            is None
        )
        assert (
            strategy.compute_retrace_limit(
                Direction.LONG, Decimal("99"), Decimal("105"), Decimal("100"), Decimal("0.5")
            )
            is None
        )

    def test_zero_depth_is_degenerate(self):
        # limit == ref: not a retrace, reject rather than place a marketable limit
        assert (
            strategy.compute_retrace_limit(
                Direction.LONG, Decimal("104"), Decimal("105"), Decimal("100"), Decimal("0")
            )
            is None
        )

    def test_broken_range_returns_none(self):
        assert (
            strategy.compute_retrace_limit(
                Direction.LONG, Decimal("104"), Decimal("100"), Decimal("100"), Decimal("0.5")
            )
            is None
        )

    def test_short_limit_is_depth_up_the_range(self):
        # ref 101, range 5, depth 0.5 → 101 + 2.5 = 103.5
        limit = strategy.compute_retrace_limit(
            Direction.SHORT, Decimal("101"), Decimal("105"), Decimal("100"), Decimal("0.5")
        )
        assert limit == Decimal("103.5")

    def test_short_limit_clamped_to_signal_high(self):
        limit = strategy.compute_retrace_limit(
            Direction.SHORT, Decimal("101"), Decimal("105"), Decimal("100"), Decimal("1.0")
        )
        assert limit == Decimal("105")

    def test_short_degenerate_when_ref_at_or_above_signal_high(self):
        assert (
            strategy.compute_retrace_limit(
                Direction.SHORT, Decimal("105"), Decimal("105"), Decimal("100"), Decimal("0.5")
            )
            is None
        )


# ---------- minimum stop-distance floor ----------


class TestStopFloor:
    def test_long_sub_floor_stop_is_pushed_down(self):
        stop = strategy.apply_stop_floor(
            Direction.LONG, Decimal("100"), Decimal("99.5"), Decimal("0.015")
        )
        assert stop == Decimal("98.5")

    def test_long_wide_stop_untouched(self):
        stop = strategy.apply_stop_floor(
            Direction.LONG, Decimal("100"), Decimal("98"), Decimal("0.015")
        )
        assert stop == Decimal("98")

    def test_short_sub_floor_stop_is_pushed_up(self):
        stop = strategy.apply_stop_floor(
            Direction.SHORT, Decimal("100"), Decimal("100.5"), Decimal("0.015")
        )
        assert stop == Decimal("101.5")

    def test_short_wide_stop_untouched(self):
        stop = strategy.apply_stop_floor(
            Direction.SHORT, Decimal("100"), Decimal("102"), Decimal("0.015")
        )
        assert stop == Decimal("102")

    def test_zero_floor_is_noop(self):
        stop = strategy.apply_stop_floor(
            Direction.LONG, Decimal("100"), Decimal("99.99"), Decimal("0")
        )
        assert stop == Decimal("99.99")

    def test_inverted_stop_within_floor_gets_normalized(self):
        # A long stop slightly ABOVE entry is inside the floor band — the floor
        # pushes it to the valid side at floor distance.
        stop = strategy.apply_stop_floor(
            Direction.LONG, Decimal("100"), Decimal("100.5"), Decimal("0.015")
        )
        assert stop == Decimal("98.5")


# ---------- partial target + trail primitives ----------


class TestPartialTarget:
    def test_long_target_is_take_r_from_initial_stop(self):
        # entry 100, stop 95 (risk 5), 1.5R → 107.5
        t = strategy.partial_target(
            Direction.LONG, Decimal("100"), Decimal("95"), Decimal("1.5")
        )
        assert t == Decimal("107.5")

    def test_short_target_mirrors(self):
        t = strategy.partial_target(
            Direction.SHORT, Decimal("100"), Decimal("105"), Decimal("1.5")
        )
        assert t == Decimal("92.5")


class TestTrailStopFromPeak:
    def test_long_trails_below_peak(self):
        s = strategy.trail_stop_from_peak(
            Direction.LONG, Decimal("110"), Decimal("5"), Decimal("1")
        )
        assert s == Decimal("105")

    def test_short_trails_above_peak(self):
        s = strategy.trail_stop_from_peak(
            Direction.SHORT, Decimal("90"), Decimal("5"), Decimal("1")
        )
        assert s == Decimal("95")


class TestTrailArmed:
    def test_arms_only_at_or_past_arm_r(self):
        args = (Direction.LONG, Decimal("100"))
        assert not strategy.trail_armed(*args, Decimal("104.9"), Decimal("5"), Decimal("1"))
        assert strategy.trail_armed(*args, Decimal("105"), Decimal("5"), Decimal("1"))
        assert strategy.trail_armed(*args, Decimal("108"), Decimal("5"), Decimal("1"))

    def test_short_mirror(self):
        assert strategy.trail_armed(
            Direction.SHORT, Decimal("100"), Decimal("95"), Decimal("5"), Decimal("1")
        )
        assert not strategy.trail_armed(
            Direction.SHORT, Decimal("100"), Decimal("95.1"), Decimal("5"), Decimal("1")
        )

    def test_zero_risk_never_arms(self):
        assert not strategy.trail_armed(
            Direction.LONG, Decimal("100"), Decimal("200"), Decimal("0"), Decimal("1")
        )


class TestRatchetStop:
    def test_long_only_moves_up(self):
        assert strategy.ratchet_stop(Direction.LONG, Decimal("95"), Decimal("101")) == Decimal("101")
        assert strategy.ratchet_stop(Direction.LONG, Decimal("101"), Decimal("99")) == Decimal("101")

    def test_short_only_moves_down(self):
        assert strategy.ratchet_stop(Direction.SHORT, Decimal("105"), Decimal("99")) == Decimal("99")
        assert strategy.ratchet_stop(Direction.SHORT, Decimal("99"), Decimal("101")) == Decimal("99")


# ---------- pending-limit fill verdicts ----------


class TestPendingFill:
    def test_long_limit_fill_detection(self):
        assert strategy.is_limit_filled(Direction.LONG, Decimal("103"), Decimal("101"), Decimal("101.5"))
        assert not strategy.is_limit_filled(Direction.LONG, Decimal("103"), Decimal("102"), Decimal("101.5"))

    def test_short_limit_fill_detection(self):
        assert strategy.is_limit_filled(Direction.SHORT, Decimal("103"), Decimal("101"), Decimal("102.5"))
        assert not strategy.is_limit_filled(Direction.SHORT, Decimal("102"), Decimal("101"), Decimal("102.5"))

    def test_long_verdicts(self):
        limit, stop = Decimal("101.5"), Decimal("98.5")
        assert strategy.check_pending_fill(Direction.LONG, Decimal("103"), Decimal("102"), limit, stop) == "none"
        assert strategy.check_pending_fill(Direction.LONG, Decimal("103"), Decimal("101"), limit, stop) == "filled"
        # Same bar sweeps the limit AND the stop → pessimistic in-and-out.
        assert strategy.check_pending_fill(Direction.LONG, Decimal("103"), Decimal("98"), limit, stop) == "filled_stopped"

    def test_short_verdicts(self):
        limit, stop = Decimal("102.5"), Decimal("106")
        assert strategy.check_pending_fill(Direction.SHORT, Decimal("102"), Decimal("100"), limit, stop) == "none"
        assert strategy.check_pending_fill(Direction.SHORT, Decimal("103"), Decimal("100"), limit, stop) == "filled"
        assert strategy.check_pending_fill(Direction.SHORT, Decimal("107"), Decimal("100"), limit, stop) == "filled_stopped"


# ---------- per-bar trail state machine ----------


class TestStepTrailBar:
    ENTRY = Decimal("100")
    INIT_STOP = Decimal("95")  # risk 5; partial target 107.5; arm at 105
    CFG = dict(
        partial_take_r=Decimal("1.5"),
        trail_arm_r=Decimal("1"),
        trail_distance_r=Decimal("1"),
    )

    def _step(self, state, high, low):
        return strategy.step_trail_bar(
            Direction.LONG, self.ENTRY, self.INIT_STOP, state,
            Decimal(str(high)), Decimal(str(low)), **self.CFG,
        )

    def test_full_sequence_arm_partial_then_trail_stop(self):
        state = TrailState(stop=Decimal("95"), peak=Decimal("100"), partial_taken=False)

        # Bar 1: runs to 106 — arms the trail (excursion 6 ≥ 5), ratchets to 101.
        state, events = self._step(state, 106, 101)
        assert events == []
        assert state.peak == Decimal("106")
        assert state.stop == Decimal("101")
        assert not state.partial_taken

        # Bar 2: hits the 107.5 partial (limit-style, exact target), peak 108 → stop 103.
        state, events = self._step(state, 108, 102)
        assert events == [("partial", Decimal("107.5"))]
        assert state.partial_taken
        assert state.peak == Decimal("108")
        assert state.stop == Decimal("103")

        # Bar 3: fades into the trailed stop → runner out at 103.
        state, events = self._step(state, 104, Decimal("102.5"))
        assert events == [("stop", Decimal("103"))]

    def test_stop_wins_tie_with_partial_in_same_bar(self):
        state = TrailState(stop=Decimal("95"), peak=Decimal("100"), partial_taken=False)
        _, events = self._step(state, 108, 94)  # bar spans both stop and target
        assert events == [("stop", Decimal("95"))]

    def test_trail_never_loosens(self):
        state = TrailState(stop=Decimal("103"), peak=Decimal("108"), partial_taken=True)
        state, events = self._step(state, 104, Decimal("103.5"))
        assert events == []
        assert state.peak == Decimal("108")  # lower high doesn't move the peak
        assert state.stop == Decimal("103")  # candidate 103 can't ratchet down

    def test_new_trail_level_cannot_stop_out_same_bar(self):
        # Bar close at 106 arms and sets stop 101, but the bar's own low 100.5
        # (below the NEW stop) must not close the trade — ratchet is on close.
        state = TrailState(stop=Decimal("95"), peak=Decimal("100"), partial_taken=False)
        state, events = self._step(state, 106, Decimal("100.5"))
        assert events == []
        assert state.stop == Decimal("101")

    def test_short_mirror_arms_and_ratchets(self):
        state = TrailState(stop=Decimal("105"), peak=Decimal("100"), partial_taken=False)
        state, events = strategy.step_trail_bar(
            Direction.SHORT, Decimal("100"), Decimal("105"), state,
            Decimal("99"), Decimal("94"), **self.CFG,
        )
        assert events == []
        assert state.peak == Decimal("94")
        assert state.stop == Decimal("99")  # 94 + 1R(5), ratcheted from 105


# ---------- plan_entry with the stop floor ----------


class TestPlanEntryStopFloor:
    def _alert(self) -> dict:
        return {
            "type": "wave_active", "symbol": "AAAUSDT", "exchange": "bybit",
            "base_asset": "AAA", "direction": "short_squeeze",
            "detected_at": datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc).isoformat(),
        }

    def test_floor_widens_tight_stop_and_sizing_follows(self):
        # signal low 103.9 → raw stop ≈103.85, only ~0.15% from entry 104.
        plan = strategy.plan_entry(
            alert=self._alert(),
            signal_candle=_candle(h=105, l=103.9, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.5"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
            risk_per_trade_pct=Decimal("0.0025"),
            stop_floor_pct=Decimal("0.015"),
        )
        assert plan is not None
        assert plan.stop_price == Decimal("104") * Decimal("0.985")
        # Risk-normalized off the FLOORED distance: $25 at the stop.
        stop_dist = (Decimal("104") - plan.stop_price) / Decimal("104")
        assert abs(plan.notional_usd * stop_dist - Decimal("25")) < Decimal("0.01")

    def test_no_floor_keeps_raw_stop(self):
        plan = strategy.plan_entry(
            alert=self._alert(),
            signal_candle=_candle(h=105, l=103.9, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.5"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert plan is not None
        assert plan.stop_price == Decimal("103.9") * Decimal("0.9995")

    def test_wide_stop_unaffected_by_floor(self):
        with_floor = strategy.plan_entry(
            alert=self._alert(),
            signal_candle=_candle(h=105, l=100, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
            stop_floor_pct=Decimal("0.015"),
        )
        without = strategy.plan_entry(
            alert=self._alert(),
            signal_candle=_candle(h=105, l=100, c=104),
            entry_price=Decimal("104"),
            paper_equity=Decimal("10000"),
            position_size_pct=Decimal("0.05"),
            stop_buffer_pct=Decimal("0.0005"),
            r_multiple=Decimal("2"),
        )
        assert with_floor is not None and without is not None
        assert with_floor.stop_price == without.stop_price


# ---------- executor transitions (fake Redis + in-memory DB) ----------


class FakeDB:
    """Just enough AsyncSession surface for the executor transition helpers."""

    def __init__(self) -> None:
        self.commits = 0

    def add(self, obj) -> None:  # pragma: no cover - unused by transitions
        pass

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj) -> None:  # pragma: no cover - unused
        pass


def _pending_trade(**overrides):
    from app.models.bot import BotTrade

    now = datetime.now(timezone.utc)
    defaults = dict(
        symbol="AAAUSDT",
        exchange="bybit",
        market_type="spot",  # spot → no funding leg, keeps the math exact
        direction="long",
        alert_type="wave_active",
        alert_detected_at=now,
        entry_price=Decimal("100"),
        entry_at=now - timedelta(minutes=10),
        entry_mode="retrace",
        limit_price=Decimal("100"),
        expire_at=now + timedelta(hours=1),
        signal_high=Decimal("105"),
        signal_low=Decimal("100"),
        notional_usd=Decimal("1000"),
        qty=Decimal("10"),
        paper_equity_at_entry=Decimal("10000"),
        stop_price=Decimal("95"),
        initial_stop_price=Decimal("95"),
        take_profit_price=Decimal("110"),
        status="pending",
    )
    defaults.update(overrides)
    return BotTrade(**defaults)


def _published_types(fake_redis) -> list[str]:
    return [json.loads(msg)["type"] for ch, msg in fake_redis.published() if ch == "alerts:bot"]


@pytest.mark.asyncio
async def test_fill_pending_trade_transitions_to_open(fake_redis):
    from app.modules.bot import executor

    db = FakeDB()
    trade = _pending_trade()
    placed_at = trade.entry_at
    await executor.fill_pending_trade(db, trade)

    assert trade.status == "open"
    assert trade.entry_price == Decimal("100")
    assert trade.entry_at > placed_at  # rewritten to the fill time
    assert db.commits == 1
    assert await fake_redis.get("bot:concurrent_count") == "1"
    assert "trade_opened" in _published_types(fake_redis)


@pytest.mark.asyncio
async def test_cancel_pending_trade_expires_with_cooldown(fake_redis):
    from app.modules.bot import executor

    db = FakeDB()
    trade = _pending_trade()
    await executor.cancel_pending_trade(db, trade)

    assert trade.status == "cancelled"
    assert trade.close_reason == "expired"
    assert trade.closed_at is not None
    assert trade.realized_pnl_usd is None  # no fill, no pnl
    assert await fake_redis.exists("bot:cooldown:AAAUSDT")
    # No counter bump for a never-filled order.
    assert await fake_redis.get("bot:concurrent_count") is None
    assert "order_cancelled" in _published_types(fake_redis)


@pytest.mark.asyncio
async def test_take_partial_profit_records_leg_and_realizes_pnl(fake_redis, monkeypatch):
    from app.config import settings
    from app.modules.bot import executor

    monkeypatch.setattr(settings, "bot_fee_pct_per_side", 0.0)
    monkeypatch.setattr(settings, "bot_partial_fraction", 0.5)
    await fake_redis.set("bot:equity:paper", "10000")

    db = FakeDB()
    trade = _pending_trade(status="open")
    await executor.take_partial_profit(db, trade, exit_price=Decimal("107.5"))

    assert trade.partial_qty == Decimal("5")
    assert trade.partial_exit_price == Decimal("107.5")
    assert trade.partial_pnl_usd == Decimal("37.5")
    assert trade.qty == Decimal("10")  # original total is preserved
    assert trade.status == "open"
    assert Decimal(await fake_redis.get("bot:equity:paper")) == Decimal("10037.5")
    assert "trade_partial_exit" in _published_types(fake_redis)


@pytest.mark.asyncio
async def test_close_after_partial_totals_r_and_equity(fake_redis, monkeypatch):
    from app.config import settings
    from app.modules.bot import executor
    from app.modules.bot.schemas import CloseReason

    monkeypatch.setattr(settings, "bot_fee_pct_per_side", 0.0)
    monkeypatch.setattr(settings, "bot_slippage_pct", 0.0)
    await fake_redis.set("bot:equity:paper", "10000")
    await fake_redis.set("bot:concurrent_count", "1")

    db = FakeDB()
    # Post-partial state: half off at 107.5 (+37.5 realized), trail at 101.
    trade = _pending_trade(
        status="open",
        stop_price=Decimal("101"),
        peak_price=Decimal("108"),
        partial_exit_price=Decimal("107.5"),
        partial_exit_at=datetime.now(timezone.utc),
        partial_qty=Decimal("5"),
        partial_pnl_usd=Decimal("37.5"),
    )
    await executor.close_paper_trade(
        db, trade, exit_price=Decimal("101"), reason=CloseReason.STOP
    )

    assert trade.status == "closed"
    assert trade.close_reason == "stop"
    # Runner: (101−100) × 5 = +5. Total: 37.5 + 5 = 42.5.
    assert trade.realized_pnl_usd == Decimal("42.5")
    # R against INITIAL stop (95) and TOTAL qty (10): risk $50 → 42.5/50.
    assert trade.realized_r == Decimal("0.85")
    assert trade.realized_r_net == Decimal("0.85")
    assert trade.fees_usd == Decimal("0")
    # Equity gets only the runner leg — the partial was realized earlier.
    assert Decimal(await fake_redis.get("bot:equity:paper")) == Decimal("10005")
    assert await fake_redis.get("bot:concurrent_count") == "0"
    # A profitable trail stop-out must not feed the loss throttle.
    assert await fake_redis.lrange("bot:recent_stops", 0, -1) == []
    assert "trade_closed" in _published_types(fake_redis)


@pytest.mark.asyncio
async def test_close_without_partial_matches_v1_math(fake_redis, monkeypatch):
    from app.config import settings
    from app.modules.bot import executor
    from app.modules.bot.schemas import CloseReason

    monkeypatch.setattr(settings, "bot_fee_pct_per_side", 0.0)
    monkeypatch.setattr(settings, "bot_slippage_pct", 0.0)
    await fake_redis.set("bot:equity:paper", "10000")
    await fake_redis.set("bot:concurrent_count", "1")

    db = FakeDB()
    trade = _pending_trade(status="open")
    await executor.close_paper_trade(
        db, trade, exit_price=Decimal("110"), reason=CloseReason.TP
    )

    assert trade.realized_pnl_usd == Decimal("100")  # (110−100) × 10
    assert trade.realized_r == Decimal("2")  # clean 2R against stop 95
    assert Decimal(await fake_redis.get("bot:equity:paper")) == Decimal("10100")


@pytest.mark.asyncio
async def test_losing_stop_still_feeds_loss_throttle(fake_redis, monkeypatch):
    from app.config import settings
    from app.modules.bot import executor
    from app.modules.bot.schemas import CloseReason

    monkeypatch.setattr(settings, "bot_fee_pct_per_side", 0.0)
    monkeypatch.setattr(settings, "bot_slippage_pct", 0.0)
    await fake_redis.set("bot:equity:paper", "10000")

    db = FakeDB()
    trade = _pending_trade(status="open")
    await executor.close_paper_trade(
        db, trade, exit_price=Decimal("95"), reason=CloseReason.STOP
    )

    assert trade.realized_pnl_usd == Decimal("-50")
    assert len(await fake_redis.lrange("bot:recent_stops", 0, -1)) == 1
