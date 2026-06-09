"""APScheduler wiring for detection loops.

All jobs swallow and log their own exceptions so a single failure never takes
down the scheduler. Jobs are no-ops in local dev unless there's data in Redis.
"""
from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.modules.gemradar import detector as gemradar_detector
from app.modules.macropulse import collector as macropulse_collector
from app.modules.oracle import board as oracle_board
from app.modules.oracle import engine as oracle_engine
from app.modules.performance import aggregator as performance_aggregator
from app.modules.radarx import detector as radarx_detector
from app.modules.sentimentpulse import collector as sentiment_collector
from app.modules.flowpulse import detector as flowpulse_detector
from app.modules.newspulse import collector as newspulse_collector
from app.modules.liquidmap import tracker as liquidmap_tracker
from app.modules.positionmonitor import monitor as positionmonitor
from app.modules.walletwatch import detector as walletwatch_detector
from app.modules.walletwatch.discovery import engine as discovery_engine
from app.modules.walletwatch.discovery import promote as discovery_promote
from app.modules.bot import monitor as bot_monitor
from app.modules.listingwatch import detector as listingwatch_detector
from app.modules.listingwatch import watcher as listingwatch_watcher
from app.modules.awakening import detector as awakening_detector
from app.modules.wavewatch import detector as wavewatch_detector
from app.modules.whaleradar import detector as whaleradar_detector
from app.services import redis_service
from app.services.exchanges import sync as exchange_sync


async def run_radarx_scan() -> None:
    symbols = await redis_service.get_symbol_list()
    if not symbols:
        return
    async with AsyncSessionLocal() as db:
        for symbol in symbols:
            try:
                await radarx_detector.detect_symbol(db, symbol)
            except Exception as e:
                log.error("radarx_scan_failed", symbol=symbol, err=str(e))
            await asyncio.sleep(0.05)


async def run_exchange_sync_all() -> None:
    """Pull fresh fills from every connected exchange and update Trade rows.

    One credential failing does not abort the loop; the failure is recorded
    on the credential row's `last_sync_error` field.
    """
    async with AsyncSessionLocal() as db:
        try:
            result = await exchange_sync.sync_all_active_credentials(db)
            log.info("exchange_sync_tick", **result)
        except Exception as exc:
            log.error("exchange_sync_tick_failed", err=str(exc))


async def refresh_symbol_list() -> None:
    # BinanceStreamManager already runs its own hourly rediscovery; this is a
    # safety net so GemRadar / tests don't get stuck with an empty set.
    symbols = await redis_service.get_symbol_list()
    if symbols:
        return
    log.info("scheduler_symbol_list_empty")


_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(run_radarx_scan, "interval", minutes=2, id="radarx_scan", coalesce=True, max_instances=1)
    sched.add_job(
        whaleradar_detector.run_large_trade_scan,
        "interval",
        minutes=1,
        id="whale_trades_scan",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        whaleradar_detector.run_oi_surge_scan,
        "interval",
        minutes=5,
        id="whale_oi_scan",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        whaleradar_detector.run_slow_oi_scan,
        "interval",
        minutes=15,
        id="whale_slow_oi_scan",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        whaleradar_detector.run_onchain_poll,
        "interval",
        seconds=60,
        id="whale_onchain_poll",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        gemradar_detector.run_scan,
        "interval",
        minutes=2,
        id="gemradar_scan",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        gemradar_detector.run_cex_listing_scan,
        "interval",
        minutes=15,
        id="gemradar_cex_listings",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        liquidmap_tracker.run_poll_force_orders,
        "interval",
        seconds=30,
        id="liquidmap_poll",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        flowpulse_detector.run_scheduled_scan,
        "interval",
        minutes=1,
        id="flowpulse_scan",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        positionmonitor.run_position_check,
        "interval",
        seconds=30,
        id="position_monitor",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(refresh_symbol_list, "interval", minutes=60, id="symbol_refresh")
    # Team 5 — analysis modules
    sched.add_job(
        sentiment_collector.run_hourly_collection,
        "interval",
        minutes=60,
        id="sentiment_hourly",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        sentiment_collector.run_funding_fast,
        "interval",
        minutes=5,
        id="sentiment_funding_fast",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        macropulse_collector.run_hourly_market_data,
        "interval",
        minutes=60,
        id="macro_hourly",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        macropulse_collector.run_daily_snapshot,
        "interval",
        hours=24,
        id="macro_daily",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        macropulse_collector.run_calendar_sync,
        "interval",
        hours=24,
        id="macro_calendar",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        newspulse_collector.run_news_collection,
        "interval",
        minutes=5,
        id="newspulse_collect",
        coalesce=True,
        max_instances=1,
    )
    # Team 6 — Oracle outcome measurement
    sched.add_job(
        oracle_engine.measure_outcomes,
        "interval",
        minutes=5,
        id="oracle_outcomes",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        performance_aggregator.refresh_all_users_performance,
        "interval",
        minutes=15,
        id="performance_refresh",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        run_exchange_sync_all,
        "interval",
        minutes=15,
        id="exchange_sync_all",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        walletwatch_detector.run_walletwatch_scan,
        "interval",
        seconds=60,
        id="walletwatch_scan",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        discovery_engine.refresh_candidates_job,
        "interval",
        hours=6,
        id="discovery_refresh_candidates",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        discovery_engine.score_candidates_job,
        "interval",
        hours=1,
        id="discovery_score_candidates",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        discovery_promote.auto_promote_job,
        "interval",
        hours=1,
        id="discovery_auto_promote",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        bot_monitor.run_monitor_tick,
        "interval",
        seconds=int(settings.bot_monitor_tick_seconds),
        id="bot_monitor",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        bot_monitor.run_daily_anchor_reset,
        "cron",
        hour=0,
        minute=0,
        id="bot_daily_anchor",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        oracle_board.run_board_job,
        "interval",
        minutes=3,
        id="oracle_board_refresh",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        listingwatch_detector.run_listingwatch_detect,
        "interval",
        seconds=60,
        id="listingwatch_detect",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        listingwatch_watcher.run_listingwatch_tick,
        "interval",
        seconds=30,
        id="listingwatch_watch",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        awakening_detector.run_awakening_tick,
        "interval",
        minutes=5,
        id="awakening_tick",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        awakening_detector.run_awakening_baseline_refresh,
        "cron",
        hour=0,
        minute=5,
        id="awakening_baseline",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        wavewatch_detector.run_wavewatch_universe_refresh,
        "interval",
        minutes=15,
        id="wavewatch_universe",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        wavewatch_detector.run_wavewatch_detect_tick,
        "interval",
        minutes=1,
        id="wavewatch_tick",
        coalesce=True,
        max_instances=1,
    )
    sched.start()
    log.info(
        "scheduler_started",
        jobs=[j.id for j in sched.get_jobs()],
        binance_enabled=settings.binance_streams_enabled,
    )
    _scheduler = sched
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("scheduler_stopped")
