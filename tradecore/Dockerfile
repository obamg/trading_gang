# TradeCore backend — FastAPI + gunicorn + uvicorn workers
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps — build tools for asyncpg/psycopg2, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first to leverage layer caching
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install gunicorn==23.0.0

# Copy the app
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root user
RUN groupadd --system tradecore && \
    useradd --system --gid tradecore --home /app --shell /sbin/nologin tradecore && \
    chown -R tradecore:tradecore /app
USER tradecore

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Entrypoint runs migrations (and optional seed) before handing off to CMD.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Single worker required: WebSocket ConnectionManager and Redis pubsub relay
# are in-process singletons that cannot be shared across workers. Multiple
# workers would cause split-brain WS state and duplicate alert delivery.
CMD ["gunicorn", "app.main:app", \
     "--workers", "1", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--timeout", "60"]
