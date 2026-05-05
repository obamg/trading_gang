"""Macro trade gate — blocks entries during unfavorable conditions.

Three gates checked in order:
  1. Risk-off environment (macro_score < -25)
  2. VIX extreme (> 30)
  3. High-impact economic event within 1 hour
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.macro import EconomicEvent
from app.modules.macropulse.score import compute_macro_context
from app.services import redis_service


async def check_macro_gates() -> dict:
    """Return trade gate status. Call before opening any position."""
    ctx = await compute_macro_context()
    score = int(ctx.get("macro_score", 0))
    risk_env = ctx.get("risk_environment", "neutral")

    # Gate 1: Risk-off absolute block
    if score < -25:
        return {
            "can_trade": False,
            "reason": f"Macro risk_off: score {score} (environment: {risk_env})",
            "recommendation": "close_longs_only",
            "macro_score": score,
            "risk_environment": risk_env,
        }

    # Gate 2: VIX spike
    r = redis_service.get_redis()
    try:
        raw_vix = await r.get("macro:vix")
        if raw_vix:
            vix_val = float(json.loads(raw_vix).get("value", 0))
            if vix_val > 30:
                return {
                    "can_trade": False,
                    "reason": f"VIX extreme: {vix_val:.1f} > 30",
                    "recommendation": "close_longs_only",
                    "macro_score": score,
                    "risk_environment": risk_env,
                }
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Gate 3: High-impact economic event within 1 hour
    now = datetime.now(timezone.utc)
    soon = now + timedelta(hours=1)
    async with AsyncSessionLocal() as db:
        event = (
            await db.execute(
                select(EconomicEvent)
                .where(
                    EconomicEvent.scheduled_at >= now,
                    EconomicEvent.scheduled_at <= soon,
                    EconomicEvent.impact == "high",
                )
                .order_by(EconomicEvent.scheduled_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if event is not None:
            return {
                "can_trade": False,
                "reason": f"High-impact event in <1h: {event.name} at {event.scheduled_at.strftime('%H:%M')} UTC",
                "recommendation": "close_only",
                "macro_score": score,
                "risk_environment": risk_env,
            }

    # All gates passed
    size_multiplier = 0.5 if score < 0 else 1.0
    return {
        "can_trade": True,
        "macro_score": score,
        "risk_environment": risk_env,
        "position_size_multiplier": size_multiplier,
    }


__all__ = ["check_macro_gates"]
