"""XsTrend rules: the smallest checks that fail if the ranking, the P&L signs or the verdict break."""
from datetime import datetime, timezone

from app.modules.xstrend import rules as R
from app.modules.xstrend.portfolio import week_of

T = R.FIRST_WEEK


def _bars(growth: dict[str, float], turnover: dict[str, float] | None = None) -> dict:
    """Coins compounding at a fixed daily rate, 100 days of history before T."""
    out = {}
    for s, g in growth.items():
        px = lambda d, g=g: 100 * (1 + g) ** d  # noqa: E731
        out[s] = {T + d * R.DAY: (px(d), px(d + 1), (turnover or {}).get(s, 1e6)) for d in range(-100, 0)}
    return out


def test_longs_are_the_strongest_and_shorts_the_weakest():
    growth = {f"C{i:02d}USDT": (i - 15) / 1000 for i in range(30)}
    uni, longs, shorts = R.pick(_bars(growth), T)
    assert len(uni) == 30
    assert longs == [f"C{i:02d}USDT" for i in range(20, 30)]
    assert shorts == [f"C{i:02d}USDT" for i in range(10)]
    w = R.weights(longs, shorts)
    assert abs(sum(w.values())) < 1e-12 and abs(sum(map(abs, w.values())) - 1) < 1e-12


def test_universe_is_top_30_by_turnover_and_needs_a_full_history():
    growth = {f"C{i:02d}USDT": 0.001 * i for i in range(32)}
    bars = _bars(growth, {"C31USDT": 1.0})          # least traded: out
    del bars["C30USDT"][T - 5 * R.DAY]               # a missing day in the last 30: out
    uni, _, _ = R.pick(bars, T)
    assert "C31USDT" not in uni and "C30USDT" not in uni and len(uni) == 30
    assert R.pick({s: b for s, b in list(bars.items())[:29]}, T) is None


def test_exact_ties_break_by_symbol_name():
    _, longs, shorts = R.pick(_bars({f"C{i:02d}USDT": 0.001 for i in range(30)}), T)
    assert shorts[0] == "C00USDT" and longs[-1] == "C29USDT"


def test_week_result_signs_and_delisted_exit():
    b = {"UPUSDT": {T: (100, 0, 0), T + R.WEEK: (110, 0, 0)},
         "DEADUSDT": {T: (100, 90, 0), T + R.DAY: (90, 50, 0)}}   # no bar at the exit: last close
    w = {"UPUSDT": 0.5, "DEADUSDT": -0.5}
    res = R.settle(w, ["UPUSDT", "DEADUSDT"], b, {"UPUSDT": 0.01, "DEADUSDT": 0.02}, T, turn=1.0)
    assert abs(res["gross"] - (0.5 * 0.10 + 0.5 * 0.50)) < 1e-12
    assert abs(res["funding"] - (0.5 * 0.01 - 0.5 * 0.02)) < 1e-12   # longs pay, shorts receive
    assert abs(res["net"] - (res["gross"] - 0.001 - res["funding"])) < 1e-12
    assert abs(R.turnover({"A": 0.05}, {"A": 0.06, "B": -0.05}) - 0.06) < 1e-12


def test_verdict_only_at_104_weeks_and_ignores_later_ones():
    assert R.scoreboard([0.01] * 103)["verdict"] == "running"
    good = [0.02, -0.005] * 52
    assert R.scoreboard(good + [-1.0] * 10)["verdict"] == "pass"
    assert R.scoreboard([-0.02, 0.005] * 52)["verdict"] == "ruled_out"
    assert R.scoreboard([0.05, -0.048] * 52)["verdict"] == "inconclusive"
    assert R.scoreboard(([-0.01] * 52) + ([0.05] * 52))["verdict"] == "inconclusive"  # a losing half


def test_week_starts_monday_midnight_utc():
    assert week_of(datetime(2026, 11, 16, 0, 15, tzinfo=timezone.utc)) == T
    assert week_of(datetime(2026, 11, 22, 23, 59, tzinfo=timezone.utc)) == T
    assert week_of(datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc)) == T - R.WEEK
