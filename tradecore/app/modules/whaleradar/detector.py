"""WhaleRadar — large trades, OI surges, on-chain transfers, slow OI accumulation.

Detectors:
  * large_trade           runs per-symbol, scans the Redis trades stream
  * oi_surge              runs every 5m, compares current OI to previous (fast moves)
  * slow_oi_accumulation  runs every 15m, 4h vs 12h z-score on flat price (predictive)
  * onchain               polls Whale Alert API every 60s
"""
from __future__ import annotations

import asyncio
import json
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.whale_entity import WhaleEntity, WhaleEntityAddress
from app.models.whaleradar import OISurgeEvent, WhaleOnchainTransfer, WhaleTrade
from app.services import redis_service, thresholds

MIN_TRADE_USD = 300_000.0
LARGE_TRADE_COOLDOWN_MINUTES = 5
OI_SURGE_THRESHOLD_PCT = 5.0
WHALE_ALERT_URL = "https://api.whale-alert.io/v1/transactions"
MIN_ONCHAIN_USD = 500_000

# Slow OI accumulation: predictive of breakouts when OI builds on flat price
SLOW_OI_LOOKBACK_MINUTES = 240        # 4h "current" window
SLOW_OI_BASELINE_MINUTES = 12 * 60    # 12h baseline for z-score
SLOW_OI_MIN_SAMPLES = 12              # need ~3h of 15-min samples
SLOW_OI_Z_THRESHOLD = 2.0
SLOW_OI_MAX_PRICE_PCT = 1.5           # price must be flat (<1.5% over the window)
SLOW_OI_ALERT_COOLDOWN_MIN = 60

# ---------- large trade ----------


async def _resolve_min_trade_usd(symbol: str, fallback: float) -> float:
    """Per-symbol p99 of recent trade sizes (or fallback when not enough data)."""
    p99 = await thresholds.get_percentile(symbol, "trade_size_usd", 99.0, fallback=None)
    if p99 is None:
        return fallback
    # Clamp to a sane band: never lower than 50k, never above 5x the global default
    return max(50_000.0, min(p99, fallback * 5))


async def scan_large_trades(db: AsyncSession, symbol: str, min_trade_usd: float = MIN_TRADE_USD) -> list[dict]:
    """Look at recent entries in the trades stream and fire for any that clear the threshold."""
    trades = await redis_service.read_trades(symbol, count=100)
    fired: list[dict] = []
    dynamic_min = await _resolve_min_trade_usd(symbol, min_trade_usd)
    for t in trades:
        try:
            quote_qty = float(t.get("usd") or t.get("quote_qty") or 0)
            price = float(t.get("p") or t.get("price") or 0)
            ts = int(float(t.get("T") or t.get("timestamp") or 0))
        except (TypeError, ValueError):
            continue
        if quote_qty > 0:
            await thresholds.add_sample(symbol, "trade_size_usd", quote_qty)
        if quote_qty < dynamic_min:
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
            elif change_pct < 0 and price_change >= 0:
                direction = "oi_unwind_bullish"
            else:
                direction = "oi_unwind_bearish"

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
        entity_info = await _resolve_entity(db, row.from_address, row.to_address)
        alert = {
            "module": "whaleradar",
            "type": "onchain_transfer",
            "id": str(row.id),
            "asset": row.asset,
            "amount_usd": float(row.amount_usd),
            "transfer_type": transfer_type,
            "chain": row.chain,
            "detected_at": detected_at.isoformat(),
            **entity_info,
        }
        await redis_service.publish_alert("whaleradar", alert)
        log.info("whaleradar_onchain", asset=row.asset, usd=float(row.amount_usd), type=transfer_type)
        fired.append(alert)
    if newest_ts:
        await r.set(cursor_key, newest_ts)
    return fired


# ---------- entity resolution ----------


