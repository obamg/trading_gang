"""Portfolio-level risk checks — enforces aggregate exposure limits."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import UserSettings
from app.models.tradelog import Trade


async def check_portfolio_risk(
    db: AsyncSession,
    user_id: UUID,
    new_risk_usd: float,
    account_balance: float,
) -> list[str]:
    """Check portfolio-level risk limits. Returns a list of warning/block messages."""
    warnings: list[str] = []

    settings_row = (
        await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    ).scalar_one_or_none()

    max_account_risk_pct = float(settings_row.oracle_max_account_risk_pct) if settings_row else 2.0
    daily_loss_limit_pct = float(settings_row.oracle_daily_loss_limit_pct) if settings_row else 5.0

    if account_balance <= 0:
        return warnings

    # 1. Total open exposure check
    open_trades = (
        await db.execute(
            select(
                func.coalesce(func.sum(Trade.size_usd), Decimal("0")),
                func.count(Trade.id),
            )
            .where(Trade.user_id == user_id, Trade.status == "open")
        )
    ).one()
    total_open_exposure = float(open_trades[0])
    open_count = int(open_trades[1])

    # Check if adding the new position exceeds max account risk
    # Estimate risk as the sum of (size_usd * stop_distance) for open trades
    open_risk_result = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        func.abs(Trade.entry_price - Trade.stop_loss_price)
                        / Trade.entry_price
                        * Trade.size_usd
                    ),
                    Decimal("0"),
                )
            )
            .where(
                Trade.user_id == user_id,
                Trade.status == "open",
                Trade.stop_loss_price.isnot(None),
            )
        )
    ).scalar_one()
    total_open_risk_usd = float(open_risk_result)

    total_risk_with_new = total_open_risk_usd + new_risk_usd
    total_risk_pct = total_risk_with_new / account_balance * 100

    if total_risk_pct > max_account_risk_pct * 3:
        warnings.append(
            f"BLOCKED: Total portfolio risk ({total_risk_pct:.1f}%) exceeds "
            f"3x max per-trade risk ({max_account_risk_pct * 3:.1f}%). "
            f"Close existing positions first."
        )
    elif total_risk_pct > max_account_risk_pct * 2:
        warnings.append(
            f"Total portfolio risk is {total_risk_pct:.1f}% of account — "
            f"approaching dangerous levels"
        )

    # 2. Daily loss limit check
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_loss_result = (
        await db.execute(
            select(func.coalesce(func.sum(Trade.net_pnl_usd), Decimal("0")))
            .where(
                Trade.user_id == user_id,
                Trade.status == "closed",
                Trade.exit_at >= today_start,
                Trade.net_pnl_usd < 0,
            )
        )
    ).scalar_one()
    daily_loss_usd = abs(float(daily_loss_result))
    daily_loss_pct = daily_loss_usd / account_balance * 100

    if daily_loss_pct >= daily_loss_limit_pct:
        warnings.append(
            f"BLOCKED: Daily loss limit reached ({daily_loss_pct:.1f}% vs "
            f"{daily_loss_limit_pct:.1f}% limit). Stop trading for today."
        )
    elif daily_loss_pct >= daily_loss_limit_pct * 0.8:
        warnings.append(
            f"Approaching daily loss limit: {daily_loss_pct:.1f}% of "
            f"{daily_loss_limit_pct:.1f}% used"
        )

    # 3. Max concurrent positions warning
    if open_count >= 10:
        warnings.append(
            f"You have {open_count} open positions — consider reducing exposure"
        )

    return warnings
