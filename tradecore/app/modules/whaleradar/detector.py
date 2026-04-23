"""WhaleRadar — large trades, OI surges, on-chain transfers.

Three independent detectors:
  * large_trade   runs per-symbol, scans the Redis trades stream
  * oi_surge      runs every 5m, compares current OI to previous
  * onchain       polls Whale Alert API every 60s
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.whaleradar import OISurgeEvent, WhaleOnchainTransfer, WhaleTrade
from app.services import redis_service

MIN_TRADE_USD = 300_000.0
LARGE_TRADE_COOLDOWN_MINUTES = 5
OI_SURGE_THRESHOLD_PCT = 5.0
WHALE_ALERT_URL = "https://api.whale-alert.io/v1/transactions"
MIN_ONCHAIN_USD = 500_000

# ---------- large trade ----------


async def scan_large_trades(db: AsyncSession, symbol: str, min_trade_usd: float = MIN_TRADE_USD) -> list[dict]:
    """Look at recent entries in the trades stream and fire for any that clear the threshold."""
    trades = await redis_service.read_trades(symbol, count=100)
    fired: list[dict] = []
    for t in trades:
        try:
            quote_qty = float(t.get("usd") or t.get("quote_qty") or 0)
            price = float(t.get("p") or t.get("price") or 0)
            ts = int(float(t.get("T") or t.get("timestamp") or 0))
        except (TypeError, ValueError):
            continue
        if quote_qty < min_trade_usd:
            continue
        if await redis_service.is_on_cooldown("whaleradar", symbol):
            break
        buyer_maker = str(t.get("m") or t.get("buyer_maker") or "0") in ("true", "1")
        side = "sell" if buyer_maker else "buy"
        detected_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc)
        row = WhaleTrade(
            symbol=symbol,
            trade_size_usd=Decimal(str(round(quote_qty, 2))),
            side=side,
            price=Decimal(str(price)),
            exchange="binance",
            is_futures=True,
            detected_at=detected_at,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        alert = {
            "module": "whaleradar",
            "type": "large_trade",
            "id": str(row.id),
            "symbol": symbol,
            "side": side,
            "trade_size_usd": round(quote_qty, 2),
            "price": price,
            "detected_at": detected_at.isoformat(),
        }
        await redis_service.set_alert_cooldown("whaleradar", symbol, LARGE_TRADE_COOLDOWN_MINUTES)
        await redis_service.publish_alert("whaleradar", alert)
        log.info("whaleradar_large_trade", symbol=symbol, size=round(quote_qty, 2), side=side)
        fired.append(alert)
        break  # one per run, per symbol
    return fired


# ---------- OI surge ----------


async def scan_oi_surges(db: AsyncSession, symbols: list[str] | None = None) -> list[dict]:
    symbols = symbols or await redis_service.get_symbol_list()
    fired: list[dict] = []
    if not symbols:
        return fired
    async with httpx.AsyncClient(timeout=15.0) as client:
        for symbol in symbols:
            try:
                resp = await client.get(
                    f"{app_settings.binance_rest_url}/fapi/v1/openInterest",
                    params={"symbol": symbol},
                )
                resp.raise_for_status()
                data = resp.json()
                oi_contracts = float(data.get("openInterest", 0))
            except Exception as e:  # network, rate-limit, etc.
                log.warning("oi_fetch_failed", symbol=symbol, err=str(e))
                continue

            candle = await redis_service.get_latest_candle(symbol)
            price = float(candle.get("c") or candle.get("close") or 0) if candle else 0.0
            oi_usd = oi_contracts * price
            prev = await redis_service.get_open_interest(symbol)
            await redis_service.set_open_interest(
                symbol,
                {"oi_contracts": oi_contracts, "oi_usd": oi_usd, "price": price},
            )
            if not prev:
                continue
            prev_oi = float(prev.get("oi_usd", 0) or 0)
            if prev_oi <= 0:
                continue
            change_pct = (oi_usd - prev_oi) / prev_oi * 100
            if abs(change_pct) < OI_SURGE_THRESHOLD_PCT:
                continue

            prev_price = float(prev.get("price", price) or price)
            price_change = ((price - prev_price) / prev_price * 100) if prev_price else 0.0
            if change_pct > 0 and price_change >= 0:
                direction = "long_heavy"
            elif change_pct > 0 and price_change < 0:
                direction = "short_heavy"
            elif change_pct < 0 and price_change < 0:
                direction = "oi_unwind"
            else:
                direction = "oi_unwind"

            row = OISurgeEvent(
                symbol=symbol,
                oi_before_usd=Decimal(str(round(prev_oi, 2))),
                oi_after_usd=Decimal(str(round(oi_usd, 2))),
                oi_change_pct=Decimal(str(round(change_pct, 2))),
                price=Decimal(str(price)),
                price_change_pct=Decimal(str(round(price_change, 2))),
                direction=direction,
                detected_at=datetime.now(timezone.utc),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            alert = {
                "module": "whaleradar",
                "type": "oi_surge",
                "id": str(row.id),
                "symbol": symbol,
                "oi_change_pct": round(change_pct, 2),
                "oi_before_usd": round(prev_oi, 2),
                "oi_after_usd": round(oi_usd, 2),
                "price": price,
                "price_change_pct": round(price_change, 2),
                "direction": direction,
                "detected_at": row.detected_at.isoformat(),
            }
            await redis_service.publish_alert("whaleradar", alert)
            log.info(
                "whaleradar_oi_surge",
                symbol=symbol,
                pct=round(change_pct, 2),
                direction=direction,
            )
            fired.append(alert)
    return fired


# ---------- on-chain ----------


async def poll_onchain_transfers(db: AsyncSession) -> list[dict]:
    api_key = getattr(app_settings, "whale_alert_api_key", "") or ""
    if not api_key:
        return []
    params = {
        "api_key": api_key,
        "min_value": MIN_ONCHAIN_USD,
    }
    cursor_key = "whaleradar:onchain:cursor"
    r = redis_service.get_redis()
    cursor = await r.get(cursor_key)
    if cursor:
        params["start"] = int(cursor)
    fired: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(WHALE_ALERT_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("whale_alert_poll_failed", err=str(e))
        return fired
    if data.get("result") != "success":
        return fired
    newest_ts = 0
    for tx in data.get("transactions", []):
        tx_hash = tx.get("hash")
        if not tx_hash:
            continue
        # Dedup by unique tx_hash
        existing = await db.execute(
            select(WhaleOnchainTransfer).where(WhaleOnchainTransfer.tx_hash == tx_hash)
        )
        if existing.scalar_one_or_none() is not None:
            continue
        from_label = (tx.get("from") or {}).get("owner_type") or ""
        to_label = (tx.get("to") or {}).get("owner_type") or ""
        transfer_type = "exchange_inflow" if "exchange" in to_label else (
            "exchange_outflow" if "exchange" in from_label else "wallet_transfer"
        )
        detected_at = datetime.fromtimestamp(int(tx.get("timestamp", 0)), tz=timezone.utc)
        newest_ts = max(newest_ts, int(tx.get("timestamp", 0)))
        row = WhaleOnchainTransfer(
            asset=(tx.get("symbol") or "").upper(),
            amount=Decimal(str(tx.get("amount", 0))),
            amount_usd=Decimal(str(tx.get("amount_usd", 0))),
            from_address=(tx.get("from") or {}).get("address"),
            to_address=(tx.get("to") or {}).get("address"),
            from_label=from_label or None,
            to_label=to_label or None,
            transfer_type=transfer_type,
            tx_hash=tx_hash,
            chain=tx.get("blockchain", "unknown"),
            detected_at=detected_at,
        )
        db.add(row)
        try:
            await db.commit()
        except Exception:  # races on unique tx_hash
            await db.rollback()
            continue
        await db.refresh(row)
        alert = {
            "module": "whaleradar",
            "type": "onchain_transfer",
            "id": str(row.id),
            "asset": row.asset,
            "amount_usd": float(row.amount_usd),
            "transfer_type": transfer_type,
            "chain": row.chain,
            "detected_at": detected_at.isoformat(),
        }
        await redis_service.publish_alert("whaleradar", alert)
        log.info("whaleradar_onchain", asset=row.asset, usd=float(row.amount_usd), type=transfer_type)
        fired.append(alert)
    if newest_ts:
        await r.set(cursor_key, newest_ts)
    return fired


# ---------- convenience wrappers for scheduler ----------


async def run_large_trade_scan() -> None:
    symbols = await redis_service.get_symbol_list()
    if not symbols:
        return
    async with AsyncSessionLocal() as db:
        for symbol in symbols:
            try:
                await scan_large_trades(db, symbol)
            except Exception as e:
                log.error("whaleradar_trade_scan_failed", symbol=symbol, err=str(e))


async def run_oi_surge_scan() -> None:
    async with AsyncSessionLocal() as db:
        try:
            await scan_oi_surges(db)
        except Exception as e:
            log.error("whaleradar_oi_scan_failed", err=str(e))


async def run_onchain_poll() -> None:
    async with AsyncSessionLocal() as db:
        try:
            await poll_onchain_transfers(db)
        except Exception as e:
            log.error("whaleradar_onchain_poll_failed", err=str(e))


__all__ = [
    "scan_large_trades",
    "scan_oi_surges",
    "poll_onchain_transfers",
    "run_large_trade_scan",
    "run_oi_surge_scan",
    "run_onchain_poll",
]

_ = asyncio  # for static checkers
