"""Forward-test report — message-composition tests.

compose_majorsbot_section is pure (no DB/Telegram I/O): feed it fake aggregate
dicts and assert the header + per-strategy rows. The DB-loading and delivery
paths are thin wrappers exercised in prod by the weekly cron run.

(WaveBot's report composition was removed when the bot module was retired; this
file now only covers the MajorsBot section that remains.)
"""
from __future__ import annotations

from app.scripts.forward_test_report import (
    MAJORSBOT_STRATEGIES,
    MAX_MESSAGE_CHARS,
    compose_majorsbot_section,
)


def _strat(
    signals: int = 0,
    filled: int = 0,
    pending: int = 0,
    cancelled: int = 0,
    closed: int = 0,
    avg_r_net: float | None = None,
    total_r_net: float | None = None,
) -> dict:
    return {
        "signals": signals,
        "filled": filled,
        "pending": pending,
        "cancelled": cancelled,
        "closed": closed,
        "avg_r_net": avg_r_net,
        "total_r_net": total_r_net,
    }


def _mb(per: dict | None = None, equity: float | None = 10_500.0, open_: int = 2) -> dict:
    return {
        "equity": equity,
        "open": open_,
        "since": "2026-07-25T00:00:00+00:00",
        "per_strategy": per
        if per is not None
        else {
            "volevent": _strat(
                signals=20, filled=12, pending=1, cancelled=7, closed=10,
                avg_r_net=0.041, total_r_net=0.41,
            ),
            "fundingfade": _strat(),
        },
    }


class TestMajorsBotSection:
    def test_header_shows_equity_open_and_since(self):
        msg = compose_majorsbot_section(_mb())
        assert "MajorsBot Forward Test" in msg
        assert "$10,500.00" in msg
        assert "Open now: `2`" in msg
        assert "2026-07-25" in msg

    def test_every_strategy_row_rendered(self):
        msg = compose_majorsbot_section(_mb())
        for name in MAJORSBOT_STRATEGIES:
            assert name in msg
        assert "+0.041" in msg  # volevent avg net R
        assert "+0.41" in msg   # volevent total net R

    def test_empty_strategy_uses_dash_placeholders(self):
        msg = compose_majorsbot_section(_mb(per={"volevent": _strat(), "fundingfade": _strat()}))
        # no closed trades → em-dash placeholders, still composes
        assert "—" in msg
        assert "volevent" in msg
        assert "fundingfade" in msg

    def test_missing_equity_renders_dash(self):
        msg = compose_majorsbot_section(_mb(equity=None))
        assert "Equity `—`" in msg

    def test_length_stays_under_telegram_budget(self):
        big = {f"strat_{i}": _strat(signals=i, closed=i, avg_r_net=0.1, total_r_net=1.0) for i in range(80)}
        msg = compose_majorsbot_section(_mb(per=big))
        assert len(msg) <= MAX_MESSAGE_CHARS
