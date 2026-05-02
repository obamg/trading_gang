"""FlowPulse — polls Binance for order flow signals every 2 minutes.

Three signals per symbol:
  1. Order book imbalance  (depth endpoint, weight 2)
  2. Taker buy/sell ratio  (takerlongshortRatio, weight 1)
  3. Top trader positions   (topLongShortPositionRatio, weight 1)

Total budget: ~60 weight/min for 30 symbols — well within 1200/min.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.flowpulse import FlowSignal
from app.services import redis_service

SMOOTHING_WINDOW = 5  # average last 5 snapshots = 10 minutes

DEPTH_URL = f"{settings.binance_rest_url}/fapi/v1/depth"
TAKER_URL = f"{settings.binance_rest_url}/futures/data/takerlongshortRatio"
TOP_POSITION_URL = f"{settings.binance_rest_url}/futures/data/topLongShortPositionRatio"

MAX_SYMBOLS = 30

# Thresholds
BOOK_IMBALANCE_ALERT = 3.0
TAKER_RATIO_ALERT = 2.0
TOP_RATIO_EXTREME = 70.0

REDIS_TTL = 180


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


async def _fetch_depth(client: httpx.AsyncClient, symbol: str) -> dict | None:
    try:
        resp = await client.get(DEPTH_URL, params={"symbol": symbol, "limit": 5})
        resp.raise_for_status()
        data = resp.json()
        bids = sum(float(p) * float(q) for p, q in data.get("bids", []))
        asks = sum(float(p) * float(q) for p, q in data.get("asks", []))
        ratio = bids / asks if asks > 0 else 0.0
        return {"bid_usd": round(bids, 2), "ask_usd": round(asks, 2), "imbalance": round(ratio, 4)}
    except Exception as e:
        log.debug("flowpulse_depth_failed", symbol=symbol, err=str(e))
        return None


async def _fetch_taker_ratio(client: httpx.AsyncClient, symbol: str) -> dict | None:
    try:
        resp = await client.get(TAKER_URL, params={"symbol": symbol, "period": "5m", "limit": 1})
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        row = data[0]
        buy_vol = float(row.get("buyVol", 0))
        sell_vol = float(row.get("sellVol", 0))
        ratio = float(row.get("buySellRatio", 0))
        return {"buy_vol": round(buy_vol, 2), "sell_vol": round(sell_vol, 2), "ratio": round(ratio, 4)}
    except Exception as e:
        log.debug("flowpulse_taker_failed", symbol=symbol, err=str(e))
        return None


async def _fetch_top_positions(client: httpx.AsyncClient, symbol: str) -> dict | None:
    try:
        resp = await client.get(TOP_POSITION_URL, params={"symbol": symbol, "period": "5m", "limit": 1})
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        row = data[0]
        long_ratio = float(row.get("longAccount", 0)) * 100
        short_ratio = float(row.get("shortAccount", 0)) * 100
        return {"long_ratio": round(long_ratio, 2), "short_ratio": round(short_ratio, 2)}
    except Exception as e:
        log.debug("flowpulse_top_pos_failed", symbol=symbol, err=str(e))
        return None


def _compute_composite(
    book: dict | None, taker: dict | None, top: dict | None
) -> tuple[str, float]:
    direction_score = 0.0
    intensity = 0.0
    weights_used = 0.0

    if book:
        imb = book["imbalance"]
        if imb > 1.0:
            direction_score += 0.4
            intensity += _clip01((imb - 1.0) / 3.0) * 0.4
        elif imb < 1.0:
            direction_score -= 0.4
            intensity += _clip01((1.0 - imb) / 0.7) * 0.4
        weights_used += 0.4

    if taker:
        ratio = taker["ratio"]
        if ratio > 1.0:
            direction_score += 0.4
            intensity += _clip01((ratio - 1.0) / 1.5) * 0.4
        elif ratio < 1.0:
            direction_score -= 0.4
            intensity += _clip01((1.0 - ratio) / 0.5) * 0.4
        weights_used += 0.4

    if top:
        long_r = top["long_ratio"]
        if long_r > 55:
            direction_score += 0.2
            intensity += _clip01((long_r - 50) / 30) * 0.2
        elif long_r < 45:
            direction_score -= 0.2
            intensity += _clip01((50 - long_r) / 30) * 0.2
        weights_used += 0.2

    if weights_used == 0:
        return "neutral", 0.0

    if direction_score > 0.1:
        direction = "bullish"
    elif direction_score < -0.1:
        direction = "bearish"
    else:
        direction = "neutral"

    return direction, round(_clip01(intensity), 3)


async def scan_symbol(
    db: AsyncSession, client: httpx.AsyncClient, symbol: str, snapshot_at: datetime
) -> bool:
    book, taker, top = await asyncio.gather(
        _fetch_depth(client, symbol),
        _fetch_taker_ratio(client, symbol),
        _fetch_top_positions(client, symbol),
    )

    if not book and not taker and not top:
        return False

    direction, intensity = _compute_composite(book, taker, top)

    row = FlowSignal(
        symbol=symbol,
        bid_usd=Decimal(str(book["bid_usd"])) if book else None,
        ask_usd=Decimal(str(book["ask_usd"])) if book else None,
        book_imbalance=Decimal(str(book["imbalance"])) if book else None,
        taker_buy_vol=Decimal(str(taker["buy_vol"])) if taker else None,
        taker_sell_vol=Decimal(str(taker["sell_vol"])) if taker else None,
        taker_ratio=Decimal(str(taker["ratio"])) if taker else None,
        top_long_ratio=Decimal(str(top["long_ratio"])) if top else None,
        top_short_ratio=Decimal(str(top["short_ratio"])) if top else None,
        direction=direction,
        intensity=Decimal(str(intensity)),
        snapshot_at=snapshot_at,
    )
    db.add(row)

    # Alert only on smoothed extremes (checked after smoothing in run_scan)
    return True


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


async def _smooth_and_cache(db: AsyncSession, symbols: list[str], snapshot_at: datetime) -> None:
    """Compute rolling averages over last SMOOTHING_WINDOW snapshots and cache in Redis."""
    r = redis_service.get_redis()

    for symbol in symbols:
        rows = (
            await db.execute(
                select(FlowSignal)
                .where(FlowSignal.symbol == symbol)
                .order_by(desc(FlowSignal.snapshot_at))
                .limit(SMOOTHING_WINDOW)
            )
        ).scalars().all()

        if not rows:
            continue

        book_vals = [float(r.book_imbalance) for r in rows if r.book_imbalance is not None]
        taker_vals = [float(r.taker_ratio) for r in rows if r.taker_ratio is not None]
        top_long_vals = [float(r.top_long_ratio) for r in rows if r.top_long_ratio is not None]
        bid_vals = [float(r.bid_usd) for r in rows if r.bid_usd is not None]
        ask_vals = [float(r.ask_usd) for r in rows if r.ask_usd is not None]

        avg_book = round(_avg(book_vals), 4) if book_vals else None
        avg_taker = round(_avg(taker_vals), 4) if taker_vals else None
        avg_top_long = round(_avg(top_long_vals), 2) if top_long_vals else None
        avg_bid = round(_avg(bid_vals), 2) if bid_vals else None
        avg_ask = round(_avg(ask_vals), 2) if ask_vals else None

        book_d = {"imbalance": avg_book} if avg_book is not None else None
        taker_d = {"ratio": avg_taker} if avg_taker is not None else None
        top_d = {"long_ratio": avg_top_long} if avg_top_long is not None else None

        direction, intensity = _compute_composite(book_d, taker_d, top_d)

        cache = {
            "book_imbalance": avg_book,
            "taker_ratio": avg_taker,
            "top_long_ratio": avg_top_long,
            "bid_usd": avg_bid,
            "ask_usd": avg_ask,
            "direction": direction,
            "intensity": intensity,
            "window": len(rows),
            "ts": snapshot_at.isoformat(),
        }
        await r.set(f"flow:{symbol}", json.dumps(cache), ex=REDIS_TTL)

        # Alert on smoothed extremes only
        is_extreme = (
            (avg_book is not None and (avg_book >= BOOK_IMBALANCE_ALERT or avg_book <= 1.0 / BOOK_IMBALANCE_ALERT))
            or (avg_taker is not None and (avg_taker >= TAKER_RATIO_ALERT or avg_taker <= 1.0 / TAKER_RATIO_ALERT))
            or (avg_top_long is not None and (avg_top_long >= TOP_RATIO_EXTREME or (100 - avg_top_long) >= TOP_RATIO_EXTREME))
        )
        if is_extreme:
            alert = {
                "module": "flowpulse",
                "type": "flow_signal",
                "symbol": symbol,
                "direction": direction,
                "intensity": intensity,
                "book_imbalance": avg_book,
                "taker_ratio": avg_taker,
                "top_long_ratio": avg_top_long,
                "smoothed": True,
                "window": len(rows),
                "detected_at": snapshot_at.isoformat(),
            }
            await redis_service.publish_alert("flowpulse", alert)
            log.info("flowpulse_alert", symbol=symbol, direction=direction, intensity=intensity, window=len(rows))


async def run_scan() -> int:
    symbols = await redis_service.get_symbol_list()
    if not symbols:
        return 0
    symbols = symbols[:MAX_SYMBOLS]
    snapshot_at = datetime.now(timezone.utc)
    scanned = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        async with AsyncSessionLocal() as db:
            for symbol in symbols:
                try:
                    ok = await scan_symbol(db, client, symbol, snapshot_at)
                    if ok:
                        scanned += 1
                except Exception as e:
                    log.error("flowpulse_scan_failed", symbol=symbol, err=str(e))
                await asyncio.sleep(0.15)
            await db.commit()

            # Smooth and cache after all raw data is committed
            await _smooth_and_cache(db, symbols, snapshot_at)

    if scanned:
        log.info("flowpulse_scan_complete", scanned=scanned, symbols=len(symbols))
    return scanned


async def run_scheduled_scan() -> None:
    try:
        await run_scan()
    except Exception as e:
        log.error("flowpulse_scheduled_scan_failed", err=str(e))


async def get_flow_signal(symbol: str) -> dict | None:
    r = redis_service.get_redis()
    raw = await r.get(f"flow:{symbol.upper()}")
    if not raw:
        return None
    return json.loads(raw)


__all__ = ["run_scan", "run_scheduled_scan", "get_flow_signal", "scan_symbol"]
