"""One-shot cleanup: rebuild ``wallet_pnl_score`` rows without BTC/ETH majors.

After excluding WBTC/WETH/cbBTC/cbETH/WBNB/BTCB/WSOL etc. from the discovery
scorer, existing ``wallet_pnl_score`` rows still hold pre-filter values until
the next time ``score_candidates_job`` touches each wallet. Wallets whose
history is *only* major tokens would never be re-touched and would keep a
stale, inflated score forever.

This script visits every ``wallet_pnl_score`` row, recomputes from
``wallet_token_pnl`` with majors excluded, and either updates or deletes.

Run with:
    docker compose exec api python -m app.scripts.cleanup_walletwatch_majors
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.logging_config import configure_logging, log
from app.models.walletwatch_discovery import WalletPnlScore, WalletTokenPnl
from app.modules.walletwatch import classifier
from app.modules.walletwatch.discovery.scorer import TokenPnlRow, compute_wallet_score


async def main() -> None:
    configure_logging()
    majors = classifier.all_major_addresses()
    deleted = 0
    rescored = 0
    unchanged = 0

    async with AsyncSessionLocal() as db:
        wallets = (
            await db.execute(select(WalletPnlScore.wallet_address))
        ).scalars().all()
        now = datetime.now(timezone.utc)

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
                await db.execute(
                    delete(WalletPnlScore).where(
                        WalletPnlScore.wallet_address == wallet
                    )
                )
                deleted += 1
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
                await db.execute(
                    delete(WalletPnlScore).where(
                        WalletPnlScore.wallet_address == wallet
                    )
                )
                deleted += 1
                continue

            chain = rows[0].chain
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
            rescored += 1

        await db.commit()

    log.info(
        "walletwatch_majors_cleanup_done",
        rescored=rescored,
        deleted=deleted,
        unchanged=unchanged,
        total=rescored + deleted,
    )


if __name__ == "__main__":
    asyncio.run(main())
