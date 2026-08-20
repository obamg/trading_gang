"""Tests for the newsevent two-leg strategy.

The contract is symmetry: whichever leg lands first, the other confirms it
inside NEWSEVENT_PAIR_WINDOW_S, and the pair fires either way. The other
half of the contract is isolation — volevent is mid-forward-test and nothing
here may change its behaviour.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.majorsbot import newsevent as ne
from app.modules.majorsbot import strategies as st


# --- pairing symmetry -----------------------------------------------------

WINDOW = st.NEWSEVENT_PAIR_WINDOW_S
BASE = 1_787_200_000_000


@pytest.mark.parametrize(
    "vol_ts,news_ts,expected",
    [
        (BASE, BASE, True),                                   # simultaneous
        (BASE, BASE - 14 * 60 * 1000, True),                  # news first, inside
        (BASE, BASE + 14 * 60 * 1000, True),                  # volume first, inside
        (BASE, BASE - WINDOW * 1000, True),                   # exactly on the edge
        (BASE, BASE + WINDOW * 1000, True),
        (BASE, BASE - (WINDOW + 1) * 1000, False),            # news too early
        (BASE, BASE + (WINDOW + 1) * 1000, False),            # volume too early
    ],
)
def test_legs_pair_in_either_order(vol_ts, news_ts, expected):
    assert st.legs_paired(vol_ts, news_ts) is expected


def test_pairing_is_order_agnostic():
    """The spec: news-then-volume and volume-then-news are the same event."""
    gap = 10 * 60 * 1000
    assert st.legs_paired(BASE, BASE + gap) == st.legs_paired(BASE + gap, BASE)


# --- direction resolution -------------------------------------------------

@pytest.mark.parametrize(
    "price,sentiment,expected",
    [
        ("long", "bullish", "long"),
        ("short", "bearish", "short"),
        ("long", "neutral", "long"),     # neutral news does not veto
        ("short", None, "short"),
        ("long", "bearish", None),       # legs disagree -> stand down
        ("short", "bullish", None),
    ],
)
def test_newsevent_direction(price, sentiment, expected):
    assert st.newsevent_direction(price, sentiment) == expected


def test_direction_rejects_garbage_price_direction():
    assert st.newsevent_direction("sideways", "bullish") is None


# --- volume leg -----------------------------------------------------------

def _flat_bars(n: int, price: float = 100.0, vol: float = 10.0) -> list[dict]:
    return [
        {"t": BASE + i * st.NEWSEVENT_BAR_MS, "o": price, "h": price,
         "l": price, "c": price, "v": vol}
        for i in range(n)
    ]


def _spike(bars: list[dict], ret: float, vol_mult: float) -> list[dict]:
    bars = [dict(b) for b in bars]
    last = bars[-1]
    open_px = last["o"]
    close = open_px * (1 + ret)
    last["c"] = close
    last["h"] = max(open_px, close)
    last["l"] = min(open_px, close)
    last["v"] = 10.0 * vol_mult
    return bars


def test_volume_leg_needs_enough_history():
    assert st.newsevent_volume_leg(_flat_bars(10)) is None


def test_flat_series_produces_no_leg():
    bars = _flat_bars(st.NEWSEVENT_LOOKBACK_BARS + 5)
    assert st.newsevent_volume_leg(bars) is None


def test_volume_leg_fires_on_spike():
    # Give the baseline some range so mean_tr_pct is non-zero but small.
    bars = _flat_bars(st.NEWSEVENT_LOOKBACK_BARS + 5)
    for i, b in enumerate(bars[:-1]):
        b["h"] = b["o"] * 1.001
        b["l"] = b["o"] * 0.999
        b["c"] = b["o"] * (1.0005 if i % 2 else 0.9995)
    bars = _spike(bars, ret=0.05, vol_mult=5.0)

    leg = st.newsevent_volume_leg(bars)
    assert leg is not None
    assert leg["direction"] == "long"
    assert leg["vol_mult"] >= st.NEWSEVENT_VOL_MULT


def test_volume_leg_requires_both_return_and_volume():
    bars = _flat_bars(st.NEWSEVENT_LOOKBACK_BARS + 5)
    for b in bars[:-1]:
        b["h"] = b["o"] * 1.001
        b["l"] = b["o"] * 0.999
    # Big move, ordinary volume -> no leg.
    assert st.newsevent_volume_leg(_spike(bars, 0.05, 1.0)) is None
    # Big volume, ordinary move -> no leg.
    assert st.newsevent_volume_leg(_spike(bars, 0.0001, 5.0)) is None


def test_short_leg_on_down_spike():
    bars = _flat_bars(st.NEWSEVENT_LOOKBACK_BARS + 5)
    for b in bars[:-1]:
        b["h"] = b["o"] * 1.001
        b["l"] = b["o"] * 0.999
    leg = st.newsevent_volume_leg(_spike(bars, -0.05, 5.0))
    assert leg is not None and leg["direction"] == "short"


# --- stop / sizing --------------------------------------------------------

def test_stop_uses_adverse_extreme():
    stop, risk = st.newsevent_stop("long", Decimal("100"), Decimal("97"))
    assert stop == Decimal("97")
    assert risk == Decimal("3")


def test_stop_floor_prevents_absurd_sizing():
    """A tight spike bar would otherwise give a near-zero stop distance, and
    risk-normalized sizing turns that into an enormous qty."""
    stop, risk = st.newsevent_stop("long", Decimal("100"), Decimal("99.99"))
    assert risk == st.NEWSEVENT_MIN_STOP_PCT * Decimal("100")
    assert stop == Decimal("100") - risk


def test_short_stop_is_above_entry():
    stop, risk = st.newsevent_stop("short", Decimal("100"), Decimal("103"))
    assert stop == Decimal("103") and risk == Decimal("3")


def test_leverage_cap_binds_qty():
    """position_size_pct is the notional cap as a multiple of equity — the
    leverage dial. Verify it actually binds rather than being decorative."""
    qty_1x = st.compute_qty(
        paper_equity=Decimal("10000"), risk_per_trade_pct=Decimal("0.01"),
        entry_price=Decimal("100"), stop_price=Decimal("99.9"),
        max_notional_pct=Decimal("1"),
    )
    qty_20x = st.compute_qty(
        paper_equity=Decimal("10000"), risk_per_trade_pct=Decimal("0.01"),
        entry_price=Decimal("100"), stop_price=Decimal("99.9"),
        max_notional_pct=Decimal("20"),
    )
    assert qty_1x == Decimal("10000") * Decimal("1") / Decimal("100")   # cap binds
    assert qty_20x > qty_1x                                             # cap relaxed
    # At 20x the risk-based qty binds instead, so risk stays at risk_pct.
    assert qty_20x == Decimal("10000") * Decimal("0.01") / Decimal("0.1")


# --- isolation from volevent ----------------------------------------------

def test_newsevent_thresholds_are_looser_than_volevent():
    """Corroboration substitutes for individual significance — but only
    downward. If these ever exceed volevent's, the premise is inverted."""
    assert st.NEWSEVENT_RET_ATR_MULT < st.VOLEVENT_RET_ATR_MULT
    assert st.NEWSEVENT_VOL_MULT < st.VOLEVENT_VOL_MULT


