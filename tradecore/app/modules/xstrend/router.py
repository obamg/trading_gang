"""XsTrend API — one read-only endpoint that feeds the portfolio page."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models.xstrend import XsTrendWeek
from app.modules.xstrend import rules as R
from app.modules.xstrend.portfolio import _dt, week_of

router = APIRouter(prefix="/xstrend", tags=["xstrend"])


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _week(w: XsTrendWeek) -> dict:
    ret, fund = w.detail.get("returns", {}), w.detail.get("funding", {})
    return {
        "week_start": w.week_start.isoformat(), "status": w.status,
        "positions": [
            {"symbol": s, "weight": wt, "return": ret.get(s), "funding": fund.get(s)}
            for s, wt in sorted(w.weights.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "turnover": _f(w.turnover), "gross": _f(w.gross), "cost": _f(w.cost),
        "funding": _f(w.funding), "net": _f(w.net), "mkt": _f(w.mkt),
    }


@router.get("/portfolio")
async def portfolio(_user: CurrentUser, db: DBSession):
    now = datetime.now(timezone.utc)
    # ponytail: loads every week of the current rules. That is 104 rows at the verdict.
    rows = (
        await db.execute(
            select(XsTrendWeek).where(XsTrendWeek.rules == R.VERSION).order_by(XsTrendWeek.week_start)
        )
    ).scalars().all()
    settled = [r for r in rows if r.status == "settled"]
    current = next((r for r in reversed(rows) if r.status == "open"), None)
    return {
        "enabled": settings.xstrend_enabled,
        "rules": {
            "version": R.VERSION, "universe": R.N_UNI, "per_leg": R.N_LEG,
            "horizons": list(R.HORIZONS), "cost_pct": R.COST * 100,
            "first_week": _dt(R.FIRST_WEEK).isoformat(),
        },
        "next_rebalance": _dt(max(week_of(now) + R.WEEK, R.FIRST_WEEK)).isoformat(),
        "current": _week(current) if current else None,
        "weeks": [_week(r) for r in settled],
        "stats": {
            **R.scoreboard([float(r.net) for r in settled]),
            "mkt_total": sum(float(r.mkt) for r in settled[:R.N_WEEKS]),
        },
        "generated_at": now.isoformat(),
    }
