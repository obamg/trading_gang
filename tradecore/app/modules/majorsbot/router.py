"""MajorsBot — API surface.

  GET /majorsbot/status     enabled flags, paper equity, open/pending counts, config
  GET /majorsbot/trades     trades, filterable by strategy/symbol/status
  GET /majorsbot/analytics  per-strategy closed-trade stats (n, win%, net R, totals)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import and_, case, desc, func, select

from app.config import settings as app_settings
from app.dependencies import CurrentUser, DBSession
from app.models.majorsbot import MajorsBotTrade
from app.modules.majorsbot import engine, equity

router = APIRouter(prefix="/majorsbot", tags=["majorsbot"])


@router.get("/status")
async def status(_user: CurrentUser, db: DBSession):
    paper_equity = await equity.get_paper_equity()
    concurrent = await equity.get_concurrent_count()
    open_count = (
        await db.execute(
            select(func.count())
            .select_from(MajorsBotTrade)
            .where(MajorsBotTrade.status == "open")
        )
    ).scalar_one()
    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(MajorsBotTrade)
            .where(MajorsBotTrade.status == "pending")
        )
    ).scalar_one()
    return {
        "enabled": bool(getattr(app_settings, "majorsbot_enabled", False)),
        "paper_equity": float(paper_equity),
        "open_positions": int(open_count),
        "pending_orders": int(pending_count),
        "concurrent_count": concurrent,
        "max_concurrent": int(app_settings.majorsbot_max_concurrent),
        "config": {
            "symbols": engine.symbol_list(),
            "volevent_enabled": bool(app_settings.majorsbot_volevent_enabled),
            "fundingfade_enabled": bool(app_settings.majorsbot_fundingfade_enabled),
            "newsevent_enabled": bool(
                getattr(app_settings, "majorsbot_newsevent_enabled", False)
            ),
            "newsevent_primary_only": bool(
                getattr(app_settings, "majorsbot_newsevent_primary_only", True)
            ),
            "newsevent_retrace_entry": bool(
                getattr(app_settings, "majorsbot_newsevent_retrace_entry", True)
            ),
            "paper_equity_initial": float(app_settings.majorsbot_paper_equity_initial),
            "risk_per_trade_pct": float(app_settings.majorsbot_risk_per_trade_pct),
            "position_size_pct": float(app_settings.majorsbot_position_size_pct),
            "maker_fee_pct": float(app_settings.majorsbot_maker_fee_pct),
            "taker_fee_pct": float(app_settings.majorsbot_taker_fee_pct),
            "slippage_pct": float(app_settings.majorsbot_slippage_pct),
            "max_hold_hours": int(app_settings.majorsbot_max_hold_hours),
        },
    }


@router.get("/trades")
async def trades(
    _user: CurrentUser,
    db: DBSession,
    strategy: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    status: str = Query(default="closed"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(MajorsBotTrade).order_by(
        desc(func.coalesce(MajorsBotTrade.closed_at, MajorsBotTrade.entry_at))
    )
    if status and status != "all":
        stmt = stmt.where(MajorsBotTrade.status == status)
    if strategy:
        stmt = stmt.where(MajorsBotTrade.strategy == strategy)
    if symbol:
        stmt = stmt.where(MajorsBotTrade.symbol == symbol.upper())
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize_trade(t) for t in rows], "limit": limit, "offset": offset}


@router.get("/analytics")
async def analytics(
    _user: CurrentUser, db: DBSession, days: int = Query(default=90, ge=1, le=365)
):
    """Per-strategy stats over closed trades in the window.

    Two expectancy metrics, deliberately:

    - ``avg_r_net`` — the pre-committed gate metric for volevent/fundingfade,
      whose R denominator is a real stop that really executes.
    - ``avg_pct_equity`` — realized P&L over the equity at entry. This is the
      honest metric for a STOPLESS strategy: newsevent sizes purely off the
      notional cap and never touches its reference stop, so 1R there is a
      phantom unit that ranged 5.0%–51.1% of equity across the first 11 trades.
      Two trades doing near-identical damage (−57.6% and −48.3% of the book)
      booked −1.13R and −9.12R. Averaging those measures spike-bar height, not
      performance. Judge newsevent on avg_pct_equity.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = and_(MajorsBotTrade.status == "closed", MajorsBotTrade.closed_at >= since)

    pct_eq_expr = func.coalesce(
        func.sum(
            MajorsBotTrade.realized_pnl_usd
            / func.nullif(MajorsBotTrade.paper_equity_at_entry, 0)
            * 100
        ),
        0,
    )
    n_expr = func.count()
    wins_expr = func.sum(case((MajorsBotTrade.realized_pnl_usd > 0, 1), else_=0))
    pnl_expr = func.coalesce(func.sum(MajorsBotTrade.realized_pnl_usd), 0)
    r_expr = func.coalesce(func.sum(MajorsBotTrade.realized_r), 0)
    r_net_expr = func.coalesce(func.sum(MajorsBotTrade.realized_r_net), 0)
    fees_expr = func.coalesce(func.sum(MajorsBotTrade.fees_usd), 0)
    funding_expr = func.coalesce(func.sum(MajorsBotTrade.funding_pnl_usd), 0)

    def _row(label, n, wins, pnl, r_sum, r_net_sum, fees, funding, pct_eq_sum):
        n_i = int(n or 0)
        wins_i = int(wins or 0)
        return {
            "label": label,
            "n_trades": n_i,
            "wins": wins_i,
            "win_rate": (wins_i / n_i) if n_i else None,
            "realized_pnl_usd": float(pnl or 0),
            "realized_r": float(r_sum or 0),
            "realized_r_net": float(r_net_sum or 0),
            "avg_r_net": (float(r_net_sum or 0) / n_i) if n_i else None,
            "expectancy_r_net": (float(r_net_sum or 0) / n_i) if n_i else None,
            "pct_equity": float(pct_eq_sum or 0),
            "avg_pct_equity": (float(pct_eq_sum or 0) / n_i) if n_i else None,
            "fees_usd": float(fees or 0),
            "funding_pnl_usd": float(funding or 0),
        }

    exprs = (
        n_expr, wins_expr, pnl_expr, r_expr, r_net_expr, fees_expr, funding_expr,
        pct_eq_expr,
    )
    strat_rows = (
        await db.execute(
            select(MajorsBotTrade.strategy, *exprs)
            .where(base)
            .group_by(MajorsBotTrade.strategy)
        )
    ).all()
    dir_rows = (
        await db.execute(
            select(MajorsBotTrade.strategy, MajorsBotTrade.direction, *exprs)
            .where(base)
            .group_by(MajorsBotTrade.strategy, MajorsBotTrade.direction)
        )
    ).all()
    sym_rows = (
        await db.execute(
            select(MajorsBotTrade.symbol, *exprs)
            .where(base)
            .group_by(MajorsBotTrade.symbol)
            .order_by(desc(n_expr))
        )
    ).all()

    return {
        "days": days,
        "by_strategy": [_row(r[0], *r[1:]) for r in strat_rows],
        "by_strategy_direction": [_row(f"{r[0]}/{r[1]}", *r[2:]) for r in dir_rows],
        "by_symbol": [_row(r[0], *r[1:]) for r in sym_rows],
        "paper_equity": float(await equity.get_paper_equity()),
    }