async def _resolve_entity(db: AsyncSession, from_addr: str | None, to_addr: str | None) -> dict:
    """Look up known whale entities by address. Returns enrichment fields."""
    for addr in (from_addr, to_addr):
        if not addr:
            continue
        result = await db.execute(
            select(WhaleEntityAddress).where(WhaleEntityAddress.address == addr)
        )
        link = result.scalar_one_or_none()
        if link is None:
            continue
        entity_result = await db.execute(
            select(WhaleEntity).where(WhaleEntity.id == link.entity_id)
        )
        entity = entity_result.scalar_one_or_none()
        if entity:
            entity.total_transfers += 1
            entity.last_seen_at = datetime.now(timezone.utc)
            await db.commit()
            return {
                "entity_name": entity.name,
                "entity_type": entity.entity_type,
                "entity_conviction": float(entity.conviction_score) if entity.conviction_score else None,
            }
    return {}


# ---------- accumulation pattern detection ----------

ACCUMULATION_LOOKBACK_MINUTES = 60
ACCUMULATION_MIN_TRADES = 3


async def detect_accumulation_pattern(db: AsyncSession, symbol: str) -> dict | None:
    """Detect if recent whale trades show accumulation or distribution."""
    from sqlalchemy import desc as sql_desc
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ACCUMULATION_LOOKBACK_MINUTES)
    trades = (
        await db.execute(
            select(WhaleTrade)
            .where(WhaleTrade.symbol == symbol, WhaleTrade.detected_at >= cutoff)
            .order_by(sql_desc(WhaleTrade.detected_at))
        )
    ).scalars().all()

    if len(trades) < ACCUMULATION_MIN_TRADES:
        return None

    buy_count = sum(1 for t in trades if t.side == "buy")
    sell_count = len(trades) - buy_count
    total_buy_usd = sum(float(t.trade_size_usd) for t in trades if t.side == "buy")
    total_sell_usd = sum(float(t.trade_size_usd) for t in trades if t.side == "sell")

    if buy_count >= ACCUMULATION_MIN_TRADES and buy_count > sell_count:
        return {
            "symbol": symbol,
            "pattern": "accumulation",
            "side": "buy",
            "trade_count": buy_count,
            "total_usd": round(total_buy_usd, 2),
            "conviction": min(buy_count / 5.0, 1.0),
        }
    elif sell_count >= ACCUMULATION_MIN_TRADES and sell_count > buy_count:
        return {
            "symbol": symbol,
            "pattern": "distribution",
            "side": "sell",
            "trade_count": sell_count,
            "total_usd": round(total_sell_usd, 2),
            "conviction": min(sell_count / 5.0, 1.0),
        }
    return None


# ---------- slow OI accumulation ----------


