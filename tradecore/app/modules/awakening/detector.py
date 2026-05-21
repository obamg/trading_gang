"""Awakening detector — flags USDT perps whose 24h turnover suddenly spikes
off a sleepy 7-day baseline, and auto-promotes them into ``symbols:active``
so RadarX / WhaleRadar / Oracle can ingest them within the next stream
rediscovery cycle.

Two jobs:

- ``run_awakening_tick`` (every 5 min): pulls current 24h turnover for every
  USDT perp on Binance and Bybit, compares against ``awakening:baseline``,
  fires alerts and writes force-subscribe sets when ratio + floor + ceiling
  conditions all hold.

- ``run_awakening_baseline_refresh`` (daily 00:05 UTC): pushes today's
  turnover into a per-symbol 7-day history list, then writes the median
  back into ``awakening:baseline``. First-day bootstrap silently seeds
  the history — no alerts fire until day 2.

Redis keys (all keyed by ``{exchange}:{symbol}`` to avoid Binance/Bybit
collisions):

    awakening:baseline                  hash {key: median_turnover_usd}
    awakening:hist:{exchange}:{symbol}  list of last 7 daily turnover values
    awakening:force_subscribe:binance   set, TTL 24h (read by binance_stream)
    awakening:force_subscribe:bybit     set, TTL 24h (read by bybit_stream)
    cooldown:awakening:{exchange}:{symbol}  TTL key, default 6h
    awakening:recent                    list, capped 200, JSON-encoded events
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone

from app.config import settings as app_settings
from app.logging_config import log
from app.modules.awakening.exchanges import Ticker, fetch_all_tickers
from app.services import redis_service

BASELINE_HASH = "awakening:baseline"
HIST_PREFIX = "awakening:hist"
FORCE_SUB_PREFIX = "awakening:force_subscribe"  # :{exchange}
RECENT_LIST = "awakening:recent"
COOLDOWN_PREFIX = "cooldown:awakening"  # :{exchange}:{symbol}

HISTORY_DAYS = 7
COOLDOWN_SECONDS = 6 * 3600
FORCE_SUB_TTL = 24 * 3600
RECENT_CAP = 200


def _key(t: Ticker) -> str:
    return f"{t.exchange}:{t.symbol}"


async def run_awakening_tick() -> dict[str, int]:
    """One detection pass. Idempotent — re-running mid-cooldown is a no-op."""
    if not getattr(app_settings, "awakening_enabled", False):
        return {"skipped": 1}

    ratio_threshold = getattr(app_settings, "awakening_ratio_threshold", 3.0)
    min_turnover = getattr(app_settings, "awakening_min_turnover_usd", 2_000_000.0)
    universe_ceiling = app_settings.binance_min_quote_volume_usd

    r = redis_service.get_redis()
    tickers = await fetch_all_tickers()
    if not tickers:
        log.warning("awakening_no_tickers")
        return {"fetched": 0}

    baselines = await r.hgetall(BASELINE_HASH) or {}
    fired = 0
    for t in tickers:
        k = _key(t)
        raw = baselines.get(k)
        if raw is None:
            continue
        try:
            baseline = float(raw)
        except (TypeError, ValueError):
            continue
        if baseline <= 0 or baseline > universe_ceiling:
            # No baseline yet, or already big enough to be in symbols:active.
            continue
        if t.turnover_24h < min_turnover:
            continue
        ratio = t.turnover_24h / baseline
        if ratio < ratio_threshold:
            continue

        cooldown_key = f"{COOLDOWN_PREFIX}:{t.exchange}:{t.symbol}"
        if await r.exists(cooldown_key):
            continue

        now = datetime.now(timezone.utc)
        payload = {
            "type": "awakening_detected",
            "exchange": t.exchange,
            "symbol": t.symbol,
            "current_turnover_usd": round(t.turnover_24h, 2),
            "baseline_turnover_usd": round(baseline, 2),
            "ratio": round(ratio, 2),
            "price_change_pct": round(t.price_change_pct, 2),
            "detected_at": now.isoformat(),
        }
        await redis_service.publish_alert("awakening", payload)
        await r.sadd(f"{FORCE_SUB_PREFIX}:{t.exchange}", t.symbol)
        await r.expire(f"{FORCE_SUB_PREFIX}:{t.exchange}", FORCE_SUB_TTL)
        await r.setex(cooldown_key, COOLDOWN_SECONDS, "1")
        await r.lpush(RECENT_LIST, json.dumps(payload))
        await r.ltrim(RECENT_LIST, 0, RECENT_CAP - 1)
        fired += 1

    log.info("awakening_tick", fetched=len(tickers), fired=fired, baselines=len(baselines))
    return {"fetched": len(tickers), "fired": fired}


async def run_awakening_baseline_refresh() -> dict[str, int]:
    """Push today's snapshot into per-symbol history, recompute medians."""
    if not getattr(app_settings, "awakening_enabled", False):
        return {"skipped": 1}

    r = redis_service.get_redis()
    tickers = await fetch_all_tickers()
    if not tickers:
        log.warning("awakening_baseline_no_tickers")
        return {"fetched": 0}

    new_baseline: dict[str, str] = {}
    for t in tickers:
        k = _key(t)
        hist_key = f"{HIST_PREFIX}:{k}"
        await r.lpush(hist_key, str(t.turnover_24h))
        await r.ltrim(hist_key, 0, HISTORY_DAYS - 1)
        # 30-day TTL so symbols that delist eventually drop out.
        await r.expire(hist_key, 30 * 24 * 3600)
        raw_hist = await r.lrange(hist_key, 0, HISTORY_DAYS - 1) or []
        values: list[float] = []
        for v in raw_hist:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
        if values:
            new_baseline[k] = str(statistics.median(values))

    if new_baseline:
        # Rewrite the hash atomically: delete + hset in a pipeline so stale
        # entries for delisted symbols don't linger.
        async with r.pipeline(transaction=True) as pipe:
            pipe.delete(BASELINE_HASH)
            pipe.hset(BASELINE_HASH, mapping=new_baseline)
            await pipe.execute()

    log.info("awakening_baseline_refresh", fetched=len(tickers), baselined=len(new_baseline))
    return {"fetched": len(tickers), "baselined": len(new_baseline)}
