"""WaveBot — exit-engineering replay (pure simulation).

Zero I/O — like strategy.py, every function is a deterministic map from
(trade, bars) to an exit result, so the whole variant grid is unit-testable
without a database, Redis, or the network.

Given a closed (or hypothetical) trade and the chronological 5m bars that
followed its entry, each variant re-plays the exit bar-by-bar under a
different management rule and reports the net R it would have produced.

Conventions (deliberately pessimistic — paper should err worse than reality,
matching monitor.py):

  * Bars are dicts with ``t`` (epoch ms) + ``o``/``h``/``l``/``c``; values may
    be floats or Decimals. Simulation starts at the first bar with
    ``t >= entry_at``.
  * Intra-bar tie: if a bar touches both the stop and the TP, the stop wins.
  * State armed by a bar (breakeven move, trail ratchet) only takes effect on
    *subsequent* bars — a bar can't rescue itself.
  * Costs: taker fee 0.0006 per side on both legs; adverse slippage 0.0005 on
    market-style exits (stop / breakeven / trail / time / data_end) but NOT
    on limit TPs.
  * ``net_r`` = net PnL / (|entry − stop| × qty) with qty = 1 — R is
    scale-free, so one unit is enough.
  * If bars run out with the position still open, exit at the last bar's
    close (reason ``data_end``, market-style costs).
  * Funding is NOT modeled — see app/scripts/replay_exits.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

TAKER_FEE_PCT = Decimal("0.0006")  # per side, charged on both legs
SLIPPAGE_PCT = Decimal("0.0005")  # adverse, market-style exits only
BAR_MS = 5 * 60 * 1000  # 5m bars

BASELINE_TP_R = Decimal("2")
BE_ARM_R = Decimal("1")
PARTIAL_TP_R = Decimal("1.5")
TRAIL_ARM_R = Decimal("1")
TRAIL_DISTANCE_R = Decimal("1")
TIME_STOP_MS = 4 * 60 * 60 * 1000
TIME_STOP_MIN_R = Decimal("0.5")

VARIANTS = ("baseline_2r", "be_at_1r", "partial_trail", "time_stop_4h")


def to_epoch_ms(value) -> int:
    """Epoch milliseconds from a datetime, ISO-8601 string, or numeric ms."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return int(value)


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


@dataclass(frozen=True)
class _Sim:
    """Normalized trade context shared by every variant."""

    is_long: bool
    entry: Decimal
    stop: Decimal
    risk: Decimal  # |entry − stop|, per unit
    entry_ms: int


def _prepare(trade: dict, bars: list[dict]) -> tuple[_Sim | None, list[dict]]:
    """Validate the trade and slice bars to those on/after entry.

    Returns (None, []) for: unknown direction, degenerate risk, non-positive
    entry, or no bars at/after entry_at — callers surface that as None.
    """
    direction = str(trade.get("direction", "")).lower()
    if direction not in ("long", "short"):
        return None, []
    entry = _dec(trade["entry_price"])
    stop = _dec(trade["stop_price"])
    risk = abs(entry - stop)
    if entry <= 0 or risk <= 0:
        return None, []
    entry_ms = to_epoch_ms(trade["entry_at"])
    usable = []
    for b in bars:
        t = int(b["t"])
        if t < entry_ms:
            continue
        usable.append(
            {"t": t, "o": _dec(b["o"]), "h": _dec(b["h"]), "l": _dec(b["l"]), "c": _dec(b["c"])}
        )
    if not usable:
        return None, []
    return _Sim(direction == "long", entry, stop, risk, entry_ms), usable


# ---------- price geometry ----------


def _price_at_r(sim: _Sim, r: Decimal) -> Decimal:
    """entry + r×risk for longs, mirror for shorts."""
    return sim.entry + r * sim.risk if sim.is_long else sim.entry - r * sim.risk


