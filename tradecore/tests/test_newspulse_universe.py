"""Tests for NewsPulse Tier 2 coin attribution.

The bar is precision, not recall: a wrong ticker routes a headline to a
symbol someone might trade, which is worse than no ticker at all. The
false-positive cases below are all real — measured against 600 prod titles
before the rules were written.
"""
from __future__ import annotations

import json

import pytest

from app.modules.newspulse import collector as c
from app.modules.newspulse import universe as u


def _map(tickers=None, names=None) -> u.CoinMap:
    return u.build_coin_map(
        set(tickers or []),
        {n: (s, r) for n, s, r in (names or [])},
    )


# --- tickers: case is what separates the ticker from the word -------------

def test_lowercase_word_is_not_a_ticker():
    """253 of 876 exchange tickers are dictionary words (SOL, ADA, DOT,
    LINK, DOGE, THE, ON, IN). Only case keeps them apart from prose."""
    m = _map(tickers=["SOL", "DOT", "LINK", "THE", "ON"])
    assert m.extract("the sol link is on a dot") == []
    assert m.extract("SOL rallies") == ["SOL"]


def test_title_case_word_is_not_a_ticker():
    """Headlines are Title Case, so 'Link' must not read as LINK."""
    m = _map(tickers=["LINK", "MOVE", "NEAR"])
    assert m.extract("A Link Between Move And Near Term Flows") == []


@pytest.mark.parametrize("acronym", ["AI", "US", "ETF", "SEC", "CEO", "GENIUS", "ATM"])
def test_acronym_stopwords_never_attribute(acronym):
    """Unfiltered, AI (52 hits) and US (39) were the top two 'tickers' found
    across 600 prod titles, plus GENIUS (the Act) and ATM."""
    m = _map(tickers=[acronym, "BTC"])
    assert m.extract(f"{acronym} news as BTC moves") == ["BTC"]


def test_quote_suffix_is_stripped_from_pair_symbols():
    m = _map(tickers=["UNITREE", "ABC"])
    assert m.extract("Launch of UNITREEUSDT perpetual") == ["UNITREE"]
    assert m.extract("ABCUSDC listed") == ["ABC"]


def test_single_char_tickers_are_never_matched():
    m = _map(tickers=["S", "W", "BTC"])
    assert m.extract("S and W and BTC") == ["BTC"]


# --- names ----------------------------------------------------------------

def test_unambiguous_names_match_case_insensitively():
    """CoinDesk house style lowercases coin names: 'Solana leads bitcoin and
    ether higher'. Case-sensitive matching missed these and scored *below*
    the regex it replaced."""
    m = _map(names=[("Solana", "SOL", 5), ("Hyperliquid", "HYPE", 30)])
    assert m.extract("solana leads the pack") == ["SOL"]
    assert m.extract("Hyperliquid volumes climb") == ["HYPE"]


def test_ether_alias_resolves_to_eth():
    m = _map(names=[("Ethereum", "ETH", 2)])
    assert m.extract("bitcoin and ether traders wait") == ["ETH"]


def test_ambiguous_name_requires_title_case():
    """'Compound bets $52 million' is the protocol; 'compound interest' isn't."""
    m = _map(names=[("Compound", "COMP", 90)])
    assert m.extract("Compound bets $52 million on a pivot") == ["COMP"]
    assert m.extract("returns from compound interest") == []


def test_ambiguous_name_outside_top_rank_is_dropped_entirely():
    m = _map(names=[("Vision", "VSN", 400)])
    assert m.extract("Vision for the industry") == []


def test_lowercase_initial_name_is_rejected():
    """The CoinGecko top-500 genuinely contains a token named 'would', which
    matched 'the crypto world would keep turning'."""
    m = _map(names=[("would", "WOULD", 480)])
    assert m.extract("the crypto world would keep turning") == []


def test_short_names_are_rejected():
    """'Sui', 'Ondo', 'Core' are too short to match safely."""
    m = _map(names=[("Sui", "SUI", 60)])
    assert m.extract("Sui network grows") == []


# --- combined behaviour ---------------------------------------------------

def test_results_are_deduped_in_first_appearance_order():
    m = _map(tickers=["BTC", "ETH"], names=[("Bitcoin", "BTC", 1), ("Ethereum", "ETH", 2)])
    assert m.extract("Ethereum and Bitcoin rally; BTC leads ETH") == ["ETH", "BTC"]


def test_legacy_map_preserves_pre_tier2_behaviour():
    m = u.legacy_coin_map()
    assert m.extract("Bitcoin and BTC rally while Solana lags") == ["BTC", "SOL"]


# --- Redis load / fallback ------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    u._CACHE.clear()
    yield
    u._CACHE.clear()


@pytest.mark.asyncio
async def test_load_falls_back_to_legacy_when_redis_is_empty(monkeypatch, fake_redis):
    monkeypatch.setattr(u.redis_service, "get_redis", lambda: fake_redis)
    coin_map = await u.load_coin_map()
    assert coin_map.extract("Bitcoin rallies") == ["BTC"]
    # A fallback must not be cached — the next tick should retry Redis.
    assert "map" not in u._CACHE


@pytest.mark.asyncio
async def test_load_falls_back_when_redis_raises(monkeypatch):
    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(u.redis_service, "get_redis", boom)
    coin_map = await u.load_coin_map()
    assert coin_map.extract("Bitcoin rallies") == ["BTC"]


@pytest.mark.asyncio
async def test_load_builds_from_redis_and_caches(monkeypatch, fake_redis):
    await fake_redis.sadd(u.TICKERS_KEY, "HYPE", "AI")
    await fake_redis.hset(
        u.NAMES_KEY,
        mapping={"Ravencoin": json.dumps(["RVN", 300])},
    )
    monkeypatch.setattr(u.redis_service, "get_redis", lambda: fake_redis)

    coin_map = await u.load_coin_map()
    assert coin_map.extract("HYPE jumps as Ravencoin exploit lands") == ["HYPE", "RVN"]
    assert coin_map.extract("AI narrative returns") == []  # stopword survives Redis
    assert "map" in u._CACHE


# --- collector integration ------------------------------------------------

def test_parse_article_uses_supplied_coin_map():
    coin_map = _map(names=[("Hyperliquid", "HYPE", 30)])
    parsed = c._parse_article(
        {"id": "x", "title": "Hyperliquid volumes climb", "description": "",
         "url": "u", "source_name": "Test", "published_at": None},
        coin_map,
    )
    assert parsed["coins"] == "HYPE"


def test_parse_article_without_map_uses_legacy():
    parsed = c._parse_article(
        {"id": "x", "title": "Bitcoin rallies", "description": "",
         "url": "u", "source_name": "Test", "published_at": None},
    )
    assert parsed["coins"] == "BTC"


def test_ambiguous_blocklist_covers_min_name_len():
    """Regression: the blocklist was derived at length >=5 while MIN_NAME_LEN
    was 4, so 'Cash' escaped it and attributed 'cash equivalents' to CASH 12
    times across 600 prod titles. Any 4-char entry proves the gap is closed."""
    assert u.MIN_NAME_LEN == 4
    assert {"cash", "just", "four", "flow", "safe"} <= u.AMBIGUOUS_NAMES


def test_low_ranked_ambiguous_short_names_are_dropped():
    m = _map(names=[("CASH", "CASH", 224), ("Four", "FORM", 282)])
    assert m.extract("stablecoins as cash equivalents") == []
    assert m.extract("MoonPay adds Cash App Pay") == []
    assert m.extract("filled in the Four form") == []
