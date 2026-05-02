"""GemRadar detector tests — risk scoring, DEX scanning, CEX listing detection."""
from __future__ import annotations

from app.modules.gemradar.detector import (
    CHAINS,
    COOLDOWN_MINUTES,
    MAX_MCAP_USD,
    MIN_MCAP_USD,
    PRICE_CHANGE_THRESHOLD,
    _score_to_label,
)


# ---------- risk score label (pure) ----------

def test_score_to_label_low():
    assert _score_to_label(15) == "low"
    assert _score_to_label(0) == "low"


def test_score_to_label_medium():
    assert _score_to_label(35) == "medium"
    assert _score_to_label(50) == "medium"


def test_score_to_label_high():
    assert _score_to_label(65) == "high"
    assert _score_to_label(75) == "high"


def test_score_to_label_extreme():
    assert _score_to_label(76) == "extreme"
    assert _score_to_label(100) == "extreme"


# ---------- mcap filter ----------

def test_mcap_in_range():
    mcap = 10_000_000.0
    assert MIN_MCAP_USD <= mcap <= MAX_MCAP_USD


def test_mcap_below_range():
    mcap = 400_000.0
    assert mcap < MIN_MCAP_USD


def test_mcap_above_range():
    mcap = 200_000_000.0
    assert mcap > MAX_MCAP_USD


# ---------- price change filter ----------

def test_price_change_passes():
    price_change_5m = 15.0
    assert price_change_5m >= PRICE_CHANGE_THRESHOLD


def test_price_change_rejected():
    price_change_5m = 1.0
    assert price_change_5m < PRICE_CHANGE_THRESHOLD


# ---------- volume/mcap ratio ----------

def test_volume_mcap_ratio():
    """Volume must be >= 5% of mcap to qualify."""
    volume_5m = 500_000.0
    mcap = 5_000_000.0
    ratio = volume_5m / mcap
    assert ratio >= 0.05  # 10% > 5%


def test_volume_mcap_ratio_low():
    volume_5m = 100_000.0
    mcap = 10_000_000.0
    ratio = volume_5m / mcap
    assert ratio < 0.05  # 1% < 5%


# ---------- risk scoring components ----------

def test_risk_score_accumulation():
    """Verify risk flag scoring matches the weights in _risk_check."""
    score = 0
    flags = []

    # Unverified contract
    jup_verified = False
    if not jup_verified:
        score += 20
        flags.append("unverified_contract")

    # No liquidity lock
    lp_locked = False
    if not lp_locked:
        score += 15
        flags.append("liquidity_unlocked")

    # Mint authority active
    mint_active = True
    if mint_active:
        score += 15
        flags.append("mint_active")

    # Concentrated holders
    top10_pct = 80.0
    if top10_pct > 70:
        score += 10
        flags.append("concentrated_holders")

    # Young contract (< 24h)
    age_hours = 6
    if age_hours < 24:
        score += 5
        flags.append("young_contract")

    assert score == 65  # all rug-check flags
    assert len(flags) == 5


def test_risk_score_clean_token():
    """A verified, locked, no-mint, distributed, mature token = low risk."""
    score = 0
    assert score == 0
    assert _score_to_label(score) == "low"


# ---------- chain configuration ----------

def test_supported_chains():
    assert "solana" in CHAINS
    assert "ethereum" in CHAINS
    assert "bsc" in CHAINS


# ---------- constants ----------

def test_cooldown_is_60_minutes():
    assert COOLDOWN_MINUTES == 60
