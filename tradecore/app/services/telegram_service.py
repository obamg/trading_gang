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
        self._bot_username: str | None = None  # filled by get_me() on start

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
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))

        await app.initialize()
        await app.start()

        # Ask Telegram who we are, so the app can build t.me deep links without
        # a hand-maintained TELEGRAM_BOT_USERNAME env var — one less setting to
        # get wrong at deploy time, and it cannot drift from the real bot.
        try:
            me = await app.bot.get_me()
            self._bot_username = me.username
        except Exception as e:  # non-fatal — /link still works without it
            log.warning("telegram_get_me_failed", error=str(e))

        # Register the command menu so commands are discoverable from
        # Telegram's ☰ button, not just from our welcome text.
        try:
            from telegram import BotCommand

            await app.bot.set_my_commands([
                BotCommand("start", "Connect your TradeCore account"),
                BotCommand("status", "Are alerts on? Am I paused?"),
                BotCommand("pause", "Mute alerts for 1 hour"),
                BotCommand("resume", "Turn alerts back on"),
                BotCommand("help", "What this bot can do"),
            ])
        except Exception as e:
            log.warning("telegram_set_commands_failed", error=str(e))

        await app.updater.start_polling(drop_pending_updates=True)
        self._app = app
        self._running = True
        log.info("telegram_bot_started", bot_username=self._bot_username)

    def deep_link(self, token: str) -> str | None:
        """`t.me` URL that connects the account in one tap.

        Telegram delivers the payload as `/start <token>`, so the user never
        copies anything between apps — that copy-paste step was both the main
        drop-off point and the reason the bot was unfindable (nothing in the
        UI said which bot to open). None when get_me() failed; callers fall
        back to showing the raw token for a manual /link.
        """
        if not self._bot_username:
            return None
        return f"https://t.me/{self._bot_username}?start={token}"

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
        """Welcome, or connect straight away when opened via a deep link.

        `t.me/<bot>?start=<token>` arrives here with the token in
        ``context.args`` — so the happy path is one tap and the user never
        sees a token at all.
        """
        args = context.args or []
        if args:
            await self._link_user(update, args[0])
            return

        chat_id = update.effective_chat.id
        async with AsyncSessionLocal() as db:
            already = await self._settings_by_chat(db, chat_id)
        if already is not None:
            await update.message.reply_text(
                "👋 You're already connected — alerts land right here.\n\n"
                "/status — check what's on\n"
                "/pause — quiet for an hour\n"
                "/help — everything I can do",
            )
            return

        await update.message.reply_text(
            "👋 *Welcome to TradeCore*\n\n"
            "I send you trading alerts the moment they fire — whale moves, new "
            "listings, unusual volume and news that actually moves price.\n\n"
            "*To connect, one tap:*\n"
            "Open TradeCore → *Settings* → *Connect Telegram*, then press the "
            "button there. It brings you straight back here and we're done.\n\n"
            "_No codes to copy. If you'd rather do it by hand, Settings also "
            "shows a code you can send as_ `/link YOUR_CODE`_._",
            parse_mode="Markdown",
        )

    async def _cmd_help(self, update, context) -> None:
        await update.message.reply_text(
            "*What I can do*\n\n"
            "/status — is everything connected and are alerts flowing?\n"
            "/pause — mute me for an hour (I'll come back on my own)\n"
            "/resume — turn alerts back on now\n"
            "/start — connect an account, or check you're connected\n\n"
            "Not getting alerts? Send /status — it'll tell you which part is off.",
            parse_mode="Markdown",
        )

    async def _cmd_link(self, update, context) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Almost there — I need your connect code.\n\n"
                "Open TradeCore → *Settings* → *Connect Telegram*. Tap the "
                "button there and it connects you automatically, or send me "
                "the code it shows like this:\n\n"
                "`/link abc123...`",
                parse_mode="Markdown",
            )
            return
        await self._link_user(update, args[0])

    async def _link_user(self, update, token: str) -> None:
        """Shared by /start <token> (deep link) and /link <token> (manual)."""
        user_id = await self._consume_link_token(token)
        if user_id is None:
            await update.message.reply_text(
                "That code didn't work — they expire after 10 minutes, so it "
                "has most likely just timed out.\n\n"
                "Head back to TradeCore → *Settings* → *Connect Telegram* and "
                "tap the button again. The new one will work.",
                parse_mode="Markdown",
            )
            return

        chat_id = update.effective_chat.id
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            us = result.scalar_one_or_none()
            if us is None:
                # Shouldn't happen — the settings row is created when the token
                # is issued. Keep it human anyway; "no settings row" means
                # nothing to the person reading it.
                log.warning("telegram_link_no_settings_row", user_id=str(user_id))
                await update.message.reply_text(
                    "Something went wrong on our side connecting your account. "
                    "Please try again from TradeCore → Settings, and if it keeps "
                    "happening let us know.",
                )
                return
            us.telegram_chat_id = str(chat_id)
            us.telegram_enabled = True
            await db.commit()

        await update.message.reply_text(
            "✅ *You're connected.*\n\n"
            "Alerts will arrive here as they happen. Nothing else to set up.\n\n"
            "If it ever gets noisy, send /pause for an hour of quiet, or "
            "/status to see what's on. /help lists the rest.",
            parse_mode="Markdown",
        )

    async def _cmd_status(self, update, context) -> None:
        chat_id = update.effective_chat.id
        async with AsyncSessionLocal() as db:
            us = await self._settings_by_chat(db, chat_id)
        if us is None:
            await update.message.reply_text(
                "This chat isn't connected to a TradeCore account yet.\n\n"
                "Open TradeCore → *Settings* → *Connect Telegram* and tap the "
                "button — it brings you back here and connects you in one step.",
                parse_mode="Markdown",
            )
            return
        paused = await self.is_paused(chat_id)

        # Say what the state MEANS, not just what it is — "enabled: ❌" left
        # people with no idea why alerts stopped or how to fix it.
        if paused:
            verdict = "⏸ Paused — quiet until the hour is up. /resume to start now."
        elif not us.telegram_enabled:
            verdict = "🔕 Alerts are switched off for this chat. Turn them back on in TradeCore → Settings."
        else:
            verdict = "🔔 All good — alerts are flowing to this chat."

        await update.message.reply_text(
            f"📊 *Status*\n\n"
            f"{verdict}\n\n"
            f"Connected: ✅\n"
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

    async def send_message(self, chat_id: int, text: str) -> bool:
        """Send a pre-formatted Markdown message to one chat.

        Unlike send_alert this does not require the polling Application to be
        running: one-shot cron scripts (e.g. app.scripts.forward_test_report)
        build a throwaway Bot client from the same configured token.
        """
        if not settings.telegram_bot_token:
            log.info("telegram_disabled")
            return False
        try:
            from telegram import Bot
        except ImportError:
            log.warning("telegram_not_installed")
            return False
        kwargs = dict(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        try:
            if self._app is not None:
                await self._app.bot.send_message(**kwargs)
            else:
                async with Bot(settings.telegram_bot_token) as bot:
                    await bot.send_message(**kwargs)
            return True
        except Exception as e:
            log.warning("telegram_send_failed", chat_id=chat_id, error=str(e))
            return False

    async def send_alert(self, chat_id: int, module: str, alert_data: dict) -> bool:
        if not self._running or self._app is None:
            return False
        if await self.is_paused(chat_id):
            return False
        text = self._format_alert(module, alert_data)
        if not text:
            # Formatter deliberately muted this event type
            # order lifecycle noise — only entries go to Telegram).
            return False
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
        t = d.get("type", "")

        if module == "radarx":
            is_div = d.get("is_divergence", False)
            header = f"⚡ *DIVERGENCE — {sym}*" if is_div else f"🚨 *RadarX Alert — {sym}*"
            div_line = f"\nDiv Score: `{d.get('divergence_score', '?')}` — volume loading, price flat" if is_div else ""
            return (
                f"{header}\n"
                f"Z-Score: `{d.get('z_score', '?')}` | Ratio: `{d.get('ratio', '?')}×`\n"
                f"Volume: `${_fmt_usd(d.get('candle_volume_usd'))}` | "
                f"Price: `{_fmt_pct(d.get('price_change_pct'))}`"
                f"{div_line}"
            )

        if module == "whaleradar":
            if t == "large_trade":
                side_emoji = "🟢" if d.get("side") == "buy" else "🔴"
                return (
                    f"🐋 *Whale Trade — {sym}*\n"
                    f"{side_emoji} {d.get('side', '?').upper()} | `${_fmt_usd(d.get('trade_size_usd'))}`\n"
                    f"Price: `{d.get('price', '?')}`"
                )
            if t == "oi_surge":
                arrow = "📈" if float(d.get("oi_change_pct", 0)) > 0 else "📉"
                return (
                    f"{arrow} *OI Surge — {sym}*\n"
                    f"Change: `{_fmt_pct(d.get('oi_change_pct'))}` | Direction: `{d.get('direction', '?')}`\n"
                    f"OI: `${_fmt_usd(d.get('oi_before_usd'))}` → `${_fmt_usd(d.get('oi_after_usd'))}`\n"
                    f"Price: `{_fmt_pct(d.get('price_change_pct'))}`"
                )
            if t == "onchain_transfer":
                asset = d.get("asset", "?")
                tt = (d.get("transfer_type") or "").replace("_", " ").title()
                return (
                    f"🐋 *On-Chain — {asset}*\n"
                    f"Type: `{tt}` | `${_fmt_usd(d.get('amount_usd'))}`\n"
                    f"Chain: `{d.get('chain', '?')}`"
                )

        if module == "gemradar":
            return (
                f"💎 *GemRadar — {sym}*\n"
                f"Price: `{_fmt_pct(d.get('price_change_pct'))}` | "
                f"MCap: `${_fmt_usd(d.get('market_cap_usd'))}`\n"
                f"Risk: `{d.get('risk_score', '?')}`"
            )

        if module == "oracle":
            rec = (d.get("recommendation") or "").replace("_", " ").upper()
            return (
                f"🔮 *Oracle — {sym}*\n"
                f"Score: `{d.get('score', '?')}` | `{rec}`\n"
                f"Confluence: `{d.get('confluence_count', '?')}` modules\n"
                f"Entry: `{d.get('entry_price', '-')}` | SL: `{d.get('stop_loss', '-')}` | TP: `{d.get('take_profit', '-')}`"
            )

        if module == "sentiment":
            if t == "extreme_funding":
                rate = float(d.get("funding_rate", 0))
                emoji = "🔴" if rate > 0 else "🟢"
                label = "Longs overpaying" if rate > 0 else "Short squeeze setup"
                return (
                    f"{emoji} *Extreme Funding — {sym}*\n"
                    f"Rate: `{rate*100:.4f}%` per 8h\n"
                    f"Signal: {label}"
                )
            if t == "crowded_positioning":
                return (
                    f"⚠️ *Crowded Position — {sym}*\n"
                    f"Side: `{d.get('side', '?').upper()}` heavy\n"
                    f"Long: `{d.get('long_ratio', '?')}%` | Short: `{d.get('short_ratio', '?')}%`"
                )

        if module == "newspulse":
            title = d.get("title", "?")
            sentiment = (d.get("sentiment") or "neutral").upper()
            importance = (d.get("importance") or "normal").upper()
            coins = d.get("coins") or ""
            source = d.get("source", "?")
            url = d.get("url", "")
            s_emoji = "🟢" if sentiment == "BULLISH" else "🔴" if sentiment == "BEARISH" else "⚪"
            imp = " 🔥" if importance == "HIGH" else ""
            return (
                f"📰 *News{imp} — {sentiment}* {s_emoji}\n"
                f"{title}\n"
                f"Source: `{source}`{f' | Coins: `{coins}`' if coins else ''}\n"
                f"[Read →]({url})" if url else ""
            )

        if module == "liquidmap":
            side_emoji = "🟢" if d.get("side") == "short" else "🔴"
            return (
                f"💥 *Large Liquidation — {sym}* {side_emoji}\n"
                f"Side: `{d.get('side', '?').upper()}` | `${_fmt_usd(d.get('size_usd'))}`\n"
                f"Price: `{d.get('price', '?')}`"
            )

        if module == "listingwatch":
            exch = (d.get("exchange") or "?").upper()
            mkt = d.get("market_type") or ""
            if t == "listing_detected":
                cross = d.get("is_cross_listing")
                others = d.get("other_exchanges") or []
                tag = f" (also on {', '.join(o.upper() for o in others)})" if cross and others else ""
                inno = " 🚀 Innovation Zone" if d.get("innovation") else ""
                return (
                    f"🆕 *New Listing — {sym}*{inno}\n"
                    f"Exchange: `{exch}` `{mkt}`{tag}\n"
                    f"Watcher armed for 4h"
                )
            direction = (d.get("direction") or "").upper()
            dir_emoji = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "⚪"
            conv = d.get("conviction")
            try:
                conv_str = f"{float(conv):.2f}"
            except (TypeError, ValueError):
                conv_str = "?"
            mins = d.get("seconds_since_t0")
            try:
                mins_str = f"{int(mins) // 60}m"
            except (TypeError, ValueError):
                mins_str = "?"
            label = (t or "signal").replace("_", " ").title()
            return (
                f"✨ *ListingWatch — {sym}* {dir_emoji}\n"
                f"Signal: `{label}` | Conviction: `{conv_str}`\n"
                f"Exchange: `{exch}` `{mkt}` | T+`{mins_str}`\n"
                f"Price: `{d.get('price', '?')}`"
            )

        if module == "awakening":
            exch = (d.get("exchange") or "?").upper()
            cur = _fmt_usd(d.get("current_turnover_usd"))
            base = _fmt_usd(d.get("baseline_turnover_usd"))
            try:
                ratio = f"{float(d.get('ratio', 0)):.1f}×"
            except (TypeError, ValueError):
                ratio = "?"
            try:
                pct = float(d.get("price_change_pct", 0))
                pct_str = f"{pct:+.2f}%"
            except (TypeError, ValueError):
                pct_str = "?"
            return (
                f"🌅 *Awakening — {sym}*\n"
                f"Exchange: `{exch}` | Ratio: `{ratio}`\n"
                f"Vol: `${cur}` vs baseline `${base}`\n"
                f"24h: `{pct_str}`"
            )

        if module == "flowpulse":
            dir_emoji = "🟢" if d.get("direction") == "bullish" else "🔴" if d.get("direction") == "bearish" else "⚪"
            direction = (d.get("direction") or "neutral").upper()
            parts = [f"🌊 *FlowPulse — {sym}* {dir_emoji}\n"]
            parts.append(f"Direction: `{direction}` | Intensity: `{d.get('intensity', '?')}`\n")
            details = []
            if d.get("book_imbalance") is not None:
                details.append(f"Book: `{d['book_imbalance']:.2f}`")
            if d.get("taker_ratio") is not None:
                details.append(f"Taker: `{d['taker_ratio']:.3f}`")
            if d.get("top_long_ratio") is not None:
                details.append(f"Top L/S: `{d['top_long_ratio']:.0f}%/{100-d['top_long_ratio']:.0f}%`")
            if details:
                parts.append(" | ".join(details))
            return "".join(parts)

        if module == "walletwatch":
            token = d.get("token_out_symbol") or _short_addr(d.get("token_out_address"))
            entity = d.get("entity_name") or _short_addr(d.get("wallet"))
            chain = (d.get("chain") or "?").lower()
            venue = d.get("venue") or "DEX"
            paid = d.get("token_in_symbol") or "?"
            conv = d.get("entity_conviction")
            try:
                conv_str = f"{float(conv):.2f}" if conv is not None else "—"
            except (TypeError, ValueError):
                conv_str = "?"
            return (
                f"🧠 *Smart Money Buy — {token}*\n"
                f"{entity} bought `{token}` on `{venue}`\n"
                f"Size: `${_fmt_usd(d.get('amount_usd'))}` (paid in `{paid}`)\n"
                f"Chain: `{chain}` | Conviction: `{conv_str}`"
            )

        if module == "wavewatch":
            base = d.get("base_asset") or sym
            mkt = (d.get("market_type") or "?").upper()
            try:
                score = f"{float(d.get('score', 0)):.2f}"
            except (TypeError, ValueError):
                score = "?"
            try:
                vol_x = f"{float(d.get('vol_ratio_now', 0)):.1f}×"
            except (TypeError, ValueError):
                vol_x = "?"
            try:
                dwell_min = int(d.get("dwell_seconds", 0) // 60)
                dwell_str = f"{dwell_min}m"
            except (TypeError, ValueError):
                dwell_str = "?"
            try:
                f = d.get("funding_pct")
                funding_str = f"{float(f) * 100:+.3f}%" if f is not None else "—"
            except (TypeError, ValueError):
                funding_str = "?"
            return (
                f"🌊 *WaveWatch — {base}* ({sym})\n"
                f"Market: `{mkt}` | Score: `{score}` | Dwell: `{dwell_str}`\n"
                f"Vol burst: `{vol_x}` | Funding: `{funding_str}`\n"
                f"_Innovation Zone — wave forming_"
            )

        return f"📣 *{module.title()} — {sym}*\n`{t or 'alert'}`"


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


def _short_addr(v) -> str:
    if not v:
        return "?"
    s = str(v)
    return f"{s[:6]}…{s[-4:]}" if len(s) > 12 else s


service = TelegramService()
