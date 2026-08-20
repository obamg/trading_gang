"""Tests for the NewsPulse Tier 1 primary announcement sources.

Payload fixtures are trimmed copies of real responses captured from the prod
VPS on 2026-08-20, so the parsers are pinned to the shapes actually served.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.modules.newspulse import announcements as a
from app.modules.newspulse import collector as c


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- ticker extraction ----------------------------------------------------

@pytest.mark.parametrize(
    "title,expected",
    [
        # Binance lists tickers inline, unbracketed.
        ("Binance Will Delist ICX, SCRT, STORJ on 2026-09-03", ["ICX", "SCRT", "STORJ"]),
        ("Binance Margin And Loan Will Delist BTTC & POWR on 2026-08-14", ["BTTC", "POWR"]),
        # Pair symbols carry the quote inline; the base is what matters.
        ("Binance Futures Will Launch UNITREEUSDT USD-Margined Perpetual Contract", ["UNITREE"]),
        # No ticker at all is a valid outcome.
        ("Notice of Removal of Spot Trading Pairs - 2026-08-21", []),
    ],
)
def test_extract_upper_tickers(title, expected):
    assert a.extract_upper_tickers(title) == expected


def test_upper_tickers_drops_stopwords_and_quotes():
    """Title-case prose is safe, but all-caps acronyms would leak without the list."""
    assert a.extract_upper_tickers("Binance Will Add NEW ETF API Support For USDT") == []


@pytest.mark.parametrize(
    "title,expected",
    [
        ("스토리지(STORJ) 거래지원 종료 안내 (9/14 15:00)", ["STORJ"]),
        ("BTC, USDT 마켓 신규 거래지원 안내 (CYS, ICNT, XAN, EDEN, AIOZ, ALLO)",
         ["CYS", "ICNT", "XAN", "EDEN", "AIOZ", "ALLO"]),
    ],
)
def test_extract_paren_tickers(title, expected):
    assert a.extract_paren_tickers(title) == expected


def test_paren_tickers_ignores_market_and_date_parentheticals():
    """The subject coin comes from the first group; trailing market/date
    parentheticals must not leak BTC/KRW/USDT in as 'the coin'."""
    title = "댑오에스(DOS) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"
    assert a.extract_paren_tickers(title) == ["DOS"]
    assert a.extract_paren_tickers("Some Notice (2026-08-19)") == []
    assert a.extract_paren_tickers("Another Notice (9/14 15:00)") == []


# --- Binance --------------------------------------------------------------

BINANCE_PAYLOAD = {
    "code": "000000",
    "data": {
        "catalogs": [{
            "catalogId": 48,
            "catalogName": "New Cryptocurrency Listing",
            "articles": [
                {"id": 282805, "code": "3e662272597c44b7939f5db5c8c86d4f",
                 "title": "Binance Will List Foo (FOOBAR)", "releaseDate": 1787106609387},
                {"id": 282552, "code": "0872245db74c4daaabd4f11984ba52c1",
                 "title": "Binance Futures Will Launch ABCUSDT Perpetual Contract",
                 "releaseDate": 1786932925476},
            ],
        }]
    },
}


@pytest.mark.asyncio
async def test_binance_catalog_parses_and_forces_high_importance():
    async with _client(lambda r: httpx.Response(200, json=BINANCE_PAYLOAD)) as client:
        items = await a._fetch_binance_catalog(client, 48, "listing", "bullish")

    assert len(items) == 2
    first = items[0]
    assert first["source_name"] == a.BINANCE_SOURCE
    assert first["importance"] == "high"
    assert first["sentiment"] == "bullish"
    assert first["coins"] == "FOOBAR"
    assert first["url"].endswith("/3e662272597c44b7939f5db5c8c86d4f")
    assert first["published_at"].year == 2026
    assert items[1]["coins"] == "ABC"  # USDT suffix stripped


@pytest.mark.asyncio
async def test_binance_throttle_returns_empty_not_raise():
    """Throttling is served as an empty 400 body — a JSON decode error, not
    an HTTP error. It must degrade to zero items, never take down the job."""
    async with _client(lambda r: httpx.Response(400, content=b"")) as client:
        assert await a._fetch_binance_catalog(client, 48, "listing", "bullish") == []


@pytest.mark.asyncio
async def test_binance_empty_200_body_returns_empty():
    async with _client(lambda r: httpx.Response(200, content=b"")) as client:
        assert await a._fetch_binance_catalog(client, 48, "listing", "bullish") == []


@pytest.mark.asyncio
async def test_binance_catalogs_are_fetched_sequentially_with_delay(monkeypatch):
    """Parallelising these is what earns the 400s — pin the spacing."""
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(a.asyncio, "sleep", fake_sleep)

    order: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        order.append(request.url.params.get("catalogId"))
        return httpx.Response(200, json={"data": {"catalogs": []}})

    async with _client(handle) as client:
        await a.fetch_binance(client)

    assert order == ["48", "161"]
    assert sleeps == [a.BINANCE_CATALOG_DELAY]


# --- Upbit ----------------------------------------------------------------

UPBIT_PAYLOAD = {
    "success": True,
    "data": {"notices": [
        {"id": 6477, "listed_at": "2026-08-14T16:00:06+09:00",
         "title": "스토리지(STORJ) 거래지원 종료 안내 (9/14 15:00)"},
        {"id": 6470, "listed_at": "2026-08-11T14:55:00+09:00",
         "title": "댑오에스(DOS) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"},
        {"id": 6468, "listed_at": "2026-08-11T15:00:00+09:00",
         "title": "레이븐코인(RVN) 거래 유의 종목 지정 안내"},
    ]},
}


@pytest.mark.asyncio
async def test_upbit_parses_korean_categories():
    async with _client(lambda r: httpx.Response(200, json=UPBIT_PAYLOAD)) as client:
        items = await a.fetch_upbit(client)

    assert [i["sentiment"] for i in items] == ["bearish", "bullish", "bearish"]
    assert [i["coins"] for i in items] == ["STORJ", "DOS", "RVN"]
    assert all(i["importance"] == "high" for i in items)
    assert items[0]["url"].endswith("id=6477")
    # +09:00 offset must be preserved, not naively read as UTC.
    assert items[0]["published_at"].utcoffset().total_seconds() == 9 * 3600


def test_upbit_delisting_beats_listing_marker():
    """거래지원 종료 (delist) contains 거래지원, which opens 신규 거래지원
    (list) — bearish markers must be checked first."""
    assert a._upbit_sentiment("스토리지(STORJ) 거래지원 종료 안내") == "bearish"
    assert a._upbit_sentiment("댑오에스(DOS) 신규 거래지원 안내") == "bullish"
    assert a._upbit_sentiment("시스템 점검 안내") == "neutral"


@pytest.mark.asyncio
async def test_upbit_http_error_returns_empty():
    async with _client(lambda r: httpx.Response(503)) as client:
        assert await a.fetch_upbit(client) == []


# --- regulators -----------------------------------------------------------

REGULATOR_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>SEC Proposes New Regulation Crypto Assets</title>
    <link>https://www.sec.gov/news/a</link><guid>a</guid>
    <description>Rules for digital asset markets.</description>
    <pubDate>Wed, 19 Aug 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>SEC Office of Municipal Securities Updates FAQs</title>
    <link>https://www.sec.gov/news/b</link><guid>b</guid>
    <description>Registration of municipal advisors.</description>
    <pubDate>Wed, 19 Aug 2026 11:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_regulator_feed_keeps_only_crypto_items():
    """Only 1 of 12 sampled SEC releases was crypto; without this gate the
    forced-high importance would flood Telegram with municipal-bond news."""
    async with _client(lambda r: httpx.Response(200, text=REGULATOR_RSS)) as client:
        items = await a._fetch_regulator(client, "SEC", "https://sec.example/rss")

    assert len(items) == 1
    assert items[0]["title"].startswith("SEC Proposes New Regulation Crypto")
    assert items[0]["importance"] == "high"
    # Sentiment is deliberately left unset so the keyword scorer decides.
    assert "sentiment" not in items[0]


def test_crypto_relevance_uses_word_boundaries():
    assert a.is_crypto_relevant("SEC charges token issuer", "")
    assert not a.is_crypto_relevant("SEC Announces Roundtable on 24-Hour Trading", "")
    # 'coin' must not fire on 'coincide'
    assert not a.is_crypto_relevant("Filings coincide with quarter end", "")


@pytest.mark.asyncio
async def test_regulator_http_error_returns_empty():
    async with _client(lambda r: httpx.Response(403)) as client:
        assert await a._fetch_regulator(client, "SEC", "https://sec.example/rss") == []


# --- override plumbing into the collector ---------------------------------

def test_parse_article_respects_source_overrides():
    """A primary source knows a delisting is bearish/high from the endpoint it
    came off; the keyword scorer must not overrule it."""
    raw = {
        "id": "abc", "title": "Binance Will Delist FOO on 2026-09-03",
        "description": "", "url": "https://example.com",
        "source_name": a.BINANCE_SOURCE, "published_at": None,
        "importance": "high", "sentiment": "bearish", "coins": "FOO",
    }
    parsed = c._parse_article(raw)
    assert parsed["importance"] == "high"
    assert parsed["sentiment"] == "bearish"
    assert parsed["coins"] == "FOO"


def test_parse_article_falls_back_to_scorer_without_overrides():
    raw = {
        "id": "abc", "title": "Bitcoin surges to a record high",
        "description": "", "url": "https://example.com",
        "source_name": "Cointelegraph", "published_at": None,
    }
    parsed = c._parse_article(raw)
    assert parsed["sentiment"] == "bullish"
    assert parsed["coins"] == "BTC"


# --- fan-out isolation ----------------------------------------------------

@pytest.mark.asyncio
async def test_one_failing_source_does_not_sink_the_rest(monkeypatch):
    async def boom(client):
        raise RuntimeError("binance down")

    async def ok(client):
        return [{"id": "x", "title": "Upbit notice", "source_name": "Upbit Notices"}]

    async def empty(client, name, url):
        return []

    monkeypatch.setattr(a, "fetch_binance", boom)
    monkeypatch.setattr(a, "fetch_upbit", ok)
    monkeypatch.setattr(a, "_fetch_regulator", empty)

    items = await a.fetch_announcements()
    assert [i["title"] for i in items] == ["Upbit notice"]
