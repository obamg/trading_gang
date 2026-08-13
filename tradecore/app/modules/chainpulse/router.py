"""ChainPulse API."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import asc, desc, select

from app.dependencies import CurrentUser, DBSession
from app.models.chainpulse import ChainPulseSnapshot
from app.services import redis_service

router = APIRouter(prefix="/chainpulse", tags=["chainpulse"])

ASSETS = ["bitcoin", "ethereum"]


def _row_to_dict(row: ChainPulseSnapshot) -> dict:
    return {
        "asset": row.asset,
        "mvrv": float(row.mvrv) if row.mvrv is not None else None,
        "nvt": float(row.nvt) if row.nvt is not None else None,
        "exchange_balance": float(row.exchange_balance) if row.exchange_balance is not None else None,
        "exchange_inflow": float(row.exchange_inflow) if row.exchange_inflow is not None else None,
        "exchange_outflow": float(row.exchange_outflow) if row.exchange_outflow is not None else None,
        "active_addresses": float(row.active_addresses) if row.active_addresses is not None else None,
        "network_profit_loss": float(row.network_profit_loss) if row.network_profit_loss is not None else None,
        "regime": row.regime,
        "metric_date": row.metric_date.isoformat(),
        "snapshot_at": row.snapshot_at.isoformat(),
    }


@router.get("/overview")
async def overview(_user: CurrentUser, db: DBSession):
    """Latest on-chain snapshot for BTC and ETH."""
    r = redis_service.get_redis()
    result = {}

    for asset in ASSETS:
        cached = await r.get(f"chainpulse:latest:{asset}")
        if cached:
            result[asset] = json.loads(cached)
            continue

        row = (
            await db.execute(
                select(ChainPulseSnapshot)
                .where(ChainPulseSnapshot.asset == asset)
                .order_by(desc(ChainPulseSnapshot.metric_date))
                .limit(1)
            )
        ).scalar_one_or_none()

        result[asset] = _row_to_dict(row) if row else None

    return result


@router.get("/history/{asset}")
async def history(asset: str, _user: CurrentUser, db: DBSession):
    """90-day daily history for an asset (bitcoin | ethereum)."""
    asset = asset.lower()
    if asset not in ASSETS:
        return {"error": "asset must be 'bitcoin' or 'ethereum'", "items": []}

    since = datetime.now(timezone.utc) - timedelta(days=90)
    rows = (
        await db.execute(
            select(ChainPulseSnapshot)
            .where(
                ChainPulseSnapshot.asset == asset,
                ChainPulseSnapshot.metric_date >= since,
            )
            .order_by(asc(ChainPulseSnapshot.metric_date))
        )
    ).scalars().all()

    return {"asset": asset, "items": [_row_to_dict(r) for r in rows]}


@router.get("/regime")
async def regime(_user: CurrentUser):
    """Current macro regime for BTC and ETH (from Redis cache)."""
    r = redis_service.get_redis()
    result = {}
    for asset in ASSETS:
        raw = await r.get(f"chainpulse:regime:{asset}")
        result[asset] = raw.decode() if raw else None
    return result
