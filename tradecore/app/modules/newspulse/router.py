from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select, desc, func

from app.dependencies import CurrentUser, DBSession
from app.models.news import NewsArticle

router = APIRouter(prefix="/news", tags=["newspulse"])


@router.get("/articles")
async def list_articles(
    _user: CurrentUser,
    db: DBSession,
    limit: int = Query(50, ge=1, le=200),
    sentiment: str | None = Query(None, pattern="^(bullish|bearish|neutral)$"),
    importance: str | None = Query(None, pattern="^(high|normal)$"),
    coin: str | None = Query(None, max_length=20),
):
    q = select(NewsArticle).order_by(desc(NewsArticle.published_at)).limit(limit)
    if sentiment:
        q = q.where(NewsArticle.sentiment == sentiment)
    if importance:
        q = q.where(NewsArticle.importance == importance)
    if coin:
        q = q.where(NewsArticle.coins.ilike(f"%{coin.upper()}%"))

    rows = (await db.execute(q)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "title": r.title,
                "url": r.url,
                "source": r.source_name,
                "sentiment": r.sentiment,
                "importance": r.importance,
                "coins": r.coins.split(",") if r.coins else [],
                "published_at": r.published_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/stats")
async def news_stats(_user: CurrentUser, db: DBSession):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    base = select(func.count(NewsArticle.id)).where(NewsArticle.published_at >= cutoff)

    total = (await db.execute(base)).scalar() or 0
    bullish = (await db.execute(base.where(NewsArticle.sentiment == "bullish"))).scalar() or 0
    bearish = (await db.execute(base.where(NewsArticle.sentiment == "bearish"))).scalar() or 0
    high_impact = (await db.execute(base.where(NewsArticle.importance == "high"))).scalar() or 0

    return {
        "articles_24h": total,
        "bullish_24h": bullish,
        "bearish_24h": bearish,
        "high_impact_24h": high_impact,
    }
