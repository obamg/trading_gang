"""MacroPulse — Yahoo macro metrics, stablecoin mcap, daily score.

Uses Yahoo's public v8 chart API rather than the yfinance library so we don't
pull in pandas as a runtime dep. The chart API returns prior close + latest,
which is what we need for change %.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.macro import EconomicEvent, MacroSnapshot
from app.services import cmc_client, redis_service

YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
COINGECKO_STABLES = (
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=stablecoins&per_page=50&page=1"
)
# CMC has no stablecoin-category aggregate on the cheap endpoints, so the
# basket is explicit. These carry the overwhelming majority of stablecoin
# market cap; the tail moves the total by well under a percent and does not
# change the direction, which is what the score reads.
STABLE_SYMBOLS = (
    "USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "TUSD", "USDD", "USDS",
)
TICKERS = {
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "us2y": "^IRX",
    "vix": "^VIX",
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "gold": "GC=F",
}


async def _fetch_ticker(client: httpx.AsyncClient, ticker: str) -> dict | None:
    try:
        resp = await client.get(
            YF_CHART.format(ticker=ticker),
            headers={"User-Agent": "Mozilla/5.0 TradeCore/0.1"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("yf_fetch_failed", ticker=ticker, err=str(e))
        return None
    try:
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        price = float(meta.get("regularMarketPrice") or 0)
        prev_close = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
        return {
            "value": price,
            "previous_close": prev_close,
            "change_pct": round(change_pct, 4),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except (KeyError, TypeError, ValueError) as e:
        log.warning("yf_parse_failed", ticker=ticker, err=str(e))
        return None


async def collect_market_data() -> dict[str, dict]:
    """Fetch all macro tickers and cache in Redis with 2h TTL. Returns raw map."""
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for key, ticker in TICKERS.items():
            row = await _fetch_ticker(client, ticker)
            if row is None:
                continue
            out[key] = row
            r = redis_service.get_redis()
            await r.set(f"macro:{key}", json.dumps(row), ex=2 * 3600)
    log.info("macropulse_market_data", tickers=list(out.keys()))
    return out


async def _fetch_stablecoin_mcap(client: httpx.AsyncClient) -> float | None:
    """Total stablecoin market cap in USD. CMC first, CoinGecko fallback.

    The two sources are not identical: CoinGecko's ``category=stablecoins``
    sweeps ~50 coins, while STABLE_SYMBOLS is the majors. The number feeds a
    *trend* (is stablecoin supply expanding?), not an absolute level, so the
    smaller basket is fine — but mixing sources across consecutive snapshots
    would put a step in that trend, which is why CMC is primary and CG only
    fills a gap rather than alternating.
    """
    try:
        data = await cmc_client.quotes_latest(list(STABLE_SYMBOLS))
    except Exception as e:  # cmc_client soft-fails, but never trust that
        data = None
        log.warning("cmc_stables_failed", err=str(e))
    if data:
        total = 0.0
        for entry in data.values():
            # A symbol CMC maps to several listings comes back as a list.
            for coin in (entry if isinstance(entry, list) else [entry]):
                if not isinstance(coin, dict):
                    continue
                mcap = ((coin.get("quote") or {}).get("USD") or {}).get("market_cap")
                total += float(mcap or 0)
        if total > 0:
            return total

    try:
        resp = await client.get(COINGECKO_STABLES, timeout=15.0)
        resp.raise_for_status()
        return sum(float(c.get("market_cap") or 0) for c in resp.json())
    except Exception as e:
        log.warning("stables_failed", err=str(e))
        return None


def compute_macro_score(
    dxy_change: float | None,
    vix_value: float | None,
    us10y_change: float | None,
    sp500_change: float | None,
) -> tuple[int, dict]:
    """Score in [-100, 100] plus the breakdown for /macro/score."""
    score = 0
    breakdown: dict[str, int] = {}
    if dxy_change is not None:
        delta = -10 if dxy_change > 0 else 10 if dxy_change < 0 else 0
        score += delta
        breakdown["dxy"] = delta
    if vix_value is not None:
        delta = -15 if vix_value > 25 else 10 if vix_value < 15 else 0
        score += delta
        breakdown["vix"] = delta
    if us10y_change is not None:
        delta = -10 if us10y_change > 0 else 5 if us10y_change < 0 else 0
        score += delta
        breakdown["us10y"] = delta
    if sp500_change is not None:
        delta = 10 if sp500_change > 0 else -10 if sp500_change < 0 else 0
        score += delta
        breakdown["sp500"] = delta
    return max(-100, min(100, score)), breakdown


async def collect_daily_snapshot(db: AsyncSession) -> dict:
    market = await collect_market_data()
    async with httpx.AsyncClient(timeout=20.0) as client:
        stablecoin_mcap = await _fetch_stablecoin_mcap(client)
    dxy = market.get("dxy", {})
    vix = market.get("vix", {})
    us10y = market.get("us10y", {})
    sp500 = market.get("sp500", {})

    score, breakdown = compute_macro_score(
        dxy.get("change_pct"),
        vix.get("value"),
        us10y.get("change_pct"),
        sp500.get("change_pct"),
    )

    snapshot_at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    row_values = dict(
        dxy=_d(dxy.get("value")),
        us10y=_d(us10y.get("value")),
        us2y=_d(market.get("us2y", {}).get("value")),
        yield_spread=_spread(us10y.get("value"), market.get("us2y", {}).get("value")),
        vix=_d(vix.get("value")),
        sp500=_d(sp500.get("value")),
        nasdaq=_d(market.get("nasdaq", {}).get("value")),
        gold_usd=_d(market.get("gold", {}).get("value")),
        stablecoin_mcap_usd=_d(stablecoin_mcap),
        macro_score=score,
        snapshot_at=snapshot_at,
    )
    stmt = pg_insert(MacroSnapshot).values(**row_values).on_conflict_do_nothing(index_elements=["snapshot_at"])
    await db.execute(stmt)
    await db.commit()

    # Cache the score + breakdown for /macro/score
    r = redis_service.get_redis()
    await r.set(
        "macro:score",
        json.dumps(
            {
                "macro_score": score,
                "breakdown": breakdown,
                "snapshot_at": snapshot_at.isoformat(),
            }
        ),
        ex=4 * 3600,
    )
    log.info("macropulse_daily_snapshot", score=score, breakdown=breakdown)
    return {"macro_score": score, "breakdown": breakdown, "snapshot_at": snapshot_at.isoformat()}


def _d(v: float | None) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(round(float(v), 6)))
    except (ValueError, TypeError):
        return None


def _spread(a: float | None, b: float | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return _d(float(a) - float(b))


# Free, keyless; this week only (Sun→Sat, rolls over on Sunday). Ask for it
# politely and rarely — the 6h cadence is well inside the publisher's limits.
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_IMPACT = {"High": "high", "Medium": "medium"}


def parse_ff_events(rows: list) -> list[dict]:
    """US medium/high-impact rows from the ForexFactory feed, times in UTC."""
    out = []
    for ev in rows if isinstance(rows, list) else []:
        impact = FF_IMPACT.get(ev.get("impact"))
        if ev.get("country") != "USD" or impact is None:
            continue
        try:
            at = datetime.fromisoformat(ev["date"]).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "name": (ev.get("title") or "Unknown")[:200],
            "country": "US",
            "impact": impact,
            "scheduled_at": at,
            "forecast_value": (ev.get("forecast") or None),
            "previous_value": (ev.get("previous") or None),
        })
    return out


async def sync_economic_calendar(db: AsyncSession) -> int:
    """Insert this week's US events that aren't stored yet. Returns new rows."""
    try:
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "tradecore-calendar"}) as client:
            resp = await client.get(FF_CALENDAR_URL)
            resp.raise_for_status()
            events = parse_ff_events(resp.json())
    except Exception as e:
        log.warning("economic_calendar_failed", err=str(e))
        return 0
    if not events:
        log.warning("economic_calendar_empty")
        return 0

    # No unique constraint on the table, so dedupe on (name, time) here.
    times = [e["scheduled_at"] for e in events]
    existing = set(
        (await db.execute(
            select(EconomicEvent.name, EconomicEvent.scheduled_at)
            .where(EconomicEvent.scheduled_at.between(min(times), max(times)))
        )).all()
    )
    new = [e for e in events if (e["name"], e["scheduled_at"]) not in existing]
    db.add_all(EconomicEvent(**e) for e in new)
    await db.commit()
    log.info("economic_calendar_synced", fetched=len(events), new=len(new))
    return len(new)


# ---------- scheduler wrappers ----------


async def run_hourly_market_data() -> None:
    try:
        await collect_market_data()
    except Exception as e:
        log.error("macropulse_hourly_failed", err=str(e))


async def run_daily_snapshot() -> None:
    async with AsyncSessionLocal() as db:
        try:
            await collect_daily_snapshot(db)
        except Exception as e:
            log.error("macropulse_daily_failed", err=str(e))


async def run_calendar_sync() -> None:
    async with AsyncSessionLocal() as db:
        try:
            await sync_economic_calendar(db)
        except Exception as e:
            log.error("macropulse_calendar_failed", err=str(e))


__all__ = [
    "collect_market_data",
    "collect_daily_snapshot",
    "compute_macro_score",
    "sync_economic_calendar",
    "run_hourly_market_data",
    "run_daily_snapshot",
    "run_calendar_sync",
]
