"""MajorsBot — pure strategy logic. Zero I/O.

Faithful port of the two bake-off survivors (majors_bakeoff/bakeoff.py):

  volevent (F4-A)     1h bar with |return| ≥ 3× trailing-30d mean TR% AND
                      volume ≥ 3× trailing-30d median volume → limit at the
                      50% retrace of the trigger bar, WITH the move. Stop at
                      the trigger bar's adverse extreme, floored at 1% of the
                      fill. 50% off at +1.5R (maker limit); runner trails 1R
                      behind the peak once +1R is reached.

  fundingfade (F1-B)  funding-rate percentile over the trailing 90 events
                      (30d): ≥99th → SHORT, ≤1st → LONG, market at the event
                      bar's open. Stop 1.5×ATR(24×1h). Exit when a later
                      funding event prints back inside the 40–60th band, or on
                      a 2×ATR peak-trail armed at +1R.

Walk semantics mirror the bake-off's pessimistic replay: within a bar the
(pre-bar) stop is checked first and wins ties; limit TPs fill flat at their
price; stops take a gap penalty on non-entry bars (min/max of bar open and
stop); peak/ratchet updates take effect on LATER bars only.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

VOLEVENT = "volevent"
FUNDINGFADE = "fundingfade"

# --- volevent constants (bake-off F4, variant A) ---
VOLEVENT_LOOKBACK_BARS = 720          # trailing 30d of 1h bars, window ends before trigger
VOLEVENT_RET_ATR_MULT = 3.0           # |bar return| ≥ 3× mean TR%
VOLEVENT_VOL_MULT = 3.0               # volume ≥ 3× median
VOLEVENT_RETRACE_DEPTH = 0.5          # limit at the trigger-bar midpoint
VOLEVENT_FILL_WINDOW_HOURS = 6        # cancel unfilled after 6 bars
VOLEVENT_MIN_STOP_PCT = Decimal("0.01")
VOLEVENT_TP_R = Decimal("1.5")        # 50% off here
VOLEVENT_PARTIAL_FRACTION = Decimal("0.5")
VOLEVENT_TRAIL_ARM_R = Decimal("1.0")
VOLEVENT_TRAIL_DIST_R = Decimal("1.0")

# --- fundingfade constants (bake-off F1, variant B) ---
FUNDING_WINDOW_EVENTS = 90            # trailing 30d of 8h events, incl. current
FF_HI_PCTILE = 0.99
FF_LO_PCTILE = 0.01
FF_BAND_LO = 0.40
FF_BAND_HI = 0.60
FF_STOP_ATR_MULT = Decimal("1.5")
FF_TRAIL_ATR_MULT = Decimal("2.0")
FF_TRAIL_ARM_R = Decimal("1.0")
FF_ATR_WINDOW = 24
FF_MAX_ENTRY_LAG_MS = 2 * 3_600_000   # skip events discovered >2h late

CLOSE_STOP = "stop"
CLOSE_TRAIL = "trail"
CLOSE_FUNDING_NORM = "funding_norm"
CLOSE_MAX_HOLD = "max_hold"
CLOSE_EXPIRED = "expired"
CLOSE_MAX_CONCURRENT = "max_concurrent"


@dataclass(frozen=True)
class PositionState:
    """Per-bar walk state, persisted across ticks via majorsbot_trades columns
    (stop_price / peak_price / partial_exit_at). peak is None until the trail
    arms — mirrors the bake-off, where the peak only starts tracking once
    favorable excursion reaches arm_r × risk."""

    stop: Decimal
    peak: Decimal | None
    partial_taken: bool


# ---------- volevent signal + entry math ----------


def volevent_signal(bars: list[dict]) -> dict | None:
    """Evaluate the LAST bar of ``bars`` (completed 1h bars, oldest-first) as a
    vol-event trigger. Returns None or a signal dict.

    Requires 722 bars so every TR in the prior-720 window has a previous close
    (the bake-off's roll windows behave identically once history is deep).
    """
    n = len(bars)
    i = n - 1
    if n < VOLEVENT_LOOKBACK_BARS + 2:
        return None
    from app.modules.majorsbot import data as _data  # pure helpers only

    ap = _data.mean_tr_pct(bars, i, VOLEVENT_LOOKBACK_BARS)
    if ap is None or ap <= 0:
        return None
    bar = bars[i]
    if bar["o"] <= 0:
        return None
    ret = (bar["c"] - bar["o"]) / bar["o"]
    if abs(ret) < VOLEVENT_RET_ATR_MULT * ap:
        return None
    med = _data.median_volume(bars, i, VOLEVENT_LOOKBACK_BARS)
    if med is None or med <= 0 or bar["v"] < VOLEVENT_VOL_MULT * med:
        return None
    direction = "long" if ret > 0 else "short"
    return {
        "direction": direction,
        "trigger_ts": int(bar["t"]),
        "trigger_high": bar["h"],
        "trigger_low": bar["l"],
        "limit_price": (bar["h"] + bar["l"]) / 2,
        "ret": ret,
        "mean_tr_pct": ap,
        "vol_mult": (bar["v"] / med) if med else None,
    }


def limit_fill_price(direction: str, bar_open: Decimal, limit: Decimal) -> Decimal:
    """Gap-aware limit fill: a bar opening beyond the limit fills at the open
    (better price) — the bake-off's min(o, mid) / max(o, mid)."""
    if direction == "long":
        return min(bar_open, limit)
    return max(bar_open, limit)


def is_limit_touched(direction: str, bar_high: Decimal, bar_low: Decimal, limit: Decimal) -> bool:
    if direction == "long":
        return bar_low <= limit
    return bar_high >= limit


def volevent_stop(
    direction: str, fill_price: Decimal, trigger_extreme: Decimal
) -> tuple[Decimal, Decimal]:
    """(stop, risk): risk = max(|fill − trigger adverse extreme|, 1% of fill)."""
    risk = max(abs(fill_price - trigger_extreme), VOLEVENT_MIN_STOP_PCT * fill_price)
    stop = fill_price - risk if direction == "long" else fill_price + risk
    return stop, risk


def take_profit_price(direction: str, entry: Decimal, risk: Decimal, r_mult: Decimal) -> Decimal:
    return entry + r_mult * risk if direction == "long" else entry - r_mult * risk


# ---------- fundingfade signal + entry math ----------


def funding_percentile(window_rates: list[float]) -> float | None:
    """Percentile of the LAST rate within the window (inclusive):
    count(x ≤ current) / len. The bake-off uses exactly this over 90 events."""
    if not window_rates:
        return None
    v = window_rates[-1]
    return sum(1 for x in window_rates if x <= v) / len(window_rates)


def fundingfade_direction(pctile: float) -> str | None:
    """≥99th percentile funding → SHORT the crowded longs; ≤1st → LONG."""
    if pctile >= FF_HI_PCTILE:
        return "short"
    if pctile <= FF_LO_PCTILE:
        return "long"
    return None


def in_normal_band(pctile: float) -> bool:
    return FF_BAND_LO <= pctile <= FF_BAND_HI


def fundingfade_stop(direction: str, entry: Decimal, atr: Decimal) -> tuple[Decimal, Decimal]:
    """(stop, risk): risk = 1.5 × ATR(24×1h)."""
    risk = FF_STOP_ATR_MULT * atr
    stop = entry - risk if direction == "long" else entry + risk
    return stop, risk


def trail_distance_for(strategy: str, risk: Decimal) -> Decimal:
    """Trail distance in PRICE units, derived from the frozen initial risk.

    volevent:    1R behind the peak → distance = risk.
    fundingfade: 2×ATR where risk = 1.5×ATR → distance = risk × (2 / 1.5).
    """
    if strategy == FUNDINGFADE:
        return risk * FF_TRAIL_ATR_MULT / FF_STOP_ATR_MULT
    return risk * VOLEVENT_TRAIL_DIST_R


# ---------- shared per-bar walk (bake-off walk_tp/walk_trail semantics) ----------


def stop_fill_raw(
    direction: str, bar_open: Decimal, stop: Decimal, is_entry_bar: bool
) -> Decimal:
    """Raw stop fill before slippage. On the entry bar the stop fills exactly;
    later bars take the gap penalty (an open beyond the stop fills at the open)."""
    if is_entry_bar:
        return stop
    if direction == "long":
        return min(bar_open, stop)
    return max(bar_open, stop)


def step_position_bar(
    direction: str,
    entry: Decimal,
    initial_stop: Decimal,
    state: PositionState,
    bar_open: Decimal,
    bar_high: Decimal,
    bar_low: Decimal,
    *,
    is_entry_bar: bool,
    tp_price: Decimal | None,
    trail_arm_r: Decimal,
    trail_distance: Decimal | None,
) -> tuple[PositionState, list[tuple]]:
    """Advance one closed 1h bar. Events, in order:
      ("close", raw_px, reason)  — stop/trail hit; reason "trail" when the stop
                                   had ratcheted off the initial, else "stop".
      ("partial", tp_price)      — 50% limit TP filled (volevent only).

    Ordering encodes the bake-off's pessimism: the PRE-bar stop is checked
    first and wins ties with the TP; peak/ratchet update happens after, so a
    new trail level can only stop us out on a LATER bar.
    """
    risk = abs(entry - initial_stop)
    stop_hit = bar_low <= state.stop if direction == "long" else bar_high >= state.stop
    if stop_hit:
        raw = stop_fill_raw(direction, bar_open, state.stop, is_entry_bar)
        reason = CLOSE_TRAIL if state.stop != initial_stop else CLOSE_STOP
        return state, [("close", raw, reason)]

    events: list[tuple] = []
    partial_taken = state.partial_taken
    if tp_price is not None and not partial_taken:
        tp_hit = bar_high >= tp_price if direction == "long" else bar_low <= tp_price
        if tp_hit:
            events.append(("partial", tp_price))
            partial_taken = True

    peak = state.peak
    stop = state.stop
    if trail_distance is not None and risk > 0:
        favorable = bar_high if direction == "long" else bar_low
        if peak is None:
            excursion = (favorable - entry) if direction == "long" else (entry - favorable)
            if excursion >= trail_arm_r * risk:
                peak = favorable
        else:
            peak = max(peak, favorable) if direction == "long" else min(peak, favorable)
        if peak is not None:
            cand = peak - trail_distance if direction == "long" else peak + trail_distance
            stop = max(stop, cand) if direction == "long" else min(stop, cand)

    return PositionState(stop=stop, peak=peak, partial_taken=partial_taken), events


# ---------- costs / accounting ----------


def adverse_slippage_price(direction: str, price: Decimal, slippage_pct: Decimal) -> Decimal:
    """Market-style exits slip against us — sell lower (long), buy higher (short)."""
    if slippage_pct <= 0:
        return price
    if direction == "long":
        return price * (Decimal("1") - slippage_pct)
    return price * (Decimal("1") + slippage_pct)


def leg_fees(
    entry_price: Decimal,
    exit_price: Decimal,
    qty: Decimal,
    entry_fee_pct: Decimal,
    exit_fee_pct: Decimal,
) -> Decimal:
    """Entry-share + exit fee for one leg — the bake-off's per-leg fee model."""
    return entry_price * qty * entry_fee_pct + exit_price * qty * exit_fee_pct


def realized_pnl(direction: str, entry: Decimal, exit_price: Decimal, qty: Decimal) -> Decimal:
    delta = (exit_price - entry) if direction == "long" else (entry - exit_price)
    return delta * qty


def funding_event_pnl(direction: str, rate: Decimal, price: Decimal, qty: Decimal) -> Decimal:
    """One 8h funding event: longs PAY a positive rate, shorts receive it."""
    transfer = rate * price * qty
    return -transfer if direction == "long" else transfer


def net_r_multiple(
    net_pnl_usd: Decimal, entry: Decimal, initial_stop: Decimal, qty: Decimal
) -> Decimal:
    """Net PnL (fees + slippage + funding included) over dollar-risk at entry."""
    risk_usd = abs(entry - initial_stop) * qty
    if risk_usd == 0:
        return Decimal("0")
    return net_pnl_usd / risk_usd


def compute_qty(
    *,
    paper_equity: Decimal,
    risk_per_trade_pct: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    max_notional_pct: Decimal,
) -> Decimal:
    """Risk-normalized qty, notional-capped:
    qty = equity × risk% / |entry − stop|, capped at equity × cap% / entry."""
    if paper_equity <= 0 or entry_price <= 0:
        return Decimal("0")
    stop_dist = abs(entry_price - stop_price)
    if stop_dist <= 0:
        return Decimal("0")
    qty = paper_equity * risk_per_trade_pct / stop_dist
    cap_qty = paper_equity * max_notional_pct / entry_price
    return min(qty, cap_qty)
