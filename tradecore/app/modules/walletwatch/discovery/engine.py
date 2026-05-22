"""Discovery orchestrator.

Two scheduled entry points:

  refresh_candidates_job
    Pulls top-gainers from CoinGecko + observed-buy tokens from
    ``wallet_swaps`` into ``discovery_tokens``. Runs every 6h.

  score_candidates_job
    Picks the next batch of tokens that haven't been scored in 24h, fetches
    their swap history, computes per-wallet PnL, upserts into
    ``wallet_token_pnl``, and rolls up into ``wallet_pnl_score``. Runs every 1h.

Per-token scoring is bounded (BATCH_PER_TICK) so a single tick can't run
the API budget dry. Each token call ≈ 3 Etherscan reqs + 1 CoinGecko req.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import asc, func, nullsfirst, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.walletwatch_discovery import (
    DiscoveryToken,
    WalletPnlScore,
    WalletTokenPnl,
)
from app.modules.walletwatch import classifier
from app.modules.walletwatch.discovery import candidates
from app.modules.walletwatch.discovery.fetcher import fetch_token_swap_events
from app.modules.walletwatch.discovery.scorer import (
    TokenPnlRow,
    compute_token_pnl,
    compute_wallet_score,
)
from app.modules.walletwatch.pricing import get_token_usd_price

BATCH_PER_TICK = 10           # tokens scored per scheduler run
RESCORE_AFTER_HOURS = 24      # only re-score tokens older than this
SCORING_WINDOW_DAYS = 30      # how far back to fetch swaps for each token


async def refresh_candidates_job() -> None:
    if not getattr(app_settings, "discovery_enabled", False):
        return
    async with AsyncSessionLocal() as db:
        try:
            counts = await candidates.refresh_candidates(db)
            log.info("discovery_candidates_refreshed", **counts)
        except Exception as e:
            log.error("discovery_candidates_failed", err=str(e))


async def _pick_next_batch(db: AsyncSession) -> list[DiscoveryToken]:
    """Tokens that have never been scored, or were last scored >24h ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RESCORE_AFTER_HOURS)
    stmt = (
        select(DiscoveryToken)
        .where(
            (DiscoveryToken.last_scored_at.is_(None))
            | (DiscoveryToken.last_scored_at < cutoff)
        )
        .order_by(nullsfirst(asc(DiscoveryToken.last_scored_at)))
        .limit(BATCH_PER_TICK)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _score_one_token(db: AsyncSession, token: DiscoveryToken) -> int:
    """Returns the number of (wallet × token) PnL rows upserted."""
    now = datetime.now(timezone.utc)
    from_ts_ms = int((now - timedelta(days=SCORING_WINDOW_DAYS)).timestamp() * 1000)
    to_ts_ms = int(now.timestamp() * 1000)

    events = await fetch_token_swap_events(
        chain=token.chain,
        token_address=token.address,
        from_ts_ms=from_ts_ms,
        to_ts_ms=to_ts_ms,
    )
    # Always advance last_scored_at so we don't retry empty tokens every tick.
    token.last_scored_at = now
    if not events:
        await db.commit()
        return 0

    current_price = await get_token_usd_price(token.chain, token.address)
    current_price_dec = Decimal(str(current_price)) if current_price else Decimal("0")

    results = compute_token_pnl(events, current_price_dec)
    upserted = 0
    for wallet, r in results.items():
        if r["total_buy_usd"] <= 0 and r["total_sell_usd"] <= 0:
            continue
        multiple = r["multiple"]
        stmt = (
            pg_insert(WalletTokenPnl)
            .values(
                wallet_address=wallet,
                chain=token.chain,
                token_address=token.address,
                token_symbol=token.symbol,
                total_buy_usd=r["total_buy_usd"],
                total_buy_amount=r["total_buy_amount"],
                total_sell_usd=r["total_sell_usd"],
                total_sell_amount=r["total_sell_amount"],
                current_balance=r["current_balance"],
                current_value_usd=r["current_value_usd"],
                realized_pnl_usd=r["realized_pnl_usd"],
                unrealized_pnl_usd=r["unrealized_pnl_usd"],
                multiple=multiple,
                first_buy_at=r.get("first_buy_at"),
                last_synced_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_wallet_token_pnl",
                set_={
                    "total_buy_usd": r["total_buy_usd"],
                    "total_buy_amount": r["total_buy_amount"],
                    "total_sell_usd": r["total_sell_usd"],
                    "total_sell_amount": r["total_sell_amount"],
                    "current_balance": r["current_balance"],
                    "current_value_usd": r["current_value_usd"],
                    "realized_pnl_usd": r["realized_pnl_usd"],
                    "unrealized_pnl_usd": r["unrealized_pnl_usd"],
                    "multiple": multiple,
                    "first_buy_at": r.get("first_buy_at"),
                    "last_synced_at": now,
                },
            )
        )
        await db.execute(stmt)
        upserted += 1
    await db.commit()
    return upserted


async def _refresh_wallet_scores(db: AsyncSession, wallets: set[str]) -> int:
    """Recompute wallet_pnl_score for the given wallets from their per-token rows."""
    if not wallets:
        return 0
    now = datetime.now(timezone.utc)
    refreshed = 0
    majors = classifier.all_major_addresses()
    for wallet in wallets:
        rows = (
            await db.execute(
                select(WalletTokenPnl).where(
                    WalletTokenPnl.wallet_address == wallet,
                    WalletTokenPnl.token_address.notin_(majors),
                )
            )
        ).scalars().all()
        if not rows:
            continue
        score = compute_wallet_score(
            [
                TokenPnlRow(
                    realized_pnl_usd=r.realized_pnl_usd,
                    unrealized_pnl_usd=r.unrealized_pnl_usd,
                    total_buy_usd=r.total_buy_usd,
                    multiple=r.multiple,
                )
                for r in rows
            ]
        )
        if score is None:
            continue
        chain = rows[0].chain  # primary chain — first observed
        stmt = (
            pg_insert(WalletPnlScore)
            .values(
                wallet_address=wallet,
                chain=chain,
                last_scored_at=now,
                **score,
            )
            .on_conflict_do_update(
                index_elements=["wallet_address"],
                set_={**score, "last_scored_at": now, "chain": chain},
            )
        )
        await db.execute(stmt)
        refreshed += 1
    await db.commit()
    return refreshed


async def score_candidates_job() -> None:
    if not getattr(app_settings, "discovery_enabled", False):
        return
    async with AsyncSessionLocal() as db:
        try:
            batch = await _pick_next_batch(db)
            if not batch:
                log.info("discovery_score_idle")
                return
            touched_wallets: set[str] = set()
            for token in batch:
                try:
                    upserted = await _score_one_token(db, token)
                    if upserted:
                        wallet_rows = (
                            await db.execute(
                                select(WalletTokenPnl.wallet_address).where(
                                    WalletTokenPnl.chain == token.chain,
                                    WalletTokenPnl.token_address == token.address,
                                )
                            )
                        ).scalars().all()
                        touched_wallets.update(wallet_rows)
                    log.info(
                        "discovery_token_scored",
                        chain=token.chain,
                        token=token.address,
                        symbol=token.symbol,
                        wallet_rows=upserted,
                    )
                except Exception as e:
                    await db.rollback()
                    log.warning(
                        "discovery_token_score_failed",
                        chain=token.chain,
                        token=token.address,
                        err=str(e),
                    )
            refreshed = await _refresh_wallet_scores(db, touched_wallets)
            log.info(
                "discovery_score_tick",
                tokens=len(batch),
                wallets_refreshed=refreshed,
            )
        except Exception as e:
            log.error("discovery_score_tick_failed", err=str(e))


__all__ = ["refresh_candidates_job", "score_candidates_job"]


_ = func  # for static checkers
