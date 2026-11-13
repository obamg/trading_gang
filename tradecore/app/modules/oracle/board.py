"""Oracle Confluence Board — cross-module agreement leaderboard.

Inverts the per-symbol query model. Instead of asking "what does Oracle say
about BTCUSDT?", asks "across the top 50 perps, which symbols currently have
≥3 modules agreeing on direction with intensity > 0.3?"

Reuses ``oracle.engine.compute_live_score`` so the per-module signal logic
isn't duplicated. The whole snapshot is computed every 3 min by the
scheduler and cached in Redis under ``oracle:board`` (5 min TTL).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.oracle import OracleOutcome, OracleSignal
from app.modules.oracle.engine import compute_live_score
from app.services import redis_service

BOARD_CACHE_KEY = "oracle:board"
UNIVERSE_CACHE_KEY = "oracle:board:universe"
BOARD_TTL_SECONDS = 300              # 5 min — recompute every 3 min, expires before next stale
UNIVERSE_TTL_SECONDS = 300
TOP_N = 50
MIN_MODULES = 3
INTENSITY_FLOOR = 0.3                # per-module bar to count as "agreeing"
MAX_ENTRY_ALERTS_PER_TICK = 3        # a market-wide move puts many symbols on at once


async def _top_symbols_by_volume() -> list[str]:
    """Top-N USDT perps by 24h quoteVolume from Binance futures REST.

    Cached 5 min in Redis to avoid hammering the public endpoint when the
    scheduler ticks. List rank moves slowly — staleness is fine.
    """
    r = redis_service.get_redis()
    cached = await r.get(UNIVERSE_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except (ValueError, TypeError):
            pass

    url = f"{app_settings.binance_rest_url}/fapi/v1/ticker/24hr"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            tickers = resp.json()
    except Exception as e:
        log.warning("oracle_board_universe_fetch_failed", err=str(e))
        return []

    perps = [
        t for t in tickers
        if isinstance(t, dict) and (t.get("symbol") or "").endswith("USDT")
    ]
    perps.sort(key=lambda t: float(t.get("quoteVolume") or 0), reverse=True)
    symbols = [t["symbol"] for t in perps[:TOP_N]]
    if symbols:
        await r.set(UNIVERSE_CACHE_KEY, json.dumps(symbols), ex=UNIVERSE_TTL_SECONDS)
    return symbols


def _agreeing_modules(breakdown: dict, direction: str) -> list[dict]:
    """Filter breakdown to modules signaling ``direction`` with intensity floor."""
    return [
        {
            "name": name,
            "direction": sig["direction"],
            "intensity": sig["intensity"],
            "weight": sig.get("weight"),
            "contribution": sig.get("contribution"),
        }
        for name, sig in breakdown.items()
        if sig.get("direction") == direction and float(sig.get("intensity", 0)) > INTENSITY_FLOOR
    ]


async def compute_board() -> dict:
    """Compute and cache the full board snapshot.

    Returns ``{bullish, bearish, updated_at, universe_size, min_modules}``.
    Bullish list is sorted by score desc, bearish by score asc (most negative
    first). Both filtered to symbols with ≥3 agreeing modules in the same
    direction.
    """
    symbols = await _top_symbols_by_volume()
    bullish: list[dict] = []
    bearish: list[dict] = []

    if symbols:
        async with AsyncSessionLocal() as db:
            for sym in symbols:
                try:
                    live = await compute_live_score(db, sym)
                except Exception as e:
                    log.warning("oracle_board_score_failed", symbol=sym, err=str(e))
                    continue

                breakdown = live.get("signals_breakdown") or {}
                bullish_modules = _agreeing_modules(breakdown, "bullish")
                bearish_modules = _agreeing_modules(breakdown, "bearish")

                # Pick the dominant direction by count, then by abs(score).
                if len(bullish_modules) >= MIN_MODULES and len(bullish_modules) > len(bearish_modules):
                    direction = "bullish"
                    modules = bullish_modules
                elif len(bearish_modules) >= MIN_MODULES and len(bearish_modules) > len(bullish_modules):
                    direction = "bearish"
                    modules = bearish_modules
                else:
                    continue  # not enough confluence, or directions split

                row = {
                    "symbol": sym,
                    "score": int(live["score"]),
                    "recommendation": live["recommendation"],
                    "confidence": live.get("confidence"),
                    "confluence_count": live.get("confluence_count", 0),
                    "agreeing_count": len(modules),
                    "current_price": live.get("current_price"),
                    "modules": modules,
                }
                if direction == "bullish":
                    bullish.append(row)
                else:
                    bearish.append(row)

    bullish.sort(key=lambda r: r["score"], reverse=True)
    bearish.sort(key=lambda r: r["score"])

    snapshot = {
        "bullish": bullish,
        "bearish": bearish,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(symbols),
        "min_modules": MIN_MODULES,
        "intensity_floor": INTENSITY_FLOOR,
    }
    r = redis_service.get_redis()
    await r.set(BOARD_CACHE_KEY, json.dumps(snapshot, default=str), ex=BOARD_TTL_SECONDS)
    return snapshot


async def get_cached_board() -> dict | None:
    """Read the cached snapshot. None if no scheduler tick has run yet."""
    r = redis_service.get_redis()
    raw = await r.get(BOARD_CACHE_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def score_bucket(score: int) -> int:
    """|score| rounded down to the nearest 10 — the "signals like this" key."""
    return abs(int(score)) // 10 * 10


def rank_picks(snapshot: dict, buckets: dict[int, dict], limit: int) -> list[dict]:
    """Strongest board rows, both directions, each with its score bucket's record."""
    rows = [*snapshot.get("bullish", []), *snapshot.get("bearish", [])]
    rows.sort(key=lambda r: abs(int(r.get("score") or 0)), reverse=True)
    empty = {"n": 0, "hit_4h_pct": None, "avg_move_4h_pct": None}
    return [
        {**r, "similar": buckets.get(score_bucket(r.get("score") or 0), empty)}
        for r in rows[:limit]
    ]


