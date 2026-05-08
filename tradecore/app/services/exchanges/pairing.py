"""Fill → Trade pairing.

Perpetuals don't return "trades," they return fills. To produce a closed
Trade row we walk fills chronologically per symbol, maintaining a running
position state. When the position shrinks or flips, we emit a closed Trade
covering the closed portion.

Idempotency: each emitted Trade is keyed on (exchange, exchange_trade_id) of
the *closing* fill. The DB has a partial unique index on this pair, so
re-syncs over an overlapping time window never duplicate.

External entries: if the first fill we see is reduce-only (realized_pnl != 0
on Binance) and we have no prior position state for that symbol, the user
opened the position before connecting their key. We record a Trade with
exit_reason='external_entry', pnl_usd=null so it doesn't pollute performance.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.services.exchanges.base import Fill


@dataclass
class TradeRecord:
    """Canonical closed-trade output. Maps 1:1 onto Trade ORM fields."""

    exchange: str
    exchange_trade_id: str  # ID of the closing fill
    exchange_order_id: str | None
    symbol: str
    side: str  # "long" | "short"
    entry_price: float
    exit_price: float
    size: float
    entry_at: datetime
    exit_at: datetime
    pnl_usd: float | None
    fees_usd: float
    net_pnl_usd: float | None
    exit_reason: str | None  # "external_entry" or None


@dataclass
class _PositionState:
    qty: float = 0.0  # signed: +long, -short
    weighted_entry: float = 0.0
    entry_at: datetime | None = None
    fees_open: float = 0.0  # cumulative entry-side fees on currently-open size

    @property
    def is_flat(self) -> bool:
        return self.qty == 0.0


def pair_fills(fills: Iterable[Fill]) -> list[TradeRecord]:
    """Convert chronological fills into closed Trade records.

    Open positions remain in state and don't appear in output (matches the
    existing performance behavior of filtering on status='closed').
    """
    by_symbol: dict[str, list[Fill]] = defaultdict(list)
    for f in fills:
        by_symbol[f.symbol].append(f)

    trades: list[TradeRecord] = []
    for symbol, sym_fills in by_symbol.items():
        sym_fills.sort(key=lambda x: (x.ts, x.exchange_trade_id))
        trades.extend(_pair_symbol(symbol, sym_fills))
    return trades


def _pair_symbol(symbol: str, fills: list[Fill]) -> list[TradeRecord]:
    state = _PositionState()
    out: list[TradeRecord] = []

    for f in fills:
        signed = f.qty if f.side == "buy" else -f.qty
        if signed == 0:
            continue

        # External-entry case: first fill we see for this symbol is reducing
        # (realized_pnl != 0) but we hold no position. Record the close and
        # mark exit_reason so performance excludes it.
        if state.is_flat and f.realized_pnl_usd not in (None, 0.0):
            out.append(
                TradeRecord(
                    exchange=f.exchange,
                    exchange_trade_id=f.exchange_trade_id,
                    exchange_order_id=f.exchange_order_id,
                    symbol=symbol,
                    side="short" if f.side == "buy" else "long",
                    entry_price=f.price,  # unknown; use exit price as placeholder
                    exit_price=f.price,
                    size=f.qty,
                    entry_at=f.ts,
                    exit_at=f.ts,
                    pnl_usd=None,
                    fees_usd=f.fee_usd,
                    net_pnl_usd=None,
                    exit_reason="external_entry",
                )
            )
            continue

        if state.is_flat:
            # Opening
            state.qty = signed
            state.weighted_entry = f.price
            state.entry_at = f.ts
            state.fees_open = f.fee_usd
            continue

        same_side = (state.qty > 0 and signed > 0) or (state.qty < 0 and signed < 0)

        if same_side:
            # Add to position; recompute weighted-average entry.
            new_qty = state.qty + signed
            state.weighted_entry = (
                state.weighted_entry * abs(state.qty) + f.price * f.qty
            ) / abs(new_qty)
            state.qty = new_qty
            state.fees_open += f.fee_usd
            continue

        # Reducing. Close `reduce_qty` of the existing position.
        reduce_qty = min(f.qty, abs(state.qty))
        side_str = "long" if state.qty > 0 else "short"
        side_mul = 1 if state.qty > 0 else -1

        # Pro-rate accumulated entry fees to the closed portion.
        portion = reduce_qty / abs(state.qty)
        entry_fees_share = state.fees_open * portion
        # The closing fill's fee is also pro-rated by what fraction of *this fill*
        # was applied to the close (vs spilled into a flip).
        close_fee_share = f.fee_usd * (reduce_qty / f.qty)
        total_fees = entry_fees_share + close_fee_share

        gross_pnl = (f.price - state.weighted_entry) * reduce_qty * side_mul
        # Trust exchange-reported realized_pnl when available — handles funding,
        # mark-vs-last, etc. Pro-rate it by the closed portion of the fill.
        if f.realized_pnl_usd is not None:
            realized_share = f.realized_pnl_usd * (reduce_qty / f.qty)
            gross_pnl = realized_share

        out.append(
            TradeRecord(
                exchange=f.exchange,
                exchange_trade_id=f.exchange_trade_id,
                exchange_order_id=f.exchange_order_id,
                symbol=symbol,
                side=side_str,
                entry_price=state.weighted_entry,
                exit_price=f.price,
                size=reduce_qty,
                entry_at=state.entry_at or f.ts,
                exit_at=f.ts,
                pnl_usd=gross_pnl,
                fees_usd=total_fees,
                net_pnl_usd=gross_pnl - total_fees,
                exit_reason=None,
            )
        )

        # Update state.
        state.fees_open -= entry_fees_share
        if f.qty < abs(state.qty):
            # Partial close — position shrinks, weighted_entry unchanged.
            state.qty += signed
        elif f.qty == abs(state.qty):
            # Full close.
            state.qty = 0.0
            state.weighted_entry = 0.0
            state.entry_at = None
            state.fees_open = 0.0
        else:
            # Flip: close everything, open fresh in the new direction with
            # the leftover fill quantity.
            leftover = f.qty - abs(state.qty)
            state.qty = leftover if signed > 0 else -leftover
            state.weighted_entry = f.price
            state.entry_at = f.ts
            state.fees_open = f.fee_usd * (leftover / f.qty)

    return out


__all__ = ["pair_fills", "TradeRecord"]
