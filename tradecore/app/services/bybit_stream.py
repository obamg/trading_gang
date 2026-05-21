"""Bybit USDT-Linear Perpetuals stream manager.

Drop-in replacement for ``BinanceStreamManager`` when Binance's WS endpoints
are geo-restricted. Writes the **same Redis schema** as the Binance manager,
so all downstream detectors (radarx, whaleradar, flowpulse, oracle, etc.)
keep working without changes.

Streams per symbol:
  kline.5.<SYMBOL>          closed 5m candles → candles:{symbol}
  publicTrade.<SYMBOL>      aggregate trades  → trades:{symbol}
  orderbook.1.<SYMBOL>      best bid/ask      → bookticker:{symbol}
  allLiquidation.<SYMBOL>   forced orders     → pubsub "liquidations" + heatmap

WS endpoint: wss://stream.bybit.com/v5/public/linear
Bybit batches messages (each ``data`` is an array) and uses topic-based
routing instead of Binance's combined-streams URL. The translators below
flatten Bybit shapes into the Binance-compatible dicts that
``redis_service.push_candle`` / ``push_trade`` etc. already expect.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from app.config import settings
from app.logging_config import log
from app.services import redis_service

STREAM_TOPICS = ("kline.5", "publicTrade", "orderbook.1", "allLiquidation")
MAX_ARGS_PER_CONN = 600          # Bybit allows ~500 unique args per sub message; we stay below
PING_INTERVAL_SECONDS = 20       # Bybit recommends pinging every ≤30s
REST_TIMEOUT = 15.0
RECONNECT_BACKOFF_START = 1.0
RECONNECT_BACKOFF_MAX = 30.0

DEFAULT_REST = "https://api.bybit.com"
DEFAULT_WS = "wss://stream.bybit.com/v5/public/linear"


# ---------- pure translators (unit-tested separately) ----------


def translate_kline(item: dict) -> dict | None:
    """Bybit kline → Binance-shape candle (same keys as binance_stream output).

    Returns None if the candle isn't closed (``confirm=False``).
    """
    if not item.get("confirm"):
        return None
    try:
        return {
            "t": int(item["start"]),
            "T": int(item["end"]),
            "o": float(item["open"]),
            "h": float(item["high"]),
            "l": float(item["low"]),
            "c": float(item["close"]),
            "v": float(item["volume"]),
            "q": float(item.get("turnover", 0)),
            # Bybit doesn't expose trade count in kline; use 0 sentinel rather than
            # invent a value. Downstream code reads .get("n") or 0 so this is safe.
            "n": 0,
        }
    except (KeyError, TypeError, ValueError):
        return None


def translate_trade(item: dict) -> dict | None:
    """Bybit publicTrade → Binance-shape aggTrade dict.

    Bybit's ``S`` is the *taker* side. Binance's ``m`` flag is "is buyer the
    market-maker?" — i.e. taker was selling. So:
      S=="Buy"  → buyer is taker     → m=0
      S=="Sell" → buyer is maker     → m=1
    """
    try:
        price = float(item["p"])
        qty = float(item["v"])
    except (KeyError, TypeError, ValueError):
        return None
    side = (item.get("S") or "").lower()
    return {
        "p": price,
        "q": qty,
        "usd": price * qty,
        "m": 1 if side == "sell" else 0,
        "T": item.get("T"),
        "a": item.get("i"),
    }


def translate_orderbook_top(data: dict) -> tuple[float, float] | None:
    """Bybit orderbook.1 snapshot → (best_bid, best_ask).

    Returns None if either side is empty (deltas can drop a side temporarily).
    """
    bids = data.get("b") or []
    asks = data.get("a") or []
    if not bids or not asks:
        return None
    try:
        bid = float(bids[0][0])
        ask = float(asks[0][0])
    except (TypeError, ValueError, IndexError):
        return None
    return bid, ask


def translate_liquidation(item: dict) -> dict | None:
    """Bybit allLiquidation → Binance forceOrder-shape event.

    Bybit ``S`` is the **side of the liquidation order**:
      S=="Sell" → long position was liquidated → side="long"
      S=="Buy"  → short position was liquidated → side="short"
    Same mapping as Binance's forceOrder.S — convenient.
    """
    symbol = item.get("s")
    if not symbol:
        return None
    try:
        price = float(item["p"])
        qty = float(item["v"])
    except (KeyError, TypeError, ValueError):
        return None
    raw_side = (item.get("S") or "").lower()
    side = "long" if raw_side == "sell" else "short"
    return {
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "usd": price * qty,
        "T": item.get("T"),
    }


def parse_topic(topic: str) -> tuple[str, str] | None:
    """Decode a Bybit topic like 'kline.5.BTCUSDT' → ('kline.5', 'BTCUSDT').

    Returns None for unknown / malformed topics.
    """
    if not topic:
        return None
    # Order matters: longer prefixes first so ``kline.5`` beats ``kline``.
    for prefix in ("kline.5", "publicTrade", "orderbook.1", "allLiquidation"):
        if topic.startswith(prefix + "."):
            return prefix, topic[len(prefix) + 1 :]
    return None


# ---------- manager ----------


class BybitStreamManager:
    def __init__(self) -> None:
        self._symbols: list[str] = []
        self._connections: list[asyncio.Task] = []
        self._discovery_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # --- lifecycle ---

    async def start(self) -> None:
        log.info("bybit_stream_manager_starting")
        self._stopping.clear()
        await self._discover_symbols()
        self._spawn_connections()
        self._discovery_task = asyncio.create_task(self._rediscovery_loop())

    async def stop(self) -> None:
        log.info("bybit_stream_manager_stopping")
        self._stopping.set()
        tasks = list(self._connections)
        if self._discovery_task:
            tasks.append(self._discovery_task)
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._connections.clear()
        self._discovery_task = None

    # --- symbol discovery ---

    async def _discover_symbols(self) -> None:
        rest = getattr(settings, "bybit_rest_url", "") or DEFAULT_REST
        try:
            async with httpx.AsyncClient(timeout=REST_TIMEOUT) as client:
                info_resp = await client.get(
                    f"{rest}/v5/market/instruments-info",
                    params={"category": "linear", "limit": 1000},
                )
                info_resp.raise_for_status()
                ticker_resp = await client.get(
                    f"{rest}/v5/market/tickers", params={"category": "linear"}
                )
                ticker_resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error("bybit_symbol_discovery_failed", error=str(e))
            return

        info = info_resp.json().get("result", {}).get("list", []) or []
        tickers_list = ticker_resp.json().get("result", {}).get("list", []) or []
        tickers = {t["symbol"]: t for t in tickers_list}

        # Reuse the Binance min-volume threshold — it's a USD figure, applies the same.
        min_vol = float(settings.binance_min_quote_volume_usd)
        active: list[str] = []
        for s in info:
            if s.get("status") != "Trading":
                continue
            if s.get("quoteCoin") != "USDT":
                continue
            if s.get("contractType") != "LinearPerpetual":
                continue
            sym = s.get("symbol") or ""
            if not sym.isascii() or not sym.isalnum():
                log.warning("bybit_skip_unusual_symbol", symbol=sym)
                continue
            t = tickers.get(sym)
            if not t:
                continue
            try:
                # Bybit's turnover24h is the USD-equivalent quote volume.
                quote_vol = float(t.get("turnover24h", 0))
            except (TypeError, ValueError):
                continue
            if quote_vol >= min_vol:
                active.append(sym)

        # Force-subscribe sets — ListingWatch (new listings, no 24h history)
        # and Awakening (sleeping perps with a fresh volume spike) both write
        # here. Membership has a TTL set by the writer so it naturally drains.
        forced: set[str] = set()
        for key in ("bybit:force_subscribe", "awakening:force_subscribe:bybit"):
            try:
                members = await redis_service.get_redis().smembers(key) or set()
            except Exception:
                members = set()
            forced |= members
        active_set = set(active)
        for sym in forced:
            if sym not in active_set and sym.isascii() and sym.isalnum():
                active.append(sym)

        active.sort()
        self._symbols = active
        await redis_service.set_symbol_list(active)
        log.info(
            "bybit_symbols_discovered",
            count=len(active),
            min_vol_usd=min_vol,
            forced=len(forced),
        )

    async def _rediscovery_loop(self) -> None:
        interval = settings.binance_symbol_refresh_minutes * 60
        try:
            while not self._stopping.is_set():
                await asyncio.sleep(interval)
                if self._stopping.is_set():
                    break
                prev = set(self._symbols)
                await self._discover_symbols()
                new = set(self._symbols)
                if new != prev:
                    log.info(
                        "bybit_symbols_changed",
                        added=len(new - prev),
                        removed=len(prev - new),
                    )
                    for t in self._connections:
                        t.cancel()
                    self._connections.clear()
                    self._spawn_connections()
        except asyncio.CancelledError:
            raise

    # --- connections ---

    def _build_args(self, symbols: list[str]) -> list[str]:
        return [f"{topic}.{sym}" for sym in symbols for topic in STREAM_TOPICS]

    def _spawn_connections(self) -> None:
        if not self._symbols:
            log.warning("bybit_no_symbols_to_stream")
            return
        args = self._build_args(self._symbols)
        for i in range(0, len(args), MAX_ARGS_PER_CONN):
            chunk = args[i : i + MAX_ARGS_PER_CONN]
            task = asyncio.create_task(
                self._run_connection(chunk, conn_idx=i // MAX_ARGS_PER_CONN)
            )
            self._connections.append(task)
        log.info(
            "bybit_connections_spawned", count=len(self._connections), total_args=len(args)
        )

    async def _run_connection(self, args: list[str], conn_idx: int) -> None:
        url = getattr(settings, "bybit_base_url", "") or DEFAULT_WS
        backoff = RECONNECT_BACKOFF_START
        while not self._stopping.is_set():
            ping_task: asyncio.Task | None = None
            try:
                async with websockets.connect(
                    url, ping_interval=None, max_size=2**22
                ) as ws:
                    log.info("bybit_ws_connected", conn=conn_idx, args=len(args))
                    backoff = RECONNECT_BACKOFF_START

                    # Bybit needs a JSON subscribe message after connect.
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))

                    # App-level ping keeps the connection alive (Bybit closes
                    # silent connections after ~5 min).
                    ping_task = asyncio.create_task(self._ping_loop(ws, conn_idx))

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        if not isinstance(msg, dict):
                            continue
                        # Subscribe ack / pong messages don't have a topic.
                        if "topic" not in msg:
                            if msg.get("op") == "subscribe" and not msg.get("success", True):
                                log.warning(
                                    "bybit_subscribe_rejected",
                                    conn=conn_idx,
                                    ret_msg=msg.get("ret_msg"),
                                )
                            continue
                        try:
                            await self._dispatch(msg)
                        except Exception as e:
                            log.error(
                                "bybit_dispatch_error",
                                error=str(e),
                                topic=msg.get("topic"),
                            )
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                log.warning(
                    "bybit_ws_disconnected", conn=conn_idx, error=str(e), retry_in=backoff
                )
            except Exception as e:
                log.error(
                    "bybit_ws_error", conn=conn_idx, error=str(e), retry_in=backoff
                )
            finally:
                if ping_task:
                    ping_task.cancel()
                    try:
                        await ping_task
                    except (asyncio.CancelledError, Exception):
                        pass

            if self._stopping.is_set():
                break
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _ping_loop(self, ws, conn_idx: int) -> None:
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL_SECONDS)
                try:
                    await ws.send(json.dumps({"op": "ping"}))
                except Exception as e:
                    log.warning("bybit_ping_failed", conn=conn_idx, error=str(e))
                    return
        except asyncio.CancelledError:
            return

    # --- event dispatch ---

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        topic = msg.get("topic") or ""
        parsed = parse_topic(topic)
        if parsed is None:
            return
        kind, symbol = parsed
        data = msg.get("data")
        if kind == "kline.5":
            for item in data or []:
                candle = translate_kline(item)
                if candle is not None:
                    await redis_service.push_candle(symbol, candle)
        elif kind == "publicTrade":
            for item in data or []:
                trade = translate_trade(item)
                if trade is not None:
                    await redis_service.push_trade(symbol, trade)
        elif kind == "orderbook.1":
            if isinstance(data, dict):
                top = translate_orderbook_top(data)
                if top is not None:
                    bid, ask = top
                    r = redis_service.get_redis()
                    await r.hset(
                        f"bookticker:{symbol}", mapping={"b": str(bid), "a": str(ask)}
                    )
                    await r.expire(f"bookticker:{symbol}", 60)
        elif kind == "allLiquidation":
            for item in data or []:
                event = translate_liquidation(item)
                if event is None:
                    continue
                await redis_service.publish_liquidation(event)
                await redis_service.update_liquidation_heatmap(
                    event["symbol"], event["price"], event["usd"], event["side"]
                )


manager = BybitStreamManager()
