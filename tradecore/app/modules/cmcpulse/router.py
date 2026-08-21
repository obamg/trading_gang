from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.cmcpulse import TradeContextSnapshot
from app.modules.cmcpulse import collector

router = APIRouter(prefix="/cmcpulse", tags=["cmcpulse"])


@router.get("/context")
async def current_context(_user: CurrentUser, symbol: str | None = Query(None, max_length=40)):
    """Current Fear & Greed + trending entry for an optional symbol."""
    return await collector.get_context(symbol)


@router.get("/snapshots")
async def trade_snapshots(
    _user: CurrentUser,
    db: DBSession,
    limit: int = Query(50, ge=1, le=200),
    strategy: str | None = Query(None, max_length=20),
):
    q = (
        select(TradeContextSnapshot)
        .order_by(desc(TradeContextSnapshot.captured_at))
        .limit(limit)
    )
    if strategy:
        q = q.where(TradeContextSnapshot.strategy == strategy)
    rows = (await db.execute(q)).scalars().all()
    return {
        "items": [
            {
                "trade_id": str(r.trade_id),
                "symbol": r.symbol,
                "strategy": r.strategy,
                "fear_greed": r.fear_greed,
                "fear_greed_class": r.fear_greed_class,
                "trending_rank": r.trending_rank,
                "trending_change_24h": float(r.trending_change_24h)
                if r.trending_change_24h is not None else None,
                "captured_at": r.captured_at.isoformat(),
            }
            for r in rows
        ]
    }
