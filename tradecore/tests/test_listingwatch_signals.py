"""Pure-function tests for the 5 listingwatch signal evaluators."""
from __future__ import annotations

from app.modules.listingwatch.signals import (
    WatcherCtx,
    evaluate_all,
    evaluate_breakout_long,
    evaluate_floor_held,
    evaluate_funding_extreme,
    evaluate_initial_squeeze,
    evaluate_pump_fade,
)


def _ctx(**overrides) -> WatcherCtx:
    base = dict(
        symbol="FOOUSDT",
        seconds_since_t0=3600,
        t0_price=1.0,
        last_price=1.0,
        high_15m=1.0,
        low_15m=1.0,
        high_1h=1.0,
        low_1h=1.0,
        cvd_5m_usd=0.0,
        volume_5m_usd=0.0,
        volume_5m_baseline_usd=0.0,
        funding_pct=None,
        floor_set_seconds_ago=None,
        is_cross_listing=False,
    )
    base.update(overrides)
    return WatcherCtx(**base)


# ---------- pump_fade ----------


def test_pump_fade_fires_after_real_pump_and_drop():
    sig = evaluate_pump_fade(_ctx(
        seconds_since_t0=900,
        t0_price=1.0,
        high_15m=1.50,    # 50% pump
        last_price=1.40,  # 6.7% off the high
        cvd_5m_usd=-1_500_000,
    ))
    assert sig is not None
    assert sig.type == "pump_fade"
    assert sig.direction == "short"
    assert sig.conviction > 0.5


def test_pump_fade_skips_too_early():
    sig = evaluate_pump_fade(_ctx(seconds_since_t0=300, t0_price=1.0, high_15m=1.5, last_price=1.4, cvd_5m_usd=-1_000_000))
    assert sig is None


def test_pump_fade_skips_no_real_pump():
    sig = evaluate_pump_fade(_ctx(seconds_since_t0=900, t0_price=1.0, high_15m=1.10, last_price=1.04, cvd_5m_usd=-1_000_000))
    assert sig is None


def test_pump_fade_skips_no_cvd_confirmation():
    sig = evaluate_pump_fade(_ctx(seconds_since_t0=900, t0_price=1.0, high_15m=1.5, last_price=1.4, cvd_5m_usd=200_000))
    assert sig is None


# ---------- breakout_long ----------


def test_breakout_long_fires_with_volume_and_buy_pressure():
    sig = evaluate_breakout_long(_ctx(
        seconds_since_t0=2400,
        high_1h=1.20,
        last_price=1.25,
        volume_5m_usd=4_000_000,
        volume_5m_baseline_usd=2_000_000,  # 2x ratio
        cvd_5m_usd=1_500_000,
    ))
    assert sig is not None
    assert sig.type == "breakout_long"
    assert sig.direction == "long"
    assert sig.conviction >= 0.5


def test_breakout_long_skips_when_not_breaking_out():
    sig = evaluate_breakout_long(_ctx(
        seconds_since_t0=2400,
        high_1h=1.30,
        last_price=1.25,
        volume_5m_usd=4_000_000,
        volume_5m_baseline_usd=2_000_000,
        cvd_5m_usd=1_500_000,
    ))
    assert sig is None


def test_breakout_long_skips_low_volume():
    sig = evaluate_breakout_long(_ctx(
        seconds_since_t0=2400,
        high_1h=1.20,
        last_price=1.25,
        volume_5m_usd=2_100_000,
        volume_5m_baseline_usd=2_000_000,  # only 1.05x
        cvd_5m_usd=1_500_000,
    ))
    assert sig is None


def test_breakout_long_skips_negative_cvd():
    sig = evaluate_breakout_long(_ctx(
        seconds_since_t0=2400,
        high_1h=1.20,
        last_price=1.25,
        volume_5m_usd=4_000_000,
        volume_5m_baseline_usd=2_000_000,
        cvd_5m_usd=-500_000,
    ))
    assert sig is None


