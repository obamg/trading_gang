"""Exchange adapter framework + credential encryption/serialization."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.exchange import ExchangeCredential
from app.services.encryption import decrypt
from app.services.exchanges import REGISTRY, register
from app.services.exchanges.base import (
    Credentials,
    Fill,
    get_adapter,
    supported_exchanges,
)
from app.services.exchanges.credentials import load_credentials, serialize


# ---------- Fill dataclass ----------

def test_fill_is_immutable():
    f = Fill(
        exchange="binance", exchange_trade_id="1", exchange_order_id="o1",
        symbol="BTCUSDT", side="buy", price=50000.0, qty=0.1, fee_usd=2.5,
        fee_asset="USDT", realized_pnl_usd=None, is_reduce_only=False,
        ts=datetime.now(timezone.utc),
    )
    with pytest.raises(Exception):
        f.price = 60000.0  # type: ignore[misc]


# ---------- Registry ----------

class _StubAdapter:
    name = "stub"
    display_name = "Stub Exchange"
    requires_passphrase = False

    async def validate(self, creds):
        return {"read": True}

    async def fetch_fills(self, creds, since):
        return []


def test_register_and_lookup():
    stub = _StubAdapter()
    register(stub)
    try:
        assert get_adapter("stub") is stub
        names = {e["name"] for e in supported_exchanges()}
        assert "stub" in names
    finally:
        REGISTRY.pop("stub", None)


def test_unknown_adapter_raises():
    with pytest.raises(ValueError, match="unknown exchange"):
        get_adapter("does-not-exist")


def test_supported_exchanges_returns_metadata_only():
    stub = _StubAdapter()
    register(stub)
    try:
        entries = supported_exchanges()
        for e in entries:
            assert set(e.keys()) == {"name", "display_name", "requires_passphrase"}
    finally:
        REGISTRY.pop("stub", None)


# ---------- Credentials encryption round-trip ----------

def _make_row(*, passphrase: str | None = None) -> ExchangeCredential:
    """Build a row with encrypted fields without touching the DB."""
    from app.services.encryption import encrypt
    row = ExchangeCredential(
        user_id=uuid4(),
        exchange="binance",
        label="default",
        api_key_enc=encrypt("MY-API-KEY"),
        api_secret_enc=encrypt("MY-API-SECRET"),
        passphrase_enc=encrypt(passphrase) if passphrase else None,
        permissions={"read": True, "trade": False, "withdraw": False},
        validated_at=datetime.now(timezone.utc),
    )
    # Fields normally set by SQLAlchemy defaults — populate so serialize() works.
    row.is_active = True
    row.last_synced_at = None
    row.last_sync_error = None
    row.created_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    return row


def test_load_credentials_roundtrip():
    row = _make_row()
    creds = load_credentials(row)
    assert creds.api_key == "MY-API-KEY"
    assert creds.api_secret == "MY-API-SECRET"
    assert creds.passphrase is None


def test_load_credentials_with_passphrase():
    row = _make_row(passphrase="OKX-PASSPHRASE")
    creds = load_credentials(row)
    assert creds.passphrase == "OKX-PASSPHRASE"


def test_serialize_never_leaks_secrets():
    row = _make_row(passphrase="OKX-PASSPHRASE")
    payload = serialize(row)
    # The output must not contain plaintext OR ciphertext for any secret.
    flat = repr(payload)
    assert "MY-API-KEY" not in flat
    assert "MY-API-SECRET" not in flat
    assert "OKX-PASSPHRASE" not in flat
    assert row.api_key_enc not in flat
    assert row.api_secret_enc not in flat
    # Sanity: shape the frontend depends on.
    assert payload["exchange"] == "binance"
    assert payload["label"] == "default"
    assert payload["permissions"] == {"read": True, "trade": False, "withdraw": False}


def test_ciphertext_is_not_plaintext():
    row = _make_row()
    assert row.api_key_enc != "MY-API-KEY"
    assert row.api_secret_enc != "MY-API-SECRET"
    # And it actually decrypts back.
    assert decrypt(row.api_key_enc) == "MY-API-KEY"


# ---------- Credentials value object ----------

def test_credentials_is_immutable():
    c = Credentials(api_key="a", api_secret="b")
    with pytest.raises(Exception):
        c.api_key = "x"  # type: ignore[misc]
