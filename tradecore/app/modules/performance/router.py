"""PerformanceCore API."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import case, desc, extract, func, select

from app.dependencies import CurrentUser, DBSession
from app.models.tradelog import Trade
from app.modules.performance.aggregator import compute_signal_accuracy, compute_user_performance

router = APIRouter(prefix="/performance", tags=["performance"])


def _serialize_snap(snap) -> dict:
    return {
        "period": snap.period,
        "period_start": snap.period_start.isoformat(),
        "period_end": snap.period_end.isoformat(),
        "is_paper": snap.is_paper,
        "total_trades": snap.total_trades,
        "winning_trades": snap.winning_trades,
        "losing_trades": snap.losing_trades,
        "breakeven_trades": snap.breakeven_trades,
        "win_rate": float(snap.win_rate) if snap.win_rate else None,
        "avg_win_pct": float(snap.avg_win_pct) if snap.avg_win_pct else None,
        "avg_loss_pct": float(snap.avg_loss_pct) if snap.avg_loss_pct else None,
        "avg_rr_achieved": float(snap.avg_rr_achieved) if snap.avg_rr_achieved else None,
        "expectancy": float(snap.expectancy) if snap.expectancy else None,
        "profit_factor": float(snap.profit_factor) if snap.profit_factor else None,
        "total_pnl_usd": float(snap.total_pnl_usd) if snap.total_pnl_usd else None,
        "total_fees_usd": float(snap.total_fees_usd) if snap.total_fees_usd else None,
        "net_pnl_usd": float(snap.net_pnl_usd) if snap.net_pnl_usd else None,
        "max_drawdown_pct": float(snap.max_drawdown_pct) if snap.max_drawdown_pct else None,
        "max_drawdown_usd": float(snap.max_drawdown_usd) if snap.max_drawdown_usd else None,
        "max_consecutive_losses": snap.max_consecutive_losses,
        "best_trade_pnl_usd": float(snap.best_trade_pnl_usd) if snap.best_trade_pnl_usd else None,
        "worst_trade_pnl_usd": float(snap.worst_trade_pnl_usd) if snap.worst_trade_pnl_usd else None,
        "best_setup": snap.best_setup,
        "computed_at": snap.computed_at.isoformat(),
    }


@router.get("/overview")
async def overview(
    user: CurrentUser,
    db: DBSession,
    is_paper: bool = Query(default=False),
):
    periods = ["7d", "30d", "90d", "all"]
    out = {}
    for p in periods:
        snap = await compute_user_performance(db, user.id, period=p, is_paper=is_paper)
        out[p] = _serialize_snap(snap)
    return out


@router.get("/equity-curve")
async def equity_curve(
    user: CurrentUser,
    db: DBSession,
    days: int = Query(default=90, ge=1, le=365),
    is_paper: bool = Query(default=False),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(Trade)
            .where(
                Trade.user_id == user.id,
                Trade.status == "closed",
                Trade.is_paper.is_(is_paper),
                Trade.exit_at >= since,
            )
            .order_by(Trade.exit_at)
        )
    ).scalars().all()
    running = 0.0
    points = []
    for t in rows:
        running += float(t.net_pnl_usd or 0)
        points.append({"t": t.exit_at.isoformat(), "equity": round(running, 2)})
    return {"points": points}


@router.get("/by-setup")
async def by_setup(user: CurrentUser, db: DBSession):
    stmt = (
        select(
            Trade.setup_name,
            func.count(Trade.id).label("total"),
            func.sum(func.coalesce(Trade.net_pnl_usd, 0)).label("net"),
            func.avg(func.coalesce(Trade.pnl_pct, 0)).label("avg_pct"),
            func.avg(func.coalesce(Trade.r_multiple, 0)).label("avg_r"),
            func.sum(case((Trade.net_pnl_usd > 0, 1), else_=0)).label("wins"),
        )
        .where(
            Trade.user_id == user.id,
            Trade.status == "closed",
            Trade.setup_name.isnot(None),
        )
        .group_by(Trade.setup_name)
    )
    rows = (await db.execute(stmt)).all()
    out = []
    for name, total, net, avg_pct, avg_r, wins in rows:
        wr = (float(wins) / total * 100) if total else None
        out.append({
            "setup": name,
            "total_trades": int(total),
            "win_rate": round(wr, 2) if wr is not None else None,
            "avg_pnl_pct": float(avg_pct) if avg_pct is not None else None,
            "avg_r_multiple": float(avg_r) if avg_r is not None else None,
            "net_pnl_usd": float(net) if net is not None else None,
        })
    return {"items": out}


@router.get("/by-symbol")
async def by_symbol(user: CurrentUser, db: DBSession, limit: int = Query(default=20, ge=1, le=100)):
    stmt = (
        select(
            Trade.symbol,
            func.count(Trade.id),
            func.sum(func.coalesce(Trade.net_pnl_usd, 0)),
        )
        .where(Trade.user_id == user.id, Trade.status == "closed")
        .group_by(Trade.symbol)
        .order_by(desc(func.sum(func.coalesce(Trade.net_pnl_usd, 0))))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {"symbol": sym, "total_trades": int(total), "net_pnl_usd": float(net or 0)}
            for sym, total, net in rows
        ]
    }


@router.get("/by-time")
async def by_time(user: CurrentUser, db: DBSession):
    # By hour of day
    hour_stmt = (
        select(
            extract("hour", Trade.entry_at).label("h"),
            func.count(Trade.id),
            func.sum(func.coalesce(Trade.net_pnl_usd, 0)),
        )
        .where(Trade.user_id == user.id, Trade.status == "closed")
        .group_by("h")
        .order_by("h")
    )
    hours = [
        {"hour": int(h), "trades": int(c), "net_pnl_usd": float(n or 0)}
        for h, c, n in (await db.execute(hour_stmt)).all()
    ]
    # By day of week (0=Sunday in postgres extract('dow'))
    dow_stmt = (
        select(
            extract("dow", Trade.entry_at).label("d"),
            func.count(Trade.id),
            func.sum(func.coalesce(Trade.net_pnl_usd, 0)),
        )
        .where(Trade.user_id == user.id, Trade.status == "closed")
        .group_by("d")
        .order_by("d")
    )
    dow = [
        {"day_of_week": int(d), "trades": int(c), "net_pnl_usd": float(n or 0)}
        for d, c, n in (await db.execute(dow_stmt)).all()
    ]
    return {"by_hour": hours, "by_day_of_week": dow}


@router.get("/signals")
async def signals(_user: CurrentUser, db: DBSession, module: str = Query(default="oracle")):
    return await compute_signal_accuracy(db, module=module)


@router.get("/r-distribution")
async def r_distribution(user: CurrentUser, db: DBSession):
    rows = (
        await db.execute(
            select(Trade.r_multiple).where(
                Trade.user_id == user.id,
                Trade.status == "closed",
                Trade.r_multiple.isnot(None),
            )
        )
    ).all()
    rs = [float(r[0]) for r in rows]
    if not rs:
        return {"buckets": [], "total": 0}
    # Bucket into integer R ranges, clipping tails
    buckets: dict[str, int] = {}
    for r in rs:
        if r <= -3:
            key = "<=-3R"
        elif r <= -2:
            key = "-3R..-2R"
        elif r <= -1:
            key = "-2R..-1R"
        elif r <= 0:
            key = "-1R..0R"
        elif r <= 1:
            key = "0R..1R"
        elif r <= 2:
            key = "1R..2R"
        elif r <= 3:
            key = "2R..3R"
        else:
            key = ">3R"
        buckets[key] = buckets.get(key, 0) + 1
    order = ["<=-3R", "-3R..-2R", "-2R..-1R", "-1R..0R", "0R..1R", "1R..2R", "2R..3R", ">3R"]
    return {
        "buckets": [{"range": k, "count": buckets.get(k, 0)} for k in order],
        "total": len(rs),
    }
