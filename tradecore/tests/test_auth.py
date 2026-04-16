"""Pure-unit tests for the auth helpers — no DB, no HTTP.

Full HTTP integration coverage lives in CI (pytest in GitHub Actions runs
against a real Postgres service); these tests exercise the token + password
logic that underpins every protected route.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.errors import AppError
from app.services.auth_service import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_verifies_original():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed)


def test_password_hash_rejects_wrong_password():
    hashed = hash_password("one-password")
    assert not verify_password("another-password", hashed)


def test_password_hashes_are_unique_per_call():
    # bcrypt uses a random salt — same plaintext should produce different hashes.
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a)
    assert verify_password("same", b)


def test_access_token_roundtrip():
    user_id = uuid4()
    token, ttl = create_access_token(user_id)
    assert ttl > 0
    decoded = decode_access_token(token)
    assert decoded == user_id


def test_access_token_rejects_garbage():
    with pytest.raises(AppError) as ei:
        decode_access_token("not-a-real-jwt")
    assert ei.value.status_code == 401


def test_refresh_token_is_opaque_and_long():
    token = create_refresh_token()
    assert isinstance(token, str)
    # token_urlsafe(48) → ~64 chars of URL-safe base64
    assert len(token) >= 48
