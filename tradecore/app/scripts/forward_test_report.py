"""WaveBot forward-test weekly report.

Summarizes the entry-filter era (trades entered after the 2026-07-10 18:45 UTC
calibration deploy) and sends the report over Telegram to every linked chat:

  - closed bot_trades: n, win%, PnL, avg realized R / net R, fees + funding,
    overall and per direction, plus the current open-position count;
  - bot_skipped_signals: counts by skip_reason, highlighting the three new
    entry filters (low_vol_ratio, funding_extreme, low_turnover);
  - a headline verdict comparing filtered-stream net expectancy against the
    pre-filter baselines (-0.203R exit replay / +0.033R recorded), and a
    one-line recommendation gated on sample size n=30.

The script never raises: every failure is logged and it exits 0, so a broken
week can't wedge cron.

Run weekly in prod (host cron, Monday 08:00 UTC):
    0 8 * * 1 docker exec trading_gang-api-1 python -m app.scripts.forward_test_report

Manual / local:
    docker compose exec api python -m app.scripts.forward_test_report --dry-run
    python -m app.scripts.forward_test_report --since 2026-07-10T18:45:00+00:00
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from app.logging_config import configure_logging, log

# Start of the entry-filter era: perp-only + blocklist + vol-ratio + funding +
# turnover filters went live at this deploy. Trades entered before it belong
# to the pre-filter baselines below, not to this forward test.
ENTRY_FILTER_ERA_START = "2026-07-10T18:45:00+00:00"

# Pre-filter baselines the verdict line compares against (avg net R / trade):
#   -0.203R  exit-rule replay over all pre-filter closed trades (replay_exits)
#   +0.033R  recorded realized_r_net of the same pre-filter stream
BASELINE_REPLAY_R = -0.203
BASELINE_RECORDED_R = 0.033

# Skip reasons introduced by the 2026-07-10 calibration — always shown first.
NEW_FILTER_REASONS = ("low_vol_ratio", "funding_extreme", "low_turnover")

MIN_SAMPLE_FOR_VERDICT = 30
MAX_MESSAGE_CHARS = 3500
_MAX_OTHER_SKIP_LINES = 8

# Telegram hard limit is 4096 — the combined WaveBot + MajorsBot message gets
# its own, slightly larger budget (compose_report already self-caps at 3500).
MAX_COMBINED_CHARS = 4000

MAJORSBOT_STRATEGIES = ("volevent", "fundingfade")


# ---------- pure aggregation ----------


def _bucket(trades: list[dict]) -> dict:
    """Aggregate one slice of closed trades into plain floats (None = n/a)."""

    def _vals(key: str) -> list[float]:
        return [float(t[key]) for t in trades if t.get(key) is not None]

    n = len(trades)
    pnls = _vals("realized_pnl_usd")
    rs = _vals("realized_r")
    rs_net = _vals("realized_r_net")
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n": n,
        "wins": wins,
        "win_pct": (100.0 * wins / n) if n else None,
        "pnl_total": sum(pnls),
        "pnl_avg": (sum(pnls) / len(pnls)) if pnls else None,
        "avg_r": (sum(rs) / len(rs)) if rs else None,
        "avg_r_net": (sum(rs_net) / len(rs_net)) if rs_net else None,
        "fees_total": sum(_vals("fees_usd")),
        "funding_total": sum(_vals("funding_pnl_usd")),
    }


def aggregate_trades(trades: list[dict]) -> dict:
    """{"overall": bucket, "by_direction": {"long": bucket, "short": bucket}}."""
    return {
        "overall": _bucket(trades),
        "by_direction": {
            d: _bucket([t for t in trades if t.get("direction") == d])
            for d in ("long", "short")
        },
    }


# ---------- pure message composition ----------


def _stat_line(label: str, b: dict) -> str:
    n = int(b.get("n") or 0)
    win = f"{b['win_pct']:5.1f}" if b.get("win_pct") is not None else "    —"
    pnl = f"{b['pnl_total']:+9.2f}" if n else "        —"
    avg_r = f"{b['avg_r']:+6.3f}" if b.get("avg_r") is not None else "     —"
    net_r = f"{b['avg_r_net']:+6.3f}" if b.get("avg_r_net") is not None else "     —"
    return f"`{label:<5} {n:>3} {win} {pnl} {avg_r} {net_r}`"


def _recommendation(n: int, avg_r_net: float | None) -> str:
    if n < MIN_SAMPLE_FOR_VERDICT:
        return (
            f"keep testing — `n={n}` closed trades, "
            f"need `{MIN_SAMPLE_FOR_VERDICT}` before judging the filters"
        )
    if avg_r_net is not None and avg_r_net > 0:
        return "gate passed — consider partial-trail exits next"
    return "filters not proving out — re-examine"


def compose_report(stats: dict) -> str:
    """Render the aggregate dict into a Telegram Markdown message (<3500 chars).

    Pure: no I/O, so tests can feed fake aggregates. Skip reasons and stat
    rows live in backtick code spans so underscores don't italicize.
    """
    overall = stats.get("overall") or _bucket([])
    by_dir = stats.get("by_direction") or {}
    skips: dict = stats.get("skips") or {}
    open_n = int(stats.get("open_positions") or 0)
    since = stats.get("since") or ENTRY_FILTER_ERA_START

    n = int(overall.get("n") or 0)
    avg_r_net = overall.get("avg_r_net")

    lines = [
        "🤖 *WaveBot Forward Test — weekly report*",
        f"Entries since `{since}` (entry-filter era)",
        "",
    ]
    if n and avg_r_net is not None:
        lines.append(
            f"*Verdict:* filtered stream `{avg_r_net:+.3f}R` net avg "
            f"over `n={n}` closed trades"
        )
    else:
        lines.append(f"*Verdict:* `n={n}` closed trades — no expectancy to judge yet")
    lines.append(
        f"Pre-filter baselines: `{BASELINE_REPLAY_R:+.3f}R` replay / "
        f"`{BASELINE_RECORDED_R:+.3f}R` recorded"
    )

    lines += [
        "",
        "*Closed trades*",
        "`dir     n  win%      pnl$   avgR   netR`",
        _stat_line("all", overall),
        _stat_line("long", by_dir.get("long") or _bucket([])),
        _stat_line("short", by_dir.get("short") or _bucket([])),
        (
            f"Fees `${overall.get('fees_total', 0.0):,.2f}` | "
            f"Funding `${overall.get('funding_total', 0.0):+,.2f}` | "
            f"Open now: `{open_n}`"
        ),
    ]

    # V2 retrace orders — only rendered when the loader provides the counts,
    # so pre-v2 aggregates (and their tests) keep composing unchanged.
    orders = stats.get("orders")
    if orders:
        fill_rate = orders.get("fill_rate")
        rate_s = f"{100.0 * fill_rate:.0f}%" if fill_rate is not None else "—"
        lines.append(
            f"Orders: pending `{int(orders.get('pending') or 0)}` | "
            f"cancelled `{int(orders.get('cancelled') or 0)}` | "
            f"fill rate `{rate_s}`"
        )

    total_skips = sum(skips.values())
    lines += ["", f"*Skipped signals* (`{total_skips}` since era start)"]
    for reason in NEW_FILTER_REASONS:
        lines.append(f"`{reason:<18} {skips.get(reason, 0):>4}` ← new filter")
    others = sorted(
        ((r, c) for r, c in skips.items() if r not in NEW_FILTER_REASONS),
        key=lambda rc: (-rc[1], rc[0]),
    )
    for reason, count in others[:_MAX_OTHER_SKIP_LINES]:
        lines.append(f"`{reason:<18} {count:>4}`")
    if len(others) > _MAX_OTHER_SKIP_LINES:
        rest = sum(c for _, c in others[_MAX_OTHER_SKIP_LINES:])
        lines.append(f"`{'(other)':<18} {rest:>4}`")

    lines += ["", f"*Recommendation:* {_recommendation(n, avg_r_net)}"]

    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 1] + "…"
    return text


def compose_majorsbot_section(mb: dict) -> str:
    """Render the MajorsBot per-strategy aggregates. Pure, like compose_report.

    mb = {"equity": float | None, "open": int, "per_strategy": {name: {
        "signals", "filled", "pending", "cancelled", "closed",
        "avg_r_net" (None when no closed), "total_r_net"}}}
    """
    per = mb.get("per_strategy") or {}
    eq = mb.get("equity")
    eq_s = f"`${eq:,.2f}`" if eq is not None else "`—`"
    lines = [
        "📊 *MajorsBot — majors paper bot*",
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
    return "\n".join(lines)


# ---------- DB loading ----------


async def _load_stats(since: datetime) -> dict:
    """Query bot_trades + bot_skipped_signals. Imports are local so the pure
    composition helpers stay importable without a reachable database."""
    from sqlalchemy import func, select

    from app.database import AsyncSessionLocal
    from app.models.bot import BotSkippedSignal, BotTrade

    async with AsyncSessionLocal() as db:
        closed_rows = (
            (
                await db.execute(
                    select(BotTrade).where(
                        BotTrade.status == "closed", BotTrade.entry_at >= since
                    )
                )
            )
            .scalars()
            .all()
        )
        open_count = (
            await db.execute(
                select(func.count())
                .select_from(BotTrade)
                .where(BotTrade.status == "open")
            )
        ).scalar_one()
        skip_rows = (
            await db.execute(
                select(BotSkippedSignal.skip_reason, func.count())
                .where(BotSkippedSignal.alert_detected_at >= since)
                .group_by(BotSkippedSignal.skip_reason)
            )
        ).all()
        # V2 retrace-order accounting: fill rate over resolved limits since the
        # window start (pending is a point-in-time count, not window-bound).
        pending_count = (
            await db.execute(
                select(func.count())
                .select_from(BotTrade)
                .where(BotTrade.status == "pending")
            )
        ).scalar_one()
        cancelled_count = (
            await db.execute(
                select(func.count())
                .select_from(BotTrade)
                .where(BotTrade.status == "cancelled", BotTrade.entry_at >= since)
            )
        ).scalar_one()
        filled_count = (
            await db.execute(
                select(func.count())
                .select_from(BotTrade)
                .where(
                    BotTrade.status.in_(("open", "closed")),
                    BotTrade.entry_mode == "retrace",
                    BotTrade.entry_at >= since,
                )
            )
        ).scalar_one()

    trades = [
        {
            "direction": (r.direction or "").lower(),
            "realized_pnl_usd": r.realized_pnl_usd,
            "realized_r": r.realized_r,
            "realized_r_net": r.realized_r_net,
            "fees_usd": r.fees_usd,
            "funding_pnl_usd": r.funding_pnl_usd,
        }
        for r in closed_rows
    ]
    stats = aggregate_trades(trades)
    stats["open_positions"] = int(open_count)
    stats["skips"] = {reason: int(count) for reason, count in skip_rows}
    resolved = int(filled_count) + int(cancelled_count)
    stats["orders"] = {
        "pending": int(pending_count),
        "cancelled": int(cancelled_count),
        "filled": int(filled_count),
        "fill_rate": (int(filled_count) / resolved) if resolved else None,
    }
    stats["since"] = since.isoformat()
    return stats


async def _load_majorsbot_stats(since: datetime) -> dict:
    """Query majorsbot_trades per strategy: signals (rows created), fills,
    pending (point-in-time), cancellations, closed n + net-R stats, equity."""
    from sqlalchemy import func, select

    from app.database import AsyncSessionLocal
    from app.models.majorsbot import MajorsBotTrade

    per: dict[str, dict] = {
        name: {
            "signals": 0,
            "filled": 0,
            "pending": 0,
            "cancelled": 0,
            "closed": 0,
            "avg_r_net": None,
            "total_r_net": None,
        }
        for name in MAJORSBOT_STRATEGIES
    }
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
        per.setdefault(strat, dict(per[MAJORSBOT_STRATEGIES[0]]))["signals"] = int(n)
    for strat, status_val, n in status_rows:
        b = per.setdefault(strat, dict(per[MAJORSBOT_STRATEGIES[0]]))
        if status_val in ("open", "closed"):
            b["filled"] += int(n)
        elif status_val == "cancelled":
            b["cancelled"] += int(n)
    for strat, n in pending_now_rows:
        per.setdefault(strat, dict(per[MAJORSBOT_STRATEGIES[0]]))["pending"] = int(n)
    for strat, n, r_net_sum in closed_rows:
        b = per.setdefault(strat, dict(per[MAJORSBOT_STRATEGIES[0]]))
        b["closed"] = int(n)
        b["total_r_net"] = float(r_net_sum or 0)
        b["avg_r_net"] = (float(r_net_sum or 0) / int(n)) if int(n) else None

    equity_val = None
    try:
        from app.modules.majorsbot import equity as mb_equity

        equity_val = float(await mb_equity.get_paper_equity())
    except Exception as e:  # Redis down must not sink the report
        log.warning("forward_report_majorsbot_equity_failed", error=str(e))

    return {"equity": equity_val, "open": int(open_count), "per_strategy": per}


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

    stats = await _load_stats(since)
    text = compose_report(stats)
    # MajorsBot section rides the same message; its failure must never break
    # the WaveBot report.
    try:
        mb_stats = await _load_majorsbot_stats(since)
        text = text + "\n\n" + compose_majorsbot_section(mb_stats)
        if len(text) > MAX_COMBINED_CHARS:
            text = text[: MAX_COMBINED_CHARS - 1] + "…"
    except Exception as e:
        log.warning("forward_report_majorsbot_failed", error=str(e))
    overall = stats["overall"]
    log.info(
        "forward_report_composed",
        since=stats["since"],
        n_closed=overall["n"],
        win_pct=overall["win_pct"],
        avg_r=overall["avg_r"],
        avg_r_net=overall["avg_r_net"],
        pnl_total=overall["pnl_total"],
        open_positions=stats["open_positions"],
        skips=stats["skips"],
        chars=len(text),
    )

    if args.dry_run:
        print(text)
        return
    await _send_report(text)


async def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Weekly WaveBot forward-test report → Telegram."
    )
    parser.add_argument(
        "--since",
        default=ENTRY_FILTER_ERA_START,
        help=f"ISO datetime — only trades entered on/after this "
        f"(default: {ENTRY_FILTER_ERA_START}, the entry-filter era boundary)",
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
