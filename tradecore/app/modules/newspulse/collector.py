"""NewsPulse — crypto news aggregator.

Fetches news from CoinGecko News API every 5 minutes. Stores articles in DB,
publishes high-impact items via Redis pubsub and Telegram.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.news import NewsArticle
from app.services import redis_service

COINGECKO_NEWS_URL = "https://api.coingecko.com/api/v3/news"

BULLISH_WORDS = {
    "surge", "surges", "surging", "soar", "soars", "soaring", "rally", "rallies",
    "bullish", "breakout", "pump", "moon", "all-time high", "ath", "gain",
    "gains", "record high", "adoption", "upgrade", "approval", "approved",
    "partnership", "launch", "launches", "milestone", "inflow", "inflows",
    "accumulate", "accumulation", "buy", "buying",
}
BEARISH_WORDS = {
    "crash", "crashes", "crashing", "plunge", "plunges", "plunging", "dump",
    "bearish", "selloff", "sell-off", "liquidation", "liquidated", "hack",
    "hacked", "exploit", "exploited", "ban", "bans", "banned", "scam",
    "fraud", "outflow", "outflows", "decline", "declining", "drop", "drops",
    "slump", "fear", "panic", "collapse", "rug", "rugged", "lawsuit", "sec",
    "crackdown", "warning", "risk",
}
HIGH_IMPACT_WORDS = {
    "sec", "etf", "fed", "federal reserve", "regulation", "regulatory",
    "ban", "hack", "exploit", "billion", "crash", "all-time high",
    "approval", "halving", "fork", "blackrock", "grayscale", "binance",
    "coinbase", "breaking",
}

COIN_PATTERNS = re.compile(
    r'\b(BTC|ETH|SOL|XRP|ADA|DOGE|AVAX|DOT|MATIC|LINK|UNI|AAVE|'
    r'Bitcoin|Ethereum|Solana|Ripple|Cardano|Dogecoin|Avalanche|Polkadot|'
    r'Polygon|Chainlink|Uniswap|Aave)\b',
    re.IGNORECASE,
)
COIN_NORMALIZE = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP",
    "cardano": "ADA", "dogecoin": "DOGE", "avalanche": "AVAX",
    "polkadot": "DOT", "polygon": "MATIC", "chainlink": "LINK",
    "uniswap": "UNI", "aave": "AAVE",
}


def _score_sentiment(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    bull = sum(1 for w in BULLISH_WORDS if w in text)
    bear = sum(1 for w in BEARISH_WORDS if w in text)
    if bull > bear and bull >= 1:
        return "bullish"
    if bear > bull and bear >= 1:
        return "bearish"
    return "neutral"


def _score_importance(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    hits = sum(1 for w in HIGH_IMPACT_WORDS if w in text)
    return "high" if hits >= 1 else "normal"


def _extract_coins(title: str, description: str) -> list[str]:
    text = f"{title} {description}"
    matches = COIN_PATTERNS.findall(text)
    seen: set[str] = set()
    coins: list[str] = []
    for m in matches:
        normalized = COIN_NORMALIZE.get(m.lower(), m.upper())
        if normalized not in seen:
            seen.add(normalized)
            coins.append(normalized)
    return coins


async def fetch_news() -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(COINGECKO_NEWS_URL, params={"page": 1})
        resp.raise_for_status()
        data = resp.json()
    return data.get("data", [])


def _parse_article(raw: dict) -> dict:
    title = raw.get("title", "")
    description = raw.get("description", "")
    coins = _extract_coins(title, description)
    sentiment = _score_sentiment(title, description)
    importance = _score_importance(title, description)

    created_at = raw.get("created_at")
    if isinstance(created_at, (int, float)):
        pub_dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
    else:
        pub_dt = datetime.now(timezone.utc)

    return {
        "source_id": str(raw.get("id", "")),
        "title": title,
        "url": raw.get("url", ""),
        "source_name": raw.get("news_site", "Unknown"),
        "sentiment": sentiment,
        "importance": importance,
        "coins": ",".join(coins) if coins else None,
        "published_at": pub_dt,
    }


async def collect_news() -> int:
    raw_articles = await fetch_news()
    if not raw_articles:
        return 0

    parsed = [_parse_article(a) for a in raw_articles if a.get("id") and a.get("title")]
    if not parsed:
        return 0

    inserted = 0
    async with AsyncSessionLocal() as db:
        for article in parsed:
            stmt = (
                pg_insert(NewsArticle)
                .values(**article, notified=False)
                .on_conflict_do_nothing(index_elements=["source_id"])
            )
            result = await db.execute(stmt)
            if result.rowcount:
                inserted += 1
        await db.commit()

        if inserted:
            new_rows = await db.execute(
                select(NewsArticle)
                .where(NewsArticle.notified == False)  # noqa: E712
                .order_by(NewsArticle.published_at.desc())
                .limit(inserted)
            )
            for row in new_rows.scalars():
                alert_data = {
                    "id": str(row.id),
                    "title": row.title,
                    "url": row.url,
                    "source": row.source_name,
                    "sentiment": row.sentiment,
                    "importance": row.importance,
                    "coins": row.coins,
                    "published_at": row.published_at.isoformat(),
                }
                await redis_service.publish_alert("newspulse", alert_data)

                if row.importance == "high":
                    from app.services.telegram_service import service as tg
                    await _notify_telegram(tg, alert_data)

                row.notified = True
            await db.commit()

    log.info("newspulse_collected", total=len(parsed), inserted=inserted)
    return inserted


async def _notify_telegram(tg, alert_data: dict) -> None:
    sentiment_emoji = {"bullish": "🟢", "bearish": "🔴"}.get(alert_data.get("sentiment", ""), "⚪")
    coins = alert_data.get("coins") or ""
    coin_tag = f" [{coins}]" if coins else ""
    text = (
        f"📰 *NewsPulse — {alert_data.get('importance', '').upper()}*{coin_tag}\n"
        f"{sentiment_emoji} {alert_data['title']}\n"
        f"Source: {alert_data.get('source', '?')}\n"
        f"[Read more]({alert_data['url']})"
    )
    try:
        r = redis_service.get_redis()
        chat_ids_raw = await r.smembers("telegram:chat_ids")
        for cid in chat_ids_raw:
            await tg.send_alert(int(cid), "newspulse", alert_data)
    except Exception as e:
        log.warning("newspulse_telegram_failed", error=str(e))


async def run_news_collection() -> None:
    try:
        await collect_news()
    except Exception as e:
        log.error("newspulse_collection_failed", error=str(e))
