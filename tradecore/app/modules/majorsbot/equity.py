"""Paper-equity bookkeeping for MajorsBot, in Redis — mirrors bot/equity.py.

  majorsbot:equity:paper       current paper balance (closed PnL only)
  majorsbot:concurrent_count   int — open positions across BOTH strategies

Separate keys from WaveBot on purpose: the two bots must never share a ledger.
No kill switch / daily anchor here — the majors book is slow (hourly bars) and
risk is bounded by majorsbot_max_concurrent × majorsbot_risk_per_trade_pct.
"""
from __future__ import annotations

from decimal import Decimal

from app.config import settings as app_settings
from app.services import redis_service

EQUITY_KEY = "majorsbot:equity:paper"
CONCURRENT_KEY = "majorsbot:concurrent_count"


async def get_paper_equity() -> Decimal:
    r = redis_service.get_redis()
    raw = await r.get(EQUITY_KEY)
    if raw is None:
        initial = Decimal(str(app_settings.majorsbot_paper_equity_initial))
        await r.set(EQUITY_KEY, str(initial))
        return initial
    return Decimal(str(_decode(raw)))


async def add_to_equity(delta: Decimal) -> Decimal:
    """Apply realized PnL to paper equity. Returns the new balance."""
    current = await get_paper_equity()
    new_equity = current + delta
    r = redis_service.get_redis()
    await r.set(EQUITY_KEY, str(new_equity))
    return new_equity


async def reset_paper_equity() -> Decimal:
    """Dev convenience — reset to MAJORSBOT_PAPER_EQUITY_INITIAL."""
    initial = Decimal(str(app_settings.majorsbot_paper_equity_initial))
    r = redis_service.get_redis()
    await r.set(EQUITY_KEY, str(initial))
    return initial


async def get_concurrent_count() -> int:
    r = redis_service.get_redis()
    raw = await r.get(CONCURRENT_KEY)
    try:
        return int(_decode(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


async def increment_concurrent() -> int:
    r = redis_service.get_redis()
    return int(await r.incr(CONCURRENT_KEY))


async def decrement_concurrent() -> int:
    r = redis_service.get_redis()
    new_val = int(await r.decr(CONCURRENT_KEY))
    if new_val < 0:
        await r.set(CONCURRENT_KEY, "0")
        return 0
    return new_val


async def reconcile_concurrent(actual_open: int) -> int:
    """Overwrite the counter with DB truth (count of open majorsbot_trades).
    Returns the drift (redis − actual) for logging. Same rationale as WaveBot:
    a stuck-high counter blocks entries forever."""
    current = await get_concurrent_count()
    drift = current - actual_open
    if drift != 0:
        r = redis_service.get_redis()
        await r.set(CONCURRENT_KEY, str(max(0, actual_open)))
    return drift


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v
