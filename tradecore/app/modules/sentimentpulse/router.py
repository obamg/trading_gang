"""SentimentPulse API."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.sentiment import MarketSentimentSnapshot, SentimentSnapshot
from app.services import redis_service

router = APIRouter(prefix="/sentiment", tags=["sentimentpulse"])


@router.get("/overview")
async def overview(_user: CurrentUser, db: DBSession):
    r = redis_service.get_redis()
    cached = await r.get("sentiment:market:latest")
    if cached:
        return json.loads(cached)
    row = (
        await db.execute(select(MarketSentimentSnapshot).order_by(desc(MarketSentimentSnapshot.snapshot_at)).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return {"fear_greed_index": None, "btc_dominance_pct": None, "total_mcap_usd": None}
    return {
        "fear_greed_index": row.fear_greed_index,
        "fear_greed_label": row.fear_greed_label,
        "btc_dominance_pct": float(row.btc_dominance_pct) if row.btc_dominance_pct is not None else None,
        "total_mcap_usd": float(row.total_mcap_usd) if row.total_mcap_usd is not None else None,
        "snapshot_at": row.snapshot_at.isoformat(),
    }


@router.get("/funding")
async def funding(
    _user: CurrentUser,
    db: DBSession,
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
):
    # Latest snapshot per symbol is the most recent `snapshot_at`.
    latest_time = (
        await db.execute(select(SentimentSnapshot.snapshot_at).order_by(desc(SentimentSnapshot.snapshot_at)).limit(1))
    ).scalar_one_or_none()
    if latest_time is None:
        return {"items": []}
    stmt = (
        select(SentimentSnapshot)
        .where(SentimentSnapshot.snapshot_at == latest_time)
        .order_by(
            desc(SentimentSnapshot.funding_rate) if sort == "desc" else SentimentSnapshot.funding_rate
        )
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "symbol": r.symbol,
                "funding_rate": float(r.funding_rate) if r.funding_rate is not None else None,
                "long_ratio": float(r.long_ratio) if r.long_ratio is not None else None,
                "short_ratio": float(r.short_ratio) if r.short_ratio is not None else None,
                "open_interest_usd": float(r.open_interest_usd) if r.open_interest_usd is not None else None,
                "snapshot_at": r.snapshot_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/long-short")
async def long_short(_user: CurrentUser, db: DBSession, limit: int = Query(default=20, ge=1, le=100)):
    latest_time = (
        await db.execute(select(SentimentSnapshot.snapshot_at).order_by(desc(SentimentSnapshot.snapshot_at)).limit(1))
    ).scalar_one_or_none()
    if latest_time is None:
        return {"items": []}
    stmt = (
        select(SentimentSnapshot)
        .where(SentimentSnapshot.snapshot_at == latest_time, SentimentSnapshot.long_ratio.is_not(None))
        .order_by(desc(SentimentSnapshot.long_ratio))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "symbol": r.symbol,
                "long_ratio": float(r.long_ratio) if r.long_ratio is not None else None,
                "short_ratio": float(r.short_ratio) if r.short_ratio is not None else None,
                "snapshot_at": r.snapshot_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/history/{symbol}")
async def history(symbol: str, _user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    stmt = (
        select(SentimentSnapshot)
        .where(SentimentSnapshot.symbol == symbol.upper(), SentimentSnapshot.snapshot_at >= since)
        .order_by(SentimentSnapshot.snapshot_at)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "symbol": symbol.upper(),
        "items": [
            {
                "funding_rate": float(r.funding_rate) if r.funding_rate is not None else None,
                "long_ratio": float(r.long_ratio) if r.long_ratio is not None else None,
                "short_ratio": float(r.short_ratio) if r.short_ratio is not None else None,
                "open_interest_usd": float(r.open_interest_usd) if r.open_interest_usd is not None else None,
                "snapshot_at": r.snapshot_at.isoformat(),
            }
            for r in rows
        ],
    }
