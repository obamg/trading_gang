"""MajorsBot — market data layer.

Bybit v5 REST klines (1h, linear) + funding history, cached in Redis so the
5-minute tick is cheap:

  majorsbot:klines:{symbol}   JSON {"bars": [...], "live": {...}} — refetched
                              only when the cache is missing the latest
                              COMPLETED hour bar (i.e. ~once per hour/symbol).
  majorsbot:funding:{symbol}  JSON [[ts_ms, rate], ...] ascending — 15-min TTL
                              (funding events land every 8h; a 15-min lag can't
                              miss one before the relevant bar closes).

Failures degrade per-symbol: a fetch error returns the stale cache when one
exists, else None — the engine skips the symbol for this tick and logs.

Also home to the pure rolling-window helpers (TR/ATR, prior-window mean TR%,
prior-window median volume) that mirror the bake-off's Sym precomputations.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass

import httpx

from app.config import settings as app_settings
from app.logging_config import log
from app.services import redis_service

H1_MS = 3_600_000
M5_MS = 300_000

KLINES_KEY = "majorsbot:klines:{symbol}"
M5_KLINES_KEY = "majorsbot:klines5m:{symbol}"
FUNDING_KEY = "majorsbot:funding:{symbol}"
KLINES_TTL_S = 2 * 3600     # safety TTL; freshness is decided by bar coverage
M5_KLINES_TTL_S = 900
FUNDING_TTL_S = 900

KLINE_LIMIT = 1000          # ≈41 days of 1h bars — covers the 720-bar lookback
M5_KLINE_LIMIT = 1000       # ≈3.5 days of 5m bars — covers the 288-bar lookback
FUNDING_LIMIT = 200         # ≈66 days of 8h events — covers the 90-event window
_TIMEOUT_S = 10.0


@dataclass
class MarketData:
    symbol: str
    bars: list[dict]                                # completed 1h bars, oldest-first {t,o,h,l,c,v}
    live_bar: dict | None = None                    # current forming bar {"t", "o"}
    funding: list[tuple[int, float]] | None = None  # (ts_ms, rate) oldest-first


# ---------- Bybit fetchers ----------


async def _fetch_klines(symbol: str) -> tuple[list[dict], dict | None] | None:
    """Returns (completed_bars_oldest_first, live_bar) or None on failure."""
    url = f"{app_settings.bybit_rest_url}/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": "60",
        "limit": KLINE_LIMIT,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("majorsbot_klines_fetch_failed", symbol=symbol, err=str(e))
        return None
    if not isinstance(data, dict) or data.get("retCode") != 0:
        log.warning(
            "majorsbot_klines_bad_response",
            symbol=symbol,
            ret_code=data.get("retCode") if isinstance(data, dict) else None,
        )
        return None
    rows = (data.get("result") or {}).get("list") or []
    now_ms = int(time.time() * 1000)
    bars: list[dict] = []
    live: dict | None = None
    for row in reversed(rows):  # Bybit returns newest-first → flip ascending
        try:
            t = int(row[0])
            bar = {
                "t": t,
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            }
        except (IndexError, TypeError, ValueError):
            continue
        if t + H1_MS <= now_ms:
            bars.append(bar)
        else:
            live = {"t": t, "o": bar["o"]}
    return bars, live


async def _fetch_funding(symbol: str) -> list[tuple[int, float]] | None:
    url = f"{app_settings.bybit_rest_url}/v5/market/funding/history"
    params = {"category": "linear", "symbol": symbol, "limit": FUNDING_LIMIT}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("majorsbot_funding_fetch_failed", symbol=symbol, err=str(e))
        return None
    if not isinstance(data, dict) or data.get("retCode") != 0:
        log.warning(
            "majorsbot_funding_bad_response",
            symbol=symbol,
            ret_code=data.get("retCode") if isinstance(data, dict) else None,
        )
        return None
    rows = (data.get("result") or {}).get("list") or []
    events: list[tuple[int, float]] = []
    for row in rows:
        try:
            events.append(
                (int(row["fundingRateTimestamp"]), float(row["fundingRate"]))
            )
        except (KeyError, TypeError, ValueError):
            continue
    events.sort(key=lambda e: e[0])
    return events


# ---------- cached access ----------


def _last_completed_bar_start(now_ms: int) -> int:
    return (now_ms // H1_MS) * H1_MS - H1_MS


async def get_market_data(symbol: str) -> MarketData | None:
    """Klines + funding for one symbol, cache-first. None = no usable klines
    (the engine must skip the symbol this tick). funding may be None on its
    own — fundingfade logic degrades, volevent keeps running."""
    r = redis_service.get_redis()
    now_ms = int(time.time() * 1000)

    kl: dict | None = None
    raw = await r.get(KLINES_KEY.format(symbol=symbol))
    if raw is not None:
        try:
            cached = json.loads(_decode(raw))
            bars = cached.get("bars") or []
            if bars and int(bars[-1]["t"]) >= _last_completed_bar_start(now_ms):
                kl = cached
        except (ValueError, KeyError, TypeError):
            raw = None
    if kl is None:
        fetched = await _fetch_klines(symbol)
        if fetched is not None:
            bars, live = fetched
            kl = {"bars": bars, "live": live}
            await r.set(
                KLINES_KEY.format(symbol=symbol), json.dumps(kl), ex=KLINES_TTL_S
            )
        elif raw is not None:
            # Stale cache beats nothing: management is a no-op on old bars.
            try:
                kl = json.loads(_decode(raw))
                log.warning("majorsbot_klines_stale_cache_used", symbol=symbol)
            except ValueError:
                kl = None
    if kl is None or not kl.get("bars"):
        return None

    funding: list[tuple[int, float]] | None = None
    fraw = await r.get(FUNDING_KEY.format(symbol=symbol))
    if fraw is not None:
        try:
            funding = [(int(t), float(rate)) for t, rate in json.loads(_decode(fraw))]
        except (ValueError, TypeError):
            funding = None
    if funding is None:
        funding = await _fetch_funding(symbol)
        if funding is not None:
            await r.set(
                FUNDING_KEY.format(symbol=symbol),
                json.dumps(funding),
                ex=FUNDING_TTL_S,
            )

    return MarketData(
        symbol=symbol, bars=kl["bars"], live_bar=kl.get("live"), funding=funding
    )


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v


# ---------- 5-minute bars (newsevent only) ----------
#
# volevent/fundingfade run on 1h bars and must keep doing so — their
# parameters mirror a 12-month backtest. newsevent pairs a volume leg with a
# news leg inside a 15-minute window, which 1h bars simply cannot resolve, so
# it gets its own faster series. Nothing above this line changes.


async def _fetch_klines_5m(symbol: str) -> tuple[list[dict], dict | None] | None:
    """Same shape as ``_fetch_klines`` but interval=5. (completed, live)."""
    url = f"{app_settings.bybit_rest_url}/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": "5",
        "limit": M5_KLINE_LIMIT,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            payload = r.json()
    except Exception as e:
        log.warning("majorsbot_klines5m_fetch_failed", symbol=symbol, err=str(e))
        return None
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        log.warning(
            "majorsbot_klines5m_bad_response",
            symbol=symbol,
            ret_code=payload.get("retCode") if isinstance(payload, dict) else None,
        )
        return None

    rows = (payload.get("result") or {}).get("list") or []
    now_ms = int(time.time() * 1000)
    bars: list[dict] = []
    live: dict | None = None
    for row in reversed(rows):  # Bybit returns newest-first → flip ascending
        try:
            t = int(row[0])
            bar = {
                "t": t,
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            }
        except (IndexError, TypeError, ValueError):
            continue
        if t + M5_MS <= now_ms:
            bars.append(bar)
        else:
            live = {"t": t, "o": bar["o"]}
    return bars, live


def _last_completed_5m_start(now_ms: int) -> int:
    return (now_ms // M5_MS) * M5_MS - M5_MS


async def get_fast_market_data(symbol: str) -> MarketData | None:
    """Completed 5-minute bars for one symbol, cache-first.

    Refetched only when the cache lacks the latest completed 5m bar, so the
    1-minute newsevent tick costs at most one REST call per symbol per 5
    minutes. ``funding`` is always None here — newsevent does not use it.
    """
    r = redis_service.get_redis()
    now_ms = int(time.time() * 1000)
    key = M5_KLINES_KEY.format(symbol=symbol)

    kl: dict | None = None
    raw = await r.get(key)
    if raw is not None:
        try:
            cached = json.loads(_decode(raw))
            bars = cached.get("bars") or []
            if bars and int(bars[-1]["t"]) >= _last_completed_5m_start(now_ms):
                kl = cached
        except (ValueError, KeyError, TypeError):
            raw = None
    if kl is None:
        fetched = await _fetch_klines_5m(symbol)
        if fetched is not None:
            bars, live = fetched
            kl = {"bars": bars, "live": live}
            await r.set(key, json.dumps(kl), ex=M5_KLINES_TTL_S)
        elif raw is not None:
            try:
                kl = json.loads(_decode(raw))
                log.warning("majorsbot_klines5m_stale_cache_used", symbol=symbol)
            except ValueError:
                kl = None
    if kl is None or not kl.get("bars"):
        return None

    return MarketData(
        symbol=symbol, bars=kl["bars"], live_bar=kl.get("live"), funding=None
    )


# ---------- pure rolling-window helpers (bake-off parity) ----------


def true_ranges(bars: list[dict]) -> list[float]:
    """TR per bar: max(h−l, |h−prev_close|, |l−prev_close|); first bar h−l."""
    out: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            out.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    return out


def atr_at(bars: list[dict], idx: int, window: int = 24) -> float | None:
    """Mean TR over the ``window`` bars ENDING AT idx (inclusive) — the
    bake-off's atr24[idx]. None when there's not enough history."""
    if idx < window - 1 or idx >= len(bars):
        return None
    trs = true_ranges(bars[: idx + 1])
    return sum(trs[idx - window + 1 : idx + 1]) / window


def mean_tr_pct(bars: list[dict], idx: int, window: int) -> float | None:
    """Mean of TR/close over bars [idx−window, idx−1] — window ends BEFORE idx
    (the bake-off's trp720). None without enough history."""
    if idx < window or idx >= len(bars):
        return None
    trs = true_ranges(bars[: idx + 1])
    vals = [trs[k] / bars[k]["c"] for k in range(idx - window, idx)]
    return sum(vals) / window


def median_volume(bars: list[dict], idx: int, window: int) -> float | None:
    """Median volume over bars [idx−window, idx−1] — window ends BEFORE idx."""
    if idx < window or idx >= len(bars):
        return None
    return statistics.median(b["v"] for b in bars[idx - window : idx])
