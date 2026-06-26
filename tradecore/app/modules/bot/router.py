"""WaveBot — API surface.

  GET  /bot/status         enabled flag, equity, daily anchor, kill switch, concurrent count
  GET  /bot/positions      open positions
  GET  /bot/trades         closed trades, filterable
  GET  /bot/equity-curve   daily closed-PnL deltas for the chart
  GET  /bot/skipped        recent skipped signals
  GET  /bot/analytics      direction/oracle/symbol/hour breakdowns over closed trades
  POST /bot/close/{id}     manual close at last 1m candle close
  POST /bot/reset-paper    dev: reset paper equity
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, case, desc, func, select

from app.config import settings as app_settings
from app.dependencies import CurrentUser, DBSession
from app.models.bot import BotSkippedSignal, BotTrade
from app.modules.bot import candle_source, equity, executor
from app.modules.bot.schemas import CloseReason

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
            "risk_per_trade_pct": float(app_settings.bot_risk_per_trade_pct),
            "r_multiple": float(app_settings.bot_take_profit_r_multiple),
            "stop_buffer_pct": float(app_settings.bot_stop_buffer_pct),
            "per_symbol_cooldown_minutes": int(app_settings.bot_per_symbol_cooldown_minutes),
            "max_hold_hours": int(app_settings.bot_max_hold_hours),
            "fee_pct_per_side": float(app_settings.bot_fee_pct_per_side),
            "slippage_pct": float(app_settings.bot_slippage_pct),
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


@router.get("/analytics")
async def analytics(_user: CurrentUser, db: DBSession, days: int = Query(default=30, ge=1, le=365)):
    """Breakdown stats over closed trades in the window: direction, oracle bucket,
    per-symbol top winners/losers, and hour-of-day (UTC). Read-only aggregation."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = and_(BotTrade.status == "closed", BotTrade.closed_at >= since)

    wins_expr = func.sum(case((BotTrade.close_reason == "tp", 1), else_=0))
    losses_expr = func.sum(case((BotTrade.close_reason == "stop", 1), else_=0))
    pnl_expr = func.coalesce(func.sum(BotTrade.realized_pnl_usd), 0)
    r_expr = func.coalesce(func.sum(BotTrade.realized_r), 0)
    n_expr = func.count()

    def _row(label, n, wins, losses, pnl, r_sum):
        n_i = int(n or 0)
        wins_i = int(wins or 0)
        losses_i = int(losses or 0)
        decided = wins_i + losses_i
        return {
            "label": label,
            "n_trades": n_i,
            "wins": wins_i,
            "losses": losses_i,
            "win_rate": (wins_i / decided) if decided else None,
            "realized_pnl_usd": float(pnl or 0),
            "realized_r": float(r_sum or 0),
            "expectancy_r": (float(r_sum or 0) / n_i) if n_i else None,
        }

    # 1. By direction
    dir_rows = (
        await db.execute(
            select(BotTrade.direction, n_expr, wins_expr, losses_expr, pnl_expr, r_expr)
            .where(base)
            .group_by(BotTrade.direction)
        )
    ).all()
    by_direction = [_row(r[0], *r[1:]) for r in dir_rows]

    # 2. By Oracle bucket — none / strong_bear / bear / neutral / bull / strong_bull
    bucket = case(
        (BotTrade.oracle_score_at_entry.is_(None), "none"),
        (BotTrade.oracle_score_at_entry <= -30, "strong_bear (≤-30)"),
        (BotTrade.oracle_score_at_entry < 0, "bear (-30..0)"),
        (BotTrade.oracle_score_at_entry == 0, "neutral (0)"),
        (BotTrade.oracle_score_at_entry < 30, "bull (0..30)"),
        else_="strong_bull (≥30)",
    )
    oracle_rows = (
        await db.execute(
            select(bucket.label("bucket"), n_expr, wins_expr, losses_expr, pnl_expr, r_expr)
            .where(base)
            .group_by("bucket")
        )
    ).all()
    by_oracle = [_row(r[0], *r[1:]) for r in oracle_rows]

    # 3. By symbol (top by traffic, with PnL/R)
    sym_rows = (
        await db.execute(
            select(BotTrade.symbol, n_expr, wins_expr, losses_expr, pnl_expr, r_expr)
            .where(base)
            .group_by(BotTrade.symbol)
            .order_by(desc(n_expr))
            .limit(50)
        )
    ).all()
    by_symbol = [_row(r[0], *r[1:]) for r in sym_rows]

    # 4. By hour-of-day (UTC) — when does the bot trade well?
    hour = func.date_part("hour", BotTrade.alert_detected_at).label("hour")
    hour_rows = (
        await db.execute(
            select(hour, n_expr, wins_expr, losses_expr, pnl_expr, r_expr)
            .where(base)
            .group_by("hour")
            .order_by("hour")
        )
    ).all()
    by_hour = [_row(int(r[0]) if r[0] is not None else None, *r[1:]) for r in hour_rows]

    return {
        "days": days,
        "by_direction": by_direction,
        "by_oracle_bucket": by_oracle,
        "by_symbol": by_symbol,
        "by_hour_utc": by_hour,
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
    candle = await candle_source.get_latest_candle(trade.symbol, trade.exchange, trade.market_type)
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
        "market_type": t.market_type,
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
