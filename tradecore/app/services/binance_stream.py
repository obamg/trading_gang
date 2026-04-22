"""Binance USDT-M Futures stream manager.

Connects to public Binance Futures WebSocket streams, normalises events, and
writes them to Redis for downstream consumers (Teams 4/5). No API key needed.

Streams per symbol:
  <symbol>@kline_5m    closed 5m candles → candles:{symbol}
  <symbol>@aggTrade    aggregate trades  → trades:{symbol}
  <symbol>@forceOrder  liquidations      → pubsub "liquidations" + heatmap
  <symbol>@bookTicker  best bid/ask      → bookticker:{symbol} hash (optional)

Binance allows up to 200 streams per connection; we shard symbols across
multiple concurrent WS connections. Symbol list is refreshed hourly from the
REST exchangeInfo + 24hr ticker endpoints.
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

STREAMS_PER_SYMBOL = ("kline_5m", "aggTrade", "forceOrder", "bookTicker")
MAX_STREAMS_PER_CONN = 180  # Binance limit is 200 — leave headroom
REST_TIMEOUT = 15.0
RECONNECT_BACKOFF_START = 1.0
RECONNECT_BACKOFF_MAX = 30.0


class BinanceStreamManager:
    def __init__(self) -> None:
        self._symbols: list[str] = []
        self._connections: list[asyncio.Task] = []
        self._discovery_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if not settings.binance_streams_enabled:
            log.info("binance_streams_disabled")
            return
        log.info("binance_stream_manager_starting")
        self._stopping.clear()
        await self._discover_symbols()
        self._spawn_connections()
        self._discovery_task = asyncio.create_task(self._rediscovery_loop())

    async def stop(self) -> None:
        log.info("binance_stream_manager_stopping")
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

    # ---------- symbol discovery ----------

    async def _discover_symbols(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=REST_TIMEOUT) as client:
                info_resp = await client.get(f"{settings.binance_rest_url}/fapi/v1/exchangeInfo")
                info_resp.raise_for_status()
                ticker_resp = await client.get(f"{settings.binance_rest_url}/fapi/v1/ticker/24hr")
                ticker_resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error("binance_symbol_discovery_failed", error=str(e))
            return

        info = info_resp.json()
        tickers = {t["symbol"]: t for t in ticker_resp.json()}

        active: list[str] = []
        min_vol = settings.binance_min_quote_volume_usd
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            if s.get("contractType") != "PERPETUAL":
                continue
            sym = s["symbol"]
            t = tickers.get(sym)
            if not t:
                continue
            try:
                quote_vol = float(t.get("quoteVolume", 0))
            except (TypeError, ValueError):
                continue
            if quote_vol >= min_vol:
                active.append(sym)

        active.sort()
        self._symbols = active
        await redis_service.set_symbol_list(active)
        log.info("binance_symbols_discovered", count=len(active), min_vol_usd=min_vol)

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
                        "binance_symbols_changed",
                        added=len(new - prev),
                        removed=len(prev - new),
                    )
                    # Recycle connections with the new symbol set
                    for t in self._connections:
                        t.cancel()
                    self._connections.clear()
                    self._spawn_connections()
        except asyncio.CancelledError:
            raise

    # ---------- connections ----------

    def _spawn_connections(self) -> None:
        if not self._symbols:
            log.warning("binance_no_symbols_to_stream")
            return
        # Chunk by stream count, not symbol count
        streams: list[str] = []
        for sym in self._symbols:
            low = sym.lower()
            for s in STREAMS_PER_SYMBOL:
                streams.append(f"{low}@{s}")

        for i in range(0, len(streams), MAX_STREAMS_PER_CONN):
            chunk = streams[i : i + MAX_STREAMS_PER_CONN]
            task = asyncio.create_task(self._run_connection(chunk, conn_idx=i // MAX_STREAMS_PER_CONN))
            self._connections.append(task)
        log.info("binance_connections_spawned", count=len(self._connections), total_streams=len(streams))

    async def _run_connection(self, streams: list[str], conn_idx: int) -> None:
        url = f"{settings.binance_base_url}/stream?streams={'/'.join(streams)}"
        backoff = RECONNECT_BACKOFF_START
        while not self._stopping.is_set():
            try:
                async with websockets.connect(url, ping_interval=180, ping_timeout=600, max_size=2**22) as ws:
                    log.info("binance_ws_connected", conn=conn_idx, streams=len(streams))
                    backoff = RECONNECT_BACKOFF_START
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        payload = msg.get("data") if "data" in msg else msg
                        if not isinstance(payload, dict):
                            continue
                        try:
                            await self._dispatch(payload)
                        except Exception as e:
                            log.error("binance_dispatch_error", error=str(e), event=payload.get("e"))
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                log.warning("binance_ws_disconnected", conn=conn_idx, error=str(e), retry_in=backoff)
            except Exception as e:
                log.error("binance_ws_error", conn=conn_idx, error=str(e), retry_in=backoff)

            if self._stopping.is_set():
                break
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    # ---------- event dispatch ----------

    async def _dispatch(self, ev: dict[str, Any]) -> None:
        etype = ev.get("e")
        if etype == "kline":
            await self._handle_kline(ev)
        elif etype == "aggTrade":
            await self._handle_agg_trade(ev)
        elif etype == "forceOrder":
            await self._handle_force_order(ev)
        elif etype == "bookTicker" or ("b" in ev and "a" in ev and "s" in ev and "e" not in ev):
            await self._handle_book_ticker(ev)

    async def _handle_kline(self, ev: dict) -> None:
        k = ev.get("k") or {}
        if not k.get("x"):  # only closed candles
            return
        symbol = k["s"]
        candle = {
            "t": k["t"],
            "T": k["T"],
            "o": float(k["o"]),
            "h": float(k["h"]),
            "l": float(k["l"]),
            "c": float(k["c"]),
            "v": float(k["v"]),
            "q": float(k["q"]),
            "n": k["n"],
        }
        await redis_service.push_candle(symbol, candle)

    async def _handle_agg_trade(self, ev: dict) -> None:
        symbol = ev.get("s")
        if not symbol:
            return
        price = float(ev.get("p", 0))
        qty = float(ev.get("q", 0))
        trade = {
            "p": price,
            "q": qty,
            "usd": price * qty,
            "m": 1 if ev.get("m") else 0,  # true = buyer is maker (seller-aggressor)
            "T": ev.get("T"),
            "a": ev.get("a"),
        }
        await redis_service.push_trade(symbol, trade)

    async def _handle_force_order(self, ev: dict) -> None:
        order = ev.get("o") or {}
        symbol = order.get("s")
        if not symbol:
            return
        try:
            price = float(order.get("ap") or order.get("p", 0))
            qty = float(order.get("q", 0))
        except (TypeError, ValueError):
            return
        raw_side = order.get("S", "").upper()
        side = "long" if raw_side == "SELL" else "short"
        usd = price * qty
        event = {
            "symbol": symbol,
            "side": side,
            "price": price,
            "qty": qty,
            "usd": usd,
            "T": order.get("T"),
        }
        await redis_service.publish_liquidation(event)
        await redis_service.update_liquidation_heatmap(symbol, price, usd, side)

    async def _handle_book_ticker(self, ev: dict) -> None:
        symbol = ev.get("s")
        if not symbol:
            return
        try:
            bid = float(ev.get("b", 0))
            ask = float(ev.get("a", 0))
        except (TypeError, ValueError):
            return
        r = redis_service.get_redis()
        await r.hset(f"bookticker:{symbol}", mapping={"b": str(bid), "a": str(ask)})
        await r.expire(f"bookticker:{symbol}", 60)


manager = BinanceStreamManager()
