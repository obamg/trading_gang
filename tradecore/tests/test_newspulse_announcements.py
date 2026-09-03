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

    async def empty_source(client):
        return []

    monkeypatch.setattr(a, "fetch_binance", boom)
    monkeypatch.setattr(a, "fetch_upbit", ok)
    monkeypatch.setattr(a, "_fetch_regulator", empty)
    # Every other source is stubbed too — without this the fan-out reaches the
    # live Bithumb/Bybit/OKX/KuCoin endpoints, making a unit test network-bound
    # and flaky. Any source added to fetch_announcements must be stubbed here.
    for name in ("fetch_bithumb", "fetch_bybit", "fetch_okx", "fetch_kucoin"):
        monkeypatch.setattr(a, name, empty_source)

    items = await a.fetch_announcements()
    assert [i["title"] for i in items] == ["Upbit notice"]


# --- 2026-09-03: Bithumb / Bybit / OKX / KuCoin ---------------------------
#
# Added to multiply the primary-source sample, which is the binding constraint
# on the newsevent forward test. Every payload below is a trimmed copy of a
# real response captured from the prod VPS on 2026-09-03.
#
# The load-bearing property for all four: newsevent treats ANY item from a
# primary source as a tradeable news leg, bypassing the importance scorer. So
# a fetcher emitting promo noise creates trades out of nothing.

from datetime import timezone as _tz  # noqa: E402


# --- ticker extraction ----------------------------------------------------

def test_all_paren_tickers_reads_every_parenthetical():
    """Bithumb designates several coins per notice, one per parenthetical.
    The first-group-only extractor tuned for Upbit silently dropped INJ."""
    title = "코어(CORE), 인젝티브(INJ) 거래유의종목 지정"
    assert a.extract_all_paren_tickers(title) == ["CORE", "INJ"]
    # The Upbit-tuned extractor is why this function had to exist.
    assert a.extract_paren_tickers(title) == ["CORE"]


def test_all_paren_tickers_still_rejects_upbit_market_list():
    """Widening to every parenthetical must not start eating the trailing
    (KRW, BTC, USDT 마켓) quote-market list."""
    title = "니어프로토콜(NEAR) KRW 마켓 추가 (KRW, BTC, USDT 마켓)"
    assert a.extract_all_paren_tickers(title) == ["NEAR"]


def test_all_paren_tickers_rejects_dates_and_times():
    assert a.extract_all_paren_tickers("코스모스(ATOM) 입출금 중지 (09/03 재개)") == ["ATOM"]
    assert a.extract_all_paren_tickers("리스트 (2026-08-19)") == []


def test_venue_names_are_not_tickers():
    """Live-data defect: every venue leads its headline with its own name, and
    the upper-token extractor read OKX and EEA as coins."""
    coins = a.extract_upper_tickers("OKX to list KAT and OKB on spot in EEA")
    assert "OKX" not in coins and "EEA" not in coins
    # OKB is a real tradeable token and must survive.
    assert coins == ["KAT", "OKB"]


# --- announcement classification -----------------------------------------

@pytest.mark.parametrize(
    "title,expected",
    [
        ("New listing: PONSUSDT Perpetual Contract, with up to 20x leverage", "bullish"),
        ("OKX to list SLX/USDT (Solstice) for spot trading", "bullish"),
        ("World Premiere: Cluster Protocol (CP) Listed on KuCoin", "bullish"),
        ("HODLer Airdrops: Aligned (ALIGN) World Premiere Listing on KuCoin", "bullish"),
        ("Delisting of ASPUSDT Perpetual Contract", "bearish"),
        ("OKX to delist ULTI, GEAR, VRA, DAO, CXT and ELON in EEA", "bearish"),
        # Promo noise that ships under the SAME announcement type as listings.
        ("USDT Token Splash— Grab a share of the 100000 USDT prize pool .", None),
        ("OKX Card x McLaren Technology Centre Giveaway Terms and Conditions", None),
        ("KuCoin Copy Trading Upgrade: Now Supporting 630 Contract Trading Pairs", None),
        # A migration is not a delisting.
        ("OKX to support AERGO crypto migration", None),
        # Non-crypto instruments published in the crypto listing feed.
        ("New listing: CVXSTOCKUSDT TradFi Perpetual Contract", None),
        ("KuCoin Futures Will List KUAISHOUUSDT and SHEINHKDUSDT Stock Index Perpetual Contracts", None),
    ],
)
def test_classify_announcement(title, expected):
    assert a.classify_announcement(title, "bullish") == expected


def test_delisting_wins_over_listing_verb():
    """'delist ... and relist' and similar tails must not read as bullish."""
    assert a.classify_announcement("Bybit will delist X and list Y", "bullish") == "bearish"


def test_listing_on_does_not_match_inside_delisting():
    """compile_terms anchors on \\b, so `listing on` cannot match the tail of
    'delisting on' — the over-permissive-substring bug this repo keeps hitting."""
    assert a.classify_announcement("Notice on delisting on 2026-09-10", "bullish") == "bearish"


# --- Bithumb --------------------------------------------------------------

