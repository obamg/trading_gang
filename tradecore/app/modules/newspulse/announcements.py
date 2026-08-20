"""NewsPulse Tier 1 — primary announcement sources.

The media RSS feeds in ``collector.py`` are a *secondary* source class: they
report on an event 3–12 minutes after it happens (measured p50 211–493s,
p90 745s). These are the *primary* publishers — the exchange doing the
listing, the regulator bringing the action — so they are the earliest point
a tradeable event is observable without a paid feed.

Sources and why each is here:

- **Binance announcements** (BAPI CMS, catalogs 48 listing / 161 delisting).
  ``listingwatch`` already diffs Binance *instrument lists*, but an instrument
  only appears at go-live; the announcement lands earlier and is what actually
  moves price. Undocumented endpoint, same trade-off as the BAPI products call
  in ``listingwatch/exchanges.py`` — treat failures as soft.
- **Upbit notices.** Korean-market listings move price hard and Upbit's notice
  API is the primary publisher. Titles are Korean; category is parsed from
  fixed phrases, not translated.
- **SEC / CFTC press releases.** Regulatory shocks, currently learned about
  5–10 min late via media RSS.

**Rate limiting:** Binance BAPI throttles hard — six unspaced requests earned
an empty 400, and it recovered once requests were ~5s apart. Catalogs are
therefore fetched sequentially with ``BINANCE_CATALOG_DELAY`` between them,
and this whole module runs on a 2-minute job rather than the RSS 1-minute
tick. Do not parallelise the Binance calls.

**Not included:** Coinbase's listing blog sits behind a Cloudflare challenge
(403 to any non-browser client) and SEC's ``/rss/litigation/*`` feeds 403 the
same way; only ``/news/pressreleases.rss`` is reachable. Both would need a
headless browser or a paid feed, which is out of scope here.

Fetchers return the same raw-dict shape as ``text.parse_rss_xml`` and may
additionally set ``importance``/``sentiment``/``coins``, which
``collector._parse_article`` treats as overrides on the keyword scorer.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

import httpx

from app.logging_config import log
from app.modules.newspulse.text import (
    compile_terms,
    distinct_hits,
    parse_rss_xml,
    source_id_for,
)

USER_AGENT = "Mozilla/5.0 (compatible; TradeCore-NewsPulse/1.0)"

# --- Binance --------------------------------------------------------------

BINANCE_CMS_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&catalogId={catalog_id}&pageNo=1&pageSize={page_size}"
)
BINANCE_ARTICLE_URL = "https://www.binance.com/en/support/announcement/detail/{code}"
BINANCE_SOURCE = "Binance Announcements"
BINANCE_PAGE_SIZE = 20
BINANCE_CATALOG_DELAY = 5.0  # seconds; below this the endpoint starts 400ing
# catalog id -> (label, sentiment)
BINANCE_CATALOGS: tuple[tuple[int, str, str], ...] = (
    (48, "listing", "bullish"),
    (161, "delisting", "bearish"),
)

# --- Upbit ----------------------------------------------------------------

UPBIT_URL = (
    "https://api-manager.upbit.com/api/v1/announcements"
    "?os=web&page=1&per_page={page_size}&category=trade"
)
UPBIT_NOTICE_URL = "https://upbit.com/service_center/notice?id={notice_id}"
UPBIT_SOURCE = "Upbit Notices"
UPBIT_PAGE_SIZE = 20
# Fixed phrases in Upbit notice titles. Order matters: the delisting phrase
# 거래지원 종료 contains 거래지원, which also opens the listing phrase.
UPBIT_BEARISH_MARKERS = ("거래지원 종료", "거래 종료", "상장폐지", "유의 종목")
UPBIT_BULLISH_MARKERS = ("신규 거래지원", "거래지원 개시", "디지털 자산 추가", "마켓 추가")

# --- Regulators -----------------------------------------------------------

REGULATOR_FEEDS: tuple[tuple[str, str], ...] = (
    ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
    ("CFTC", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
)
# Only 1 of 12 SEC press releases and 0 of 10 CFTC releases sampled were
# crypto-related — the rest is municipal securities, boiler rooms, advisory
# committees. Without this gate the regulator feeds would flood Telegram,
# since every announcement item is forced to high importance.
CRYPTO_RELEVANCE = compile_terms({
    "crypto", "cryptocurrency", "cryptocurrencies", "crypto asset",
    "crypto assets", "digital asset", "digital assets", "digital commodity",
    "virtual currency", "virtual currencies", "bitcoin", "btc", "ethereum",
    "eth", "token", "tokens", "tokenized", "stablecoin", "stablecoins",
    "blockchain", "defi", "exchange-traded product", "spot bitcoin",
    "coinbase", "binance", "ripple", "solana",
})

# --- ticker extraction ----------------------------------------------------

# Whole-paren-content ticker list: "(STORJ)" or "(CYS, ICNT, XAN)". Anchoring
# on the closing paren is what rejects "(KRW, BTC, USDT 마켓)" (trailing
# Korean), "(2026-08-19)" (hyphens) and "(9/14 15:00)" (slash/colon).
PAREN_TICKERS_RE = re.compile(r"\(([A-Z0-9]{2,10}(?:,\s*[A-Z0-9]{2,10})*)\)")
# Deliberately wider than a ticker: pair symbols carry the quote inline
# (UNITREEUSDT is 11 chars), so length is enforced in _clean_ticker *after*
# the quote suffix is stripped, not here.
UPPER_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,14}\b")
QUOTE_ASSETS = {
    "USDT", "USDC", "USD", "BUSD", "FDUSD", "TUSD", "DAI", "KRW", "EUR",
    "TRY", "BRL", "GBP", "JPY", "BNB",
}
# All-caps non-tickers that show up in Binance announcement titles.
TICKER_STOPWORDS = {
    "API", "NFT", "P2P", "VIP", "AMA", "OTC", "KYC", "TGE", "IEO", "ICO",
    "ETF", "ETP", "AML", "FAQ", "USD", "TWAP", "VWAP", "PNL", "TVL", "AI",
    "CEO", "CFO", "SEC", "CFTC", "DEFI", "NEW", "AND", "THE", "FOR", "WILL",
    "ICYMI", "US", "UK", "EU", "UTC",
}
QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def _clean_ticker(token: str) -> str | None:
    """Normalise one candidate ticker, or None if it isn't one.

    Perp/pair symbols carry the quote inline (``UNITREEUSDT``); the base is
    what a reader cares about.
    """
    for suffix in QUOTE_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            token = token[: -len(suffix)]
            break
    if token in QUOTE_ASSETS or token in TICKER_STOPWORDS:
        return None
    if not (2 <= len(token) <= 10) or not any(ch.isalpha() for ch in token):
        return None
    return token


def _dedupe(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_paren_tickers(title: str) -> list[str]:
    """Tickers from the *first* pure-ticker parenthetical.

    Upbit titles are consistently ``이름(TICKER) …`` with trailing market
    parentheticals like ``(KRW, BTC, USDT 마켓)``. Taking only the first
    group keeps the subject coin and drops the quote markets.
    """
    m = PAREN_TICKERS_RE.search(title)
    if not m:
        return []
    cleaned = [_clean_ticker(t.strip()) for t in m.group(1).split(",")]
    return _dedupe([t for t in cleaned if t])


def extract_upper_tickers(title: str) -> list[str]:
    """Tickers from all-caps tokens — Binance lists them inline and unbracketed
    ("Binance Will Delist ICX, SCRT, STORJ on 2026-09-03")."""
    cleaned = [_clean_ticker(t) for t in UPPER_TOKEN_RE.findall(title)]
    return _dedupe([t for t in cleaned if t])


# --- fetchers -------------------------------------------------------------

async def _fetch_binance_catalog(
    client: httpx.AsyncClient, catalog_id: int, label: str, sentiment: str
) -> list[dict]:
    url = BINANCE_CMS_URL.format(catalog_id=catalog_id, page_size=BINANCE_PAGE_SIZE)
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        # Throttling shows up as an empty 400 body, i.e. a JSON decode error.
        log.warning("newspulse_binance_fetch_failed", catalog=label, err=str(e))
        return []

    items: list[dict] = []
    for catalog in ((payload.get("data") or {}).get("catalogs") or []):
        for article in (catalog.get("articles") or []):
            title = (article.get("title") or "").strip()
            code = article.get("code")
            if not title or not code:
                continue
            released = article.get("releaseDate")
            published = (
                datetime.fromtimestamp(released / 1000, tz=timezone.utc)
                if isinstance(released, (int, float))
                else datetime.now(timezone.utc)
            )
            coins = extract_upper_tickers(title)
            items.append({
                "id": source_id_for(f"binance:{article.get('id')}"),
                "title": title,
                "description": "",
                "url": BINANCE_ARTICLE_URL.format(code=code),
                "source_name": BINANCE_SOURCE,
                "published_at": published,
                "importance": "high",
                "sentiment": sentiment,
                "coins": ",".join(coins) if coins else None,
            })
    return items


async def fetch_binance(client: httpx.AsyncClient) -> list[dict]:
    """Fetch Binance catalogs **sequentially** — see the rate-limit note above."""
    items: list[dict] = []
    for idx, (catalog_id, label, sentiment) in enumerate(BINANCE_CATALOGS):
        if idx:
            await asyncio.sleep(BINANCE_CATALOG_DELAY)
        items.extend(await _fetch_binance_catalog(client, catalog_id, label, sentiment))
    return items


def _upbit_sentiment(title: str) -> str:
    # Bearish first: 거래지원 종료 (delisting) contains 거래지원, which also
    # opens the bullish 신규 거래지원 (new listing).
    if any(marker in title for marker in UPBIT_BEARISH_MARKERS):
        return "bearish"
    if any(marker in title for marker in UPBIT_BULLISH_MARKERS):
        return "bullish"
    return "neutral"


async def fetch_upbit(client: httpx.AsyncClient) -> list[dict]:
    url = UPBIT_URL.format(page_size=UPBIT_PAGE_SIZE)
    try:
        resp = await client.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        log.warning("newspulse_upbit_fetch_failed", err=str(e))
        return []

    items: list[dict] = []
    for notice in ((payload.get("data") or {}).get("notices") or []):
        title = (notice.get("title") or "").strip()
        notice_id = notice.get("id")
        if not title or notice_id is None:
            continue
        try:
            published = datetime.fromisoformat(notice.get("listed_at", ""))
        except (TypeError, ValueError):
            published = datetime.now(timezone.utc)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        coins = extract_paren_tickers(title)
        items.append({
            "id": source_id_for(f"upbit:{notice_id}"),
            "title": title,
            "description": "",
            "url": UPBIT_NOTICE_URL.format(notice_id=notice_id),
            "source_name": UPBIT_SOURCE,
            "published_at": published,
            "importance": "high",
            "sentiment": _upbit_sentiment(title),
            "coins": ",".join(coins) if coins else None,
        })
    return items


def is_crypto_relevant(title: str, description: str) -> bool:
    return distinct_hits(CRYPTO_RELEVANCE, f"{title} {description}") >= 1


async def _fetch_regulator(
    client: httpx.AsyncClient, source_name: str, url: str
) -> list[dict]:
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("newspulse_regulator_fetch_failed", source=source_name, err=str(e))
        return []

    items = []
    for raw in parse_rss_xml(resp.text, source_name):
        if not is_crypto_relevant(raw["title"], raw["description"]):
            continue
        # Importance is forced; sentiment is left unset so the keyword scorer
        # in collector.py decides direction from the headline itself.
        raw["importance"] = "high"
        items.append(raw)
    return items


async def fetch_announcements() -> list[dict]:
    """Fan out across the primary sources.

    Binance is sequential internally; the three source groups run in parallel
    since they are different hosts.
    """
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        groups = await asyncio.gather(
            fetch_binance(client),
            fetch_upbit(client),
            *(_fetch_regulator(client, name, url) for name, url in REGULATOR_FEEDS),
            return_exceptions=True,
        )

    items: list[dict] = []
    for group in groups:
        if isinstance(group, BaseException):
            log.warning("newspulse_announcement_group_failed", err=str(group))
            continue
        items.extend(group)
    return items
