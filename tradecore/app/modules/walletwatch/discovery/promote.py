"""Promotion of discovered wallets into ``whale_entities``.

Two entry points:

  promote_score(db, score, name=None)
    Idempotent helper: turns one ``WalletPnlScore`` row into a ``WhaleEntity`` +
    ``WhaleEntityAddress`` and stamps ``promoted_at`` on the score. Used by
    both the manual POST endpoint and the auto-promote scheduler job — they
    stay in lock-step.

  auto_promote_job()
    Scheduler entry. No-op unless ``DISCOVERY_AUTO_PROMOTE_ENABLED=true``.
    Selects ``WalletPnlScore`` rows above all four threshold gates, promotes
    them (up to ``DISCOVERY_AUTO_PROMOTE_MAX_PER_TICK`` per run), and emits
    one ``alerts:walletwatch`` event per promotion so the dashboard sees new
    smart-money joining the watchlist immediately.

Thresholds are intentionally conservative — false positives mean the
detector starts spamming alerts for a junk wallet. Loosen via env vars
once you've watched the auto-promotion log for a few cycles and trust it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.walletwatch_discovery import WalletPnlScore
from app.models.whale_entity import WhaleEntity, WhaleEntityAddress
from app.services import redis_service


def _short_addr(addr: str) -> str:
    if not addr or len(addr) <= 10:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


def _default_entity_name(score: WalletPnlScore) -> str:
    return f"PnL Discovery {_short_addr(score.wallet_address)}"


def _conviction_for(score: WalletPnlScore) -> Decimal:
    width_bonus = 1 + min(int(score.token_count), 10) / 10.0
    raw = float(score.win_rate) * width_bonus
    return Decimal(str(round(min(raw, 1.0), 3)))


async def promote_score(
    db: AsyncSession,
    score: WalletPnlScore,
    *,
    name: str | None = None,
    entity_type: str = "smart_money",
    label: str = "auto-promoted",
) -> dict:
    """Promote one wallet — idempotent. Caller is responsible for ``db.commit()``."""
    if score.promoted_at is not None:
        return {
            "ok": True,
            "reason": "already_promoted",
            "entity_id": str(score.promoted_entity_id) if score.promoted_entity_id else None,
            "wallet_address": score.wallet_address,
        }

    # Never re-add an address the pruner already threw out. Scoring carries no
    # frequency signal at all (win_count+loss_count counts CLOSED POSITIONS —
    # a wallet doing 16,633 swaps/day scores 12), so a bot can look excellent
    # here forever and would otherwise be promoted, pruned, and promoted again
    # every cycle. The unique index on `address` would reject the duplicate
    # anyway, but silently, inside a swallowed exception.
    existing = (
        await db.execute(
            select(WhaleEntityAddress).where(
                func.lower(WhaleEntityAddress.address) == (score.wallet_address or "").lower()
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        score.promoted_at = datetime.now(timezone.utc)
        score.promoted_entity_id = existing.entity_id
        return {
            "ok": True,
            "reason": "address_already_watched"
            if existing.is_active
            else "address_deactivated",
            "entity_id": str(existing.entity_id),
            "wallet_address": score.wallet_address,
        }

    entity_name = name or _default_entity_name(score)
    entity = (
        await db.execute(select(WhaleEntity).where(WhaleEntity.name == entity_name))
    ).scalar_one_or_none()
    if entity is None:
        entity = WhaleEntity(
            name=entity_name,
            entity_type=entity_type,
            conviction_score=_conviction_for(score),
        )
        db.add(entity)
        await db.flush()

    db.add(
        WhaleEntityAddress(
            entity_id=entity.id,
            address=score.wallet_address,
            chain=score.chain,
            label=label,
        )
    )
    now = datetime.now(timezone.utc)
    score.promoted_at = now
    score.promoted_entity_id = entity.id
    return {
        "ok": True,
        "entity_id": str(entity.id),
        "wallet_address": score.wallet_address,
        "promoted_at": now.isoformat(),
    }


def _apply_eligibility_filters(stmt):
    s = app_settings
    return stmt.where(
        WalletPnlScore.promoted_at.is_(None),
        WalletPnlScore.discovery_score >= s.discovery_auto_promote_min_score,
        WalletPnlScore.total_realized_usd >= s.discovery_auto_promote_min_realized_usd,
        WalletPnlScore.win_rate >= s.discovery_auto_promote_min_win_rate,
        WalletPnlScore.token_count >= s.discovery_auto_promote_min_token_count,
    )


async def _publish_promotion_alert(score: WalletPnlScore, entity_id: str) -> None:
    alert = {
        "module": "walletwatch",
        "type": "auto_promoted",
        "wallet": score.wallet_address,
        "chain": score.chain,
        "entity_id": entity_id,
        "discovery_score": float(score.discovery_score),
        "total_realized_usd": float(score.total_realized_usd),
        "win_rate": float(score.win_rate),
        "token_count": int(score.token_count),
        "best_multiple": float(score.best_multiple),
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_service.publish_alert("walletwatch", alert)


async def auto_promote_job() -> None:
    """Scheduler entry. No-op unless ``DISCOVERY_AUTO_PROMOTE_ENABLED=true``."""
    if not getattr(app_settings, "discovery_auto_promote_enabled", False):
        return
    limit = max(1, int(getattr(app_settings, "discovery_auto_promote_max_per_tick", 5)))
    async with AsyncSessionLocal() as db:
        try:
            stmt = (
                _apply_eligibility_filters(select(WalletPnlScore))
                .order_by(desc(WalletPnlScore.discovery_score))
                .limit(limit)
            )
            rows = list((await db.execute(stmt)).scalars().all())
            if not rows:
                log.info("discovery_auto_promote_idle")
                return
            promoted = 0
            for score in rows:
                try:
                    result = await promote_score(db, score)
                    await db.commit()
                    # Any `reason` means nothing was actually added — counting
                    # it as a promotion would also publish a walletwatch alert
                    # for a wallet we did not start watching.
                    if result.get("reason"):
                        log.info(
                            "discovery_auto_promote_skipped",
                            wallet=score.wallet_address,
                            reason=result["reason"],
                        )
                        continue
                    promoted += 1
                    await _publish_promotion_alert(score, result["entity_id"])
                    log.info(
                        "discovery_auto_promoted",
                        wallet=score.wallet_address,
                        chain=score.chain,
                        score=float(score.discovery_score),
                        realized_usd=float(score.total_realized_usd),
                        win_rate=float(score.win_rate),
                        token_count=int(score.token_count),
                    )
                except Exception as e:
                    await db.rollback()
                    log.warning(
                        "discovery_auto_promote_failed",
                        wallet=score.wallet_address,
                        err=str(e),
                    )
            log.info(
                "discovery_auto_promote_tick",
                candidates=len(rows),
                promoted=promoted,
            )
        except Exception as e:
            log.error("discovery_auto_promote_tick_failed", err=str(e))


__all__ = ["promote_score", "auto_promote_job"]
