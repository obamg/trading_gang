"""SentimentPulse — funding/OI/long-short snapshots + fast funding z-score & basis."""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.sentiment import MarketSentimentSnapshot, SentimentSnapshot
from app.services import redis_service

FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"  # returns lastFundingRate per symbol
LS_RATIO_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
CG_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
TICKER_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

EXTREME_FUNDING_ABS = 0.0005  # 0.05% per 8h
EXTREME_LS_PCT = 70.0
TOP_SYMBOLS = 50

# Fast funding/basis tracker (5-min cadence)
FUNDING_FAST_HISTORY_TTL = 25 * 3600
FUNDING_FAST_SHORT_MIN = 240            # 4h trailing window
FUNDING_FAST_LONG_MIN = 24 * 60         # 24h baseline
FUNDING_FAST_MIN_SAMPLES = 8


async def _top_symbols_by_volume(client: httpx.AsyncClient, limit: int = TOP_SYMBOLS) -> list[str]:
    """Rank USDT perpetuals by 24h quote volume."""
    try:
        r = await client.get(TICKER_24H_URL)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        log.warning("sentiment_top_symbols_failed", err=str(e))
        return []
    ranked = sorted(
        (row for row in rows if isinstance(row, dict) and row.get("symbol", "").endswith("USDT")),
        key=lambda row: float(row.get("quoteVolume") or 0),
        reverse=True,
    )
    return [r["symbol"] for r in ranked[:limit]]


async def collect_per_symbol(db: AsyncSession) -> int:
    """Collect funding/OI/long-short for top N symbols. Returns count written."""
    snapshot_at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    written = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        symbols = await _top_symbols_by_volume(client)
        # Funding rates — single call, filter to our symbols
        funding_by_sym: dict[str, float] = {}
        try:
            r = await client.get(FUNDING_RATE_URL)
            r.raise_for_status()
            for row in r.json():
                funding_by_sym[row["symbol"]] = float(row.get("lastFundingRate") or 0)
        except Exception as e:
            log.warning("sentiment_funding_fetch_failed", err=str(e))

        for symbol in symbols:
            funding = funding_by_sym.get(symbol)
            if funding is not None:
                await redis_service.set_funding_rate(symbol, funding)

            long_ratio: float | None = None
            short_ratio: float | None = None
            try:
                r = await client.get(
                    LS_RATIO_URL, params={"symbol": symbol, "period": "1h", "limit": 1}
                )
                r.raise_for_status()
                data = r.json()
                if data:
                    long_ratio = float(data[0].get("longAccount") or 0) * 100
                    short_ratio = float(data[0].get("shortAccount") or 0) * 100
            except Exception as e:
                log.debug("sentiment_ls_failed", symbol=symbol, err=str(e))

            oi_usd: float | None = None
            try:
                r = await client.get(OI_URL, params={"symbol": symbol})
                r.raise_for_status()
                oi_contracts = float(r.json().get("openInterest") or 0)
                latest = await redis_service.get_latest_candle(symbol)
                price = float(latest.get("close", 0)) if latest else 0.0
                oi_usd = oi_contracts * price
            except Exception as e:
                log.debug("sentiment_oi_failed", symbol=symbol, err=str(e))

            stmt = pg_insert(SentimentSnapshot).values(
                symbol=symbol,
                funding_rate=Decimal(str(funding)) if funding is not None else None,
                long_ratio=Decimal(str(round(long_ratio, 2))) if long_ratio is not None else None,
                short_ratio=Decimal(str(round(short_ratio, 2))) if short_ratio is not None else None,
                open_interest_usd=Decimal(str(round(oi_usd, 2))) if oi_usd is not None else None,
                snapshot_at=snapshot_at,
            ).on_conflict_do_nothing(index_elements=["symbol", "snapshot_at"])
            await db.execute(stmt)
            written += 1

            # Extreme alerts
            if funding is not None and abs(funding) > EXTREME_FUNDING_ABS:
                await redis_service.publish_alert(
                    "sentiment",
                    {
                        "module": "sentimentpulse",
                        "type": "extreme_funding",
                        "symbol": symbol,
                        "funding_rate": funding,
                        "detected_at": snapshot_at.isoformat(),
                    },
                )
            if long_ratio is not None and (long_ratio >= EXTREME_LS_PCT or short_ratio is not None and short_ratio >= EXTREME_LS_PCT):
                side = "long" if long_ratio >= EXTREME_LS_PCT else "short"
                await redis_service.publish_alert(
                    "sentiment",
                    {
                        "module": "sentimentpulse",
                        "type": "crowded_positioning",
                        "symbol": symbol,
                        "side": side,
                        "long_ratio": long_ratio,
                        "short_ratio": short_ratio,
                        "detected_at": snapshot_at.isoformat(),
                    },
                )
    await db.commit()
    log.info("sentimentpulse_per_symbol_snapshot", count=written)
    return written


