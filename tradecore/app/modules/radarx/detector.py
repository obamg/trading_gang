"""RadarX — volume spike detection on 5m closed candles.

Reads the last 21 candles from Redis: baseline = oldest 20, target = newest.
Falls back to Binance REST klines when Redis candles are stale (WebSocket
kline streams may be unavailable from some VPS locations).
Fires when z-score and ratio over the 20-candle baseline both clear thresholds
(defaults: z ≥ 3.0, ratio ≥ 4.0) AND 24h quote volume ≥ min_volume_usd.
Cooldown suppresses re-alerting the same symbol within 30 minutes.
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.logging_config import log
from app.models.radarx import RadarXAlert
from app.services import redis_service

CANDLE_STALE_SECONDS = 600
_rest_client: httpx.AsyncClient | None = None


def _get_rest_client() -> httpx.AsyncClient:
    global _rest_client
    if _rest_client is None or _rest_client.is_closed:
        _rest_client = httpx.AsyncClient(timeout=10.0)
    return _rest_client


async def _fetch_candles_rest(symbol: str, limit: int = 25) -> list[dict]:
    """Fetch 5m klines from Binance REST API and update Redis cache."""
    client = _get_rest_client()
    url = f"{app_settings.binance_rest_url}/fapi/v1/klines"
    resp = await client.get(url, params={"symbol": symbol, "interval": "5m", "limit": limit})
    resp.raise_for_status()
    rows = resp.json()
    candles: list[dict] = []
    for r in rows:
        candles.append({
            "t": r[0], "T": r[6],
            "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
            "v": float(r[5]), "q": float(r[7]), "n": r[8],
        })
    # Only keep closed candles (close_time in the past)
    now_ms = int(time.time() * 1000)
    closed = [c for c in candles if c["T"] < now_ms]
    if closed:
        # Update Redis: overwrite the candle list (newest first)
        r = redis_service.get_redis()
        key = f"candles:{symbol}"
        import json
        pipe = r.pipeline()
        await pipe.delete(key).execute()
        for c in reversed(closed):
            await r.lpush(key, json.dumps(c))
        await r.ltrim(key, 0, 49)
    return list(reversed(closed))  # newest first


async def _get_candles(symbol: str, limit: int = 21) -> list[dict]:
    """Get candles from Redis, falling back to REST if stale."""
    candles = await redis_service.get_candles(symbol, limit=limit)
    if candles:
        close_time = candles[0].get("T") or 0
        close_ts = int(close_time) / 1000 if close_time > 10_000_000_000 else int(close_time)
        age = time.time() - close_ts
        if age < CANDLE_STALE_SECONDS:
            return candles
    try:
        rest_candles = await _fetch_candles_rest(symbol, limit + 5)
        return rest_candles[:limit]
    except Exception:
        return candles

# Defaults — may be overridden per-user when delivering, but detection runs
# against these global thresholds for efficiency.
DEFAULT_Z_THRESHOLD = 3.0
DEFAULT_RATIO_THRESHOLD = 4.0
DEFAULT_MIN_VOLUME_24H = 10_000_000.0
COOLDOWN_MINUTES = 30
DIVERGENCE_MAX_PRICE_PCT = 1.0


def _candle_quote_volume(candle: dict) -> float:
    """Accept either pre-computed quote_volume or fall back to close*volume."""
    for key in ("q", "quote_volume"):
        if key in candle:
            try:
                return float(candle[key])
            except (TypeError, ValueError):
                pass
    try:
        close = float(candle.get("c") or candle.get("close") or 0)
        vol = float(candle.get("v") or candle.get("volume") or 0)
        return close * vol
    except (TypeError, ValueError):
        return 0.0


def _candle_close(candle: dict) -> float:
    try:
        return float(candle.get("c") or candle.get("close") or 0)
    except (TypeError, ValueError):
        return 0.0


async def detect_symbol(
    db: AsyncSession,
    symbol: str,
    volume_24h_usd: float | None = None,
    *,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
    min_volume_usd: float = DEFAULT_MIN_VOLUME_24H,
) -> dict | None:
    """Evaluate a single symbol. Returns alert dict if fired, else None."""

    if volume_24h_usd is not None and volume_24h_usd < min_volume_usd:
        return None

    if await redis_service.is_on_cooldown("radarx", symbol):
        return None

    candles = await _get_candles(symbol, limit=21)
    if len(candles) < 21:
        return None

    current = candles[0]
    baseline = candles[1:21]

    current_vol = _candle_quote_volume(current)
    baseline_vols = [_candle_quote_volume(c) for c in baseline]
    if current_vol <= 0 or not any(v > 0 for v in baseline_vols):
        return None

    mean_vol = statistics.fmean(baseline_vols)
    if mean_vol <= 0:
        return None
    try:
        std_vol = statistics.pstdev(baseline_vols)
    except statistics.StatisticsError:
        std_vol = 0.0

    z_score = (current_vol - mean_vol) / std_vol if std_vol > 0 else 0.0
    ratio = current_vol / mean_vol

    if z_score < z_threshold or ratio < ratio_threshold:
        return None

    price = _candle_close(current)
    # Price change over the baseline window
    base_open = float(baseline[-1].get("o") or baseline[-1].get("open") or price) if baseline else price
    price_change_pct = ((price - base_open) / base_open * 100) if base_open else 0.0

    close_time = current.get("T") or current.get("close_time") or 0
    triggered_at = datetime.fromtimestamp(
        int(close_time) / 1000, tz=timezone.utc
    ) if close_time else datetime.now(timezone.utc)

    abs_pct = abs(price_change_pct)
    is_divergence = z_score >= DEFAULT_Z_THRESHOLD and abs_pct < DIVERGENCE_MAX_PRICE_PCT
    div_score = round(z_score / abs_pct, 1) if abs_pct > 0 else round(z_score * 100, 1)

    alert: dict[str, Any] = {
        "module": "radarx",
        "symbol": symbol,
        "z_score": round(z_score, 2),
        "ratio": round(ratio, 2),
        "candle_volume_usd": round(current_vol, 2),
        "avg_volume_usd": round(mean_vol, 2),
        "price": price,
        "price_change_pct": round(price_change_pct, 2),
        "volume_24h_usd": volume_24h_usd,
        "is_divergence": is_divergence,
        "divergence_score": div_score if is_divergence else None,
        "triggered_at": triggered_at.isoformat(),
        "tradingview_url": f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P",
    }

    # Dedup: skip if an alert with the same symbol+triggered_at already exists
    existing = await db.execute(
        select(RadarXAlert.id)
        .where(RadarXAlert.symbol == symbol, RadarXAlert.triggered_at == triggered_at)
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        await redis_service.set_alert_cooldown("radarx", symbol, COOLDOWN_MINUTES)
        return None

    row = RadarXAlert(
        symbol=symbol,
        timeframe="5m",
        z_score=Decimal(str(round(z_score, 2))),
        ratio=Decimal(str(round(ratio, 2))),
        candle_volume_usd=Decimal(str(round(current_vol, 2))),
        avg_volume_usd=Decimal(str(round(mean_vol, 2))),
        price=Decimal(str(price)),
        price_change_pct=Decimal(str(round(price_change_pct, 2))),
        volume_24h_usd=Decimal(str(volume_24h_usd)) if volume_24h_usd is not None else None,
        triggered_at=triggered_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    alert["id"] = str(row.id)

    await redis_service.set_alert_cooldown("radarx", symbol, COOLDOWN_MINUTES)
    await redis_service.publish_alert("radarx", alert)
    log.info(
        "radarx_alert",
        symbol=symbol,
        z_score=alert["z_score"],
        ratio=alert["ratio"],
        price=price,
    )
    return alert


async def live_top_movers(limit: int = 20) -> list[dict]:
    """Score every active symbol by current z-score against its last 20 candles."""
    symbols = await redis_service.get_symbol_list()
    scored: list[dict] = []
    for symbol in symbols:
        candles = await _get_candles(symbol, limit=21)
        if len(candles) < 21:
            continue
        current_vol = _candle_quote_volume(candles[0])
        base_vols = [_candle_quote_volume(c) for c in candles[1:21]]
        if current_vol <= 0 or not any(v > 0 for v in base_vols):
            continue
        mean_vol = statistics.fmean(base_vols)
        if mean_vol <= 0:
            continue
        try:
            std_vol = statistics.pstdev(base_vols)
        except statistics.StatisticsError:
            std_vol = 0.0
        z = (current_vol - mean_vol) / std_vol if std_vol > 0 else 0.0
        scored.append(
            {
                "symbol": symbol,
                "z_score": round(z, 2),
                "ratio": round(current_vol / mean_vol, 2),
                "price": _candle_close(candles[0]),
            }
        )
    scored.sort(key=lambda r: r["z_score"], reverse=True)
    return scored[:limit]


__all__ = ["detect_symbol", "live_top_movers", "DEFAULT_Z_THRESHOLD", "DEFAULT_RATIO_THRESHOLD"]


# Silence flake re: unused imports from config for tooling
_ = app_settings
