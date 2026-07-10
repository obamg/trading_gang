"""Forward-test report — message-composition tests.

compose_report is pure (no DB/Telegram I/O): feed it fake aggregate dicts and
assert the headline + the three recommendation branches. The DB-loading and
delivery paths are thin wrappers exercised in prod by the weekly cron run.
"""
from __future__ import annotations

from app.scripts.forward_test_report import (
    MAX_MESSAGE_CHARS,
    MIN_SAMPLE_FOR_VERDICT,
    aggregate_trades,
    compose_report,
)


def _bucket(n: int, avg_r_net: float | None) -> dict:
    return {
        "n": n,
        "wins": n // 2,
        "win_pct": 50.0 if n else None,
        "pnl_total": 12.5 if n else 0.0,
        "pnl_avg": 1.0 if n else None,
        "avg_r": 0.11 if n else None,
        "avg_r_net": avg_r_net,
        "fees_total": 3.5,
        "funding_total": -0.25,
    }


def _stats(n: int, avg_r_net: float | None, skips: dict | None = None) -> dict:
    return {
        "since": "2026-07-10T18:45:00+00:00",
        "open_positions": 2,
        "overall": _bucket(n, avg_r_net),
        "by_direction": {
            "long": _bucket(n // 2, avg_r_net),
            "short": _bucket(n - n // 2, avg_r_net),
        },
        "skips": skips
        if skips is not None
        else {
            "low_vol_ratio": 40,
            "funding_extreme": 11,
            "low_turnover": 7,
            "oracle_veto": 25,
            "cooldown": 3,
        },
    }


class TestRecommendationBranches:
    def test_small_sample_says_keep_testing(self):
        msg = compose_report(_stats(n=MIN_SAMPLE_FOR_VERDICT - 1, avg_r_net=0.5))
        assert "keep testing" in msg
        assert "gate passed" not in msg
        assert "filters not proving out" not in msg

    def test_positive_expectancy_at_sample_passes_gate(self):
        msg = compose_report(_stats(n=MIN_SAMPLE_FOR_VERDICT, avg_r_net=0.041))
        assert "gate passed — consider partial-trail exits next" in msg
        assert "keep testing" not in msg

    def test_non_positive_expectancy_at_sample_flags_filters(self):
        msg = compose_report(_stats(n=45, avg_r_net=-0.02))
        assert "filters not proving out — re-examine" in msg
        # zero expectancy is not a pass either
        msg_zero = compose_report(_stats(n=MIN_SAMPLE_FOR_VERDICT, avg_r_net=0.0))
        assert "filters not proving out — re-examine" in msg_zero
        assert "gate passed" not in msg_zero


class TestMessageShape:
    def test_headline_shows_expectancy_sample_and_baselines(self):
        msg = compose_report(_stats(n=18, avg_r_net=0.041))
        assert "+0.041R" in msg
        assert "n=18" in msg
        assert "-0.203R" in msg  # pre-filter replay baseline
        assert "+0.033R" in msg  # pre-filter recorded baseline

    def test_new_filters_highlighted_even_when_zero(self):
        msg = compose_report(_stats(n=5, avg_r_net=0.1, skips={"oracle_veto": 9}))
        for reason in ("low_vol_ratio", "funding_extreme", "low_turnover"):
            assert reason in msg
        assert "oracle_veto" in msg

    def test_zero_trades_still_composes_and_keeps_testing(self):
        msg = compose_report(_stats(n=0, avg_r_net=None))
        assert "n=0" in msg
        assert "keep testing" in msg

    def test_length_stays_under_telegram_budget(self):
        skips = {f"reason_{i}": i for i in range(40)}
        msg = compose_report(_stats(n=200, avg_r_net=0.2, skips=skips))
        assert len(msg) <= MAX_MESSAGE_CHARS


class TestAggregateTrades:
    def test_overall_and_direction_buckets(self):
        trades = [
            {
                "direction": "long",
                "realized_pnl_usd": 10,
                "realized_r": 1.0,
                "realized_r_net": 0.9,
                "fees_usd": 0.5,
                "funding_pnl_usd": -0.1,
            },
            {
                "direction": "short",
                "realized_pnl_usd": -5,
                "realized_r": -1.0,
                "realized_r_net": -1.1,
                "fees_usd": 0.5,
                "funding_pnl_usd": 0.0,
            },
        ]
        stats = aggregate_trades(trades)
        overall = stats["overall"]
        assert overall["n"] == 2
        assert overall["wins"] == 1
        assert overall["win_pct"] == 50.0
        assert overall["pnl_total"] == 5.0
        assert overall["avg_r_net"] == (0.9 - 1.1) / 2
        assert overall["fees_total"] == 1.0
        assert stats["by_direction"]["long"]["n"] == 1
        assert stats["by_direction"]["short"]["avg_r_net"] == -1.1

    def test_empty_stream(self):
        stats = aggregate_trades([])
        assert stats["overall"]["n"] == 0
        assert stats["overall"]["avg_r_net"] is None
        assert stats["overall"]["win_pct"] is None