async def collect_market(db: AsyncSession) -> dict | None:
    snapshot_at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    fg_index: int | None = None
    fg_label: str | None = None
    btc_dominance: float | None = None
    total_mcap: float | None = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(FEAR_GREED_URL)
            r.raise_for_status()
            data = r.json().get("data") or []
            if data:
                fg_index = int(data[0].get("value"))
                fg_label = data[0].get("value_classification")
        except Exception as e:
            log.warning("fear_greed_failed", err=str(e))

        try:
            r = await client.get(CG_GLOBAL_URL)
            r.raise_for_status()
            data = r.json().get("data") or {}
            btc_dominance = float((data.get("market_cap_percentage") or {}).get("btc") or 0) or None
            total_mcap = float((data.get("total_market_cap") or {}).get("usd") or 0) or None
        except Exception as e:
            log.warning("coingecko_global_failed", err=str(e))

    stmt = pg_insert(MarketSentimentSnapshot).values(
        fear_greed_index=fg_index,
        fear_greed_label=fg_label,
        btc_dominance_pct=Decimal(str(round(btc_dominance, 2))) if btc_dominance is not None else None,
        total_mcap_usd=Decimal(str(round(total_mcap, 2))) if total_mcap is not None else None,
        snapshot_at=snapshot_at,
    ).on_conflict_do_nothing(index_elements=["snapshot_at"])
    await db.execute(stmt)
    await db.commit()
    # Cache in Redis for fast /overview
    r = redis_service.get_redis()
    cached = {
        "fear_greed_index": fg_index,
        "fear_greed_label": fg_label,
        "btc_dominance_pct": btc_dominance,
        "total_mcap_usd": total_mcap,
        "snapshot_at": snapshot_at.isoformat(),
    }
    import json
    await r.set("sentiment:market:latest", json.dumps(cached), ex=2 * 3600)
    log.info("sentimentpulse_market_snapshot", **{k: v for k, v in cached.items() if k != "snapshot_at"})
    return cached


async def run_hourly_collection() -> None:
    async with AsyncSessionLocal() as db:
        try:
            await collect_per_symbol(db)
        except Exception as e:
            log.error("sentimentpulse_per_symbol_failed", err=str(e))
        try:
            await collect_market(db)
        except Exception as e:
            log.error("sentimentpulse_market_failed", err=str(e))


# ---------- fast funding + basis ----------


