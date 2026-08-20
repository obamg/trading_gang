"""Pure-function tests for NewsPulse keyword scoring and feed parsing.

The regression bar here is the substring-matching bug: the original scorer
used ``word in text``, so "ath" fired on "gather", "ban" on "banking", and
"sec" on "second"/"sector". That tagged 45% of all articles high-impact
(Telegram fires on high) and skewed sentiment bearish. Every term set must
go through ``text.compile_terms``.
"""
from __future__ import annotations

import httpx
import pytest

from app.modules.newspulse import collector as c
from app.modules.newspulse import text as t


# --- the bug this module exists to prevent -------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Ethereum whales gather ahead of the network merge",  # 'ath' in gather
        "Traders chart a path to the next cycle",             # 'ath' in path
        "Rather than exit, long-term holders wait",           # 'ath' in rather
    ],
)
def test_ath_does_not_match_inside_other_words(text):
    assert t.distinct_hits(c.BULLISH_RE, text) == 0


@pytest.mark.parametrize(
    "text",
    [
        "HSBC executes first live banking transaction on Swift",  # 'ban' in banking
        "Urban treasury flows into digital assets",               # 'ban' in urban
        "OCC head promises rules by the second quarter",          # 'sec' in second
        "The mining sector sees renewed interest",                # 'sec' in sector
        "Wallet security improves after audit",                   # 'sec' in security
    ],
)
def test_ban_and_sec_do_not_match_inside_other_words(text):
    assert t.distinct_hits(c.BEARISH_RE, text) == 0
    assert c._score_importance(text, "") == "normal"


def test_standalone_terms_still_match():
    """Word boundaries must not break the legitimate acronym uses."""
    assert t.distinct_hits(c.BEARISH_RE, "SEC sues exchange") >= 1
    assert t.distinct_hits(c.BULLISH_RE, "BTC prints a new ATH") >= 1
    assert t.distinct_hits(c.BEARISH_RE, "China bans crypto mining") >= 1


# --- sentiment ------------------------------------------------------------

def test_sentiment_directions():
    assert c._score_sentiment("Bitcoin surges to record high", "") == "bullish"
    assert c._score_sentiment("Exchange hacked, funds drained", "") == "bearish"
    assert c._score_sentiment("Company appoints a new CFO", "") == "neutral"


def test_sentiment_neutral_when_balanced():
    """Mixed signals must not resolve to a direction: 'sec' vs 'approval'."""
    assert c._score_sentiment("SEC grants approval", "") == "neutral"


def test_sentiment_reads_description_too():
    assert c._score_sentiment("Quiet headline", "The token crashed after the exploit") == "bearish"


def test_repeated_term_counts_once():
    """Distinct-term counting stops one repeated word from outvoting the other side."""
    repeated = "surge surge surge surge"
    assert t.distinct_hits(c.BULLISH_RE, repeated) == 1
    assert c._score_sentiment(repeated, "hacked exploit crash") == "bearish"


# --- importance -----------------------------------------------------------

def test_single_strong_term_is_high_impact():
    assert c._score_importance("Protocol halts network after exploit", "") == "high"
    assert c._score_importance("SEC proposes new crypto rules", "") == "high"


def test_single_weak_term_is_not_high_impact():
    """One ambient term is the false-positive case that flooded Telegram."""
    assert c._score_importance("Binance lists a new trading pair", "") == "normal"
    assert c._score_importance("Fund reports a billion in volume", "") == "normal"


def test_weak_terms_corroborate_to_high():
    score = c._score_importance("Blackrock ETF inflows near a billion as Coinbase custody grows", "")
    assert score == "high"


def test_non_crypto_story_is_not_high_impact():
    """Regression: 'Moderna Stock Doubles on Cancer Breakthrough' scored high."""
    assert c._score_importance("Moderna Stock Doubles on Cancer Breakthrough", "") == "normal"


def test_multiword_phrase_beats_its_constituents():
    """Longest-first ordering: 'all-time high' is consumed as one term."""
    assert t.distinct_hits(c.BULLISH_RE, "Bitcoin hits an all-time high") == 1


# --- coin extraction ------------------------------------------------------

def test_extract_coins_normalizes_and_dedupes():
    """Attribution itself is covered in test_newspulse_universe.py; this pins
    that the collector still resolves names and tickers to one symbol each."""
    from app.modules.newspulse.universe import legacy_coin_map

    coins = c._extract_coins("Bitcoin and BTC rally while Solana lags", "", legacy_coin_map())
    assert coins == ["BTC", "SOL"]


# --- conditional GET ------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_validators():
    c._FEED_VALIDATORS.clear()
    yield
    c._FEED_VALIDATORS.clear()


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.mark.asyncio
async def test_conditional_get_stores_and_replays_validators():
    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(
            200,
            text="<rss><channel></channel></rss>",
            headers={"ETag": 'W/"abc"', "Last-Modified": "Wed, 19 Aug 2026 10:00:00 GMT"},
        )

    async with _client(httpx.MockTransport(handle)) as client:
        await c._fetch_one_feed(client, "Test", "https://example.com/rss")
        await c._fetch_one_feed(client, "Test", "https://example.com/rss")

    assert "if-none-match" not in seen[0]
    assert seen[1]["if-none-match"] == 'W/"abc"'
    assert seen[1]["if-modified-since"] == "Wed, 19 Aug 2026 10:00:00 GMT"


@pytest.mark.asyncio
async def test_304_yields_no_items_and_does_not_raise():
    async with _client(httpx.MockTransport(lambda r: httpx.Response(304))) as client:
        assert await c._fetch_one_feed(client, "Test", "https://example.com/rss") == []


@pytest.mark.asyncio
async def test_stale_validators_dropped_when_origin_stops_sending_them():
    c._FEED_VALIDATORS["https://example.com/rss"] = {"If-None-Match": 'W/"old"'}

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<rss><channel></channel></rss>")

    async with _client(httpx.MockTransport(handle)) as client:
        await c._fetch_one_feed(client, "Test", "https://example.com/rss")

    assert "https://example.com/rss" not in c._FEED_VALIDATORS


@pytest.mark.asyncio
async def test_fetch_failure_is_swallowed():
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(httpx.MockTransport(handle)) as client:
        assert await c._fetch_one_feed(client, "Test", "https://example.com/rss") == []


# --- RSS parsing ----------------------------------------------------------

RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Exchange halts withdrawals after exploit</title>
    <link>https://example.com/a</link>
    <guid>https://example.com/a</guid>
    <description>&lt;p&gt;Roughly $10M drained.&lt;/p&gt;</description>
    <pubDate>Wed, 19 Aug 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Missing link is skipped</title>
    <guid>no-link</guid>
  </item>
</channel></rss>"""


def test_parse_rss_strips_html_and_skips_incomplete_items():
    items = t.parse_rss_xml(RSS_SAMPLE, "Test")
    assert len(items) == 1
    assert items[0]["description"] == "Roughly $10M drained."
    assert items[0]["published_at"].year == 2026
    assert len(items[0]["id"]) == 40  # sha1 hex, fits source_id varchar(64)


def test_parse_rss_survives_malformed_xml():
    assert t.parse_rss_xml("not xml at all", "Test") == []


# --- backfill guard -------------------------------------------------------

def test_notify_max_age_is_short_enough_to_block_backfill():
    """Announcement endpoints return a page of history (Upbit's newest trade
    notice was 6 days old when Tier 1 was built). A first run must not replay
    that to Telegram as breaking news."""
    assert c.NOTIFY_MAX_AGE.total_seconds() <= 3600
