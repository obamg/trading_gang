"""Pure-function signal evaluators for the post-listing watcher.

Each evaluator takes a ``WatcherCtx`` (live state for one listing) and
returns a ``Signal | None``. Evaluators are deterministic and side-effect
free — the watcher handles persistence, cooldowns, and alerts.

The five signals (v1):
  1. ``pump_fade``         — short setup after the initial spike
  2. ``breakout_long``     — long setup on confirmed 1h-high break
  3. ``initial_squeeze``   — extreme volume in the first 5 minutes (info)
  4. ``funding_extreme``   — funding > +0.5% (8h equiv) or < −0.5%
  5. ``floor_held``        — 30m-low untouched ≥ 30min → support confirmed
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WatcherCtx:
    """Snapshot of one listing's live state, computed by the watcher each tick."""

    symbol: str               # data-source symbol (Bybit perp ticker)
    seconds_since_t0: int
    t0_price: float
    last_price: float
    high_15m: float
    low_15m: float
    high_1h: float
    low_1h: float
    cvd_5m_usd: float         # signed: positive = aggressive buys dominate
    volume_5m_usd: float      # raw notional
    volume_5m_baseline_usd: float  # rolling median 5m bucket prior to last
    funding_pct: float | None # 8h equivalent, e.g. 0.005 = 0.5%
    floor_set_seconds_ago: int | None  # how long ago the current 30m low was set
    is_cross_listing: bool


@dataclass
class Signal:
    type: str
    direction: str   # long | short | neutral
    conviction: float  # 0..1
    context: dict


# ---------- 1. pump_fade ----------


PUMP_FADE_DROP = 0.05        # drop ≥5% from rolling 15m high
PUMP_FADE_MIN_AGE_S = 600    # at least 10min since T-0
PUMP_FADE_MIN_PUMP = 0.20    # initial pump from t0 must have been ≥20%


def evaluate_pump_fade(ctx: WatcherCtx) -> Signal | None:
    if ctx.seconds_since_t0 < PUMP_FADE_MIN_AGE_S:
        return None
    # Was there a real pump to fade?
    pump = (ctx.high_15m / ctx.t0_price - 1) if ctx.t0_price > 0 else 0
    if pump < PUMP_FADE_MIN_PUMP:
        return None
    # Drop from the 15m high.
    drop = (ctx.high_15m - ctx.last_price) / ctx.high_15m if ctx.high_15m > 0 else 0
    if drop < PUMP_FADE_DROP:
        return None
    # Sustained negative aggressor flow confirms.
    if ctx.cvd_5m_usd >= 0:
        return None
    # Conviction: scale on the size of the drop and the sign of CVD.
    conviction = min(1.0, 0.4 + drop * 4 + min(abs(ctx.cvd_5m_usd) / 1_000_000, 0.4))
    return Signal(
        type="pump_fade",
        direction="short",
        conviction=round(conviction, 3),
        context={
            "pump_pct": round(pump, 4),
            "drop_pct": round(drop, 4),
            "cvd_5m_usd": round(ctx.cvd_5m_usd, 2),
            "high_15m": ctx.high_15m,
            "last_price": ctx.last_price,
        },
    )


# ---------- 2. breakout_long ----------


BREAKOUT_MIN_AGE_S = 1800        # need at least 30min of data to have a meaningful 1h-high
BREAKOUT_MIN_VOL_RATIO = 1.5     # current 5m volume > 1.5× baseline


def evaluate_breakout_long(ctx: WatcherCtx) -> Signal | None:
    if ctx.seconds_since_t0 < BREAKOUT_MIN_AGE_S:
        return None
    if ctx.high_1h <= 0 or ctx.last_price <= ctx.high_1h:
        return None  # not actually breaking out
    if ctx.volume_5m_baseline_usd <= 0:
        return None
    vol_ratio = ctx.volume_5m_usd / ctx.volume_5m_baseline_usd
    if vol_ratio < BREAKOUT_MIN_VOL_RATIO:
        return None
    if ctx.cvd_5m_usd <= 0:
        return None  # no buyer pressure
    conviction = min(1.0, 0.5 + min((vol_ratio - 1) * 0.2, 0.3) + min(ctx.cvd_5m_usd / 2_000_000, 0.2))
    return Signal(
        type="breakout_long",
        direction="long",
        conviction=round(conviction, 3),
        context={
            "high_1h": ctx.high_1h,
            "last_price": ctx.last_price,
            "vol_ratio": round(vol_ratio, 2),
            "cvd_5m_usd": round(ctx.cvd_5m_usd, 2),
        },
    )


