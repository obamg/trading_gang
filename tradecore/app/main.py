"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
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
from app.modules.tradelog.router import router as tradelog_router
from app.modules.newspulse.router import router as newspulse_router
from app.modules.whaleradar.router import router as whaleradar_router
from app.services import redis_service
from app.services.binance_stream import manager as binance_manager
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

    # Singleton services must only run on one worker. Use a Redis lock so the
    # first worker to start becomes the leader; the rest skip these services.
    r = redis_service.get_redis()
    is_leader = await r.set("tradecore:leader", "1", nx=True, ex=300)
    _leader_refresh: asyncio.Task | None = None
    if is_leader:
        async def _keep_leader():
            try:
                while True:
                    await asyncio.sleep(120)
                    await r.expire("tradecore:leader", 300)
            except asyncio.CancelledError:
                pass
        _leader_refresh = asyncio.create_task(_keep_leader())
        await binance_manager.start()
        await telegram_service.start()
        await liquidation_listener.start()
        await oracle_trigger.start()
        log.info("worker_is_leader", singletons="binance,telegram,liquidation,oracle")
    else:
        log.info("worker_is_follower", singletons="skipped")

    if settings.scheduler_enabled:
        start_scheduler()
    log.info("startup_complete", env=settings.app_env)
    yield
    if settings.scheduler_enabled:
        stop_scheduler()
    if is_leader:
        await oracle_trigger.stop()
        await liquidation_listener.stop()
        await telegram_service.stop()
        await binance_manager.stop()
        if _leader_refresh:
            _leader_refresh.cancel()
        await r.delete("tradecore:leader")
    await ws_manager.stop_relay()
    await redis_service.close_redis()
    await engine.dispose()
    log.info("shutdown_complete")


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
app.include_router(tradelog_router)
app.include_router(performance_router)
app.include_router(newspulse_router)


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

    # Binance stream liveness — fail if last BTCUSDT candle is older than 10min
    stream_status = "ok"
    try:
        r = redis_service.get_redis()
        raw = await r.lindex("candles:BTCUSDT", 0)
        if raw is None:
            stream_status = "no_data" if settings.binance_streams_enabled else "disabled"
        else:
            import json as _json
            candle = _json.loads(raw)
            ts = candle.get("close_time") or candle.get("open_time") or 0
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
        "binance_stream": stream_status,
        "env": settings.app_env,
    }


@app.get("/")
async def root():
    return {"name": "TradeCore API", "version": "0.1.0", "docs": "/docs"}
