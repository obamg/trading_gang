"""Standalone scheduler entrypoint.

Used by the prod `scheduler` container so APScheduler jobs run in a dedicated
process and don't contend with the API workers. Shares the exact same codepath
as the in-process scheduler; it just skips the FastAPI HTTP server.

Run with:
    python -m app.services.scheduler_standalone
"""
from __future__ import annotations

import asyncio
import signal

from app.logging_config import configure_logging, log
from app.services import redis_service
from app.services.scheduler import start_scheduler, stop_scheduler


async def main() -> None:
    configure_logging()
    await redis_service.init_redis()
    start_scheduler()
    log.info("scheduler_standalone_started")

    stop_event = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        await stop_event.wait()
    finally:
        stop_scheduler()
        await redis_service.close_redis()
        log.info("scheduler_standalone_stopped")


if __name__ == "__main__":
    asyncio.run(main())
