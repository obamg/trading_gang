"""Sanity checks for the Fernet encryption helper."""
from __future__ import annotations

import pytest

from app.services.encryption import EncryptionError, decrypt, encrypt


def test_roundtrip_preserves_plaintext():
    secret = "binance-api-secret-abc123"
    token = encrypt(secret)
    assert token != secret
    assert decrypt(token) == secret


def test_tampered_ciphertext_raises():
    token = encrypt("hello")
    tampered = token[:-4] + "XXXX"
    with pytest.raises(EncryptionError):
        decrypt(tampered)


def test_encrypt_rejects_none():
    with pytest.raises(EncryptionError):
        encrypt(None)  # type: ignore[arg-type]


def test_ciphertexts_are_unique_per_call():
    # Fernet uses a random IV, so the same plaintext encrypts to different tokens.
    a = encrypt("same")
    b = encrypt("same")
    assert a != b
    assert decrypt(a) == decrypt(b) == "same"