# ---------- 3. initial_squeeze ----------


SQUEEZE_WINDOW_S = 300            # only fire in the first 5min
SQUEEZE_MIN_VOL_USD = 500_000     # absolute floor — don't fire on tiny dust listings
SQUEEZE_VOL_RATIO = 5.0           # vs baseline (or absolute when baseline is zero)


def evaluate_initial_squeeze(ctx: WatcherCtx) -> Signal | None:
    if ctx.seconds_since_t0 > SQUEEZE_WINDOW_S:
        return None
    if ctx.volume_5m_usd < SQUEEZE_MIN_VOL_USD:
        return None
    # Two qualifying paths: ratio against baseline (when we have one) OR
    # large absolute notional in the first window.
    qualifies_ratio = (
        ctx.volume_5m_baseline_usd > 0
        and ctx.volume_5m_usd / ctx.volume_5m_baseline_usd >= SQUEEZE_VOL_RATIO
    )
    qualifies_absolute = ctx.volume_5m_usd >= 5_000_000
    if not (qualifies_ratio or qualifies_absolute):
        return None
    direction = "long" if ctx.cvd_5m_usd > 0 else "short" if ctx.cvd_5m_usd < 0 else "neutral"
    conviction = min(1.0, 0.55 + min(ctx.volume_5m_usd / 20_000_000, 0.4))
    return Signal(
        type="initial_squeeze",
        direction=direction,
        conviction=round(conviction, 3),
        context={
            "volume_5m_usd": round(ctx.volume_5m_usd, 2),
            "cvd_5m_usd": round(ctx.cvd_5m_usd, 2),
            "qualifies_ratio": qualifies_ratio,
            "qualifies_absolute": qualifies_absolute,
        },
    )


# ---------- 4. funding_extreme ----------


FUNDING_EXTREME_PCT = 0.005   # 0.5% (8h equivalent)


def evaluate_funding_extreme(ctx: WatcherCtx) -> Signal | None:
    if ctx.funding_pct is None:
        return None
    if abs(ctx.funding_pct) < FUNDING_EXTREME_PCT:
        return None
    # Positive funding = longs paying shorts → over-positioned long → fade is short.
    direction = "short" if ctx.funding_pct > 0 else "long"
    # Conviction climbs steeply past the threshold.
    conviction = min(1.0, 0.5 + (abs(ctx.funding_pct) - FUNDING_EXTREME_PCT) * 80)
    return Signal(
        type="funding_extreme",
        direction=direction,
        conviction=round(conviction, 3),
        context={
            "funding_pct": ctx.funding_pct,
        },
    )


# ---------- 5. floor_held ----------


FLOOR_HELD_MIN_AGE_S = 1800       # at least 30min in
FLOOR_HELD_MIN_FLOOR_AGE_S = 1800 # 30m-low must be ≥30min old (untouched)
FLOOR_HELD_PROXIMITY = 0.05       # current price within 5% of the floor


def evaluate_floor_held(ctx: WatcherCtx) -> Signal | None:
    if ctx.seconds_since_t0 < FLOOR_HELD_MIN_AGE_S:
        return None
    if ctx.floor_set_seconds_ago is None or ctx.floor_set_seconds_ago < FLOOR_HELD_MIN_FLOOR_AGE_S:
        return None
    if ctx.low_1h <= 0:
        return None
    proximity = abs(ctx.last_price - ctx.low_1h) / ctx.low_1h
    if proximity > FLOOR_HELD_PROXIMITY:
        return None
    return Signal(
        type="floor_held",
        direction="long",
        conviction=0.55,
        context={
            "low_1h": ctx.low_1h,
            "floor_age_s": ctx.floor_set_seconds_ago,
            "proximity_pct": round(proximity, 4),
        },
    )


EVALUATORS = [
    evaluate_pump_fade,
    evaluate_breakout_long,
    evaluate_initial_squeeze,
    evaluate_funding_extreme,
    evaluate_floor_held,
]


def evaluate_all(ctx: WatcherCtx) -> list[Signal]:
    out: list[Signal] = []
    for fn in EVALUATORS:
        s = fn(ctx)
        if s is not None:
            out.append(s)
    return out
