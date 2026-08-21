"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import engine
from app.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    validation_error_handler,
)
from app.logging_config import configure_logging, log
from app.rate_limit import limiter
from app.routers import auth as auth_router
from app.routers import settings as settings_router
from app.routers import ws as ws_router
from app.modules.gemradar.router import router as gemradar_router
from app.modules.liquidmap.router import router as liquidmap_router
from app.modules.liquidmap.tracker import listener as liquidation_listener
from app.modules.oracle.listener import trigger as oracle_trigger
from app.modules.macropulse.router import router as macropulse_router
from app.modules.oracle.router import router as oracle_router
from app.modules.performance.router import router as performance_router
from app.modules.radarx.router import router as radarx_router
from app.modules.riskcalc.router import router as riskcalc_router
from app.modules.sentimentpulse.router import router as sentiment_router
from app.modules.flowpulse.router import router as flowpulse_router
from app.modules.newspulse.router import router as newspulse_router
from app.modules.cmcpulse.router import router as cmcpulse_router
from app.modules.whaleradar.router import router as whaleradar_router
from app.modules.exchanges.router import router as exchanges_router
from app.modules.walletwatch.router import router as walletwatch_router
from app.modules.listingwatch.router import router as listingwatch_router
from app.modules.awakening.router import router as awakening_router
from app.modules.wavewatch.router import router as wavewatch_router
from app.modules.chainpulse.router import router as chainpulse_router
from app.modules.majorsbot.router import router as majorsbot_router
from app.services import redis_service
from app.services.binance_stream import manager as binance_manager
from app.services.bybit_stream import manager as bybit_manager
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.telegram_service import service as telegram_service
from app.services.ws_manager import manager as ws_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Verify DB connectivity on startup
    async with engine.begin() as conn:
        from sqlalchemy import text
        await conn.execute(text("SELECT 1"))
    # Redis + downstream services
    await redis_service.init_redis()
    await ws_manager.start_relay()

    # Singleton services (binance stream, telegram, liquidation listener,
    # oracle trigger) must only run on one worker. Self-healing leader
    # election: a worker holds a Redis lock with a unique token, refreshes
    # it on a short cadence, and re-attempts acquisition if the lock is
    # ever lost. Survives non-graceful shutdowns — a stale lock from a
    # killed container expires within LEADER_TTL_SECONDS and the next
    # poll picks it up.
    r = redis_service.get_redis()
    leader_stop = asyncio.Event()
    leader_task = asyncio.create_task(_run_leader_loop(r, leader_stop))

    if settings.scheduler_enabled:
        start_scheduler()
    log.info("startup_complete", env=settings.app_env)
    yield
    if settings.scheduler_enabled:
        stop_scheduler()
    leader_stop.set()
    try:
        await leader_task
    except Exception as e:
        log.error("leader_task_shutdown_error", err=str(e))
    await ws_manager.stop_relay()
    await redis_service.close_redis()
    await engine.dispose()
    log.info("shutdown_complete")


LEADER_KEY = "tradecore:leader"
LEADER_TTL_SECONDS = 45
LEADER_POLL_SECONDS = 15


