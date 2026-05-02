"""Frontend WebSocket connection manager.

Tracks authenticated WebSocket connections per user_id and fans out events
to them. Also relays Redis pub/sub alerts to connected users based on their
settings and watchlist.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from typing import Any
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.settings import UserSettings, Watchlist
from app.services import redis_service
from app.services.telegram_service import service as telegram_service

UNREAD_BUFFER_SIZE = 20
ALERT_MODULES = ("radarx", "whaleradar", "gemradar", "oracle", "sentiment", "macro", "newspulse", "liquidmap", "flowpulse")


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: dict[UUID, set[WebSocket]] = defaultdict(set)
        self._unread: dict[UUID, deque[dict]] = defaultdict(lambda: deque(maxlen=UNREAD_BUFFER_SIZE))
        self._lock = asyncio.Lock()
        self._relay_task: asyncio.Task | None = None

    # ---------- connection tracking ----------

    async def register(self, user_id: UUID, ws: WebSocket) -> None:
        async with self._lock:
            self._conns[user_id].add(ws)
        log.info("ws_client_connected", user_id=str(user_id), total=len(self._conns[user_id]))

        # Replay any unread events buffered while user was offline
        for event in list(self._unread.get(user_id, ())):
            try:
                await ws.send_json(event)
            except Exception:
                break

    async def unregister(self, user_id: UUID, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._conns.get(user_id)
            if conns and ws in conns:
                conns.discard(ws)
                if not conns:
                    self._conns.pop(user_id, None)
        log.info("ws_client_disconnected", user_id=str(user_id))

    async def broadcast_to_user(self, user_id: UUID, event: dict[str, Any]) -> None:
        conns = list(self._conns.get(user_id, ()))
        if not conns:
            self._unread[user_id].append(event)
            return
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(user_id, ws)

    async def broadcast_all(self, event: dict[str, Any]) -> None:
        for user_id in list(self._conns.keys()):
            await self.broadcast_to_user(user_id, event)

    # ---------- redis pubsub relay ----------

    async def start_relay(self) -> None:
        if self._relay_task is None or self._relay_task.done():
            self._relay_task = asyncio.create_task(self._relay_loop())

    async def stop_relay(self) -> None:
        if self._relay_task:
            self._relay_task.cancel()
            try:
                await self._relay_task
            except (asyncio.CancelledError, Exception):
                pass
            self._relay_task = None

    async def _relay_loop(self) -> None:
        """Subscribe to Redis pub/sub and fan out to per-user websockets."""
        try:
            r = redis_service.get_redis()
        except RuntimeError:
            log.warning("ws_relay_skipped_no_redis")
            return

        channels = [f"alerts:{m}" for m in ALERT_MODULES] + ["liquidations"]
        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(*channels)
            log.info("ws_relay_subscribed", channels=channels)
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                channel = msg.get("channel", "")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                raw = msg.get("data", "")
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    payload = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                log.info("ws_relay_received", channel=channel, symbol=payload.get("symbol") if isinstance(payload, dict) else None)
                await self._route_alert(channel, payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("ws_relay_error", error=str(e))
        finally:
            try:
                await pubsub.unsubscribe()
                await pubsub.aclose()
            except Exception:
                pass

    async def _route_alert(self, channel: str, payload: dict) -> None:
        """Deliver alerts to subscribed users based on watchlist + settings."""
        module = channel.split(":", 1)[1] if channel.startswith("alerts:") else channel
        event_type = f"{module}_alert" if channel.startswith("alerts:") else "liquidation"
        event = {"type": event_type, "data": payload}

        symbol = payload.get("symbol") if isinstance(payload, dict) else None

        async with AsyncSessionLocal() as db:
            # WebSocket delivery to connected users
            for user_id in list(self._conns.keys()):
                if await self._user_should_receive(db, user_id, module, symbol):
                    await self.broadcast_to_user(user_id, event)

            # Telegram delivery to all users with telegram_enabled
            await self._deliver_telegram(db, module, payload, symbol)

    async def _user_should_receive(
        self, db: AsyncSession, user_id: UUID, module: str, symbol: str | None
    ) -> bool:
        """Gate on watchlist membership when a symbol is present.

        For now: if the user has any watchlists and the symbol is not in any
        of them, skip. If they have no watchlists, receive everything.
        Module-level mute toggles live in user_settings — read but don't
        enforce here beyond telegram (Team 4/5 tightens this).
        """
        if symbol is None:
            return True
        result = await db.execute(
            select(Watchlist).where(Watchlist.user_id == user_id)
        )
        wls = result.scalars().all()
        if not wls:
            return True
        for w in wls:
            if symbol in (w.symbols or []):
                return True
        return False


    async def _deliver_telegram(
        self, db: AsyncSession, module: str, payload: dict, symbol: str | None
    ) -> None:
        """Fan out alert to all Telegram-enabled users."""
        if module == "liquidation":
            return
        result = await db.execute(
            select(UserSettings).where(
                UserSettings.telegram_enabled.is_(True),
                UserSettings.telegram_chat_id.isnot(None),
            )
        )
        for us in result.scalars():
            if symbol and not await self._user_should_receive(db, us.user_id, module, symbol):
                continue
            try:
                chat_id = int(us.telegram_chat_id)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            asyncio.create_task(telegram_service.send_alert(chat_id, module, payload))


manager = ConnectionManager()
