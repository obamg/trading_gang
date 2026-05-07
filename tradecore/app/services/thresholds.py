"""Per-symbol rolling-percentile thresholds.

Replaces hardcoded global thresholds (z=3, $300k, imbalance 2.5, ...) with a
per-symbol rolling distribution. Values are stored in a Redis sorted set per
(symbol, metric); the score is epoch ms, the member is "{ts}:{value}". Members
are unique because the timestamp is part of the key, and we cap each set to
``ROLLING_MAX_SAMPLES`` newest entries.

API:
  add_sample(symbol, metric, value)
  get_percentile(symbol, metric, p, fallback=...)
  get_z_score(symbol, metric, value, min_samples=...)
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

from app.services import redis_service

ROLLING_MAX_SAMPLES = 500
ROLLING_TTL_SECONDS = 30 * 24 * 3600
MIN_SAMPLES_DEFAULT = 30


def _key(symbol: str, metric: str) -> str:
    return f"thresholds:{symbol.upper()}:{metric}"


async def add_sample(symbol: str, metric: str, value: float) -> None:
    r = redis_service.get_redis()
    key = _key(symbol, metric)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    member = f"{now_ms}:{value:.10g}"
    await r.zadd(key, {member: now_ms})
    await r.zremrangebyrank(key, 0, -(ROLLING_MAX_SAMPLES + 1))
    await r.expire(key, ROLLING_TTL_SECONDS)


async def _get_samples(symbol: str, metric: str) -> list[float]:
    r = redis_service.get_redis()
    raw = await r.zrange(_key(symbol, metric), 0, -1)
    out: list[float] = []
    for member in raw:
        parts = member.split(":", 1)
        if len(parts) != 2:
            continue
        try:
            out.append(float(parts[1]))
        except (ValueError, TypeError):
            continue
    return out


async def get_percentile(
    symbol: str,
    metric: str,
    p: float,
    *,
    fallback: float | None = None,
    min_samples: int = MIN_SAMPLES_DEFAULT,
) -> float | None:
    samples = await _get_samples(symbol, metric)
    if len(samples) < min_samples:
        return fallback
    samples.sort()
    if p <= 0:
        return samples[0]
    if p >= 100:
        return samples[-1]
    k = (p / 100.0) * (len(samples) - 1)
    lo = int(k)
    hi = min(lo + 1, len(samples) - 1)
    frac = k - lo
    return samples[lo] * (1 - frac) + samples[hi] * frac


async def get_z_score(
    symbol: str,
    metric: str,
    value: float,
    *,
    min_samples: int = MIN_SAMPLES_DEFAULT,
) -> float | None:
    samples = await _get_samples(symbol, metric)
    if len(samples) < min_samples:
        return None
    mean = statistics.fmean(samples)
    try:
        std = statistics.pstdev(samples)
    except statistics.StatisticsError:
        return None
    if std <= 0:
        return None
    return (value - mean) / std


async def sample_count(symbol: str, metric: str) -> int:
    r = redis_service.get_redis()
    return int(await r.zcard(_key(symbol, metric)))


__all__ = [
    "add_sample",
    "get_percentile",
    "get_z_score",
    "sample_count",
    "ROLLING_MAX_SAMPLES",
]
