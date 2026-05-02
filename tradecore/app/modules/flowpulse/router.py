"""FlowPulse API — order flow signals."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import desc, select, func

from app.dependencies import CurrentUser, DBSession
from app.models.flowpulse import FlowSignal
from app.modules.flowpulse.detector import get_flow_signal
from app.services import redis_service

router = APIRouter(prefix="/flowpulse", tags=["flowpulse"])


def _serialize(row: FlowSignal) -> dict:
    return {
        "id": str(row.id),
        "symbol": row.symbol,
        "bid_usd": float(row.bid_usd) if row.bid_usd is not None else None,
        "ask_usd": float(row.ask_usd) if row.ask_usd is not None else None,
        "book_imbalance": float(row.book_imbalance) if row.book_imbalance is not None else None,
        "taker_buy_vol": float(row.taker_buy_vol) if row.taker_buy_vol is not None else None,
        "taker_sell_vol": float(row.taker_sell_vol) if row.taker_sell_vol is not None else None,
        "taker_ratio": float(row.taker_ratio) if row.taker_ratio is not None else None,
        "top_long_ratio": float(row.top_long_ratio) if row.top_long_ratio is not None else None,
        "top_short_ratio": float(row.top_short_ratio) if row.top_short_ratio is not None else None,
        "direction": row.direction,
        "intensity": float(row.intensity) if row.intensity is not None else None,
        "snapshot_at": row.snapshot_at.isoformat(),
    }


@router.get("/live/{symbol}")
async def live_signal(symbol: str, _user: CurrentUser):
    data = await get_flow_signal(symbol.upper())
    if not data:
        return {"symbol": symbol.upper(), "signal": None}
    return {"symbol": symbol.upper(), "signal": data}


@router.get("/overview")
async def overview(_user: CurrentUser):
    """Return smoothed (rolling average) signals from Redis cache."""
    r = redis_service.get_redis()
    symbols = await redis_service.get_symbol_list()
    items = []
    snapshot_at = None
    for symbol in symbols[:30]:
        raw = await r.get(f"flow:{symbol}")
        if not raw:
            continue
        data = json.loads(raw)
        items.append({
            "id": symbol,
            "symbol": symbol,
            "bid_usd": data.get("bid_usd"),
            "ask_usd": data.get("ask_usd"),
            "book_imbalance": data.get("book_imbalance"),
            "taker_buy_vol": None,
            "taker_sell_vol": None,
            "taker_ratio": data.get("taker_ratio"),
            "top_long_ratio": data.get("top_long_ratio"),
            "top_short_ratio": round(100 - data["top_long_ratio"], 2) if data.get("top_long_ratio") is not None else None,
            "direction": data.get("direction"),
            "intensity": data.get("intensity"),
            "window": data.get("window", 1),
            "snapshot_at": data.get("ts"),
        })
        if snapshot_at is None:
            snapshot_at = data.get("ts")
    return {"items": items, "snapshot_at": snapshot_at}


@router.get("/history/{symbol}")
async def history(
    symbol: str,
    _user: CurrentUser,
    db: DBSession,
    hours: int = Query(default=24, ge=1, le=168),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(FlowSignal)
            .where(FlowSignal.symbol == symbol.upper(), FlowSignal.snapshot_at >= since)
            .order_by(desc(FlowSignal.snapshot_at))
            .limit(500)
        )
    ).scalars().all()
    return {"symbol": symbol.upper(), "items": [_serialize(r) for r in rows]}


@router.get("/extremes")
async def extremes(
    _user: CurrentUser,
    db: DBSession,
    hours: int = Query(default=1, ge=1, le=24),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(FlowSignal)
            .where(
                FlowSignal.snapshot_at >= since,
                FlowSignal.intensity >= 0.5,
            )
            .order_by(desc(FlowSignal.intensity))
            .limit(50)
        )
    ).scalars().all()
    return {"items": [_serialize(r) for r in rows]}


@router.get("/stats")
async def stats(_user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(
            func.count(FlowSignal.id),
            func.count(FlowSignal.id).filter(FlowSignal.direction == "bullish"),
            func.count(FlowSignal.id).filter(FlowSignal.direction == "bearish"),
            func.avg(FlowSignal.book_imbalance),
            func.avg(FlowSignal.taker_ratio),
        ).where(FlowSignal.snapshot_at >= since)
    )
    row = result.one()
    return {
        "snapshots_24h": row[0],
        "bullish_24h": row[1],
        "bearish_24h": row[2],
        "avg_book_imbalance": round(float(row[3]), 4) if row[3] else None,
        "avg_taker_ratio": round(float(row[4]), 4) if row[4] else None,
    }
