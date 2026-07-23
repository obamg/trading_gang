"""Internal types for WaveBot — enums + the strategy's TradePlan."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class SkipReason(str, Enum):
    BOT_DISABLED = "bot_disabled"
    KILL_SWITCH = "kill_switch"
    ALREADY_OPEN = "already_open"
    COOLDOWN = "cooldown"
    MAX_CONCURRENT = "max_concurrent"
    ORACLE_VETO = "oracle_veto"
    NEWS_VETO = "news_veto"
    NO_CANDLES = "no_candles"
    NO_EQUITY = "no_equity"
    INVALID_DIRECTION = "invalid_direction"
    EXCHANGE_UNSUPPORTED = "exchange_unsupported"
    NOT_PERP = "not_perp"
    SYMBOL_BLOCKED = "symbol_blocked"
    LOW_TURNOVER = "low_turnover"
    DIRECTION_DISABLED = "direction_disabled"
    MAX_OPEN_RISK = "max_open_risk"
    LOW_VOL_RATIO = "low_vol_ratio"
    FUNDING_EXTREME = "funding_extreme"
    BLOCKED_HOUR = "blocked_hour"


class CloseReason(str, Enum):
    STOP = "stop"
    TP = "tp"
    MANUAL = "manual"
    KILL_SWITCH = "kill_switch"
    MAX_HOLD = "max_hold"
    EXPIRED = "expired"  # pending limit order cancelled unfilled (v2 retrace)


@dataclass(frozen=True)
class TrailState:
    """Mutable-per-bar trail bookkeeping for partial_trail exits, carried
    between monitor ticks via bot_trades columns (stop_price / peak_price /
    partial_exit_at). Immutable so strategy.step_trail_bar stays pure."""

    stop: Decimal
    peak: Decimal
    partial_taken: bool


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    exchange: str
    market_type: str | None
    direction: Direction
    alert_type: str
    alert_detected_at: datetime
    signal_high: Decimal
    signal_low: Decimal
    stop_price: Decimal
    take_profit_price: Decimal
    notional_usd: Decimal
    paper_equity: Decimal
    vol_ratio: Decimal | None = None
    funding_pct: Decimal | None = None
    pct_change: Decimal | None = None
    oracle_score: Decimal | None = None
