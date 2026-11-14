"""DayTrade API — one read-only endpoint that feeds the whole desk page."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import desc, select

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models.daytrade import DayTradePlan
from app.modules.daytrade.playbook import gate, scoreboard
from app.modules.daytrade.scanner import BOARD_KEY, CLOSED, PLAYBOOK, day_stats, exchange, symbols
from app.services import redis_service

router = APIRouter(prefix="/daytrade", tags=["daytrade"])


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _serialize(p: DayTradePlan) -> dict:
    return {
        "id": str(p.id), "exchange": p.exchange, "symbol": p.symbol,
        "session_at": p.session_at.isoformat(), "side": p.side, "status": p.status,
        "entry": _f(p.entry), "stop": _f(p.stop), "target": _f(p.target),
        "range_high": _f(p.range_high), "range_low": _f(p.range_low),
        "stop_pct": _f(p.stop_pct), "vol_x": _f(p.vol_x), "funding_rate": _f(p.funding_rate),
        "reasons": (p.checks or {}).get("reasons", []),
        "sizing": (p.checks or {}).get("sizing", {}),
        "ai_verdict": p.ai_verdict, "ai_reason": p.ai_reason,
        "triggered_at": p.triggered_at.isoformat(), "exit_by": p.exit_by.isoformat(),
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        "exit_price": _f(p.exit_price), "pnl_r": _f(p.pnl_r),
    }


@router.get("/desk")
async def desk(_user: CurrentUser, db: DBSession):
    now = datetime.now(timezone.utc)
    raw = await redis_service.get_redis().get(BOARD_KEY)
    board = json.loads(raw) if raw else None

    recent = (
        await db.execute(select(DayTradePlan).order_by(desc(DayTradePlan.triggered_at)).limit(60))
    ).scalars().all()
    # ponytail: loads every closed result to build the curve. Fine for years of
    # 4-a-day plans; move to a SQL window function if the table gets large.
    results = (
        await db.execute(
            select(DayTradePlan.pnl_r, DayTradePlan.ai_verdict)
            .where(
                DayTradePlan.status.in_(CLOSED),
                # A different data source or a changed rule is a different test.
                DayTradePlan.exchange == exchange(),
                DayTradePlan.playbook == PLAYBOOK.version,
            )
            .order_by(DayTradePlan.closed_at)
        )
    ).all()

    # Descriptive only. The AI split is a second hypothesis on the same sample;
    # it must never be used to rescue a failed gate (that would be retuning).
    by_ai: dict[str, dict] = {}
    for verdict in ("take", "skip"):
        rs = [float(r.pnl_r) for r in results if r.ai_verdict == verdict]
        by_ai[verdict] = {"n": len(rs), "avg_r": round(sum(rs) / len(rs), 3) if rs else None}

    day = day_stats([p for p in recent if p.triggered_at >= now - timedelta(days=2)], now)
    pb = asdict(PLAYBOOK)
    pb["friction_pct"] = PLAYBOOK.friction * 100
    pb["version"] = PLAYBOOK.version
    return {
        "enabled": settings.daytrade_enabled,
        "ai_enabled": bool(settings.anthropic_api_key),
        "exchange": exchange(),
        "symbols": symbols(),
        "account_usd": settings.daytrade_account_usd,
        "playbook": pb,
        "board": board,
        "gate": {
            "trades": day["trades"],
            "loss_r": round(day["loss_r"], 2),
            "open": len(day["open_symbols"]),
            "streak": day["streak"],
            # Would a brand-new symbol be refused right now?
            "blocked": gate("", day, PLAYBOOK),
        },
        "plans": [_serialize(p) for p in recent],
        "stats": {**scoreboard([float(r.pnl_r) for r in results], PLAYBOOK), "by_ai": by_ai},
        "generated_at": now.isoformat(),
    }
