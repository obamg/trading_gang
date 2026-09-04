"""WalletWatch detector — for every labeled smart-money address, fetch new
DEX swaps from the relevant chain, classify, persist, and alert.

Cursors live in Redis (per chain, per address):
  walletwatch:cursor:ethereum:0x...    last block seen (int)
  walletwatch:cursor:bsc:0x...         last block seen (int)
  walletwatch:cursor:solana:Abc...     last signature seen (str)

Idempotency comes from the DB unique constraint
``UNIQUE(chain, tx_hash, wallet_address)``; cursor is just an optimization to
keep API calls cheap. A new wallet starts at "now − ~1h" instead of genesis.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.walletwatch import WalletSwap
from app.models.whale_entity import WhaleEntity, WhaleEntityAddress
from app.modules.walletwatch import classifier, pricing
from app.modules.walletwatch.chains import arbitrum as arb_chain
from app.modules.walletwatch.chains import base as base_chain
from app.modules.walletwatch.chains import bsc as bsc_chain
from app.modules.walletwatch.chains import ethereum as eth_chain
from app.modules.walletwatch.chains import solana as sol_chain
from app.services import redis_service

CURSOR_KEY = "walletwatch:cursor:{chain}:{addr}"

# Chains a wallet is checked on, by `WhaleEntityAddress.chain`.
CHAIN_HANDLERS = {
    "ethereum": eth_chain,
    "bsc": bsc_chain,
    "arbitrum": arb_chain,
    "base": base_chain,
    "solana": sol_chain,
}


async def _get_cursor(chain: str, addr: str) -> str | None:
    r = redis_service.get_redis()
    return await r.get(CURSOR_KEY.format(chain=chain, addr=addr))


async def _set_cursor(chain: str, addr: str, value: str | int) -> None:
    r = redis_service.get_redis()
    # 30d TTL — long enough that an inactive wallet still resumes correctly.
    await r.set(CURSOR_KEY.format(chain=chain, addr=addr), str(value), ex=30 * 24 * 3600)


async def _list_watched(db: AsyncSession) -> list[tuple[WhaleEntityAddress, WhaleEntity | None]]:
    rows = (
        await db.execute(
            select(WhaleEntityAddress, WhaleEntity)
            .join(
                WhaleEntity, WhaleEntity.id == WhaleEntityAddress.entity_id, isouter=True
            )
            .where(WhaleEntityAddress.is_active.is_(True))
        )
    ).all()
    return [(a, e) for (a, e) in rows]


async def _persist_and_alert(
    db: AsyncSession,
    swap: dict[str, Any],
    entity: WhaleEntity | None,
    min_usd: float,
) -> bool:
    """Returns True if a new row was inserted, False if duplicate."""
    chain = swap["chain"]
    swap_type = classifier.classify_swap(
        chain,
        in_assets=[swap["token_in_address"]],
        out_assets=[swap["token_out_address"]],
    )

    amount_usd = await pricing.estimate_swap_usd(
        chain,
        swap["token_in_address"],
        swap["token_in_amount"],
        swap["token_out_address"],
        swap["token_out_amount"],
    )
    if amount_usd is None:
        # No pricing — skip silently. Better to miss a row than poison the table.
        return False

    stmt = (
        pg_insert(WalletSwap)
        .values(
            wallet_address=swap["wallet_address"],
            entity_id=entity.id if entity else None,
            chain=chain,
            tx_hash=swap["tx_hash"],
            block_number=swap.get("block_number"),
            swap_type=swap_type,
            token_in_address=swap["token_in_address"],
            token_in_symbol=swap.get("token_in_symbol"),
            token_in_amount=swap["token_in_amount"],
            token_out_address=swap["token_out_address"],
            token_out_symbol=swap.get("token_out_symbol"),
            token_out_amount=swap["token_out_amount"],
            amount_usd=Decimal(str(round(amount_usd, 2))),
            venue=swap.get("venue"),
            detected_at=swap["detected_at"],
        )
        .on_conflict_do_nothing(constraint="uq_wallet_swap_tx")
        .returning(WalletSwap.id)
    )
    result = await db.execute(stmt)
    inserted_id = result.scalar_one_or_none()
    if inserted_id is None:
        return False
    await db.commit()

    if (
        amount_usd >= min_usd
        and classifier.is_interesting_buy(chain, swap_type, swap["token_out_address"])
    ):
        alert = {
            "module": "walletwatch",
            "type": "smart_money_buy",
            "id": str(inserted_id),
            "wallet": swap["wallet_address"],
            "entity_name": entity.name if entity else None,
            "entity_conviction": float(entity.conviction_score)
            if entity and entity.conviction_score is not None
            else None,
            "chain": chain,
            "venue": swap.get("venue"),
            "token_out_symbol": swap.get("token_out_symbol"),
            "token_out_address": swap["token_out_address"],
            "token_in_symbol": swap.get("token_in_symbol"),
            "amount_usd": round(amount_usd, 2),
            "tx_hash": swap["tx_hash"],
            "detected_at": swap["detected_at"].isoformat(),
        }
        await redis_service.publish_alert("walletwatch", alert)
        log.info(
            "walletwatch_smart_money_buy",
            wallet=swap["wallet_address"],
            chain=chain,
            token=swap.get("token_out_symbol") or swap["token_out_address"],
            usd=round(amount_usd, 2),
        )
    return True


async def _scan_one(
    db: AsyncSession, addr_row: WhaleEntityAddress, entity: WhaleEntity | None, min_usd: float
) -> int:
    chain = (addr_row.chain or "").lower()
    handler = CHAIN_HANDLERS.get(chain)
    if handler is None:
        return 0
    addr = addr_row.address
    cursor = await _get_cursor(chain, addr)

    if chain == "solana":
        swaps, new_cursor = await handler.fetch_swaps(addr, cursor)
    else:
        try:
            from_block = int(cursor) if cursor else 0
        except (TypeError, ValueError):
            from_block = 0
        swaps, new_cursor = await handler.fetch_swaps(addr, from_block)

    inserted = 0
    for swap in swaps:
        try:
            if await _persist_and_alert(db, swap, entity, min_usd):
                inserted += 1
        except Exception as e:
            await db.rollback()
            log.warning(
                "walletwatch_persist_failed",
                wallet=addr,
                chain=chain,
                tx=swap.get("tx_hash"),
                err=str(e),
            )

    if new_cursor is not None and str(new_cursor) != str(cursor or ""):
        await _set_cursor(chain, addr, new_cursor)
    return inserted


async def scan_all(db: AsyncSession) -> dict[str, int]:
    if not getattr(app_settings, "walletwatch_enabled", False):
        return {"skipped": 1}
    min_usd = float(getattr(app_settings, "walletwatch_min_usd", 25_000.0))
    watched = await _list_watched(db)
    if not watched:
        return {"watched": 0, "inserted": 0}
    total_inserted = 0
    for addr_row, entity in watched:
        try:
            total_inserted += await _scan_one(db, addr_row, entity, min_usd)
        except Exception as e:
            log.error(
                "walletwatch_scan_one_failed",
                wallet=addr_row.address,
                chain=addr_row.chain,
                err=str(e),
            )
    return {"watched": len(watched), "inserted": total_inserted}


async def run_walletwatch_scan() -> None:
    """Scheduler entrypoint. Swallows + logs exceptions per project convention."""
    async with AsyncSessionLocal() as db:
        try:
            result = await scan_all(db)
            log.info("walletwatch_tick", **result)
        except Exception as e:
            log.error("walletwatch_tick_failed", err=str(e))


__all__ = ["scan_all", "run_walletwatch_scan"]
