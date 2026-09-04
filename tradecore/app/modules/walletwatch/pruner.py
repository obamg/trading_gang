"""Deactivate high-frequency addresses — the anti-bot guard for WalletWatch.

Why this exists: ``discovery`` scores wallets on realized PnL, win rate and
token breadth. None of those penalise *frequency*, so the auto-promoter
happily promoted MEV/HFT bots — measured 2026-09-04, one promoted address was
doing 35,019 swaps/day on its own and the top five accounted for 78,000 of
80,284 daily swaps. That is unbounded scan load, unbounded CoinGecko pricing
calls, and (for one of them) 440 Telegram alerts a day.

The signal that separates them is stark and needs no model — real
smart-money wallets trade tens of times a day, bots trade thousands:

    35019, 16633, 12067, 8073, 6166 | 658, 600, 323, 289, 155, 80, 54, 2, 0 …

``WALLETWATCH_MAX_SWAPS_PER_DAY`` cuts in that gap. Deactivation is a flag,
not a delete: the row and its PnL-discovery provenance survive, so a wallet
can be reinstated by flipping ``is_active`` back.

IMPORTANT consequence, measured before shipping: every walletwatch alert
currently produced comes from a wallet above this ceiling. Pruning therefore
takes the alert feed to ~zero. That is a signal-quality result, not a
regression — the alerts were bot noise. Getting real alerts back requires
discovery to surface genuine smart money, which is a separate problem.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.walletwatch import WalletSwap
from app.models.whale_entity import WhaleEntityAddress

REASON_HIGH_FREQUENCY = "high_frequency"


async def swap_counts_24h(db: AsyncSession) -> dict[str, int]:
    """address(lower) -> swaps in the last 24h."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (
        await db.execute(
            select(
                func.lower(WalletSwap.wallet_address),
                func.count(WalletSwap.id),
            )
            .where(WalletSwap.created_at >= cutoff)
            .group_by(func.lower(WalletSwap.wallet_address))
        )
    ).all()
    return {addr: int(n) for addr, n in rows}


async def prune_high_frequency(db: AsyncSession) -> dict:
    """Deactivate active addresses over the daily swap ceiling."""
    ceiling = int(getattr(app_settings, "walletwatch_max_swaps_per_day", 500))
    if ceiling <= 0:
        return {"skipped": "disabled"}

    counts = await swap_counts_24h(db)
    active = (
        await db.execute(
            select(WhaleEntityAddress).where(WhaleEntityAddress.is_active.is_(True))
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    pruned = []
    for row in active:
        n = counts.get((row.address or "").lower(), 0)
        if n <= ceiling:
            continue
        row.is_active = False
        row.deactivated_at = now
        row.deactivated_reason = REASON_HIGH_FREQUENCY
        pruned.append((row.address, n))

    if pruned:
        await db.commit()
        for addr, n in pruned:
            log.warning(
                "walletwatch_address_pruned",
                wallet=addr,
                swaps_24h=n,
                ceiling=ceiling,
                reason=REASON_HIGH_FREQUENCY,
            )
    return {
        "active_before": len(active),
        "pruned": len(pruned),
        "ceiling": ceiling,
    }


async def run_prune_job() -> None:
    """Scheduler entry. Swallows + logs per project convention."""
    async with AsyncSessionLocal() as db:
        try:
            result = await prune_high_frequency(db)
            if result.get("pruned"):
                log.info("walletwatch_prune_tick", **result)
        except Exception as e:
            log.error("walletwatch_prune_failed", err=str(e))


__all__ = ["prune_high_frequency", "run_prune_job", "swap_counts_24h", "REASON_HIGH_FREQUENCY"]
