"""Pure-function PnL scoring for wallet discovery.

Two layers:

  compute_token_pnl(events, current_price) → per-wallet rows for ONE token
    Uses FIFO cost-basis accounting on a list of buy/sell events.

  compute_wallet_score(per_token_rows) → one wallet's rolled-up score
    Aggregates win rate, avg multiple, and a combined ``discovery_score``
    that the leaderboard sorts on.

No I/O — these functions take data and return data so they're trivial to
unit-test and reason about.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

ZERO = Decimal("0")
ONE = Decimal("1")
SIX_DP = Decimal("0.0001")


@dataclass(frozen=True)
class TokenSwapEvent:
    wallet: str
    side: str  # "buy" | "sell"
    amount_token: Decimal
    amount_usd: Decimal
    ts: datetime


def _safe_unit(amount_usd: Decimal, amount_token: Decimal) -> Decimal:
    if amount_token <= 0:
        return ZERO
    return amount_usd / amount_token


def compute_token_pnl(
    events: Iterable[TokenSwapEvent],
    current_price_usd: Decimal,
) -> dict[str, dict]:
    """Group events by wallet and compute FIFO cost-basis PnL for one token.

    Returns: { wallet_address: { total_buy_usd, total_buy_amount, total_sell_usd,
               total_sell_amount, current_balance, current_value_usd,
               realized_pnl_usd, unrealized_pnl_usd, multiple, first_buy_at } }

    Notes:
      - Sells beyond known buys (e.g. wallet had a position before our scan
        window) are credited with zero cost basis. This biases scores upward
        for wallets we joined late, which is acceptable for a leaderboard —
        promotion logic should weigh ``win_count`` and ``token_count`` to
        offset.
      - Multiple = (realized_usd + current_value_usd) / total_buy_usd.
        NULL when total_buy_usd is 0.
    """
    by_wallet: dict[str, list[TokenSwapEvent]] = {}
    for ev in events:
        by_wallet.setdefault(ev.wallet, []).append(ev)

    out: dict[str, dict] = {}
    for wallet, evs in by_wallet.items():
        evs = sorted(evs, key=lambda x: x.ts)

        # FIFO queue: list of (remaining_amount, unit_cost_usd).
        buy_queue: list[tuple[Decimal, Decimal]] = []
        total_buy_usd = ZERO
        total_buy_amount = ZERO
        total_sell_usd = ZERO
        total_sell_amount = ZERO
        realized = ZERO
        first_buy_at: datetime | None = None

        for ev in evs:
            if ev.amount_token <= 0:
                continue
            if ev.side == "buy":
                unit_cost = _safe_unit(ev.amount_usd, ev.amount_token)
                buy_queue.append((ev.amount_token, unit_cost))
                total_buy_usd += ev.amount_usd
                total_buy_amount += ev.amount_token
                if first_buy_at is None:
                    first_buy_at = ev.ts
            elif ev.side == "sell":
                total_sell_usd += ev.amount_usd
                total_sell_amount += ev.amount_token
                unit_revenue = _safe_unit(ev.amount_usd, ev.amount_token)
                remaining = ev.amount_token
                while remaining > 0 and buy_queue:
                    buy_amt, buy_unit_cost = buy_queue[0]
                    used = min(remaining, buy_amt)
                    realized += used * (unit_revenue - buy_unit_cost)
                    remaining -= used
                    buy_amt -= used
                    if buy_amt <= 0:
                        buy_queue.pop(0)
                    else:
                        buy_queue[0] = (buy_amt, buy_unit_cost)
                if remaining > 0:
                    # Sold more than we observed buying — treat as zero-cost.
                    realized += remaining * unit_revenue

        current_balance = sum((amt for amt, _ in buy_queue), start=ZERO)
        remaining_cost = sum((amt * cost for amt, cost in buy_queue), start=ZERO)
        current_value = current_balance * current_price_usd
        unrealized = current_value - remaining_cost

        if total_buy_usd > 0:
            multiple = (total_sell_usd + current_value) / total_buy_usd
        else:
            multiple = None

        out[wallet] = {
            "total_buy_usd": total_buy_usd,
            "total_buy_amount": total_buy_amount,
            "total_sell_usd": total_sell_usd,
            "total_sell_amount": total_sell_amount,
            "current_balance": current_balance,
            "current_value_usd": current_value,
            "realized_pnl_usd": realized,
            "unrealized_pnl_usd": unrealized,
            "multiple": multiple,
            "first_buy_at": first_buy_at,
        }
    return out


@dataclass(frozen=True)
class TokenPnlRow:
    """Subset of WalletTokenPnl fields the aggregator needs."""
    realized_pnl_usd: Decimal
    unrealized_pnl_usd: Decimal
    total_buy_usd: Decimal
    multiple: Decimal | None


def compute_wallet_score(rows: list[TokenPnlRow]) -> dict | None:
    """Roll up one wallet's per-token rows into a single score.

    discovery_score = (realized + unrealized) × win_rate × log10(token_count + 1)

    The log factor rewards width (a wallet that hit 5 tokens is more
    interesting than one that hit 1, but not 5x more). The win_rate
    multiplier punishes wallets that got lucky on one position while
    losing on most. PnL ≤ 0 → score 0 (uninteresting).
    """
    if not rows:
        return None

    total_realized = sum((r.realized_pnl_usd for r in rows), start=ZERO)
    total_unrealized = sum((r.unrealized_pnl_usd for r in rows), start=ZERO)
    total_cost = sum((r.total_buy_usd for r in rows), start=ZERO)

    multiples = [r.multiple for r in rows if r.multiple is not None]
    win_count = sum(1 for m in multiples if m > ONE)
    loss_count = sum(1 for m in multiples if m <= ONE)
    decisive = win_count + loss_count
    win_rate = (Decimal(win_count) / Decimal(decisive)) if decisive > 0 else ZERO
    avg_multiple = (sum(multiples, start=ZERO) / Decimal(len(multiples))) if multiples else ZERO
    best_multiple = max(multiples) if multiples else ZERO

    token_count = len(rows)
    pnl = total_realized + total_unrealized
    if pnl > 0:
        width_factor = Decimal(str(math.log10(token_count + 1) + 1))
        discovery_score = pnl * win_rate * width_factor
    else:
        discovery_score = ZERO

    return {
        "total_realized_usd": total_realized,
        "total_unrealized_usd": total_unrealized,
        "total_cost_basis_usd": total_cost,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate.quantize(SIX_DP),
        "avg_multiple": avg_multiple,
        "best_multiple": best_multiple,
        "token_count": token_count,
        "discovery_score": discovery_score,
    }


__all__ = [
    "TokenSwapEvent",
    "TokenPnlRow",
    "compute_token_pnl",
    "compute_wallet_score",
]
