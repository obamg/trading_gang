"""Sync orchestrator: credential → adapter → fills → pairing → upsert.

The orchestrator is exchange-agnostic. It looks up the adapter from the
registry by `credential.exchange`, so adding OKX or Bitget later is a
single new file in this package, not a change here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import log
from app.models.exchange import ExchangeCredential
from app.services.exchanges import binance, bybit  # noqa: F401  - register adapters
from app.services.exchanges.base import get_adapter
from app.services.exchanges.credentials import get_credential, load_credentials
from app.services.exchanges.pairing import pair_fills
from app.services.exchanges.upsert import upsert_trades

INITIAL_BACKFILL_DAYS = 90
RESYNC_OVERLAP_DAYS = 1  # re-pull last 24h on every sync to catch late settlements


async def sync_credential(db: AsyncSession, user_id: UUID, cred_id: UUID) -> dict:
    """Run a full sync for one credential.

    Returns a dict with counters and last-sync metadata. Updates the credential
    row with `last_synced_at` / `last_sync_error`.
    """
    cred = await get_credential(db, user_id, cred_id)
    if cred is None:
        return {"ok": False, "reason": "credential_not_found"}
    if not cred.is_active:
        return {"ok": False, "reason": "credential_inactive"}

    return await _run_sync(db, cred)


async def _run_sync(db: AsyncSession, cred: ExchangeCredential) -> dict:
    adapter = get_adapter(cred.exchange)
    creds_plain = load_credentials(cred)

    if cred.last_synced_at is not None:
        since = cred.last_synced_at - timedelta(days=RESYNC_OVERLAP_DAYS)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=INITIAL_BACKFILL_DAYS)

    try:
        fills = await adapter.fetch_fills(creds_plain, since=since)
    except Exception as exc:
        cred.last_sync_error = str(exc)[:500]
        await db.commit()
        log.error("exchange_sync_failed", cred_id=str(cred.id), err=str(exc))
        return {"ok": False, "reason": "fetch_failed", "error": str(exc)}

    records = pair_fills(fills)
    counters = await upsert_trades(db, cred.user_id, records)

    cred.last_synced_at = datetime.now(timezone.utc)
    cred.last_sync_error = None
    await db.commit()

    log.info(
        "exchange_sync_completed",
        cred_id=str(cred.id),
        exchange=cred.exchange,
        fills=len(fills),
        paired=len(records),
        **counters,
    )
    return {
        "ok": True,
        "fills_fetched": len(fills),
        "trades_paired": len(records),
        "trades_inserted": counters["inserted"],
        "trades_skipped": counters["skipped"],
        "synced_at": cred.last_synced_at.isoformat(),
    }


async def sync_all_active_credentials(db: AsyncSession) -> dict:
    """Iterate every active credential and sync it. Used by the scheduler.

    One failure does not abort the loop; the credential's last_sync_error
    field captures it.
    """
    from sqlalchemy import select

    rows = (
        await db.execute(
            select(ExchangeCredential).where(ExchangeCredential.is_active.is_(True))
        )
    ).scalars().all()

    totals = {"credentials": 0, "ok": 0, "failed": 0}
    for cred in rows:
        totals["credentials"] += 1
        try:
            result = await _run_sync(db, cred)
            totals["ok" if result.get("ok") else "failed"] += 1
        except Exception as exc:
            totals["failed"] += 1
            log.error("exchange_sync_unexpected", cred_id=str(cred.id), err=str(exc))
    return totals


__all__ = ["sync_credential", "sync_all_active_credentials"]
