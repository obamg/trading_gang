"""WaveBot — exit-replay simulation tests.

Pure-logic coverage of app/modules/bot/replay.py only: no network, no DB.
The kline-fetching CLI (app/scripts/replay_exits.py) is exercised manually.

Fixed fixture geometry unless stated otherwise: LONG, entry 100, stop 95
(risk 5) → +1R=105, +1.5R=107.5, +2R=110. Cost constants: 0.0006 taker/side,
0.0005 adverse slippage on market-style exits.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.bot import replay

T0 = 1_750_000_000_000  # arbitrary epoch ms; bars step 5m from here
BAR_MS = replay.BAR_MS


def _bar(i: int, h, l, c) -> dict:
    return {"t": T0 + i * BAR_MS, "o": c, "h": h, "l": l, "c": c}


def _trade(direction: str = "long", entry: str = "100", stop: str = "95") -> dict:
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "entry_price": Decimal(entry),
        "stop_price": Decimal(stop),
        "entry_at": T0,
    }


class TestBaseline2R:
    def test_clean_2r_win(self):
        bars = [
            # Pre-entry bar that would hit everything — must be ignored.
            {"t": T0 - BAR_MS, "o": 100, "h": 120, "l": 90, "c": 100},
            _bar(0, 104, 99, 103),
            _bar(1, 110, 104, 109),
        ]
        res = replay.baseline_2r(_trade(), bars)
        assert res["exit_reason"] == "tp"
        assert res["exit_price"] == Decimal("110")  # limit TP — no slippage
        # net = 10 − (100+110)×0.0006 = 9.874 → 9.874/5
        assert res["net_r"] == Decimal("1.9748")
        assert res["hold_minutes"] == 10.0  # exit on the 2nd post-entry bar

    def test_stop_loss_is_worse_than_minus_1r(self):
        res = replay.baseline_2r(_trade(), [_bar(0, 101, 94, 96)])
        assert res["exit_reason"] == "stop"
        # fill 95×0.9995=94.9525; net = −5.0475 − 194.9525×0.0006
        assert res["exit_price"] == Decimal("94.9525")
        assert res["net_r"] == Decimal("-1.0328943")
        assert res["net_r"] < Decimal("-1")  # slippage + fees push past −1R

    def test_short_2r_win(self):
        bars = [_bar(0, 101, 96, 98), _bar(1, 97, 89.5, 90.5)]
        res = replay.baseline_2r(_trade("short", "100", "105"), bars)
        assert res["exit_reason"] == "tp"
        assert res["exit_price"] == Decimal("90")
        # net = 10 − (100+90)×0.0006 = 9.886 → 9.886/5
        assert res["net_r"] == Decimal("1.9772")


class TestPessimisticTie:
    def test_same_bar_stop_and_tp_is_stop(self):
        # Bar touches both 95 and 110 — stop wins, matching monitor.py.
        res = replay.baseline_2r(_trade(), [_bar(0, 111, 94, 100)])
        assert res["exit_reason"] == "stop"
        assert res["net_r"] < Decimal("-1")


class TestBreakevenAt1R:
    def test_saves_would_be_loser_after_1r_touch(self):
        bars = [
            _bar(0, 105.5, 99, 105),  # touches +1R → BE arms for next bars
            _bar(1, 103, 94, 95.5),  # would hit the 95 stop
        ]
        baseline = replay.baseline_2r(_trade(), bars)
        assert baseline["exit_reason"] == "stop"
        assert baseline["net_r"] == Decimal("-1.0328943")

        res = replay.be_at_1r(_trade(), bars)
        assert res["exit_reason"] == "breakeven"
        # BE exit at entry, market-style: fill 99.95; net = −0.05 − 0.11997
        assert res["exit_price"] == Decimal("99.9500")
        assert res["net_r"] == Decimal("-0.033994")
        assert res["net_r"] > Decimal("-1")  # loser saved (minus costs)

    def test_arming_bar_cannot_rescue_itself(self):
        # Same bar touches +1R AND the stop — BE only applies to subsequent
        # bars (and stop is checked first), so this is a full −1R stop-out.
        res = replay.be_at_1r(_trade(), [_bar(0, 105.5, 94, 95)])
        assert res["exit_reason"] == "stop"
        assert res["net_r"] < Decimal("-1")


class TestPartialTrail:
    def test_blends_legs_on_clean_run(self):
        bars = [
            _bar(0, 104, 99, 103),  # nothing yet
            _bar(1, 106, 102, 105.5),  # +1R → trail arms, peak 106
            _bar(2, 108, 104, 107.8),  # leg1 fills at 107.5 (limit); peak 108
            _bar(3, 112.5, 107, 112),  # peak 112.5 → trail stop 107.5 (next bar)
            _bar(4, 112, 107.4, 108),  # low ≤ 107.5 → trail exit
        ]
        res = replay.partial_trail(_trade(), bars)
        leg_tp, leg_trail = res["legs"]

        assert leg_tp["exit_reason"] == "tp"
        assert leg_tp["exit_price"] == Decimal("107.5")  # limit — no slippage
        # net = 7.5 − 207.5×0.0006 = 7.3755 → /5
        assert leg_tp["net_r"] == Decimal("1.4751")
        assert leg_tp["hold_minutes"] == 15.0

        assert leg_trail["exit_reason"] == "trail"
        # fill 107.5×0.9995 = 107.44625; net = 7.44625 − 207.44625×0.0006
        assert leg_trail["exit_price"] == Decimal("107.44625")
        assert leg_trail["net_r"] == Decimal("1.46435645")
        assert leg_trail["hold_minutes"] == 25.0

        # 50/50 blend of the two legs' net R.
        assert res["net_r"] == (leg_tp["net_r"] + leg_trail["net_r"]) / 2
        assert res["net_r"] == Decimal("1.469728225")
        assert res["exit_reason"] == "tp/trail"
        assert res["exit_price"] == leg_trail["exit_price"]  # later leg = flat
        assert res["hold_minutes"] == 25.0

    def test_stop_before_partial_kills_both_legs(self):
        res = replay.partial_trail(_trade(), [_bar(0, 101, 94, 96)])
        assert res["exit_reason"] == "stop/stop"
        assert res["net_r"] == Decimal("-1.0328943")


class TestTimeStop4H:
    def test_exits_stagnant_trade_at_4h(self):
        # Flat chop: never near stop (95), TP (110), or +0.5R at close (102.5).
        bars = [_bar(i, 101.5, 99.5, 101) for i in range(60)]
        res = replay.time_stop_4h(_trade(), bars)
        assert res["exit_reason"] == "time"
        assert 240 <= res["hold_minutes"] <= 250  # first bar ≥ 4h after entry
        # Exit at close 101, market-style: fill 100.9495; net = 0.9495 − 0.1205697
        assert res["exit_price"] == Decimal("100.9495")
        assert res["net_r"] == Decimal("0.16578606")

    def test_survivor_above_half_r_runs_to_tp(self):
        bars = [_bar(i, 103, 99.5, 102) for i in range(48)]
        bars.append(_bar(48, 104, 101, 103))  # 4h check: close +0.6R ≥ +0.5R
        bars.append(_bar(49, 110.5, 103, 110))
        res = replay.time_stop_4h(_trade(), bars)
        assert res["exit_reason"] == "tp"
        assert res["net_r"] == Decimal("1.9748")


class TestDataEnd:
    def test_exit_at_last_close_when_bars_run_out(self):
        bars = [_bar(i, 101.5, 99.5, 101) for i in range(3)]
        res = replay.baseline_2r(_trade(), bars)
        assert res["exit_reason"] == "data_end"
        # Market-style at last close 101: fill 100.9495
        assert res["exit_price"] == Decimal("100.9495")
        assert res["net_r"] == Decimal("0.16578606")
        assert res["hold_minutes"] == 15.0


class TestCosts:
    def test_costs_reduce_net_r_below_gross(self):
        # A clean 2R winner nets < 2R (fees on both legs)…
        win = replay.baseline_2r(_trade(), [_bar(0, 110, 99, 109)])
        assert win["exit_reason"] == "tp"
        assert win["net_r"] < Decimal("2")
        # …and a stop-out nets below −1R (fees + slippage).
        loss = replay.baseline_2r(_trade(), [_bar(0, 101, 94, 96)])
        assert loss["exit_reason"] == "stop"
        assert loss["net_r"] < Decimal("-1")


class TestRunAllVariants:
    def test_returns_all_four_variants_in_order(self):
        bars = [_bar(i, 101.5, 99.5, 101) for i in range(3)]
        results = replay.run_all_variants(_trade(), bars)
        assert [r["variant"] for r in results] == list(replay.VARIANTS)

    def test_no_usable_bars_returns_empty(self):
        # All bars pre-date the entry — nothing to simulate.
        bars = [{"t": T0 - BAR_MS, "o": 100, "h": 101, "l": 99, "c": 100}]
        assert replay.run_all_variants(_trade(), bars) == []

    def test_degenerate_trade_returns_empty(self):
        trade = _trade(entry="100", stop="100")  # zero risk
        assert replay.run_all_variants(trade, [_bar(0, 101, 99, 100)]) == []
