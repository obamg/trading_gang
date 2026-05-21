"""ListingWatch detector — diffs current exchange instrument lists against
a Redis-cached "known" set, inserts a NewListingEvent for every truly new
symbol, fires a ``listing_detected`` alert, and writes the symbol into the
Bybit force-subscribe set so the WS picks it up immediately.

Cross-listing logic: if a newly-listed symbol's base asset already trades
on another (exchange, market_type), the row is flagged is_cross_listing
and other_exchanges is populated.

Bootstrap behavior: the first time the detector runs, the Redis known-set
is empty. We don't want to fire alerts for the entire universe on first
boot — instead we seed the known-set silently and only emit on the *next*
diff. Sentinel key ``listingwatch:bootstrapped`` tracks this.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.listing import NewListingEvent
from app.modules.listingwatch.exchanges import ListedSymbol, fetch_all
from app.services import redis_service

KNOWN_KEY = "listingwatch:known"  # set, members are "{exchange}:{market}:{symbol}"
BOOTSTRAP_KEY = "listingwatch:bootstrapped"
FORCE_SUB_KEY = "bybit:force_subscribe"  # set, watcher writes; bybit_stream reads
WATCHER_WINDOW_HOURS = 4


def _key(s: ListedSymbol) -> str:
    return f"{s.exchange}:{s.market_type}:{s.symbol}"


def _index_by_base(symbols: Iterable[ListedSymbol]) -> dict[str, list[ListedSymbol]]:
    out: dict[str, list[ListedSymbol]] = {}
    for s in symbols:
        out.setdefault(s.base_asset.upper(), []).append(s)
    return out


async def run_listingwatch_detect() -> dict[str, int]:
    """One detector tick. Idempotent."""
    if not getattr(app_settings, "listingwatch_enabled", False):
        return {"skipped": 1}

    r = redis_service.get_redis()
    current = await fetch_all()
    if not current:
        log.warning("listingwatch_no_symbols_returned")
        return {"fetched": 0}

    current_keys = {_key(s) for s in current}
    known = await r.smembers(KNOWN_KEY) or set()

    bootstrapped = await r.exists(BOOTSTRAP_KEY)
    if not bootstrapped:
        # First run — silently seed the known-set without firing alerts.
        if current_keys:
            await r.sadd(KNOWN_KEY, *current_keys)
        await r.set(BOOTSTRAP_KEY, "1")
        log.info("listingwatch_bootstrapped", seeded=len(current_keys))
        return {"bootstrapped": len(current_keys)}

    new_keys = current_keys - known
    if not new_keys:
        return {"fetched": len(current), "new": 0}

    # Build cross-listing index from the current snapshot — by base asset.
    by_base = _index_by_base(current)

    inserted = 0
    by_lookup = {_key(s): s for s in current}
    now = datetime.now(timezone.utc)
    ends = now + timedelta(hours=WATCHER_WINDOW_HOURS)

    async with AsyncSessionLocal() as db:
        for k in new_keys:
            s = by_lookup.get(k)
            if s is None:
                continue
            siblings = [
                {"exchange": x.exchange, "market_type": x.market_type, "symbol": x.symbol}
                for x in by_base.get(s.base_asset.upper(), [])
                if _key(x) != k
            ]
            listed_at = (
                datetime.fromtimestamp(s.listing_ts_ms / 1000, tz=timezone.utc)
                if s.listing_ts_ms
                else now
            )
            stmt = (
                pg_insert(NewListingEvent)
                .values(
                    exchange=s.exchange,
                    market_type=s.market_type,
                    symbol=s.symbol,
                    base_asset=s.base_asset,
                    quote_asset=s.quote_asset,
                    is_cross_listing=bool(siblings),
                    other_exchanges=siblings or None,
                    detected_at=now,
                    listed_at=listed_at,
                    watcher_ends_at=ends,
                    status="watching",
                )
                .on_conflict_do_nothing(constraint="uq_listing_event")
                .returning(NewListingEvent.id)
            )
            res = await db.execute(stmt)
            row_id = res.scalar()
            if row_id is None:
                continue
            inserted += 1

            await redis_service.publish_alert(
                "listingwatch",
                {
                    "type": "listing_detected",
                    "listing_id": str(row_id),
                    "exchange": s.exchange,
                    "market_type": s.market_type,
                    "symbol": s.symbol,
                    "base_asset": s.base_asset,
                    "is_cross_listing": bool(siblings),
                    "other_exchanges": siblings,
                    "innovation": s.innovation,
                    "detected_at": now.isoformat(),
                },
            )
            # Force the Bybit WS to subscribe even if 24h turnover is zero.
            # 5h TTL > watcher window so the symbol is always there for the
            # full life of the watcher.
            if s.exchange == "bybit" and s.market_type == "perp":
                await r.sadd(FORCE_SUB_KEY, s.symbol)
                await r.expire(FORCE_SUB_KEY, 5 * 3600)

        await db.commit()

    # Update known-set last so a crash mid-loop replays unchanged on next tick.
    await r.sadd(KNOWN_KEY, *new_keys)
    log.info("listingwatch_tick", fetched=len(current), new=len(new_keys), inserted=inserted)
    return {"fetched": len(current), "new": len(new_keys), "inserted": inserted}
