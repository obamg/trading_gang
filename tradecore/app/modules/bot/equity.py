"""Paper-equity bookkeeping for WaveBot, in Redis.

Sources of truth:
  bot:equity:paper             current paper balance (closed PnL only — open
                               positions don't affect this until they close)
  bot:concurrent_count         int — open position counter
  bot:daily_anchor:{YYYY-MM-DD} equity at 00:00 UTC, snapshotted on first read
                               or by the daily reset job
  bot:kill_switch:{YYYY-MM-DD}  "1" if the daily-drawdown cap tripped today

The kill switch only blocks NEW entries — open positions keep running until
their own stops hit. That's deliberate: forcing exits in a drawdown often
makes drawdowns worse.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.config import settings as app_settings
from app.services import redis_service

EQUITY_KEY = "bot:equity:paper"
CONCURRENT_KEY = "bot:concurrent_count"
DAILY_ANCHOR_KEY = "bot:daily_anchor:{date}"
KILL_SWITCH_KEY = "bot:kill_switch:{date}"

# 48h TTL on date-keyed values — survives clock skew at the day boundary
# and means yesterday's keys auto-evict.
_DAY_TTL_SECONDS = 48 * 3600


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def get_paper_equity() -> Decimal:
    r = redis_service.get_redis()
    raw = await r.get(EQUITY_KEY)
    if raw is None:
        initial = Decimal(str(app_settings.bot_paper_equity_initial))
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
    """Dev convenience — reset to BOT_PAPER_EQUITY_INITIAL."""
    initial = Decimal(str(app_settings.bot_paper_equity_initial))
    r = redis_service.get_redis()
    await r.set(EQUITY_KEY, str(initial))
    return initial


async def get_daily_anchor() -> Decimal:
    """Equity at the start of the UTC day. Lazy-creates if not set."""
    r = redis_service.get_redis()
    key = DAILY_ANCHOR_KEY.format(date=_today_iso())
    raw = await r.get(key)
    if raw is None:
        eq = await get_paper_equity()
        await r.set(key, str(eq), ex=_DAY_TTL_SECONDS)
        return eq
    return Decimal(str(_decode(raw)))


async def reset_daily_anchor() -> Decimal:
    """Force a new daily anchor — called by the scheduler cron at 00:00 UTC."""
    eq = await get_paper_equity()
    r = redis_service.get_redis()
    key = DAILY_ANCHOR_KEY.format(date=_today_iso())
    await r.set(key, str(eq), ex=_DAY_TTL_SECONDS)
    return eq


async def is_kill_switch_tripped() -> bool:
    r = redis_service.get_redis()
    return bool(await r.exists(KILL_SWITCH_KEY.format(date=_today_iso())))


async def trip_kill_switch() -> None:
    r = redis_service.get_redis()
    await r.set(KILL_SWITCH_KEY.format(date=_today_iso()), "1", ex=_DAY_TTL_SECONDS)


async def check_and_maybe_trip_kill_switch() -> bool:
    """Compare equity vs daily anchor; trip if equity ≤ anchor × (1 − cap).

    Returns True if the switch is now tripped (either was already, or just got).
    Called after every close and at the top of every alert evaluation.
    """
    if await is_kill_switch_tripped():
        return True
    anchor = await get_daily_anchor()
    equity = await get_paper_equity()
    cap = Decimal(str(app_settings.bot_daily_drawdown_cap_pct))
    threshold = anchor * (Decimal("1") - cap)
    if equity <= threshold:
        await trip_kill_switch()
        return True
    return False


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


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v
