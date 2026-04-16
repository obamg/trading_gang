"""MacroPulse API."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.macro import EconomicEvent, MacroSnapshot
from app.modules.macropulse.score import compute_macro_context
from app.services import redis_service

router = APIRouter(prefix="/macro", tags=["macropulse"])


@router.get("/snapshot")
async def snapshot(_user: CurrentUser):
    """Latest cached tickers. Falls back to empty object if none populated."""
    r = redis_service.get_redis()
    out: dict[str, dict] = {}
    for key in ("dxy", "us10y", "us2y", "vix", "sp500", "nasdaq", "gold"):
        raw = await r.get(f"macro:{key}")
        if raw:
            try:
                out[key] = json.loads(raw)
            except json.JSONDecodeError:
                continue
    return {"items": out}


@router.get("/score")
async def score(_user: CurrentUser):
    return await compute_macro_context()


@router.get("/calendar")
async def calendar(
    _user: CurrentUser,
    db: DBSession,
    impact: str | None = Query(default=None, pattern="^(high|medium|low)$"),
    days: int = Query(default=7, ge=1, le=30),
):
    now = datetime.now(timezone.utc)
    stmt = (
        select(EconomicEvent)
        .where(EconomicEvent.scheduled_at >= now, EconomicEvent.scheduled_at <= now + timedelta(days=days))
        .order_by(EconomicEvent.scheduled_at)
    )
    if impact:
        stmt = stmt.where(EconomicEvent.impact == impact)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "name": r.name,
                "country": r.country,
                "impact": r.impact,
                "scheduled_at": r.scheduled_at.isoformat(),
                "forecast_value": r.forecast_value,
                "actual_value": r.actual_value,
                "previous_value": r.previous_value,
            }
            for r in rows
        ]
    }


@router.get("/etf-flows")
async def etf_flows(_user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(days=30)
    rows = (
        await db.execute(
            select(MacroSnapshot)
            .where(MacroSnapshot.snapshot_at >= since, MacroSnapshot.btc_etf_flows_usd.is_not(None))
            .order_by(MacroSnapshot.snapshot_at)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "snapshot_at": r.snapshot_at.isoformat(),
                "flow_usd": float(r.btc_etf_flows_usd) if r.btc_etf_flows_usd is not None else None,
            }
            for r in rows
        ]
    }


@router.get("/history")
async def history(_user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(days=90)
    rows = (
        await db.execute(
            select(MacroSnapshot).where(MacroSnapshot.snapshot_at >= since).order_by(MacroSnapshot.snapshot_at)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "snapshot_at": r.snapshot_at.isoformat(),
                "dxy": float(r.dxy) if r.dxy is not None else None,
                "vix": float(r.vix) if r.vix is not None else None,
                "us10y": float(r.us10y) if r.us10y is not None else None,
                "us2y": float(r.us2y) if r.us2y is not None else None,
                "sp500": float(r.sp500) if r.sp500 is not None else None,
                "nasdaq": float(r.nasdaq) if r.nasdaq is not None else None,
                "gold_usd": float(r.gold_usd) if r.gold_usd is not None else None,
                "btc_etf_flows_usd": float(r.btc_etf_flows_usd) if r.btc_etf_flows_usd is not None else None,
                "stablecoin_mcap_usd": float(r.stablecoin_mcap_usd) if r.stablecoin_mcap_usd is not None else None,
                "macro_score": r.macro_score,
            }
            for r in rows
        ]
    }
