"""Telegram alerting for MajorsBot entries.

Contract: trade_opened reaches Telegram with a readable card; every other
majorsbot event type is muted (empty string) so the channel carries entries
only, while WS still relays the full lifecycle.
"""
from __future__ import annotations

from app.services.telegram_service import service as tg
from app.services.ws_manager import ALERT_MODULES, WATCHLIST_EXEMPT_MODULES


def _fmt(d: dict) -> str:
    return tg._format_alert("majorsbot", d)


def test_majorsbot_is_relayed_and_watchlist_exempt():
    """Without the relay entry nothing ever reaches Telegram; without the
    exemption, entries on news symbols (STORJUSDT) that nobody watchlists
    would be silently swallowed."""
    assert "majorsbot" in ALERT_MODULES
    assert "majorsbot" in WATCHLIST_EXEMPT_MODULES


def test_entry_alert_formats_newsevent_card():
    text = _fmt({
        "type": "trade_opened",
        "symbol": "STORJUSDT",
        "strategy": "newsevent",
        "direction": "short",
        "entry_price": 0.25,
        "stop_price": 0.27375,
        "qty": 400000.0,
        "leverage": 10.0,
        "stop_kind": "liquidation",
        "news_source": "Binance Announcements",
        "leg_gap_s": 240.0,
    })
    assert "MajorsBot Entry — STORJUSDT" in text
    assert "🔴" in text
    assert "`newsevent` SHORT" in text
    # Stopless entries must say Liq, not Stop — the number is where the
    # position dies, and mislabelling it as a stop would misread the risk.
    assert "Liq: `0.27375`" in text and "Stop:" not in text
    assert "Lev: `10.0x`" in text
    assert "Binance Announcements" in text and "(240s gap)" in text


def test_entry_alert_formats_volevent_card_without_extras():
    """volevent's limit-fill payload has no extras — the card must not break
    or claim a liquidation exit."""
    text = _fmt({
        "type": "trade_opened",
        "symbol": "BTCUSDT",
        "strategy": "volevent",
        "direction": "long",
        "entry_price": 69500.0,
        "stop_price": 68100.0,
        "qty": 0.01,
    })
    assert "MajorsBot Entry — BTCUSDT" in text
    assert "🟢" in text
    assert "Stop: `68100.0`" in text and "Liq:" not in text
    assert "Lev:" not in text
    assert "News:" not in text


def test_non_entry_events_are_muted():
    for t in ("order_placed", "order_cancelled", "trade_partial_exit", "trade_closed"):
        assert _fmt({"type": t, "symbol": "BTCUSDT"}) == ""


def test_notional_survives_garbage_prices():
    text = _fmt({
        "type": "trade_opened", "symbol": "X", "strategy": "newsevent",
        "direction": "long", "entry_price": None, "qty": None,
    })
    assert "MajorsBot Entry" in text  # degraded, not crashed


def test_alert_extra_cannot_overwrite_core_payload_keys():
    """alert_extra adds context; it must never clobber entry_price & co."""
    import inspect

    from app.modules.majorsbot import executor

    src = inspect.getsource(executor.open_market_trade)
    assert "setdefault" in src
