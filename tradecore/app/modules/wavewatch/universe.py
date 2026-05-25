"""WaveWatch universe — Bybit Innovation Zone membership.

Refreshes the set of innovation-flagged assets every 15 min:
  • Bybit perp innovation tokens (read from /v5/market/instruments-info)
  • Bybit spot innovation tokens (already fetched by listingwatch's
    exchanges.fetch_bybit_spot_innovation — we just reuse it)

For perps we also write the symbols to ``wavewatch:force_subscribe:bybit``
with TTL so the existing bybit_stream picks them up even if their 24h
turnover is below the universe gate. This guarantees we have candle and
trade data in Redis to score against.

Spot tokens get tracked in the DB but not WS-fed today (would need a
separate spot stream manager). They show up in /wavewatch/universe but
do not currently produce wave alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.wavewatch import WaveAsset
from app.modules.listingwatch.exchanges import (
    fetch_bybit_perps,
    fetch_bybit_spot_innovation,
)
from app.services import redis_service

FORCE_SUB_KEY = "wavewatch:force_subscribe:bybit"
FORCE_SUB_TTL_SECONDS = 30 * 60  # 30min — refreshed every 15min, so 2× the cadence


async def refresh_universe() -> dict:
    """Discover current Bybit Innovation Zone assets, upsert wave_assets,
    and force-subscribe the Bybit WS to perp symbols. Idempotent."""
    if not getattr(app_settings, "wavewatch_enabled", False):
        return {"skipped": 1}

    async with httpx.AsyncClient() as client:
        try:
            perps = await fetch_bybit_perps(client)
        except Exception as e:
            log.warning("wavewatch_perp_fetch_failed", err=str(e))
            perps = []
        try:
            spots = await fetch_bybit_spot_innovation(client)
        except Exception as e:
            log.warning("wavewatch_spot_fetch_failed", err=str(e))
            spots = []

    innovation_perps = [p for p in perps if p.innovation]
    # Cross-reference: a perp whose API row didn't carry innovation but
    # whose base asset has a spot innovation pair is still "innovation".
    spot_bases = {s.base_asset.upper() for s in spots}
    perps_by_base = {p.base_asset.upper(): p for p in perps}
    for base in spot_bases:
        p = perps_by_base.get(base)
        if p is not None and p not in innovation_perps:
            innovation_perps.append(p)

    current = innovation_perps + spots
    now = datetime.now(timezone.utc)

    # Update DB: upsert active rows; mark removed any rows not in current.
    async with AsyncSessionLocal() as db:
        seen_keys: set[tuple[str, str, str]] = set()
        for s in current:
            key = (s.exchange, s.market_type, s.symbol)
            seen_keys.add(key)
            stmt = (
                pg_insert(WaveAsset)
                .values(
                    exchange=s.exchange,
                    market_type=s.market_type,
                    symbol=s.symbol,
                    base_asset=s.base_asset,
                    status="active",
                    first_seen_at=now,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_wave_asset",
                    set_={"status": "active", "last_seen_at": now},
                )
            )
            await db.execute(stmt)
        await db.commit()
        active_db = (
            await db.execute(
                select(WaveAsset).where(WaveAsset.status == "active")
            )
        ).scalars().all()
        for row in active_db:
            if (row.exchange, row.market_type, row.symbol) not in seen_keys:
                row.status = "removed"
        await db.commit()

    # Force-subscribe perp symbols to Bybit WS so the stream picks them up
    # even if 24h turnover is below the gate. Spot symbols aren't WS-fed
    # today (needs a separate spot stream manager — deferred to phase B).
    r = redis_service.get_redis()
    perp_syms = [p.symbol for p in innovation_perps if p.symbol]
    if perp_syms:
        await r.sadd(FORCE_SUB_KEY, *perp_syms)
        await r.expire(FORCE_SUB_KEY, FORCE_SUB_TTL_SECONDS)

    counts = {
        "perps": len(innovation_perps),
        "spots": len(spots),
        "force_subscribed": len(perp_syms),
    }
    log.info("wavewatch_universe_refreshed", **counts)
    return counts


async def list_active_perps(db: AsyncSession) -> list[WaveAsset]:
    """Active innovation perps — the symbols the detector should score."""
    rows = (
        await db.execute(
            select(WaveAsset).where(
                WaveAsset.status == "active",
                WaveAsset.exchange == "bybit",
                WaveAsset.market_type == "perp",
            )
        )
    ).scalars().all()
    return list(rows)


__all__ = ["refresh_universe", "list_active_perps", "FORCE_SUB_KEY"]
