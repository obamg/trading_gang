"""NewsPulse Tier 2 — coin attribution universe.

Replaces the hardcoded 22-symbol regex that left 55% of articles with no
ticker. Two sources, refreshed daily into Redis:

- **Tickers** from ``listingwatch.exchanges.fetch_all()`` — the instrument
  lists we already poll, so no new upstream dependency.
- **Names** from CoinGecko's top-500 by market cap, because crypto media
  overwhelmingly writes the *name* ("Hyperliquid", "Ravencoin"), not the
  ticker.

Measured on 600 real prod titles: 40.2% → 44.7% attributed.

Matching is deliberately conservative — this repo has shipped an
over-permissive matcher twice (see ``text.compile_terms``), and a wrong
ticker here is worse than none, because it routes a headline to a symbol
someone might trade.

Three rules, each earning its keep against measured false positives:

1. **Tickers match ALL-CAPS and case-sensitively.** 253 of 876 exchange
   tickers are ordinary English words (SOL, ADA, DOT, LINK, DOGE, THE, ON,
   IN, US). Case is what separates the ticker from the word — prose writes
   "sol", never "SOL". A dictionary blocklist is NOT usable here; it would
   delete the most important tickers.
2. **ACRONYM_STOPWORDS** removes the ALL-CAPS tokens that still collide.
   Unfiltered, the top two "tickers" found across 600 titles were AI (52
   hits, meaning artificial intelligence) and US (39, meaning the country),
   plus GENIUS (the Act, not a token) and ATM.
3. **Names split by ambiguity.** A name that is not an ordinary English word
   ("Hyperliquid", "Ravencoin", "Centrifuge") matches case-insensitively,
   which is required because CoinDesk house style lowercases coin names
   ("Solana leads bitcoin and ether higher"). A name that IS an ordinary
   English word (``AMBIGUOUS_NAMES``) must appear in Title Case *and* belong
   to a top-``AMBIGUOUS_MAX_RANK`` coin — "Compound bets $52 million" counts,
   "compound interest" does not.

If Redis holds nothing (first boot, or every refresh has failed), matching
falls back to ``LEGACY_*`` — the original 22 symbols — so attribution can
degrade but never disappear.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from app.config import settings
from app.logging_config import log
from app.services import redis_service

TICKERS_KEY = "newspulse:tickers"
NAMES_KEY = "newspulse:coin_names"
# Generous: a week of failed refreshes still leaves attribution working.
UNIVERSE_TTL_SECONDS = 7 * 24 * 3600

CG_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
CG_PAGES = 2
CG_PER_PAGE = 250

MIN_TICKER_LEN = 2      # 1-char tickers (S, W, T, C…) are unmatchable safely
MIN_NAME_LEN = 4
AMBIGUOUS_MAX_RANK = 150

QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")

# ALL-CAPS tokens that appear in crypto headlines but are never the subject.
# Some (TRUMP, HOOD, SPY, COIN) *are* real tickers — they are excluded anyway
# because the headline sense dominates and a false attribution is costlier
# than a missed one.
ACRONYM_STOPWORDS = frozenset({
    "AI", "US", "UK", "EU", "UN", "CEO", "CFO", "CTO", "COO", "ETF", "ETP",
    "SEC", "CFTC", "DOJ", "IRS", "FBI", "FDIC", "OCC", "FASB", "GDP", "CPI",
    "PCE", "FOMC", "API", "NFT", "DAO", "TVL", "OTC", "KYC", "AML", "IPO",
    "ICO", "IEO", "TGE", "P2P", "VIP", "AMA", "FAQ", "USD", "EUR", "GBP",
    "JPY", "KRW", "AMM", "L1", "L2", "ATH", "ATM", "GENIUS", "CLARITY",
    "ONE", "NOW", "NOT", "THE", "AND", "FOR", "NEW", "ALL", "ANY", "TOP",
    "OWN", "ON", "IN", "AT", "BE", "ME", "GO", "RE", "SO", "UP", "OG", "LA",
    "MA", "TRUMP", "TRUTH", "SPY", "HOOD", "COIN", "BABA", "DELL", "MSTR",
})

# Coin names that are also ordinary English words. Derived by intersecting
# the CoinGecko top-500 names with /usr/share/dict/words at length >=
# MIN_NAME_LEN; frozen here so production needs no wordlist. Regenerate (at
# the same minimum length!) when refreshing this list — deriving it at >=5
# while MIN_NAME_LEN was 4 left a gap that attributed "cash equivalents" to
# CASH twelve times across 600 prod titles.
AMBIGUOUS_NAMES = frozenset({
    "anvil", "aster", "avalanche", "babylon", "beam", "bedrock", "bonk",
    "canton", "capricorn", "cash", "centrifuge", "comedian", "compound",
    "conflux", "cross", "dash", "derive", "diem", "dola", "dusk", "flare",
    "flow", "fluid", "four", "gala", "gate", "genius", "gnosis", "golem",
    "grass", "hedera", "humanity", "hydration", "immutable", "iota",
    "jupiter", "just", "kava", "kite", "lighter", "linea", "mantis",
    "mantle", "midnight", "monad", "morpho", "nexus", "oasis", "olympus",
    "optimism", "orca", "pearl", "pendle", "perle", "pharos", "plasma",
    "plume", "purr", "quant", "rain", "render", "river", "safe", "score",
    "seeker", "sentient", "shuffle", "sonic", "soon", "spark", "stellar",
    "stronghold", "tagger", "talus", "tether", "troll", "tron", "turbo",
    "ultima", "velo", "velvet", "venus", "vision", "walrus", "wormhole",
})

# Media shorthand that is not any coin's registered name.
NAME_ALIASES = {"ether": "ETH", "btc": "BTC"}

# Fallback when Redis has no universe yet — the pre-Tier-2 behaviour.
LEGACY_NAMES = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP",
    "cardano": "ADA", "dogecoin": "DOGE", "avalanche": "AVAX",
    "polkadot": "DOT", "polygon": "MATIC", "chainlink": "LINK",
    "uniswap": "UNI", "aave": "AAVE",
}
LEGACY_TICKERS = frozenset({
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC",
    "LINK", "UNI", "AAVE",
})

UPPER_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,14}\b")


def _alternation(terms) -> str:
    return "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))


@dataclass(frozen=True)
class CoinMap:
    """Compiled matchers. Built once per universe version, not per article."""

    tickers: frozenset[str]
    plain_names: dict[str, str]      # lowercase name -> symbol, case-insensitive
    ambiguous_names: dict[str, str]  # Title-Case name -> symbol, case-sensitive
    _plain_re: re.Pattern[str] | None
    _ambiguous_re: re.Pattern[str] | None

    def extract(self, text: str) -> list[str]:
        """Return attributed symbols, deduped, in first-appearance order."""
        hits: list[tuple[int, str]] = []

        for m in UPPER_TOKEN_RE.finditer(text):
            token = m.group(0)
            for suffix in QUOTE_SUFFIXES:
                if token.endswith(suffix) and len(token) > len(suffix) + 1:
                    token = token[: -len(suffix)]
                    break
            if token in self.tickers:
                hits.append((m.start(), token))

        if self._plain_re is not None:
            for m in self._plain_re.finditer(text):
                symbol = self.plain_names.get(m.group(0).lower())
                if symbol:
                    hits.append((m.start(), symbol))

        if self._ambiguous_re is not None:
            for m in self._ambiguous_re.finditer(text):
                symbol = self.ambiguous_names.get(m.group(0))
                if symbol:
                    hits.append((m.start(), symbol))

        found: list[str] = []
        seen: set[str] = set()
        for _, symbol in sorted(hits, key=lambda hit: hit[0]):
            if symbol not in seen:
                seen.add(symbol)
                found.append(symbol)
        return found


def build_coin_map(
    tickers: set[str], names: dict[str, tuple[str, int]]
) -> CoinMap:
    """Assemble a CoinMap. ``names`` maps name -> (symbol, market-cap rank)."""
    safe_tickers = frozenset(
        t for t in tickers
        if len(t) >= MIN_TICKER_LEN and t not in ACRONYM_STOPWORDS
    )

    plain: dict[str, str] = dict(NAME_ALIASES)
    ambiguous: dict[str, str] = {}
    for name, (symbol, rank) in names.items():
        if len(name) < MIN_NAME_LEN or not name[:1].isupper():
            # Lowercase-initial "names" are not proper nouns — the CoinGecko
            # top-500 genuinely contains a token called "would".
            continue
        if name.lower() in AMBIGUOUS_NAMES:
            if rank and rank <= AMBIGUOUS_MAX_RANK:
                ambiguous[name] = symbol
        else:
            plain[name.lower()] = symbol

    return CoinMap(
        tickers=safe_tickers,
        plain_names=plain,
        ambiguous_names=ambiguous,
        _plain_re=(
            re.compile(rf"\b(?:{_alternation(plain)})\b", re.IGNORECASE)
            if plain else None
        ),
        _ambiguous_re=(
            re.compile(rf"\b(?:{_alternation(ambiguous)})\b")
            if ambiguous else None
        ),
    )


def legacy_coin_map() -> CoinMap:
    """Pre-Tier-2 behaviour, used when Redis holds no universe."""
    return build_coin_map(
        set(LEGACY_TICKERS),
        {n.title(): (s, 1) for n, s in LEGACY_NAMES.items()},
    )


# --- refresh --------------------------------------------------------------

async def _fetch_coingecko_names() -> dict[str, tuple[str, int]]:
    headers = {}
    cg_key = getattr(settings, "coingecko_api_key", "") or ""
    if cg_key:
        headers["x-cg-demo-api-key"] = cg_key

    names: dict[str, tuple[str, int]] = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for page in range(1, CG_PAGES + 1):
            try:
                resp = await client.get(
                    CG_MARKETS_URL,
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": CG_PER_PAGE,
                        "page": page,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                rows = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("newspulse_coingecko_names_failed", page=page, err=str(e))
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                name = (row.get("name") or "").strip()
                symbol = (row.get("symbol") or "").strip().upper()
                if name and symbol:
                    names[name] = (symbol, row.get("market_cap_rank") or 9999)
    return names


async def _fetch_exchange_tickers() -> set[str]:
    from app.modules.listingwatch import exchanges

    try:
        listed = await exchanges.fetch_all()
    except Exception as e:
        log.warning("newspulse_universe_exchange_fetch_failed", err=str(e))
        return set()
    return {s.base_asset.upper() for s in listed if s.base_asset}


async def refresh_universe() -> tuple[int, int]:
    """Repopulate the Redis universe. Returns (n_tickers, n_names).

    A source that fails leaves its existing Redis key untouched rather than
    blanking it — stale attribution beats none.
    """
    r = redis_service.get_redis()
    tickers = await _fetch_exchange_tickers()
    names = await _fetch_coingecko_names()

    if tickers:
        await r.delete(TICKERS_KEY)
        await r.sadd(TICKERS_KEY, *tickers)
        await r.expire(TICKERS_KEY, UNIVERSE_TTL_SECONDS)
    if names:
        await r.delete(NAMES_KEY)
        await r.hset(
            NAMES_KEY,
            mapping={n: json.dumps([s, rank]) for n, (s, rank) in names.items()},
        )
        await r.expire(NAMES_KEY, UNIVERSE_TTL_SECONDS)

    log.info(
        "newspulse_universe_refreshed", tickers=len(tickers), names=len(names)
    )
    _CACHE.clear()
    return len(tickers), len(names)


# --- load (in-process cache) ----------------------------------------------

# Rebuilding an ~900-alternative regex per tick would be wasteful, so the
# compiled CoinMap is held here and invalidated by refresh_universe().
_CACHE: dict[str, CoinMap] = {}


async def load_coin_map() -> CoinMap:
    cached = _CACHE.get("map")
    if cached is not None:
        return cached

    try:
        r = redis_service.get_redis()
        raw_tickers = await r.smembers(TICKERS_KEY) or set()
        raw_names = await r.hgetall(NAMES_KEY) or {}
    except Exception as e:
        log.warning("newspulse_universe_load_failed", err=str(e))
        return legacy_coin_map()

    tickers = {_as_str(t).upper() for t in raw_tickers}
    names: dict[str, tuple[str, int]] = {}
    for name, payload in raw_names.items():
        try:
            symbol, rank = json.loads(_as_str(payload))
        except (ValueError, TypeError):
            continue
        names[_as_str(name)] = (symbol, rank)

    if not tickers and not names:
        return legacy_coin_map()

    coin_map = build_coin_map(tickers, names)
    _CACHE["map"] = coin_map
    return coin_map


def _as_str(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def run_universe_refresh() -> None:
    try:
        await refresh_universe()
    except Exception as e:
        log.error("newspulse_universe_refresh_failed", error=str(e))
