"""PerformanceCore — compute per-user and per-module performance.

Idempotent: running compute_user_performance twice for the same period
updates the existing snapshot instead of duplicating.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import fmean
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import log
from app.models.oracle import OracleOutcome, OracleSignal
from app.models.performance import PerformanceSnapshot, SignalPerformance
from app.models.tradelog import Trade


PERIODS = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": None,
}


def _max_drawdown(equity: list[float]) -> tuple[float, float]:
    """Return (max_dd_usd, max_dd_pct) over the equity curve (cumulative net PnL)."""
    if not equity:
        return 0.0, 0.0
    peak = equity[0]
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd_usd:
            max_dd_usd = dd
            if peak != 0:
                max_dd_pct = dd / abs(peak) * 100 if peak > 0 else 0.0
    return round(max_dd_usd, 2), round(max_dd_pct, 2)


async def compute_user_performance(
    db: AsyncSession,
    user_id: UUID,
    period: str = "all",
    is_paper: bool | None = None,
) -> PerformanceSnapshot:
    now = datetime.now(timezone.utc)
    delta = PERIODS.get(period)
    period_start = (now - delta) if delta else datetime(2000, 1, 1, tzinfo=timezone.utc)
    period_end = now

    stmt = (
        select(Trade)
        .where(
            Trade.user_id == user_id,
            Trade.status == "closed",
            Trade.exit_at.isnot(None),
            Trade.exit_at >= period_start,
            Trade.exit_at <= period_end,
        )
        .order_by(Trade.exit_at)
    )
    if is_paper is not None:
        stmt = stmt.where(Trade.is_paper.is_(is_paper))
    trades = (await db.execute(stmt)).scalars().all()

    total = len(trades)
    wins = [t for t in trades if (t.net_pnl_usd or Decimal(0)) > 0]
    losses = [t for t in trades if (t.net_pnl_usd or Decimal(0)) < 0]
    breakevens = [t for t in trades if (t.net_pnl_usd or Decimal(0)) == 0]

    win_pcts = [float(t.pnl_pct) for t in wins if t.pnl_pct is not None]
    loss_pcts = [float(t.pnl_pct) for t in losses if t.pnl_pct is not None]
    r_multiples = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
    fees = sum(float(t.fees_usd or 0) for t in trades)
    gross_pnl = sum(float(t.pnl_usd or 0) for t in trades)
    net_pnl = sum(float(t.net_pnl_usd or 0) for t in trades)

    win_rate = (len(wins) / total * 100) if total else None
    avg_win_pct = fmean(win_pcts) if win_pcts else None
    avg_loss_pct = fmean(loss_pcts) if loss_pcts else None
    avg_rr = fmean(r_multiples) if r_multiples else None

    expectancy = None
    if win_rate is not None and avg_win_pct is not None and avg_loss_pct is not None:
        expectancy = (win_rate / 100 * avg_win_pct) + ((1 - win_rate / 100) * avg_loss_pct)
    elif win_rate is not None and avg_win_pct is not None:
        expectancy = win_rate / 100 * avg_win_pct

    gross_wins = sum(float(t.net_pnl_usd or 0) for t in wins)
    gross_losses = abs(sum(float(t.net_pnl_usd or 0) for t in losses))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else None

    # Equity curve
    equity = []
    running = 0.0
    for t in trades:
        running += float(t.net_pnl_usd or 0)
        equity.append(running)
    max_dd_usd, max_dd_pct = _max_drawdown(equity)

    max_consecutive_losses = 0
    cur_losing = 0
    for t in trades:
        if (t.net_pnl_usd or Decimal(0)) < 0:
            cur_losing += 1
            max_consecutive_losses = max(max_consecutive_losses, cur_losing)
        else:
            cur_losing = 0

    best_trade = max((float(t.net_pnl_usd or 0) for t in trades), default=0.0)
    worst_trade = min((float(t.net_pnl_usd or 0) for t in trades), default=0.0)

    # Best setup by net PnL
    by_setup: dict[str, float] = {}
    for t in trades:
        if t.setup_name:
            by_setup[t.setup_name] = by_setup.get(t.setup_name, 0.0) + float(t.net_pnl_usd or 0)
    best_setup = max(by_setup, key=by_setup.get) if by_setup else None

    # Upsert — find existing snapshot for this (user, period, is_paper)
    existing = (
        await db.execute(
            select(PerformanceSnapshot)
            .where(
                PerformanceSnapshot.user_id == user_id,
                PerformanceSnapshot.period == period,
                PerformanceSnapshot.is_paper.is_(bool(is_paper)) if is_paper is not None else PerformanceSnapshot.is_paper.is_(False),
            )
            .order_by(desc(PerformanceSnapshot.computed_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    snap = existing or PerformanceSnapshot(
        user_id=user_id, period=period, period_start=period_start, period_end=period_end,
        is_paper=bool(is_paper) if is_paper is not None else False,
    )
    snap.period_start = period_start
    snap.period_end = period_end
    snap.total_trades = total
    snap.winning_trades = len(wins)
    snap.losing_trades = len(losses)
    snap.breakeven_trades = len(breakevens)
    snap.win_rate = Decimal(str(round(win_rate, 2))) if win_rate is not None else None
    snap.avg_win_pct = Decimal(str(round(avg_win_pct, 4))) if avg_win_pct is not None else None
    snap.avg_loss_pct = Decimal(str(round(avg_loss_pct, 4))) if avg_loss_pct is not None else None
    snap.avg_rr_achieved = Decimal(str(round(avg_rr, 2))) if avg_rr is not None else None
    snap.expectancy = Decimal(str(round(expectancy, 4))) if expectancy is not None else None
    snap.profit_factor = Decimal(str(round(profit_factor, 2))) if profit_factor is not None else None
    snap.total_pnl_usd = Decimal(str(round(gross_pnl, 2)))
    snap.total_fees_usd = Decimal(str(round(fees, 2)))
    snap.net_pnl_usd = Decimal(str(round(net_pnl, 2)))
    snap.max_drawdown_usd = Decimal(str(max_dd_usd))
    snap.max_drawdown_pct = Decimal(str(max_dd_pct))
    snap.max_consecutive_losses = max_consecutive_losses
    snap.best_trade_pnl_usd = Decimal(str(round(best_trade, 2)))
    snap.worst_trade_pnl_usd = Decimal(str(round(worst_trade, 2)))
    snap.best_setup = best_setup
    snap.computed_at = datetime.now(timezone.utc)

    if existing is None:
        db.add(snap)
    await db.commit()
    await db.refresh(snap)
    log.info("performance_snapshot_computed", user_id=str(user_id), period=period, total=total)
    return snap


async def compute_signal_accuracy(db: AsyncSession, module: str = "oracle") -> dict:
    """Aggregate oracle_outcomes into a SignalPerformance row keyed by module."""
    # Pull signals+outcomes
    rows = (
        await db.execute(
            select(OracleSignal, OracleOutcome)
            .join(OracleOutcome, OracleOutcome.signal_id == OracleSignal.id)
        )
    ).all()
    total = len(rows)
    correct_1h = sum(1 for _, o in rows if o.was_correct_1h is True)
    correct_4h = sum(1 for _, o in rows if o.was_correct_4h is True)
    measured_1h = sum(1 for _, o in rows if o.was_correct_1h is not None)
    measured_4h = sum(1 for _, o in rows if o.was_correct_4h is not None)
    moves_1h = [float(o.pnl_1h_pct) for _, o in rows if o.pnl_1h_pct is not None]
    moves_4h = [float(o.pnl_4h_pct) for _, o in rows if o.pnl_4h_pct is not None]

    snap = SignalPerformance(
        module=module,
        symbol=None,
        total_signals=total,
        correct_1h=correct_1h,
        correct_4h=correct_4h,
        accuracy_1h_pct=Decimal(str(round(correct_1h / measured_1h * 100, 2))) if measured_1h else None,
        accuracy_4h_pct=Decimal(str(round(correct_4h / measured_4h * 100, 2))) if measured_4h else None,
        avg_move_1h_pct=Decimal(str(round(fmean(moves_1h), 2))) if moves_1h else None,
        avg_move_4h_pct=Decimal(str(round(fmean(moves_4h), 2))) if moves_4h else None,
    )
    db.add(snap)
    await db.commit()
    return {
        "module": module,
        "total_signals": total,
        "accuracy_1h_pct": float(snap.accuracy_1h_pct) if snap.accuracy_1h_pct else None,
        "accuracy_4h_pct": float(snap.accuracy_4h_pct) if snap.accuracy_4h_pct else None,
        "avg_move_1h_pct": float(snap.avg_move_1h_pct) if snap.avg_move_1h_pct else None,
        "avg_move_4h_pct": float(snap.avg_move_4h_pct) if snap.avg_move_4h_pct else None,
    }


__all__ = ["compute_user_performance", "compute_signal_accuracy", "PERIODS"]
