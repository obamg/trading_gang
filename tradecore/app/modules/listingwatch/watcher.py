"""Per-listing watcher — runs every 30s, iterates active listings, refreshes
state, evaluates signals, persists fired signals, and ends the watcher at
T+4h.

Data source resolution per listing:
  - bybit:perp listings → use the listing symbol directly (Bybit WS feeds it).
  - cross-listings on binance/okx whose base asset is also on Bybit perp →
    use the Bybit perp sibling's data (live price action lands there first).
  - listings without a Bybit perp sibling → no live data; we still keep the
    row alive (frontend shows the cross-listing alert) but signals won't fire.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.listing import ListingSignal, NewListingEvent
from app.modules.listingwatch.signals import Signal, WatcherCtx, evaluate_all
from app.services import redis_service

COOLDOWN_MINUTES = 30


# ---------- data-source resolution ----------


def _resolve_data_symbol(event: NewListingEvent) -> str | None:
    """Return the Bybit perp ticker that has live data for this listing,
    or None if no Bybit data source is available."""
    if event.exchange == "bybit" and event.market_type == "perp":
        return event.symbol
    # Cross-listings: look in other_exchanges for a Bybit perp sibling.
    siblings = event.other_exchanges or []
    for s in siblings:
        if s.get("exchange") == "bybit" and s.get("market_type") == "perp":
            return s.get("symbol")
    return None


# ---------- state computation ----------


def _compute_window_extremes(
    candles: list[dict], window_seconds: int, now_ms: int
) -> tuple[float | None, float | None]:
    """Return (high, low) over the last *window_seconds* of candle data.
    candles are stored newest-first, with single-letter binance kline keys
    (h, l, t)."""
    cutoff = now_ms - window_seconds * 1000
    hi = lo = None
    for c in candles:
        ts = int(c.get("T") or c.get("t") or 0)
        if ts < cutoff:
            break
        try:
            h = float(c["h"])
            l = float(c["l"])
        except (KeyError, TypeError, ValueError):
            continue
        hi = h if hi is None else max(hi, h)
        lo = l if lo is None else min(lo, l)
    return hi, lo


def _compute_volume_and_cvd(trades: list[dict], window_seconds: int, now_ms: int) -> tuple[float, float]:
    """Return (volume_usd, cvd_usd) over the last *window_seconds* of trades.
    Trades come from a Redis stream — strings only, so cast each field."""
    cutoff = now_ms - window_seconds * 1000
    vol = 0.0
    cvd = 0.0
    for t in trades:
        try:
            ts = int(t.get("T", 0))
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        try:
            usd = float(t.get("usd", 0))
        except (TypeError, ValueError):
            continue
        vol += usd
        # m=1 → buyer is maker → trade was taker-sell → CVD negative.
        if str(t.get("m", "0")) == "1":
            cvd -= usd
        else:
            cvd += usd
    return vol, cvd


def _baseline_volume(trades: list[dict], window_seconds: int, now_ms: int) -> float:
    """Median 5m-bucket volume over the *previous* 5 windows (skip the
    current bucket). Returns 0.0 when not enough data."""
    buckets: list[float] = []
    for k in range(1, 6):
        end = now_ms - (k - 1) * window_seconds * 1000
        start = end - window_seconds * 1000
        v = 0.0
        for t in trades:
            try:
                ts = int(t.get("T", 0))
                usd = float(t.get("usd", 0))
            except (TypeError, ValueError):
                continue
            if start <= ts < end:
                v += usd
        buckets.append(v)
    # The first bucket is the current one — exclude it from the baseline.
    prior = sorted(buckets[1:])
    if not prior:
        return 0.0
    mid = len(prior) // 2
    if len(prior) % 2:
        return prior[mid]
    return (prior[mid - 1] + prior[mid]) / 2


async def _build_ctx(event: NewListingEvent, data_symbol: str) -> WatcherCtx | None:
    """Read Redis state for *data_symbol* and assemble a WatcherCtx. Returns
    None when there's no candle data yet (watcher will retry next tick)."""
    candles = await redis_service.get_candles(data_symbol, limit=50)
    if not candles:
        return None
    last = candles[0]
    try:
        last_price = float(last["c"])
    except (KeyError, TypeError, ValueError):
        return None

    now_ms = int(time.time() * 1000)
    hi_15, lo_15 = _compute_window_extremes(candles, 15 * 60, now_ms)
    hi_1h, lo_1h = _compute_window_extremes(candles, 60 * 60, now_ms)
    if hi_15 is None or lo_15 is None:
        # Fallback: latest candle alone.
        hi_15 = float(last.get("h", last_price))
        lo_15 = float(last.get("l", last_price))
    if hi_1h is None or lo_1h is None:
        hi_1h = hi_15
        lo_1h = lo_15

    trades = await redis_service.read_trades(data_symbol, count=1000)
    vol_5m, cvd_5m = _compute_volume_and_cvd(trades, 5 * 60, now_ms)
    baseline = _baseline_volume(trades, 5 * 60, now_ms)
    funding = await redis_service.get_funding_rate(data_symbol)

    # T-0 price: capture once.
    t0_price = float(event.t0_price) if event.t0_price is not None else last_price

    seconds_since_t0 = int(
        (datetime.now(timezone.utc) - (event.t0_captured_at or event.detected_at)).total_seconds()
    )

    # Floor age: how long has the current 1h-low been the low? Approximate by
    # the timestamp of the candle whose low matches lo_1h.
    floor_age_s: int | None = None
    if lo_1h:
        for c in candles:
            try:
                if abs(float(c.get("l", 0)) - lo_1h) < 1e-9:
                    ts = int(c.get("t") or c.get("T") or 0)
                    if ts:
                        floor_age_s = max(0, int((now_ms - ts) / 1000))
                    break
            except (TypeError, ValueError):
                continue

    return WatcherCtx(
        symbol=data_symbol,
        seconds_since_t0=seconds_since_t0,
        t0_price=t0_price,
        last_price=last_price,
        high_15m=hi_15,
        low_15m=lo_15,
        high_1h=hi_1h,
        low_1h=lo_1h,
        cvd_5m_usd=cvd_5m,
        volume_5m_usd=vol_5m,
        volume_5m_baseline_usd=baseline,
        funding_pct=funding,
        floor_set_seconds_ago=floor_age_s,
        is_cross_listing=event.is_cross_listing,
    )


