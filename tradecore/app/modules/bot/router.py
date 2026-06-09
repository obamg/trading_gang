"""WaveBot — API surface.

  GET  /bot/status         enabled flag, equity, daily anchor, kill switch, concurrent count
  GET  /bot/positions      open positions
  GET  /bot/trades         closed trades, filterable
  GET  /bot/equity-curve   daily closed-PnL deltas for the chart
  GET  /bot/skipped        recent skipped signals
  POST /bot/close/{id}     manual close at last 1m candle close
  POST /bot/reset-paper    dev: reset paper equity
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func, select

from app.config import settings as app_settings
from app.dependencies import CurrentUser, DBSession
from app.models.bot import BotSkippedSignal, BotTrade
from app.modules.bot import equity, executor
from app.modules.bot.schemas import CloseReason
from app.services import redis_service

router = APIRouter(prefix="/bot", tags=["bot"])


@router.get("/status")
async def status(_user: CurrentUser):
    paper_equity = await equity.get_paper_equity()
    anchor = await equity.get_daily_anchor()
    kill = await equity.is_kill_switch_tripped()
    concurrent = await equity.get_concurrent_count()
    drawdown_pct = float((paper_equity - anchor) / anchor) if anchor > 0 else 0.0
    return {
        "enabled": bool(getattr(app_settings, "bot_enabled", False)),
        "paper_equity": float(paper_equity),
        "daily_anchor": float(anchor),
        "drawdown_pct": drawdown_pct,
        "kill_switch_tripped": kill,
        "concurrent_open": concurrent,
        "max_concurrent": int(app_settings.bot_max_concurrent),
        "config": {
            "position_size_pct": float(app_settings.bot_position_size_pct),
            "r_multiple": float(app_settings.bot_take_profit_r_multiple),
            "stop_buffer_pct": float(app_settings.bot_stop_buffer_pct),
            "per_symbol_cooldown_minutes": int(app_settings.bot_per_symbol_cooldown_minutes),
            "daily_drawdown_cap_pct": float(app_settings.bot_daily_drawdown_cap_pct),
            "oracle_veto_long_below": float(app_settings.bot_oracle_veto_long_below),
            "oracle_veto_short_above": float(app_settings.bot_oracle_veto_short_above),
            "news_veto_window_minutes": int(app_settings.bot_news_veto_window_minutes),
            "entry_delay_seconds": int(app_settings.bot_entry_delay_seconds),
        },
    }


@router.get("/positions")
async def positions(_user: CurrentUser, db: DBSession):
    rows = (
        await db.execute(
            select(BotTrade)
            .where(BotTrade.status == "open")
            .order_by(BotTrade.entry_at)
        )
    ).scalars().all()
    return {"items": [_serialize_trade(t) for t in rows]}


@router.get("/trades")
async def trades(
    _user: CurrentUser,
    db: DBSession,
    symbol: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(BotTrade)
        .where(BotTrade.status == "closed")
        .order_by(desc(BotTrade.closed_at))
    )
    if symbol:
        stmt = stmt.where(BotTrade.symbol == symbol)
    if reason:
        stmt = stmt.where(BotTrade.close_reason == reason)
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize_trade(t) for t in rows], "limit": limit, "offset": offset}


@router.get("/equity-curve")
async def equity_curve(_user: CurrentUser, db: DBSession, days: int = Query(default=30, ge=1, le=365)):
    """Daily realized P&L for the chart — sum of realized_pnl_usd by closed_at day."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(
            func.date_trunc("day", BotTrade.closed_at).label("day"),
            func.coalesce(func.sum(BotTrade.realized_pnl_usd), 0).label("pnl"),
            func.count().label("n_trades"),
        )
        .where(BotTrade.status == "closed", BotTrade.closed_at >= since)
        .group_by("day")
        .order_by("day")
    )
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {
                "day": r.day.isoformat() if r.day else None,
                "realized_pnl_usd": float(r.pnl or 0),
                "n_trades": int(r.n_trades),
            }
            for r in rows
        ],
        "current_equity": float(await equity.get_paper_equity()),
    }


@router.get("/skipped")
async def skipped(
    _user: CurrentUser,
    db: DBSession,
    reason: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = (
        select(BotSkippedSignal)
        .order_by(desc(BotSkippedSignal.alert_detected_at))
        .limit(limit)
    )
    if reason:
        stmt = stmt.where(BotSkippedSignal.skip_reason == reason)
    if symbol:
        stmt = stmt.where(BotSkippedSignal.symbol == symbol)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "symbol": r.symbol,
                "exchange": r.exchange,
                "alert_type": r.alert_type,
                "direction": r.direction,
                "alert_detected_at": r.alert_detected_at.isoformat(),
                "skip_reason": r.skip_reason,
                "oracle_score": float(r.oracle_score) if r.oracle_score is not None else None,
                "context": r.context,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/close/{trade_id}")
async def close_manual(_user: CurrentUser, db: DBSession, trade_id: str):
    """Manually close an open position at the latest 1m candle close."""
    trade = (
        await db.execute(select(BotTrade).where(BotTrade.id == trade_id))
    ).scalar_one_or_none()
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")
    if trade.status != "open":
        return {"ok": True, "reason": "already_closed", "id": str(trade.id)}
    candle = await redis_service.get_latest_candle(trade.symbol)
    if candle is None:
        raise HTTPException(status_code=409, detail="no candle available for fill")
    fill = Decimal(str(candle.get("c") or candle.get("close") or 0))
    if fill <= 0:
        raise HTTPException(status_code=409, detail="invalid fill price")
    closed = await executor.close_paper_trade(
        db, trade, exit_price=fill, reason=CloseReason.MANUAL
    )
    return {"ok": True, "id": str(closed.id), "close_price": float(fill)}


@router.post("/reset-paper")
async def reset_paper(_user: CurrentUser):
    """Reset paper equity to the initial value. Dev convenience — does not touch
    open positions, so use only when there are none (verify via /bot/positions)."""
    new_equity = await equity.reset_paper_equity()
    return {"ok": True, "paper_equity": float(new_equity)}


def _serialize_trade(t: BotTrade) -> dict:
    return {
        "id": str(t.id),
        "symbol": t.symbol,
        "exchange": t.exchange,
        "direction": t.direction,
        "alert_type": t.alert_type,
        "alert_detected_at": t.alert_detected_at.isoformat(),
        "entry_price": float(t.entry_price),
        "entry_at": t.entry_at.isoformat(),
        "signal_high": float(t.signal_high),
        "signal_low": float(t.signal_low),
        "notional_usd": float(t.notional_usd),
        "qty": float(t.qty),
        "stop_price": float(t.stop_price),
        "take_profit_price": float(t.take_profit_price),
        "paper_equity_at_entry": float(t.paper_equity_at_entry),
        "close_price": float(t.close_price) if t.close_price is not None else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "close_reason": t.close_reason,
        "realized_pnl_usd": float(t.realized_pnl_usd) if t.realized_pnl_usd is not None else None,
        "realized_r": float(t.realized_r) if t.realized_r is not None else None,
        "oracle_score_at_entry": float(t.oracle_score_at_entry) if t.oracle_score_at_entry is not None else None,
        "vol_ratio": float(t.vol_ratio) if t.vol_ratio is not None else None,
        "funding_pct": float(t.funding_pct) if t.funding_pct is not None else None,
        "pct_change": float(t.pct_change) if t.pct_change is not None else None,
        "status": t.status,
    }
