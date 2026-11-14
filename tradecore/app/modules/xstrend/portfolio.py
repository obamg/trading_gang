"""XsTrend — the weekly job: settle the week that ended, open the new basket.

Every input is a closed public Bybit daily bar or a settled funding rate, so
the job is idempotent and a missed week is rebuilt later with the same result.
Paper only: it writes rows, never orders.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.xstrend import XsTrendWeek
from app.modules.xstrend import rules as R

_SEM = asyncio.Semaphore(8)


def _dt(t: int) -> datetime:
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc)


def week_of(now: datetime) -> int:
    """ms of the Monday 00:00 UTC at or before *now*."""
    day = int(now.timestamp() * 1000) // R.DAY * R.DAY
    return day - now.weekday() * R.DAY


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    # A failed request must fail the whole tick: a basket ranked on a partial
    # universe would be a different portfolio. The next hourly run retries.
    err: object = None
    for attempt in range(4):
        try:
            async with _SEM:
                r = await client.get(f"{settings.bybit_rest_url}{path}", params=params, timeout=20)
            d = r.json()
            if d.get("retCode") == 0:
                return d["result"]
            err = d.get("retMsg")
        except Exception as e:
            err = e
        await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"bybit {path} {params.get('symbol', '')}: {err}")


async def instruments(client: httpx.AsyncClient) -> list[str]:
    """USDT linear perps, listed and delisted (a coin delisted mid-week still has to settle)."""
    out = []
    for status in ("Trading", "Closed"):
        cur = ""
        while True:
            q = {"category": "linear", "limit": 1000, "status": status, **({"cursor": cur} if cur else {})}
            r = await _get(client, "/v5/market/instruments-info", q)
            out += [i["symbol"] for i in r["list"]
                    if i.get("quoteCoin") == "USDT" and i.get("contractType") == "LinearPerpetual"
                    and i.get("baseCoin") not in R.STABLE]
            cur = r.get("nextPageCursor") or ""
            if not cur or not r["list"]:
                break
    return out


async def daily(client: httpx.AsyncClient, sym: str, start: int, end: int) -> dict:
    """{day_ms: (open, close, turnover)} for daily bars opening in [start, end]. Max 200 bars."""
    lst = (await _get(client, "/v5/market/kline", {
        "category": "linear", "symbol": sym, "interval": "D", "start": start, "end": end, "limit": 200,
    }))["list"]
    return {int(k[0]): (float(k[1]), float(k[4]), float(k[6])) for k in lst}


async def funding_sum(client: httpx.AsyncClient, sym: str, lo: int, hi: int) -> float:
    # ponytail: one page of 200. A week of hourly funding is 168 settlements; paginate if
    # Bybit ever settles more often than that.
    lst = (await _get(client, "/v5/market/funding/history", {
        "category": "linear", "symbol": sym, "startTime": lo, "endTime": hi - 1, "limit": 200,
    }))["list"]
    return sum(float(k["fundingRate"]) for k in lst if lo <= int(k["fundingRateTimestamp"]) < hi)


async def build(client: httpx.AsyncClient, t: int) -> tuple[list[str], dict[str, float]] | None:
    """(universe, weights) for the week starting at t, or None if fewer than 30 coins qualify."""
    syms = await instruments(client)
    got = await asyncio.gather(*(daily(client, s, t - 120 * R.DAY, t - 1) for s in syms))
    picked = R.pick({s: b for s, b in zip(syms, got) if b}, t)
    if not picked:
        return None
    uni, longs, shorts = picked
    return uni, R.weights(longs, shorts)


async def result(client: httpx.AsyncClient, row: XsTrendWeek, t: int) -> dict:
    syms = sorted(set(row.weights) | set(row.universe))
    got = await asyncio.gather(*(daily(client, s, t, t + R.WEEK) for s in syms))
    held = sorted(row.weights)
    fund = dict(zip(held, await asyncio.gather(*(funding_sum(client, s, t, t + R.WEEK) for s in held))))
    res = R.settle(row.weights, row.universe, dict(zip(syms, got)), fund, t, float(row.turnover))
    res["fund_by_symbol"] = fund
    return res


async def run_xstrend_tick() -> dict[str, int]:
    if not settings.xstrend_enabled:
        return {"skipped": 1}
    try:
        return await _tick(datetime.now(timezone.utc))
    except Exception as e:  # never take the scheduler down
        log.error("xstrend_tick_failed", err=str(e))
        return {"failed": 1}


async def _tick(now: datetime) -> dict[str, int]:
    done = {"opened": 0, "settled": 0}
    now_ms, cur = int(now.timestamp() * 1000), week_of(now)
    async with AsyncSessionLocal() as db, httpx.AsyncClient() as client:
        rows = (await db.execute(select(XsTrendWeek).where(XsTrendWeek.rules == R.VERSION))).scalars().all()
        by_t = {int(r.week_start.timestamp() * 1000): r for r in rows}
        t = R.FIRST_WEEK
        while t <= cur:  # oldest first: a week's cost needs the previous week settled
            row = by_t.get(t)
            if row is None:
                prev = by_t.get(t - R.WEEK)
                drifted = prev.detail.get("drifted", {}) if prev and prev.status == "settled" else {}
                got = await build(client, t)
                row = XsTrendWeek(week_start=_dt(t), rules=R.VERSION, status="skipped", universe=[], weights={}, detail={})
                if got:
                    row.status, row.universe, row.weights = "open", got[0], got[1]
                    row.turnover = R.turnover(got[1], drifted)
                db.add(row)
                await db.commit()
                by_t[t] = row
                done["opened"] += 1
                log.info("xstrend_week_opened", week=row.week_start.isoformat(), status=row.status)
            if row.status == "open" and t + R.WEEK <= now_ms:
                res = await result(client, row, t)
                row.status, row.settled_at = "settled", now
                row.gross, row.cost, row.funding, row.net, row.mkt = (
                    res["gross"], res["cost"], res["funding"], res["net"], res["mkt"])
                row.detail = {"returns": res["returns"], "drifted": res["drifted"], "funding": res["fund_by_symbol"]}
                await db.commit()
                done["settled"] += 1
                log.info("xstrend_week_settled", week=row.week_start.isoformat(), net=round(res["net"], 5))
            t += R.WEEK
    return done