def new_entries(prev: dict | None, snapshot: dict) -> list[dict]:
    """Rows whose (symbol, direction) was not on the previous board, strongest first.

    No previous board (cold start, or the cache expired after a missed tick)
    means nothing is "new" — otherwise a restart would page the whole board.
    """
    if prev is None:
        return []
    seen = {(r["symbol"], side) for side in ("bullish", "bearish") for r in prev.get(side, [])}
    rows = [
        {**r, "direction": side}
        for side in ("bullish", "bearish")
        for r in snapshot.get(side, [])
        if (r["symbol"], side) not in seen
    ]
    rows.sort(key=lambda r: abs(int(r.get("score") or 0)), reverse=True)
    return rows


async def _record_entry(row: dict) -> str | None:
    """Persist an alerted board entry so measure_outcomes follows it up.

    Stored as an OracleSignal with timeframe="board" (the feed that produced
    it); direction, not the live recommendation, decides long/short, since
    that is what the alert told the user.
    """
    price = row.get("current_price")
    if not price:
        return None  # nothing to measure against
    async with AsyncSessionLocal() as db:
        sig = OracleSignal(
            symbol=row["symbol"],
            score=int(row["score"]),
            recommendation="long" if row["direction"] == "bullish" else "short",
            confidence=row.get("confidence") or "medium",
            confluence_count=int(row["agreeing_count"]),
            signals_breakdown={m["name"]: m for m in row["modules"]},
            entry_price=Decimal(str(price)),
            timeframe="board",
            signal_at=datetime.now(timezone.utc),
            alerted=True,
        )
        db.add(sig)
        await db.flush()
        db.add(OracleOutcome(signal_id=sig.id, price_at_signal=Decimal(str(price))))
        await db.commit()
        return str(sig.id)


async def _alert_entries(prev: dict | None, snapshot: dict) -> None:
    sent = 0
    for row in new_entries(prev, snapshot):
        if sent >= MAX_ENTRY_ALERTS_PER_TICK:
            break
        sym = row["symbol"]
        if await redis_service.is_on_cooldown("oracle_board", sym):
            continue  # flapped off and back on the board — already told
        await redis_service.set_alert_cooldown("oracle_board", sym, app_settings.oracle_alert_cooldown_minutes)
        try:
            signal_id = await _record_entry(row)
        except Exception as e:  # an alert without a record beats no alert
            log.warning("oracle_board_entry_record_failed", symbol=sym, err=str(e))
            signal_id = None
        await redis_service.publish_alert("oracle", {
            "type": "board_entry",
            "id": signal_id,
            "symbol": sym,
            "direction": row["direction"],
            "score": row["score"],
            "agreeing_count": row["agreeing_count"],
            "agreeing_modules": [m["name"] for m in row["modules"]],
            "current_price": row.get("current_price"),
        })
        sent += 1
        log.info("oracle_board_entry_published", symbol=sym, direction=row["direction"], score=row["score"])


async def run_board_job() -> None:
    """Scheduler entrypoint. Swallows + logs exceptions per project convention."""
    try:
        prev = await get_cached_board()
        snapshot = await compute_board()
        await _alert_entries(prev, snapshot)
        log.info(
            "oracle_board_tick",
            bullish=len(snapshot["bullish"]),
            bearish=len(snapshot["bearish"]),
            universe=snapshot["universe_size"],
        )
    except Exception as e:
        log.error("oracle_board_tick_failed", err=str(e))


__all__ = [
    "compute_board",
    "get_cached_board",
    "new_entries",
    "rank_picks",
    "run_board_job",
    "score_bucket",
    "BOARD_CACHE_KEY",
    "MIN_MODULES",
    "INTENSITY_FLOOR",
    "TOP_N",
]
