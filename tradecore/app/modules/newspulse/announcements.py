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
- **Bithumb notices** (added 2026-09-03). Korea's #2 exchange, the same
  listing-pump mechanism as Upbit, and it additionally publishes
  거래유의종목 지정 ("investment caution designation") — a Korean-specific
  *bearish* catalyst nothing else in the pipeline sees.
- **Bybit / OKX / KuCoin announcements** (added 2026-09-03). Official,
  documented, keyless. Lower per-event impact than the Korean venues but they
  multiply the primary-source sample, which is the binding constraint on the
  newsevent forward test (0.45 paired signals/day → n≥30 was 3–4 months out).
- **SEC / CFTC press releases.** Regulatory shocks, currently learned about
  5–10 min late via media RSS.

**Why these fetchers filter hard.** ``newsevent`` treats *any* item from a
PRIMARY source as a tradeable news leg, bypassing the keyword importance
scorer entirely. So whatever these functions emit becomes a trade. Every
exchange mixes promos into the same announcement feed ("USDT Token Splash —
grab a share of the prize pool" is typed ``new_crypto`` on Bybit), so each
fetcher **whitelists a listing/delisting verb phrase** rather than
blacklisting promo words — the same word-boundary discipline that the
newspulse keyword scorer needed. Anything unmatched is dropped and logged at
debug so real phrasing can be learned from prod rather than guessed.

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
from datetime import datetime, timedelta, timezone

import httpx

from app.logging_config import log
from app.modules.newspulse.text import (
    compile_terms,
    distinct_hits,
    parse_rss_xml,
    source_id_for,
)

USER_AGENT = "Mozilla/5.0 (compatible; TradeCore-NewsPulse/1.0)"

# Bithumb publishes wall-clock Seoul time with no offset marker.
KST = timezone(timedelta(hours=9))

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

# --- Bithumb --------------------------------------------------------------

# NOTE: this endpoint ALWAYS returns exactly the 5 most recent notices —
# `count`, `page` and `category` are all accepted and all ignored (verified
# 2026-09-03). There is no pagination and no backfill. At the 2-minute job
# cadence that is ample for the observed ~30 notices/day, but a burst of >5
# inside one tick would be lost. Do not add paging params expecting more.
# `feed.bithumb.com` (the other documented host) sits behind a Cloudflare
# challenge and 403s any non-browser client; api.bithumb.com does not.
BITHUMB_URL = "https://api.bithumb.com/v1/notices"
BITHUMB_SOURCE = "Bithumb Notices"
# Categories are Korean and arrive as a list. Only these are tradeable:
# 입출금 (deposit/withdrawal pauses) is routine chain-maintenance noise and
# 이벤트 is pure promo — both are dropped.
BITHUMB_CAUTION_CATEGORY = "거래유의"
BITHUMB_BEARISH_MARKERS = (
    "거래유의종목 지정",   # investment-caution designation
    "거래지원 종료",       # delisting
    "거래 종료",
    "상장폐지",
    "유의 종목",
)
BITHUMB_BULLISH_MARKERS = (
    "마켓 추가",           # market added
    "신규 거래지원",       # new trading support = listing
    "거래지원 개시",
    "원화 마켓 추가",
)

# --- Bybit / OKX / KuCoin -------------------------------------------------

BYBIT_URL = "https://api.bybit.com/v5/announcements/index?locale=en-US&type={ann_type}&limit={limit}"
BYBIT_SOURCE = "Bybit Announcements"
BYBIT_TYPES: tuple[tuple[str, str], ...] = (("new_crypto", "bullish"), ("delistings", "bearish"))

OKX_URL = "https://www.okx.com/api/v5/support/announcements?annType={ann_type}&page=1"
OKX_SOURCE = "OKX Announcements"
OKX_TYPES: tuple[tuple[str, str], ...] = (
    ("announcements-new-listings", "bullish"),
    ("announcements-delistings", "bearish"),
)

KUCOIN_URL = "https://api.kucoin.com/api/v3/announcements?pageSize={limit}&annType={ann_type}"
KUCOIN_SOURCE = "KuCoin Announcements"
KUCOIN_TYPES: tuple[tuple[str, str], ...] = (("new-listings", "bullish"), ("delistings", "bearish"))

ANNOUNCEMENT_PAGE_SIZE = 20

# Whitelisted verb phrases. An item must match one of these to be emitted at
# all — see the module docstring on why this is a whitelist.
LISTING_PHRASES = compile_terms({
    "will list", "to list", "new listing", "new listings", "lists",
    "listed on", "will launch", "to launch", "listing of", "will add",
    "to add", "now live on", "completed the listing",
    # "listing on" added after a live probe dropped a genuine listing:
    # "HODLer Airdrops: Aligned (ALIGN) World Premiere Listing on KuCoin".
    # Safe against "delisting on" because compile_terms anchors on \b, so
    # `\blisting` cannot match inside "delisting" — and delisting is checked
    # first regardless.
    "listing on", "will be listed", "is now listed",
})
DELISTING_PHRASES = compile_terms({
    "will delist", "to delist", "delist", "delists", "delisting",
    "delisted", "will remove", "to remove", "removal of",
    "will be removed", "termination of trading", "will cease trading",
})
# Non-crypto instruments these venues publish in the SAME announcement type:
# TradFi/stock-index perps (CVXSTOCKUSDT, KUAISHOUUSDT, SHEINHKDUSDT). Their
# "tickers" are not coins and would attribute to nothing — or worse, collide.
NON_CRYPTO_MARKERS = compile_terms({
    "tradfi", "stock index", "stock indices", "pre-market", "premarket",
    "equity index", "tokenized stock", "tokenized equities", "xstock",
})


def classify_announcement(title: str, default_sentiment: str) -> str | None:
    """bullish | bearish | None(=drop) for an English exchange announcement.

    Delisting is checked FIRST: "will delist X and relist Y" is a delisting,
    and several venues phrase removals with a listing verb in the tail.
    """
    if distinct_hits(NON_CRYPTO_MARKERS, title) >= 1:
        return None
    if distinct_hits(DELISTING_PHRASES, title) >= 1:
        return "bearish"
    if distinct_hits(LISTING_PHRASES, title) >= 1:
        return "bullish"
    return None


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
    # Venue names. Every venue leads its own headline with its own name
    # ("OKX to list SLX/USDT"), which extract_upper_tickers happily read as a
    # ticker — observed attributing OKX and EEA as coins on live data.
    # OKB, BNB, KCS etc. are NOT here: those are real tradeable tokens.
    "OKX", "KUCOIN", "BYBIT", "BINANCE", "UPBIT", "BITHUMB", "MEXC", "HTX",
    "GATE", "HODLER", "HODL",
    # Jurisdictions / regions these venues scope announcements to.
    "EEA", "EEE", "MENA", "APAC", "LATAM", "TR", "BR", "JP", "KR",
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


def extract_all_paren_tickers(title: str) -> list[str]:
    """Tickers from EVERY pure-ticker parenthetical, not just the first.

    Bithumb puts one coin per parenthetical and lists several per notice:
    ``코어(CORE), 인젝티브(INJ) 거래유의종목 지정`` designates two coins, and
    ``extract_paren_tickers`` (first-group-only, tuned for Upbit's trailing
    ``(KRW, BTC, USDT 마켓)``) would silently drop INJ.

    Safe to widen here because the regex requires the whole parenthetical to
    be tickers — Upbit's market list has trailing Korean and never matches,
    and date/time parentheticals like ``(09/03 재개)`` are rejected by the
    slash. Quote assets that do slip through are dropped by _clean_ticker.
    """
    out: list[str] = []
    for m in PAREN_TICKERS_RE.finditer(title):
        for raw in m.group(1).split(","):
            cleaned = _clean_ticker(raw.strip())
            if cleaned:
                out.append(cleaned)
    return _dedupe(out)


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


def _bithumb_sentiment(title: str, categories: list[str]) -> str | None:
    """bullish | bearish | None(=drop) for a Bithumb notice.

    Bearish markers are checked before bullish for the same reason as Upbit:
    거래지원 종료 (delisting) contains 거래지원, which opens the listing
    phrase 신규 거래지원.
    """
    if any(marker in title for marker in BITHUMB_BEARISH_MARKERS):
        return "bearish"
    if any(marker in title for marker in BITHUMB_BULLISH_MARKERS):
        return "bullish"
    # Category is the fallback: a caution notice is bearish even if Bithumb
    # rephrases the title. 입출금 / 이벤트 fall through to None and are dropped.
    if BITHUMB_CAUTION_CATEGORY in categories:
        return "bearish"
    return None


async def fetch_bithumb(client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(
            BITHUMB_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        log.warning("newspulse_bithumb_fetch_failed", err=str(e))
        return []

    if not isinstance(payload, list):
        log.warning("newspulse_bithumb_unexpected_shape", type=type(payload).__name__)
        return []

    items: list[dict] = []
    for notice in payload:
        title = (notice.get("title") or "").strip()
        url = notice.get("pc_url") or ""
        if not title or not url:
            continue
        categories = [c for c in (notice.get("categories") or []) if c]
        sentiment = _bithumb_sentiment(title, categories)
        if sentiment is None:
            log.debug("newspulse_bithumb_dropped", title=title[:80], categories=categories)
            continue
        # "2026-09-03 09:35:00", KST — Bithumb publishes wall-clock Seoul time
        # with no offset. Parsing it as UTC would date-shift every notice by
        # 9h and put it outside newsevent's 15-minute pairing window.
        published = _parse_kst(notice.get("published_at"))
        coins = extract_all_paren_tickers(title)
        items.append({
            "id": source_id_for(f"bithumb:{url}"),
            "title": title,
            "description": "",
            "url": url,
            "source_name": BITHUMB_SOURCE,
            "published_at": published,
            "importance": "high",
            "sentiment": sentiment,
            "coins": ",".join(coins) if coins else None,
        })
    return items


async def _fetch_json_announcements(
    client: httpx.AsyncClient,
    *,
    source_name: str,
    url: str,
    extract_rows,
    default_sentiment: str,
) -> list[dict]:
    """Shared shell for the three English venues: fetch, filter, normalise.

    ``extract_rows`` maps the venue payload to
    ``(uid, title, url, published_at)`` tuples; everything after that —
    the listing/delisting whitelist, ticker extraction, dict shape — is
    identical across Bybit/OKX/KuCoin.
    """
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        log.warning("newspulse_announcement_fetch_failed", source=source_name, err=str(e))
        return []

    items: list[dict] = []
    try:
        rows = list(extract_rows(payload))
    except (AttributeError, KeyError, TypeError, IndexError) as e:
        log.warning("newspulse_announcement_parse_failed", source=source_name, err=str(e))
        return []

    for uid, title, article_url, published in rows:
        title = (title or "").strip()
        if not title or not uid:
            continue
        sentiment = classify_announcement(title, default_sentiment)
        if sentiment is None:
            log.debug("newspulse_announcement_dropped", source=source_name, title=title[:80])
            continue
        coins = extract_upper_tickers(title) or extract_paren_tickers(title)
        items.append({
            "id": source_id_for(f"{source_name}:{uid}"),
            "title": title,
            "description": "",
            "url": article_url or "",
            "source_name": source_name,
            "published_at": published or datetime.now(timezone.utc),
            "importance": "high",
            "sentiment": sentiment,
            "coins": ",".join(coins) if coins else None,
        })
    return items


def _ms_to_dt(value) -> datetime | None:
    """Epoch-millis -> aware UTC. OKX sends pTime as a *string* of millis,
    Bybit/KuCoin send ints, so both are accepted."""
    if isinstance(value, str):
        value = value.strip()
        if not value.lstrip("-").isdigit():
            return None
        value = int(value)
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_kst(raw) -> datetime:
    """'YYYY-MM-DD HH:MM:SS' in Asia/Seoul (UTC+9) -> aware UTC datetime."""
    try:
        naive = datetime.strptime(str(raw).strip(), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return naive.replace(tzinfo=KST).astimezone(timezone.utc)


async def fetch_bybit(client: httpx.AsyncClient) -> list[dict]:
    def rows(payload):
        for a in ((payload.get("result") or {}).get("list") or []):
            yield (
                a.get("url"), a.get("title"), a.get("url"),
                _ms_to_dt(a.get("publishTime")) or _ms_to_dt(a.get("dateTimestamp")),
            )

    out: list[dict] = []
    for ann_type, sentiment in BYBIT_TYPES:
        out.extend(await _fetch_json_announcements(
            client, source_name=BYBIT_SOURCE,
            url=BYBIT_URL.format(ann_type=ann_type, limit=ANNOUNCEMENT_PAGE_SIZE),
            extract_rows=rows, default_sentiment=sentiment,
        ))
    return out


async def fetch_okx(client: httpx.AsyncClient) -> list[dict]:
    def rows(payload):
        for block in (payload.get("data") or []):
            for a in (block.get("details") or []):
                yield (a.get("url"), a.get("title"), a.get("url"), _ms_to_dt(a.get("pTime")))

    out: list[dict] = []
    for ann_type, sentiment in OKX_TYPES:
        out.extend(await _fetch_json_announcements(
            client, source_name=OKX_SOURCE, url=OKX_URL.format(ann_type=ann_type),
            extract_rows=rows, default_sentiment=sentiment,
        ))
    return out


async def fetch_kucoin(client: httpx.AsyncClient) -> list[dict]:
    def rows(payload):
        for a in ((payload.get("data") or {}).get("items") or []):
            yield (
                a.get("annId"), a.get("annTitle"), a.get("annUrl"),
                _ms_to_dt(a.get("cTime")),
            )

    out: list[dict] = []
    for ann_type, sentiment in KUCOIN_TYPES:
        out.extend(await _fetch_json_announcements(
            client, source_name=KUCOIN_SOURCE,
            url=KUCOIN_URL.format(ann_type=ann_type, limit=ANNOUNCEMENT_PAGE_SIZE),
            extract_rows=rows, default_sentiment=sentiment,
        ))
    return out


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
            fetch_bithumb(client),
            fetch_bybit(client),
            fetch_okx(client),
            fetch_kucoin(client),
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
