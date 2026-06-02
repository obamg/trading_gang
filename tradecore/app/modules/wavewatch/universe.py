"""WaveWatch universe — Innovation Zone membership across Bybit + Binance.

Refreshes the set of innovation-flagged assets every 15 min:
  • Bybit perp innovation tokens (/v5/market/instruments-info, symbolType)
  • Bybit spot innovation tokens (listingwatch.fetch_bybit_spot_innovation)
  • Binance spot Innovation/Seed tier (listingwatch.fetch_binance_innovation,
    via the public BAPI product-tags endpoint)
  • Binance perps cross-referenced against the spot Innovation/Seed set —
    a perp counts as innovation if its base asset is tagged on spot

For perps we write the symbols to per-exchange force-subscribe sets with
TTL so the streams pick them up even if their 24h turnover is below the
universe gate:
  • bybit  → ``wavewatch:force_subscribe:bybit``  (read by bybit_stream)
  • binance → ``wavewatch:force_subscribe:binance`` (read by binance_stream)

Spot innovation tokens get tracked in the DB but not WS-fed today (would
need a separate spot stream manager). They show up in /wavewatch/universe
but do not currently produce wave alerts.
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
    ListedSymbol,
    fetch_binance_innovation,
    fetch_binance_perps,
    fetch_bybit_perps,
    fetch_bybit_spot_innovation,
)
from app.services import redis_service

FORCE_SUB_KEY = "wavewatch:force_subscribe:bybit"
FORCE_SUB_KEY_BINANCE = "wavewatch:force_subscribe:binance"
FORCE_SUB_TTL_SECONDS = 30 * 60  # 30min — refreshed every 15min, so 2× the cadence


async def refresh_universe() -> dict:
    """Discover current Bybit Innovation Zone assets, upsert wave_assets,
    and force-subscribe the Bybit WS to perp symbols. Idempotent."""
    if not getattr(app_settings, "wavewatch_enabled", False):
        return {"skipped": 1}

    async with httpx.AsyncClient() as client:
        try:
            bybit_perps = await fetch_bybit_perps(client)
        except Exception as e:
            log.warning("wavewatch_perp_fetch_failed", exchange="bybit", err=str(e))
            bybit_perps = []
        try:
            bybit_spots = await fetch_bybit_spot_innovation(client)
        except Exception as e:
            log.warning("wavewatch_spot_fetch_failed", exchange="bybit", err=str(e))
            bybit_spots = []
        try:
            binance_perps_all = await fetch_binance_perps(client)
        except Exception as e:
            log.warning("wavewatch_perp_fetch_failed", exchange="binance", err=str(e))
            binance_perps_all = []
        try:
            binance_spots = await fetch_binance_innovation(client)
        except Exception as e:
            log.warning("wavewatch_spot_fetch_failed", exchange="binance", err=str(e))
            binance_spots = []

    # --- Bybit innovation perps ---
    # Direct flag from instruments-info, plus cross-reference with spot
    # innovation pairs (a perp whose API row omitted the tag but whose base
    # asset has a spot innovation pair counts as innovation too).
    bybit_innovation_perps = [p for p in bybit_perps if p.innovation]
    bybit_spot_bases = {s.base_asset.upper() for s in bybit_spots}
    bybit_perps_by_base = {p.base_asset.upper(): p for p in bybit_perps}
    for base in bybit_spot_bases:
        p = bybit_perps_by_base.get(base)
        if p is not None and p not in bybit_innovation_perps:
            bybit_innovation_perps.append(p)

    # --- Binance innovation perps ---
    # Binance's perp API does not carry the Innovation/Seed tag; we infer it
    # by matching perp base assets against the spot Innovation/Seed set.
    # Bases like "1000PEPE" need normalising — strip leading multiplier so
    # a 1000PEPEUSDT perp matches a PEPE spot tag.
    def _strip_multiplier(base: str) -> str:
        b = base.upper()
        for prefix in ("1000000", "10000", "1000"):
            if b.startswith(prefix) and len(b) > len(prefix):
                return b[len(prefix):]
        return b

    binance_spot_bases = {s.base_asset.upper() for s in binance_spots}
    binance_innovation_perps = [
        p for p in binance_perps_all
        if _strip_multiplier(p.base_asset) in binance_spot_bases
    ]
    # Mark them innovation for downstream consumers — the API row itself
    # doesn't set the flag.
    binance_innovation_perps = [
        ListedSymbol(
            exchange=p.exchange,
            market_type=p.market_type,
            symbol=p.symbol,
            base_asset=p.base_asset,
            quote_asset=p.quote_asset,
            listing_ts_ms=p.listing_ts_ms,
            innovation=True,
        )
        for p in binance_innovation_perps
    ]

    innovation_perps = bybit_innovation_perps + binance_innovation_perps
    spots = bybit_spots + binance_spots
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

    # Force-subscribe perp symbols per exchange so each stream picks them up
    # even if 24h turnover is below the gate. Spot symbols aren't WS-fed
    # today (needs a separate spot stream manager — deferred to phase B).
    r = redis_service.get_redis()
    bybit_syms = [p.symbol for p in bybit_innovation_perps if p.symbol]
    binance_syms = [p.symbol for p in binance_innovation_perps if p.symbol]
    if bybit_syms:
        await r.sadd(FORCE_SUB_KEY, *bybit_syms)
        await r.expire(FORCE_SUB_KEY, FORCE_SUB_TTL_SECONDS)
    if binance_syms:
        await r.sadd(FORCE_SUB_KEY_BINANCE, *binance_syms)
        await r.expire(FORCE_SUB_KEY_BINANCE, FORCE_SUB_TTL_SECONDS)

    counts = {
        "perps_bybit": len(bybit_innovation_perps),
        "perps_binance": len(binance_innovation_perps),
        "spots_bybit": len(bybit_spots),
        "spots_binance": len(binance_spots),
        "force_subscribed": len(bybit_syms) + len(binance_syms),
    }
    log.info("wavewatch_universe_refreshed", **counts)
    return counts


async def list_active_perps(db: AsyncSession) -> list[WaveAsset]:
    """Active innovation perps — the symbols the detector should score.

    Returns both Bybit and Binance perps; the detector reads candles by
    bare symbol (``candles:{symbol}``) which both stream managers write to.
    """
    rows = (
        await db.execute(
            select(WaveAsset).where(
                WaveAsset.status == "active",
                WaveAsset.market_type == "perp",
            )
        )
    ).scalars().all()
    return list(rows)


__all__ = [
    "refresh_universe",
    "list_active_perps",
    "FORCE_SUB_KEY",
    "FORCE_SUB_KEY_BINANCE",
]