def _serialize_trade(t: MajorsBotTrade) -> dict:
    def _f(v):
        return float(v) if v is not None else None

    return {
        "id": str(t.id),
        "symbol": t.symbol,
        "exchange": t.exchange,
        "market_type": t.market_type,
        "direction": t.direction,
        "strategy": t.strategy,
        "signal_at": t.signal_at.isoformat(),
        "entry_price": float(t.entry_price),
        "entry_at": t.entry_at.isoformat(),
        "entry_bar_at": t.entry_bar_at.isoformat() if t.entry_bar_at else None,
        "entry_mode": t.entry_mode,
        "limit_price": _f(t.limit_price),
        "expire_at": t.expire_at.isoformat() if t.expire_at else None,
        "signal_high": _f(t.signal_high),
        "signal_low": _f(t.signal_low),
        "notional_usd": float(t.notional_usd),
        "qty": float(t.qty),
        "paper_equity_at_entry": float(t.paper_equity_at_entry),
        "stop_price": float(t.stop_price),
        "initial_stop_price": _f(t.initial_stop_price),
        "take_profit_price": _f(t.take_profit_price),
        "peak_price": _f(t.peak_price),
        "partial_exit_price": _f(t.partial_exit_price),
        "partial_exit_at": t.partial_exit_at.isoformat() if t.partial_exit_at else None,
        "partial_pnl_usd": _f(t.partial_pnl_usd),
        "partial_qty": _f(t.partial_qty),
        "close_price": _f(t.close_price),
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "close_reason": t.close_reason,
        "realized_pnl_usd": _f(t.realized_pnl_usd),
        "realized_r": _f(t.realized_r),
        "realized_r_net": _f(t.realized_r_net),
        "realized_pct_equity": (
            float(t.realized_pnl_usd / t.paper_equity_at_entry * 100)
            if t.realized_pnl_usd is not None and t.paper_equity_at_entry
            else None
        ),
        "fees_usd": _f(t.fees_usd),
        "funding_pnl_usd": _f(t.funding_pnl_usd),
        "funding_rate_at_entry": _f(t.funding_rate_at_entry),
        "funding_pctile_at_entry": _f(t.funding_pctile_at_entry),
        "status": t.status,
    }
