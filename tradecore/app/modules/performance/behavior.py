"""Behavioral analytics — detect revenge trading and tilt patterns."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import log
from app.models.tradelog import Trade

REVENGE_WINDOW_MINUTES = 30
REVENGE_SIZE_MULTIPLIER = 1.3
TILT_WINDOW_MINUTES = 120
TILT_CONSECUTIVE_LOSSES = 3


async def detect_revenge_trading(db: AsyncSession, user_id: UUID) -> dict | None:
    """Detect if the user is revenge trading.

    Pattern: loss followed by a larger position opened within 30 minutes.
    Returns warning dict if detected, None otherwise.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=REVENGE_WINDOW_MINUTES)
    result = await db.execute(
        select(Trade)
        .where(
            Trade.user_id == user_id,
            Trade.entry_at >= cutoff,
        )
        .order_by(Trade.entry_at.desc())
        .limit(10)
    )
    recent = result.scalars().all()
    if len(recent) < 2:
        return None

    for i in range(len(recent) - 1):
        current = recent[i]
        previous = recent[i + 1]

        if previous.status != "closed" or (previous.pnl_usd or 0) >= 0:
            continue

        prev_size = float(previous.size_usd or 0)
        curr_size = float(current.size_usd or 0)
        if prev_size <= 0:
            continue

        time_gap = (current.entry_at - (previous.exit_at or previous.entry_at)).total_seconds()
        if time_gap > REVENGE_WINDOW_MINUTES * 60:
            continue

        if curr_size >= prev_size * REVENGE_SIZE_MULTIPLIER:
            log.warning(
                "revenge_trade_detected",
                user_id=str(user_id),
                prev_loss=float(previous.pnl_usd or 0),
                size_increase=round(curr_size / prev_size, 2),
            )
            return {
                "warning": "revenge_trading",
                "severity": "high",
                "previous_loss_usd": float(previous.pnl_usd or 0),
                "size_increase_ratio": round(curr_size / prev_size, 2),
                "time_between_seconds": int(time_gap),
                "recommendation": "Step away. Size increase after a loss indicates emotional trading.",
            }
    return None


async def detect_tilt(db: AsyncSession, user_id: UUID) -> dict | None:
    """Detect tilt: consecutive losses in a short window.

    Returns warning if user has 3+ consecutive losses in 2 hours.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=TILT_WINDOW_MINUTES)
    result = await db.execute(
        select(Trade)
        .where(
            Trade.user_id == user_id,
            Trade.status == "closed",
            Trade.exit_at >= cutoff,
        )
        .order_by(Trade.exit_at.desc())
        .limit(10)
    )
    recent = result.scalars().all()

    consecutive_losses = 0
    total_loss = Decimal("0")
    for trade in recent:
        if (trade.pnl_usd or 0) < 0:
            consecutive_losses += 1
            total_loss += trade.pnl_usd or Decimal("0")
        else:
            break

    if consecutive_losses >= TILT_CONSECUTIVE_LOSSES:
        log.warning(
            "tilt_detected",
            user_id=str(user_id),
            consecutive_losses=consecutive_losses,
            total_loss=float(total_loss),
        )
        return {
            "warning": "tilt",
            "severity": "high" if consecutive_losses >= 5 else "medium",
            "consecutive_losses": consecutive_losses,
            "total_loss_usd": float(total_loss),
            "recommendation": "Take a break. Consecutive losses erode discipline.",
        }
    return None


async def check_behavioral_gates(db: AsyncSession, user_id: UUID) -> dict:
    """Run all behavioral checks before allowing a new trade.

    Returns {"can_trade": True/False, "warnings": [...]}
    """
    warnings = []

    revenge = await detect_revenge_trading(db, user_id)
    if revenge:
        warnings.append(revenge)

    tilt = await detect_tilt(db, user_id)
    if tilt:
        warnings.append(tilt)

    has_high = any(w.get("severity") == "high" for w in warnings)
    return {
        "can_trade": not has_high,
        "warnings": warnings,
        "position_size_multiplier": 0.5 if warnings and not has_high else 1.0,
    }