def test_volevent_constants_untouched():
    """volevent is at n=21 of a 30-trade forward test; these mirror a 12-month
    backtest and must not drift."""
    assert st.VOLEVENT_RET_ATR_MULT == 3.0
    assert st.VOLEVENT_VOL_MULT == 3.0
    assert st.VOLEVENT_LOOKBACK_BARS == 720
    assert st.VOLEVENT_MIN_STOP_PCT == Decimal("0.01")


def test_partial_profit_default_fraction_preserves_volevent():
    """take_partial_profit grew a `fraction` param for newsevent; omitting it
    must still mean volevent's fraction."""
    import inspect

    from app.modules.majorsbot import executor

    sig = inspect.signature(executor.take_partial_profit)
    assert sig.parameters["fraction"].default is None


# --- news leg extraction --------------------------------------------------

def test_symbol_for_coin():
    assert ne.symbol_for_coin("btc") == "BTCUSDT"
    assert ne.symbol_for_coin(" HYPE ") == "HYPEUSDT"


class _Row:
    def __init__(self, coins, importance, source, sentiment, published_at, title="t"):
        self.coins = coins
        self.importance = importance
        self.source_name = source
        self.sentiment = sentiment
        self.published_at = published_at
        self.title = title


@pytest.mark.asyncio
async def test_recent_news_legs_filters_and_fans_out(monkeypatch):
    from datetime import datetime, timezone

    ts = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    rows = [
        _Row("BTC,ETH", "high", "CoinDesk", "bullish", ts),
        _Row("SOL", "normal", "CoinDesk", "bullish", ts),        # dropped: not high
        _Row("HYPE", "normal", "Binance Announcements", "bullish", ts),  # kept: primary
    ]

    class _Result:
        def scalars(self):
            class _S:
                def all(self_inner):
                    return rows
            return _S()

    class _DB:
        async def execute(self, *a, **k):
            return _Result()

    legs = await ne.recent_news_legs(_DB(), 900)
    assert set(legs) == {"BTCUSDT", "ETHUSDT", "HYPEUSDT"}
    assert legs["HYPEUSDT"]["primary"] is True
    assert legs["BTCUSDT"]["primary"] is False
    assert legs["BTCUSDT"]["sentiment"] == "bullish"


