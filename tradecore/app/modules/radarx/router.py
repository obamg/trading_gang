"""RadarX API — list/detail/top-movers/stats."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select

from app.dependencies import CurrentUser, DBSession
from app.errors import AppError
from app.models.radarx import RadarXAlert
from app.modules.radarx import detector

router = APIRouter(prefix="/radarx", tags=["radarx"])


@router.get("/alerts")
async def list_alerts(
    _user: CurrentUser,
    db: DBSession,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(RadarXAlert).order_by(desc(RadarXAlert.triggered_at))
    if symbol:
        stmt = stmt.where(RadarXAlert.symbol == symbol.upper())
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: UUID, _user: CurrentUser, db: DBSession):
    row = await db.get(RadarXAlert, alert_id)
    if row is None:
        raise AppError(404, "Alert not found", "NOT_FOUND")
    return _serialize(row)


@router.get("/top-movers")
async def top_movers(_user: CurrentUser, limit: int = Query(default=20, ge=1, le=100)):
    return {"items": await detector.live_top_movers(limit=limit)}


@router.get("/stats")
async def stats(_user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    count_stmt = select(func.count()).select_from(RadarXAlert).where(RadarXAlert.triggered_at >= since)
    avg_stmt = select(func.avg(RadarXAlert.z_score)).where(RadarXAlert.triggered_at >= since)
    top_stmt = (
        select(RadarXAlert.symbol, func.count().label("n"))
        .where(RadarXAlert.triggered_at >= since)
        .group_by(RadarXAlert.symbol)
        .order_by(desc("n"))
        .limit(1)
    )
    count = (await db.execute(count_stmt)).scalar_one()
    avg_z = (await db.execute(avg_stmt)).scalar_one()
    top = (await db.execute(top_stmt)).first()
    return {
        "alerts_24h": int(count),
        "avg_z_score": float(avg_z) if avg_z is not None else None,
        "top_symbol": top[0] if top else None,
        "top_symbol_count": int(top[1]) if top else 0,
    }


def _serialize(r: RadarXAlert) -> dict:
    return {
        "id": str(r.id),
        "symbol": r.symbol,
        "timeframe": r.timeframe,
        "z_score": float(r.z_score),
        "ratio": float(r.ratio),
        "candle_volume_usd": float(r.candle_volume_usd),
        "avg_volume_usd": float(r.avg_volume_usd),
        "price": float(r.price),
        "price_change_pct": float(r.price_change_pct) if r.price_change_pct is not None else None,
        "volume_24h_usd": float(r.volume_24h_usd) if r.volume_24h_usd is not None else None,
        "triggered_at": r.triggered_at.isoformat(),
    }
