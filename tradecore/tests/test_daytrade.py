"""DayTrade playbook — the rules are pure functions, so they are tested as such."""
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.daytrade.playbook import (
    BAR_MS, Playbook, alert_entry, build_plan, evaluate, gate, net_r, resolve, scoreboard, session_start,
)

PB = Playbook()
DAY = datetime(2026, 11, 10, tzinfo=timezone.utc)  # a 00:00 UTC session open
T0 = int(DAY.timestamp() * 1000)


def bars(start_ms: int, closes: list[float], vol: float = 100.0, spread: float = 0.1) -> list[dict]:
    return [
        {"t": start_ms + i * BAR_MS, "o": c, "h": c + spread, "l": c - spread, "c": c, "v": vol}
        for i, c in enumerate(closes)
    ]


def session(pre: float = 100.0, breakout: float | None = None, breakout_vol: float = 300.0) -> list[dict]:
    """30 flat bars before the open, a 12-bar range 100.1..100.9, then an optional breakout bar."""
    rows = bars(T0 - 30 * BAR_MS, [pre] * 30)
    rows += bars(T0, [100.2, 100.8] * 6)
    if breakout is not None:
        rows += bars(T0 + 12 * BAR_MS, [breakout], vol=breakout_vol)
    return rows


AFTER_BREAKOUT = DAY + timedelta(minutes=66)  # 1 minute after the 13th bar closes


def test_range_is_forming_until_every_bar_closed():
    st = evaluate(session(), DAY + timedelta(minutes=30), "00:00", PB)
    assert st["status"] == "forming"
    assert (st["bars_closed"], st["bars_needed"]) == (6, 12)


def test_waiting_inside_the_range():
    st = evaluate(session(breakout=100.5), AFTER_BREAKOUT, "00:00", PB)
    assert st["status"] == "waiting"
    assert st["range_high"] == pytest.approx(100.9) and st["range_low"] == pytest.approx(100.1)


def test_closed_bar_above_range_on_volume_triggers_long():
    st = evaluate(session(breakout=101.5), AFTER_BREAKOUT, "00:00", PB)
    assert (st["status"], st["side"]) == ("triggered", "LONG")
    assert st["vol_x"] == 3.0
    # No next bar yet: fill at the trigger close, moved against us by slippage.
    assert st["entry"] == pytest.approx(101.5 * 1.0005)


def test_entry_fills_at_the_next_bars_open_not_the_trigger_close():
    rows = session(breakout=101.5) + [{"t": T0 + 13 * BAR_MS, "o": 101.9, "h": 102, "l": 101.8, "c": 101.9, "v": 1.0}]
    st = evaluate(rows, AFTER_BREAKOUT, "00:00", PB)
    assert st["entry"] == pytest.approx(101.9 * 1.0005)


def test_unclosed_bar_is_not_an_entry():
    # Same bars, but "now" is before the breakout bar has closed.
    st = evaluate(session(breakout=101.5), DAY + timedelta(minutes=63), "00:00", PB)
    assert st["status"] == "waiting"


def test_weak_volume_breakout_is_skipped():
    st = evaluate(session(breakout=101.5, breakout_vol=100.0), AFTER_BREAKOUT, "00:00", PB)
    assert st["status"] == "waiting"
    assert "volume" in st["skipped"][0]["why"]


def test_breakout_against_the_trend_is_skipped():
    # Price fell from 105 into the range: the slow EMA is still far above 101.5.
    st = evaluate(session(pre=105.0, breakout=101.5), AFTER_BREAKOUT, "00:00", PB)
    assert st["status"] == "waiting"
    assert "trend" in st["skipped"][0]["why"]


def test_stale_trigger_is_missed_not_entered():
    st = evaluate(session(breakout=101.5), AFTER_BREAKOUT + timedelta(minutes=30), "00:00", PB)
    assert st["status"] == "missed"


def test_short_below_range():
    st = evaluate(session(breakout=99.5), AFTER_BREAKOUT, "00:00", PB)
    assert (st["status"], st["side"]) == ("triggered", "SHORT")


def test_plan_stop_at_midpoint_target_at_2r():
    plan, reasons = build_plan("LONG", 101.5, 100.9, 100.1, PB)
    assert reasons == []
    assert plan["stop"] == pytest.approx(100.5)
    assert plan["target"] == pytest.approx(103.5)
    short, _ = build_plan("SHORT", 99.5, 100.9, 100.1, PB)
    assert short["stop"] == pytest.approx(100.5) and short["target"] == pytest.approx(97.5)


def test_plan_refuses_a_stop_too_tight_to_pay_after_fees():
    # Stop 0.5% away: the "2R" target nets only ~1.38x the risk once fees are paid.
    plan, reasons = build_plan("LONG", 100.5, 100.2, 99.8, PB)
    assert plan["net_rr"] < PB.min_net_rr and "fees" in reasons[0]


def test_stop_out_is_exactly_minus_one_r():
    assert net_r("LONG", 101.5, 100.5, 100.5, True, PB) == pytest.approx(-1.0)
    assert net_r("SHORT", 99.5, 100.5, 100.5, True, PB) == pytest.approx(-1.0)


