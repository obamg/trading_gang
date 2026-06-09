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


class CloseReason(str, Enum):
    STOP = "stop"
    TP = "tp"
    MANUAL = "manual"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    exchange: str
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
