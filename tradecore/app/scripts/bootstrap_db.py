"""Bring the database to head, whichever state it is in.

Replaces a bare ``alembic upgrade head`` in the entrypoint, because that
command **cannot succeed on an empty database**.

The reason is structural, not a one-off bug. ``001_initial`` builds the
schema with ``Base.metadata.create_all`` — a projection of whatever the
models look like *today*, not a snapshot of April 2026. So on a fresh
database, 001 already creates tables that later migrations go on to create
(``004`` → "relation news_articles already exists") and columns that later
migrations go on to add, while NOT creating tables whose models have since
been deleted (``002`` → "relation plans does not exist", because the Plan
model went away with billing). Replaying 002…N on top of a create_all
baseline is contradictory by construction.

This went unnoticed because every database anyone actually uses was created
before the drift: prod and existing dev volumes hold an ``alembic_version``
row and simply apply new revisions, which works fine. Only a brand-new
environment hits it — a fresh ``docker compose up --build``, or a first
deploy to a new host.

So the state decides the strategy:

- **Has ``alembic_version``** → ``alembic upgrade head``. Unchanged, and this
  is the path prod takes. New revisions apply exactly as before.
- **Completely empty** → ``create_all`` + ``alembic stamp head``. This is the
  documented Alembic flow for a create_all baseline: build the current
  schema directly, then record that every revision is accounted for.
- **Has tables but no ``alembic_version``** → ``alembic upgrade head``, so a
  legacy pre-Alembic database still gets a real attempt (and a real error)
  rather than being silently stamped at head over a schema nobody verified.

One caveat worth knowing: a fresh database now gets its schema from the
models, while an old one got it from the migration chain. Those agree only
as long as every migration keeps mirroring the models. They do agree today —
if they ever drift, a table or column present in one and absent in the other
is the symptom.
"""
from __future__ import annotations

import subprocess
import sys

from sqlalchemy import create_engine, inspect, text

from app.config import settings
from app.models import Base


def _sync_url() -> str:
    url = getattr(settings, "database_url_sync", "") or getattr(
        settings, "database_url", ""
    )
    if not url:
        raise SystemExit("[bootstrap_db] no DATABASE_URL_SYNC / DATABASE_URL set")
    return url


def _alembic(*args: str) -> None:
    """Run the alembic CLI, surfacing its exit code."""
    result = subprocess.run(["alembic", *args])
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def classify(tables: set[str]) -> str:
    """'versioned' | 'unversioned' | 'empty' — the strategy selector."""
    if "alembic_version" in tables:
        return "versioned"
    return "unversioned" if tables else "empty"


def main() -> None:
    engine = create_engine(_sync_url())
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    state = classify(tables)

    if state == "versioned":
        print("[bootstrap_db] versioned database → alembic upgrade head", flush=True)
        _alembic("upgrade", "head")
        return

    if state == "unversioned":
        # Populated but unversioned: do NOT stamp over a schema we have not
        # verified. Let alembic try, and fail loudly if it cannot reconcile.
        print(
            f"[bootstrap_db] unversioned database with {len(tables)} existing "
            "table(s) → attempting alembic upgrade head",
            flush=True,
        )
        _alembic("upgrade", "head")
        return

    print(
        "[bootstrap_db] empty database → create_all + stamp head "
        "(001_initial is a create_all baseline; replaying the chain on an "
        "empty DB conflicts with itself)",
        flush=True,
    )
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            # gen_random_uuid() backs most primary keys.
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'))
            Base.metadata.create_all(bind=conn)
    finally:
        engine.dispose()
    _alembic("stamp", "head")
    print("[bootstrap_db] schema created and stamped at head", flush=True)


if __name__ == "__main__":
    sys.exit(main())