async def collect_funding_fast() -> int:
    """5-min poll: funding rate + perp-spot basis with rolling z-scores.

    Stores raw samples in ``funding_history:{symbol}`` (sorted set, score=ms,
    member="ms:funding:basis"), and the latest computed signal in
    ``funding_fast:{symbol}`` (TTL 15m) for oracle to consume.
    """
    r = redis_service.get_redis()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    long_cut_ms = now_ms - FUNDING_FAST_LONG_MIN * 60 * 1000
    short_cut_ms = now_ms - FUNDING_FAST_SHORT_MIN * 60 * 1000

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(FUNDING_RATE_URL)
            resp.raise_for_status()
            rows = resp.json()
    except Exception as e:
        log.warning("funding_fast_fetch_failed", err=str(e))
        return 0

    if not isinstance(rows, list):
        return 0

    active = set(await redis_service.get_symbol_list())
    written = 0

    for row in rows:
        sym = row.get("symbol") if isinstance(row, dict) else None
        if not sym or not sym.endswith("USDT"):
            continue
        if active and sym not in active:
            continue
        try:
            funding = float(row.get("lastFundingRate", 0) or 0)
            mark = float(row.get("markPrice", 0) or 0)
            index = float(row.get("indexPrice", 0) or 0)
        except (TypeError, ValueError):
            continue
        if mark <= 0 or index <= 0:
            continue
        basis_pct = (mark - index) / index * 100

        history_key = f"funding_history:{sym}"
        member = f"{now_ms}:{funding:.10g}:{basis_pct:.6f}"
        await r.zadd(history_key, {member: now_ms})
        await r.zremrangebyscore(history_key, 0, long_cut_ms - 1)
        await r.expire(history_key, FUNDING_FAST_HISTORY_TTL)

        raw_samples = await r.zrange(history_key, 0, -1, withscores=True)
        funding_long: list[float] = []
        funding_short: list[float] = []
        basis_long: list[float] = []
        for sample, score in raw_samples:
            parts = sample.split(":")
            if len(parts) < 3:
                continue
            try:
                f_v = float(parts[1])
                b_v = float(parts[2])
                ts = float(score)
            except (TypeError, ValueError):
                continue
            if ts >= long_cut_ms:
                funding_long.append(f_v)
                basis_long.append(b_v)
            if ts >= short_cut_ms:
                funding_short.append(f_v)

        funding_z_24h: float | None = None
        funding_z_4h: float | None = None
        basis_z_24h: float | None = None
        if len(funding_long) >= FUNDING_FAST_MIN_SAMPLES:
            mean_l = statistics.fmean(funding_long)
            try:
                std_l = statistics.pstdev(funding_long)
            except statistics.StatisticsError:
                std_l = 0.0
            if std_l > 0:
                funding_z_24h = (funding - mean_l) / std_l
                if len(funding_short) >= 4:
                    mean_s = statistics.fmean(funding_short)
                    funding_z_4h = (mean_s - mean_l) / std_l
            if basis_long:
                mean_b = statistics.fmean(basis_long)
                try:
                    std_b = statistics.pstdev(basis_long)
                except statistics.StatisticsError:
                    std_b = 0.0
                if std_b > 0:
                    basis_z_24h = (basis_pct - mean_b) / std_b

        payload = {
            "symbol": sym,
            "funding_rate": funding,
            "basis_pct": round(basis_pct, 4),
            "funding_z_4h": round(funding_z_4h, 2) if funding_z_4h is not None else None,
            "funding_z_24h": round(funding_z_24h, 2) if funding_z_24h is not None else None,
            "basis_z_24h": round(basis_z_24h, 2) if basis_z_24h is not None else None,
            "samples": len(funding_long),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await r.set(f"funding_fast:{sym}", json.dumps(payload), ex=900)
        await redis_service.set_funding_rate(sym, funding)
        written += 1

    if written:
        log.info("funding_fast_collected", count=written)
    return written


async def get_funding_fast(symbol: str) -> dict | None:
    r = redis_service.get_redis()
    raw = await r.get(f"funding_fast:{symbol.upper()}")
    return json.loads(raw) if raw else None


async def run_funding_fast() -> None:
    try:
        await collect_funding_fast()
    except Exception as e:
        log.error("funding_fast_failed", err=str(e))


_ = settings
__all__ = [
    "collect_per_symbol",
    "collect_market",
    "collect_funding_fast",
    "get_funding_fast",
    "run_hourly_collection",
    "run_funding_fast",
]