def _r_at_price(sim: _Sim, price: Decimal) -> Decimal:
    move = (price - sim.entry) if sim.is_long else (sim.entry - price)
    return move / sim.risk


def _stop_hit(sim: _Sim, bar: dict, stop: Decimal) -> bool:
    return bar["l"] <= stop if sim.is_long else bar["h"] >= stop


def _tp_hit(sim: _Sim, bar: dict, tp: Decimal) -> bool:
    return bar["h"] >= tp if sim.is_long else bar["l"] <= tp


def _favorable_extreme(sim: _Sim, bar: dict) -> Decimal:
    return bar["h"] if sim.is_long else bar["l"]


# ---------- cost model ----------


def _fill_price(sim: _Sim, price: Decimal, market_style: bool) -> Decimal:
    """Worsen market-style fills by the slippage haircut; limit TPs fill flat."""
    if not market_style:
        return price
    if sim.is_long:
        return price * (Decimal("1") - SLIPPAGE_PCT)
    return price * (Decimal("1") + SLIPPAGE_PCT)


def _leg_net_r(sim: _Sim, raw_price: Decimal, market_style: bool) -> tuple[Decimal, Decimal]:
    """(fill_price, net_r) for one unit exiting at ``raw_price``."""
    fill = _fill_price(sim, raw_price, market_style)
    gross = (fill - sim.entry) if sim.is_long else (sim.entry - fill)
    fees = (sim.entry + fill) * TAKER_FEE_PCT  # qty=1 → notional == price
    return fill, (gross - fees) / sim.risk


def _result(
    sim: _Sim,
    variant: str,
    raw_price: Decimal,
    reason: str,
    bar: dict,
    market_style: bool,
) -> dict:
    fill, net_r = _leg_net_r(sim, raw_price, market_style)
    return {
        "variant": variant,
        "exit_price": fill,
        "exit_reason": reason,
        "net_r": net_r,
        # Held through the close of the exit bar (intra-bar timing is unknown).
        "hold_minutes": (bar["t"] + BAR_MS - sim.entry_ms) / 60_000.0,
    }


# ---------- shared bar-walk engine ----------


def _run_managed(
    sim: _Sim,
    bars: list[dict],
    variant: str,
    *,
    tp_r: Decimal,
    breakeven_arm_r: Decimal | None = None,
    time_stop_ms: int | None = None,
    time_stop_min_r: Decimal | None = None,
) -> dict:
    """Fixed-TP walk with optional breakeven move and one-shot time stop.

    Per-bar order (pessimistic): stop → TP → time check at close → arm
    breakeven for subsequent bars.
    """
    tp = _price_at_r(sim, tp_r)
    stop = sim.stop
    be_moved = False
    time_checked = False
    for bar in bars:
        if _stop_hit(sim, bar, stop):
            reason = "breakeven" if be_moved else "stop"
            return _result(sim, variant, stop, reason, bar, market_style=True)
        if _tp_hit(sim, bar, tp):
            return _result(sim, variant, tp, "tp", bar, market_style=False)
        if (
            time_stop_ms is not None
            and not time_checked
            and bar["t"] >= sim.entry_ms + time_stop_ms
        ):
            time_checked = True
            if _r_at_price(sim, bar["c"]) < (time_stop_min_r or Decimal("0")):
                return _result(sim, variant, bar["c"], "time", bar, market_style=True)
        if (
            breakeven_arm_r is not None
            and not be_moved
            and _r_at_price(sim, _favorable_extreme(sim, bar)) >= breakeven_arm_r
        ):
            stop = sim.entry
            be_moved = True
    return _result(sim, variant, bars[-1]["c"], "data_end", bars[-1], market_style=True)


