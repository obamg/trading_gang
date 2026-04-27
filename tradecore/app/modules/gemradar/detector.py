"""GemRadar — small-cap DEX scanner + CEX listing feed.

Scans DexScreener every 2 minutes for pairs in the configured mcap band that
show large price & volume moves relative to market cap. Flagged pairs are
put through a rug check (RugCheck for Solana tokens — best effort) to produce
a risk score.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.gemradar import GemRadarAlert
from app.services import redis_service

DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
GECKOTERMINAL_TRENDING = "https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools"
GECKO_CHAIN_MAP = {"solana": "solana", "ethereum": "eth", "bsc": "bsc"}
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
BINANCE_ANNOUNCEMENTS = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
    "?catalogId=48&pageNo=1&pageSize=10"
)

MIN_MCAP_USD = 500_000
MAX_MCAP_USD = 100_000_000
PRICE_CHANGE_THRESHOLD = 3.0  # percent in 5m
VOLUME_MCAP_RATIO_THRESHOLD = 1.0  # percent of mcap traded in 5m
COOLDOWN_MINUTES = 60
CHAINS = ("solana", "ethereum", "bsc")


def _score_to_label(score: int) -> str:
    if score <= 30:
        return "low"
    if score <= 50:
        return "medium"
    if score <= 75:
        return "high"
    return "extreme"


async def _risk_check(chain: str, address: str) -> dict[str, Any]:
    """Best-effort rug check. Returns a dict with the fields we persist.

    Non-Solana chains fall back to a conservative baseline score since
    RugCheck is Solana-only.
    """
    default = {
        "is_contract_verified": None,
        "is_liquidity_locked": None,
        "has_mint_function": None,
        "top10_wallet_pct": None,
        "contract_age_hours": None,
        "risk_score_numeric": 50,
        "risk_flags": [],
    }
    if chain != "solana" or not address:
        return default
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(RUGCHECK_URL.format(mint=address))
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("rugcheck_failed", mint=address, err=str(e))
        return default

    verified = bool(data.get("verification", {}).get("jup_verified", False))
    # RugCheck shape is inconsistent; pull defensively.
    markets = data.get("markets", []) or []
    liq_locked = any(m.get("lp", {}).get("lpLocked", 0) > 0 for m in markets) if markets else False
    mint_active = bool(data.get("token", {}).get("mintAuthority"))
    top_holders = data.get("topHolders") or []
    top10 = sum((h.get("pct") or 0.0) for h in top_holders[:10])
    risk_flags = []
    score = 0
    if not verified:
        score += 30
        risk_flags.append("unverified_contract")
    if not liq_locked:
        score += 25
        risk_flags.append("liquidity_unlocked")
    if mint_active:
        score += 20
        risk_flags.append("mint_active")
    if top10 > 70:
        score += 15
        risk_flags.append("concentrated_holders")
    # Age heuristic — if RugCheck reports creation, compute hours
    age_hours: int | None = None
    created = data.get("detectedAt") or data.get("created_at")
    if created:
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            age_hours = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() / 3600))
            if age_hours < 24:
                score += 10
                risk_flags.append("young_contract")
        except ValueError:
            age_hours = None

    return {
        "is_contract_verified": verified,
        "is_liquidity_locked": liq_locked,
        "has_mint_function": mint_active,
        "top10_wallet_pct": round(top10, 2) if top_holders else None,
        "contract_age_hours": age_hours,
        "risk_score_numeric": min(100, score),
        "risk_flags": risk_flags,
    }


async def _fetch_trending(chain: str) -> list[dict]:
    """Fetch trending pools from GeckoTerminal for a given chain."""
    network = GECKO_CHAIN_MAP.get(chain, chain)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                GECKOTERMINAL_TRENDING.format(network=network),
                params={"page": 1},
            )
            resp.raise_for_status()
            pools = resp.json().get("data", []) or []
            results = []
            for p in pools:
                attr = p.get("attributes") or {}
                name = attr.get("name", "")
                token_symbol = name.split(" / ")[0].strip() if " / " in name else name
                mcap = float(attr.get("market_cap_usd") or attr.get("fdv_usd") or 0)
                pct = attr.get("price_change_percentage") or {}
                vol = attr.get("volume_usd") or {}
                results.append({
                    "chain": chain,
                    "symbol": token_symbol.upper(),
                    "name": name,
                    "address": attr.get("address", ""),
                    "mcap": mcap,
                    "price_usd": float(attr.get("base_token_price_usd") or 0),
                    "price_change_5m": float(pct.get("m5") or 0),
                    "price_change_1h": float(pct.get("h1") or 0),
                    "price_change_24h": float(pct.get("h24") or 0),
                    "volume_5m": float(vol.get("m5") or 0),
                    "volume_1h": float(vol.get("h1") or 0),
                    "volume_24h": float(vol.get("h24") or 0),
                    "dex": attr.get("dex_id", ""),
                })
            return results
    except Exception as e:
        log.warning("geckoterminal_failed", chain=chain, err=str(e))
        return []


async def scan(db: AsyncSession) -> list[dict]:
    fired: list[dict] = []
    seen_symbols: set[str] = set()

    for chain in CHAINS:
        pools = await _fetch_trending(chain)
        for p in pools:
            symbol = p["symbol"]
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)

            mcap = p["mcap"]
            if mcap < MIN_MCAP_USD or mcap > MAX_MCAP_USD:
                continue

            price_change_5m = abs(p["price_change_5m"])
            volume_5m = p["volume_5m"]
            vol_mcap_ratio = (volume_5m / mcap * 100) if mcap > 0 else 0

            if price_change_5m < PRICE_CHANGE_THRESHOLD and vol_mcap_ratio < VOLUME_MCAP_RATIO_THRESHOLD:
                continue

            if await redis_service.is_on_cooldown("gemradar", symbol):
                continue

            address = p.get("address", "")
            risk = await _risk_check(chain, address)
            risk_label = _score_to_label(int(risk["risk_score_numeric"]))

            detected_at = datetime.now(timezone.utc)
            row = GemRadarAlert(
                symbol=symbol,
                name=p.get("name"),
                contract_address=address or None,
                chain=chain,
                dex=p.get("dex"),
                is_cex_listed=False,
                price_usd=Decimal(str(p["price_usd"])),
                price_change_pct=Decimal(str(round(p["price_change_5m"], 2))),
                price_change_period_min=5,
                volume_usd_current=Decimal(str(round(volume_5m, 2))),
                volume_mcap_ratio=Decimal(str(round(vol_mcap_ratio / 100, 4))) if mcap else None,
                market_cap_usd=Decimal(str(round(mcap, 2))),
                risk_score=risk_label,
                risk_score_numeric=int(risk["risk_score_numeric"]),
                is_contract_verified=risk["is_contract_verified"],
                is_liquidity_locked=risk["is_liquidity_locked"],
                has_mint_function=risk["has_mint_function"],
                top10_wallet_pct=Decimal(str(risk["top10_wallet_pct"])) if risk["top10_wallet_pct"] is not None else None,
                contract_age_hours=risk["contract_age_hours"],
                risk_flags=risk["risk_flags"],
                detected_at=detected_at,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)

            alert = {
                "module": "gemradar",
                "type": "small_cap_move",
                "id": str(row.id),
                "symbol": symbol,
                "chain": chain,
                "price_usd": p["price_usd"],
                "price_change_pct": round(p["price_change_5m"], 2),
                "market_cap_usd": round(mcap, 2),
                "volume_5m_usd": round(volume_5m, 2),
                "risk_score": risk_label,
                "risk_score_numeric": int(risk["risk_score_numeric"]),
                "risk_flags": risk["risk_flags"],
                "detected_at": detected_at.isoformat(),
            }
            await redis_service.set_alert_cooldown("gemradar", symbol, COOLDOWN_MINUTES)
            await redis_service.publish_alert("gemradar", alert)
            log.info(
                "gemradar_alert",
                symbol=symbol,
                chain=chain,
                risk=risk_label,
                mcap=int(mcap),
                pct=round(p["price_change_5m"], 2),
            )
            fired.append(alert)
    return fired


async def scan_cex_listings(db: AsyncSession) -> list[dict]:
    """Poll Binance announcements for 'will list' headlines."""
    r = redis_service.get_redis()
    seen_key = "gemradar:binance_announcements_seen"
    fired: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(BINANCE_ANNOUNCEMENTS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("binance_announcements_failed", err=str(e))
        return fired

    articles = (
        ((data.get("data") or {}).get("articles")) or []
    )
    for art in articles:
        title = (art.get("title") or "").strip()
        code = str(art.get("code") or art.get("id") or title)
        if not title or not code:
            continue
        if "will list" not in title.lower() and "will add" not in title.lower():
            continue
        if await r.sismember(seen_key, code):
            continue
        await r.sadd(seen_key, code)
        await r.expire(seen_key, 30 * 24 * 3600)

        row = GemRadarAlert(
            symbol=title[:40],
            name=title,
            is_cex_listed=True,
            cex_name="binance",
            risk_score="low",
            risk_flags=["cex_listing"],
            detected_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        alert = {
            "module": "gemradar",
            "type": "cex_listing",
            "id": str(row.id),
            "cex_name": "binance",
            "title": title,
            "detected_at": row.detected_at.isoformat(),
        }
        await redis_service.publish_alert("gemradar", alert)
        log.info("gemradar_cex_listing", title=title)
        fired.append(alert)
    return fired


# ---------- scheduler wrappers ----------


async def run_scan() -> None:
    async with AsyncSessionLocal() as db:
        try:
            await scan(db)
        except Exception as e:
            log.error("gemradar_scan_failed", err=str(e))


async def run_cex_listing_scan() -> None:
    async with AsyncSessionLocal() as db:
        try:
            await scan_cex_listings(db)
        except Exception as e:
            log.error("gemradar_cex_scan_failed", err=str(e))


__all__ = ["scan", "scan_cex_listings", "run_scan", "run_cex_listing_scan"]
