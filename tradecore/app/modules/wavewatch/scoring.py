"""Pre-wave scoring — pure functions.

Takes a list of 5m candles (newest-first, matching ``redis_service.get_candles``
output) and optional funding rate. Returns:

  score    — composite readiness 0..1
  onset    — bool, is the wave starting right now?
  components — per-feature contributions, for debug and the API

Score components (each clamped 0..1, then weight-summed):

  vol_baseline_rising   recent 1h avg volume vs preceding 3h baseline
  green_ratio           proportion of green candles in last 12 (buy bias proxy)
  range_compression     last-12 stdev vs 12-before stdev (Bollinger squeeze)
  higher_lows           are the last 6 candle lows trending up?
  funding_warmup        funding flipping from negative to flat/positive (perps)

Onset condition (must ALL hold):

  current 5m volume ≥ 3× rolling 4h median volume
  current candle closes higher than the prior 12 candles' close
  current candle is green
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median, pstdev


# Weights sum to 1.0 for perps; for spot (funding=None) weights renormalize.
WEIGHTS = {
    "vol_baseline_rising": 0.25,
    "green_ratio": 0.25,
    "range_compression": 0.20,
    "higher_lows": 0.15,
    "funding_warmup": 0.15,
}

ONSET_VOL_RATIO = 3.0
ONSET_LOOKBACK = 12


@dataclass(frozen=True)
class Score:
    score: float
    onset: bool
    components: dict[str, float]
    vol_ratio_now: float  # latest candle vol / 4h median — used for ranking


def _candle_volume(c: dict) -> float:
    # Prefer quote turnover (USD-ish); fall back to base volume.
    try:
        v = float(c.get("q") or 0.0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    try:
        return float(c.get("v") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _closes(candles: list[dict]) -> list[float]:
    out: list[float] = []
    for c in candles:
        try:
            out.append(float(c["c"]))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _lows(candles: list[dict]) -> list[float]:
    out: list[float] = []
    for c in candles:
        try:
            out.append(float(c["l"]))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _opens(candles: list[dict]) -> list[float]:
    out: list[float] = []
    for c in candles:
        try:
            out.append(float(c["o"]))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def compute_score(candles: list[dict], funding_pct: float | None) -> Score | None:
    """``candles`` newest-first (Redis lrange order). Needs ≥24 candles
    (~2h of 5m) — returns None otherwise so detector skips the symbol."""
    if len(candles) < 24:
        return None

    # Reverse to chronological for everything that cares about order.
    chrono = list(reversed(candles))
    closes = _closes(chrono)
    opens = _opens(chrono)
    lows = _lows(chrono)
    vols = [_candle_volume(c) for c in chrono]

    if not closes or not vols:
        return None

    components: dict[str, float] = {}

    # --- 1. volume baseline rising ---
    # Last 1h = last 12 candles. Prior 3h = the 36 before that.
    recent_vols = vols[-12:]
    older_vols = vols[-48:-12] if len(vols) >= 48 else vols[:-12]
    avg_recent = sum(recent_vols) / max(len(recent_vols), 1)
    avg_older = sum(older_vols) / max(len(older_vols), 1) if older_vols else 0.0
    if avg_older > 0:
        ratio = avg_recent / avg_older
        # Map: 1.0 → 0, 1.5 → 1.0, 2.0+ → 1.0 (saturate)
        components["vol_baseline_rising"] = max(0.0, min(1.0, (ratio - 1.0) / 0.5))
    else:
        components["vol_baseline_rising"] = 0.0

    # --- 2. green candle ratio over last 12 ---
    n = min(12, len(closes), len(opens))
    last_closes = closes[-n:]
    last_opens = opens[-n:]
    greens = sum(1 for c, o in zip(last_closes, last_opens) if c > o)
    # Map: 0.5 → 0, 0.75 → 0.5, 1.0 → 1.0
    ratio = greens / n if n else 0.0
    components["green_ratio"] = max(0.0, min(1.0, (ratio - 0.5) / 0.5))

    # --- 3. range compression: last-12 stdev vs 12-before stdev ---
    if len(closes) >= 24:
        last_std = pstdev(closes[-12:]) or 1e-9
        prev_std = pstdev(closes[-24:-12]) or 1e-9
        compress = prev_std / last_std  # >1 means tighter now
        # Map: 1.0 → 0, 2.0 → 1.0
        components["range_compression"] = max(0.0, min(1.0, (compress - 1.0)))
    else:
        components["range_compression"] = 0.0

    # --- 4. higher lows on last 6 candles ---
    if len(lows) >= 6:
        last_lows = lows[-6:]
        ups = sum(
            1 for a, b in zip(last_lows[:-1], last_lows[1:]) if b >= a
        )
        # Map: 3 → 0, 5 → 1.0
        components["higher_lows"] = max(0.0, min(1.0, (ups - 3) / 2))
    else:
        components["higher_lows"] = 0.0

    # --- 5. funding warmup (perps only) ---
    if funding_pct is not None:
        # Reward funding in the [-0.0005, +0.0005] zone — neutral after a
        # likely-negative stretch. Penalize extreme positive (overheated)
        # or extreme negative (bears still dominant).
        f = funding_pct
        if -0.0005 <= f <= 0.0005:
            components["funding_warmup"] = 1.0
        elif 0.0005 < f <= 0.002:
            components["funding_warmup"] = 0.5
        elif -0.002 <= f < -0.0005:
            components["funding_warmup"] = 0.5
        else:
            components["funding_warmup"] = 0.0
    else:
        components["funding_warmup"] = 0.0

    # --- weighted sum ---
    if funding_pct is None:
        active_weights = {k: v for k, v in WEIGHTS.items() if k != "funding_warmup"}
        total_w = sum(active_weights.values())
        score = sum(components[k] * w / total_w for k, w in active_weights.items())
    else:
        score = sum(components[k] * w for k, w in WEIGHTS.items())

    # --- onset ---
    latest_vol = vols[-1] if vols else 0.0
    baseline_vols = vols[-49:-1] if len(vols) >= 49 else vols[:-1]
    baseline = median(baseline_vols) if baseline_vols else 0.0
    vol_ratio_now = (latest_vol / baseline) if baseline > 0 else 0.0

    latest_close = closes[-1]
    prior_closes = closes[-13:-1] if len(closes) >= 13 else closes[:-1]
    breaks_high = bool(prior_closes) and latest_close > max(prior_closes)
    latest_open = opens[-1] if opens else latest_close
    is_green = latest_close > latest_open

    onset = (
        vol_ratio_now >= ONSET_VOL_RATIO
        and breaks_high
        and is_green
    )

    return Score(
        score=round(score, 3),
        onset=onset,
        components={k: round(v, 3) for k, v in components.items()},
        vol_ratio_now=round(vol_ratio_now, 2),
    )


__all__ = ["Score", "compute_score", "WEIGHTS", "ONSET_VOL_RATIO"]
