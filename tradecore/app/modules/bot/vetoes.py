"""WaveBot — alert vetoes.

The listener runs these in order, fail-fast. First one to return a SkipReason
shortcuts the rest. Cheap checks first (config, Redis flags), expensive checks
last (Oracle live score, DB lookups).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.models.bot import BotTrade
from app.models.news import NewsArticle
from app.modules.bot import equity
from app.modules.bot.schemas import Direction, SkipReason
from app.modules.oracle.engine import compute_live_score
from app.services import redis_service

COOLDOWN_KEY = "bot:cooldown:{symbol}"

# Bybit uses the in-process WS candle stream; Binance is monitored via REST polls
# from the bot monitor (see monitor.py:_latest_candle).
_SUPPORTED_EXCHANGES = {"bybit", "binance"}


def supports_exchange(exchange: str) -> bool:
    return exchange.lower() in _SUPPORTED_EXCHANGES


def blocklist() -> set[str]:
    """Parse the comma-separated BOT_SYMBOL_BLOCKLIST into an upper-cased set."""
    raw = getattr(app_settings, "bot_symbol_blocklist", "") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def is_blocked(symbol: str) -> bool:
    return symbol.upper() in blocklist()


def direction_enabled(direction: Direction) -> bool:
    """Per-direction gate. Both default on; flip via env when fresh data shows a
    side is negative-EV. Config, not code — the June/July samples flipped sign."""
    if direction == Direction.LONG:
        return bool(getattr(app_settings, "bot_long_enabled", True))
    return bool(getattr(app_settings, "bot_short_enabled", True))


def market_allowed(market_type: str | None) -> bool:
    """False when perp-only mode is on and this isn't a perp. Spot can't be
    shorted live, so its (paper-only) short edge is fiction — see asset analysis."""
    if not getattr(app_settings, "bot_perp_only", False):
        return True
    return (market_type or "").lower() == "perp"


async def already_open(db: AsyncSession, symbol: str) -> bool:
    res = await db.execute(
        select(BotTrade.id)
        .where(BotTrade.symbol == symbol, BotTrade.status == "open")
        .limit(1)
    )
    return res.scalar_one_or_none() is not None


async def on_cooldown(symbol: str) -> bool:
    r = redis_service.get_redis()
    return bool(await r.exists(COOLDOWN_KEY.format(symbol=symbol)))


async def set_cooldown(symbol: str) -> None:
    r = redis_service.get_redis()
    minutes = int(app_settings.bot_per_symbol_cooldown_minutes)
    await r.set(COOLDOWN_KEY.format(symbol=symbol), "1", ex=max(60, minutes * 60))


async def oracle_veto(
    db: AsyncSession, symbol: str, direction: Direction
) -> tuple[bool, float | None]:
    """Returns (should_veto, oracle_score). Errors degrade to no-veto + None."""
    try:
        result = await compute_live_score(db, symbol)
    except Exception:
        return (False, None)
    score = float(result.get("score", 0))
    if direction == Direction.LONG and score < app_settings.bot_oracle_veto_long_below:
        return (True, score)
    if direction == Direction.SHORT and score > app_settings.bot_oracle_veto_short_above:
        return (True, score)
    return (False, score)


async def news_veto(db: AsyncSession, base_asset: str) -> bool:
    """Skip if a high-importance NewsArticle mentions the base asset recently.

    NewsArticle.coins is a free-text comma-separated list (e.g. "BTC,ETH"),
    so we substring-match. Matches are intentionally loose — better to skip a
    borderline alert than trade through a macro headline.
    """
    if not base_asset:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=int(app_settings.bot_news_veto_window_minutes)
    )
    stmt = (
        select(NewsArticle.id)
        .where(
            NewsArticle.published_at >= cutoff,
            NewsArticle.importance == "high",
            NewsArticle.coins.ilike(f"%{base_asset}%"),
        )
        .limit(1)
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none() is not None


async def evaluate(
    db: AsyncSession,
    *,
    symbol: str,
    base_asset: str,
    exchange: str,
    market_type: str | None,
    direction: Direction,
) -> tuple[SkipReason | None, float | None]:
    """Run all vetoes in priority order. Returns (skip_reason_or_none, oracle_score).

    The oracle score is returned even when the bot would *not* veto, so the
    executor can persist it on the trade row for postmortems.

    Cheapest checks first: pure config (perp-only, blocklist) before any I/O,
    then Redis/DB, then the liquidity floor (a candle fetch) last.
    """
    if not getattr(app_settings, "bot_enabled", False):
        return (SkipReason.BOT_DISABLED, None)
    if not direction_enabled(direction):
        return (SkipReason.DIRECTION_DISABLED, None)
    if not market_allowed(market_type):
        return (SkipReason.NOT_PERP, None)
    if is_blocked(symbol):
        return (SkipReason.SYMBOL_BLOCKED, None)
    if await equity.is_kill_switch_tripped():
        return (SkipReason.KILL_SWITCH, None)
    if not supports_exchange(exchange):
        return (SkipReason.EXCHANGE_UNSUPPORTED, None)
    if await already_open(db, symbol):
        return (SkipReason.ALREADY_OPEN, None)
    if await on_cooldown(symbol):
        return (SkipReason.COOLDOWN, None)
    concurrent = await equity.get_concurrent_count()
    if concurrent >= int(app_settings.bot_max_concurrent):
        return (SkipReason.MAX_CONCURRENT, None)
    veto, score = await oracle_veto(db, symbol, direction)
    if veto:
        return (SkipReason.ORACLE_VETO, score)
    if await news_veto(db, base_asset):
        return (SkipReason.NEWS_VETO, score)
    min_turnover = float(getattr(app_settings, "bot_min_turnover_usd", 0) or 0)
    if min_turnover > 0:
        from app.modules.bot import candle_source  # local import avoids cycle
        turnover = await candle_source.recent_turnover_usd(symbol, exchange, market_type)
        if turnover < min_turnover:
            return (SkipReason.LOW_TURNOVER, score)
    return (None, score)