def test_resolve_counts_a_bar_hitting_both_as_the_stop():
    exit_by = DAY + timedelta(hours=8)
    both = [{"t": T0, "o": 101.5, "h": 103.6, "l": 100.4, "c": 102.0, "v": 1.0}]
    out = resolve("LONG", 101.5, 100.5, 103.5, T0, exit_by, both, DAY + timedelta(minutes=10), PB)
    assert out[0] == "stop" and out[2] == pytest.approx(-1.0)


def test_stop_gapped_through_fills_at_the_open_and_costs_more_than_1r():
    exit_by = DAY + timedelta(hours=8)
    gap = [{"t": T0, "o": 99.0, "h": 99.2, "l": 98.5, "c": 99.0, "v": 1.0}]
    status, px, r = resolve("LONG", 101.5, 100.5, 103.5, T0, exit_by, gap, DAY + timedelta(minutes=10), PB)
    assert (status, px) == ("stop", 99.0) and r < -2.0


def test_target_touched_but_not_traded_through_is_not_a_fill():
    exit_by = DAY + timedelta(hours=8)
    touch = [{"t": T0, "o": 101.5, "h": 103.5, "l": 101.0, "c": 103.0, "v": 1.0}]
    assert resolve("LONG", 101.5, 100.5, 103.5, T0, exit_by, touch, DAY + timedelta(minutes=10), PB) is None


def test_us_open_follows_new_york_time_across_daylight_saving():
    summer = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
    winter = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
    assert session_start(summer, "09:30@America/New_York").hour == 13   # EDT
    assert session_start(winter, "09:30@America/New_York").hour == 14   # EST
    before_open = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    assert session_start(before_open, "09:30@America/New_York").day == 11


def test_changing_any_rule_changes_the_playbook_version():
    assert Playbook().version == PB.version
    assert Playbook(target_r=3.0).version != PB.version


def test_changing_the_rule_code_changes_the_playbook_version(monkeypatch):
    from app.modules.daytrade import playbook
    before = PB.version
    monkeypatch.setattr(playbook, "_RULES_SRC", "edited")
    assert PB.version != before


def test_live_fill_is_the_alert_price_and_only_later_bars_count():
    assert alert_entry("LONG", 100.0, PB) == pytest.approx(100.05)
    assert alert_entry("SHORT", 100.0, PB) == pytest.approx(99.95)
    # A stop hit in the bar before the alert must not be scored.
    alert = DAY + timedelta(minutes=67)
    rows = [
        {"t": T0 + 12 * BAR_MS, "o": 101, "h": 101.5, "l": 98.0, "c": 101, "v": 1.0},   # before alert
        {"t": T0 + 14 * BAR_MS, "o": 101, "h": 101.5, "l": 100.8, "c": 101, "v": 1.0},  # after
    ]
    out = resolve("LONG", 101.0, 100.0, 103.0, int(alert.timestamp() * 1000),
                  DAY + timedelta(hours=8), rows, DAY + timedelta(hours=2), PB)
    assert out is None


def test_resolve_target_time_stop_and_still_open():
    exit_by = DAY + timedelta(hours=8)
    win = [{"t": T0, "o": 101.5, "h": 103.6, "l": 101.0, "c": 103.0, "v": 1.0}]
    status, px, r = resolve("LONG", 101.5, 100.5, 103.5, T0, exit_by, win, DAY + timedelta(minutes=10), PB)
    assert (status, px) == ("target", 103.5) and 1.5 < r < 2.0  # fees shave the 2R

    drift = bars(T0, [102.0, 102.2])
    assert resolve("LONG", 101.5, 100.5, 103.5, T0, exit_by, drift, DAY + timedelta(minutes=15), PB) is None
    status, px, r = resolve("LONG", 101.5, 100.5, 103.5, T0, exit_by, drift, exit_by, PB)
    assert (status, px) == ("time", 102.2) and r > 0


def test_gate_enforces_the_daily_budget():
    ok = {"trades": 1, "session_trades": 1, "loss_r": 1.0, "open_symbols": [], "streak": 1}
    assert gate("BTCUSDT", ok, PB) == []
    assert gate("BTCUSDT", {**ok, "trades": 4}, PB)
    assert gate("BTCUSDT", {**ok, "session_trades": 2}, PB)             # correlated basket cap
    assert gate("BTCUSDT", {**ok, "streak": 3}, PB)
    assert gate("BTCUSDT", {**ok, "loss_r": 2.5}, PB)                  # 2.5 + this 1R > 3R
    assert gate("BTCUSDT", {**ok, "open_symbols": ["BTCUSDT"]}, PB)   # one plan per symbol


def test_scoreboard_is_judged_once_and_needs_a_margin():
    assert scoreboard([1.0] * 59, PB)["verdict"] == "collecting"
    # 60 small losers, then a hot streak: the gate was already failed.
    sb = scoreboard([-0.1] * 60 + [2.0] * 50, PB)
    assert sb["verdict"] == "failed" and sb["n"] == 110
    # A positive average is not enough: +0.1R with this much noise is luck-sized.
    noisy = scoreboard([1.2, -1.0] * 30, PB)
    assert noisy["avg_r"] == pytest.approx(0.1) and noisy["bar"] > 0.1 and noisy["verdict"] == "failed"
    assert scoreboard([2.0, -1.0] * 30, PB)["verdict"] == "passed"