BITHUMB_PAYLOAD = [
    {"categories": ["거래유의"], "title": "코어(CORE), 인젝티브(INJ) 거래유의종목 지정",
     "pc_url": "https://feed.bithumb.com/notice/1654001", "published_at": "2026-09-01 17:00:00"},
    {"categories": ["입출금"], "title": "코스모스(ATOM) 입출금 일시 중지 안내 (09/03 재개)",
     "pc_url": "https://feed.bithumb.com/notice/1654675", "published_at": "2026-09-03 09:35:00"},
    {"categories": ["이벤트"], "title": "빗썸 AI 사용하고 최대 300만원 상당 비트코인 받자!",
     "pc_url": "https://feed.bithumb.com/notice/1654100", "published_at": "2026-09-01 17:13:21"},
    {"categories": ["거래지원"], "title": "니어프로토콜(NEAR) 원화 마켓 추가",
     "pc_url": "https://feed.bithumb.com/notice/1654200", "published_at": "2026-09-02 10:00:00"},
]


@pytest.mark.asyncio
async def test_bithumb_emits_only_tradeable_notices():
    def handler(request):
        return httpx.Response(200, json=BITHUMB_PAYLOAD)

    async with _client(handler) as client:
        items = await a.fetch_bithumb(client)

    by_title = {i["title"]: i for i in items}
    # deposit-pause and promo are routine noise and must never become legs
    assert len(items) == 2, [i["title"] for i in items]
    caution = by_title["코어(CORE), 인젝티브(INJ) 거래유의종목 지정"]
    assert caution["sentiment"] == "bearish"
    assert caution["coins"] == "CORE,INJ"
    assert caution["source_name"] == a.BITHUMB_SOURCE
    assert caution["importance"] == "high"
    listing = by_title["니어프로토콜(NEAR) 원화 마켓 추가"]
    assert listing["sentiment"] == "bullish" and listing["coins"] == "NEAR"


@pytest.mark.asyncio
async def test_bithumb_published_at_is_converted_from_kst():
    """Bithumb stamps wall-clock Seoul time with no offset. Reading it as UTC
    would shift every notice 9h and push it outside newsevent's 15-min window."""
    def handler(request):
        return httpx.Response(200, json=[BITHUMB_PAYLOAD[0]])

    async with _client(handler) as client:
        items = await a.fetch_bithumb(client)

    published = items[0]["published_at"]
    assert published.tzinfo is not None
    assert published.astimezone(_tz.utc).isoformat() == "2026-09-01T08:00:00+00:00"


@pytest.mark.asyncio
async def test_bithumb_tolerates_unexpected_shape():
    def handler(request):
        return httpx.Response(200, json={"error": "nope"})

    async with _client(handler) as client:
        assert await a.fetch_bithumb(client) == []


@pytest.mark.asyncio
async def test_bithumb_tolerates_http_error():
    def handler(request):
        return httpx.Response(503)

    async with _client(handler) as client:
        assert await a.fetch_bithumb(client) == []


# --- Bybit / OKX / KuCoin -------------------------------------------------

@pytest.mark.asyncio
async def test_bybit_filters_promos_and_keeps_listings():
    payload = {"retCode": 0, "result": {"list": [
        {"title": "New listing: PONSUSDT Perpetual Contract, with up to 20x leverage",
         "url": "https://announcements.bybit.com/a/pons", "publishTime": 1788000000000},
        {"title": "USDT Token Splash— Grab a share of the 100000 USDT prize pool .",
         "url": "https://announcements.bybit.com/a/splash", "publishTime": 1788000001000},
    ]}}

    def handler(request):
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        items = await a.fetch_bybit(client)

    # two ann types are fetched, so the single listing appears once per type
    titles = {i["title"] for i in items}
    assert titles == {"New listing: PONSUSDT Perpetual Contract, with up to 20x leverage"}
    assert all(i["source_name"] == a.BYBIT_SOURCE for i in items)
    assert items[0]["coins"] == "PONS"          # USDT suffix stripped


@pytest.mark.asyncio
async def test_okx_parses_string_millis_timestamp():
    """OKX sends pTime as a STRING of epoch millis; Bybit/KuCoin send ints."""
    payload = {"code": "0", "data": [{"details": [
        {"title": "OKX to list SLX/USDT (Solstice) for spot trading",
         "url": "https://okx.com/a/slx", "annType": "announcements-new-listings",
         "pTime": "1783648816124"},
    ]}]}

    def handler(request):
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        items = await a.fetch_okx(client)

    assert items and items[0]["coins"] == "SLX"
    assert items[0]["published_at"].year == 2026


@pytest.mark.asyncio
async def test_kucoin_parses_items():
    payload = {"code": "200000", "data": {"items": [
        {"annId": 329319, "annTitle": "KuCoin Futures New Listing: CPUSDT Perpetual Contract",
         "annUrl": "https://kucoin.com/a/cp", "cTime": 1788000000000},
    ]}}

    def handler(request):
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        items = await a.fetch_kucoin(client)

    assert items and items[0]["coins"] == "CP"
    assert items[0]["source_name"] == a.KUCOIN_SOURCE


def test_ms_to_dt_accepts_int_and_string_and_rejects_junk():
    assert a._ms_to_dt(1788000000000).year == 2026
    assert a._ms_to_dt("1788000000000").year == 2026
    assert a._ms_to_dt("not-a-number") is None
    assert a._ms_to_dt(0) is None
    assert a._ms_to_dt(None) is None


# --- cross-module contract ------------------------------------------------

def test_every_announcement_source_is_registered_primary():
    """newsevent gates its news leg on PRIMARY_SOURCES. A source added here but
    not registered there is collected and then silently never traded."""
    from app.modules.majorsbot import newsevent as ne

    emitted = {
        a.BINANCE_SOURCE, a.UPBIT_SOURCE, a.BITHUMB_SOURCE,
        a.BYBIT_SOURCE, a.OKX_SOURCE, a.KUCOIN_SOURCE,
    }
    assert emitted <= set(ne.PRIMARY_SOURCES)
