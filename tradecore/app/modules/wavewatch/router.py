"""WaveWatch API.

Two endpoints today:
  GET /wavewatch/universe         — the current innovation asset list with
                                    latest score + last-alert timestamp
  GET /wavewatch/{symbol}/state   — full snapshot for one symbol (score,
                                    components, recent candles, funding,
                                    cooldown remaining)
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.wavewatch import WaveAsset
from app.modules.wavewatch import scoring
from app.config import settings as app_settings
from app.modules.wavewatch.detector import (
    LAST_ACTIVE_ALERT_KEY,
    LAST_ALERT_KEY,
    SCORE_KEY,
    SINCE_KEY,
)
from app.services import redis_service

router = APIRouter(prefix="/wavewatch", tags=["wavewatch"])


def _serialize(a: WaveAsset) -> dict:
    return {
        "id": str(a.id),
        "exchange": a.exchange,
        "market_type": a.market_type,
        "symbol": a.symbol,
        "base_asset": a.base_asset,
        "status": a.status,
        "first_seen_at": a.first_seen_at.isoformat() if a.first_seen_at else None,
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        "latest_score": float(a.latest_score) if a.latest_score is not None else None,
        "latest_score_at": a.latest_score_at.isoformat() if a.latest_score_at else None,
        "last_alerted_at": a.last_alerted_at.isoformat() if a.last_alerted_at else None,
    }


@router.get("/universe")
async def list_universe(
    _user: CurrentUser,
    db: DBSession,
    status: str = Query(default="active", regex="^(active|removed|all)$"),
    limit: int = Query(default=200, ge=1, le=500),
):
    stmt = select(WaveAsset).order_by(desc(WaveAsset.latest_score).nullslast())
    if status != "all":
        stmt = stmt.where(WaveAsset.status == status)
    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize(a) for a in rows]}


@router.get("/{symbol}/state")
async def wavewatch_state(
    _user: CurrentUser,
    db: DBSession,
    symbol: str,
):
    # Bare-symbol lookup — Bybit and Binance can share tickers (e.g. LABUSDT
    # on both), so order by latest_score so the more interesting row wins
    # and avoid MultipleResultsFound.
    row = (
        await db.execute(
            select(WaveAsset)
            .where(WaveAsset.symbol == symbol)
            .order_by(desc(WaveAsset.latest_score).nullslast())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="symbol not in wavewatch universe")

    candles = await redis_service.get_candles(symbol, limit=50)
    funding = await redis_service.get_funding_rate(symbol)
    score = scoring.compute_score(candles, funding) if len(candles) >= 24 else None
    active = (
        scoring.compute_active(
            candles,
            funding,
            min_pct_change=float(app_settings.wavewatch_active_pct_threshold),
            min_vol_ratio=float(app_settings.wavewatch_active_vol_ratio),
            funding_extreme=float(app_settings.wavewatch_active_funding_extreme),
        )
        if len(candles) >= 24
        else None
    )

    r = redis_service.get_redis()
    since_iso = await r.get(SINCE_KEY.format(sym=symbol))
    last_alert_iso = await r.get(LAST_ALERT_KEY.format(sym=symbol))
    last_active_alert_iso = await r.get(LAST_ACTIVE_ALERT_KEY.format(sym=symbol))
    now = datetime.now(timezone.utc)
    dwell_s = 0
    if since_iso:
        try:
            dwell_s = int((now - datetime.fromisoformat(since_iso)).total_seconds())
        except ValueError:
            dwell_s = 0

    return {
        "asset": _serialize(row),
        "live_score": (
            {
                "score": score.score,
                "onset": score.onset,
                "components": score.components,
                "vol_ratio_now": score.vol_ratio_now,
            }
            if score is not None
            else None
        ),
        "live_active": (
            {
                "triggered": active.triggered,
                "direction": active.direction,
                "pct_change": active.pct_change,
                "vol_ratio": active.vol_ratio,
            }
            if active is not None
            else None
        ),
        "funding_pct": funding,
        "since_above_threshold": since_iso,
        "dwell_seconds": dwell_s,
        "last_alert": last_alert_iso,
        "last_active_alert": last_active_alert_iso,
        "candles_available": len(candles),
    }
