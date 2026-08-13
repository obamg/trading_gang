"""MajorsBot forward-test weekly report.

Summarizes MajorsBot paper-trading activity per strategy and sends the report
over Telegram to every linked chat:

  - per strategy (volevent | fundingfade): signals created, fills, pending +
    cancelled orders, closed n, and average / total net R;
  - current paper equity and open-position count.

The script never raises: every failure is logged and it exits 0, so a broken
week can't wedge cron.

(WaveBot was retired 2026-07-31 — its section of this report was removed when
the bot module was deleted; migration 022 dropped its tables.)

Run weekly in prod (host cron, Monday 08:00 UTC):
    0 8 * * 1 docker exec trading_gang-api-1 python -m app.scripts.forward_test_report

Manual / local:
    docker compose exec api python -m app.scripts.forward_test_report --dry-run
    python -m app.scripts.forward_test_report --since 2026-07-25T00:00:00+00:00
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from app.logging_config import configure_logging, log

# MajorsBot went live in prod paper mode on this date — the default report
# window start.
MAJORSBOT_ERA_START = "2026-07-25T00:00:00+00:00"

MAJORSBOT_STRATEGIES = ("volevent", "fundingfade")

# Telegram hard limit is 4096; self-cap below it.
MAX_MESSAGE_CHARS = 4000


# ---------- pure message composition ----------


def compose_majorsbot_section(mb: dict) -> str:
    """Render the MajorsBot per-strategy aggregates into a Telegram Markdown
    message. Pure: no I/O, so tests can feed fake aggregates.

    mb = {"equity": float | None, "open": int, "since": str | None,
          "per_strategy": {name: {
        "signals", "filled", "pending", "cancelled", "closed",
        "avg_r_net" (None when no closed), "total_r_net"}}}
    """
    per = mb.get("per_strategy") or {}
    eq = mb.get("equity")
    eq_s = f"`${eq:,.2f}`" if eq is not None else "`—`"
    since = mb.get("since") or MAJORSBOT_ERA_START
    lines = [
        "📊 *MajorsBot Forward Test — weekly report*",
        f"Activity since `{since}`",
        f"Equity {eq_s} | Open now: `{int(mb.get('open') or 0)}`",
        "`strat        sig fill pend canc clsd  netR   totR`",
    ]
    for name in MAJORSBOT_STRATEGIES:
        b = per.get(name) or {}
        avg = f"{b['avg_r_net']:+6.3f}" if b.get("avg_r_net") is not None else "     —"
        tot = f"{b['total_r_net']:+6.2f}" if b.get("total_r_net") is not None else "     —"
        lines.append(
            f"`{name:<11} {int(b.get('signals') or 0):>4} {int(b.get('filled') or 0):>4} "
            f"{int(b.get('pending') or 0):>4} {int(b.get('cancelled') or 0):>4} "
            f"{int(b.get('closed') or 0):>4} {avg} {tot}`"
        )
    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 1] + "…"
    return text


# ---------- DB loading ----------


async def _load_majorsbot_stats(since: datetime) -> dict:
    """Query majorsbot_trades per strategy: signals (rows created), fills,
    pending (point-in-time), cancellations, closed n + net-R stats, equity."""
    from sqlalchemy import func, select

    from app.database import AsyncSessionLocal
    from app.models.majorsbot import MajorsBotTrade

    def _empty() -> dict:
        return {
            "signals": 0,
            "filled": 0,
            "pending": 0,
            "cancelled": 0,
            "closed": 0,
            "avg_r_net": None,
            "total_r_net": None,
        }

    per: dict[str, dict] = {name: _empty() for name in MAJORSBOT_STRATEGIES}
    async with AsyncSessionLocal() as db:
        sig_rows = (
            await db.execute(
                select(MajorsBotTrade.strategy, func.count())
                .where(MajorsBotTrade.created_at >= since)
                .group_by(MajorsBotTrade.strategy)
            )
        ).all()
        status_rows = (
            await db.execute(
                select(MajorsBotTrade.strategy, MajorsBotTrade.status, func.count())
                .where(MajorsBotTrade.created_at >= since)
                .group_by(MajorsBotTrade.strategy, MajorsBotTrade.status)
            )
        ).all()
        closed_rows = (
            await db.execute(
                select(
                    MajorsBotTrade.strategy,
                    func.count(),
                    func.coalesce(func.sum(MajorsBotTrade.realized_r_net), 0),
                )
                .where(
                    MajorsBotTrade.status == "closed",
                    MajorsBotTrade.created_at >= since,
                )
                .group_by(MajorsBotTrade.strategy)
            )
        ).all()
        open_count = (
            await db.execute(
                select(func.count())
                .select_from(MajorsBotTrade)
                .where(MajorsBotTrade.status == "open")
            )
        ).scalar_one()
        pending_now_rows = (
            await db.execute(
                select(MajorsBotTrade.strategy, func.count())
                .where(MajorsBotTrade.status == "pending")
                .group_by(MajorsBotTrade.strategy)
            )
        ).all()

    for strat, n in sig_rows:
        per.setdefault(strat, _empty())["signals"] = int(n)
    for strat, status_val, n in status_rows:
        b = per.setdefault(strat, _empty())
        if status_val in ("open", "closed"):
            b["filled"] += int(n)
        elif status_val == "cancelled":
            b["cancelled"] += int(n)
    for strat, n in pending_now_rows:
        per.setdefault(strat, _empty())["pending"] = int(n)
    for strat, n, r_net_sum in closed_rows:
        b = per.setdefault(strat, _empty())
        b["closed"] = int(n)
        b["total_r_net"] = float(r_net_sum or 0)
        b["avg_r_net"] = (float(r_net_sum or 0) / int(n)) if int(n) else None

    equity_val = None
    try:
        from app.modules.majorsbot import equity as mb_equity

        equity_val = float(await mb_equity.get_paper_equity())
    except Exception as e:  # Redis down must not sink the report
        log.warning("forward_report_majorsbot_equity_failed", error=str(e))

    return {
        "equity": equity_val,
        "open": int(open_count),
        "per_strategy": per,
        "since": since.isoformat(),
    }


# ---------- delivery ----------


async def _send_report(text: str) -> None:
    """Deliver via the existing Telegram service to every linked chat — same
    chat-id resolution as ws_manager._deliver_telegram (user_settings rows
    with telegram_enabled + telegram_chat_id). Falls back to stdout when the
    bot is disabled or nobody is linked, so cron mail still captures it."""
    from sqlalchemy import select

    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.settings import UserSettings
    from app.services.telegram_service import service as telegram_service

    if not (settings.telegram_bot_enabled and settings.telegram_bot_token):
        log.info("forward_report_telegram_disabled")
        print(text)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(
                UserSettings.telegram_enabled.is_(True),
                UserSettings.telegram_chat_id.isnot(None),
            )
        )
        chat_ids: list[int] = []
        for us in result.scalars():
            try:
                chat_ids.append(int(us.telegram_chat_id))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue

    if not chat_ids:
        log.warning("forward_report_no_chat_ids")
        print(text)
        return

    delivered = sum(
        [1 for cid in chat_ids if await telegram_service.send_message(cid, text)]
    )
    log.info("forward_report_sent", chats=len(chat_ids), delivered=delivered)


# ---------- main ----------


async def _run(args: argparse.Namespace) -> None:
    since = datetime.fromisoformat(args.since)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    mb_stats = await _load_majorsbot_stats(since)
    text = compose_majorsbot_section(mb_stats)
    log.info(
        "forward_report_composed",
        since=mb_stats["since"],
        open_positions=mb_stats["open"],
        equity=mb_stats["equity"],
        chars=len(text),
    )

    if args.dry_run:
        print(text)
        return
    await _send_report(text)


async def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Weekly MajorsBot forward-test report → Telegram."
    )
    parser.add_argument(
        "--since",
        default=MAJORSBOT_ERA_START,
        help=f"ISO datetime — only trades created on/after this "
        f"(default: {MAJORSBOT_ERA_START}, MajorsBot go-live)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the report to stdout instead of sending Telegram messages",
    )
    args = parser.parse_args()
    try:
        await _run(args)
    except Exception as e:
        # Cron job — never crash, never non-zero exit.
        log.error("forward_report_failed", error=str(e))


if __name__ == "__main__":
    asyncio.run(main())
