"""Macro context computation — consumed by Oracle (Team 6)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from app.database import AsyncSessionLocal
from app.models.macro import EconomicEvent, MacroSnapshot
from app.services import redis_service


def _vix_level(value: float | None) -> str:
    if value is None:
        return "neutral"
    if value < 15:
        return "low"
    if value > 25:
        return "high"
    return "medium"


def _dxy_trend(change_pct: float | None) -> str:
    if change_pct is None:
        return "neutral"
    if change_pct > 0.1:
        return "rising"
    if change_pct < -0.1:
        return "falling"
    return "neutral"


def _etf_label(flow: float | None) -> str:
    if flow is None:
        return "neutral"
    if flow > 0:
        return "positive"
    if flow < 0:
        return "negative"
    return "neutral"


def _risk_environment(score: int) -> str:
    if score >= 25:
        return "favorable"
    if score >= 0:
        return "neutral"
    if score >= -25:
        return "caution"
    return "risk_off"


async def compute_macro_context(symbol: str | None = None) -> dict:
    """Returns the dict shape documented in the Team 5 spec."""
    r = redis_service.get_redis()
    # Pull cached ticker data first — these are the freshest values.
    cached = {}
    for key in ("dxy", "vix", "us10y", "us2y", "sp500", "nasdaq", "gold"):
        raw = await r.get(f"macro:{key}")
        if raw:
            try:
                cached[key] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    score_raw = await r.get("macro:score")
    score_payload = json.loads(score_raw) if score_raw else {}
    score = int(score_payload.get("macro_score", 0))

    # Fall back to latest DB snapshot for anything missing
    etf_flows: float | None = None
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(MacroSnapshot).order_by(desc(MacroSnapshot.snapshot_at)).limit(1))
        ).scalar_one_or_none()
        if row is not None:
            if not cached.get("dxy") and row.dxy is not None:
                cached["dxy"] = {"value": float(row.dxy), "change_pct": 0.0}
            if not cached.get("vix") and row.vix is not None:
                cached["vix"] = {"value": float(row.vix)}
            etf_flows = float(row.btc_etf_flows_usd) if row.btc_etf_flows_usd is not None else None
            if not score_payload and row.macro_score is not None:
                score = int(row.macro_score)

        # Upcoming high-impact events in the next 24h
        soon = datetime.now(timezone.utc) + timedelta(hours=24)
        evs = (
            (
                await db.execute(
                    select(EconomicEvent)
                    .where(
                        EconomicEvent.scheduled_at >= datetime.now(timezone.utc),
                        EconomicEvent.scheduled_at <= soon,
                        EconomicEvent.impact == "high",
                    )
                    .order_by(EconomicEvent.scheduled_at)
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        events = [
            f"{e.name} {e.scheduled_at.strftime('%H:%M')} UTC" for e in evs
        ]

    _ = symbol  # placeholder — per-symbol customization can come later
    return {
        "macro_score": score,
        "dxy_trend": _dxy_trend(cached.get("dxy", {}).get("change_pct")),
        "vix_level": _vix_level(cached.get("vix", {}).get("value")),
        "etf_flows": _etf_label(etf_flows),
        "risk_environment": _risk_environment(score),
        "key_events_24h": events,
    }


__all__ = ["compute_macro_context"]
