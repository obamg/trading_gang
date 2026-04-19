"""LiquidMap API."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select

from app.dependencies import CurrentUser, DBSession, require_feature
from app.models.liquidmap import LiquidationEvent
from app.modules.liquidmap.tracker import get_heatmap

router = APIRouter(
    prefix="/liquidmap", tags=["liquidmap"],
    dependencies=[Depends(require_feature("liquidmap"))],
)


@router.get("/heatmap/{symbol}")
async def heatmap(symbol: str, _user: CurrentUser, limit: int = Query(default=20, ge=1, le=100)):
    levels = await get_heatmap(symbol, top_n=limit)
    return {"symbol": symbol.upper(), "levels": levels}


@router.get("/recent")
async def recent(
    _user: CurrentUser,
    db: DBSession,
    symbol: str | None = Query(default=None),
    hours: int = Query(default=2, ge=1, le=24),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(LiquidationEvent)
        .where(LiquidationEvent.detected_at >= since, LiquidationEvent.size_usd >= 1_000_000)
        .order_by(desc(LiquidationEvent.detected_at))
    )
    if symbol:
        stmt = stmt.where(LiquidationEvent.symbol == symbol.upper())
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "symbol": r.symbol,
                "side": r.side,
                "size_usd": float(r.size_usd),
                "price": float(r.price),
                "detected_at": r.detected_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/stats/{symbol}")
async def stats(symbol: str, _user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    base = select(
        LiquidationEvent.side, func.coalesce(func.sum(LiquidationEvent.size_usd), 0)
    ).where(LiquidationEvent.symbol == symbol.upper(), LiquidationEvent.detected_at >= since).group_by(
        LiquidationEvent.side
    )
    rows = (await db.execute(base)).all()
    totals = {"long": 0.0, "short": 0.0}
    for side, total in rows:
        totals[side] = float(total)
    return {
        "symbol": symbol.upper(),
        "long_usd_24h": totals["long"],
        "short_usd_24h": totals["short"],
        "net_usd_24h": totals["long"] - totals["short"],
    }
