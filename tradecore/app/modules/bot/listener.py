"""WaveBot — Redis pubsub listener.

Subscribes to ``alerts:wavewatch`` on the leader worker. For each ``wave_active``
alert: run vetoes, wait one 1m bar for entry confirmation, persist a paper
trade or log the skip.

Singleton — only the leader runs this, registered in ``app/main.py``.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from app.config import settings as app_settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.modules.bot import candle_source, equity, executor, strategy, vetoes
from app.modules.bot.schemas import Direction, SkipReason
from app.services import redis_service

CHANNEL = "alerts:wavewatch"


class BotListener:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop())
        log.info("bot_listener_started", channel=CHANNEL)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        log.info("bot_listener_stopped")

    async def _loop(self) -> None:
        r = redis_service.get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            while not self._stopping.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=5.0
                )
                if msg is None:
                    continue
                data = msg.get("data")
                if not data:
                    continue
                try:
                    payload = (
                        json.loads(data)
                        if isinstance(data, str)
                        else json.loads(data.decode())
                    )
                except (json.JSONDecodeError, AttributeError):
                    continue
                if payload.get("type") != "wave_active":
                    continue
                # Each alert is handled in its own task so a slow entry-delay
                # sleep doesn't block the next alert from being processed.
                asyncio.create_task(self._handle_alert(payload))
        finally:
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.aclose()

    async def _handle_alert(self, alert: dict) -> None:
        symbol = alert.get("symbol")
        exchange = alert.get("exchange", "")
        market_type = alert.get("market_type")
        base_asset = alert.get("base_asset", "")
        if not symbol:
            return
        direction = strategy.map_direction(alert.get("direction", ""))
        if direction is None:
            async with AsyncSessionLocal() as db:
                await executor.log_skipped(
                    db,
                    alert=alert,
                    direction=alert.get("direction", "unknown"),
                    reason=SkipReason.INVALID_DIRECTION,
                )
            return

        async with AsyncSessionLocal() as db:
            skip_reason, oracle_score = await vetoes.evaluate(
                db,
                symbol=symbol,
                base_asset=base_asset,
                exchange=exchange,
                market_type=market_type,
                direction=direction,
            )
            if skip_reason is not None:
                await executor.log_skipped(
                    db,
                    alert=alert,
                    direction=direction,
                    reason=skip_reason,
                    oracle_score=oracle_score,
                )
                return

        # Read the signal candle (latest 5m bar at alert time). Pulled now —
        # if it disappears during the entry delay, we'd rather fail closed.
        signal_candle = await candle_source.get_latest_candle(symbol, exchange, market_type)
        if signal_candle is None:
            async with AsyncSessionLocal() as db:
                await executor.log_skipped(
                    db, alert=alert, direction=direction, reason=SkipReason.NO_CANDLES
                )
            return

        # Wait for the next 1m bar before pulling the entry fill — filters
        # single-print spikes that would have already reverted.
        await asyncio.sleep(int(app_settings.bot_entry_delay_seconds))

        entry_candle = await candle_source.get_latest_candle(symbol, exchange, market_type)
        if entry_candle is None:
            async with AsyncSessionLocal() as db:
                await executor.log_skipped(
                    db, alert=alert, direction=direction, reason=SkipReason.NO_CANDLES
                )
            return

        # Re-check concurrency and kill switch — they may have changed
        # during the sleep, especially in a market-wide cascade where many
        # alerts arrive in the same minute.
        async with AsyncSessionLocal() as db:
            if await equity.is_kill_switch_tripped():
                await executor.log_skipped(
                    db, alert=alert, direction=direction, reason=SkipReason.KILL_SWITCH
                )
                return
            concurrent = await equity.get_concurrent_count()
            if concurrent >= int(app_settings.bot_max_concurrent):
                await executor.log_skipped(
                    db,
                    alert=alert,
                    direction=direction,
                    reason=SkipReason.MAX_CONCURRENT,
                )
                return
            if await vetoes.already_open(db, symbol):
                await executor.log_skipped(
                    db, alert=alert, direction=direction, reason=SkipReason.ALREADY_OPEN
                )
                return

            paper_equity = await equity.get_paper_equity()
            # Prefer a live price (Bybit orderbook mid) — the latest Redis candle
            # is a CLOSED 5m bar, up to 5min stale right after a cascade.
            live_px = await candle_source.get_live_price(symbol, exchange, market_type)
            entry_price = (
                live_px
                if live_px is not None and live_px > 0
                else Decimal(str(entry_candle.get("c") or entry_candle.get("close") or 0))
            )
            plan = strategy.plan_entry(
                alert=alert,
                signal_candle=signal_candle,
                entry_price=entry_price,
                paper_equity=paper_equity,
                position_size_pct=Decimal(str(app_settings.bot_position_size_pct)),
                stop_buffer_pct=Decimal(str(app_settings.bot_stop_buffer_pct)),
                r_multiple=Decimal(str(app_settings.bot_take_profit_r_multiple)),
                risk_per_trade_pct=Decimal(str(app_settings.bot_risk_per_trade_pct)),
                oracle_score=Decimal(str(oracle_score)) if oracle_score is not None else None,
            )
            if plan is None:
                await executor.log_skipped(
                    db,
                    alert=alert,
                    direction=direction,
                    reason=SkipReason.NO_CANDLES,
                    extra_context={"entry_candle": entry_candle, "signal_candle": signal_candle},
                )
                return

            # Liquidity at entry — recorded (not gated) so the turnover floor can
            # be calibrated against realized outcomes.
            try:
                entry_turnover = Decimal(
                    str(await candle_source.recent_turnover_usd(symbol, exchange, market_type))
                )
            except Exception:
                entry_turnover = None

            await executor.open_paper_trade(
                db,
                plan,
                entry_price=entry_price,
                oracle_score=Decimal(str(oracle_score)) if oracle_score is not None else None,
                entry_turnover_usd=entry_turnover,
            )


listener = BotListener()

__all__ = ["listener", "BotListener"]
