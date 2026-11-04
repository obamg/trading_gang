"""Oracle API."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import Integer, cast, desc, func, select

from app.dependencies import CurrentUser, DBSession
from app.errors import AppError
from app.models.oracle import OracleOutcome, OracleSignal
from app.models.settings import UserSettings
from app.modules.oracle.board import get_cached_board, rank_picks
from app.modules.oracle.engine import DEFAULT_WEIGHTS, compute_live_score, generate_signal

router = APIRouter(
    prefix="/oracle", tags=["oracle"],
)


def _serialize_signal(row: OracleSignal) -> dict:
    return {
        "id": str(row.id),
        "symbol": row.symbol,
        "score": row.score,
        "recommendation": row.recommendation,
        "confidence": row.confidence,
        "confluence_count": row.confluence_count,
        "entry_price": float(row.entry_price) if row.entry_price else None,
        "stop_loss": float(row.stop_loss) if row.stop_loss else None,
        "take_profit": float(row.take_profit) if row.take_profit else None,
        "rr_ratio": float(row.rr_ratio) if row.rr_ratio else None,
        "macro_score": row.macro_score,
        "is_paper": row.is_paper,
        "signal_at": row.signal_at.isoformat(),
    }


@router.get("/signals")
async def signals(
    _user: CurrentUser,
    db: DBSession,
    symbol: str | None = Query(default=None),
    recommendation: str | None = Query(default=None),
    min_score: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(OracleSignal).order_by(desc(OracleSignal.signal_at))
    if symbol:
        stmt = stmt.where(OracleSignal.symbol == symbol.upper())
    if recommendation:
        stmt = stmt.where(OracleSignal.recommendation == recommendation)
    if min_score is not None:
        stmt = stmt.where(func.abs(OracleSignal.score) >= min_score)
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_serialize_signal(r) for r in rows]}


@router.get("/signals/{signal_id}")
async def signal_detail(signal_id: UUID, _user: CurrentUser, db: DBSession):
    row = (
        await db.execute(select(OracleSignal).where(OracleSignal.id == signal_id))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(404, "Signal not found", "NOT_FOUND")
    outcome = (
        await db.execute(select(OracleOutcome).where(OracleOutcome.signal_id == signal_id))
    ).scalar_one_or_none()
    base = _serialize_signal(row)
    base["signals_breakdown"] = row.signals_breakdown
    base["outcome"] = (
        {
            "price_at_signal": float(outcome.price_at_signal),
            "price_15m": float(outcome.price_15m) if outcome.price_15m else None,
            "price_1h": float(outcome.price_1h) if outcome.price_1h else None,
            "price_4h": float(outcome.price_4h) if outcome.price_4h else None,
            "price_24h": float(outcome.price_24h) if outcome.price_24h else None,
            "pnl_15m_pct": float(outcome.pnl_15m_pct) if outcome.pnl_15m_pct else None,
            "pnl_1h_pct": float(outcome.pnl_1h_pct) if outcome.pnl_1h_pct else None,
            "pnl_4h_pct": float(outcome.pnl_4h_pct) if outcome.pnl_4h_pct else None,
            "pnl_24h_pct": float(outcome.pnl_24h_pct) if outcome.pnl_24h_pct else None,
            "was_correct_1h": outcome.was_correct_1h,
            "was_correct_4h": outcome.was_correct_4h,
        }
        if outcome else None
    )
    return base


@router.get("/performance")
async def performance(_user: CurrentUser, db: DBSession):
    total = (await db.execute(select(func.count(OracleSignal.id)))).scalar_one()
    correct_1h = (
        await db.execute(
            select(func.count(OracleOutcome.id)).where(OracleOutcome.was_correct_1h.is_(True))
        )
    ).scalar_one()
    correct_4h = (
        await db.execute(
            select(func.count(OracleOutcome.id)).where(OracleOutcome.was_correct_4h.is_(True))
        )
    ).scalar_one()
    measured_1h = (
        await db.execute(
            select(func.count(OracleOutcome.id)).where(OracleOutcome.was_correct_1h.isnot(None))
        )
    ).scalar_one()
    measured_4h = (
        await db.execute(
            select(func.count(OracleOutcome.id)).where(OracleOutcome.was_correct_4h.isnot(None))
        )
    ).scalar_one()
    return {
        "total_signals": total,
        "measured_1h": measured_1h,
        "measured_4h": measured_4h,
        "accuracy_1h_pct": round(correct_1h / measured_1h * 100, 2) if measured_1h else None,
        "accuracy_4h_pct": round(correct_4h / measured_4h * 100, 2) if measured_4h else None,
    }


@router.get("/live/{symbol}")
async def live(symbol: str, _user: CurrentUser, db: DBSession):
    return await compute_live_score(db, symbol)


@router.get("/board")
async def board(
    _user: CurrentUser,
    direction: str | None = Query(default=None, regex="^(bullish|bearish)$"),
    min_modules: int = Query(default=3, ge=2, le=8),
    min_score: int = Query(default=0, ge=0, le=100),
):
    """Cross-module confluence leaderboard.

    Returns the latest cached snapshot from ``oracle:board`` (Redis), filtered
    by direction / minimum agreeing modules / minimum |score|. ``stale=True``
    means the scheduler hasn't computed a snapshot yet — wait one tick.
    """
    snapshot = await get_cached_board()
    if snapshot is None:
        return {
            "bullish": [],
            "bearish": [],
            "updated_at": None,
            "stale": True,
            "min_modules": 3,
        }

    def _filter(rows: list[dict], sign: int) -> list[dict]:
        return [
            r for r in rows
            if r.get("agreeing_count", 0) >= min_modules
            and (sign * int(r.get("score") or 0)) >= min_score
        ]

    bullish = _filter(snapshot.get("bullish", []), 1) if direction in (None, "bullish") else []
    bearish = _filter(snapshot.get("bearish", []), -1) if direction in (None, "bearish") else []
    return {
        "bullish": bullish,
        "bearish": bearish,
        "updated_at": snapshot.get("updated_at"),
        "stale": False,
        "min_modules": snapshot.get("min_modules", 3),
        "universe_size": snapshot.get("universe_size"),
    }


TRACK_RECORD_DAYS = 7
SIMILAR_LOOKBACK_DAYS = 30


def _outcome_stats(n, hit, move) -> dict:
    return {
        "n": int(n or 0),
        "hit_4h_pct": round(float(hit) * 100, 1) if hit is not None else None,
        "avg_move_4h_pct": round(float(move), 3) if move is not None else None,
    }


@router.get("/picks")
async def picks(
    _user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=10, ge=1, le=10),
):
    """Top board setups right now, plus how Oracle's alerts have actually done.

    Moves are directional (sign(score) × raw 4h move), so +0.2 means the
    call was right by 0.2%. Alerts are judged against the non-alerted
    signals from the same window, never against zero — a rally flatters
    every long (see docs/ORACLE_SIGNAL_HARNESS.md, Eval A).
    """
    now = datetime.now(timezone.utc)
    hit = func.avg(cast(OracleOutcome.was_correct_4h, Integer))
    move = func.avg(func.sign(OracleSignal.score) * OracleOutcome.pnl_4h_pct)

    async def _grouped(key, days: int) -> dict:
        stmt = (
            select(key.label("k"), func.count().label("n"), hit.label("hit"), move.label("move"))
            .join(OracleOutcome, OracleOutcome.signal_id == OracleSignal.id)
            .where(OracleOutcome.was_correct_4h.isnot(None))
            .where(OracleSignal.signal_at >= now - timedelta(days=days))
            .group_by(key)
        )
        return {r.k: _outcome_stats(r.n, r.hit, r.move) for r in await db.execute(stmt)}

    record = await _grouped(OracleSignal.alerted, TRACK_RECORD_DAYS)
    # Integer division in Postgres; must match board.score_bucket.
    buckets = await _grouped(func.abs(OracleSignal.score) // 10 * 10, SIMILAR_LOOKBACK_DAYS)

    snapshot = await get_cached_board()
    empty = _outcome_stats(0, None, None)
    return {
        "track_record": {
            "days": TRACK_RECORD_DAYS,
            "alerted": record.get(True, empty),
            "others": record.get(False, empty),
        },
        "picks": rank_picks(snapshot, buckets, limit) if snapshot else [],
        "similar_lookback_days": SIMILAR_LOOKBACK_DAYS,
        "updated_at": snapshot.get("updated_at") if snapshot else None,
        "stale": snapshot is None,
    }


class GenerateRequest(BaseModel):
    symbol: str
    persist: bool = True


@router.post("/generate")
async def generate(payload: GenerateRequest, user: CurrentUser, db: DBSession):
    # Pull the user's weights and min_score override
    row = (
        await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        weights = DEFAULT_WEIGHTS
        min_score = 65
        is_paper = True
    else:
        weights = {
            "macropulse": row.weight_macropulse,
            "whaleradar": row.weight_whaleradar,
            "radarx": row.weight_radarx,
            "liquidmap": row.weight_liquidmap,
            "sentimentpulse": row.weight_sentimentpulse,
            "gemradar": row.weight_gemradar,
        }
        min_score = int(row.oracle_min_score)
        is_paper = bool(row.oracle_paper_mode)

    if not payload.persist:
        return await compute_live_score(db, payload.symbol, weights=weights)

    result = await generate_signal(
        db, payload.symbol,
        weights=weights,
        min_score_to_alert=min_score,
        is_paper=is_paper,
    )
    if result is None:
        raise AppError(400, "Unable to generate signal — missing price data", "NO_PRICE_DATA")
    return result


class SettingsUpdate(BaseModel):
    weight_macropulse: int | None = Field(default=None, ge=0, le=100)
    weight_whaleradar: int | None = Field(default=None, ge=0, le=100)
    weight_radarx: int | None = Field(default=None, ge=0, le=100)
    weight_liquidmap: int | None = Field(default=None, ge=0, le=100)
    weight_sentimentpulse: int | None = Field(default=None, ge=0, le=100)
    weight_gemradar: int | None = Field(default=None, ge=0, le=100)
    oracle_min_score: int | None = Field(default=None, ge=0, le=100)
    oracle_min_confluence: int | None = Field(default=None, ge=0, le=6)
    oracle_paper_mode: bool | None = None


@router.post("/settings")
async def update_settings(payload: SettingsUpdate, user: CurrentUser, db: DBSession):
    row = (
        await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        row = UserSettings(user_id=user.id)
        db.add(row)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return {
        "weight_macropulse": row.weight_macropulse,
        "weight_whaleradar": row.weight_whaleradar,
        "weight_radarx": row.weight_radarx,
        "weight_liquidmap": row.weight_liquidmap,
        "weight_sentimentpulse": row.weight_sentimentpulse,
        "weight_gemradar": row.weight_gemradar,
        "oracle_min_score": row.oracle_min_score,
        "oracle_min_confluence": row.oracle_min_confluence,
        "oracle_paper_mode": row.oracle_paper_mode,
    }