async def _run_leader_loop(r, stop_event: asyncio.Event) -> None:
    """Self-healing leader election. Runs throughout the worker's lifetime.

    - When this worker holds the lock: refresh TTL on each poll.
    - When it doesn't: try to acquire (NX). If acquired, start singletons.
    - If the lock is lost (clock skew, network partition): stop singletons.
    - On lifespan shutdown: stop singletons and release the lock if owned.
    """
    token = secrets.token_hex(8)
    owns = False

    # Pick the market-data manager once per leader acquisition. Switching
    # ``MARKET_DATA_SOURCE`` requires a restart, which is fine — it's a
    # deploy-time config, not a runtime knob.
    source = (settings.market_data_source or "").lower()
    if source == "bybit":
        market_manager = bybit_manager
    elif source == "binance" and settings.binance_streams_enabled:
        market_manager = binance_manager
    else:
        market_manager = None

    async def _start_singletons() -> None:
        if market_manager is not None:
            await market_manager.start()
        else:
            log.info("market_data_source_disabled", source=source)
        await telegram_service.start()
        await liquidation_listener.start()
        await oracle_trigger.start()
        log.info("worker_became_leader", token=token, market_source=source)

    async def _stop_singletons() -> None:
        try:
            await oracle_trigger.stop()
            await liquidation_listener.stop()
            await telegram_service.stop()
            if market_manager is not None:
                await market_manager.stop()
        except Exception as e:
            log.error("singleton_stop_error", err=str(e))

    try:
        while not stop_event.is_set():
            try:
                if owns:
                    current = await r.get(LEADER_KEY)
                    if isinstance(current, bytes):
                        current = current.decode()
                    if current == token:
                        await r.expire(LEADER_KEY, LEADER_TTL_SECONDS)
                    else:
                        log.warning("worker_lost_leadership", token=token, current=current)
                        owns = False
                        await _stop_singletons()
                else:
                    acquired = await r.set(LEADER_KEY, token, nx=True, ex=LEADER_TTL_SECONDS)
                    if acquired:
                        owns = True
                        await _start_singletons()
            except Exception as e:
                log.error("leader_loop_error", err=str(e), owns=owns)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=LEADER_POLL_SECONDS)
                break
            except asyncio.TimeoutError:
                continue
    finally:
        if owns:
            log.info("worker_releasing_leadership", token=token)
            await _stop_singletons()
            try:
                current = await r.get(LEADER_KEY)
                if isinstance(current, bytes):
                    current = current.decode()
                if current == token:
                    await r.delete(LEADER_KEY)
            except Exception as e:
                log.error("leader_release_error", err=str(e))


app = FastAPI(
    title="TradeCore API",
    version="0.1.0",
    lifespan=lifespan,
)

# ----- Middleware -----

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# slowapi wiring
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "Too many requests", "code": "RATE_LIMITED"},
    )


# ----- Error handlers -----

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)


# ----- Routers -----

app.include_router(auth_router.router)
app.include_router(settings_router.router)
app.include_router(ws_router.router)
app.include_router(radarx_router)
app.include_router(whaleradar_router)
app.include_router(gemradar_router)
app.include_router(sentiment_router)
app.include_router(macropulse_router)
app.include_router(liquidmap_router)
app.include_router(oracle_router)
app.include_router(riskcalc_router)
app.include_router(performance_router)
app.include_router(newspulse_router)
app.include_router(cmcpulse_router)
app.include_router(flowpulse_router)
app.include_router(exchanges_router)
app.include_router(walletwatch_router)
app.include_router(listingwatch_router)
app.include_router(awakening_router)
app.include_router(wavewatch_router)
app.include_router(chainpulse_router)
app.include_router(majorsbot_router)


@app.get("/health")
async def health():
    """Health probe — returns 200 with per-dependency status. Used by load balancer + docker healthcheck."""
    from sqlalchemy import text
    import time
    from app.database import AsyncSessionLocal

    db_status = "ok"
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"fail: {type(exc).__name__}"

    redis_status = "ok"
    try:
        r = redis_service.get_redis()
        await r.ping()
    except Exception as exc:
        redis_status = f"fail: {type(exc).__name__}"

    # Market data stream liveness — same Redis schema regardless of source.
    # Fail if last BTCUSDT candle is older than 10min.
    stream_status = "ok"
    try:
        r = redis_service.get_redis()
        raw = await r.lindex("candles:BTCUSDT", 0)
        if raw is None:
            source = (settings.market_data_source or "").lower()
            stream_status = "no_data" if source in ("bybit", "binance") else "disabled"
        else:
            import json as _json
            candle = _json.loads(raw)
            # candles are stored with single-letter Binance kline keys:
            # T = close time, t = open time (both ms since epoch)
            ts = (
                candle.get("T")
                or candle.get("t")
                or candle.get("close_time")
                or candle.get("open_time")
                or 0
            )
            # ms → s if needed
            ts_s = ts / 1000 if ts > 10_000_000_000 else ts
            age_s = time.time() - ts_s
            if age_s > 600:
                stream_status = f"stale_{int(age_s)}s"
    except Exception as exc:
        stream_status = f"fail: {type(exc).__name__}"

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return {
        "status": overall,
        "db": db_status,
        "redis": redis_status,
        "binance_stream": stream_status,  # historical key name; covers active source
        "market_data_source": settings.market_data_source,
        "env": settings.app_env,
    }


@app.get("/")
async def root():
    return {"name": "TradeCore API", "version": "0.1.0", "docs": "/docs"}
