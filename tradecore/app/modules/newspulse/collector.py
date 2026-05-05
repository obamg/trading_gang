"""NewsPulse — crypto news aggregator.

Polls a small set of free RSS feeds every 5 minutes. Stores articles in DB,
publishes high-impact items via Redis pubsub and Telegram.

Switched from CoinGecko News API after they moved that endpoint behind a
PRO-only paywall in 2026. RSS gives us ~95% of the price-moving stories
from the same sources for free, with no auth and no quota.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.news import NewsArticle
from app.services import redis_service

RSS_FEEDS = (
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
)
RSS_USER_AGENT = "Mozilla/5.0 (compatible; TradeCore-NewsPulse/1.0)"
HTML_TAG_RE = re.compile(r"<[^>]+>")

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


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return HTML_TAG_RE.sub("", text).strip()


def _parse_pub_date(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _parse_rss_xml(xml_text: str, source_name: str) -> list[dict]:
    """Parse an RSS 2.0 feed body into a list of raw article dicts.

    Source IDs are sha1(guid or link) truncated to 40 chars to fit the
    news_articles.source_id column (varchar 64).
    """
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("newspulse_rss_parse_failed", source=source_name, err=str(e))
        return items

    channel = root.find("channel")
    if channel is None:
        return items

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        if not title or not link:
            continue

        description = _strip_html(item.findtext("description") or "")
        pub_date = _parse_pub_date(item.findtext("pubDate"))
        # sha1 → 40 hex chars, fits in varchar(64) and stable across runs
        source_id = hashlib.sha1(guid.encode("utf-8")).hexdigest()

        items.append({
            "id": source_id,
            "title": title,
            "description": description,
            "url": link,
            "source_name": source_name,
            "published_at": pub_date,
        })
    return items


async def _fetch_one_feed(client: httpx.AsyncClient, source_name: str, url: str) -> list[dict]:
    try:
        resp = await client.get(url, headers={"User-Agent": RSS_USER_AGENT})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("newspulse_rss_fetch_failed", source=source_name, err=str(e))
        return []
    return _parse_rss_xml(resp.text, source_name)


async def fetch_news() -> list[dict]:
    """Fan out across all configured RSS feeds in parallel."""
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_fetch_one_feed(client, name, url) for name, url in RSS_FEEDS),
            return_exceptions=False,
        )
    articles: list[dict] = []
    for batch in results:
        articles.extend(batch)
    return articles


def _parse_article(raw: dict) -> dict:
    title = raw.get("title", "")
    description = raw.get("description", "")
    coins = _extract_coins(title, description)
    sentiment = _score_sentiment(title, description)
    importance = _score_importance(title, description)
    pub_dt = raw.get("published_at") or datetime.now(timezone.utc)

    return {
        "source_id": str(raw.get("id", "")),
        "title": title,
        "url": raw.get("url", ""),
        "source_name": raw.get("source_name", "Unknown"),
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

    log.info("newspulse_collected", total=len(parsed), inserted=inserted, sources=len(RSS_FEEDS))
    return inserted


async def _notify_telegram(tg, alert_data: dict) -> None:
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
