"""Symmetric encryption for stored secrets (e.g. exchange API keys).

Usage:
    from app.services.encryption import encrypt, decrypt
    ciphertext = encrypt("my-binance-secret")
    plaintext  = decrypt(ciphertext)

Key management:
    ENCRYPTION_KEY must be a base64-encoded 32-byte Fernet key. Generate with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Store the key in the environment — never commit it. Rotating the key
    invalidates all existing ciphertexts; if you need to rotate, decrypt with
    the old key then re-encrypt with the new one.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class EncryptionError(RuntimeError):
    """Raised when encryption/decryption fails."""


@lru_cache
def _fernet() -> Fernet:
    key = settings.encryption_key
    if not key:
        # In dev we derive a key from app_secret_key so the API still works,
        # but this is NOT secure — production refuses to boot without a real key.
        if settings.is_production:
            raise EncryptionError("ENCRYPTION_KEY is not set")
        import base64
        import hashlib
        digest = hashlib.sha256(settings.app_secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise EncryptionError(f"Invalid ENCRYPTION_KEY: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns a URL-safe token (str)."""
    if plaintext is None:
        raise EncryptionError("cannot encrypt None")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token. Raises EncryptionError if the token is invalid or tampered."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionError("invalid or tampered ciphertext") from exc
