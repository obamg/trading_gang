"""Oracle alert follow-ups — verdict bar, long/short framing, Telegram text."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.modules.oracle.engine import followup_payload, followup_verdict
from app.services.telegram_service import _format_followup


def test_verdict_splits_wrong_from_flat():
    assert followup_verdict("long", 0.5)[0] == "right"
    assert followup_verdict("long", -0.5)[0] == "wrong"
    assert followup_verdict("long", 0.1)[0] == "flat"       # inside the 0.2% bar
    assert followup_verdict("short", -0.5)[:2] == ("right", 0.5)  # short profits when price falls
    assert followup_verdict("strong_long", 0.4)[0] == "flat"  # strong calls need 0.5%


def _sig(rec: str):
    sig = SimpleNamespace(
        id=uuid4(), symbol="SOLUSDT", recommendation=rec, score=-40, timeframe="board",
        signal_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
    )
    out = SimpleNamespace(
        price_at_signal=Decimal("100"), price_4h=Decimal("98"), pnl_4h_pct=Decimal("-2"),
        mfe_1h_pct=Decimal("0.3"), mae_1h_pct=Decimal("1.5"),
    )
    return sig, out


def test_short_payload_flips_best_and_worst():
    sig, out = _sig("short")
    p = followup_payload(sig, out, "4h", {"days": 7, "n": 5, "hit_pct": 60, "avg_move_pct": 0.3})
    assert p["verdict"] == "right" and p["move_pct"] == 2.0
    assert p["best_1h_pct"] == 1.5 and p["worst_1h_pct"] == 0.3  # down is good for a short
    text = _format_followup(p)
    assert "Right" in text and "SOLUSDT" in text and "too few to trust" in text


def test_neutral_signal_has_no_followup():
    sig, out = _sig("neutral")
    assert followup_payload(sig, out, "4h", {}) is None
