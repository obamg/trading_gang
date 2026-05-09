"""Bulk-import labeled smart-money wallets from a CSV file.

CSV format (header required):
    name,entity_type,address,chain,label,conviction_score
    Wintermute OTC,desk,0x4f3a120E72C76c22ae802D129F599BFDbc31cb81,ethereum,Wintermute hot 1,0.80
    GSR,desk,0x...,ethereum,GSR settlement,
    Lookonchain SM #14,smart_money,0x...,ethereum,,0.65
    JumpTrading SOL,desk,Abc...,solana,Jump 1,0.75

Columns:
  - name              required, unique per WhaleEntity (one entity → many addresses)
  - entity_type       required, e.g. desk, fund, smart_money, mm, otc, individual
  - address           required, the on-chain address (0x... for EVM, base58 for Solana)
  - chain             required, one of: ethereum, bsc, arbitrum, base, solana
  - label             optional, free-form tag for the address ("hot wallet 1", etc.)
  - conviction_score  optional, 0..1 — used by the alert payload as a quality signal

Idempotent: re-running the same CSV is a no-op. Existing entities are reused
(matched by name); existing addresses are skipped (matched by address+chain).

Run with:
    docker compose exec api python -m app.scripts.import_wallets /path/to/wallets.csv
or (locally in the api container):
    python -m app.scripts.import_wallets wallets.csv
"""
from __future__ import annotations

import asyncio
import csv
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.logging_config import configure_logging, log
from app.models.whale_entity import WhaleEntity, WhaleEntityAddress

VALID_CHAINS = {"ethereum", "bsc", "arbitrum", "base", "solana"}
REQUIRED_COLS = {"name", "entity_type", "address", "chain"}


def _normalize_address(chain: str, addr: str) -> str:
    """EVM addresses are case-insensitive — store lower-case for dedup. Solana
    base58 mints are case-sensitive — preserve as-is."""
    addr = (addr or "").strip()
    if chain == "solana":
        return addr
    return addr.lower()


async def _import(path: Path) -> dict[str, int]:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    stats = {"rows": 0, "entities_created": 0, "addresses_added": 0, "skipped": 0}

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV missing required columns: {sorted(missing)}")
        rows = list(reader)

    async with AsyncSessionLocal() as db:
        # Cache entity-by-name to avoid one query per row when the CSV groups
        # multiple wallets under the same desk.
        entity_cache: dict[str, WhaleEntity] = {}

        for row in rows:
            stats["rows"] += 1
            name = (row.get("name") or "").strip()
            entity_type = (row.get("entity_type") or "").strip()
            chain = (row.get("chain") or "").strip().lower()
            address = _normalize_address(chain, row.get("address") or "")
            label = (row.get("label") or "").strip() or None
            conviction_raw = (row.get("conviction_score") or "").strip()

            if not (name and entity_type and chain and address):
                log.warning("import_wallets_skip_missing_field", row=row)
                stats["skipped"] += 1
                continue
            if chain not in VALID_CHAINS:
                log.warning(
                    "import_wallets_skip_unknown_chain",
                    chain=chain,
                    valid=sorted(VALID_CHAINS),
                )
                stats["skipped"] += 1
                continue

            # Resolve / create the entity by name (case-sensitive — matches DB unique).
            entity = entity_cache.get(name)
            if entity is None:
                entity = (
                    await db.execute(select(WhaleEntity).where(WhaleEntity.name == name))
                ).scalar_one_or_none()
                if entity is None:
                    conviction: Decimal | None = None
                    if conviction_raw:
                        try:
                            conviction = Decimal(conviction_raw)
                        except Exception:
                            log.warning(
                                "import_wallets_bad_conviction",
                                name=name,
                                value=conviction_raw,
                            )
                    entity = WhaleEntity(
                        name=name,
                        entity_type=entity_type,
                        conviction_score=conviction,
                    )
                    db.add(entity)
                    await db.flush()  # populate entity.id
                    stats["entities_created"] += 1
                entity_cache[name] = entity

            # Skip if this address is already linked anywhere (unique constraint).
            existing = (
                await db.execute(
                    select(WhaleEntityAddress).where(
                        WhaleEntityAddress.address == address
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                stats["skipped"] += 1
                continue

            db.add(
                WhaleEntityAddress(
                    entity_id=entity.id,
                    address=address,
                    chain=chain,
                    label=label,
                )
            )
            stats["addresses_added"] += 1

        await db.commit()

    return stats


async def main() -> None:
    configure_logging()
    if len(sys.argv) != 2:
        print("usage: python -m app.scripts.import_wallets <wallets.csv>", file=sys.stderr)
        raise SystemExit(2)
    stats = await _import(Path(sys.argv[1]))
    log.info("import_wallets_done", **stats)
    print(
        f"Imported: {stats['rows']} rows, "
        f"{stats['entities_created']} new entities, "
        f"{stats['addresses_added']} new addresses, "
        f"{stats['skipped']} skipped."
    )


if __name__ == "__main__":
    asyncio.run(main())
