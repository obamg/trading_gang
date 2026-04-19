"""TradeLog API."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select

from app.dependencies import CurrentUser, DBSession, require_feature
from app.errors import AppError
from app.models.tradelog import Trade, TradeTag
from app.modules.tradelog import service

router = APIRouter(
    prefix="/tradelog", tags=["tradelog"],
    dependencies=[Depends(require_feature("tradelog"))],
)


class TradeCreate(BaseModel):
    symbol: str
    asset_type: str = "futures"
    side: str = Field(pattern="^(long|short)$")
    entry_price: float = Field(gt=0)
    entry_at: datetime | None = None
    size: float = Field(gt=0)
    leverage: float = 1.0
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    is_paper: bool = False
    setup_name: str | None = None
    notes: str | None = None
    emotion: str | None = None
    followed_oracle: bool = False
    oracle_signal_id: UUID | None = None
    exchange: str | None = None
    exchange_trade_id: str | None = None
    tags: list[str] | None = None


class TradeUpdate(BaseModel):
    exit_price: float | None = Field(default=None, gt=0)
    exit_at: datetime | None = None
    fees_usd: float | None = Field(default=None, ge=0)
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    notes: str | None = None
    setup_name: str | None = None
    emotion: str | None = None
    followed_oracle: bool | None = None
    tags_add: list[str] | None = None


async def _tags_for(db, trade_id: UUID) -> list[str]:
    rows = (
        await db.execute(select(TradeTag.tag).where(TradeTag.trade_id == trade_id))
    ).all()
    return [t[0] for t in rows]


@router.get("/trades")
async def list_trades(
    user: CurrentUser,
    db: DBSession,
    status: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    is_paper: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(Trade)
        .where(Trade.user_id == user.id, Trade.status != "deleted")
        .order_by(desc(Trade.entry_at))
    )
    if status:
        stmt = stmt.where(Trade.status == status)
    if symbol:
        stmt = stmt.where(Trade.symbol == symbol.upper())
    if is_paper is not None:
        stmt = stmt.where(Trade.is_paper.is_(is_paper))
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        out.append(service.serialize(r, tags=await _tags_for(db, r.id)))
    return {"items": out}


@router.post("/trades")
async def create(payload: TradeCreate, user: CurrentUser, db: DBSession):
    data = payload.model_dump()
    trade = await service.create_trade(db, user.id, data)
    return service.serialize(trade, tags=await _tags_for(db, trade.id))


@router.patch("/trades/{trade_id}")
async def patch(trade_id: UUID, payload: TradeUpdate, user: CurrentUser, db: DBSession):
    updates = payload.model_dump(exclude_unset=True)
    trade = await service.update_trade(db, user.id, trade_id, updates)
    if trade is None:
        raise AppError(404, "Trade not found", "NOT_FOUND")
    return service.serialize(trade, tags=await _tags_for(db, trade.id))


@router.delete("/trades/{trade_id}")
async def delete(trade_id: UUID, user: CurrentUser, db: DBSession):
    ok = await service.soft_delete(db, user.id, trade_id)
    if not ok:
        raise AppError(404, "Trade not found", "NOT_FOUND")
    return {"deleted": True}


@router.get("/tags")
async def tags(user: CurrentUser, db: DBSession):
    stmt = (
        select(TradeTag.tag, func.count(TradeTag.id))
        .join(Trade, Trade.id == TradeTag.trade_id)
        .where(Trade.user_id == user.id, Trade.status != "deleted")
        .group_by(TradeTag.tag)
        .order_by(desc(func.count(TradeTag.id)))
    )
    rows = (await db.execute(stmt)).all()
    return {"items": [{"tag": t, "count": c} for t, c in rows]}


@router.get("/setups")
async def setups(user: CurrentUser, db: DBSession):
    stmt = (
        select(
            Trade.setup_name,
            func.count(Trade.id).label("total"),
            func.sum(func.coalesce(Trade.net_pnl_usd, 0)).label("net_pnl"),
            func.avg(func.coalesce(Trade.r_multiple, 0)).label("avg_r"),
        )
        .where(
            Trade.user_id == user.id,
            Trade.status == "closed",
            Trade.setup_name.isnot(None),
        )
        .group_by(Trade.setup_name)
        .order_by(desc(func.sum(func.coalesce(Trade.net_pnl_usd, 0))))
    )
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {
                "setup": name,
                "total_trades": int(total),
                "net_pnl_usd": float(net_pnl or 0),
                "avg_r_multiple": float(avg_r or 0),
            }
            for name, total, net_pnl, avg_r in rows
        ]
    }


@router.post("/sync")
async def sync(_user: CurrentUser):
    # Binance API key encryption + live fetch is a Team 8 concern —
    # we expose the endpoint so the frontend can wire it, but it's a stub.
    raise AppError(
        501,
        "Exchange sync requires connected API keys (not yet configured)",
        "SYNC_NOT_CONFIGURED",
    )
