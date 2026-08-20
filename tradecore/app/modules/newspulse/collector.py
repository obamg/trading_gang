"""NewsPulse — crypto news aggregator.

Two source tiers, two scheduler jobs, one storage/notify path:

- **Media RSS** (this file, ``newspulse_collect``, every minute): secondary
  reporting, measured p50 211–493s behind the event. Conditional GET, so an
  unchanged feed costs a 304 and no body.
- **Primary announcements** (``announcements.py``, ``newspulse_announcements``,
  every 2 minutes): exchange listing/delisting notices and regulator press
  releases — the publishers the media tier is reporting *on*.

Both land in ``news_articles`` and share ``_collect``, so dedupe (unique
``source_id``), the WS relay and the Telegram fan-out are defined once.

Switched from CoinGecko News API after they moved that endpoint behind a
PRO-only paywall in 2026. RSS gives us ~95% of the price-moving stories
from the same sources for free, with no auth and no quota.

Keyword matching is **word-boundary anchored** (see ``text.compile_terms``).
The original substring implementation made ``ath`` fire on "gather"/"path",
``ban`` on "banking", and ``sec`` on "second"/"sector", which tagged 45% of
all articles high-impact and skewed sentiment bearish. Any new term set here
must go through ``compile_terms`` — never ``word in text``.

Coin attribution lives in ``universe.py``, driven off the exchange instrument
lists plus CoinGecko names rather than a hardcoded symbol list.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.news import NewsArticle
from app.modules.newspulse import announcements, universe
from app.modules.newspulse.text import compile_terms, distinct_hits, parse_rss_xml
from app.modules.newspulse.universe import CoinMap, legacy_coin_map
from app.services import redis_service

RSS_FEEDS = (
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
)
RSS_USER_AGENT = "Mozilla/5.0 (compatible; TradeCore-NewsPulse/1.0)"
# Per-feed HTTP validators for conditional GET, keyed by feed URL. The
# scheduler is a single process and this job is its only writer, so an
# in-process dict is enough — a restart costs one unconditional refetch.
_FEED_VALIDATORS: dict[str, dict[str, str]] = {}

# Anything published longer ago than this is stored but never alerted on.
# Announcement endpoints return a page of history, so without this a first
# run (or a restart after downtime) would blast days of old notices to
# Telegram as if they had just happened.
NOTIFY_MAX_AGE = timedelta(minutes=30)

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
# Importance is scored as a weighted sum, not a flat hit count. A single
# STRONG term clears the bar on its own; WEAK terms are ambient crypto-media
# vocabulary that only means something when several corroborate.
HIGH_IMPACT_STRONG = {
    "hack", "hacks", "hacked", "exploit", "exploits", "exploited", "breach",
    "breached", "drained", "insolvency", "insolvent", "bankruptcy",
    "halt", "halts", "halted",
    "delist", "delisted", "delisting", "etf", "sec", "cftc", "lawsuit",
    "sues", "sued", "indicted", "crackdown", "halving", "hard fork",
    "fomc", "rate cut", "rate hike", "emergency",
}
HIGH_IMPACT_WEAK = {
    "fed", "federal reserve", "regulation", "regulatory", "ban", "bans",
    "banned", "approval", "approved", "billion", "crash", "crashes",
    "all-time high", "ath", "fork", "upgrade", "unlock", "unlocks",
    "blackrock", "grayscale", "microstrategy", "binance", "coinbase",
    "breaking", "inflation", "cpi", "inflows", "outflows",
}
STRONG_WEIGHT = 3
WEAK_WEIGHT = 1
HIGH_IMPACT_THRESHOLD = 3  # one STRONG term, or three corroborating WEAK ones

BULLISH_RE = compile_terms(BULLISH_WORDS)
BEARISH_RE = compile_terms(BEARISH_WORDS)
STRONG_RE = compile_terms(HIGH_IMPACT_STRONG)
WEAK_RE = compile_terms(HIGH_IMPACT_WEAK)


def _score_sentiment(title: str, description: str) -> str:
    text = f"{title} {description}"
    bull = distinct_hits(BULLISH_RE, text)
    bear = distinct_hits(BEARISH_RE, text)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _score_importance(title: str, description: str) -> str:
    text = f"{title} {description}"
    score = (
        distinct_hits(STRONG_RE, text) * STRONG_WEIGHT
        + distinct_hits(WEAK_RE, text) * WEAK_WEIGHT
    )
    return "high" if score >= HIGH_IMPACT_THRESHOLD else "normal"


def _extract_coins(title: str, description: str, coin_map: CoinMap) -> list[str]:
    return coin_map.extract(f"{title} {description}")


async def _fetch_one_feed(client: httpx.AsyncClient, source_name: str, url: str) -> list[dict]:
    """Fetch one feed, using stored ETag / Last-Modified for a conditional GET.

    An unchanged feed answers 304 with no body, which is what makes the
    1-minute poll cadence cheap enough to run against someone else's server.
    """
    headers = {"User-Agent": RSS_USER_AGENT, **_FEED_VALIDATORS.get(url, {})}
    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 304:
            return []
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("newspulse_rss_fetch_failed", source=source_name, err=str(e))
        return []

    validators = {}
    if etag := resp.headers.get("ETag"):
        validators["If-None-Match"] = etag
    if last_modified := resp.headers.get("Last-Modified"):
        validators["If-Modified-Since"] = last_modified
    if validators:
        _FEED_VALIDATORS[url] = validators
    else:
        # Feed stopped sending validators — drop stale ones so we don't pin
        # an ETag the origin no longer honours.
        _FEED_VALIDATORS.pop(url, None)

    return parse_rss_xml(resp.text, source_name)


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


def _parse_article(raw: dict, coin_map: CoinMap | None = None) -> dict:
    """Normalise one raw item for insert.

    ``sentiment``/``importance``/``coins`` already set by the caller are
    treated as overrides: a primary source (see ``announcements.py``) knows
    from the endpoint it came off that a Binance delisting is bearish and
    high-impact, which the keyword scorer could only guess at.
    """
    title = raw.get("title", "")
    description = raw.get("description", "")
    coins = _extract_coins(title, description, coin_map or legacy_coin_map())
    pub_dt = raw.get("published_at") or datetime.now(timezone.utc)

    return {
        "source_id": str(raw.get("id", "")),
        "title": title,
        "url": raw.get("url", ""),
        "source_name": raw.get("source_name", "Unknown"),
        "sentiment": raw.get("sentiment") or _score_sentiment(title, description),
        "importance": raw.get("importance") or _score_importance(title, description),
        "coins": raw.get("coins") or (",".join(coins) if coins else None),
        "published_at": pub_dt,
    }


async def _collect(
    fetcher: Callable[[], Awaitable[list[dict]]], kind: str
) -> int:
    raw_articles = await fetcher()
    if not raw_articles:
        return 0

    # Loaded once per tick, not per article — the compiled matcher spans ~900
    # tickers plus ~400 coin names.
    coin_map = await universe.load_coin_map()
    parsed = [
        _parse_article(a, coin_map)
        for a in raw_articles
        if a.get("id") and a.get("title")
    ]
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
            # Every unnotified row, not the `inserted` newest by publish date.
            # The old `.order_by(published_at.desc()).limit(inserted)` silently
            # stranded any row published older than the newest of the batch —
            # which is the normal case now that announcement endpoints return a
            # page of history rather than a live-only feed.
            pending = await db.execute(
                select(NewsArticle).where(NewsArticle.notified == False)  # noqa: E712
            )
            now = datetime.now(timezone.utc)
            notified = 0
            for row in pending.scalars():
                row.notified = True
                # Backfill guard: a first run, or a restart after downtime,
                # pulls in items hours or days old. Record them, don't alert.
                if now - row.published_at > NOTIFY_MAX_AGE:
                    continue

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
                notified += 1

                if row.importance == "high":
                    from app.services.telegram_service import service as tg
                    await _notify_telegram(tg, alert_data)
            await db.commit()

            log.info(
                "newspulse_collected",
                kind=kind,
                total=len(parsed),
                inserted=inserted,
                notified=notified,
            )
    return inserted


async def collect_news() -> int:
    """Media RSS — runs on the 1-minute tick."""
    return await _collect(fetch_news, "rss")


async def collect_announcements() -> int:
    """Primary exchange/regulator announcements — runs on the 2-minute tick.

    Separate job because Binance BAPI throttles: see the rate-limit note in
    ``announcements.py``.
    """
    return await _collect(announcements.fetch_announcements, "announcements")


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


async def run_announcement_collection() -> None:
    try:
        await collect_announcements()
    except Exception as e:
        log.error("newspulse_announcements_failed", error=str(e))
