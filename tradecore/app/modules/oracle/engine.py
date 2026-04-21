"""Oracle — aggregates signals from all modules into a scored recommendation.

Pure detection + persistence logic. The router wires HTTP; the scheduler
wires outcome measurement; the alert bus triggers on-demand runs.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.oracle import OracleOutcome, OracleSignal
from app.models.gemradar import GemRadarAlert
from app.models.radarx import RadarXAlert
from app.models.sentiment import SentimentSnapshot
from app.models.whaleradar import OISurgeEvent, WhaleTrade
from app.modules.liquidmap.tracker import get_heatmap
from app.modules.macropulse.score import compute_macro_context
from app.services import redis_service

DEFAULT_WEIGHTS = {
    "macropulse": 25,
    "whaleradar": 20,
    "radarx": 15,
    "liquidmap": 15,
    "sentimentpulse": 15,
    "gemradar": 10,
}

# Majors where GemRadar doesn't apply.
MAJOR_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT",
}


def _dir(value: int) -> str:
    if value > 0:
        return "bullish"
    if value < 0:
        return "bearish"
    return "neutral"


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------- per-module signal collectors ----------

async def _radarx_signal(db: AsyncSession, symbol: str) -> dict:
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    row = (
        await db.execute(
            select(RadarXAlert)
            .where(RadarXAlert.symbol == symbol, RadarXAlert.triggered_at >= since)
            .order_by(desc(RadarXAlert.triggered_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return {"direction": "neutral", "intensity": 0.0, "detail": None}
    price_change = float(row.price_change_pct or 0)
    direction = "bullish" if price_change > 0 else ("bearish" if price_change < 0 else "neutral")
    intensity = _clip01(float(row.z_score) / 8.0)
    return {
        "direction": direction,
        "intensity": round(intensity, 3),
        "detail": {"z_score": float(row.z_score), "price_change_pct": price_change},
    }


async def _whaleradar_signal(db: AsyncSession, symbol: str) -> dict:
    since_trade = datetime.now(timezone.utc) - timedelta(minutes=10)
    trade = (
        await db.execute(
            select(WhaleTrade)
            .where(WhaleTrade.symbol == symbol, WhaleTrade.detected_at >= since_trade)
            .order_by(desc(WhaleTrade.detected_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    since_oi = datetime.now(timezone.utc) - timedelta(minutes=30)
    oi = (
        await db.execute(
            select(OISurgeEvent)
            .where(OISurgeEvent.symbol == symbol, OISurgeEvent.detected_at >= since_oi)
            .order_by(desc(OISurgeEvent.detected_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    if trade is None and oi is None:
        return {"direction": "neutral", "intensity": 0.0, "detail": None}

    direction_score = 0
    intensity = 0.0
    detail: dict = {}

    if trade is not None:
        side = (trade.side or "").lower()
        t_dir = 1 if side == "buy" else (-1 if side == "sell" else 0)
        direction_score += t_dir
        intensity += _clip01(float(trade.trade_size_usd) / 2_000_000.0)
        detail["trade"] = {"side": side, "size_usd": float(trade.trade_size_usd)}

    if oi is not None:
        oi_dir_raw = (oi.direction or "").lower()
        if oi_dir_raw == "long_heavy":
            oi_dir = 1
        elif oi_dir_raw == "short_heavy":
            oi_dir = -1
        else:
            oi_dir = 0
        direction_score += oi_dir
        intensity += _clip01(abs(float(oi.oi_change_pct)) / 20.0)
        detail["oi"] = {"direction": oi_dir_raw, "change_pct": float(oi.oi_change_pct)}

    intensity = _clip01(intensity)
    return {
        "direction": _dir(direction_score),
        "intensity": round(intensity, 3),
        "detail": detail or None,
    }


async def _liquidmap_signal(symbol: str, current_price: float) -> dict:
    levels = await get_heatmap(symbol, top_n=20)
    if not levels or current_price <= 0:
        return {"direction": "neutral", "intensity": 0.0, "detail": None}

    nearby_shorts = 0.0
    nearby_longs = 0.0
    total = 0.0
    for lv in levels:
        total += lv["size_usd"]
        diff = (lv["price"] - current_price) / current_price
        # short liquidations *above* price → bullish fuel
        if lv["side"] == "short" and 0 < diff <= 0.02:
            nearby_shorts += lv["size_usd"]
        # long liquidations *below* price → bearish fuel
        if lv["side"] == "long" and -0.02 <= diff < 0:
            nearby_longs += lv["size_usd"]

    if total <= 0:
        return {"direction": "neutral", "intensity": 0.0, "detail": None}

    if nearby_shorts > nearby_longs:
        direction = "bullish"
        intensity = _clip01(nearby_shorts / total)
        detail = {"short_cluster_usd": round(nearby_shorts, 2)}
    elif nearby_longs > nearby_shorts:
        direction = "bearish"
        intensity = _clip01(nearby_longs / total)
        detail = {"long_cluster_usd": round(nearby_longs, 2)}
    else:
        direction = "neutral"
        intensity = 0.0
        detail = None
    return {"direction": direction, "intensity": round(intensity, 3), "detail": detail}


async def _sentiment_signal(db: AsyncSession, symbol: str) -> dict:
    row = (
        await db.execute(
            select(SentimentSnapshot)
            .where(SentimentSnapshot.symbol == symbol)
            .order_by(desc(SentimentSnapshot.snapshot_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return {"direction": "neutral", "intensity": 0.0, "detail": None}

    funding = float(row.funding_rate or 0)
    long_ratio = float(row.long_ratio or 0)

    direction_score = 0
    intensity = 0.0
    detail: dict = {"funding_rate": funding, "long_ratio": long_ratio}

    if funding > 0.0003:   # 0.03% → overheated longs = bearish
        direction_score -= 1
        intensity += _clip01(funding / 0.001)
    elif funding < -0.0001:  # squeezable shorts = bullish
        direction_score += 1
        intensity += _clip01(abs(funding) / 0.001)

    if long_ratio and long_ratio >= 65:
        direction_score -= 1
        intensity += _clip01((long_ratio - 50) / 30)
    elif long_ratio and long_ratio <= 35:
        direction_score += 1
        intensity += _clip01((50 - long_ratio) / 30)

    intensity = _clip01(intensity)
    return {
        "direction": _dir(direction_score),
        "intensity": round(intensity, 3),
        "detail": detail,
    }


async def _macro_signal() -> dict:
    ctx = await compute_macro_context()
    score = int(ctx.get("macro_score", 0))
    if score > 30:
        direction = "bullish"
    elif score < -30:
        direction = "bearish"
    else:
        direction = "neutral"
    intensity = _clip01(abs(score) / 100.0)
    return {
        "direction": direction,
        "intensity": round(intensity, 3),
        "detail": {"macro_score": score, "risk_environment": ctx.get("risk_environment")},
        "_ctx": ctx,
    }


async def _gemradar_signal(db: AsyncSession, symbol: str) -> dict:
    # Small-caps only; majors return zero-intensity.
    if symbol.upper() in MAJOR_SYMBOLS:
        return {"direction": "neutral", "intensity": 0.0, "detail": None}

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    row = (
        await db.execute(
            select(GemRadarAlert)
            .where(GemRadarAlert.symbol == symbol, GemRadarAlert.detected_at >= since)
            .order_by(desc(GemRadarAlert.detected_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return {"direction": "neutral", "intensity": 0.0, "detail": None}

    price_change = float(row.price_change_pct or 0)
    direction = "bullish" if price_change > 0 else ("bearish" if price_change < 0 else "neutral")
    # Intensity: inverse of risk score (low risk = high confidence) combined with volume
    risk_numeric = row.risk_score_numeric or 50
    risk_factor = _clip01(1.0 - risk_numeric / 100.0)
    vol_factor = _clip01(float(row.volume_mcap_ratio or 0) / 0.5)
    intensity = _clip01((risk_factor + vol_factor) / 2.0)
    return {
        "direction": direction,
        "intensity": round(intensity, 3),
        "detail": {
            "price_change_pct": price_change,
            "risk_score": row.risk_score,
            "volume_mcap_ratio": float(row.volume_mcap_ratio) if row.volume_mcap_ratio else None,
        },
    }


# ---------- scoring ----------

def _score_to_recommendation(score: float) -> str:
    if score >= 75:
        return "strong_long"
    if score >= 50:
        return "long"
    if score >= 25:
        return "watch_long"
    if score <= -75:
        return "strong_short"
    if score <= -50:
        return "short"
    if score <= -25:
        return "watch_short"
    return "neutral"


def _confidence(confluence: int) -> str:
    if confluence >= 5:
        return "high"
    if confluence >= 3:
        return "medium"
    return "low"


def _atr_from_candles(candles: list[dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    # candles[0] is newest
    for i in range(min(period, len(candles) - 1)):
        c = candles[i]
        prev = candles[i + 1]
        try:
            high = float(c.get("high", 0))
            low = float(c.get("low", 0))
            prev_close = float(prev.get("close", 0))
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            if tr > 0:
                trs.append(tr)
        except (TypeError, ValueError):
            continue
    if not trs:
        return 0.0
    return statistics.fmean(trs)


# ---------- main entry points ----------

async def compute_live_score(db: AsyncSession, symbol: str, weights: dict | None = None) -> dict:
    """Compute the current score for a symbol without persisting."""
    sym = symbol.upper()
    weights = weights or DEFAULT_WEIGHTS

    # Current price: latest candle from Redis
    latest = await redis_service.get_latest_candle(sym)
    current_price = float(latest.get("close", 0)) if latest else 0.0

    radarx = await _radarx_signal(db, sym)
    whale = await _whaleradar_signal(db, sym)
    liq = await _liquidmap_signal(sym, current_price) if current_price > 0 else {
        "direction": "neutral", "intensity": 0.0, "detail": None
    }
    sent = await _sentiment_signal(db, sym)
    macro = await _macro_signal()
    gem = await _gemradar_signal(db, sym)

    modules = {
        "radarx": radarx,
        "whaleradar": whale,
        "liquidmap": liq,
        "sentimentpulse": sent,
        "macropulse": macro,
        "gemradar": gem,
    }

    # Normalize weights so they always sum to 100, preventing score compression
    # when modules are inactive or user-configured weights don't sum to 100.
    raw_weight_sum = sum(float(weights.get(name, 0)) for name in modules)
    weight_scale = 100.0 / raw_weight_sum if raw_weight_sum > 0 else 0.0

    breakdown: dict[str, dict] = {}
    total_score = 0.0
    confluence = 0
    for name, sig in modules.items():
        dir_val = {"bullish": 1, "bearish": -1}.get(sig["direction"], 0)
        raw_weight = float(weights.get(name, 0))
        normalized_weight = raw_weight * weight_scale
        contribution = dir_val * float(sig["intensity"]) * normalized_weight
        breakdown[name] = {
            "direction": sig["direction"],
            "intensity": sig["intensity"],
            "weight": round(normalized_weight, 2),
            "contribution": round(contribution, 2),
            "detail": sig.get("detail"),
        }
        total_score += contribution
        if abs(sig["intensity"]) > 0.3:
            confluence += 1

    # Clamp to [-100, 100]
    score = max(-100.0, min(100.0, total_score))
    recommendation = _score_to_recommendation(score)
    confidence = _confidence(confluence)

    macro_ctx = macro.get("_ctx") or {}
    return {
        "symbol": sym,
        "score": int(round(score)),
        "recommendation": recommendation,
        "confidence": confidence,
        "confluence_count": confluence,
        "signals_breakdown": breakdown,
        "current_price": current_price,
        "macro_context": {
            "macro_score": macro_ctx.get("macro_score"),
            "vix_level": macro_ctx.get("vix_level"),
            "dxy_trend": macro_ctx.get("dxy_trend"),
            "risk_environment": macro_ctx.get("risk_environment"),
        },
    }


async def _compute_trade_params(symbol: str, entry: float, direction: str) -> dict:
    """Stop = entry ± 1.5·ATR; target at 2.5R."""
    candles = await redis_service.get_candles(symbol, limit=15)
    atr = _atr_from_candles(candles, period=14)
    if entry <= 0 or atr <= 0:
        return {"entry_price": entry or None, "stop_loss": None, "take_profit": None, "rr_ratio": None}
    stop_dist = atr * 1.5
    if direction == "bearish":
        stop = entry + stop_dist
        target = entry - stop_dist * 2.5
    else:
        stop = entry - stop_dist
        target = entry + stop_dist * 2.5
    return {
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "rr_ratio": 2.5,
    }


async def generate_signal(
    db: AsyncSession,
    symbol: str,
    *,
    weights: dict | None = None,
    min_score_to_alert: int = 65,
    is_paper: bool = True,
) -> dict | None:
    """Full pipeline: compute score → persist → optionally publish alert.

    Returns the persisted signal dict or None if it couldn't be produced
    (e.g. missing price).
    """
    sym = symbol.upper()
    live = await compute_live_score(db, sym, weights=weights)

    current_price = live["current_price"]
    if current_price <= 0:
        return None

    # Trade params only make sense for directional recs
    direction = (
        "bullish" if live["score"] > 0
        else ("bearish" if live["score"] < 0 else "neutral")
    )
    params = await _compute_trade_params(sym, current_price, direction) if direction != "neutral" else {
        "entry_price": current_price, "stop_loss": None, "take_profit": None, "rr_ratio": None
    }

    macro_ctx = live["macro_context"]
    # VIX / DXY cached values
    r = redis_service.get_redis()
    vix_val = None
    dxy_val = None
    try:
        raw_vix = await r.get("macro:vix")
        raw_dxy = await r.get("macro:dxy")
        if raw_vix:
            vix_val = float(json.loads(raw_vix).get("value")) if raw_vix else None
        if raw_dxy:
            dxy_val = float(json.loads(raw_dxy).get("value")) if raw_dxy else None
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    signal_at = datetime.now(timezone.utc)
    row = OracleSignal(
        symbol=sym,
        asset_type="futures",
        score=int(live["score"]),
        recommendation=live["recommendation"],
        confidence=live["confidence"],
        confluence_count=int(live["confluence_count"]),
        signals_breakdown=live["signals_breakdown"],
        entry_price=Decimal(str(params["entry_price"])) if params.get("entry_price") else None,
        stop_loss=Decimal(str(params["stop_loss"])) if params.get("stop_loss") else None,
        take_profit=Decimal(str(params["take_profit"])) if params.get("take_profit") else None,
        rr_ratio=Decimal(str(params["rr_ratio"])) if params.get("rr_ratio") else None,
        macro_score=macro_ctx.get("macro_score"),
        vix_at_signal=Decimal(str(round(vix_val, 2))) if vix_val is not None else None,
        dxy_at_signal=Decimal(str(round(dxy_val, 3))) if dxy_val is not None else None,
        is_paper=is_paper,
        timeframe="5m",
        signal_at=signal_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Seed the outcome row with the signal-time price
    outcome = OracleOutcome(
        signal_id=row.id,
        price_at_signal=Decimal(str(current_price)),
    )
    db.add(outcome)
    await db.commit()

    payload = {
        "id": str(row.id),
        "module": "oracle",
        "symbol": sym,
        "score": int(live["score"]),
        "recommendation": live["recommendation"],
        "confidence": live["confidence"],
        "confluence_count": int(live["confluence_count"]),
        "entry_price": float(row.entry_price) if row.entry_price else None,
        "stop_loss": float(row.stop_loss) if row.stop_loss else None,
        "take_profit": float(row.take_profit) if row.take_profit else None,
        "rr_ratio": float(row.rr_ratio) if row.rr_ratio else None,
        "signal_at": signal_at.isoformat(),
    }

    if abs(int(live["score"])) >= min_score_to_alert:
        await redis_service.publish_alert("oracle", payload)
        log.info(
            "oracle_signal_published",
            symbol=sym,
            score=int(live["score"]),
            recommendation=live["recommendation"],
        )

    return payload


# ---------- outcome measurement ----------

async def measure_outcomes() -> int:
    """Scheduled job: backfill price_15m/1h/4h/24h for signals that have matured.

    Idempotent — only fills slots still NULL.
    """
    now = datetime.now(timezone.utc)
    filled = 0
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(OracleSignal, OracleOutcome)
                .join(OracleOutcome, OracleOutcome.signal_id == OracleSignal.id)
                .where(OracleSignal.signal_at >= now - timedelta(hours=36))
            )
        ).all()
        for sig, out in rows:
            age = now - sig.signal_at
            price_at_signal = float(out.price_at_signal)
            if price_at_signal <= 0:
                continue
            changed = False

            async def _maybe(slot: str, min_minutes: int) -> None:
                nonlocal changed
                if getattr(out, slot) is not None or age < timedelta(minutes=min_minutes):
                    return
                # Try to find the candle at the exact target time
                target_time = sig.signal_at + timedelta(minutes=min_minutes)
                target_ts_ms = int(target_time.timestamp() * 1000)
                candle = await redis_service.get_candle_at(
                    sig.symbol, target_ts_ms, tolerance_ms=300_000,
                )
                if candle is None:
                    # Fall back to latest only if the target time has passed
                    # and we're within a reasonable window (< 2x the interval)
                    if age <= timedelta(minutes=min_minutes * 2):
                        latest = await redis_service.get_latest_candle(sig.symbol)
                        if latest:
                            candle = latest
                if candle is None:
                    return
                price = float(candle.get("c") or candle.get("close") or 0)
                if price <= 0:
                    return
                setattr(out, slot, Decimal(str(price)))
                pct = (price - price_at_signal) / price_at_signal * 100
                pnl_slot = slot.replace("price_", "pnl_") + "_pct"
                setattr(out, pnl_slot, Decimal(str(round(pct, 4))))
                changed = True

            await _maybe("price_15m", 15)
            await _maybe("price_1h", 60)
            await _maybe("price_4h", 240)
            await _maybe("price_24h", 24 * 60)

            # Correctness checks — only when we just filled the slot
            if out.price_1h is not None and out.was_correct_1h is None:
                long_like = sig.recommendation in ("long", "strong_long", "watch_long")
                short_like = sig.recommendation in ("short", "strong_short", "watch_short")
                pct = float(out.pnl_1h_pct or 0)
                if long_like:
                    out.was_correct_1h = pct > 0
                elif short_like:
                    out.was_correct_1h = pct < 0
                else:
                    out.was_correct_1h = None
                changed = True

            if out.price_4h is not None and out.was_correct_4h is None:
                long_like = sig.recommendation in ("long", "strong_long", "watch_long")
                short_like = sig.recommendation in ("short", "strong_short", "watch_short")
                pct = float(out.pnl_4h_pct or 0)
                if long_like:
                    out.was_correct_4h = pct > 0
                elif short_like:
                    out.was_correct_4h = pct < 0
                else:
                    out.was_correct_4h = None
                changed = True

            if changed:
                out.measured_at = now
                filled += 1

        if filled:
            await db.commit()
    if filled:
        log.info("oracle_outcomes_updated", filled=filled)
    return filled


__all__ = [
    "DEFAULT_WEIGHTS",
    "compute_live_score",
    "generate_signal",
    "measure_outcomes",
]
