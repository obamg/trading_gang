"""WalletWatch API."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select

from app.dependencies import CurrentUser, DBSession
from app.models.walletwatch import WalletSwap
from app.models.whale_entity import WhaleEntity

router = APIRouter(prefix="/walletwatch", tags=["walletwatch"])


@router.get("/recent")
async def list_recent(
    _user: CurrentUser,
    db: DBSession,
    chain: str | None = Query(default=None),
    swap_type: str | None = Query(default=None, regex="^(buy|sell|rotate)$"),
    entity_id: str | None = Query(default=None),
    min_usd: float = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(WalletSwap, WhaleEntity)
        .join(WhaleEntity, WhaleEntity.id == WalletSwap.entity_id, isouter=True)
        .order_by(desc(WalletSwap.detected_at))
    )
    if chain:
        stmt = stmt.where(WalletSwap.chain == chain.lower())
    if swap_type:
        stmt = stmt.where(WalletSwap.swap_type == swap_type)
    if entity_id:
        stmt = stmt.where(WalletSwap.entity_id == entity_id)
    if min_usd > 0:
        stmt = stmt.where(WalletSwap.amount_usd >= min_usd)
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {
                "id": str(s.id),
                "wallet_address": s.wallet_address,
                "entity_id": str(s.entity_id) if s.entity_id else None,
                "entity_name": e.name if e else None,
                "entity_conviction": float(e.conviction_score)
                if e and e.conviction_score is not None
                else None,
                "chain": s.chain,
                "swap_type": s.swap_type,
                "venue": s.venue,
                "token_in_address": s.token_in_address,
                "token_in_symbol": s.token_in_symbol,
                "token_in_amount": float(s.token_in_amount),
                "token_out_address": s.token_out_address,
                "token_out_symbol": s.token_out_symbol,
                "token_out_amount": float(s.token_out_amount),
                "amount_usd": float(s.amount_usd),
                "tx_hash": s.tx_hash,
                "detected_at": s.detected_at.isoformat(),
            }
            for (s, e) in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/top-tokens")
async def top_tokens(
    _user: CurrentUser,
    db: DBSession,
    chain: str | None = Query(default=None),
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Tokens being net-bought by smart money in the last N hours.

    Layer 1 ranking — counts distinct buyers and sums USD. Cluster-aware
    ranking comes in Layer 3.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(
            WalletSwap.chain,
            WalletSwap.token_out_address,
            WalletSwap.token_out_symbol,
            func.count(func.distinct(WalletSwap.wallet_address)).label("buyer_count"),
            func.sum(WalletSwap.amount_usd).label("total_usd"),
            func.count().label("buy_count"),
        )
        .where(
            WalletSwap.swap_type == "buy",
            WalletSwap.detected_at >= since,
        )
        .group_by(WalletSwap.chain, WalletSwap.token_out_address, WalletSwap.token_out_symbol)
        .order_by(desc("total_usd"))
        .limit(limit)
    )
    if chain:
        stmt = stmt.where(WalletSwap.chain == chain.lower())
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {
                "chain": r.chain,
                "token_address": r.token_out_address,
                "token_symbol": r.token_out_symbol,
                "buyer_count": int(r.buyer_count),
                "buy_count": int(r.buy_count),
                "total_usd": float(r.total_usd or 0),
            }
            for r in rows
        ],
        "since": since.isoformat(),
    }


@router.get("/stats")
async def stats(_user: CurrentUser, db: DBSession):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    counts_stmt = (
        select(WalletSwap.swap_type, func.count())
        .where(WalletSwap.detected_at >= since)
        .group_by(WalletSwap.swap_type)
    )
    counts = {row[0]: int(row[1]) for row in (await db.execute(counts_stmt)).all()}
    total_buy_usd = (
        await db.execute(
            select(func.coalesce(func.sum(WalletSwap.amount_usd), 0)).where(
                WalletSwap.detected_at >= since,
                WalletSwap.swap_type == "buy",
            )
        )
    ).scalar_one()
    return {
        "buys_24h": counts.get("buy", 0),
        "sells_24h": counts.get("sell", 0),
        "rotates_24h": counts.get("rotate", 0),
        "total_buy_usd_24h": float(total_buy_usd or 0),
    }