def _run_trail(
    sim: _Sim,
    bars: list[dict],
    variant: str,
    *,
    arm_r: Decimal,
    trail_distance_r: Decimal,
) -> dict:
    """No-TP walk: initial stop, then a peak-following trail once ``arm_r`` is
    reached. Trail ratchets on bar close — the bar that makes a new peak can't
    stop itself out on that peak."""
    stop = sim.stop
    trail_dist = trail_distance_r * sim.risk
    peak: Decimal | None = None
    for bar in bars:
        if _stop_hit(sim, bar, stop):
            reason = "trail" if stop != sim.stop else "stop"
            return _result(sim, variant, stop, reason, bar, market_style=True)
        fav = _favorable_extreme(sim, bar)
        if peak is None:
            if _r_at_price(sim, fav) >= arm_r:
                peak = fav
        else:
            peak = max(peak, fav) if sim.is_long else min(peak, fav)
        if peak is not None:
            trail_stop = peak - trail_dist if sim.is_long else peak + trail_dist
            stop = max(stop, trail_stop) if sim.is_long else min(stop, trail_stop)
    return _result(sim, variant, bars[-1]["c"], "data_end", bars[-1], market_style=True)


# ---------- variants ----------


def baseline_2r(trade: dict, bars: list[dict]) -> dict | None:
    """Sanity anchor: fixed TP at +2R, stop where the bot placed it."""
    sim, usable = _prepare(trade, bars)
    if sim is None:
        return None
    return _run_managed(sim, usable, "baseline_2r", tp_r=BASELINE_TP_R)


def be_at_1r(trade: dict, bars: list[dict]) -> dict | None:
    """Baseline, but once a bar's favorable extreme reaches +1R the stop moves
    to entry (breakeven) for subsequent bars."""
    sim, usable = _prepare(trade, bars)
    if sim is None:
        return None
    return _run_managed(sim, usable, "be_at_1r", tp_r=BASELINE_TP_R, breakeven_arm_r=BE_ARM_R)


def partial_trail(trade: dict, bars: list[dict]) -> dict | None:
    """50% off at +1.5R (limit); the remainder trails at 1R behind the peak
    favorable price once +1R is reached. The two legs' net R blend 50/50.

    The blended result reports the later leg's exit price / hold (when the
    position is fully flat) and both legs under ``legs``.
    """
    sim, usable = _prepare(trade, bars)
    if sim is None:
        return None
    leg_tp = _run_managed(sim, usable, "partial_trail", tp_r=PARTIAL_TP_R)
    leg_trail = _run_trail(
        sim, usable, "partial_trail", arm_r=TRAIL_ARM_R, trail_distance_r=TRAIL_DISTANCE_R
    )
    later = leg_tp if leg_tp["hold_minutes"] >= leg_trail["hold_minutes"] else leg_trail
    return {
        "variant": "partial_trail",
        "exit_price": later["exit_price"],
        "exit_reason": f"{leg_tp['exit_reason']}/{leg_trail['exit_reason']}",
        "net_r": (leg_tp["net_r"] + leg_trail["net_r"]) / 2,
        "hold_minutes": later["hold_minutes"],
        "legs": [leg_tp, leg_trail],
    }


def time_stop_4h(trade: dict, bars: list[dict]) -> dict | None:
    """Baseline exits, plus: at the first bar ≥4h after entry, if unrealized
    (at that bar's close) is below +0.5R, exit at the close with market-style
    costs. One-shot — a trade that passes the check runs to stop/TP."""
    sim, usable = _prepare(trade, bars)
    if sim is None:
        return None
    return _run_managed(
        sim,
        usable,
        "time_stop_4h",
        tp_r=BASELINE_TP_R,
        time_stop_ms=TIME_STOP_MS,
        time_stop_min_r=TIME_STOP_MIN_R,
    )


def run_all_variants(trade: dict, bars: list[dict]) -> list[dict]:
    """All variant results for one trade; [] when the trade/bars don't validate."""
    results = []
    for fn in (baseline_2r, be_at_1r, partial_trail, time_stop_4h):
        res = fn(trade, bars)
        if res is not None:
            results.append(res)
    return results
