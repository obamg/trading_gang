from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.cmcpulse import TradeContextSnapshot
from app.modules.cmcpulse import collector
from app.services import cmc_client

router = APIRouter(prefix="/cmcpulse", tags=["cmcpulse"])


@router.get("/credits")
async def cmc_credits(_user: CurrentUser):
    """CMC key status + credit spend.

    ``estimated_used`` is our own running counter; ``plan`` is CMC's
    authoritative figure from the free ``/v1/key/info`` endpoint. The point
    is to see the monthly limit approaching rather than discover it as a
    wall of 429s.
    """
    out: dict = {
        "enabled": cmc_client.enabled(),
        "estimated_used_this_month": await cmc_client.credits_used_this_month(),
        "estimated_budget": cmc_client.BASIC_MONTHLY_CREDITS,
        "plan": None,
    }
    info = await cmc_client.key_info()
    if info:
        out["plan"] = info.get("plan")
        out["usage"] = info.get("usage")
    return out


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
                "most_visited_rank": r.most_visited_rank,
                "gainers_losers_rank": r.gainers_losers_rank,
                "community_rank": r.community_rank,
                "btc_dominance_pct": float(r.btc_dominance_pct)
                if r.btc_dominance_pct is not None else None,
                "total_mcap_usd": float(r.total_mcap_usd)
                if r.total_mcap_usd is not None else None,
                "captured_at": r.captured_at.isoformat(),
            }
            for r in rows
        ]
    }
