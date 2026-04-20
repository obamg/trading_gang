"""Seed a ready-to-use development environment.

Idempotent: safe to run repeatedly. Creates:
  * A verified test user (test@example.com / test1234) with default watchlist + settings
  * A second empty account (demo@example.com / demo1234) for multi-user testing
Note: we use ``example.com`` rather than a .local domain because pydantic's
EmailStr validator rejects special-use TLDs; the /auth routes use EmailStr so
seeded accounts must pass the same check to be loggable.

Run with:
    docker compose run --rm seed
or (inside the api container):
    python -m app.scripts.seed_dev
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.logging_config import configure_logging, log
from app.models.settings import UserSettings, Watchlist
from app.models.user import User
from app.services.auth_service import hash_password


TEST_ACCOUNTS = [
    {
        "email": "test@example.com",
        "password": "test1234",
        "full_name": "Test Trader",
        "is_verified": True,
        "watchlist": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    },
    {
        "email": "demo@example.com",
        "password": "demo1234",
        "full_name": "Demo User",
        "is_verified": True,
        "watchlist": [],
    },
]


async def _seed_user(db, spec: dict) -> tuple[User, bool]:
    """Upsert a single user. Returns (user, created)."""
    existing = await db.execute(select(User).where(User.email == spec["email"]))
    user = existing.scalar_one_or_none()
    if user is not None:
        return user, False

    user = User(
        email=spec["email"],
        password_hash=hash_password(spec["password"]),
        full_name=spec["full_name"],
        auth_provider="email",
        is_verified=spec["is_verified"],
        is_active=True,
    )
    db.add(user)
    await db.flush()

    db.add(UserSettings(user_id=user.id))
    db.add(
        Watchlist(
            user_id=user.id,
            name="Default",
            symbols=spec["watchlist"],
            is_default=True,
        )
    )
    return user, True


async def main() -> int:
    configure_logging()
    summary: list[str] = []
    async with AsyncSessionLocal() as db:
        for spec in TEST_ACCOUNTS:
            user, created = await _seed_user(db, spec)
            verb = "created" if created else "exists"
            summary.append(f"  - {spec['email']} / {spec['password']}  [{verb}]")
            log.info("seed_user", email=user.email, created=created)
        await db.commit()

    print("TradeCore dev seed complete. Accounts ready to log in:")
    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
