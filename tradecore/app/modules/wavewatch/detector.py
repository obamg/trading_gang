"""WaveWatch detector — per-tick orchestrator.

Reads each active innovation perp, scores it, evaluates wave onset, ranks
qualifiers, applies the per-symbol cooldown and global rate cap, then
publishes alerts.

State kept in Redis:
  wavewatch:score:{symbol}        latest score (float), TTL 600s
  wavewatch:since:{symbol}        ISO ts when score first crossed threshold, TTL 7200s
  wavewatch:last_alert:{symbol}   ISO ts of last fired alert, TTL = cooldown
  wavewatch:hour_count            int, alerts in current hour bucket, TTL 3600s
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.wavewatch import WaveAsset
from app.modules.wavewatch import scoring, universe
from app.services import redis_service


SCORE_KEY = "wavewatch:score:{sym}"
SINCE_KEY = "wavewatch:since:{sym}"
LAST_ALERT_KEY = "wavewatch:last_alert:{sym}"
HOUR_COUNT_KEY = "wavewatch:hour_count"


async def _evaluate_one(
    asset: WaveAsset,
    *,
    score_threshold: float,
    dwell_seconds: int,
) -> dict | None:
    """Score one asset and return a candidate dict if it's ready to alert,
    else None. Updates Redis state regardless."""
    candles = await redis_service.get_candles(asset.symbol, limit=50)
    if len(candles) < 24:
        return None
    funding = await redis_service.get_funding_rate(asset.symbol)
    result = scoring.compute_score(candles, funding)
    if result is None:
        return None

    r = redis_service.get_redis()
    now = datetime.now(timezone.utc)
    await r.set(SCORE_KEY.format(sym=asset.symbol), str(result.score), ex=600)

    # Track how long the score has been above threshold (dwell time).
    since_key = SINCE_KEY.format(sym=asset.symbol)
    if result.score >= score_threshold:
        existing = await r.get(since_key)
        if existing is None:
            await r.set(since_key, now.isoformat(), ex=7200)
            dwell_s = 0
        else:
            try:
                dwell_s = int((now - datetime.fromisoformat(existing)).total_seconds())
            except ValueError:
                dwell_s = 0
                await r.set(since_key, now.isoformat(), ex=7200)
    else:
        await r.delete(since_key)
        dwell_s = 0

    if not result.onset:
        return None
    if result.score < score_threshold:
        return None
    if dwell_s < dwell_seconds:
        return None

    return {
        "asset": asset,
        "score": result.score,
        "onset": True,
        "components": result.components,
        "vol_ratio_now": result.vol_ratio_now,
        "dwell_seconds": dwell_s,
        "funding_pct": funding,
    }


async def run_wavewatch_tick() -> dict:
    """One detector tick. Idempotent; rate-limits internally."""
    if not getattr(app_settings, "wavewatch_enabled", False):
        return {"skipped": 1}

    score_threshold = float(getattr(app_settings, "wavewatch_score_threshold", 0.6))
    dwell_seconds = int(getattr(app_settings, "wavewatch_score_dwell_minutes", 30)) * 60
    max_per_hour = int(getattr(app_settings, "wavewatch_max_alerts_per_hour", 5))
    cooldown_hours = int(getattr(app_settings, "wavewatch_symbol_cooldown_hours", 6))

    r = redis_service.get_redis()

    async with AsyncSessionLocal() as db:
        assets = await universe.list_active_perps(db)

    candidates: list[dict] = []
    scored = 0
    for a in assets:
        try:
            c = await _evaluate_one(
                a,
                score_threshold=score_threshold,
                dwell_seconds=dwell_seconds,
            )
            scored += 1
            if c is not None:
                candidates.append(c)
        except Exception as e:
            log.warning("wavewatch_score_failed", symbol=a.symbol, err=str(e))

    if not candidates:
        return {"scored": scored, "candidates": 0, "alerted": 0}

    # Rank by score × volume burst — strongest setup × strongest break first.
    candidates.sort(
        key=lambda c: c["score"] * max(c["vol_ratio_now"], 1.0),
        reverse=True,
    )

    # Global hourly cap.
    try:
        hour_count = int(await r.get(HOUR_COUNT_KEY) or 0)
    except (TypeError, ValueError):
        hour_count = 0
    remaining = max(0, max_per_hour - hour_count)
    if remaining <= 0:
        log.info(
            "wavewatch_hour_cap_hit",
            cap=max_per_hour,
            suppressed=len(candidates),
        )
        return {
            "scored": scored,
            "candidates": len(candidates),
            "alerted": 0,
            "suppressed_hour_cap": len(candidates),
        }

    now = datetime.now(timezone.utc)
    fired = 0
    suppressed_cooldown = 0

    async with AsyncSessionLocal() as db:
        for c in candidates:
            if fired >= remaining:
                break
            asset: WaveAsset = c["asset"]

            # Per-symbol cooldown.
            last_alert_key = LAST_ALERT_KEY.format(sym=asset.symbol)
            existing_alert = await r.get(last_alert_key)
            if existing_alert is not None:
                suppressed_cooldown += 1
                continue

            alert = {
                "type": "wave_incoming",
                "asset_id": str(asset.id),
                "exchange": asset.exchange,
                "market_type": asset.market_type,
                "symbol": asset.symbol,
                "base_asset": asset.base_asset,
                "score": c["score"],
                "components": c["components"],
                "vol_ratio_now": c["vol_ratio_now"],
                "dwell_seconds": c["dwell_seconds"],
                "funding_pct": c["funding_pct"],
                "detected_at": now.isoformat(),
            }
            await redis_service.publish_alert("wavewatch", alert)
            await r.set(
                last_alert_key,
                now.isoformat(),
                ex=cooldown_hours * 3600,
            )
            # Hour counter — initialize TTL on first set, increment otherwise.
            new_count = await r.incr(HOUR_COUNT_KEY)
            if new_count == 1:
                await r.expire(HOUR_COUNT_KEY, 3600)

            await db.execute(
                update(WaveAsset)
                .where(WaveAsset.id == asset.id)
                .values(
                    latest_score=Decimal(str(c["score"])),
                    latest_score_at=now,
                    last_alerted_at=now,
                )
            )
            fired += 1
            log.info(
                "wavewatch_wave_incoming",
                symbol=asset.symbol,
                base=asset.base_asset,
                score=c["score"],
                vol_ratio=c["vol_ratio_now"],
                dwell_s=c["dwell_seconds"],
            )
        await db.commit()

    return {
        "scored": scored,
        "candidates": len(candidates),
        "alerted": fired,
        "suppressed_cooldown": suppressed_cooldown,
    }


async def run_wavewatch_universe_refresh() -> None:
    """Scheduler entry point for universe refresh — swallows exceptions per
    project convention."""
    try:
        result = await universe.refresh_universe()
        log.info("wavewatch_universe_tick", **result)
    except Exception as e:
        log.error("wavewatch_universe_failed", err=str(e))


async def run_wavewatch_detect_tick() -> None:
    """Scheduler entry point for the per-tick detector — swallows exceptions."""
    try:
        result = await run_wavewatch_tick()
        log.info("wavewatch_tick", **result)
    except Exception as e:
        log.error("wavewatch_tick_failed", err=str(e))


__all__ = [
    "run_wavewatch_tick",
    "run_wavewatch_universe_refresh",
    "run_wavewatch_detect_tick",
]
