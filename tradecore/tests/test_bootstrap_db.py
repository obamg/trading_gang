"""Database bootstrap strategy selection.

The branch that matters is the third one. Stamping a POPULATED but
unversioned database at head would tell Alembic that migrations nobody ran
are already applied — silently, over a schema no one verified. It must
attempt a real upgrade and fail loudly instead.
"""
from __future__ import annotations

from app.scripts.bootstrap_db import classify


def test_versioned_database_upgrades():
    """The prod path: an alembic_version row means apply new revisions."""
    assert classify({"alembic_version", "users", "majorsbot_trades"}) == "versioned"


def test_empty_database_gets_create_all():
    """001_initial is a create_all baseline, so replaying the chain on an
    empty DB conflicts with itself — build the schema directly instead."""
    assert classify(set()) == "empty"


def test_populated_but_unversioned_is_never_stamped():
    """Must NOT be treated as empty: stamping would skip every migration."""
    assert classify({"users", "trades"}) == "unversioned"


def test_alembic_version_alone_still_counts_as_versioned():
    """A stamped-but-schemaless DB is someone's deliberate state; upgrade it."""
    assert classify({"alembic_version"}) == "versioned"