def test_breakout_long_skips_too_early():
    sig = evaluate_breakout_long(_ctx(
        seconds_since_t0=600,
        high_1h=1.20, last_price=1.25,
        volume_5m_usd=4_000_000, volume_5m_baseline_usd=2_000_000, cvd_5m_usd=1_500_000,
    ))
    assert sig is None


# ---------- initial_squeeze ----------


def test_initial_squeeze_fires_on_absolute_volume():
    sig = evaluate_initial_squeeze(_ctx(
        seconds_since_t0=120,
        volume_5m_usd=8_000_000,
        cvd_5m_usd=4_000_000,
    ))
    assert sig is not None
    assert sig.type == "initial_squeeze"
    assert sig.direction == "long"


def test_initial_squeeze_fires_on_ratio():
    sig = evaluate_initial_squeeze(_ctx(
        seconds_since_t0=120,
        volume_5m_usd=600_000,
        volume_5m_baseline_usd=100_000,  # 6x ratio
        cvd_5m_usd=-200_000,
    ))
    assert sig is not None
    assert sig.direction == "short"


def test_initial_squeeze_skips_after_5min_window():
    sig = evaluate_initial_squeeze(_ctx(
        seconds_since_t0=600,
        volume_5m_usd=10_000_000,
        cvd_5m_usd=4_000_000,
    ))
    assert sig is None


def test_initial_squeeze_skips_below_floor():
    sig = evaluate_initial_squeeze(_ctx(
        seconds_since_t0=120,
        volume_5m_usd=400_000,  # below SQUEEZE_MIN_VOL_USD
    ))
    assert sig is None


# ---------- funding_extreme ----------


def test_funding_extreme_long_pays_short():
    sig = evaluate_funding_extreme(_ctx(funding_pct=0.008))  # 0.8% > 0.5%
    assert sig is not None
    assert sig.direction == "short"
    assert sig.conviction > 0.5


def test_funding_extreme_short_pays_long():
    sig = evaluate_funding_extreme(_ctx(funding_pct=-0.007))
    assert sig is not None
    assert sig.direction == "long"


def test_funding_extreme_skips_normal_range():
    sig = evaluate_funding_extreme(_ctx(funding_pct=0.0001))
    assert sig is None


def test_funding_extreme_skips_when_unknown():
    sig = evaluate_funding_extreme(_ctx(funding_pct=None))
    assert sig is None


# ---------- floor_held ----------


def test_floor_held_fires_when_floor_untouched_and_close():
    sig = evaluate_floor_held(_ctx(
        seconds_since_t0=2400,
        floor_set_seconds_ago=2200,
        low_1h=1.0,
        last_price=1.02,  # within 5%
    ))
    assert sig is not None
    assert sig.direction == "long"


def test_floor_held_skips_when_floor_too_recent():
    sig = evaluate_floor_held(_ctx(
        seconds_since_t0=2400,
        floor_set_seconds_ago=300,
        low_1h=1.0,
        last_price=1.02,
    ))
    assert sig is None


def test_floor_held_skips_when_far_from_floor():
    sig = evaluate_floor_held(_ctx(
        seconds_since_t0=2400,
        floor_set_seconds_ago=2200,
        low_1h=1.0,
        last_price=1.20,  # 20% above
    ))
    assert sig is None


# ---------- evaluate_all ----------


def test_evaluate_all_returns_multiple_signals_when_applicable():
    """Funding-extreme and pump-fade can both fire on the same tick."""
    sigs = evaluate_all(_ctx(
        seconds_since_t0=900,
        t0_price=1.0,
        high_15m=1.5,
        last_price=1.4,
        cvd_5m_usd=-1_000_000,
        funding_pct=0.008,
    ))
    types = {s.type for s in sigs}
    assert "pump_fade" in types
    assert "funding_extreme" in types


def test_evaluate_all_returns_empty_on_quiet_state():
    sigs = evaluate_all(_ctx())
    assert sigs == []
