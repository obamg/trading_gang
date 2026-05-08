"""Exchange adapter contract + canonical Fill shape.

Every exchange returns its fills in a different envelope. Adapters normalize
those into `Fill` objects so the pairing algorithm never branches on exchange.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

FillSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class Fill:
    """One executed trade fragment from an exchange.

    `realized_pnl` is the exchange-reported P&L on closing fills (Binance and
    Bybit both provide this). When None, pairing computes from entry/exit.
    """

    exchange: str
    exchange_trade_id: str
    exchange_order_id: str | None
    symbol: str
    side: FillSide
    price: float
    qty: float
    fee_usd: float
    fee_asset: str | None
    realized_pnl_usd: float | None
    is_reduce_only: bool
    ts: datetime
    raw: dict = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class Credentials:
    """Decrypted credentials passed into adapter calls. Never persisted."""

    api_key: str
    api_secret: str
    passphrase: str | None = None


@runtime_checkable
class ExchangeAdapter(Protocol):
    name: str
    display_name: str
    requires_passphrase: bool

    async def validate(self, creds: Credentials) -> dict:
        """Ping the exchange, return permissions dict.

        Must raise if the key has withdraw or trade permissions enabled — we
        only accept read-only / read-positions keys.
        """
        ...

    async def fetch_fills(
        self, creds: Credentials, since: datetime
    ) -> list[Fill]:
        """Return all futures fills at or after `since`, oldest first."""
        ...


REGISTRY: dict[str, ExchangeAdapter] = {}


def register(adapter: ExchangeAdapter) -> ExchangeAdapter:
    """Register an adapter in the global registry. Idempotent."""
    REGISTRY[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> ExchangeAdapter:
    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unknown exchange: {name}") from exc


def supported_exchanges() -> list[dict]:
    """Public metadata for the frontend: which exchanges + which fields the form needs."""
    return [
        {
            "name": a.name,
            "display_name": a.display_name,
            "requires_passphrase": a.requires_passphrase,
        }
        for a in REGISTRY.values()
    ]
