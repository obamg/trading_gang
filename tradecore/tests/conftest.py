"""Shared pytest fixtures.

These tests focus on pure logic (RiskCalc, Oracle scoring, password hashing)
and Redis-backed detectors (RadarX). Full FastAPI integration tests run in CI
against a real Postgres service container; those fixtures live at the bottom
of this file and are skipped when the DB is unreachable.
"""
from __future__ import annotations

import asyncio
import os

import pytest


# Set safe defaults BEFORE any app modules import.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-only-for-ci")
os.environ.setdefault("APP_SECRET_KEY", "test-app-secret-only-for-ci")
os.environ.setdefault(
    "ENCRYPTION_KEY", "8g-1hXY-2V0nD40VOCk3k-c0mHScOBWxCpL_qcfHQZE="
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class FakeRedis:
    """Minimal async stand-in for the Redis client used by the detectors.

    Supports just enough operations for RadarX + cooldown logic.
    """

    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}
        self._keys: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._published: list[tuple[str, str]] = []

    async def ping(self) -> bool:
        return True

    async def lindex(self, key: str, idx: int):
        lst = self._lists.get(key, [])
        try:
            return lst[idx]
        except IndexError:
            return None

    async def lrange(self, key: str, start: int, stop: int):
        lst = self._lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

    async def lpush(self, key: str, *values: str):
        self._lists.setdefault(key, [])
        for v in values:
            self._lists[key].insert(0, v)
        return len(self._lists[key])

    async def ltrim(self, key: str, start: int, stop: int):
        if key in self._lists:
            self._lists[key] = self._lists[key][start : stop + 1]
        return True

    async def set(self, key: str, value: str, ex: int | None = None):
        self._keys[key] = value
        return True

    async def get(self, key: str):
        return self._keys.get(key)

    async def exists(self, key: str):
        return 1 if key in self._keys else 0

    async def smembers(self, key: str):
        return self._sets.get(key, set())

    async def sadd(self, key: str, *members: str):
        self._sets.setdefault(key, set()).update(members)
        return len(members)

    async def hincrbyfloat(self, key: str, field: str, increment: float):
        self._hashes.setdefault(key, {})
        cur = float(self._hashes[key].get(field, "0"))
        self._hashes[key][field] = str(cur + increment)
        return cur + increment

    async def hgetall(self, key: str):
        return dict(self._hashes.get(key, {}))

    async def expire(self, key: str, seconds: int):
        return True

    async def publish(self, channel: str, message: str):
        self._published.append((channel, message))
        return 1

    def published(self) -> list[tuple[str, str]]:
        return list(self._published)


@pytest.fixture
def fake_redis(monkeypatch):
    """Swap the module-level Redis client for a fake and return it."""
    import app.services.redis_service as rsvc

    fake = FakeRedis()
    monkeypatch.setattr(rsvc, "_redis", fake)
    return fake
