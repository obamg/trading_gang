"""OracleTrigger — subscribes to detection-module alerts and runs Oracle
on the triggered symbol. Produces an oracle signal any time a RadarX or
WhaleRadar alert fires.
"""
from __future__ import annotations

import asyncio
import json

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.modules.oracle.engine import generate_signal
from app.services import redis_service

CHANNELS = ("alerts:radarx", "alerts:whaleradar")


class OracleTrigger:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop())
        log.info("oracle_trigger_started", channels=list(CHANNELS))

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        log.info("oracle_trigger_stopped")

    async def _loop(self) -> None:
        r = redis_service.get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(*CHANNELS)
        try:
            while not self._stopping.is_set():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if msg is None:
                    continue
                data = msg.get("data")
                if not data:
                    continue
                try:
                    payload = json.loads(data) if isinstance(data, str) else json.loads(data.decode())
                except (json.JSONDecodeError, AttributeError):
                    continue
                symbol = payload.get("symbol")
                if not symbol:
                    continue
                try:
                    async with AsyncSessionLocal() as db:
                        await generate_signal(db, symbol)
                except Exception as e:
                    log.error("oracle_trigger_failed", symbol=symbol, err=str(e))
        finally:
            await pubsub.unsubscribe(*CHANNELS)
            await pubsub.aclose()


trigger = OracleTrigger()

__all__ = ["trigger", "OracleTrigger"]
