"""MacroPulse — Yahoo macro metrics, ETF flows, stablecoin mcap, daily score.

Uses Yahoo's public v8 chart API rather than the yfinance library so we don't
pull in pandas as a runtime dep. The chart API returns prior close + latest,
which is what we need for change %.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.macro import EconomicEvent, MacroSnapshot
from app.services import redis_service

YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
COINGECKO_STABLES = (
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=stablecoins&per_page=50&page=1"
)
COINGLASS_ETF = "https://open-api.coinglass.com/public/v2/indicator/bitcoin_etf"

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


async def _fetch_etf_flows(client: httpx.AsyncClient, api_key: str | None) -> float | None:
    headers = {"coinglassSecret": api_key} if api_key else {}
    try:
        resp = await client.get(COINGLASS_ETF, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("etf_flows_failed", err=str(e))
        return None
    # The endpoint returns an array of daily rows; last entry is most recent.
    try:
        rows = (data.get("data") or {}).get("list") or data.get("data") or []
        if not rows:
            return None
        latest = rows[-1] if isinstance(rows, list) else None
        if not latest:
            return None
        return float(latest.get("flowUsd") or latest.get("changeUsd") or 0)
    except (KeyError, ValueError, AttributeError):
        return None


async def _fetch_stablecoin_mcap(client: httpx.AsyncClient) -> float | None:
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
    etf_flows: float | None,
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
    if etf_flows is not None:
        delta = 15 if etf_flows > 0 else -15 if etf_flows < 0 else 0
        score += delta
        breakdown["etf_flows"] = delta
    if sp500_change is not None:
        delta = 10 if sp500_change > 0 else -10 if sp500_change < 0 else 0
        score += delta
        breakdown["sp500"] = delta
    return max(-100, min(100, score)), breakdown


async def collect_daily_snapshot(db: AsyncSession, api_key: str | None = None) -> dict:
    market = await collect_market_data()
    async with httpx.AsyncClient(timeout=20.0) as client:
        etf_flows = await _fetch_etf_flows(client, api_key)
        stablecoin_mcap = await _fetch_stablecoin_mcap(client)
    dxy = market.get("dxy", {})
    vix = market.get("vix", {})
    us10y = market.get("us10y", {})
    sp500 = market.get("sp500", {})

    score, breakdown = compute_macro_score(
        dxy.get("change_pct"),
        vix.get("value"),
        us10y.get("change_pct"),
        etf_flows,
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
        btc_etf_flows_usd=_d(etf_flows),
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
    log.info("macropulse_daily_snapshot", score=score, breakdown=breakdown, etf=etf_flows)
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


async def sync_economic_calendar(db: AsyncSession, api_key: str | None = None) -> int:
    """Sync upcoming events. TradingEconomics requires an API key; gracefully no-op without it."""
    if not api_key:
        log.info("economic_calendar_skipped_no_key")
        return 0
    url = "https://api.tradingeconomics.com/calendar"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params={"c": api_key, "format": "json"})
            resp.raise_for_status()
            rows = resp.json()
    except Exception as e:
        log.warning("economic_calendar_failed", err=str(e))
        return 0

    count = 0
    for ev in rows:
        scheduled = ev.get("Date")
        if not scheduled:
            continue
        try:
            scheduled_at = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
        except ValueError:
            continue
        if scheduled_at < datetime.now(timezone.utc) - timedelta(days=1):
            continue
        impact_raw = int(ev.get("Importance", 1))
        impact = {3: "high", 2: "medium", 1: "low"}.get(impact_raw, "low")
        stmt = pg_insert(EconomicEvent).values(
            name=ev.get("Event", "Unknown"),
            category=ev.get("Category"),
            country=(ev.get("Country") or "US")[:10],
            impact=impact,
            scheduled_at=scheduled_at,
            forecast_value=str(ev.get("Forecast") or "") or None,
            previous_value=str(ev.get("Previous") or "") or None,
        ).on_conflict_do_nothing()
        await db.execute(stmt)
        count += 1
    await db.commit()
    log.info("economic_calendar_synced", count=count)
    return count


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
            from app.config import settings as _s
            await sync_economic_calendar(db, getattr(_s, "trading_economics_api_key", None))
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
