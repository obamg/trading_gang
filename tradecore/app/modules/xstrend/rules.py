"""Weekly cross-sectional trend — the rules, as pure functions.

Ported line for line from docs/research/scripts/xs_trend.py, the study that
passed its backtest. The forward test is pre-registered in
docs/research/2026-11-13-weekly-xs-trend-forward.md: 104 weeks, judged once.
These are constants on purpose (not env-tunable). VERSION hashes this file, so
any edit here starts a new record.

`bars` everywhere is {symbol: {day_ms: (open, close, turnover)}}.
"""
from __future__ import annotations

import hashlib
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

DAY = 86_400_000
WEEK = 7 * DAY
FIRST_WEEK = int(datetime(2026, 11, 16, tzinfo=timezone.utc).timestamp() * 1000)
STABLE = frozenset({"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDE", "PYUSD"})
N_UNI, N_LEG, COST, Z = 30, 10, 0.0010, 1.28
MIN_BARS, HORIZONS = 90, (7, 14, 28)
N_WEEKS, RULED_OUT_BELOW = 104, 0.0010
VERSION = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]


def pick(bars: dict, t: int) -> tuple[list[str], list[str], list[str]] | None:
    """(universe, longs, shorts) at rebalance time t, from bars strictly before t."""
    elig = []
    for s, b in bars.items():
        if any((t - k * DAY) not in b for k in range(1, 31)):
            continue
        if sum(1 for d in b if d < t) < MIN_BARS:
            continue
        elig.append((sum(b[t - k * DAY][2] for k in range(1, 31)), s))
    uni = sorted(s for _, s in sorted(elig, reverse=True)[:N_UNI])
    if len(uni) < N_UNI:
        return None
    score = {s: 0 for s in uni}  # integer rank sums: float averages broke exact ties
    for h in HORIZONS:
        ret = {s: bars[s][t - DAY][1] / bars[s][t - (h + 1) * DAY][1] - 1 for s in uni}
        for rank, s in enumerate(sorted(uni, key=lambda s: (ret[s], s))):
            score[s] += rank
    order = sorted(uni, key=lambda s: (score[s], s))
    return uni, order[-N_LEG:], order[:N_LEG]


def weights(longs: list[str], shorts: list[str]) -> dict[str, float]:
    return {**{s: 0.5 / N_LEG for s in longs}, **{s: -0.5 / N_LEG for s in shorts}}


def week_return(b: dict, t: int) -> float:
    """Monday open to next Monday open; a coin with no bar at the exit uses its last close."""
    if t not in b:
        return 0.0
    if t + WEEK in b:
        return b[t + WEEK][0] / b[t][0] - 1
    last = max(d for d in b if t <= d < t + WEEK)
    return b[last][1] / b[t][0] - 1


def turnover(w: dict[str, float], prev: dict[str, float]) -> float:
    """Notional traded going from last week's drifted weights to this week's."""
    return sum(abs(w.get(s, 0.0) - prev.get(s, 0.0)) for s in set(w) | set(prev))


def settle(w: dict[str, float], uni: list[str], bars: dict, fund: dict[str, float], t: int, turn: float) -> dict:
    """One week's result. `fund` = summed funding rate per symbol over the week (longs pay a positive rate)."""
    r = {s: week_return(bars.get(s, {}), t) for s in set(w) | set(uni)}
    gross = sum(w[s] * r[s] for s in w)
    funding = sum(w[s] * fund.get(s, 0.0) for s in w)
    return {
        "gross": gross, "cost": COST * turn, "funding": funding,
        "net": gross - COST * turn - funding,
        "mkt": st.fmean(r[s] for s in uni),
        "returns": r,
        "drifted": {s: w[s] * (1 + r[s]) for s in w},
    }


def scoreboard(nets: list[float]) -> dict:
    """Running record against the pre-registered test. Only the first N_WEEKS count."""
    nets = nets[:N_WEEKS]
    n = len(nets)
    out = {"n": n, "target": N_WEEKS, "verdict": "running", "mean": None, "sd": None, "bar": None,
           "low": None, "high": None, "total": sum(nets), "drawdown": 0.0, "win_rate": None,
           "top5_share": None}
    eq = peak = 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        out["drawdown"] = min(out["drawdown"], eq - peak)
    if n < 2:
        return out
    m, se = st.fmean(nets), st.stdev(nets) / n ** 0.5
    out.update(mean=m, sd=st.stdev(nets), bar=Z * se, low=m - Z * se, high=m + Z * se,
               win_rate=sum(x > 0 for x in nets) / n)
    if eq > 0:
        out["top5_share"] = sum(sorted(nets)[-5:]) / eq
    if n >= N_WEEKS:
        halves = st.fmean(nets[:n // 2]) > 0 and st.fmean(nets[n // 2:]) > 0
        out["verdict"] = ("pass" if m > Z * se and halves
                          else "ruled_out" if m + Z * se < RULED_OUT_BELOW else "inconclusive")
    return out
