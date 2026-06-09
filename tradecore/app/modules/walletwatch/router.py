"""WalletWatch API."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select

from app.dependencies import CurrentUser, DBSession
from app.models.walletwatch import WalletSwap
from app.models.walletwatch_discovery import (
    DiscoveryToken,
    WalletPnlScore,
    WalletTokenPnl,
)
from app.models.whale_entity import WhaleEntity
from app.modules.walletwatch.classifier import all_major_addresses
from app.modules.walletwatch.discovery.promote import promote_score

router = APIRouter(prefix="/walletwatch", tags=["walletwatch"])


@router.get("/recent")
async def list_recent(
    _user: CurrentUser,
    db: DBSession,
    chain: str | None = Query(default=None),
    swap_type: str | None = Query(default=None, regex="^(buy|sell|rotate)$"),
    entity_id: str | None = Query(default=None),
    min_usd: float = Query(default=0, ge=0),
    include_majors: bool = Query(default=False),
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
    if not include_majors:
        stmt = stmt.where(WalletSwap.token_out_address.notin_(all_major_addresses()))
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
    include_majors: bool = Query(default=False),
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
    if not include_majors:
        stmt = stmt.where(WalletSwap.token_out_address.notin_(all_major_addresses()))
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


@router.get("/discovery/leaderboard")
async def discovery_leaderboard(
    _user: CurrentUser,
    db: DBSession,
    chain: str | None = Query(default=None),
    min_realized: float = Query(default=0, ge=0),
    min_win_rate: float = Query(default=0, ge=0, le=1),
    min_token_count: int = Query(default=1, ge=1),
    only_unpromoted: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Top candidate wallets by discovery_score, filterable.

    The default ``only_unpromoted=True`` shows wallets we haven't already
    pulled into ``whale_entities`` so this surfaces *new* findings.
    """
    stmt = select(WalletPnlScore).order_by(desc(WalletPnlScore.discovery_score))
    if chain:
        stmt = stmt.where(WalletPnlScore.chain == chain.lower())
    if min_realized > 0:
        stmt = stmt.where(WalletPnlScore.total_realized_usd >= min_realized)
    if min_win_rate > 0:
        stmt = stmt.where(WalletPnlScore.win_rate >= min_win_rate)
    if min_token_count > 1:
        stmt = stmt.where(WalletPnlScore.token_count >= min_token_count)
    if only_unpromoted:
        stmt = stmt.where(WalletPnlScore.promoted_at.is_(None))
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "wallet_address": r.wallet_address,
                "chain": r.chain,
                "total_realized_usd": float(r.total_realized_usd),
                "total_unrealized_usd": float(r.total_unrealized_usd),
                "total_cost_basis_usd": float(r.total_cost_basis_usd),
                "win_count": r.win_count,
                "loss_count": r.loss_count,
                "win_rate": float(r.win_rate),
                "avg_multiple": float(r.avg_multiple),
                "best_multiple": float(r.best_multiple),
                "token_count": r.token_count,
                "discovery_score": float(r.discovery_score),
                "promoted_at": r.promoted_at.isoformat() if r.promoted_at else None,
                "last_scored_at": r.last_scored_at.isoformat(),
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/discovery/wallet/{wallet_address}")
async def discovery_wallet_detail(
    _user: CurrentUser,
    db: DBSession,
    wallet_address: str,
):
    """Per-token PnL breakdown for one wallet — sanity-check a leaderboard row."""
    addr = wallet_address.lower() if wallet_address.startswith("0x") else wallet_address
    score = (
        await db.execute(
            select(WalletPnlScore).where(WalletPnlScore.wallet_address == addr)
        )
    ).scalar_one_or_none()
    rows = (
        await db.execute(
            select(WalletTokenPnl)
            .where(WalletTokenPnl.wallet_address == addr)
            .order_by(desc(WalletTokenPnl.realized_pnl_usd + WalletTokenPnl.unrealized_pnl_usd))
        )
    ).scalars().all()
    return {
        "wallet_address": addr,
        "score": (
            {
                "discovery_score": float(score.discovery_score),
                "total_realized_usd": float(score.total_realized_usd),
                "total_unrealized_usd": float(score.total_unrealized_usd),
                "win_count": score.win_count,
                "loss_count": score.loss_count,
                "win_rate": float(score.win_rate),
                "best_multiple": float(score.best_multiple),
                "token_count": score.token_count,
                "promoted_at": score.promoted_at.isoformat() if score.promoted_at else None,
            }
            if score
            else None
        ),
        "tokens": [
            {
                "chain": r.chain,
                "token_address": r.token_address,
                "token_symbol": r.token_symbol,
                "total_buy_usd": float(r.total_buy_usd),
                "total_sell_usd": float(r.total_sell_usd),
                "current_value_usd": float(r.current_value_usd),
                "realized_pnl_usd": float(r.realized_pnl_usd),
                "unrealized_pnl_usd": float(r.unrealized_pnl_usd),
                "multiple": float(r.multiple) if r.multiple is not None else None,
                "first_buy_at": r.first_buy_at.isoformat() if r.first_buy_at else None,
            }
            for r in rows
        ],
    }


@router.get("/discovery/tokens")
async def discovery_tokens(
    _user: CurrentUser,
    db: DBSession,
    chain: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Tokens currently being scored (debugging visibility)."""
    stmt = select(DiscoveryToken).order_by(desc(DiscoveryToken.discovered_at))
    if chain:
        stmt = stmt.where(DiscoveryToken.chain == chain.lower())
    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "chain": r.chain,
                "address": r.address,
                "symbol": r.symbol,
                "name": r.name,
                "source": r.source,
                "discovered_at": r.discovered_at.isoformat(),
                "last_scored_at": r.last_scored_at.isoformat() if r.last_scored_at else None,
            }
            for r in rows
        ]
    }


@router.post("/discovery/promote/{wallet_address}")
async def discovery_promote(
    _user: CurrentUser,
    db: DBSession,
    wallet_address: str,
    name: str = Query(..., min_length=1, max_length=100),
    entity_type: str = Query(default="smart_money"),
):
    """Promote a discovered wallet into whale_entities (manual review gate).

    Idempotent — re-promoting an already-promoted wallet is a no-op. Shares
    the promotion logic with the scheduler's auto-promote job so both paths
    create identically-shaped entities.
    """
    addr = wallet_address.lower() if wallet_address.startswith("0x") else wallet_address
    score = (
        await db.execute(select(WalletPnlScore).where(WalletPnlScore.wallet_address == addr))
    ).scalar_one_or_none()
    if score is None:
        return {"ok": False, "reason": "wallet_not_in_discovery"}
    result = await promote_score(db, score, name=name, entity_type=entity_type)
    await db.commit()
    return result


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