async def scan_slow_oi_accumulation(symbols: list[str] | None = None) -> list[dict]:
    """Detect slow OI buildup over hours on flat price — leads breakouts.

    Samples OI every 15 min into a Redis sorted set, computes 4h z-score
    against the 12h baseline, and fires when |z| > 2 and price is flat.
    Persists the latest signal to ``slow_oi:{symbol}`` for oracle to read.
    """
    symbols = symbols or await redis_service.get_symbol_list()
    fired: list[dict] = []
    if not symbols:
        return fired
    r = redis_service.get_redis()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - SLOW_OI_BASELINE_MINUTES * 60 * 1000

    async with httpx.AsyncClient(timeout=15.0) as client:
        for symbol in symbols:
            try:
                resp = await client.get(
                    f"{app_settings.binance_rest_url}/fapi/v1/openInterest",
                    params={"symbol": symbol},
                )
                resp.raise_for_status()
                oi_contracts = float(resp.json().get("openInterest", 0) or 0)
            except Exception as e:
                log.debug("slow_oi_fetch_failed", symbol=symbol, err=str(e))
                continue

            candle = await redis_service.get_latest_candle(symbol)
            price = float(candle.get("c") or candle.get("close") or 0) if candle else 0.0
            if price <= 0 or oi_contracts <= 0:
                continue
            oi_usd = oi_contracts * price

            history_key = f"oi_history:{symbol}"
            sample_member = f"{now_ms}:{oi_usd:.2f}:{price:.8f}"
            await r.zadd(history_key, {sample_member: now_ms})
            await r.zremrangebyscore(history_key, 0, cutoff_ms - 1)
            await r.expire(history_key, SLOW_OI_BASELINE_MINUTES * 60 + 600)

            raw = await r.zrange(history_key, 0, -1, withscores=True)
            samples: list[tuple[float, float, float]] = []
            for member, score in raw:
                parts = member.split(":")
                if len(parts) < 3:
                    continue
                try:
                    samples.append((float(score), float(parts[1]), float(parts[2])))
                except (ValueError, TypeError):
                    continue
            if len(samples) < SLOW_OI_MIN_SAMPLES:
                continue

            window_cut = now_ms - SLOW_OI_LOOKBACK_MINUTES * 60 * 1000
            window = [s for s in samples if s[0] >= window_cut]
            baseline = [s for s in samples if s[0] < window_cut]
            if len(window) < 4 or len(baseline) < 4:
                continue

            baseline_oi = [s[1] for s in baseline]
            mean_b = statistics.fmean(baseline_oi)
            try:
                std_b = statistics.pstdev(baseline_oi)
            except statistics.StatisticsError:
                std_b = 0.0
            if std_b <= 0 or mean_b <= 0:
                continue

            current_oi = window[-1][1]
            z = (current_oi - mean_b) / std_b

            window_prices = [s[2] for s in window if s[2] > 0]
            if not window_prices:
                continue
            price_change_pct = (window_prices[-1] - window_prices[0]) / window_prices[0] * 100

            if abs(z) < SLOW_OI_Z_THRESHOLD or abs(price_change_pct) > SLOW_OI_MAX_PRICE_PCT:
                continue

            if z > 0:
                if price_change_pct > 0.3:
                    direction = "bullish_buildup"
                elif price_change_pct < -0.3:
                    direction = "bearish_buildup"
                else:
                    direction = "neutral_buildup"
            else:
                direction = "unwinding"

            payload = {
                "symbol": symbol,
                "z_score": round(z, 2),
                "current_oi_usd": round(current_oi, 2),
                "baseline_mean_usd": round(mean_b, 2),
                "price_change_pct": round(price_change_pct, 3),
                "direction": direction,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
            await r.set(f"slow_oi:{symbol}", json.dumps(payload), ex=900)

            if not await redis_service.is_on_cooldown("whaleradar_slow_oi", symbol):
                alert = {"module": "whaleradar", "type": "slow_oi_accumulation", **payload}
                await redis_service.publish_alert("whaleradar", alert)
                await redis_service.set_alert_cooldown(
                    "whaleradar_slow_oi", symbol, SLOW_OI_ALERT_COOLDOWN_MIN
                )
                log.info(
                    "whaleradar_slow_oi",
                    symbol=symbol,
                    z=round(z, 2),
                    price_pct=round(price_change_pct, 2),
                    direction=direction,
                )
                fired.append(alert)

    return fired


async def get_slow_oi_signal(symbol: str) -> dict | None:
    r = redis_service.get_redis()
    raw = await r.get(f"slow_oi:{symbol}")
    return json.loads(raw) if raw else None


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


async def run_slow_oi_scan() -> None:
    try:
        await scan_slow_oi_accumulation()
    except Exception as e:
        log.error("whaleradar_slow_oi_scan_failed", err=str(e))


__all__ = [
    "scan_large_trades",
    "scan_oi_surges",
    "scan_slow_oi_accumulation",
    "poll_onchain_transfers",
    "get_slow_oi_signal",
    "run_large_trade_scan",
    "run_oi_surge_scan",
    "run_slow_oi_scan",
    "run_onchain_poll",
]

_ = asyncio  # for static checkers
