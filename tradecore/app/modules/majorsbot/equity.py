"""Paper-equity bookkeeping for MajorsBot, in Redis — mirrors bot/equity.py.

Keyed by LEDGER since 2026-08-20: newsevent runs stopless at high leverage,
where a single liquidation costs ~95% of its book. On a shared ledger that
event would slash volevent's sizing base mid-forward-test — so each risk
regime gets its own equity and its own concurrent counter.

  ledger "paper"      volevent + fundingfade (the original book)
      majorsbot:equity:paper       current paper balance (closed PnL only)
      majorsbot:concurrent_count   int — open positions on this ledger
  ledger "newsevent"
      majorsbot:equity:newsevent
      majorsbot:concurrent:newsevent

The default ledger's keys are byte-identical to the pre-split keys on
purpose — the live volevent forward test must not notice this refactor.

Separate keys from WaveBot on purpose: the two bots must never share a ledger.
No kill switch / daily anchor here — the majors book is slow (hourly bars) and
risk is bounded by majorsbot_max_concurrent × majorsbot_risk_per_trade_pct.
"""
from __future__ import annotations

from decimal import Decimal

from app.config import settings as app_settings
from app.modules.majorsbot import strategies
from app.services import redis_service

DEFAULT_LEDGER = "paper"


def ledger_for(strategy: str) -> str:
    """Which equity book a strategy trades against."""
    return "newsevent" if strategy == strategies.NEWSEVENT else DEFAULT_LEDGER


def _equity_key(ledger: str) -> str:
    return f"majorsbot:equity:{ledger}"


def _concurrent_key(ledger: str) -> str:
    # Legacy spelling for the default book — preserves live state.
    if ledger == DEFAULT_LEDGER:
        return "majorsbot:concurrent_count"
    return f"majorsbot:concurrent:{ledger}"


async def get_paper_equity(ledger: str = DEFAULT_LEDGER) -> Decimal:
    r = redis_service.get_redis()
    raw = await r.get(_equity_key(ledger))
    if raw is None:
        initial = Decimal(str(app_settings.majorsbot_paper_equity_initial))
        await r.set(_equity_key(ledger), str(initial))
        return initial
    return Decimal(str(_decode(raw)))


async def add_to_equity(delta: Decimal, ledger: str = DEFAULT_LEDGER) -> Decimal:
    """Apply realized PnL to paper equity. Returns the new balance."""
    current = await get_paper_equity(ledger)
    new_equity = current + delta
    r = redis_service.get_redis()
    await r.set(_equity_key(ledger), str(new_equity))
    return new_equity


async def reset_paper_equity(ledger: str = DEFAULT_LEDGER) -> Decimal:
    """Dev convenience — reset to MAJORSBOT_PAPER_EQUITY_INITIAL."""
    initial = Decimal(str(app_settings.majorsbot_paper_equity_initial))
    r = redis_service.get_redis()
    await r.set(_equity_key(ledger), str(initial))
    return initial


async def get_concurrent_count(ledger: str = DEFAULT_LEDGER) -> int:
    r = redis_service.get_redis()
    raw = await r.get(_concurrent_key(ledger))
    try:
        return int(_decode(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


async def increment_concurrent(ledger: str = DEFAULT_LEDGER) -> int:
    r = redis_service.get_redis()
    return int(await r.incr(_concurrent_key(ledger)))


async def decrement_concurrent(ledger: str = DEFAULT_LEDGER) -> int:
    r = redis_service.get_redis()
    new_val = int(await r.decr(_concurrent_key(ledger)))
    if new_val < 0:
        await r.set(_concurrent_key(ledger), "0")
        return 0
    return new_val


async def reconcile_concurrent(actual_open: int, ledger: str = DEFAULT_LEDGER) -> int:
    """Overwrite the counter with DB truth (count of open majorsbot_trades on
    this ledger). Returns the drift (redis − actual) for logging. Same
    rationale as WaveBot: a stuck-high counter blocks entries forever."""
    current = await get_concurrent_count(ledger)
    drift = current - actual_open
    if drift != 0:
        r = redis_service.get_redis()
        await r.set(_concurrent_key(ledger), str(max(0, actual_open)))
    return drift


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v
