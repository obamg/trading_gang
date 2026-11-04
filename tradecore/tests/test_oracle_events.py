"""Incoming events: options-expiry dates, pick window, ForexFactory parsing."""
from datetime import datetime, timezone

from app.modules.macropulse.collector import parse_ff_events
from app.modules.oracle.events import in_window, options_expiries


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_options_expiry_last_friday_and_quarterly():
    # Last Friday of Dec 2026 is the 25th; quarterly → high.
    [ev] = options_expiries(_utc(2026, 12, 24, 9), hours=48)
    assert ev["scheduled_at"] == _utc(2026, 12, 25, 8) and ev["impact"] == "high"
    # Nov 2026: last Friday is the 27th; monthly → medium.
    [ev] = options_expiries(_utc(2026, 11, 26, 9), hours=48)
    assert ev["scheduled_at"] == _utc(2026, 11, 27, 8) and ev["impact"] == "medium"
    # Crosses the month boundary: Jan 2027's last Friday is the 29th.
    assert options_expiries(_utc(2027, 1, 28, 9), hours=48)[0]["scheduled_at"] == _utc(2027, 1, 29, 8)
    assert options_expiries(_utc(2026, 11, 4), hours=48) == []


def test_in_window_is_inclusive_and_forward_only():
    now = _utc(2026, 11, 12, 10)
    evs = [{"scheduled_at": _utc(2026, 11, 12, h)} for h in (9, 13, 14, 15)]
    assert [e["scheduled_at"].hour for e in in_window(evs, now)] == [13, 14]


def test_parse_ff_keeps_us_medium_high_in_utc():
    rows = [
        {"title": "CPI m/m", "country": "USD", "date": "2026-11-12T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": ""},
        {"title": "Retail Sales", "country": "USD", "date": "2026-11-13T08:30:00-05:00", "impact": "Medium"},
        {"title": "Bank Holiday", "country": "USD", "date": "2026-11-11T00:00:00-05:00", "impact": "Holiday"},
        {"title": "GDP", "country": "EUR", "date": "2026-11-12T05:00:00-05:00", "impact": "High"},
        {"title": "Broken", "country": "USD", "date": "not-a-date", "impact": "High"},
    ]
    out = parse_ff_events(rows)
    assert [e["name"] for e in out] == ["CPI m/m", "Retail Sales"]
    assert out[0]["scheduled_at"] == _utc(2026, 11, 12, 13, 30)
    assert out[0]["forecast_value"] == "0.3%" and out[0]["previous_value"] is None
    assert parse_ff_events({"error": "rate limited"}) == []
