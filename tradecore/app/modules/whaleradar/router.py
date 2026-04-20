"""WhaleRadar API."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select

from app.dependencies import CurrentUser, DBSession
from app.models.whaleradar import OISurgeEvent, WhaleOnchainTransfer, WhaleTrade

router = APIRouter(
    prefix="/whaleradar", tags=["whaleradar"],
)


@router.get("/trades")
async def list_trades(
    _user: CurrentUser,
    db: DBSession,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(WhaleTrade).order_by(desc(WhaleTrade.detected_at))
    if symbol:
        stmt = stmt.where(WhaleTrade.symbol == symbol.upper())
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "symbol": r.symbol,
                "trade_size_usd": float(r.trade_size_usd),
                "side": r.side,
                "price": float(r.price),
                "exchange": r.exchange,
                "detected_at": r.detected_at.isoformat(),
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/onchain")
async def list_onchain(
    _user: CurrentUser,
    db: DBSession,
    asset: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(WhaleOnchainTransfer).order_by(desc(WhaleOnchainTransfer.detected_at))
    if asset:
        stmt = stmt.where(WhaleOnchainTransfer.asset == asset.upper())
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "asset": r.asset,
                "amount": float(r.amount),
                "amount_usd": float(r.amount_usd),
                "from_label": r.from_label,
                "to_label": r.to_label,
                "transfer_type": r.transfer_type,
                "chain": r.chain,
                "tx_hash": r.tx_hash,
                "detected_at": r.detected_at.isoformat(),
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/oi-surges")
async def list_oi_surges(
    _user: CurrentUser,
    db: DBSession,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(OISurgeEvent).order_by(desc(OISurgeEvent.detected_at))
    if symbol:
        stmt = stmt.where(OISurgeEvent.symbol == symbol.upper())
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "symbol": r.symbol,
                "oi_change_pct": float(r.oi_change_pct),
                "oi_before_usd": float(r.oi_before_usd),
                "oi_after_usd": float(r.oi_after_usd),
                "price": float(r.price),
                "price_change_pct": float(r.price_change_pct) if r.price_change_pct is not None else None,
                "direction": r.direction,
                "detected_at": r.detected_at.isoformat(),
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats")
async def stats(_user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    trades_count = (
        await db.execute(select(func.count()).select_from(WhaleTrade).where(WhaleTrade.detected_at >= since))
    ).scalar_one()
    onchain_count = (
        await db.execute(
            select(func.count()).select_from(WhaleOnchainTransfer).where(WhaleOnchainTransfer.detected_at >= since)
        )
    ).scalar_one()
    oi_count = (
        await db.execute(select(func.count()).select_from(OISurgeEvent).where(OISurgeEvent.detected_at >= since))
    ).scalar_one()
    total_trade_usd = (
        await db.execute(
            select(func.coalesce(func.sum(WhaleTrade.trade_size_usd), 0)).where(WhaleTrade.detected_at >= since)
        )
    ).scalar_one()
    return {
        "trades_24h": int(trades_count),
        "onchain_24h": int(onchain_count),
        "oi_surges_24h": int(oi_count),
        "total_trade_volume_usd_24h": float(total_trade_usd),
    }
