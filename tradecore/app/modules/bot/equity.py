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


STOPS_LIST_KEY = "bot:recent_stops"
THROTTLE_KEY = "bot:throttle"


async def record_stop_close() -> None:
    """Log a stop-out timestamp and trip the loss throttle if N stops landed
    inside the window. Throttled = risk_per_trade is multiplied by
    bot_loss_throttle_factor until the cooldown key expires."""
    n_trip = int(getattr(app_settings, "bot_loss_throttle_stops", 0) or 0)
    r = redis_service.get_redis()
    now = datetime.now(timezone.utc)
    await r.lpush(STOPS_LIST_KEY, now.isoformat())
    await r.ltrim(STOPS_LIST_KEY, 0, 49)
    if n_trip <= 0:
        return
    window_h = float(app_settings.bot_loss_throttle_window_hours)
    cutoff = now.timestamp() - window_h * 3600
    recent = 0
    for raw in await r.lrange(STOPS_LIST_KEY, 0, n_trip - 1):
        try:
            if datetime.fromisoformat(_decode(raw)).timestamp() >= cutoff:
                recent += 1
        except ValueError:
            continue
    if recent >= n_trip:
        cooldown_s = int(float(app_settings.bot_loss_throttle_cooldown_hours) * 3600)
        await r.set(THROTTLE_KEY, "1", ex=max(60, cooldown_s))


async def throttle_factor() -> Decimal:
    """Risk multiplier for new entries — 1 normally, the configured factor
    while the loss throttle is tripped."""
    if int(getattr(app_settings, "bot_loss_throttle_stops", 0) or 0) <= 0:
        return Decimal("1")
    r = redis_service.get_redis()
    if await r.exists(THROTTLE_KEY):
        return Decimal(str(app_settings.bot_loss_throttle_factor))
    return Decimal("1")


async def get_concurrent_count() -> int:
    r = redis_service.get_redis()
    raw = await r.get(CONCURRENT_KEY)
    try:
        return int(_decode(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


async def reconcile_concurrent(actual_open: int) -> int:
    """Overwrite the counter with DB truth (count of open bot_trades rows).

    The incr/decr pair can drift if a process dies between the DB commit and
    the counter update; a stuck-high counter blocks entries forever. Called
    every monitor tick. Returns the drift (redis − actual) for logging.
    """
    current = await get_concurrent_count()
    drift = current - actual_open
    if drift != 0:
        r = redis_service.get_redis()
        await r.set(CONCURRENT_KEY, str(max(0, actual_open)))
    return drift


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