# ---------- persistence ----------


async def _persist_signal(
    db: AsyncSession,
    event: NewListingEvent,
    sig: Signal,
    ctx: WatcherCtx,
) -> None:
    db.add(
        ListingSignal(
            listing_id=event.id,
            signal_type=sig.type,
            direction=sig.direction,
            conviction=Decimal(str(sig.conviction)),
            price_at_emit=Decimal(str(ctx.last_price)),
            seconds_since_t0=ctx.seconds_since_t0,
            context=sig.context,
            emitted_at=datetime.now(timezone.utc),
        )
    )
    event.signal_count = (event.signal_count or 0) + 1


async def _publish_alert(event: NewListingEvent, sig: Signal, ctx: WatcherCtx) -> None:
    await redis_service.publish_alert(
        "listingwatch",
        {
            "type": sig.type,
            "listing_id": str(event.id),
            "exchange": event.exchange,
            "market_type": event.market_type,
            "symbol": event.symbol,
            "data_symbol": ctx.symbol,
            "base_asset": event.base_asset,
            "direction": sig.direction,
            "conviction": sig.conviction,
            "price": ctx.last_price,
            "seconds_since_t0": ctx.seconds_since_t0,
            "is_cross_listing": event.is_cross_listing,
            "context": sig.context,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        },
    )


# ---------- main tick ----------


async def run_listingwatch_tick() -> dict[str, int]:
    """One watcher tick — process every active listing.

    - End watchers past T+4h.
    - Capture T-0 snapshot when first data lands.
    - Refresh rolling state, evaluate signals, fire & cooldown.
    """
    if not getattr(app_settings, "listingwatch_enabled", False):
        return {"skipped": 1}

    r = redis_service.get_redis()
    now = datetime.now(timezone.utc)
    stats = {"active": 0, "ended": 0, "signals": 0, "no_data": 0}

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(NewListingEvent).where(NewListingEvent.status == "watching")
            )
        ).scalars().all()
        stats["active"] = len(rows)

        for event in rows:
            if event.watcher_ends_at and now >= event.watcher_ends_at:
                event.status = "ended"
                stats["ended"] += 1
                # Drain force-subscribe membership for Bybit perps so they
                # fall back to volume-filtering once turnover is established.
                if event.exchange == "bybit" and event.market_type == "perp":
                    try:
                        await r.srem("bybit:force_subscribe", event.symbol)
                    except Exception:
                        pass
                continue

            data_symbol = _resolve_data_symbol(event)
            if data_symbol is None:
                stats["no_data"] += 1
                continue

            ctx = await _build_ctx(event, data_symbol)
            if ctx is None:
                stats["no_data"] += 1
                continue

            # T-0 capture (first time we have a price for this listing).
            if event.t0_price is None:
                event.t0_price = Decimal(str(ctx.last_price))
                event.t0_captured_at = now

            # Refresh persistent rolling state.
            event.last_price = Decimal(str(ctx.last_price))
            event.high_15m = Decimal(str(ctx.high_15m))
            event.low_15m = Decimal(str(ctx.low_15m))
            event.high_1h = Decimal(str(ctx.high_1h))
            event.low_1h = Decimal(str(ctx.low_1h))
            if ctx.funding_pct is not None:
                event.last_funding_pct = Decimal(str(ctx.funding_pct))

            # Evaluate signals; respect per-(symbol, signal_type) cooldown.
            for sig in evaluate_all(ctx):
                cd_key = f"cooldown:listingwatch:{event.symbol}:{sig.type}"
                if await r.exists(cd_key):
                    continue
                await _persist_signal(db, event, sig, ctx)
                await _publish_alert(event, sig, ctx)
                await r.set(cd_key, "1", ex=COOLDOWN_MINUTES * 60)
                stats["signals"] += 1

        await db.commit()

    log.info("listingwatch_watcher_tick", **stats)
    return stats
