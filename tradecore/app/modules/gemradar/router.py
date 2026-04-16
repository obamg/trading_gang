"""GemRadar API."""
from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.gemradar import GemRadarAlert

router = APIRouter(prefix="/gemradar", tags=["gemradar"])


def _serialize(r: GemRadarAlert) -> dict:
    return {
        "id": str(r.id),
        "symbol": r.symbol,
        "name": r.name,
        "contract_address": r.contract_address,
        "chain": r.chain,
        "dex": r.dex,
        "is_cex_listed": r.is_cex_listed,
        "cex_name": r.cex_name,
        "price_usd": float(r.price_usd) if r.price_usd is not None else None,
        "price_change_pct": float(r.price_change_pct) if r.price_change_pct is not None else None,
        "market_cap_usd": float(r.market_cap_usd) if r.market_cap_usd is not None else None,
        "volume_usd_current": float(r.volume_usd_current) if r.volume_usd_current is not None else None,
        "volume_mcap_ratio": float(r.volume_mcap_ratio) if r.volume_mcap_ratio is not None else None,
        "risk_score": r.risk_score,
        "risk_score_numeric": r.risk_score_numeric,
        "risk_flags": r.risk_flags,
        "detected_at": r.detected_at.isoformat(),
    }


@router.get("/alerts")
async def list_alerts(
    _user: CurrentUser,
    db: DBSession,
    risk_score: str | None = Query(default=None),
    chain: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(GemRadarAlert).where(GemRadarAlert.is_cex_listed.is_(False)).order_by(desc(GemRadarAlert.detected_at))
    if risk_score:
        stmt = stmt.where(GemRadarAlert.risk_score == risk_score)
    if chain:
        stmt = stmt.where(GemRadarAlert.chain == chain.lower())
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/trending")
async def trending(_user: CurrentUser, db: DBSession, limit: int = Query(default=20, ge=1, le=100)):
    stmt = (
        select(GemRadarAlert)
        .where(GemRadarAlert.is_cex_listed.is_(False))
        .order_by(desc(GemRadarAlert.detected_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize(r) for r in rows]}


@router.get("/new-listings")
async def new_listings(_user: CurrentUser, db: DBSession, limit: int = Query(default=50, ge=1, le=200)):
    stmt = (
        select(GemRadarAlert)
        .where(GemRadarAlert.is_cex_listed.is_(True))
        .order_by(desc(GemRadarAlert.detected_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize(r) for r in rows]}
