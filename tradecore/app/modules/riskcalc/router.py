"""RiskCalc API."""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.dependencies import CurrentUser, DBSession
from app.errors import AppError
from app.models.riskcalc import RiskCalcHistory
from app.modules.riskcalc.calculator import RiskInput, calculate_position

router = APIRouter(prefix="/riskcalc", tags=["riskcalc"])


class CalcRequest(BaseModel):
    account_balance_usd: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=100)
    entry_price: float = Field(gt=0)
    stop_loss_price: float = Field(gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    max_leverage: float = Field(default=20.0, gt=0, le=125)
    asset_type: str = Field(default="futures")
    side: str = Field(default="long")
    symbol: str | None = None
    oracle_signal_id: UUID | None = None


@router.post("/calculate")
async def calculate(payload: CalcRequest, user: CurrentUser, db: DBSession):
    try:
        result = calculate_position(
            RiskInput(
                account_balance_usd=payload.account_balance_usd,
                risk_pct=payload.risk_pct,
                entry_price=payload.entry_price,
                stop_loss_price=payload.stop_loss_price,
                take_profit_price=payload.take_profit_price,
                max_leverage=payload.max_leverage,
                asset_type=payload.asset_type,
                side=payload.side,
            )
        )
    except ValueError as e:
        raise AppError(400, str(e), "INVALID_INPUT") from e

    row = RiskCalcHistory(
        user_id=user.id,
        symbol=payload.symbol.upper() if payload.symbol else None,
        account_balance_usd=Decimal(str(round(payload.account_balance_usd, 2))),
        risk_pct=Decimal(str(round(payload.risk_pct, 2))),
        risk_amount_usd=Decimal(str(result["risk_amount_usd"])),
        entry_price=Decimal(str(payload.entry_price)),
        stop_loss_price=Decimal(str(payload.stop_loss_price)),
        take_profit_price=Decimal(str(payload.take_profit_price)) if payload.take_profit_price else None,
        stop_distance_pct=Decimal(str(round(result["stop_distance_pct"], 2))),
        position_size=Decimal(str(result["position_size_units"])),
        position_size_usd=Decimal(str(result["position_size_usd"])),
        leverage=Decimal(str(result["leverage"])) if result["leverage"] else None,
        liquidation_price=Decimal(str(result["liquidation_price"])) if result["liquidation_price"] else None,
        max_loss_usd=Decimal(str(result["max_loss_usd"])),
        potential_profit_usd=Decimal(str(result["potential_profit_usd"])) if result["potential_profit_usd"] is not None else None,
        rr_ratio=Decimal(str(result["rr_ratio"])) if result["rr_ratio"] is not None else None,
        warnings=result["warnings"],
        oracle_signal_id=payload.oracle_signal_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {"id": str(row.id), **result}


@router.get("/history")
async def history(
    user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = (
        select(RiskCalcHistory)
        .where(RiskCalcHistory.user_id == user.id)
        .order_by(desc(RiskCalcHistory.calculated_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "symbol": r.symbol,
                "account_balance_usd": float(r.account_balance_usd),
                "risk_pct": float(r.risk_pct),
                "entry_price": float(r.entry_price),
                "stop_loss_price": float(r.stop_loss_price),
                "take_profit_price": float(r.take_profit_price) if r.take_profit_price else None,
                "position_size_usd": float(r.position_size_usd),
                "leverage": float(r.leverage) if r.leverage else None,
                "liquidation_price": float(r.liquidation_price) if r.liquidation_price else None,
                "max_loss_usd": float(r.max_loss_usd),
                "rr_ratio": float(r.rr_ratio) if r.rr_ratio else None,
                "warnings": r.warnings,
                "calculated_at": r.calculated_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/history/{calc_id}")
async def history_detail(calc_id: UUID, user: CurrentUser, db: DBSession):
    row = (
        await db.execute(
            select(RiskCalcHistory).where(
                RiskCalcHistory.id == calc_id,
                RiskCalcHistory.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(404, "Calculation not found", "NOT_FOUND")
    return {
        "id": str(row.id),
        "symbol": row.symbol,
        "account_balance_usd": float(row.account_balance_usd),
        "risk_pct": float(row.risk_pct),
        "risk_amount_usd": float(row.risk_amount_usd),
        "entry_price": float(row.entry_price),
        "stop_loss_price": float(row.stop_loss_price),
        "take_profit_price": float(row.take_profit_price) if row.take_profit_price else None,
        "stop_distance_pct": float(row.stop_distance_pct),
        "position_size": float(row.position_size),
        "position_size_usd": float(row.position_size_usd),
        "leverage": float(row.leverage) if row.leverage else None,
        "liquidation_price": float(row.liquidation_price) if row.liquidation_price else None,
        "max_loss_usd": float(row.max_loss_usd),
        "potential_profit_usd": float(row.potential_profit_usd) if row.potential_profit_usd else None,
        "rr_ratio": float(row.rr_ratio) if row.rr_ratio else None,
        "warnings": row.warnings,
        "oracle_signal_id": str(row.oracle_signal_id) if row.oracle_signal_id else None,
        "calculated_at": row.calculated_at.isoformat(),
    }
