"""LiquidMap — subscribes to Redis `liquidations` pub/sub, persists notable events,
maintains a per-symbol price-bucketed heatmap in Redis, and fires alerts on
very large liquidations.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.liquidmap import LiquidationEvent
from app.services import redis_service

PERSIST_THRESHOLD = 100_000.0   # $100k+ saved to DB
ALERT_THRESHOLD = 1_000_000.0   # $1M+ published as large_liquidation alert
HEATMAP_BUCKET_PCT = 0.001       # 0.1% buckets
HEATMAP_TTL_SECONDS = 4 * 3600


def _price_bucket(price: float, bucket_pct: float = HEATMAP_BUCKET_PCT) -> str:
    if price <= 0:
        return "0"
    step = price * bucket_pct
    bucket = round(price / step) * step
    return f"{bucket:.8f}"


async def ingest_event(db: AsyncSession, event: dict) -> dict | None:
    """Handle one liquidation event from the pubsub. Returns alert dict if fired."""
    try:
        symbol = str(event["symbol"]).upper()
        side = str(event["side"]).lower()
        size_usd = float(event.get("usd") or event.get("size_usd") or event.get("size") or 0)
        price = float(event.get("price") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or size_usd <= 0:
        return None

    # Heatmap update always — independent of thresholds
    r = redis_service.get_redis()
    key = f"liqmap:{symbol}"
    field = f"{side}:{_price_bucket(price)}"
    await r.hincrbyfloat(key, field, size_usd)
    await r.expire(key, HEATMAP_TTL_SECONDS)

    if size_usd < PERSIST_THRESHOLD:
        return None

    detected_at = datetime.now(timezone.utc)
    row = LiquidationEvent(
        symbol=symbol,
        side=side,
        size_usd=Decimal(str(round(size_usd, 2))),
        price=Decimal(str(price)),
        is_cascade=False,
        detected_at=detected_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    if size_usd >= ALERT_THRESHOLD:
        alert = {
            "module": "liquidmap",
            "type": "large_liquidation",
            "id": str(row.id),
            "symbol": symbol,
            "side": side,
            "size_usd": round(size_usd, 2),
            "price": price,
            "detected_at": detected_at.isoformat(),
        }
        await redis_service.publish_alert("liquidmap", alert)
        log.info("liquidmap_large_liquidation", symbol=symbol, side=side, usd=round(size_usd, 2))
        return alert
    return None


async def get_heatmap(symbol: str, top_n: int = 20) -> list[dict]:
    """Cluster buckets within 0.5% of each other and return top N concentration levels."""
    r = redis_service.get_redis()
    raw = await r.hgetall(f"liqmap:{symbol.upper()}")
    if not raw:
        return []
    # raw keys look like "long:12345.67000000"
    levels: list[tuple[str, float, float]] = []
    for field, value in raw.items():
        try:
            side, price_str = field.split(":", 1)
            price = float(price_str)
            size = float(value)
        except (ValueError, TypeError):
            continue
        levels.append((side, price, size))
    if not levels:
        return []

    levels.sort(key=lambda x: x[1])  # by price
    clustered: list[dict] = []
    CLUSTER_PCT = 0.005
    for side, price, size in levels:
        merged = False
        for c in clustered:
            if c["side"] != side:
                continue
            center = c["price"]
            if center and abs(price - center) / center <= CLUSTER_PCT:
                c["size_usd"] += size
                c["price"] = (center * (c["count"]) + price) / (c["count"] + 1)
                c["count"] += 1
                merged = True
                break
        if not merged:
            clustered.append({"side": side, "price": price, "size_usd": size, "count": 1})
    clustered.sort(key=lambda c: c["size_usd"], reverse=True)
    return [
        {"side": c["side"], "price": round(c["price"], 8), "size_usd": round(c["size_usd"], 2)}
        for c in clustered[:top_n]
    ]


# ---------- pubsub listener ----------


class LiquidationListener:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop())
        log.info("liquidmap_listener_started")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        log.info("liquidmap_listener_stopped")

    async def _loop(self) -> None:
        r = redis_service.get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe("liquidations")
        try:
            while not self._stopping.is_set():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if msg is None:
                    continue
                data = msg.get("data")
                if not data:
                    continue
                try:
                    event = json.loads(data) if isinstance(data, str) else json.loads(data.decode())
                except (json.JSONDecodeError, AttributeError):
                    continue
                try:
                    async with AsyncSessionLocal() as db:
                        await ingest_event(db, event)
                except Exception as e:
                    log.error("liquidmap_ingest_failed", err=str(e))
        finally:
            await pubsub.unsubscribe("liquidations")
            await pubsub.aclose()


listener = LiquidationListener()

__all__ = ["ingest_event", "get_heatmap", "listener", "LiquidationListener"]
