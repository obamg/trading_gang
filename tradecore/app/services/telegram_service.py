"""Telegram bot — commands + alert delivery.

One bot serves all users, keyed by chat_id stored in user_settings.
Linking flow:
  1. User clicks "Connect Telegram" in the app → backend issues a short-lived
     link token (opaque, stored in Redis keyed to user_id).
  2. User sends `/link <token>` to the bot → bot validates token, stores
     chat_id on user_settings, deletes the Redis token.

Commands:
  /start  — welcome + instructions
  /link <token>
  /status
  /pause  — 1h silence
  /resume
"""
from __future__ import annotations

import secrets
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.settings import UserSettings
from app.services import redis_service

LINK_TOKEN_TTL = 600  # 10 minutes
PAUSE_DEFAULT_SECONDS = 3600


class TelegramService:
    def __init__(self) -> None:
        self._app = None  # python-telegram-bot Application
        self._running = False

    # ---------- link token mgmt ----------

    async def create_link_token(self, user_id: UUID) -> str:
        token = secrets.token_urlsafe(16)
        r = redis_service.get_redis()
        await r.set(f"tg_link:{token}", str(user_id), ex=LINK_TOKEN_TTL)
        return token

    async def _consume_link_token(self, token: str) -> UUID | None:
        r = redis_service.get_redis()
        key = f"tg_link:{token}"
        uid = await r.get(key)
        if not uid:
            return None
        await r.delete(key)
        try:
            return UUID(uid)
        except ValueError:
            return None

    # ---------- pause state ----------

    async def is_paused(self, chat_id: int) -> bool:
        r = redis_service.get_redis()
        return bool(await r.exists(f"tg_pause:{chat_id}"))

    async def _set_pause(self, chat_id: int, seconds: int = PAUSE_DEFAULT_SECONDS) -> None:
        r = redis_service.get_redis()
        await r.set(f"tg_pause:{chat_id}", "1", ex=seconds)

    async def _clear_pause(self, chat_id: int) -> None:
        r = redis_service.get_redis()
        await r.delete(f"tg_pause:{chat_id}")

    # ---------- DB helpers ----------

    async def _settings_by_chat(self, db: AsyncSession, chat_id: int) -> UserSettings | None:
        result = await db.execute(
            select(UserSettings).where(UserSettings.telegram_chat_id == str(chat_id))
        )
        return result.scalar_one_or_none()

    # ---------- bot lifecycle ----------

    async def start(self) -> None:
        if not settings.telegram_bot_enabled or not settings.telegram_bot_token:
            log.info("telegram_disabled")
            return
        try:
            from telegram.ext import Application, CommandHandler
        except ImportError:
            log.warning("telegram_not_installed")
            return

        app = Application.builder().token(settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("link", self._cmd_link))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        self._app = app
        self._running = True
        log.info("telegram_bot_started")

    async def stop(self) -> None:
        if not self._running or self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            log.warning("telegram_stop_error", error=str(e))
        self._app = None
        self._running = False
        log.info("telegram_bot_stopped")

    # ---------- command handlers ----------

    async def _cmd_start(self, update, context) -> None:
        await update.message.reply_text(
            "👋 *Welcome to TradeCore*\n\n"
            "To receive alerts, open TradeCore → Settings → Connect Telegram. "
            "Copy the link token you're shown, then reply here with:\n\n"
            "`/link YOUR_TOKEN`",
            parse_mode="Markdown",
        )

    async def _cmd_link(self, update, context) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: `/link <token>`", parse_mode="Markdown")
            return
        user_id = await self._consume_link_token(args[0])
        if user_id is None:
            await update.message.reply_text("❌ Invalid or expired token.")
            return
        chat_id = update.effective_chat.id
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            us = result.scalar_one_or_none()
            if us is None:
                await update.message.reply_text("❌ No settings row for this account.")
                return
            us.telegram_chat_id = str(chat_id)
            us.telegram_enabled = True
            await db.commit()
        await update.message.reply_text("✅ Telegram linked. You'll now receive alerts here.")

    async def _cmd_status(self, update, context) -> None:
        chat_id = update.effective_chat.id
        async with AsyncSessionLocal() as db:
            us = await self._settings_by_chat(db, chat_id)
        if us is None:
            await update.message.reply_text("❌ Not linked. Use `/link <token>` first.", parse_mode="Markdown")
            return
        paused = await self.is_paused(chat_id)
        await update.message.reply_text(
            f"📊 *Status*\n"
            f"Linked: ✅\n"
            f"Alerts enabled: {'✅' if us.telegram_enabled else '❌'}\n"
            f"Paused: {'⏸ yes' if paused else '▶️ no'}",
            parse_mode="Markdown",
        )

    async def _cmd_pause(self, update, context) -> None:
        chat_id = update.effective_chat.id
        await self._set_pause(chat_id)
        await update.message.reply_text("⏸ Alerts paused for 1 hour. `/resume` to re-enable.", parse_mode="Markdown")

    async def _cmd_resume(self, update, context) -> None:
        chat_id = update.effective_chat.id
        await self._clear_pause(chat_id)
        await update.message.reply_text("▶️ Alerts resumed.")

    # ---------- alert delivery ----------

    async def send_alert(self, chat_id: int, module: str, alert_data: dict) -> bool:
        if not self._running or self._app is None:
            return False
        if await self.is_paused(chat_id):
            return False
        text = self._format_alert(module, alert_data)
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            return True
        except Exception as e:
            log.warning("telegram_send_failed", chat_id=chat_id, error=str(e))
            return False

    def _format_alert(self, module: str, d: dict) -> str:
        sym = d.get("symbol", "?")
        if module == "radarx":
            return (
                f"🚨 *RadarX Alert — {sym}*\n"
                f"Z-Score: `{d.get('z_score', '?')}` | Ratio: `{d.get('ratio', '?')}×`\n"
                f"Volume: `${_fmt_usd(d.get('candle_volume_usd'))}` | "
                f"Price: `{_fmt_pct(d.get('price_change_pct'))}`"
            )
        if module == "whale":
            return (
                f"🐋 *Whale Alert — {sym}*\n"
                f"Side: `{d.get('side', '?')}` | Size: `${_fmt_usd(d.get('trade_size_usd'))}`\n"
                f"Price: `{d.get('price', '?')}`"
            )
        if module == "gemradar":
            return (
                f"💎 *GemRadar — {sym}*\n"
                f"Price: `{_fmt_pct(d.get('price_change_pct'))}` | "
                f"MCap: `${_fmt_usd(d.get('market_cap_usd'))}`\n"
                f"Risk: `{d.get('risk_score', '?')}`"
            )
        if module == "oracle":
            return (
                f"🔮 *Oracle Signal — {sym}*\n"
                f"Score: `{d.get('score', '?')}` | Rec: `{d.get('recommendation', '?')}`\n"
                f"Confluence: `{d.get('confluence_count', '?')}`"
            )
        return f"📣 *{module.title()} — {sym}*\n```\n{d}\n```"


def _fmt_usd(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.2f}"


def _fmt_pct(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "?"


service = TelegramService()