@pytest.mark.asyncio
async def test_universe_is_majors_union_listed_news_symbols(monkeypatch, fake_redis):
    monkeypatch.setattr(ne.redis_service, "get_redis", lambda: fake_redis)
    await fake_redis.sadd(ne.ACTIVE_SYMBOLS_KEY, "HYPEUSDT", "STORJUSDT")
    monkeypatch.setattr(
        "app.modules.majorsbot.engine.symbol_list", lambda: ["BTCUSDT", "ETHUSDT"]
    )

    universe = await ne.tradeable_universe({"HYPEUSDT", "NOTLISTEDUSDT"})
    assert "BTCUSDT" in universe and "ETHUSDT" in universe
    assert "HYPEUSDT" in universe            # news symbol that Bybit lists
    assert "NOTLISTEDUSDT" not in universe   # news symbol with no perp


@pytest.mark.asyncio
async def test_universe_is_majors_only_without_news(monkeypatch, fake_redis):
    monkeypatch.setattr(ne.redis_service, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(
        "app.modules.majorsbot.engine.symbol_list", lambda: ["BTCUSDT"]
    )
    assert await ne.tradeable_universe(set()) == ["BTCUSDT"]


# --- leg store round-trip -------------------------------------------------

@pytest.mark.asyncio
async def test_leg_store_round_trip(monkeypatch, fake_redis):
    monkeypatch.setattr(ne.redis_service, "get_redis", lambda: fake_redis)
    leg = {"direction": "long", "ts_ms": BASE, "close": 100.0}
    await ne.store_leg(ne.VOL_LEG_KEY, "BTCUSDT", leg, 900)
    assert await ne.load_leg(ne.VOL_LEG_KEY, "BTCUSDT") == leg
    await ne.clear_legs("BTCUSDT")
    assert await ne.load_leg(ne.VOL_LEG_KEY, "BTCUSDT") is None


@pytest.mark.asyncio
async def test_load_leg_tolerates_corrupt_payload(monkeypatch, fake_redis):
    monkeypatch.setattr(ne.redis_service, "get_redis", lambda: fake_redis)
    await fake_redis.set(ne.VOL_LEG_KEY.format(symbol="BTCUSDT"), "not json")
    assert await ne.load_leg(ne.VOL_LEG_KEY, "BTCUSDT") is None


# --- tick gating ----------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_is_noop_when_strategy_disabled(monkeypatch):
    monkeypatch.setattr(ne.app_settings, "majorsbot_enabled", True, raising=False)
    monkeypatch.setattr(ne.app_settings, "majorsbot_newsevent_enabled", False, raising=False)
    assert await ne.run_newsevent_tick() == {
        "symbols": 0, "opened": 0, "closed": 0, "vol_legs": 0, "news_legs": 0
    }


@pytest.mark.asyncio
async def test_tick_is_noop_when_bot_disabled(monkeypatch):
    monkeypatch.setattr(ne.app_settings, "majorsbot_enabled", False, raising=False)
    monkeypatch.setattr(ne.app_settings, "majorsbot_newsevent_enabled", True, raising=False)
    totals = await ne.run_newsevent_tick()
    assert totals["symbols"] == 0


# --- stopless mode: liquidation is the exit -------------------------------

def test_liquidation_price_long_and_short():
    """At 20x with 0.5% maintenance margin the position dies ~4.5% away."""
    liq_long = st.liquidation_price("long", Decimal("100"), Decimal("20"))
    liq_short = st.liquidation_price("short", Decimal("100"), Decimal("20"))
    assert liq_long == Decimal("100") * (1 - (Decimal("1") / 20 - Decimal("0.005")))
    assert liq_short == Decimal("100") * (1 + (Decimal("1") / 20 - Decimal("0.005")))
    assert Decimal("95") < liq_long < Decimal("96")
    assert Decimal("104") < liq_short < Decimal("105")


def test_liquidation_tightens_as_leverage_rises():
    d = Decimal
    far = st.liquidation_price("long", d("100"), d("5"))
    near = st.liquidation_price("long", d("100"), d("20"))
    assert near > far  # 20x liquidates closer to entry


def test_no_liquidation_at_or_below_1x():
    """At <=1x the move required exceeds 100%, i.e. price would have to reach
    zero — there is no liquidation to model."""
    assert st.liquidation_price("long", Decimal("100"), Decimal("1")) is None
    assert st.liquidation_price("long", Decimal("100"), Decimal("0.5")) is None


def test_liquidation_guards_bad_inputs():
    assert st.liquidation_price("long", Decimal("100"), Decimal("0")) is None
    assert st.liquidation_price("long", Decimal("0"), Decimal("20")) is None


def test_effective_leverage():
    assert st.effective_leverage(Decimal("200000"), Decimal("10000")) == Decimal("20")
    assert st.effective_leverage(Decimal("100"), Decimal("0")) == Decimal("0")


def test_stopless_default_is_off_by_config():
    """The stop was removed by request — assert the shipped default so a
    silent revert is caught."""
    from app.config import Settings

    assert Settings().majorsbot_newsevent_stop_enabled is False


def test_r_reference_survives_stop_removal():
    """R must stay anchored to a real risk unit even with no protective stop,
    otherwise net R is not comparable to volevent and the n>=30 gate is
    meaningless."""
    ref_stop, ref_risk = st.newsevent_stop("long", Decimal("100"), Decimal("98"))
    assert ref_risk == Decimal("2")
    # Reference risk is independent of leverage / liquidation.
    liq = st.liquidation_price("long", Decimal("100"), Decimal("20"))
    assert liq is not None and liq != ref_stop


def test_open_market_trade_separates_exit_stop_from_r_reference():
    import inspect

    from app.modules.majorsbot import executor

    sig = inspect.signature(executor.open_market_trade)
    assert sig.parameters["initial_stop_price"].default is None


# --- concurrency scoping --------------------------------------------------

@pytest.mark.asyncio
async def test_count_open_is_scoped_to_newsevent():
    """Regression: using the bot-wide equity.get_concurrent_count() meant
    volevent's 2 open prod positions would permanently block newsevent from
    ever entering at a per-strategy cap of 1."""
    seen = {}

    class _Result:
        def scalars(self):
            class _S:
                def all(self_inner):
                    return []
            return _S()

    class _DB:
        async def execute(self, stmt, *a, **k):
            seen["sql"] = str(stmt)
            return _Result()

    assert await ne.count_open(_DB()) == 0
    # The query must filter on strategy, not count everything open.
    assert "strategy" in seen["sql"].lower()


def test_newsevent_does_not_use_global_concurrency_counter():
    import inspect

    src = inspect.getsource(ne._try_enter)
    assert "count_open(db)" in src
    assert "get_concurrent_count" not in src


# --- pre-mortem fixes (2026-08-20) ----------------------------------------

from datetime import datetime, timezone


class _FakeTrade:
    def __init__(self, **kw):
        self.id = "t1"
        self.symbol = "BTCUSDT"
        self.direction = "long"
        self.strategy = "newsevent"
        self.entry_price = 100.0
        self.initial_stop_price = 98.0     # risk = 2 → 1R = 2
        self.stop_price = 98.0
        self.qty = 2.0                      # notional 200 on 10k equity → lev unreachable
        self.paper_equity_at_entry = 10000.0
        self.partial_qty = None
        self.partial_exit_at = None
        self.partial_pnl_usd = None
        self.entry_bar_at = datetime.fromtimestamp(BASE / 1000, tz=timezone.utc)
        self.entry_at = self.entry_bar_at
        self.status = "open"
        for k, v in kw.items():
            setattr(self, k, v)


def _bar(i, o, h, l, c):
    return {"t": BASE + i * st.NEWSEVENT_BAR_MS, "o": o, "h": h, "l": l, "c": c, "v": 1.0}


def _md(bars):
    from app.modules.majorsbot.data import MarketData

    return MarketData(symbol="BTCUSDT", bars=bars)


class _WalkDB:
    async def commit(self):
        pass


@pytest.fixture()
def _walker_env(monkeypatch):
    """Stopless config, majors symbol list, no funding, captured executor calls."""
    calls = {"close": [], "partial": []}

    async def fake_close(db, trade, *, raw_exit_price, reason, funding_pnl=None, **kw):
        calls["close"].append({"price": raw_exit_price, "reason": reason, "funding": funding_pnl})
        trade.status = "closed"

    async def fake_partial(db, trade, *, exit_price, bar_close_at, fraction=None, **kw):
        calls["partial"].append({"price": exit_price})
        trade.partial_qty = float(trade.qty) * 0.5
        trade.partial_exit_at = bar_close_at

    async def no_funding(symbol):
        return None

    monkeypatch.setattr(ne.executor, "close_trade", fake_close)
    monkeypatch.setattr(ne.executor, "take_partial_profit", fake_partial)
    monkeypatch.setattr(ne.data, "get_funding", no_funding)
    monkeypatch.setattr(ne.app_settings, "majorsbot_newsevent_stop_enabled", False, raising=False)
    monkeypatch.setattr("app.modules.majorsbot.engine.symbol_list", lambda: ["BTCUSDT"])
    return calls


# Bars: bar1 dips near entry, bar2 hits TP (103) and arms the trail
# (peak 104.5 − 1R = 102.5), bar3 later hits the trail.
BAR1 = _bar(1, 100.0, 100.5, 99.2, 100.0)
BAR2 = _bar(2, 100.0, 104.5, 100.0, 104.0)
BAR3 = _bar(3, 104.0, 104.2, 102.4, 102.6)


@pytest.mark.asyncio
async def test_replay_does_not_rewalk_history_with_ratcheted_stop(_walker_env):
    """THE pre-mortem walker bug: after the trail armed and trade.stop_price
    was persisted at 102.5, the next tick re-walked bar1 (low 99.2) against
    that ratcheted stop and closed every winner at ~+1R. Replay semantics
    must rebuild the ratchet from entry-time state instead."""
    trade = _FakeTrade()
    db = _WalkDB()

    # Tick 1: partial at TP, trail arms, display stop ratchets — no close.
    closed = await ne._walk_open(db, trade, _md([BAR1, BAR2]))
    assert closed is False
    assert len(_walker_env["partial"]) == 1
    assert float(trade.stop_price) == 102.5

    # Tick 2, same bars, now with the ratcheted stop persisted: the buggy
    # walker closed here on bar1. Replay must not.
    closed = await ne._walk_open(db, trade, _md([BAR1, BAR2]))
    assert closed is False
    assert _walker_env["close"] == []

    # Tick 3: a genuinely later bar crosses the trail → close, reason trail.
    closed = await ne._walk_open(db, trade, _md([BAR1, BAR2, BAR3]))
    assert closed is True
    assert len(_walker_env["close"]) == 1
    assert _walker_env["close"][0]["reason"] == st.CLOSE_TRAIL
    assert float(_walker_env["close"][0]["price"]) == 102.5


@pytest.mark.asyncio
async def test_trail_exit_takes_gap_penalty(_walker_env):
    """A bar opening beyond the trail fills at the open, not at the level."""
    trade = _FakeTrade()
    db = _WalkDB()
    gap_bar = _bar(3, 101.0, 101.5, 100.8, 101.2)  # opens below the 102.5 trail
    closed = await ne._walk_open(db, trade, _md([BAR1, BAR2, gap_bar]))
    assert closed is True
    assert float(_walker_env["close"][0]["price"]) == 101.0  # the open, worse


@pytest.mark.asyncio
async def test_liquidation_books_bankruptcy_price(_walker_env):
    """At 10x, liq triggers at 90.5 (0.5% MMR) but the account's realized
    outcome is the bankruptcy price 90.0 — full margin loss, not an orderly
    exit at the trigger."""
    trade = _FakeTrade(qty=1000.0)  # notional 100k on 10k equity → 10x
    db = _WalkDB()
    crash = _bar(1, 100.0, 100.0, 90.4, 90.6)  # pierces liq 90.5
    closed = await ne._walk_open(db, trade, _md([crash]))
    assert closed is True
    assert _walker_env["close"][0]["reason"] == st.CLOSE_LIQUIDATION
    assert float(_walker_env["close"][0]["price"]) == 90.0


def test_bankruptcy_price_math():
    assert st.bankruptcy_price("long", Decimal("100"), Decimal("10")) == Decimal("90")
    assert st.bankruptcy_price("short", Decimal("100"), Decimal("10")) == Decimal("110")
    assert st.bankruptcy_price("long", Decimal("100"), Decimal("1")) is None


def test_non_major_mmr_is_stricter(monkeypatch):
    monkeypatch.setattr("app.modules.majorsbot.engine.symbol_list", lambda: ["BTCUSDT"])
    assert ne._mmr_for("BTCUSDT") == st.NEWSEVENT_MAINTENANCE_MARGIN_RATE
    assert ne._mmr_for("STORJUSDT") == st.NEWSEVENT_MMR_NON_MAJOR
    # Stricter MMR → liquidation strikes EARLIER (closer to entry).
    liq_major = st.liquidation_price("long", Decimal("100"), Decimal("10"), ne._mmr_for("BTCUSDT"))
    liq_small = st.liquidation_price("long", Decimal("100"), Decimal("10"), ne._mmr_for("STORJUSDT"))
    assert liq_small > liq_major


# --- funding accrual ------------------------------------------------------

@pytest.mark.asyncio
async def test_funding_accrues_over_hold(monkeypatch):
    """A long paying positive funding loses money; priced at the containing
    5m bar's open."""
    event_ts = BASE + int(1.5 * st.NEWSEVENT_BAR_MS)  # inside bar 1

    async def fake_funding(symbol):
        return [(event_ts, 0.001), (BASE - 1000, 0.5)]  # second is pre-entry

    monkeypatch.setattr(ne.data, "get_funding", fake_funding)
    trade = _FakeTrade(qty=10.0)
    md = _md([_bar(1, 100.0, 101.0, 99.0, 100.5), _bar(2, 100.5, 101.0, 100.0, 100.8)])

    pnl = await ne._accrued_funding(trade, md, exit_bar_ms=BASE + 2 * st.NEWSEVENT_BAR_MS)
    # long pays: −(0.001 × bar1.open 100.0 × qty 10) = −1.0
    assert pnl == Decimal("-1.0")


# --- ledger isolation -----------------------------------------------------

from app.modules.majorsbot import equity as eq_mod


def test_ledger_mapping():
    assert eq_mod.ledger_for(st.NEWSEVENT) == "newsevent"
    assert eq_mod.ledger_for(st.VOLEVENT) == "paper"
    assert eq_mod.ledger_for(st.FUNDINGFADE) == "paper"


def test_default_ledger_keys_are_byte_identical_to_legacy():
    """The live volevent forward test's Redis state must survive this
    refactor — the default ledger MUST resolve to the pre-split keys."""
    assert eq_mod._equity_key("paper") == "majorsbot:equity:paper"
    assert eq_mod._concurrent_key("paper") == "majorsbot:concurrent_count"
    assert eq_mod._equity_key("newsevent") == "majorsbot:equity:newsevent"
    assert eq_mod._concurrent_key("newsevent") == "majorsbot:concurrent:newsevent"


@pytest.mark.asyncio
async def test_ledgers_do_not_share_balance(monkeypatch, fake_redis):
    monkeypatch.setattr(eq_mod.redis_service, "get_redis", lambda: fake_redis)
    base = await eq_mod.get_paper_equity()
    await eq_mod.add_to_equity(Decimal("-9500"), "newsevent")  # a liquidation
    # volevent's book must be untouched.
    assert await eq_mod.get_paper_equity() == base
    assert await eq_mod.get_paper_equity("newsevent") == base - Decimal("9500")


def test_engine_reconcile_excludes_newsevent():
    """engine reconciles the DEFAULT ledger's counter from DB truth; counting
    newsevent's open position there would zero-drift the wrong book."""
    import inspect

    from app.modules.majorsbot import engine

    assert "NEWSEVENT" in inspect.getsource(engine._count_open)
